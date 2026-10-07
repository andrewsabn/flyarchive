"""Входящая папка: настоящее пополнение архива (FR-36, FR-37, FR-40 … FR-47).

    process(каталог архива, входящая папка) -> Summary(batch, counts, receipts, problems)

Что происходит с файлом, положенным во входящую папку:
    1. Он ждёт, пока его размер и время изменения не перестанут меняться (30 секунд).
    2. Проходит приёмку: тип по содержимому, распаковка архивов, правила, сверка с архивом, проверка моделью.
    3. Принятое ложится в корпус, `corpus/входящие/<пачка>/…`, и в индекс, базой «входящие».
       Документ с находками — в `очередь/<пачка>/…`, программа и негодный архив — в `карантин/<пачка>/…`.
       Очередь и карантин лежат вне корпуса и в индекс не попадают.
    4. Копия сверяется с исходником по sha256. Совпало — исходник убирается из входящей папки.
       Не совпало — исходник остаётся, копия убирается, причина в квитанции и в ответе.
    5. На каждый файл пишется квитанция: `квитанции/<пачка>.jsonl`. Рядом — `<пачка>.meta.json`: когда шёл проход
       и какие были замечания (читает `batches.py`).
    6. Пока идёт проход, в каталоге архива лежит `inbox-progress.json`: этап, сколько готово, какой файл последним (`progress.py`).
    7. Долги индексации (`index/pending.jsonl`: документ лежит в корпусе, а в индексе его нет) проход отдаёт в начале и ещё раз
       в конце: долг, записанный пока он шёл (принятие без ожидания индексации), индексируется этим же проходом. Правит файл долгов только
       `edit_pending` под замком `index/pending.lock`: принятия и проход идут параллельно и не должны терять или задваивать долги.
    8. Картинки и сканы без текста (FR-92) принимаются, но в индекс сразу не идут: документ записывается долгом «ждёт описания»
       (`index/vision-pending.jsonl`, тот же замок). В конце прохода, после квитанций, этап vision отдаёт эти долги локальной модели со зрением
       (`vision.py`): не больше `vision_pages` страниц и `vision_minutes` минут за проход, остальное остаётся долгом. Модель недоступна — не сбой:
       долг остаётся, в замечаниях пачки одна запись с кодом. Квитанция пишется до описания и после не меняется, поэтому в ней — причина
       ожидания (`vision`), а живое состояние (описан, ждёт, страниц, модель, секунд) отдаёт `vision_state` по sha256 (`flyarchive inbox batch`).

Ничего не теряется. То, что архив не взял (не документ, то же письмо с другими байтами), переезжает
в папку возврата рядом со входящей: `<входящая>-возврат/<пачка>/…`. Архив с таким содержимым возвращается целиком.
Удаляется то, чья точная копия уже лежит в архиве, в очереди или в карантине, и то, что не содержимое: пустой файл и след операционной
системы (`intake.trace_reason`: каталог `__MACOSX`, `.DS_Store`, `Thumbs.db`, `desktop.ini`, `._*` с подписью AppleDouble). Они получают
решение «пропущен» с причиной в квитанции, не возвращаются и не мешают убрать папку или архив, где лежали. Служебный файл по шаблону
имени (`service_names`, `service_root_names`) и описание письма — данные, которые владелец счёл ненужными в архиве: их судьбу задаёт
настройка `service_fate` — `return` (по умолчанию, возврат) или `delete` (убрать, возврату исходника не мешает).
"""
import contextlib
import fcntl
import json
import os
import shutil
import time
from collections import Counter, namedtuple

import batches
import dedupe_index
import docdate
import gate
import ingest
import install
import intake
import journal as journal_mod
import known
import messages
import progress
import settings
import sources
import unpack
import vision as vision_mod

_SETTINGS = settings.startup()
SOURCES = sources.startup()           # таблица источников: по ней приёмка узнаёт базу старого документа
Summary = namedtuple("Summary", "batch counts receipts problems")
VisionRun = namedtuple("VisionRun", "documents pages waiting problems stopped busy")
STABLE = 30                         # секунд без изменений, прежде чем файл берётся (FR-47)
STATE = "inbox-state.json"
PENDING = os.path.join("index", "pending.jsonl")
PENDING_LOCK = os.path.join("index", "pending.lock")
# долги «ждёт описания» (FR-92): документ принят, а текста для поиска в нём нет — его опишет модель со зрением. Отдельный файл под тем же замком:
# долги индексации повторяются каждый проход и снимаются по успеху, а у долга описания свой бюджет (страницы и минуты), своя причина
# ожидания и свой счётчик неудач; в одном файле их пришлось бы различать на каждом шагу, и ошибка снимала бы чужой долг
VISION_PENDING = os.path.join("index", "vision-pending.jsonl")
VISION_LOCK = os.path.join("index", "vision.lock")       # один шаг описания за раз: проход и команда `flyarchive vision run` не сталкиваются
VISION_PAGES = 60                   # страниц за проход (настройка vision_pages)
VISION_MINUTES = 10                 # минут за проход (настройка vision_minutes)
VISION_MAX_LIMIT = 100_000          # предел --limit у команд vision
DEST = {"accept": ("corpus", "входящие"), "review": ("очередь",), "quarantine": ("карантин",)}
HOLD = ("garbled",)                 # с такими находками документ ждёт решения владельца, сколько бы баллов ни набрал
ALWAYS_REVIEW = ("llm_unchecked",)  # не проверенное моделью ждёт человека при любом пороге
IGNORED = ("desktop.ini", "thumbs.db")
ATTACH = ".вложения"
FRESH = 120                         # время изменения моложе этого — время распаковки, а не дата документа
sha256_of = gate.sha256_of


class InboxError(messages.CodedError):
    pass


class Busy(InboxError):
    pass


@contextlib.contextmanager
def locked(home):
    """Один разбор за раз: таймер и кнопка «разобрать сейчас» не должны столкнуться."""
    os.makedirs(home, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(home, "inbox.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(messages.make("inbox.busy"))
        yield
    finally:
        os.close(fd)


def _mkdirs(path, root):
    """Каталоги от root до path, каждый закрыт от других пользователей."""
    made = root
    for part in os.path.relpath(path, root).split(os.sep):
        if part in ("", "."):
            continue
        made = os.path.join(made, part)
        if not os.path.isdir(made):
            try:
                os.mkdir(made, 0o700)
            except FileExistsError:                 # параллельное решение владельца успело раньше
                if not os.path.isdir(made):
                    raise
    return path


# ── что готово к разбору ────────────────────────────────────────
def _snapshot(path):
    """Размер и время изменения записи: файла — его самого, каталога — всего, что внутри."""
    if not os.path.isdir(path):
        st = os.lstat(path)
        return [st.st_size, st.st_mtime_ns]
    out = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            st = os.lstat(os.path.join(root, f))
            out.append([os.path.relpath(os.path.join(root, f), path), st.st_size, st.st_mtime_ns])
    return out


def stable_entries(inbox, state_path, now, stable_seconds=STABLE):
    """Записи верхнего уровня, которые не менялись stable_seconds. Состояние помнится между проходами:
    копирование может сохранять старое время изменения, поэтому на одно время полагаться нельзя."""
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    fresh, ready = {}, []
    for name in sorted(os.listdir(inbox)):
        if name.startswith(".") or name.lower() in IGNORED:
            continue
        try:
            snap = _snapshot(os.path.join(inbox, name))
        except OSError:
            continue                              # исчез или недоступен: посмотрим в следующий раз
        old = state.get(name)
        since = old["since"] if old and old.get("snap") == snap else now
        fresh[name] = {"snap": snap, "since": since}
        if now - since >= stable_seconds:
            ready.append(name)
    tmp = state_path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(fresh, f, ensure_ascii=False)
    os.replace(tmp, state_path)
    return ready


def _batch_id(home, now):
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    batch, n = base, 1
    while os.path.exists(os.path.join(home, "квитанции", batch + ".jsonl")) or os.path.exists(os.path.join(home, "staging", "входящие-" + batch)):
        n += 1
        batch = f"{base}-{n}"
    return batch


# ── долги индексации ────────────────────────────────────────────
def _read_pending(home, name=PENDING):
    try:
        with open(os.path.join(home, name), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def _write_pending(home, items, name=PENDING):
    """Запись файла долгов целиком, через временный файл. Зовётся только из edit_pending: без замка писать нельзя."""
    path = os.path.join(home, name)
    if not items:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


@contextlib.contextmanager
def _pending_locked(home):
    """Замок файла долгов: принятия из очереди и проход разбора идут параллельно и не должны терять или задваивать чужие долги."""
    os.makedirs(os.path.join(home, "index"), mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(home, PENDING_LOCK), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)                                    # вместе с дескриптором замок отпускается


def edit_pending(home, change, name=PENDING):
    """Единственный путь правки файла долгов (FR-74): под замком читается файл, change(список) возвращает новый список, он записывается.
    Чтение, правка и запись — одно действие: чужая правка между ними невозможна. Возвращает записанный список.
    name — какой файл долгов: индексации (по умолчанию) или описания (VISION_PENDING); замок у них один."""
    with _pending_locked(home):
        items = change(_read_pending(home, name))
        _write_pending(home, items, name)
        return items


def add_debts(home, items, name=PENDING):
    """Дописывает долги; долг с тем же rel второй раз не записывается."""
    def add(current):
        have = {it["rel"] for it in current}
        return current + [it for it in items if it["rel"] not in have]
    return edit_pending(home, add, name)


def drop_debts(home, rels, name=PENDING):
    """Снимает долги с этими rel; чужие долги, записанные за это время, остаются."""
    rels = set(rels)
    return edit_pending(home, lambda current: [it for it in current if it["rel"] not in rels], name)


def _index(home, items, table, embed, step=None, watch=None):
    """Индексирует документы корпуса. Возвращает (итог, сообщение о сбое открытия таблицы).
    step(готово, всего, rel) — ход индексации по документам. watch() — контекст на время индексации: пока она идёт, проход
    не выглядит брошенным (progress.Progress.heartbeat); без него — как есть."""
    if not items:
        return ingest.Indexed([], [], [], 0), None
    if table is None:
        try:
            table = ingest.open_table(home)
        except ingest.TableMissing as e:                # таблицы нет: проход её не заводит, документы остаются долгами (заводит `flyarchive init`)
            return ingest.Indexed([], [], [(it["rel"], e.message) for it in items], 0), None
        except ingest.LibraryMissing as e:              # нет библиотеки индекса: причина названа сообщением lib.missing, документы остаются долгами
            return ingest.Indexed([], [], [(it["rel"], e.message) for it in items], 0), None
        except Exception as e:
            return ingest.Indexed([], [], [(it["rel"], f"индекс не открылся: {type(e).__name__}") for it in items], 0), None
    try:
        with (watch() if watch else contextlib.nullcontext()):
            result = ingest.index_documents(items, table, embed, **({"progress": step} if step else {}))
    except Exception as e:
        return ingest.Indexed([], [], [(it["rel"], f"{type(e).__name__}: {e}") for it in items], 0), None
    done = {it["rel"]: it["path"] for it in items}
    if result.indexed:
        with open(os.open(os.path.join(home, "index", "ingested.txt"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", encoding="utf-8") as f:
            for rel in result.indexed:
                f.write(done[rel] + "\n")       # полная пересборка индекса не возьмёт их второй раз
    if result.skipped:
        _note_blank(home, items, result.skipped)
    return result, None


def library_missing(why):
    """Причина сбоя индексации — нет библиотеки индекса (сообщение lib.missing из _index)?"""
    return isinstance(why, messages.Message) and why.code == "lib.missing"


def _once(problems):
    """Замечание lib.missing — одно на проход или запуск, сколько бы документов и этапов ни упёрлись в ту же библиотеку; остальные — как есть."""
    seen, out = set(), []
    for p in problems:
        if library_missing(p):
            if str(p) in seen:
                continue
            seen.add(str(p))
        out.append(p)
    return out


def _index_problem(rel, why, still=False):
    """Замечание о документе, которого нет в индексе. why — причина из _index: сообщение «таблицы индекса нет» даёт замечание с командой,
    которая её заводит; «нет библиотеки индекса» — само сообщение lib.missing (библиотека названа, документа в нём нет: он остаётся долгом);
    любая иная причина — слова как есть. still — повторная попытка: документ уже лежит в долгах."""
    if library_missing(why):
        return why
    if isinstance(why, messages.Message) and why.code == "index.no_table":
        return messages.make("problem.index_no_table", rel=rel)
    if still:
        return messages.make("problem.index_still", rel=rel, why=why)
    return messages.make("problem.index_failed", rel=rel, why=why)


def _note_blank(home, items, skipped):
    """Документ принят, а индексатор не нашёл в нём текста (картинка, PDF без текстового слоя): долг «ждёт описания» (FR-92).
    Сюда идут все, кто индексирует: проход, отдача долгов индексации, принятие из очереди. Сбой записи долга индексацию не рушит:
    документ найдёт `flyarchive vision backfill`."""
    by_rel = {it["rel"]: it for it in items}
    debts = []
    for rel, _ in skipped:
        it = by_rel.get(rel)
        if it is None or "text" in it or not vision_mod.wants(it.get("type"), rel):
            continue
        try:
            debts.append({"rel": rel, "path": it["path"], "sha256": sha256_of(it["path"]), "updated": it.get("updated"),
                          "space": it.get("space"), "type": it.get("type"), "added": time.strftime("%Y-%m-%d")})
        except OSError:
            continue
    if debts:
        try:
            add_debts(home, debts, VISION_PENDING)
        except OSError:
            pass


def _retry_pending(home, table, embed, tried=None, step=None, watch=None):
    """Отдача долгов: документы, которые лежат в корпусе, но не попали в индекс. Возвращает замечания.

    tried — rel, которые этот проход уже пробовал (по ним повтор не идёт: иначе одна беда считалась бы дважды); отданные сюда
    добавляются. Индексация долгая и идёт без замка; снятие — под замком и только того, что отдано: долги, записанные за это
    время принятиями, остаются и уйдут в отдачу в конце прохода. Долг, чьего файла в корпусе уже нет, снимается."""
    tried = set() if tried is None else tried
    due = [it for it in _read_pending(home) if it["rel"] not in tried]
    items = [it for it in due if os.path.exists(it["path"])]
    gone = {it["rel"] for it in due} - {it["rel"] for it in items}
    if gone:
        drop_debts(home, gone)
    if not items:
        return []
    tried.update(it["rel"] for it in items)
    if step:
        step(0, len(items), None)
    result, _ = _index(home, items, table, embed, step, watch)
    failed = dict(result.failed)
    drop_debts(home, [it["rel"] for it in items if it["rel"] not in failed])
    return [_index_problem(rel, why, still=True) for rel, why in result.failed]


def _hand_out_late(home, table, embed, tried, live):
    """Отдача долгов в конце прохода: тех, что записали, пока он шёл. Сбой отдачи — замечание, а не отказ: к этому времени
    файлы пачки уже разложены и квитанции записаны."""
    try:
        return _retry_pending(home, table, embed, tried, lambda done, total, rel: live.update("index", done, total, rel), live.heartbeat)
    except Exception as e:
        return [messages.make("problem.pending_failed", error=f"{type(e).__name__}: {e}")]


# ── описание схем и сканов моделью со зрением (FR-92) ───────────
class VisionBusy(Exception):
    """Шаг описания уже идёт (другой проход или команда `flyarchive vision run`)."""


@contextlib.contextmanager
def _vision_locked(home):
    """Один шаг описания за раз. Занят — VisionBusy сразу, не ожидание: ждать десять минут чужого описания незачем."""
    os.makedirs(os.path.join(home, "index"), mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(home, VISION_LOCK), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise VisionBusy()
        yield
    finally:
        os.close(fd)


def hand_out_vision(home, model, table=None, embed=None, pages=VISION_PAGES, minutes=VISION_MINUTES, limit=None, step=None, watch=None, clock=None):
    """Отдача долгов «ждёт описания»: модель описывает документы по одному, описание ложится в индекс фрагментами того же документа.

    За раз — не больше `pages` страниц (запросов к модели) и не дольше `minutes` минут, не больше `limit` документов; остальное остаётся долгом.
    Модель не ответила, не загружена, занята или срок вышел — остановка: дальше первого сбоя не идёт ни один документ, долги целы, в замечаниях
    одна запись с кодом. Сбой страницы или документа другие страницы и документы не теряет. Описание, которое не удалось проиндексировать,
    остаётся в файле описания: повтор идёт без модели. Долг снимается сразу после успеха документа (оборванный шаг доделает следующий).
    step(готово, всего, rel) — ход; watch() — контекст на время работы (сердцебиение хода); clock — часы (по умолчанию time.monotonic).
    Возвращает VisionRun(документов закончено, страниц отдано модели, ждёт долгов, замечания, сообщение об остановке или None, занято ли)."""
    clock = clock or time.monotonic
    try:
        with _vision_locked(home):
            return _hand_out_locked(home, model, table, embed, pages, minutes, limit, step, watch, clock)
    except VisionBusy:
        return VisionRun(0, 0, len(_read_pending(home, VISION_PENDING)), [], None, True)


def _hand_out_locked(home, model, table, embed, pages, minutes, limit, step, watch, clock):
    debts = _read_pending(home, VISION_PENDING)
    gone = [d for d in debts if not os.path.exists(d["path"])]           # документ убран из корпуса: описывать нечего
    if gone:
        drop_debts(home, [d["rel"] for d in gone], VISION_PENDING)
    todo = [d for d in debts if d not in gone]
    todo = todo if limit is None else todo[:limit]
    if not todo:
        return VisionRun(0, 0, len(_read_pending(home, VISION_PENDING)), [], None, False)
    deadline = clock() + minutes * 60
    used = docs = failed_pages = failed_docs = reached = 0
    problems, stop, last_why, updates = [], None, "", {}
    with (watch() if watch else contextlib.nullcontext()):
        if step:
            step(0, len(todo), None)
        for i, debt in enumerate(todo):
            if used >= pages or clock() >= deadline:
                break
            if step and i:
                step(i, len(todo), todo[i - 1]["rel"])
            rel = debt["rel"]
            try:
                out = vision_mod.describe(home, rel, debt["sha256"], model, budget=pages - used, deadline=deadline, clock=clock,
                                          tries=debt.get("tries", 0))
            except Exception as e:                                       # неожиданный сбой одного документа другие не теряют
                failed_docs += 1
                last_why = f"{type(e).__name__}: {e}"[:200]
                reached = i + 1
                continue
            used += out.asked
            problems.extend(out.problems)
            if out.stop is not None:
                stop = out.stop
                break
            reached = i + 1
            if out.failed:
                failed_pages += out.failed
            if out.failed or (out.why is not None and not out.complete):
                failed_docs += 1
            if out.why is not None:
                last_why = out.why.args["why"] if out.why.code == "vision.failed" else str(out.why)
            if out.drop:
                drop_debts(home, [rel], VISION_PENDING)
                continue
            if not out.complete:
                updates[rel] = {"tries": debt.get("tries", 0) + (1 if out.failed else 0), "why_msg": out.why.to_json() if out.why else None}
                continue
            if out.sections:
                item = {"path": debt["path"], "rel": rel, "updated": debt.get("updated"), "space": debt.get("space"), "title": debt.get("title"),
                        "source": debt.get("source"), "type": debt.get("type"), "text": out.sections}
                result, _ = _index(home, [item], table, embed)
                if result.failed:                                        # описание цело в файле: повтор пройдёт без модели
                    problems.extend(_index_problem(r, w) for r, w in result.failed)
                    updates[rel] = {"tries": debt.get("tries", 0), "why_msg": None}
                    continue
            drop_debts(home, [rel], VISION_PENDING)
            docs += 1
        if step:
            step(reached, len(todo), todo[reached - 1]["rel"] if reached else None)
    for debt in todo[reached:]:                        # не тронутые в этот раз ждут по причине остановки, а не по старой
        if stop is not None or debt.get("why_msg") is not None:
            updates[debt["rel"]] = {"tries": debt.get("tries", 0), "why_msg": stop.to_json() if stop is not None else None}
    if stop is not None:
        problems.append(stop)
    if failed_pages or failed_docs:
        problems.append(messages.make("problem.vision_failed", pages=failed_pages, docs=failed_docs, why=last_why))
    if updates:
        def apply(current):
            out = []
            for d in current:
                if d["rel"] in updates:
                    d = {**d, **updates[d["rel"]]}
                    if d.get("why_msg") is None:
                        d.pop("why_msg", None)
                out.append(d)
            return out
        edit_pending(home, apply, VISION_PENDING)
    return VisionRun(docs, used, len(_read_pending(home, VISION_PENDING)), _once(problems), stop, False)


def _vision_stage(home, model, table, embed, live, pages, minutes, clock):
    """Этап vision прохода: долги описания отдаются в конце. Выключен (model None), долгов нет или шаг занят — ничего. Сбой самого шага
    проход не роняет: одна запись в замечаниях, долги целы."""
    if model is None or not _read_pending(home, VISION_PENDING):
        return []
    if not live.on:                                    # у прохода без новых файлов пачки нет, ход всё равно виден
        live.start(None, "vision")
    try:
        run = hand_out_vision(home, model, table, embed, pages=pages, minutes=minutes, clock=clock,
                              step=lambda done, total, rel: live.update("vision", done, total, rel), watch=live.heartbeat)
    except Exception as e:
        return [messages.make("problem.vision_aborted", error=f"{type(e).__name__}: {e}"[:200])]
    return run.problems


def _old_meta(corpus, rel, full):
    """(база, пространство, название, дата) старого документа корпуса так, как их записал бы основной индексатор;
    None — корень корпуса таблице источников неизвестен."""
    return SOURCES.old_meta(corpus, rel, full)


def backfill_vision(home, limit, source=None, dry_run=False, indexed=None, table=None):
    """Старые документы корпуса без текста в индексе (картинки и PDF): записывает их долгами «ждёт описания», модель не зовёт.
    limit — сколько документов записать (обязателен: старое описывается только по команде владельца и порциями); source — только эта база индекса;
    dry_run — только показать; indexed — пути, которые в индексе есть (по умолчанию читается живая таблица, table — подставная).
    Возвращает {"candidates": сколько нашлось, "added": сколько записано, "dry_run", "files": пути выбранных}."""
    if limit is None:
        raise InboxError(messages.make("vision.limit_needed"))
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= VISION_MAX_LIMIT:
        raise InboxError(messages.make("vision.bad_limit", max=VISION_MAX_LIMIT))
    if indexed is None:
        indexed = dedupe_index.indexed_paths(table if table is not None else ingest.open_table(home))
    have = {dedupe_index.norm(p) for p in indexed}
    corpus = os.path.join(home, "corpus")
    waiting = {d["rel"] for d in _read_pending(home, VISION_PENDING)}
    found = []
    for folder, dirs, files in os.walk(corpus):
        dirs[:] = sorted(d for d in dirs if d != "_карантин")           # карантин в индекс не идёт и описываться не должен
        for name in files:
            full = os.path.join(folder, name)
            if name.startswith("._") or not vision_mod.wants(None, name) or os.path.islink(full):
                continue
            rel = os.path.relpath(full, corpus).replace(os.sep, "/")
            if dedupe_index.norm(rel) in have or rel in waiting:
                continue
            meta = _old_meta(corpus, rel, full)
            if meta is None or (source and meta[0] != source):
                continue
            found.append((rel, full, meta))
    found.sort(key=lambda f: f[0])
    chosen = found[:limit]
    added = 0
    if chosen and not dry_run:
        debts = []
        for rel, full, (src, space, title, updated) in chosen:
            debts.append({"rel": rel, "path": full, "sha256": sha256_of(full), "updated": updated, "space": space, "type": os.path.splitext(rel)[1].lstrip(".").lower(),
                          "title": title, "source": src, "added": time.strftime("%Y-%m-%d")})
        before = len(_read_pending(home, VISION_PENDING))
        added = len(add_debts(home, debts, VISION_PENDING)) - before
    return {"candidates": len(found), "added": added, "dry_run": bool(dry_run), "files": [f[0] for f in chosen]}


def vision_state(home, sha, rel=None):
    """Живое состояние описания документа по sha256 — для истории пачки: квитанция пишется до описания и после уже не меняется.
    None — описывать документу не нужно или о нём нечего сказать. Иначе {"state": "described" | "waiting", …}.
    Если названы страницы, описание которых правила не пропустили, и дан путь документа rel, в `notes` — сообщения vision.blocked о них."""
    if not isinstance(sha, str) or not vision_mod.SHA.fullmatch(sha):
        return None
    record = vision_mod.load(home, sha)
    debt = next((d for d in _read_pending(home, VISION_PENDING) if d.get("sha256") == sha), None)
    if record is None and debt is None:
        return None
    if debt is None:
        state = {"state": "described", "pages": len(record["pages"]), "total": record.get("total", 0), "truncated": bool(record.get("truncated")),
                 "model": record.get("model"), "seconds": record.get("seconds"), "date": record.get("date"), "skipped": list(record.get("skipped", []))}
        blocked = [s for s in state["skipped"] if s.get("why") == "blocked" and isinstance(s.get("rule"), str)]
        if blocked and rel:
            state["notes"] = [messages.make("vision.blocked", rel=rel, page=s["page"], rule=s["rule"]).to_json() for s in blocked]
        return state
    reason = debt.get("why_msg") or (messages.make("vision.waiting") if vision_wanted(load_config(home)) else messages.make("vision.off")).to_json()
    return {"state": "waiting", "pages": len(record["pages"]) if record else 0, "total": record.get("total", 0) if record else None,
            "reason": reason["text"], "reason_msg": reason}


def forget_vision(home, sha, rel=None, keep=False):
    """Документ убран из архива (`flyarchive doc delete`): долг описания по его пути снимается, а файл описания и долги по этому содержимому — если
    оно больше нигде не лежит (keep=True: то же содержимое есть под другим путём, описание ему ещё нужно)."""
    if not keep:
        vision_mod.forget(home, sha)
    edit_pending(home, lambda current: [d for d in current if not (d["rel"] == rel or (not keep and d.get("sha256") == sha))], VISION_PENDING)


# ── разбор ──────────────────────────────────────────────────────
def _mtimes(inbox, names):
    out = {}
    for name in names:
        top = os.path.join(inbox, name)
        if os.path.isfile(top):
            out[name] = os.stat(top).st_mtime
            continue
        for root, _, files in os.walk(top):
            for f in files:
                p = os.path.join(root, f)
                out[os.path.relpath(p, inbox).replace(os.sep, "/")] = os.lstat(p).st_mtime
    return out


def _date(v, final, by_name, mtimes, accepted, real_now, notes=None):
    """Дата документа и откуда она: FR-45 и FR-45а. notes — список замечаний (docdate.date_of)."""
    name = v["name"]
    folder, _, base = name.rpartition("/")
    if folder.endswith(ATTACH):                    # вложение письма получает дату письма
        stem = folder[:-len(ATTACH)]
        for ext in (".eml", ".msg"):
            letter = by_name.get(stem + ext)
            if letter and letter.get("date") and docdate.plausible(*letter["date"].split("-"), accepted):
                return letter["date"], "письмо"
        day = docdate._from_name(os.path.basename(folder), accepted)
        if day:
            return day, "каталог вложений"
    mtime = mtimes.get(name)
    if mtime is None and v.get("archive"):          # файл из архива: время изменения сохранено распаковкой
        try:
            mtime = os.stat(final).st_mtime
            if abs(real_now - mtime) < FRESH:
                mtime = None
        except OSError:
            mtime = None
    return docdate.date_of(final, v["type"], base, accepted, mtime, notes)


def process(home, inbox, now=None, table=None, embed=None, checker=None, stable_seconds=STABLE, limits=None, jobs=1,
            hold_rules=HOLD, threshold=None, clock=None, vision=None, vision_pages=VISION_PAGES, vision_minutes=VISION_MINUTES,
            vision_clock=None, service_names=(), service_root_names=(), service_fate="return"):
    """Один проход по входящей папке. checker — проверяющий моделью (llm_check.Checker) или None.
    threshold — порог баллов из настройки: до него документ принимается, выше — идёт в очередь.
    service_names, service_root_names — шаблоны имён служебных файлов из настройки (по умолчанию пусто): такие файлы пропускаются.
    service_fate — судьба пропущенного по шаблону файла и описания письма: return (по умолчанию и при любом другом значении) — возврат,
    delete — убрать, возврату исходника не мешает. Пустые файлы и следы операционных систем убираются при любой судьбе.
    clock — часы файла хода (по умолчанию настоящие). Файл хода лежит, пока идёт проход, и убирается, чем бы тот ни кончился.
    vision — модель, которая описывает картинки и сканы (vision.Model), или None: шаг описания выключен. Долги описания записываются в любом
    случае, а отдаются в конце прохода (этап vision) не больше чем на vision_pages страниц и vision_minutes минут; vision_clock — часы этого предела."""
    now = time.time() if now is None else now
    if not os.path.isdir(inbox):
        raise InboxError(messages.make("inbox.folder_missing", path=inbox))
    with locked(home), progress.Progress(home, clock=clock) as live:       # замок первый: занятый проход чужой файл хода не трогает
        tried = set()                                   # долги, которые этот проход уже отдавал на индексацию
        problems = _retry_pending(home, table, embed, tried)
        names = stable_entries(inbox, os.path.join(home, STATE), now, stable_seconds)
        if not names:
            problems += _hand_out_late(home, table, embed, tried, live)         # принятия шли, пока проход отдавал долги
            problems += _vision_stage(home, vision, table, embed, live, vision_pages, vision_minutes, vision_clock)
            return Summary(None, {}, None, _once(problems))
        db = os.path.join(home, "index", "known.sqlite")
        archive = known.Known(db)
        if archive.info() is None:
            raise InboxError(messages.make("known.not_built"))
        started = time.time()
        batch = _batch_id(home, now)
        live.start(batch)
        work = os.path.join(home, "staging", "входящие-" + batch)
        receipts, tries = [], []

        def judge(only, tick=None):
            """Приёмка названных записей в свой рабочий каталог. Возвращает (каталог, решения)."""
            into = os.path.join(work, str(len(tries)))
            tries.append(into)
            result = intake.run(inbox, into, limits=limits or unpack.Limits(), jobs=jobs, corpus=os.path.join(home, "corpus"),
                                archive=archive, llm=checker, only=set(only), tick=tick, service_names=service_names,
                                service_root_names=service_root_names)
            with open(result.report_jsonl, encoding="utf-8") as f:
                verdicts = [json.loads(line) for line in f if line.strip()]
            if threshold is not None:
                for v in verdicts:               # порог из настройки; карантин и пропуск от него не зависят
                    if v["decision"] in ("accept", "review"):
                        forced = any(f["rule"] in ALWAYS_REVIEW for f in v["findings"])
                        v["decision"] = "review" if forced or v["score"] > threshold else "accept"
            return into, verdicts

        def settle(only, into, verdicts, tracker=None):
            book = {}
            try:
                problems.extend(_settle(home, inbox, batch, into, verdicts, only, _mtimes(inbox, only), now, table, embed,
                                        hold_rules, db, book, tracker, tried, vision is not None, service_fate))
            except Exception as e:               # квитанции о том, что уже легло на место, пишутся в любом случае
                problems.append(messages.make("problem.settle_aborted", error=f"{type(e).__name__}: {e}"))
            for r in book.values():
                r.pop("_final", None)
            receipts.extend(book.values())

        try:
            os.makedirs(os.path.dirname(work), mode=0o700, exist_ok=True)
            os.mkdir(work, 0o700)
            try:
                whole = judge(names, live.update)
            except intake.IntakeError:
                raise
            except Exception:
                whole = None
            if whole is not None:
                settle(names, *whole, tracker=live)
            else:                                # приёмка упала: записи разбираются по одной, сбойная возвращается владельцу
                for n, name in enumerate(names):
                    live.update("intake", n, len(names), names[n - 1] if n else None)      # в ходе — записи верхнего уровня
                    try:
                        one = judge([name])
                    except intake.IntakeError:
                        raise
                    except Exception as e:
                        rec, note = _reject(inbox, batch, name, e, now)
                        receipts.append(rec)
                        problems.append(note)
                        continue
                    settle([name], *one)
                live.update("intake", len(names), len(names), names[-1])
        except intake.IntakeError as e:
            raise InboxError(messages.of(e))
        finally:
            shutil.rmtree(work, ignore_errors=True)
            with contextlib.suppress(OSError):
                os.rmdir(os.path.dirname(work))
        receipts.sort(key=lambda r: r["name"])
        counts = dict(Counter(r["decision"] for r in receipts))
        folder = _mkdirs(os.path.join(home, "квитанции"), home)
        path = os.path.join(folder, batch + ".jsonl")
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
            for r in receipts:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        problems += _hand_out_late(home, table, embed, tried, live)           # долги, записанные за время прохода: после квитанций
        problems += _vision_stage(home, vision, table, embed, live, vision_pages, vision_minutes, vision_clock)       # описание — тоже после квитанций
        problems[:] = _once(problems)                                          # нет библиотеки индекса: одно замечание на проход, а не по этапу и по документу
        try:                                       # длительность и замечания рядом с квитанциями; сбой не должен терять квитанции
            batches.write_meta(home, batch, started, time.time(), problems)
        except Exception as e:
            problems.append(messages.make("problem.meta_not_written", error=f"{type(e).__name__}: {e}"))
        journal_mod.Journal(os.path.join(home, "logs", "access.jsonl")).write(
            server="inbox", client="владелец", tool="batch", params={"batch": batch, **counts},
            status=1 if problems else 0, outcome=f"с замечаниями: {len(problems)}" if problems else "ok",
            ms=int((time.time() - started) * 1000))
        return Summary(batch, counts, path, problems)


def _back_root(inbox):
    inbox = os.path.abspath(inbox)
    return os.path.join(os.path.dirname(inbox), os.path.basename(inbox) + "-возврат")


def _back(inbox, batch):
    """Папка возврата рядом со входящей: туда уходит то, что архив не взял."""
    return os.path.join(_back_root(inbox), batch)


def _reject(inbox, batch, name, error, now):
    """Запись, на которой приёмка упала, возвращается владельцу: иначе она останавливала бы разбор на каждом проходе."""
    why = f"{type(error).__name__}: {error}"[:300]
    reason = messages.make("reason.intake_failed", why=why)
    rec = {"name": name, "sha256": "", "size": 0, "type": "", "decision": "failed", "score": 0, "findings": [],
           "reason": str(reason), "reason_msg": reason.to_json(), "checked_by": None, "path": None, "batch": batch,
           "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "date": None, "date_source": None, "verified": None,
           "indexed": False, "archive": None, "archive_sha256": None, "inner": None, "duplicate_of": None, "returned": None}
    try:
        target = os.path.join(_back(inbox, batch), name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(os.path.join(inbox, name), target)
    except OSError as e:
        return rec, messages.make("problem.intake_failed_kept", name=name, why=why, os_error=str(e))
    rec["returned"] = f"{batch}/{name}"
    return rec, messages.make("problem.intake_failed_returned", name=name, why=why)


def _drop(path, stop):
    """Убирает неудавшуюся копию и опустевшие каталоги над ней, не выше stop."""
    with contextlib.suppress(OSError):
        os.remove(path)
    folder = os.path.dirname(path)
    with contextlib.suppress(OSError):
        while os.path.commonpath([folder, stop]) == stop:
            os.rmdir(folder)
            folder = os.path.dirname(folder)


def _settle(home, inbox, batch, work, verdicts, names, mtimes, now, table, embed, hold_rules, db, receipts, live=None, tried=None,
            vision_on=False, service_fate="return"):
    """Раскладка по местам, сверка, даты, база известного, индекс и судьба исходников. Квитанции пишутся в receipts
    по ходу работы: если раскладка оборвётся, запись о сделанном останется. Возвращает замечания.
    live — ход разбора (progress.Progress) или None: этапы settle, index, sources.
    tried — множество rel: документы, отданные здесь на индексацию, добавляются в него (отдача в конце прохода их не повторяет).
    vision_on — шаг описания включён: в квитанции документа без текста названа причина ожидания (vision.waiting или vision.off).
    service_fate — return или delete: что делать с пропущенным по шаблону имени файлом и с описанием письма (см. DEFAULTS)."""
    note = live.update if live is not None else (lambda *args: None)
    problems = []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    accepted_day = time.strftime("%Y-%m-%d", time.localtime(now))
    real_now = time.time()
    by_name = {v["name"]: v for v in verdicts}
    placed_sha, failed = set(), set()

    ordered = sorted(verdicts, key=lambda v: v["name"])
    for n, v in enumerate(ordered):
        note("settle", n, len(ordered), ordered[n - 1]["name"] if n else None)      # готово — всё, что до этого файла
        name = v["name"]
        rec = {"name": name, "sha256": v["sha256"], "size": v["size"], "type": v["type"], "decision": v["decision"],
               "score": v["score"], "findings": v["findings"], "reason": v.get("reason", ""), "reason_msg": v.get("reason_msg"),
               "checked_by": v.get("checked_by"),
               "path": None, "batch": batch, "time": stamp, "date": None, "date_source": None, "verified": None,
               "indexed": False, "archive": v.get("archive"), "archive_sha256": v.get("archive_sha256"),
               "inner": v.get("inner"), "duplicate_of": v.get("duplicate_of"), "returned": None}
        if v.get("notes"):
            rec["notes"] = v["notes"]
            if "notes_msg" in v:                       # у отчёта старого образца сообщений к примечаниям нет
                rec["notes_msg"] = v["notes_msg"]
        receipts[name] = rec
        if v["decision"] not in DEST or not v.get("placed"):
            continue
        held = [f["rule"] for f in v["findings"] if f["rule"] in hold_rules] if v["decision"] == "accept" else []
        if held:
            reason = messages.make("reason.held", rule=held[0])
            rec.update(decision="review", reason=str(reason), reason_msg=reason.to_json())
        root = os.path.join(home, *DEST[rec["decision"]], batch)
        final = os.path.join(root, *name.split("/"))
        date = (None, None)
        try:                                       # сбой на одном файле не останавливает остальные
            _mkdirs(os.path.dirname(final), home)
            shutil.move(os.path.join(work, *v["placed"].split("/")), final)     # где бы приёмка его ни держала
            os.chmod(final, 0o600)
            trouble = None if sha256_of(final) == v["sha256"] else messages.make("problem.copy_mismatch", name=name)
            if trouble is None and rec["decision"] in ("accept", "review") and v.get("family") != "archive":
                notes = []
                date = _date(v, final, by_name, mtimes, accepted_day, real_now, notes)
                problems.extend(n for n in notes if n not in problems)       # нет библиотеки писем: называется один раз, а не на каждое письмо
        except Exception as e:
            trouble = messages.make("problem.not_placed", name=name, error=f"{type(e).__name__}: {e}")
        if trouble:
            _drop(final, root)
            rec.update(verified=False)
            failed.add(name)
            problems.append(trouble)
            continue
        placed_sha.add(v["sha256"])
        rec.update(verified=True, path="/".join(DEST[rec["decision"]][-1:] + (batch, name)), _final=final,
                   date=date[0], date_source=date[1])
    note("settle", len(ordered), len(ordered), ordered[-1]["name"] if ordered else None)

    # принятое сразу попадает в базу известного: следующая пачка должна его узнавать
    took = [r for r in receipts.values() if r["decision"] == "accept" and r["verified"]]
    if took:
        try:
            known.add(db, [(r["path"], r["size"], os.stat(r["_final"]).st_mtime_ns, r["sha256"], by_name[r["name"]].get("mid"),
                            by_name[r["name"]].get("fp"), r["date"] if by_name[r["name"]].get("family") == "mail" else None)
                           for r in took])
        except Exception as e:                     # без записи в базе известного следующий приём взял бы документ второй раз
            for r in took:
                _drop(r.pop("_final"), os.path.join(home, *DEST["accept"], batch))
                placed_sha.discard(r["sha256"])
                r.update(verified=False, path=None, date=None, date_source=None)
                failed.add(r["name"])
            problems.append(messages.make("problem.known_rejected", error=f"{type(e).__name__}: {e}"))
            took = []
    if took:
        items = [{"path": r["_final"], "rel": r["path"], "updated": r["date"], "space": batch, "type": r["type"]} for r in took]
        shown = {r["path"]: r["name"] for r in took}                 # в ходе — имя во входящей папке, а не путь в корпусе
        note("index", 0, len(items), None)
        if tried is not None:
            tried.update(it["rel"] for it in items)
        indexed, _ = _index(home, items, table, embed, lambda done, total, rel: note("index", done, total, shown.get(rel, rel)),
                            live.heartbeat if live is not None else None)
        done = set(indexed.indexed)
        notes = dict(indexed.skipped)
        for r in took:
            r["indexed"] = r["path"] in done
            if r["path"] in notes:
                r["index_note"] = notes[r["path"]]
                if vision_mod.wants(r["type"], r["name"]):     # квитанция пишется до описания: в ней — почему документ ещё не описан
                    why = (messages.make("vision.waiting") if vision_on else messages.make("vision.off"))
                    r["vision"] = {"state": "waiting", "reason": str(why), "reason_msg": why.to_json()}
        if indexed.failed:
            bad = dict(indexed.failed)
            add_debts(home, [it for it in items if it["rel"] in bad])
            problems += [_index_problem(rel, why) for rel, why in indexed.failed]

    # судьба исходников: убрать то, что сохранено точной копией или не содержимое; вернуть то, что архив не взял
    again = known.Known(db)
    back = _back(inbox, batch)
    note("sources", 0, len(receipts), None)

    def kept(rec):
        """Содержимое сохранено байт в байт: в корпусе, очереди, карантине или уже лежит в архиве."""
        if rec["verified"]:
            return True
        return rec["decision"] == "duplicate" and (rec["sha256"] in placed_sha or again.find(sha256=rec["sha256"]) is not None)

    def spare(rec):
        """Файл, который не нужно ни хранить, ни возвращать: пустой или след операционной системы (не содержимое), а при service_fate=delete —
        ещё и служебный файл по шаблону имени и описание письма (данные, которые владелец счёл ненужными)."""
        why = by_name[rec["name"]].get("skipped_as")
        return rec["decision"] == "skip" and (why == "trace" or (why == "service" and service_fate == "delete"))

    def give_back(rel):
        target = os.path.join(back, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(os.path.join(inbox, *rel.split("/")), target)
        return f"{batch}/{rel}"

    archives = sorted((r for r in receipts.values() if by_name[r["name"]].get("family") == "archive" and not r["archive"]),
                      key=lambda r: r["name"])
    in_archive = set()
    for a in archives:                             # архив, лежавший во входящей папке файлом
        members = [r for r in receipts.values() if r["name"].startswith(a["name"] + "/")]
        in_archive.update(r["name"] for r in members)
        source = os.path.join(inbox, *a["name"].split("/"))
        lost = [r for r in members if by_name[r["name"]].get("family") != "archive" and not kept(r) and not spare(r) and r["name"] not in failed]
        broken = [r for r in members if r["name"] in failed] or (a["verified"] is False)
        if a["decision"] == "quarantine" and not a["verified"]:
            broken = True
        try:
            if broken:
                a["source"] = "оставлен"
            elif lost:
                a["returned"] = give_back(a["name"])
                a["source"] = "возвращён"
                shown = ", ".join(r["name"][len(a["name"]) + 1:] for r in lost[:5])
                a["note"] = f"архив не взял {len(lost)} из {len(members)} файлов: {shown}" + (" …" if len(lost) > 5 else "")
            else:
                os.remove(source)
                a["source"] = "убран"
        except OSError as e:
            a["source"] = "оставлен"
            problems.append(messages.make("problem.source_not_removed", name=a["name"], os_error=str(e)))
    ordered = sorted(receipts.values(), key=lambda r: r["name"])
    for n, r in enumerate(ordered):
        note("sources", n, len(ordered), ordered[n - 1]["name"] if n else None)
        name = r["name"]
        if name in in_archive or r in archives or by_name[name].get("archive"):
            continue                               # файл из архива: его судьбу решает архив
        source = os.path.join(inbox, *name.split("/"))
        if not os.path.lexists(source):
            continue
        if name in failed:
            continue
        try:
            if kept(r) or spare(r):                # стирается то, что лежит точной копией, и то, что не содержимое; остальное, что архив не взял, — возврат
                os.remove(source)
            else:
                r["returned"] = give_back(name)
        except OSError as e:
            problems.append(messages.make("problem.source_not_removed", name=name, os_error=str(e)))
    note("sources", len(ordered), len(ordered), ordered[-1]["name"] if ordered else None)
    for name in names:                             # пустые каталоги после разбора не нужны
        top = os.path.join(inbox, name)
        if os.path.isdir(top):
            for root, dirs, files in os.walk(top, topdown=False):
                with contextlib.suppress(OSError):
                    if not os.listdir(root):
                        os.rmdir(root)
    return problems


# ── настройка, таймер, состояние (FR-41) ────────────────────────
PERIODS = (1, 5, 10, 30, 60)
# Настройки приёмки лежат в inbox.json каталога архива, в общую схему настроек (settings.py) не входят: их меняют команда `inbox set`
# и раздел Intake в DSH. cloud — запасная облачная модель: по умолчанию выключена, текст документов уходит с машины, только когда
# владелец сам её включил.
# vision: None — не задано, описание изображений следует за проверкой моделью (llm); True и False — владелец задал своё
# service_names, service_root_names — шаблоны имён служебных файлов (как в fnmatch, без учёта регистра): такой файл приёмка пропускает.
# Первый список действует в любой папке, второй — только в корне входящей папки или архива-файла. По умолчанию пусты: никакое имя
# само по себе файл «служебным» не делает.
# service_fate — что разбор делает с пропущенным по этим шаблонам файлом и с описанием письма (`.json` рядом с одноимённым письмом): это данные,
# которые владелец мог счесть ненужными в архиве. return (по умолчанию) — файл уходит в папку возврата, а папка или архив-файл, где он лежал,
# возвращается по общим правилам; delete — файл убирается и возврату исходника не мешает. Пустой файл и следы операционных систем настройки
# не знают: они не содержимое и убираются всегда (intake.trace_reason).
FATES = ("return", "delete")
DEFAULTS = {"period": 30, "llm": True, "cloud": False, "stable_seconds": STABLE,
            "threshold": gate.THRESHOLD, "max_gb": 2, "max_files": 5000, "max_ratio": 100, "depth": 3,
            "vision": None, "vision_pages": VISION_PAGES, "vision_minutes": VISION_MINUTES,
            "service_names": [], "service_root_names": [], "service_fate": "return"}
RANGES = {"threshold": (0, 100), "max_gb": (0.01, 500), "max_files": (1, 2_000_000), "max_ratio": (2, 10_000), "depth": (1, 10),
          "vision_pages": (1, 1000), "vision_minutes": (1, 240)}
SERVICE_KEYS = ("service_names", "service_root_names")
SERVICE_MAX, SERVICE_LEN = 50, 200          # шаблонов в списке и знаков в шаблоне
SERVICE, TIMER = install.INBOX_SERVICE, install.INBOX_TIMER


def vision_wanted(cfg):
    """Описывать ли изображения: заданная владельцем настройка vision, а пока она не задана — как проверка моделью (llm)."""
    return cfg["llm"] if cfg["vision"] is None else cfg["vision"]


def default_inbox(home):
    return os.path.join(home, "входящие")


def load_config(home):
    """Настройка входящих: папка, период, проверка моделью, запасная облачная модель."""
    cfg = {"inbox": default_inbox(home), **DEFAULTS}
    try:
        with open(os.path.join(home, "inbox.json"), encoding="utf-8") as f:
            saved = json.load(f)
        cfg.update({k: saved[k] for k in cfg if k in saved})
    except (OSError, ValueError):
        pass
    for key in SERVICE_KEYS:                 # свой список у каждой настройки: умолчание общее и не должно меняться
        if isinstance(cfg[key], list):
            cfg[key] = list(cfg[key])
    return cfg


def service_patterns(cfg):
    """(шаблоны в любой папке, шаблоны только в корне) из настройки: годятся только непустые строки. Испорченное вручную значение не действует;
    отказ на нём даёт запись настройки (check_config)."""
    def good(value):
        return tuple(p for p in value if isinstance(p, str) and p) if isinstance(value, list) else ()
    return good(cfg.get("service_names")), good(cfg.get("service_root_names"))


def service_fate(cfg):
    """Судьба служебных файлов из настройки: delete только при точном значении; испорченное вручную значение — возврат, то есть ничего не стирается."""
    return "delete" if cfg.get("service_fate") == "delete" else "return"


def check_config(home, cfg):
    if cfg["period"] not in PERIODS:
        raise InboxError(messages.make("inbox.bad_period", periods=", ".join(map(str, PERIODS))))
    if not isinstance(cfg["llm"], bool) or not isinstance(cfg["cloud"], bool):
        raise InboxError(messages.make("inbox.bad_switch"))
    if cfg["vision"] is not None and not isinstance(cfg["vision"], bool):
        raise InboxError(messages.make("cli.bad_switch", name="vision"))
    for key, (low, high) in RANGES.items():
        value = cfg[key]
        whole = key != "max_gb"
        if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)) or not low <= value <= high:
            if whole:
                raise InboxError(messages.make("inbox.bad_int", key=key, low=low, high=high))
            raise InboxError(messages.make("inbox.bad_number", key=key, low=low, high=high))
    for key in SERVICE_KEYS:             # сравнивается имя файла без каталога: шаблон с косой чертой не сработал бы никогда
        value = cfg.get(key, [])
        if (not isinstance(value, list) or len(value) > SERVICE_MAX
                or not all(isinstance(p, str) and p.strip() and len(p) <= SERVICE_LEN and not any(c in p for c in "/\\\r\n\0") for p in value)):
            raise InboxError(messages.make("inbox.bad_service_names", key=key, max=SERVICE_MAX, length=SERVICE_LEN))
    fate = cfg.get("service_fate", "return")
    if not isinstance(fate, str) or fate not in FATES:
        raise InboxError(messages.make("inbox.bad_service_fate", values=", ".join(FATES)))
    path, base = os.path.realpath(cfg["inbox"]), os.path.realpath(home)
    if path != os.path.realpath(default_inbox(home)) and (path == base or path.startswith(base + os.sep) or base.startswith(path + os.sep)):
        raise InboxError(messages.make("inbox.folder_inside_archive", archive=base))


def require_folder(path):
    """Папка для смены из плагина (--must-exist): уже есть и не корень и не домашний каталог (ссылка на них — тоже они).
    Что она не внутри архива и не содержит его, проверяет check_config. Каталоги здесь не создаются."""
    if not os.path.isdir(path):
        raise InboxError(messages.make("inbox.folder_missing", path=path))
    if os.path.realpath(path) in (os.sep, os.path.realpath(os.path.expanduser("~"))):
        raise InboxError(messages.make("inbox.folder_too_wide", path=path))


def save_config(home, cfg):
    check_config(home, cfg)
    os.makedirs(cfg["inbox"], exist_ok=True)
    tmp = os.path.join(home, "inbox.json.tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(home, "inbox.json"))


def units(home, period):
    """Тексты службы и таймера systemd для разбора по расписанию: {имя файла: текст}. Пишет их тот же код и по тем же шаблонам, что
    у установки (install.py): код служба берёт из репозитория, данные и журналы — из каталога архива."""
    return install.inbox_units(home, period)


def units_dir():
    """Каталог пользовательских служб systemd: настройка units_dir (по умолчанию ~/.config/systemd/user)."""
    return _SETTINGS["units_dir"]


def install_timer(home, period, run=None):
    """Пишет службу и таймер и включает таймер. Возвращает сообщение (messages.Message) о сбое systemd или None."""
    import subprocess
    os.makedirs(os.path.join(home, "logs"), mode=0o700, exist_ok=True)
    install.write_units(units_dir(), units(home, period))
    run = run or (lambda *args: subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=60))
    for args in (install.RELOAD, *install.TIMER_START):
        try:
            r = run(*args)
        except (OSError, subprocess.SubprocessError) as e:
            return messages.make("inbox.timer_no_systemd", error_type=type(e).__name__)
        if r.returncode != 0:
            return messages.make("inbox.timer_failed", command=" ".join(args), why=(r.stderr or "").strip()[:200])
    return None


def start_service(run=None):
    """Запускает службу разбора и сразу возвращается: ни конца разбора, ни индексации не ждёт. Сбой systemd — InboxError с сообщением
    (cli.kick_no_systemd, cli.kick_failed, cli.kick_failed_silent). Для кнопки «разобрать сейчас» это отказ, а для принятия без индексации
    нет: долг записан, и его отдаст ближайший проход. run(*аргументы systemctl --user) подставляют тесты."""
    import subprocess
    run = run or (lambda *args: subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30))
    try:
        r = run("start", "--no-block", SERVICE)
    except (OSError, subprocess.SubprocessError) as e:
        raise InboxError(messages.make("cli.kick_no_systemd", error_type=type(e).__name__))
    if r.returncode != 0:
        why = (r.stderr or "").strip()[:200]
        if why:
            raise InboxError(messages.make("cli.kick_failed", why=why))
        raise InboxError(messages.make("cli.kick_failed_silent"))


def _count_files(root):
    return sum(len(files) for _, _, files in os.walk(root)) if os.path.isdir(root) else 0


def status(home):
    cfg = load_config(home)
    waiting = 0
    if os.path.isdir(cfg["inbox"]):
        waiting = sum(1 for n in os.listdir(cfg["inbox"]) if not n.startswith(".") and n.lower() not in IGNORED)
    # последняя пачка — по порядку номеров (две в одну секунду: «…-2» новее); решения владельца в счёт не идут, битые строки пропускаются
    (newest,) = batches.list_batches(home, 1)["batches"] or [None]
    last = {"batch": newest["id"], "time": newest["time"], **newest["counts"]} if newest else None
    queue, quarantine = _count_files(os.path.join(home, "очередь")), _count_files(os.path.join(home, "карантин"))
    names, root_names = service_patterns(cfg)
    return {"inbox": cfg["inbox"], "period": cfg["period"], "llm": cfg["llm"], "cloud": cfg["cloud"],
            **{k: cfg[k] for k in RANGES}, "service_names": list(names), "service_root_names": list(root_names), "service_fate": service_fate(cfg),
            "timer": os.path.exists(os.path.join(units_dir(), TIMER)), "waiting": waiting,
            "queue": queue, "quarantine": quarantine,
            "pending": len(_read_pending(home)), "last_batch": last,
            "returned": _back_root(cfg["inbox"]), "home": home, "attention": queue + quarantine,
            "problems": newest["problems"] if newest else 0, "progress": progress.read(home),
            "vision": vision_wanted(cfg), "vision_pending": len(_read_pending(home, VISION_PENDING)), "vision_done": vision_mod.count(home)}


def limits_of(cfg):
    """Пределы распаковки архивов из настройки."""
    return unpack.Limits(max_bytes=int(cfg["max_gb"] * 1024 ** 3), max_files=cfg["max_files"], max_ratio=cfg["max_ratio"],
                         max_depth=cfg["depth"])

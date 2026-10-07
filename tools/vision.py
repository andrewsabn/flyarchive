"""Описание схем и сканов локальной моделью со зрением (FR-92): шаг описания одного документа и хранение описаний.

    model = from_env()                                    # локальная модель из окружения (адрес, ключ, имя — как у llm_check)
    out = describe(home, "входящие/пачка/схема.png", sha256, model)

Картинки и страницы PDF без текстового слоя в индекс не попадали: текста в них для индексатора нет. Здесь документ описывает локальная модель
со зрением, а описание ложится в индекс фрагментами того же документа (долги, бюджет прохода и индексация — в `inbox.hand_out_vision`).

Что делает шаг:
    - картинку страницы даёт просмотр (`preview.page`): непроверенный файл читает только рабочий процесс в песочнице, модели уходит готовый
      PNG не больше 4 Мп. Этот модуль файл корпуса не открывает и не разбирает; число страниц берёт у `preview.show`, размер картинки читает
      из заголовка PNG, который вернул просмотр;
    - описываются первые MAX_PAGES страниц документа; страница меньше MIN_SIDE точек по стороне не описывается;
    - запрос уходит локальной точке из `llm_check` (адрес, ключ и имя модели — оттуда же): без инструментов, температура 0, размышление
      выключено, не больше MAX_TOKENS токенов, срок TIMEOUT секунд. Облачный запасной путь здесь не берётся никогда, даже если он настроен.
      Запросы идут по одному, одна страница — один запрос; перед каждым проверяется, что модель загружена и готова (`llm_check.swap_running`;
      сервер без перечня загруженных моделей считается готовым). Сервер, отвергший дополнительные поля запроса, получает запрос ещё раз
      без них (`llm_check.http_transport`);
    - описание — данные: длина ограничена, управляющие знаки убраны, ключ модели затёрт, текст проходит `gate.scan_text`. Страница с находкой
      HIGH или CRITICAL в индекс не идёт и в файл описания не пишется (в нём остаются только номер страницы и правило).

Хранение: `index/vision/<sha256>.json` (0600, каталог 0700, запись через временный файл и os.replace). Имя файла — только sha256: имя документа
и текст описания в пути не попадают. Файл пополняется по странице: сбой одной страницы или остановка модели сделанного не теряют, а описание
того же sha256 повторно не делается. Документ закончен, когда каждая из первых страниц описана или пропущена (мала, закрыта правилами,
не далась за MAX_TRIES проходов).

Сбои двух видов. Модель не ответила, не загружена, занята, срок истёк — остановка всего шага (`Outcome.stop`): дальше первого сбоя не идёт ни
один документ, долг остаётся. Страница не далась (просмотр, негодный ответ) — сбой страницы: остальные страницы и документы идут.
"""
import base64
import json
import os
import re
import struct
import time
import unicodedata
from collections import namedtuple

import gate
import llm_check
import messages
import preview

PreviewError = preview.PreviewError          # имя запоминается здесь: тесты подставляют вместо `preview` свой просмотр

VERSION = 1
MAX_PAGES = 20               # страниц документа, которые описываются
MIN_SIDE = 120               # картинка меньше этого по стороне не описывается
PAGE_CHARS = 8000            # длина описания одной страницы
MAX_TOKENS = 1500
TIMEOUT = 180                # секунд на один запрос к модели
MAX_TRIES = 3                # проходов, за которые страница может не даться; потом она пропускается и документ закрывается
FOLDER = os.path.join("index", "vision")
TYPES = frozenset(("png", "jpg", "jpeg", "gif", "bmp", "tiff", "tif", "webp", "svg", "pdf"))
LEAD = "Описание изображения, сделанное моделью (страница {page}):"
SYSTEM = "Ты готовишь описание схемы для поискового индекса. Пиши по-русски, простым текстом без разметки."
QUESTION = ("Опиши изображение так, чтобы его можно было найти поиском.\n"
            "1) Одной строкой: что это за схема и о чём она.\n"
            "2) Перечисли все надписи дословно, как на изображении, по одной в строке.\n"
            "3) Опиши связи: что с чем соединено и в каком порядке идёт процесс.\n"
            "Не выдумывай того, чего на изображении нет; неразборчивое помечай словом «неразборчиво».")
# отказы просмотра, после которых файл не откроется никогда: документ закрывается без описания, а не ждёт вечно
PERMANENT = frozenset(("preview.encrypted", "preview.broken", "preview.empty", "preview.image_too_large", "preview.unsupported",
                       "preview.program", "preview.needs_converter", "preview.memory"))
BLOCKING = ("HIGH", "CRITICAL")
PNG_HEAD = b"\x89PNG\r\n\x1a\n"
SHA = re.compile(r"[0-9a-f]{64}")
BEARER = re.compile(r"(?i)(bearer\s+)\S+")
CUT = ("Cc", "Cf", "Cs", "Co", "Cn")           # управляющие, форматирующие и прочие невидимые знаки: из описания они убираются

Outcome = namedtuple("Outcome", "complete asked pages failed total truncated stop problems drop why sections record")
# complete — документ закончен (каждая из первых страниц описана или пропущена); asked — запросов к модели получило ответ; pages — описанных
# страниц у документа; failed — страниц, которые не далась в этот раз; stop — сообщение об остановке шага (модель) или None;
# problems — сообщения о закрытых правилами страницах; drop — долг безнадёжен (файл не тот), его надо снять; why — последняя причина сбоя
# страницы или документа (сообщение); sections — тексты для индекса (только у законченного документа); record — запись файла описания


class Stop(Exception):
    """Остановка всего шага: модель не ответила, не готова или срок истёк."""

    def __init__(self, message):
        super().__init__(str(message))
        self.message = message


class BadAnswer(Exception):
    """Ответ модели негоден: не текст или пусто."""


def wants(type_, name=""):
    """Описывается ли документ: тип по содержимому (его назвала приёмка) из TYPES, а без типа — расширение имени."""
    kind = type_.lower() if isinstance(type_, str) else ""
    if kind:
        return kind in TYPES
    return os.path.splitext(name or "")[1].lower().lstrip(".") in TYPES


def png_size(data):
    """(ширина, высота) из заголовка PNG — не раскрывая картинку; None, если это не PNG или размер нулевой."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 24 or data[:8] != PNG_HEAD or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", bytes(data[16:24]))
    return (width, height) if width > 0 and height > 0 else None


# ── слова чужого происхождения в сообщения и описание ───────────
def _controls(text):
    """Без управляющих и невидимых знаков; переводы строк и табуляция остаются, \\r\\n и \\r становятся \\n."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in CUT)


def _hide_key(text, key):
    """Ключ модели и заголовок авторизации затёрты: ответ с ошибкой может повторять заголовки запроса."""
    if key and len(key) >= 4:
        text = text.replace(key, "***")
    return BEARER.sub(lambda m: m.group(1) + "***", text)


def _scrub(text, key=""):
    """Слова ошибки (сервера, передачи, просмотра) для сообщения: без ключа, без управляющих знаков, не длиннее двухсот."""
    return _hide_key(_controls(str(text)), key).replace("\n", " ")[:200]


# ── модель ──────────────────────────────────────────────────────
class Model:
    """Локальная модель со зрением. Берётся только `checker.local`: запасная облачная точка (checker.fallback) здесь не существует."""

    def __init__(self, checker, timeout=TIMEOUT):
        self.endpoint = checker.local
        self.transport, self.running, self.timeout = checker.transport, checker.running, timeout
        self.name, self.key = self.endpoint.name, self.endpoint.key or ""

    def ready(self):
        """None, если модель загружена и готова; иначе сообщение о том, почему шаг стоит. Один быстрый запрос со сроком (llm_check.swap_running).
        Сервер, который не сообщает, что загружено (перечня нет), модель готовой считает: шаг не стоит. Сервер с перечнем — как раньше:
        нужной модели в перечне нет — стоит.
        Имя модели не задано (настройка llm_local_model) — запроса нет вовсе: шаг стоит с причиной."""
        if not self.name:
            return messages.make("vision.model_not_configured")
        try:
            loaded = self.running()
            if loaded is None:
                return None
            loaded = list(loaded)
        except llm_check.ModelError as e:
            return messages.make("vision.model_down", why=_scrub(e, self.key))
        except Exception as e:
            return messages.make("vision.model_down", why=type(e).__name__)
        if self.name in loaded:
            return None
        if not loaded:
            return messages.make("vision.model_not_loaded")
        return messages.make("vision.model_busy", loaded=_scrub(", ".join(map(str, loaded)), self.key))

    def ask(self, png):
        """Описание страницы: текст ответа модели как есть. Остановка шага — Stop, негодный ответ — BadAnswer."""
        if not self.name:
            raise Stop(messages.make("vision.model_not_configured"))         # запроса с пустым именем модели нет никогда
        url ="data:image/png;base64," + base64.b64encode(png).decode("ascii")
        payload = {"model": self.name, "temperature": 0, "max_tokens": MAX_TOKENS, **self.endpoint.extra,
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": [{"type": "text", "text": QUESTION}, {"type": "image_url", "image_url": {"url": url}}]}]}
        payload["chat_template_kwargs"] = {**payload.get("chat_template_kwargs", {}), "enable_thinking": False}     # размышление выключено всегда
        try:
            content = self.transport(self.endpoint, payload, self.timeout)
        except llm_check.ModelTimeout:
            raise Stop(messages.make("vision.model_timeout", seconds=int(self.timeout)))
        except llm_check.ModelError as e:
            raise Stop(messages.make("vision.model_down", why=_scrub(e, self.key)))
        except Exception as e:
            raise Stop(messages.make("vision.model_down", why=_scrub(type(e).__name__, self.key)))
        if not isinstance(content, str):
            raise BadAnswer()
        return content


def from_env(env=None):
    """Модель для этой машины: локальная точка, адрес, имя и ключ — те же, что у проверки моделью. Облачная точка не собирается вовсе."""
    return Model(llm_check.from_env(env, cloud=False))


def _page_text(raw, key):
    """(текст страницы, None) или (None, правило), если правила приёмки нашли в описании находку HIGH или CRITICAL. Пусто — BadAnswer.
    Сначала проверка, потом чистка: спрятанные знаки (метки, смена направления) правила должны увидеть."""
    text = raw[:PAGE_CHARS]
    for f in gate.scan_text(text):
        if f.level in BLOCKING:
            return None, f.rule
    text = _hide_key(_controls(text), key).strip()
    if not text:
        raise BadAnswer()
    return text, None


# ── файл описания ───────────────────────────────────────────────
def _sha(sha):
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("sha256 должен быть 64 знаками 0-9a-f")
    return sha


def path(home, sha):
    """Файл описания документа: имя — только sha256, ничего из имени документа и текста описания."""
    return os.path.join(home, FOLDER, _sha(sha) + ".json")


def load(home, sha):
    """Запись файла описания или None: файла нет, он испорчен или другой версии, sha негоден."""
    try:
        with open(path(home, sha), encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None
    ok = isinstance(record, dict) and record.get("version") == VERSION and isinstance(record.get("pages"), list)
    return record if ok else None


def save(home, sha, record):
    """Запись через временный файл и os.replace, файл 0600, каталоги 0700. Сбой записи не оставляет ни временного файла, ни половины описания."""
    target = path(home, sha)
    folder = home
    for part in FOLDER.split(os.sep):
        folder = os.path.join(folder, part)
        if not os.path.isdir(folder):
            try:
                os.mkdir(folder, 0o700)
            except FileExistsError:
                pass
    tmp = target + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(record, f, ensure_ascii=False)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def forget(home, sha):
    """Убирает файл описания. Нет файла — не ошибка."""
    try:
        os.remove(path(home, sha))
    except FileNotFoundError:
        pass


def count(home):
    """Сколько документов описано (файлов в index/vision)."""
    try:
        return sum(1 for n in os.listdir(os.path.join(home, FOLDER)) if n.endswith(".json") and SHA.fullmatch(n[:-5]))
    except OSError:
        return 0


def sections(record):
    """Тексты для индекса: по странице, каждый начинается со строки, что это описание изображения, сделанное моделью, с номером страницы."""
    return [LEAD.format(page=p["page"]) + "\n" + p["text"] for p in record.get("pages", [])]


def is_complete(record):
    """Каждая из первых страниц документа описана или пропущена."""
    covered = {p["page"] for p in record.get("pages", [])} | {s["page"] for s in record.get("skipped", [])}
    return all(n in covered for n in range(1, min(record.get("total") or 0, MAX_PAGES) + 1))


def _stamp(now):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


# ── шаг описания одного документа ───────────────────────────────
def _out(record=None, **kw):
    done = bool(record) and is_complete(record)
    base = {"complete": done, "asked": 0, "pages": len(record["pages"]) if record else 0, "failed": 0,
            "total": (record or {}).get("total", 0), "truncated": bool((record or {}).get("truncated")), "stop": None, "problems": [],
            "drop": False, "why": None, "sections": sections(record) if done else [], "record": record}
    base.update(kw)
    if "complete" in kw and not kw["complete"]:
        base["sections"] = []
    return Outcome(**base)


def _failed(why, key=""):
    return messages.make("vision.failed", why=_scrub(why, key))


def describe(home, rel, sha, model, *, viewer=None, budget=MAX_PAGES, deadline=None, clock=None, tries=0, now=None):
    """Описывает документ корпуса `rel` (sha256 — `sha`) страницу за страницей. Возвращает Outcome.

    budget — сколько запросов к модели ещё можно сделать, deadline — до какого времени по `clock` новая страница ещё начинается (запрос, который
    уже идёт, не обрывается). tries — сколько проходов страницы этого документа уже не давались: на MAX_TRIES-м они пропускаются и документ закрывается.
    viewer — просмотр (по умолчанию `preview`): подставляют тесты. Файл `rel` этот модуль не открывает никогда."""
    viewer = viewer or preview
    clock = clock or time.monotonic
    record = load(home, sha)
    if record is not None and is_complete(record):
        return _out(record)                                      # то же содержимое уже описано: модель не зовётся
    if budget <= 0 or (deadline is not None and clock() >= deadline):
        return _out(record, complete=False)
    stop = model.ready()                                         # выключенная модель не стоит даже запуска песочницы
    if stop:
        return _out(record, complete=False, stop=stop)
    ready_checked = True                                         # эта проверка — перед первым запросом
    try:
        shown = viewer.show(home, "corpus", rel)
    except PreviewError as e:
        return _out(record, complete=False, why=_failed(e, model.key))
    except Exception as e:
        return _out(record, complete=False, why=_failed(type(e).__name__))
    if not isinstance(shown, dict):
        return _out(record, complete=False, why=_failed("просмотр вернул не описание файла"))
    kind = shown.get("kind")
    meta = shown.get("meta") or {}
    if kind not in ("pages", "image"):
        note = shown.get("note") if isinstance(shown.get("note"), dict) else {}
        code = note.get("code") if kind == "none" else "kind." + str(kind)
        if kind == "rendering":
            return _out(record, complete=False, why=_failed(messages.make("preview.still_rendering")))
        if kind == "none" and code not in PERMANENT:
            return _out(record, complete=False, why=_failed(note.get("text") or "просмотр не показал файл", model.key))
        done = {"version": VERSION, "model": model.name, "date": _stamp(now), "seconds": 0, "total": 0, "truncated": False, "pages": [],
                "skipped": [], "note": str(code)}
        save(home, sha, done)                                    # описывать нечего и не будет: документ закрыт
        return _out(done)
    if meta.get("sha256") != sha:
        return _out(record, complete=False, drop=True, problems=[messages.make("vision.changed", rel=rel)])
    total = shown["pages"] if kind == "pages" else 1
    if record is None:
        record = {"version": VERSION, "model": model.name, "date": _stamp(now), "seconds": 0, "total": total, "truncated": False,
                  "pages": [], "skipped": []}
    record.update(total=total, truncated=total > MAX_PAGES, model=model.name)
    record.setdefault("skipped", [])
    covered = {p["page"] for p in record["pages"]} | {s["page"] for s in record["skipped"]}
    asked, failed, why, problems, spent, changed = 0, [], None, [], 0.0, False
    for n in range(1, min(total, MAX_PAGES) + 1):
        if n in covered:
            continue
        if asked >= budget or (deadline is not None and clock() >= deadline):
            break                                                # остальное остаётся долгом
        try:
            png = viewer.page(home, "corpus", rel, n)
        except PreviewError as e:
            why = _failed(e, model.key)
            if messages.of(e).code == "preview.still_rendering":  # просмотр занят другим запросом: это ожидание, а не неудача страницы
                break
            failed.append(n)
            continue
        except Exception as e:
            failed.append(n)
            why = _failed(type(e).__name__)
            continue
        size = png_size(png)
        if size is None:
            failed.append(n)
            why = _failed(messages.make("preview.bad_answer", what="not_png"))
            continue
        if min(size) < MIN_SIDE:                                 # значок и подпись описывать незачем
            record["skipped"].append({"page": n, "why": "small"})
            changed = True
            continue
        if not ready_checked:
            stop = model.ready()
            if stop:
                return _finish(home, sha, record, changed, asked, failed, problems, why, stop=stop, spent=spent, now=now)
        ready_checked = False
        began = clock()
        try:
            raw = model.ask(png)
        except Stop as s:
            spent += clock() - began
            return _finish(home, sha, record, changed, asked, failed, problems, why, stop=s.message, spent=spent, now=now)
        except BadAnswer:
            raw = None
        spent += clock() - began
        asked += 1
        try:
            if raw is None:
                raise BadAnswer()
            text, rule = _page_text(raw, model.key)
        except BadAnswer:
            failed.append(n)
            why = messages.make("vision.bad_answer", page=n)
            continue
        if rule is not None:                                     # описание с находкой в индекс не идёт и в файл не пишется
            record["skipped"].append({"page": n, "why": "blocked", "rule": rule})
            problems.append(messages.make("vision.blocked", rel=rel, page=n, rule=rule))
        else:
            record["pages"].append({"page": n, "text": text})
        changed = True
        record["seconds"] = round(record.get("seconds", 0) + spent, 1)
        spent = 0.0
        record["date"] = _stamp(now)
        save(home, sha, record)                                  # сделанное не теряется, что бы ни случилось дальше
        changed = False
    if failed and tries + 1 >= MAX_TRIES:                        # страница не давалась столько проходов: пропускается, документ закрывается
        record["skipped"].extend({"page": n, "why": "failed"} for n in failed)
        changed = True
    return _finish(home, sha, record, changed, asked, failed, problems, why, spent=spent, now=now)


def _finish(home, sha, record, changed, asked, failed, problems, why, stop=None, spent=0.0, now=None):
    """Запись файла (если в нём есть что сохранить) и итог документа."""
    record["pages"].sort(key=lambda p: p["page"])
    if changed or spent:
        record["seconds"] = round(record.get("seconds", 0) + spent, 1)
        record["date"] = _stamp(now)
    if (changed or spent) and (record["pages"] or record["skipped"]):
        save(home, sha, record)
    done = is_complete(record) and stop is None
    return _out(record, complete=done, asked=asked, failed=len(failed), stop=stop, problems=problems, why=why)

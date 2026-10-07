"""Разбор входящей пачки в папку: распаковка архивов, приёмка каждого файла, раскладка по решениям.

    run(источник, каталог) -> Result(counts, report_jsonl, report_md, seconds)

Источник — файл, архив или каталог. Он не изменяется. В каталоге разбора появляются:
    принято/            документы, прошедшие приёмку
    на-утверждение/     документы с находками больше порога — решает человек
    карантин/           программы, скрипты и архивы, которые нельзя проверить
    отчёт.jsonl         строка на каждый файл и архив: решение, баллы, находки, происхождение
    отчёт.md            сводка для человека
Пропущенное (не документы) и дубликаты никуда не копируются — они только в отчёте. У пропущенного может стоять пометка skipped_as:
trace — не содержимое (пустой файл, след операционной системы, trace_reason), service — служебный файл по шаблону имени или описание письма
(service_reason); по ней разбор входящих (inbox.py) решает, мешает ли такой файл убрать исходник.
Каталог разбора не может лежать внутри корпуса: до решения человека в индекс ничего не попадает.

Файлы проверяются кругами: сначала всё, что лежит в источнике, затем содержимое найденных
архивов, затем содержимое архивов из архивов. Каждый файл читается один раз, в рабочем процессе.

Проверка моделью (если задан llm) идёт после сверки с архивом и до раскладки: модель видит только то,
что иначе пошло бы в «принято» или «на утверждение». Дубликаты, пропущенное и карантин ей не показываются.
"""
import fnmatch
import json
import os
import shutil
import time
from collections import Counter, namedtuple
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import gate
import messages
import settings
import unpack

CORPUS = os.path.join(settings.startup()["home"], "corpus")
DIRS = {"accept": "принято", "review": "на-утверждение", "quarantine": "карантин"}
TITLES = {"accept": "Принято", "review": "На утверждение", "quarantine": "Карантин", "skip": "Пропущено (не документы)",
          "duplicate": "Дубликаты", "unpacked": "Архивов распаковано", "failed": "Приёмка упала (возвращено)"}
TEMP = ".распаковка"

Result = namedtuple("Result", "counts report_jsonl report_md seconds")
Task = namedtuple("Task", "path name parent depth temp budget is_source")


class IntakeError(messages.CodedError):
    pass


# Не содержимое: пустой файл и следы операционных систем. Это не данные человека: их кладёт сама система (Finder и Проводник заводят свои
# файлы в каждой открытой папке, архиватор на Mac добавляет каталог __MACOSX со спутниками «._имя»), и сохранять их в архиве незачем. Такой
# файл получает решение «пропущен» с причиной в отчёте (skipped_as = "trace"), а разбор входящих его убирает и возврату исходника не мешает.
# Перечень один для всех и не настраивается: файл человека с таким именем следом не считается, поэтому «._имя» — след, только если он начинается
# с подписи формата AppleDouble или лежит в __MACOSX; всё остальное с таким именем — обычный файл, который архив берёт или возвращает.
# Имена — в нижнем регистре: сравнение идёт без учёта регистра, как у шаблонов служебных имён.
TRACE_FOLDERS = ("__macosx",)                                     # каталог и всё, что в нём, на любой глубине (в том числе внутри архива)
TRACE_FILES = (".ds_store", "thumbs.db", "desktop.ini")           # файлы Finder и Проводника Windows
APPLEDOUBLE = b"\x00\x05\x16\x07"                                 # начало файла-спутника AppleDouble


def _named(base, patterns):
    """Подходит ли имя файла (без каталога) под один из шаблонов: как в fnmatch, без учёта регистра."""
    base = base.lower()
    return any(fnmatch.fnmatchcase(base, pattern.lower()) for pattern in patterns)


def _appledouble(path):
    try:
        with open(path, "rb") as f:
            return f.read(len(APPLEDOUBLE)) == APPLEDOUBLE
    except OSError:
        return False


def trace_reason(task, size):
    """Пустой файл или след операционной системы (см. TRACE_FOLDERS, TRACE_FILES): сообщение-причина из каталога, иначе None.
    Файл человека с похожим именем (`._заметка.txt` без подписи AppleDouble вне __MACOSX, `Thumbs.db.txt`) следом не считается."""
    if size == 0:
        return messages.make("reason.empty_file")
    folder, _, base = task.name.rpartition("/")
    inside = any(part.lower() in TRACE_FOLDERS for part in folder.split("/"))
    if base.startswith("._") and (inside or _appledouble(task.path)):
        return messages.make("reason.macos_sidecar")
    if inside or base.lower() in TRACE_FILES:
        return messages.make("reason.system_trace")
    return None


def service_reason(task, names=(), root_names=()):
    """Файл со «служебным» именем: так его называет владелец (настройка приёмки), по умолчанию таких имён нет — любой файл со своим текстом
    документ. names — шаблоны имён в любой папке (причина reason.service_listing: «перечень, а не документ»), root_names — только в корне
    источника или архива-файла (reason.service_readme: «описание или отчёт о проверке»). Образец: names — `*.tmp`, `*.bak` (файлы-спутники выгрузок,
    которые в архиве не нужны); root_names — `index.*`.
    Возвращает причину сообщением из каталога или None."""
    folder, _, base = task.name.rpartition("/")
    if _named(base, names):
        return messages.make("reason.service_listing")
    if folder == (task.parent or "") and _named(base, root_names):         # лежит в корне источника или в корне архива
        return messages.make("reason.service_readme")
    return None


def _why(message):
    """Причина решения для записи отчёта: прежний русский текст (reason) и сообщение из каталога (reason_msg)."""
    return {"reason": str(message), "reason_msg": message.to_json()}


def _note(rec, message):
    """Примечание к архиву: текст в notes, сообщение в notes_msg — списки идут в одном порядке."""
    rec["notes"].append(str(message))
    rec["notes_msg"].append(message.to_json())


def _inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def _check(job):
    """Проверка одного файла. Работает и в отдельном процессе, поэтому сбой возвращается данными."""
    path, name, known = job
    try:
        return gate.check_file(path, name, known)
    except Exception as e:
        msg = gate.fit(lambda s: messages.make("finding.check_crashed", error_type=type(e).__name__, error=s), str(e))
        return {"name": name, "sha256": "", "size": os.path.getsize(path) if os.path.exists(path) else 0,
                "family": "?", "type": "?", "decision": "review", "score": gate.LEVELS["HIGH"], "reason": "", "reason_msg": None,
                "findings": [gate.Finding("unreadable", "HIGH", "весь файл", str(msg), msg, messages.make("where.file"))._asdict()]}


class _Run:
    def __init__(self, source, into, limits, only=None):
        self.source, self.into, self.limits, self.only = source, into, limits, only
        self.temp = os.path.join(into, TEMP)
        self.verdicts = []      # записи отчёта
        self.archives = {}      # имя архива -> запись отчёта
        self.n = 0

    def origin(self, v, task):
        parent = task.parent
        v.update(archive=parent, archive_sha256=self.archives[parent]["sha256"] if parent else None,
                 inner=task.name[len(parent) + 1:] if parent else None, depth=task.depth, placed=None,
                 duplicate_of=None)
        return v

    def first_round(self):
        if os.path.isfile(self.source):
            return [Task(self.source, os.path.basename(self.source), None, 0, False, None, True)]
        tasks = []
        for root, dirs, files in os.walk(self.source):
            dirs.sort()
            if self.only is not None and root == self.source:
                dirs[:] = [d for d in dirs if d in self.only]        # остальное в источнике ещё не готово к разбору
                files = [f for f in files if f in self.only]
            for f in sorted(files):
                path = os.path.join(root, f)
                rel = os.path.relpath(path, self.source).replace(os.sep, "/")
                if os.path.islink(path) or not os.path.isfile(path):
                    self.verdicts.append({"name": rel, "sha256": "", "size": 0, "family": "", "type": "", "decision": "skip",
                                          "score": 0, "findings": [], **_why(messages.make("reason.not_a_file")),
                                          "archive": None, "archive_sha256": None, "inner": None, "depth": 0, "placed": None,
                                          "duplicate_of": None})
                    continue
                tasks.append(Task(path, rel, None, 0, False, None, False))
        return tasks

    def open_archive(self, task, v):
        """Распаковывает архив и возвращает задания на его содержимое. Негодный архив — в карантин целиком."""
        rec = self.origin(v, task)
        rec.update(family="archive", notes=[], notes_msg=[], findings=[], score=0)
        self.archives[task.name] = rec
        self.verdicts.append(rec)
        error, members = None, []
        if task.depth + 1 > self.limits.max_depth:
            error = unpack.UnpackError("depth", messages.make("unpack.depth", limit=self.limits.max_depth))
        else:
            self.n += 1
            # вложенные архивы делят предел с внешним: счёт общий на всё дерево
            budget = task.budget if task.budget is not None else unpack.Budget()
            dest = os.path.join(self.temp, str(self.n))
            try:
                members = unpack.unpack(task.path, dest, self.limits, budget)
            except unpack.UnpackError as e:
                error = e
            except Exception as e:                # сбой самого распаковщика: архив в карантин, пачка идёт дальше
                shutil.rmtree(dest, ignore_errors=True)
                error = unpack.UnpackError("broken", messages.make("unpack.broken_crash", error_type=type(e).__name__, error=str(e)))
        if error is not None:
            level = "CRITICAL" if error.reason == "bomb" else "HIGH"
            where = messages.make("where.archive")
            rec.update(decision="quarantine", score=gate.LEVELS[level], **_why(messages.make("reason.archive_rejected")),
                       findings=[gate.Finding("archive", level, str(where), str(error)[:gate.QUOTE], error.message, where)._asdict()])
            if task.is_source:
                _note(rec, messages.make("note.archive_not_copied"))
            else:
                rec["placed"] = self.place(task.path, task.name, "quarantine", move=task.temp)
            return []
        rec.update(decision="unpacked", **_why(messages.make("reason.archive_unpacked", count=len(members))))
        return [Task(extracted, f"{task.name}/{inner}", task.name, task.depth + 1, True, budget, False)
                for inner, extracted in sorted(members)]

    def place(self, path, name, decision, move):
        target = os.path.join(self.into, DIRS[decision], *name.split("/"))
        made = self.into
        for part in os.path.relpath(os.path.dirname(target), self.into).split(os.sep):
            made = os.path.join(made, part)
            if not os.path.isdir(made):
                os.mkdir(made, 0o700)
        if move:
            os.replace(path, target)
        else:
            shutil.copyfile(path, target)
        os.chmod(target, 0o600)
        return os.path.relpath(target, self.into).replace(os.sep, "/")


def _markdown(verdicts, counts, source, seconds, archive_info=None):
    lines = [f"# Разбор пачки: {os.path.basename(source.rstrip('/'))}", "",
             f"Источник: `{source}`. Проверено записей: {len(verdicts)}. Время: {seconds:.0f} с.", "",
             "| Решение | Число |", "|---|---:|"]
    lines += [f"| {TITLES[k]} | {counts[k]} |" for k in TITLES if counts.get(k)]
    lines += ["", "## Сверка с архивом", ""]
    if archive_info:
        in_archive = [v for v in verdicts if v.get("duplicate_of")]
        lines.append(f"База известного: файлов {archive_info['files']}, писем {archive_info['mail']}, собрана {archive_info['built']}. "
                     f"Уже лежит в архиве: {len(in_archive)}.")
        lines += [""] + [f"- `{v['name']}` — в архиве это `{v['duplicate_of']}`" for v in in_archive[:50]]
        if len(in_archive) > 50:
            lines.append(f"- … и ещё {len(in_archive) - 50}, полный список в отчёт.jsonl")
    else:
        lines.append("Сверка с архивом не проводилась: дубликаты искались только внутри пачки.")
    checked = Counter(v["checked_by"] or "не проверено" for v in verdicts if "checked_by" in v)
    if checked:
        lines += ["", "## Проверка моделью", "", "| Кто проверил | Документов |", "|---|---:|"]
        lines += [f"| {who} | {n} |" for who, n in checked.most_common()]
        if any(v.get("checked_cloud") for v in verdicts):        # по точке, которая отвечала, а не по имени модели
            lines += ["", "Текст документов, проверенных не локальной моделью, уходил с машины."]
    types = Counter(f"{v['family']}/{v['type']}" for v in verdicts if v["family"] not in ("", "archive"))
    lines += ["", "## Типы файлов", "", "| Тип | Число |", "|---|---:|"] + [f"| {t} | {n} |" for t, n in types.most_common(20)]
    rules = Counter(f["rule"] for v in verdicts for f in v["findings"])
    if rules:
        lines += ["", "## Находки по правилам", "", "| Правило | Файлов |", "|---|---:|"] + [f"| {r} | {n} |" for r, n in rules.most_common()]
    for decision, title in (("quarantine", "Карантин"), ("review", "На утверждение")):
        rows = [v for v in verdicts if v["decision"] == decision]
        if rows:
            lines += ["", f"## {title}: {len(rows)}", ""]
            for v in rows[:100]:
                first = v["findings"][0] if v["findings"] else {"rule": "", "level": "", "quote": v["reason"]}
                lines.append(f"- `{v['name']}` — {v['score']} баллов; {first['rule']} {first['level']}: {first['quote']}")
            if len(rows) > 100:
                lines.append(f"- … и ещё {len(rows) - 100}, полный список в отчёт.jsonl")
    notes = [(v["name"], n) for v in verdicts for n in v.get("notes", [])]
    if notes:
        lines += ["", "## Примечания к архивам", ""] + [f"- `{name}` — {n}" for name, n in notes[:100]]
    return "\n".join(lines) + "\n"


def _by_model(v, path, llm):
    """Проверка одного документа моделью. Любой сбой — документ на утверждение, а не потеря."""
    try:
        gate.apply_llm(v, path, llm)
    except Exception as e:
        msg = messages.make("finding.llm_unchecked_failed", error_type=type(e).__name__)
        v["findings"].append(gate.Finding("llm_unchecked", "HIGH", "весь файл", str(msg), msg, messages.make("where.file"))._asdict())
        v.update(decision="review", score=v["score"] + gate.LEVELS["HIGH"], checked_by=None, checked_cloud=False)


def run(source, into, limits=unpack.Limits(), jobs=1, known=None, corpus=CORPUS, progress=None, archive=None,
        llm=None, llm_jobs=4, llm_progress=None, only=None, tick=None, service_names=(), service_root_names=()):
    """archive — база известного (known.Known): по ней узнаётся то, что уже лежит в архиве.
    llm — проверяющий (llm_check.Checker) или None; llm_jobs — сколько документов модель читает разом.
    only — имена верхнего уровня источника-каталога, которые брать; остальное не трогается.
    service_names, service_root_names — шаблоны имён служебных файлов (inbox.json; по умолчанию пусто): такие файлы пропускаются (service_reason).
    tick(этап, готово, всего, имя) — ход для живого экрана: этап intake — по каждому завершённому файлу (всего растёт, когда
    открывается архив), этап model — по каждому документу, который прочитала модель."""
    started = time.time()
    source, into = os.path.abspath(source), os.path.abspath(into)
    if not os.path.exists(source):
        raise IntakeError(messages.make("intake.no_source", path=source))
    if _inside(into, corpus):
        raise IntakeError(messages.make("intake.into_corpus", corpus=corpus))
    if os.path.isdir(source) and _inside(into, source):
        raise IntakeError(messages.make("intake.into_source"))
    if os.path.exists(into) and os.listdir(into):
        raise IntakeError(messages.make("intake.into_not_empty", path=into))
    os.makedirs(into, mode=0o700, exist_ok=True)
    os.chmod(into, 0o700)

    r = _Run(source, into, limits, only)
    pool = ProcessPoolExecutor(max_workers=jobs) if jobs > 1 else None
    leaves, done = [], 0
    try:
        tasks = r.first_round()
        while tasks:
            jobs_in = [(t.path, t.name, known) for t in tasks]
            if pool and len(tasks) > 1:
                results = pool.map(_check, jobs_in, chunksize=max(1, min(64, len(tasks) // (jobs * 4) or 1)))
            else:
                results = map(_check, jobs_in)
            following, before = [], done
            for task, v in zip(tasks, results):
                done += 1
                if progress and done % 2000 == 0:
                    progress(done)
                if v["family"] == "archive" or v["type"] == "broken-zip":
                    following.extend(r.open_archive(task, v))
                else:
                    leaves.append((task, v))
                if tick:
                    tick("intake", done, before + len(tasks) + len(following), task.name)
            tasks = following

        seen_sha, seen_mid, seen_fp = set(), set(), {}
        names = {task.name for task, _ in leaves}
        for task, v in sorted(leaves, key=lambda x: x[0].name):     # дубликаты внутри пачки: принимается первый по имени
            r.origin(v, task)
            stem, ext = os.path.splitext(task.name)
            trace = trace_reason(task, v["size"]) if v["decision"] in ("accept", "review", "skip") else None    # программа и карантин остаются
            if trace:                                      # не содержимое: пропущено, не читается моделью, разбор входящих его убирает
                v.update(decision="skip", score=0, findings=[], skipped_as="trace", **_why(trace))
            if v["decision"] in ("accept", "review"):
                # служебные файлы по имени из настройки и описание письма рядом с письмом: в архив идёт письмо, а не его описание;
                # их судьба — настройка приёмки service_fate (skipped_as = "service")
                service = service_reason(task, service_names, service_root_names)
                if service:
                    v.update(decision="skip", skipped_as="service", **_why(service))
                elif ext.lower() == ".json" and (stem + ".eml" in names or stem + ".msg" in names):
                    v.update(decision="skip", skipped_as="service", **_why(messages.make("reason.service_mail_description")))
            if v["decision"] in ("accept", "review"):
                sha, mid, fp = v["sha256"], v.get("mid"), v.get("fp")
                where = archive.find(sha256=sha, mid=mid, fp=fp) if archive is not None else None
                # отпечаток сравнивается, только когда Message-ID нет хотя бы у одного из двух писем
                in_batch = sha in seen_sha or (mid and mid in seen_mid) or (fp in seen_fp and not (mid and seen_fp[fp]))
                if where:
                    v.update(decision="duplicate", duplicate_of=where, **_why(messages.make("reason.duplicate_in_archive", path=where)))
                elif in_batch:
                    v.update(decision="duplicate", **_why(messages.make("reason.duplicate_in_batch")))
                else:
                    seen_sha.add(sha)
                    if mid:
                        seen_mid.add(mid)
                    if fp:
                        seen_fp[fp] = mid
        ordered = sorted(leaves, key=lambda x: x[0].name)
        if llm is not None:
            todo = [(task, v) for task, v in ordered if v["decision"] in ("accept", "review")]
            if tick and todo:
                tick("model", 0, len(todo), None)
            with ThreadPoolExecutor(max_workers=max(1, llm_jobs)) as threads:
                for n, _ in enumerate(threads.map(lambda item: _by_model(item[1], item[0].path, llm), todo), 1):
                    if llm_progress:
                        llm_progress(n, len(todo))
                    if tick:
                        tick("model", n, len(todo), todo[n - 1][0].name)          # map отдаёт результаты в порядке заданий
        for task, v in ordered:
            if v["decision"] in DIRS:
                v["placed"] = r.place(task.path, task.name, v["decision"], move=task.temp)
            if v["family"] == "executable" and task.parent:
                p = task.parent
                while p:                          # отметка на каждом архиве по пути вверх
                    if len(r.archives[p]["notes"]) < 20:
                        _note(r.archives[p], messages.make("note.executable_inside", name=task.name[len(p) + 1:]))
                    p = r.archives[p]["archive"]
            r.verdicts.append(v)
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
        shutil.rmtree(r.temp, ignore_errors=True)

    verdicts = sorted(r.verdicts, key=lambda v: v["name"])
    counts = dict(Counter(v["decision"] for v in verdicts))
    seconds = time.time() - started
    report_jsonl, report_md = os.path.join(into, "отчёт.jsonl"), os.path.join(into, "отчёт.md")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)           # права владельца — при создании, а не по маске процесса
    with os.fdopen(os.open(report_jsonl, flags, 0o600), "w", encoding="utf-8") as f:
        for v in verdicts:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    with os.fdopen(os.open(report_md, flags, 0o600), "w", encoding="utf-8") as f:
        f.write(_markdown(verdicts, counts, source, seconds, archive.info() if archive is not None else None))
    return Result(counts, report_jsonl, report_md, seconds)

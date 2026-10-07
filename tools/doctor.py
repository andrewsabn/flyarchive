"""Проверка окружения (FR-107): `flyarchive doctor [--json]`. Ничего не меняет и ничего не создаёт, даже каталог архива.

Проверяется и называется: система (Linux), версия Python (нижняя граница — PYTHON_MIN), обязательные библиотеки (requirements.txt), библиотеки
по форматам (requirements-optional.txt: что без каждой не работает), программы (bwrap, 7z, dot, docker, systemctl, dsh, tailscale: ни одна не
обязательна; найденный bwrap пробуется один раз коротким запуском — песочница может быть запрещена ядром или контейнером, и тогда это отдельная строка
по желанию с тем же id), каким интерпретатором просмотр запускает рабочий процесс (строка preview_python), служба векторов (один запрос к embed_url со сроком
в минуту, пока модель поднимается: отвечает ли и та ли размерность, что в настройке embed_dim), заведён ли архив и есть ли в нём таблица индекса, задана ли
локальная модель.

Итог проверки — ok, missing (обязательного нет: код возврата 1) или optional_missing (нет необязательного или архив ещё не заведён: подсказка).
Обязательное — система, Python, библиотеки из requirements.txt и служба векторов. Каждое сообщение — из каталога (tools/messages.py); ключей
и значений секретов здесь нет: читаются только настройки, не файлы ключей. Обязательные библиотеки (requirements.txt) не только ищутся, но и загружаются:
сломанная установка (файлы есть, загрузка падает) — «НЕТ» с первой строкой ошибки (lib.broken). Необязательные только ищутся без импорта
(importlib.util.find_spec): тяжёлые вроде docling проверка не загружает.
"""
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections import namedtuple

import messages

# Нижняя граница Python. Синтаксис кода tools/ читается и версией 3.8 (проверено разбором с feature_version), но обязательные библиотеки на нижних
# границах версий (lancedb 0.38, pyarrow 25, pymupdf 1.28) требуют 3.10 — ниже них не поставить; проект проверен на 3.12.
PYTHON_MIN = (3, 10)
PROBE_TEXT = "проверка"             # что отправляется службе векторов: один короткий текст
EMBED_TIMEOUT_S = 60                # служба векторов на процессоре может поднимать модель при первом запросе (гигабайт с диска): минуты хватает
NOTICE_AFTER_S = 3                  # если служба не ответила за столько секунд, человеку называется ожидание: команда не зависла
ERROR_MAX = 200                     # знаков первой строки ошибки загрузки библиотеки в сообщении: чужой текст в строку проверки целиком не идёт
SANDBOX_PROBE = ("--unshare-all", "--ro-bind", "/", "/", "true")     # пробный запуск песочницы: свои пространства имён, система только для чтения, ничего не пишется
SANDBOX_TIMEOUT_S = 10              # срок пробного запуска: он занимает доли секунды, а зависшей программой проверка не держится
WHY_MAX = 120                       # знаков в причине отказа пробного запуска (первая строка вывода bwrap): остальное человеку не нужно

# (имя при импорте, пакет, обязательна ли, вид слов «что без неё не работает» в messages.WORDS). Сверяется тестом с requirements*.txt.
LIBRARIES = (
    ("lancedb", "lancedb", True, "index"),
    ("pyarrow", "pyarrow", True, "schema"),
    ("pymupdf", "pymupdf", True, "pdf"),
    ("PIL", "Pillow", False, "images"),
    ("docx", "python-docx", False, "docx"),
    ("openpyxl", "openpyxl", False, "xlsx"),
    ("pptx", "python-pptx", False, "pptx"),
    ("extract_msg", "extract-msg", False, "msg"),
    ("olefile", "olefile", False, "ole"),
    ("docling", "docling", False, "docling"),
    ("matplotlib", "matplotlib", False, "charts"),
    ("reportlab", "reportlab", False, "pdf_out"),
)
# (имя в id, под какими именами ищется в PATH, вид слов «что без неё не работает»). Обязательной программы нет.
PROGRAMS = (
    ("bwrap", ("bwrap",), "bwrap"),
    ("7z", ("7z", "7za", "7zz"), "7z"),
    ("dot", ("dot",), "dot"),
    ("docker", ("docker",), "docker"),
    ("systemctl", ("systemctl",), "systemctl"),
    ("dsh", ("dsh",), "dsh"),
    ("tailscale", ("tailscale",), "tailscale"),
)
# Шрифт с кириллицей для pdf, которые собирает сервер документов: стандартные шрифты reportlab её не содержат, русский текст выходил квадратами.
# Ищется по именам файлов в каталогах шрифтов системы; первое семейство из перечня, найденное в этих каталогах, и берётся (обычное начертание и
# жирное, жирного нет — обычное). Свой файл .ttf — необязательная настройка pdf_font. Пакет — что поставить, если шрифта нет (Debian и Ubuntu).
FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts", "~/.fonts")
FONT_FAMILIES = (("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"), ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
                 ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"), ("FreeSans.ttf", "FreeSansBold.ttf"))
FONT_PACKAGE = "fonts-dejavu-core"
Font = namedtuple("Font", "regular bold")
ARCHIVE_DIRS = ("secrets", "index", "corpus")        # что оставляет после себя flyarchive init
MARK = {"ok": "ok", "missing": "НЕТ", "optional_missing": "по желанию"}
REQUIRED_FILE, OPTIONAL_FILE = "requirements.txt", "requirements-optional.txt"

Check = namedtuple("Check", "id status message")


def library_ids():
    return ["lib." + package.lower() for _, package, _, _ in LIBRARIES]


def program_ids():
    return ["tool." + name for name, _, _ in PROGRAMS]


def _text(version):
    return ".".join(str(part) for part in version)


# ── отдельные проверки ──────────────────────────────────────────
def refusal(system=None):
    """Отказ для системы, где архив работать не может (всё, кроме Linux: fcntl, resource, bwrap, systemctl --user), иначе None. Принимает имя
    системы (platform.system()), поэтому тесты зовут её с любым именем и не подменяют sys.platform всему процессу. Команда flyarchive отвечает этой
    строкой и кодом 2 на любой подкоманде, кроме --help."""
    system = platform.system() if system is None else system
    return None if system == "Linux" else messages.make("cli.not_linux")


def check_system(system):
    message = refusal(system)
    if message is None:
        return Check("system", "ok", messages.make("doctor.system_ok", system=system))
    return Check("system", "missing", message)


def check_python(version):
    version = tuple(version)[:3]
    if version[:2] >= PYTHON_MIN:
        return Check("python", "ok", messages.make("doctor.python_ok", version=_text(version), need=_text(PYTHON_MIN)))
    return Check("python", "missing", messages.make("doctor.python_old", version=_text(version), need=_text(PYTHON_MIN)))


def _present(module):
    """Есть ли модуль, без его импорта. Заблокированный импорт (sys.modules[имя] = None) и битый путь поиска — «нет»."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _version(package):
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "?"


def _first_line(error):
    """Вид исключения и первая строка его текста, не длиннее ERROR_MAX: «ImportError: libarrow.so.99: cannot open shared object file»."""
    lines = str(error).strip().splitlines()
    return (type(error).__name__ + (": " + lines[0].strip() if lines else ""))[:ERROR_MAX]


def check_library(module, package, must, use):
    """Обязательная библиотека должна не только найтись, но и загрузиться: файлы на месте, а загрузка падает — это сломанная установка, и
    она проходила бы как «есть», пока не упал бы первый поиск. Необязательные только ищутся: загрузка некоторых занимает десятки секунд."""
    id_ = "lib." + package.lower()
    file = REQUIRED_FILE if must else OPTIONAL_FILE
    if not _present(module):
        return Check(id_, "missing" if must else "optional_missing", messages.make("lib.missing", package=package, use=use, file=file))
    if must:
        try:
            importlib.import_module(module)
        except Exception as e:
            return Check(id_, "missing", messages.make("lib.broken", package=package, error=_first_line(e), file=file))
    return Check(id_, "ok", messages.make("doctor.lib_ok", package=package, version=_version(package)))


def probe_sandbox(path, run=subprocess.run):
    """Один короткий пробный запуск bwrap: None — песочница создаётся, иначе причина одной строкой ASCII: «exit N: первая строка вывода», «timeout N s»
    или вид исключения. Вывод целиком не возвращается. run подменяется в тестах."""
    try:
        got = run([path, *SANDBOX_PROBE], capture_output=True, stdin=subprocess.DEVNULL, timeout=SANDBOX_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"timeout {SANDBOX_TIMEOUT_S} s"
    except OSError as e:
        return type(e).__name__
    if got.returncode == 0:
        return None
    lines = [line.strip() for line in (got.stderr or b"").decode("utf-8", "replace").splitlines() if line.strip()]
    first = "".join(c if " " <= c <= "~" else "?" for c in lines[0])[:WHY_MAX] if lines else ""
    return f"exit {got.returncode}: {first}" if first else f"exit {got.returncode}"


def check_program(name, candidates, use, which, probe=None):
    """Программа найдена — ok; не найдена — по желанию. probe(путь) — пробный запуск найденной (None — удался, иначе причина): не удался — тоже по
    желанию, но с сообщением «программа есть, а работать не может» и тем же id."""
    for candidate in candidates:
        found = which(candidate)
        if found:
            why = probe(found) if probe is not None else None
            if why is not None:
                return Check("tool." + name, "optional_missing", messages.make("doctor.sandbox_blocked", name=candidate, why=why, use=use))
            return Check("tool." + name, "ok", messages.make("doctor.tool_ok", name=candidate))
    return Check("tool." + name, "optional_missing", messages.make("doctor.tool_missing", name="/".join(candidates), use=use))


def check_preview_python(home):
    """Каким интерпретатором просмотр запускает рабочий процесс (preview.worker_python) и можно ли отдать его песочнице: нельзя — то же сообщение,
    каким просмотр отказывает человеку."""
    import preview
    found = preview.worker_python(home)
    if found.refusal is not None:
        return Check("preview_python", "optional_missing", found.refusal)
    return Check("preview_python", "ok", messages.make("doctor.preview_python_ok", python=found.path))


def _seconds(value):
    """Срок для сообщения: целое число секунд без дробной части."""
    return int(value) if float(value).is_integer() else round(float(value), 1)


def check_embed(s, timeout=None, notify=None):
    """Один запрос к службе векторов: отвечает ли и та ли размерность. Тело — как у индексатора: на процессоре, пока не включена embed_gpu.
    Срок вышел — «не ответила» (первый запрос мог поднимать модель), соединение отвергнуто или ответ негоден — «не отвечает». notify(сообщение)
    зовётся один раз, если ответа нет дольше NOTICE_AFTER_S секунд: команда в текстовом режиме печатает это в stderr."""
    url, model, want = s["embed_url"], s["embed_model"], s["embed_dim"]
    body = {"model": model, "input": [PROBE_TEXT]}
    if not s["embed_gpu"]:
        body["options"] = {"num_gpu": 0}
    wait = EMBED_TIMEOUT_S if timeout is None else timeout
    request = urllib.request.Request(url, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    notice = None
    if notify is not None and wait > NOTICE_AFTER_S:
        notice = threading.Timer(NOTICE_AFTER_S, notify, [messages.make("doctor.embed_waiting", seconds=_seconds(wait))])
        notice.daemon = True
        notice.start()
    try:
        with urllib.request.urlopen(request, timeout=wait) as answer:
            got = len(json.load(answer)["embeddings"][0])
    except urllib.error.HTTPError as e:
        why = f"HTTP {e.code}"
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):          # срок вышел при соединении
            return Check("embed", "missing", messages.make("embed.slow", seconds=_seconds(wait)))
        why = e.reason if isinstance(e.reason, str) else type(e.reason).__name__
    except TimeoutError:                                  # срок вышел, пока служба думала над ответом (socket.timeout — тот же класс с Python 3.10)
        return Check("embed", "missing", messages.make("embed.slow", seconds=_seconds(wait)))
    except Exception as e:                    # ответ не того вида, обрыв на середине, негодный JSON: причина — вид сбоя, без текста чужого ответа
        why = type(e).__name__
    else:
        if got == want:
            return Check("embed", "ok", messages.make("doctor.embed_ok", url=url, model=model, dim=got))
        return Check("embed", "missing", messages.make("doctor.embed_dim", got=got, want=want))
    finally:
        if notice is not None:
            notice.cancel()
    return Check("embed", "missing", messages.make("embed.down", url=url, why=str(why)[:80], model=model))


def check_archive(home):
    if all(os.path.isdir(os.path.join(home, name)) for name in ARCHIVE_DIRS):
        return Check("archive", "ok", messages.make("doctor.archive_ok", path=home))
    return Check("archive", "optional_missing", messages.make("doctor.archive_missing", path=home))


def check_table(home):
    """Таблица индекса заведённого архива. Открывается, но не заводится: нет каталога индекса — нет и обращения к библиотеке."""
    import ingest
    try:
        ingest.open_table(home)
    except ingest.TableMissing:
        return Check("table", "optional_missing", messages.make("index.no_table"))
    except Exception as e:
        return Check("table", "optional_missing", messages.make("doctor.table_failed", error_type=type(e).__name__))
    return Check("table", "ok", messages.make("doctor.table_ok"))


def check_model(s):
    if s["llm_local_model"]:
        return Check("model", "ok", messages.make("doctor.model_ok", model=s["llm_local_model"]))
    return Check("model", "optional_missing", messages.make("init.todo_model"))


def find_font(configured=""):
    """Шрифт с кириллицей для pdf: Font(обычный, жирный или None) или None. Сначала файл из настройки pdf_font (если он есть), потом обычные
    семейства (FONT_FAMILIES) по именам файлов в каталогах FONT_DIRS. Только имена файлов: шрифты не открываются и ничего не создаётся."""
    if configured:
        path = os.path.expanduser(configured)
        if os.path.isfile(path):
            return Font(path, None)
    wanted = {name for family in FONT_FAMILIES for name in family}
    found = {}
    for folder in FONT_DIRS:
        for root, dirs, files in os.walk(os.path.expanduser(folder)):
            dirs.sort()                                  # порядок обхода не зависит от файловой системы
            for name in sorted(files):
                if name in wanted:
                    found.setdefault(name, os.path.join(root, name))
    for regular, bold in FONT_FAMILIES:
        if regular in found:
            return Font(found[regular], found.get(bold))
    return None


def check_font(s):
    """Шрифт для pdf с русским текстом: необязателен, без него сервер документов отказывает только на тексте не из латиницы."""
    font = find_font(s["pdf_font"])
    if font is None:
        return Check("font", "optional_missing", messages.make("doctor.font_missing", package=FONT_PACKAGE))
    return Check("font", "ok", messages.make("doctor.font_ok", path=font.regular))


# ── проверка целиком ────────────────────────────────────────────
def say_waiting(message):
    """Строка ожидания службы векторов: в stderr, чтобы не мешать выводу (в --json её нет: там notify не передаётся)."""
    print(message, file=sys.stderr, flush=True)


def collect(s, system=None, python=None, which=None, timeout=None, notify=None, probe=None):
    """Все проверки по порядку. s — настройки (settings.load); система, версия Python, поиск программ и пробный запуск bwrap подменяются в тестах.
    notify — что делать, если служба векторов отвечает дольше NOTICE_AFTER_S секунд (команда в текстовом режиме передаёт say_waiting)."""
    which = shutil.which if which is None else which
    probes = {"bwrap": probe_sandbox if probe is None else probe}
    out = [check_system(platform.system() if system is None else system),
           check_python(sys.version_info[:3] if python is None else python)]
    libraries = [check_library(*row) for row in LIBRARIES]
    out += libraries
    out += [check_program(*row, which, probes.get(row[0])) for row in PROGRAMS]
    out.append(check_preview_python(s["home"]))
    out.append(check_font(s))
    out.append(check_embed(s, timeout, notify))
    archive = check_archive(s["home"])
    out.append(archive)
    if archive.status == "ok" and any(c.id == "lib.lancedb" and c.status == "ok" for c in libraries):     # сломанная библиотека таблицу не откроет
        out.append(check_table(s["home"]))
    out.append(check_model(s))
    return out


def missing(checks):
    return [c for c in checks if c.status == "missing"]


def exit_code(checks):
    return 1 if missing(checks) else 0


def label(check):
    """Коротко, чего не хватает: имя пакета, Python, Linux или слова «служба векторов» (имя настройки адреса — в подробной строке проверки)."""
    if check.id.startswith("lib."):
        return check.message.args["package"]
    return {"system": "Linux", "python": "Python", "embed": "служба векторов"}.get(check.id, check.id)


def summary(checks):
    lost = missing(checks)
    if not lost:
        return messages.make("doctor.summary_ok")
    return messages.make("doctor.summary_missing", names=", ".join(label(c) for c in lost))


def tail(checks):
    """Строка для конца init и install: чего обязательного не хватает, из той же проверки."""
    lost = missing(checks)
    if not lost:
        return messages.make("doctor.tail_ok")
    return messages.make("doctor.tail_missing", names=", ".join(label(c) for c in lost))


def to_json(check):
    return {"id": check.id, "status": check.status, "message": check.message.to_json()}


def lines(checks):
    """Вывод для человека: заголовок, по строке на проверку, итог."""
    return [str(messages.make("doctor.title"))] + [f"  {MARK[c.status]:<10}  {c.message}" for c in checks] + ["", str(summary(checks))]


def report(checks, as_json=False):
    """(текст вывода, код возврата)."""
    if as_json:
        return json.dumps({"ok": not missing(checks), "checks": [to_json(c) for c in checks]}, ensure_ascii=False), exit_code(checks)
    return "\n".join(lines(checks)), exit_code(checks)

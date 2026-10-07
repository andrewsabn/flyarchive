"""Просмотр файла без контейнера (FR-76): ссылка, рабочий процесс в песочнице, описание, кэш, замки.

    show(home, area, path, members)          описание для плагина: {"kind", "meta", …} — `flyarchive preview show --json`
    page(home, area, path, number, members)  байты PNG одной страницы — `flyarchive preview page`
    clean(home)                              чистка кэша по возрасту и объёму — `flyarchive preview clean`

Файл непроверенный и может быть вредоносным. Поэтому родитель (этот модуль) его не разбирает: ссылку проверяет по именам, байты
читает только потоком для sha256, а тип и содержимое определяет рабочий процесс (`preview_worker.py`) в `bwrap` без сети, с пределами
памяти и времени. Что рабочий процесс вернул, проверяется до записи в кэш: обычный файл (не ссылка), размер, PNG по началу и концу,
описание по схеме. В браузер уходят только PNG и простой текст. Без песочницы файл не разбирается никогда.
Рабочий процесс идёт настоящим интерпретатором родителя (`worker_python`; у виртуального окружения это его базовый интерпретатор), с библиотеками
из окружения родителя. Лежит интерпретатор в системных каталогах — песочница его и так видит; нет (свой Python в домашнем каталоге, pyenv, conda) —
каталог его установки привязывается только для чтения, а если так отдать его нельзя безопасно, просмотр отвечает отказом `preview.python_outside`.

Ссылка — область (`queue`, `quarantine`, `corpus`) и путь: `очередь/<пачка>/<имя>`, `карантин/<пачка>/<имя>` или путь от корня корпуса.
Кэш — `cache/preview/<sha256>/`: `desc.json` и `page-N.png`; ключ — sha256 тех байтов, что рисуются. Замки (flock): `<sha>.lock` на описание,
`<sha>.pN.lock` на страницу, `.slot0` и `.slot1` — не больше двух рабочих процессов разом. Запрос, заставший описание занятым, получает
`{"kind": "rendering"}` и не ждёт; страница ждёт чужое описание или страницу не дольше PAGE_WAIT.
Результат `none` в кэш не пишется: с новыми типами (Office — 15, видео — 17) ответ изменится.
Точка расширения: новый вид — в `SCHEMAS` и в `_public`, правило определения — в `preview_worker._classify`.

Письма, архивы и календарь (FR-77): разбирает их тот же рабочий процесс; родитель принимает `mail` (шапка, текст, список вложений), `listing`
(состав архива) и обычный `text` календаря только по схеме с пределами и без управляющих знаков в именах. Вложение письма (`--member`)
достаёт рабочий процесс: кладёт байты в выходной каталог, родитель их не разбирает, а считает sha256 потоком и открывает файл без перехода по
ссылке; дальше вложение — обычный файл со своим sha256 (он и есть ключ кэша: два вложения одного письма не перекрываются, одно вложение из разных
писем описывается один раз), его описывает уже новый рабочий процесс в своей песочнице. Вложение письма во вложении — ещё одно такое же извлечение.
Каталог с байтами вложения живёт, пока идёт просмотр: песочнице нужен сам файл (`--ro-bind-fd` не принимает удалённый).

Office, HTML и метафайлы (FR-78): рабочий процесс называет их тип (`needs_converter`), и родитель, если тип из CONVERT_TYPES, отдаёт файл
контейнеру (`preview_container.py`): LibreOffice без сети делает из него PDF, а дальше это обычный PDF. Контейнер получает не путь из очереди,
а копию байт из уже открытого дескриптора в своём каталоге `.in-*` (sha256 копии сверяется), пишет в отдельный `.out-*`; родитель принимает ровно
один обычный файл, начинающийся с `%PDF-`, не больше PDF_MAX, и кладёт его в кэш как `<sha256 исходного>/base.pdf`. Описывает и рисует этот PDF
тот же рабочий процесс в `bwrap`. Одно преобразование за раз: общий замок `.render.lock`, занят — `{"kind": "rendering"}`. `page` преобразование
не запускает никогда: нет `base.pdf` — отказ.
"""
import collections
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import site
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time

import batches
import corpus_path
import messages
import preview_container as C

AREAS = {"queue": "очередь", "quarantine": "карантин", "corpus": None}
MAX_MEMBERS = 2                      # вложение во вложении; глубже — отказ

# те же значения у рабочего процесса: тест сверяет, что они совпадают
TEXT_LIMIT = 20_000
MAX_PAGES = 20
MAX_PAGE_PIXELS = 4_000_000
MAX_PAGE_SIDE = 10_000
MAX_IMAGE_SIDE = 2000
IMAGE_MEGAPIXELS = 100

# письма, архивы, вложения: те же значения у рабочего процесса, тест сверяет, что они совпадают
MAIL_PEOPLE = 100
MAIL_LINE = 300
MAIL_SUBJECT = 1000
MAX_ATTACHMENTS = 200
NAME_LIMIT = 255
LISTING_MAX = 500
MEMBER_MAX = 64 * 1024 ** 2
NAME_BAD = r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069\ud800-\udfff]"

MAX_PAGE_COUNT = 10 ** 7             # сколько страниц в документе родитель ещё считает правдой
MAX_SIZE = 1 << 40                   # размер вложения письма
MAX_LISTED = 1 << 50                 # размер записи архива
RESULT_MAX = 1 << 20                 # result.json
PNG_MAX = 32 << 20
OUT_MAX = 256 << 20                  # всё, что рабочий процесс успел записать в выходной каталог
OUT_ENTRIES = 1000
CHUNK = 1 << 20
SLOTS = 2                            # рабочих процессов одновременно
WORKER_TIMEOUT = 90                  # секунд на один запуск
SLOT_WAIT = 120                      # сколько ждать свободный слот
PAGE_WAIT = 60                       # сколько страница ждёт чужое описание или чужую отрисовку этой же страницы
CACHE_DAYS = 7
CACHE_MAX = 2 * 1024 ** 3
WORK_DAYS = 3600                     # брошенный рабочий каталог старше часа убирается
TMP_SIZE = 128 << 20                 # /tmp внутри песочницы
# Office, HTML и метафайлы (FR-78): тип назвал рабочий процесс (needs_converter), преобразует контейнер
CONVERT_TYPES = frozenset(("doc", "docx", "rtf", "odt", "xls", "xlsx", "xlsm", "xlsb", "csv", "ods", "ppt", "pptx", "pptm", "ppsx", "odp", "vsd", "vsdx",
                           "emf", "wmf", "wmz", "emz", "html"))
CONVERT_MAX = 100 << 20              # исходный файл больше в контейнер не идёт
PDF_MAX = 200 << 20                  # PDF от контейнера и всё, что он успел записать в выходной каталог
WORKER = "preview_worker.py"
CODE_DIR = os.path.dirname(os.path.realpath(__file__))
SYSTEM_DIRS = ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
VISIBLE_DIRS = ("/usr",) + SYSTEM_DIRS         # их песочница привязывает или связывает сама: интерпретатор оттуда виден без привязки
FALLBACK_PYTHON = "/usr/bin/python3"           # у встроенного Python пути интерпретатора нет: берётся системный
PYTHON = os.path.realpath(sys.executable) if sys.executable else FALLBACK_PYTHON      # интерпретатор родителя; у окружения — базовый

PNG_HEAD = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
DAY = 86_400

Ref = collections.namedtuple("Ref", "full area batch name")
Run = collections.namedtuple("Run", "code timed_out tail", defaults=(False, ""))
Described = collections.namedtuple("Described", "desc note type")
# интерпретатор рабочего процесса: путь, каталог его установки для привязки только для чтения (None — песочница видит его и так), отказ (None — можно)
WorkerPython = collections.namedtuple("WorkerPython", "path bind refusal")
# то, что просматривается: сам файл (member False) или вложение письма (member True): открытый файл, его sha256, размер и имя
Item = collections.namedtuple("Item", "fd sha size name member")


class PreviewError(messages.CodedError):
    """Отказ просмотра с сообщением из каталога."""


class Busy(Exception):
    """Замок или слот заняты и не освободились за отведённое время."""


class _Bad(Exception):
    """Рабочий процесс вернул не то; what — что именно, слово для сообщения preview.bad_answer."""

    def __init__(self, what):
        super().__init__(what)
        self.what = what


class _Fail(Exception):
    """Сбой запуска или приёмки: message — пояснение из каталога, type — тип файла, если рабочий процесс его назвал."""

    def __init__(self, message, type_=None):
        super().__init__(str(message))
        self.message = message
        self.type = type_


def bwrap_path():
    return shutil.which("bwrap")


# ── ссылка ──────────────────────────────────────────────────────
def _member(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value):
        return int(value)
    return None


def check_members(values):
    """Ключи --member: целые от 0, не больше двух. Негодное или лишнее — отказ."""
    values = list(values or [])
    members = [_member(v) for v in values]
    if len(values) > MAX_MEMBERS or any(m is None for m in members):
        raise PreviewError(messages.make("preview.bad_member", max=MAX_MEMBERS))
    return members


def resolve(home, area, path):
    """Файл внутри своей области: очереди, карантина или корпуса. Файл при этом не открывается.

    Отклоняются: `..`, `.` и пустые части, абсолютный путь, чужая область, ссылка на любом звене пути (в том числе на каталог пачки)
    и всё, что не обычный файл. Корень области может быть ссылкой — корпус владелец вправе держать на другом диске."""
    if not isinstance(area, str) or area not in AREAS:
        raise PreviewError(messages.make("preview.bad_area", areas=", ".join(AREAS)))
    corpus, folder = area == "corpus", AREAS[area]
    if not isinstance(path, str) or not path:
        raise PreviewError(messages.make("review.doc_path_needed") if corpus else messages.make("review.path_needed"))
    parts = path.replace("\\", "/").split("/")
    if "\x00" in path or any(p in ("", ".", "..") for p in parts) or (not corpus and (parts[0] != folder or len(parts) < 3)):
        raise PreviewError(messages.make("review.doc_path_outside", path=path) if corpus else messages.make("review.bad_path", area=folder))
    if corpus and parts[0] in corpus_path.ALIAS and len(parts) > 1:      # путь из выдачи поиска под старым корнем
        parts[0] = corpus_path.ALIAS[parts[0]]
    rest = parts if corpus else parts[1:]
    root = os.path.realpath(os.path.join(home, "corpus" if corpus else folder))
    full, here = os.path.join(root, *rest), root
    missing = PreviewError(messages.make("review.no_doc", path=path) if corpus else messages.make("review.no_file", path=path))
    for part in rest:
        here = os.path.join(here, part)
        if os.path.islink(here):
            raise missing
    if not os.path.isfile(full) or not os.path.realpath(full).startswith(root + os.sep):
        raise missing
    batch = None
    if corpus:
        name = "/".join(rest)
        if len(rest) >= 3 and rest[0] == "входящие" and batches.ID.fullmatch(rest[1]):
            batch, name = rest[1], "/".join(rest[2:])
    else:
        batch, name = parts[1], "/".join(parts[2:])
    return Ref(full, area, batch, name)


def _open_input(full):
    """Входной файл на чтение: не по ссылке, только обычный, без ожидания (канал не подвесит)."""
    fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "не обычный файл")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open(ref, path):
    try:
        return _open_input(ref.full)
    except OSError:
        raise PreviewError(messages.make("review.no_doc", path=path) if ref.area == "corpus" else messages.make("review.no_file", path=path))


def _sha256(fd):
    """sha256 и размер файла: файл читается потоком кусками по CHUNK, целиком в память не берётся."""
    digest, size = hashlib.sha256(), 0
    while True:
        chunk = os.read(fd, CHUNK)
        if not chunk:
            return digest.hexdigest(), size
        digest.update(chunk)
        size += len(chunk)


def _ext(named):
    """Расширение имени (у файла Ref, у Item — имя вложения) для рабочего процесса: только буквы и цифры, до десяти знаков, иначе пусто."""
    ext = os.path.splitext(named.name)[1].lower().lstrip(".")
    return ext if re.fullmatch(r"[a-z0-9]{1,10}", ext) else ""


# ── сведения о файле ────────────────────────────────────────────
def _receipt(home, batch, name):
    """Квитанция приёмки этого файла: последняя запись с таким именем, сделанная разбором, а не решением владельца."""
    found = [r for r in batches._records(home, batch) or [] if r.get("name") == name and "by" not in r]
    return found[-1] if found else {}


def _meta(home, ref, item, type_):
    """Имя, тип, размер, sha256, пачка и происхождение. Происхождение (архив и путь внутри) — из квитанции, если она есть.
    У вложения письма квитанции нет: запись пачки с тем же именем принадлежит другому файлу. item None — вложение не достали."""
    if item is None or item.member:
        return {"name": item and item.name, "type": type_, "size": item and item.size, "sha256": item and item.sha, "batch": ref.batch,
                "origin": {"archive": None, "inner": None}}
    receipt = _receipt(home, ref.batch, ref.name) if ref.batch else {}
    text = lambda value: value if isinstance(value, str) else None          # noqa: E731
    return {"name": ref.name.rsplit("/", 1)[-1], "type": type_ or text(receipt.get("type")), "size": item.size, "sha256": item.sha, "batch": ref.batch,
            "origin": {"archive": text(receipt.get("archive")), "inner": text(receipt.get("inner"))}}


# ── кэш: каталоги, запись, замки ────────────────────────────────
def _cache(home):
    return os.path.join(home, "cache", "preview")


def _mkdirs(path, root):
    """Каталоги от root до path, каждый 0700 (os.makedirs даёт этот режим только последнему)."""
    made = root
    for part in os.path.relpath(path, root).split(os.sep):
        if part in ("", "."):
            continue
        made = os.path.join(made, part)
        try:
            os.mkdir(made, 0o700)
            os.chmod(made, 0o700)
        except FileExistsError:
            if not os.path.isdir(made):
                raise NotADirectoryError(errno.ENOTDIR, "на месте каталога лежит файл", made)
    return path


def _write_atomic(path, data):
    """Файл 0600 через временный файл рядом и os.replace: читатель видит либо прежний, либо целый новый."""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _acquire(path):
    """Замок-файл (flock) или None, если занят. Замок держится, пока открыт возвращённый дескриптор."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise


@contextlib.contextmanager
def _take(paths, wait):
    """Первый свободный замок из paths; занято всё — ждёт не дольше wait секунд, потом Busy."""
    end = time.monotonic() + wait
    while True:
        fd = None
        for path in paths:
            fd = _acquire(path)
            if fd is not None:
                break
        if fd is not None:
            break
        if time.monotonic() >= end:
            raise Busy()
        time.sleep(0.02)
    try:
        yield
    finally:
        os.close(fd)


def _slots(cache):
    return [os.path.join(cache, f".slot{i}") for i in range(SLOTS)]


# ── что вернул рабочий процесс ──────────────────────────────────
def _read_regular(path, limit):
    """Байты обычного файла не больше limit. Ссылка, каталог, канал и слишком большой файл — _Bad."""
    try:
        st = os.lstat(path)
    except OSError:
        raise _Bad("missing")
    if not stat.S_ISREG(st.st_mode):
        raise _Bad("not_a_file")
    if st.st_size > limit:
        raise _Bad("too_big")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise _Bad("not_a_file")
    try:
        now = os.fstat(fd)
        if not stat.S_ISREG(now.st_mode) or now.st_size > limit:
            raise _Bad("not_a_file")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(CHUNK, limit + 1 - total))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise _Bad("too_big")
    finally:
        os.close(fd)


def _parse(raw):
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        raise _Bad("schema")
    if not isinstance(obj, dict):
        raise _Bad("schema")
    return obj


TYPE = re.compile(r"[a-z0-9][a-z0-9._+-]{0,19}")
ERROR_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,59}")
# причина kind none -> параметры и их вид: type — слово типа, error — имя исключения, count — число от 0
REASONS = {"program": {}, "unsupported": {"type": "type"}, "needs_converter": {"type": "type"}, "empty": {}, "encrypted": {}, "memory": {},
           "too_large": {"megapixels": "count", "limit": "count"}, "broken": {"error_type": "error"},
           "no_member": {"member": "count", "count": "count"}, "needs_extractor": {"type": "type"}, "too_big": {"limit": "count"}, "no_events": {}}
NAME_RE = re.compile(NAME_BAD)
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2}(:[0-9]{2})?)?")


def _need(ok):
    if not ok:
        raise _Bad("schema")


def _number(value, low, high):
    _need(isinstance(value, int) and not isinstance(value, bool) and low <= value <= high)
    return value


def _word(value):
    _need(isinstance(value, str) and TYPE.fullmatch(value))
    return value


def _text_schema(obj, page):
    _need(set(obj) == {"kind", "type", "text", "truncated"} and isinstance(obj["text"], str) and len(obj["text"]) <= TEXT_LIMIT
          and isinstance(obj["truncated"], bool))
    return {"kind": "text", "type": _word(obj["type"]), "text": obj["text"], "truncated": obj["truncated"]}


def _pages_schema(obj, page):
    _need(set(obj) == {"kind", "type", "pages", "shown"})
    pages = _number(obj["pages"], 1, MAX_PAGE_COUNT)
    shown = _number(obj["shown"], 1, MAX_PAGES)
    _need(shown == min(pages, MAX_PAGES))
    return {"kind": "pages", "type": _word(obj["type"]), "pages": pages, "shown": shown}


def _image_schema(obj, page):
    _need(set(obj) == {"kind", "type"})
    return {"kind": "image", "type": _word(obj["type"])}


def _page_schema(obj, page):
    _need(set(obj) == {"kind", "page"} and type(obj["page"]) is int and obj["page"] == page)
    return {"kind": "page", "page": page}


def _none_schema(obj, page):
    _need(set(obj) == {"kind", "type", "reason", "args"} and isinstance(obj["reason"], str) and obj["reason"] in REASONS
          and isinstance(obj["args"], dict))
    shape, args = REASONS[obj["reason"]], obj["args"]
    _need(set(args) == set(shape))
    clean = {}
    for name, kind in shape.items():
        value = args[name]
        if kind == "type":
            clean[name] = _word(value)
        elif kind == "error":
            _need(isinstance(value, str) and ERROR_TYPE.fullmatch(value))
            clean[name] = value
        else:
            clean[name] = _number(value, 0, 10 ** 9)
    return {"kind": "none", "type": _word(obj["type"]), "reason": obj["reason"], "args": clean}


def _line(value, limit):
    """Имя или поле из чужого файла: строка не длиннее limit без управляющих знаков (их заменил рабочий процесс)."""
    _need(isinstance(value, str) and len(value) <= limit and not NAME_RE.search(value))
    return value


def _people(value):
    _need(isinstance(value, list) and len(value) <= MAIL_PEOPLE)
    return [_line(one, MAIL_LINE) for one in value]


def _mail_schema(obj, page):
    _need(set(obj) == {"kind", "type", "mail"} and isinstance(obj["mail"], dict))
    mail = obj["mail"]
    _need(set(mail) == {"from", "to", "cc", "date", "subject", "text", "truncated", "attachments"})
    _need(mail["date"] is None or (isinstance(mail["date"], str) and ISO_DATE.fullmatch(mail["date"])))
    _need(isinstance(mail["text"], str) and len(mail["text"]) <= TEXT_LIMIT and isinstance(mail["truncated"], bool))
    _need(isinstance(mail["attachments"], list) and len(mail["attachments"]) <= MAX_ATTACHMENTS)
    rows = []
    for number, item in enumerate(mail["attachments"]):
        _need(isinstance(item, dict) and set(item) == {"name", "size", "type", "member", "executable"} and isinstance(item["executable"], bool))
        rows.append({"name": _line(item["name"], NAME_LIMIT), "size": _number(item["size"], 0, MAX_SIZE), "type": _word(item["type"]),
                     "member": _number(item["member"], number, number), "executable": item["executable"]})          # номер — место в списке
    return {"kind": "mail", "type": _word(obj["type"]), "mail": {
        "from": _line(mail["from"], MAIL_LINE), "to": _people(mail["to"]), "cc": _people(mail["cc"]), "date": mail["date"],
        "subject": _line(mail["subject"], MAIL_SUBJECT), "text": mail["text"], "truncated": mail["truncated"], "attachments": rows}}


def _listing_schema(obj, page):
    _need(set(obj) == {"kind", "type", "listing", "truncated"} and isinstance(obj["truncated"], bool))
    _need(isinstance(obj["listing"], list) and len(obj["listing"]) <= LISTING_MAX)
    rows = []
    for item in obj["listing"]:
        _need(isinstance(item, dict) and set(item) == {"name", "size"})
        rows.append({"name": _line(item["name"], NAME_LIMIT), "size": _number(item["size"], 0, MAX_LISTED)})
    return {"kind": "listing", "type": _word(obj["type"]), "listing": rows, "truncated": obj["truncated"]}


def _member_schema(obj, page):
    _need(set(obj) == {"kind", "name"})
    return {"kind": "member", "name": _line(obj["name"], NAME_LIMIT)}


SCHEMAS = {"text": _text_schema, "pages": _pages_schema, "image": _image_schema, "mail": _mail_schema, "listing": _listing_schema,
           "page": _page_schema, "member": _member_schema, "none": _none_schema}
DESCRIBED = ("text", "pages", "image", "mail", "listing", "none")      # что может вернуть описание; страница — page или none, вложение — member или none


def _check(obj, op, page=None):
    kind = obj.get("kind")
    if kind not in (DESCRIBED if op == "describe" else ("member", "none") if op == "member" else ("page", "none")):
        raise _Bad("schema")
    result = SCHEMAS[kind](obj, page)
    if result.get("reason") == "no_member" and op != "member":         # «нет такого вложения» — ответ только на просьбу достать вложение
        raise _Bad("schema")
    return result


def _check_png(data, kind):
    """Начало и конец PNG и размер из заголовка: картинка — до MAX_IMAGE_SIDE по стороне, страница — до MAX_PAGE_PIXELS точек."""
    if len(data) < 33 or data[:8] != PNG_HEAD or data[12:16] != b"IHDR" or data[-12:] != PNG_END:
        raise _Bad("not_png")
    width, height = struct.unpack(">II", data[16:24])
    side = MAX_IMAGE_SIDE if kind == "image" else MAX_PAGE_SIDE
    if width < 1 or height < 1 or max(width, height) > side or (kind != "image" and width * height > MAX_PAGE_PIXELS):
        raise _Bad("dimensions")
    return data


def _note(result):
    """Пояснение к none из причины, которую назвал рабочий процесс (она уже прошла проверку схемы)."""
    reason, args = result["reason"], result["args"]
    if reason == "program":
        return messages.make("preview.program")
    if reason == "unsupported":
        return messages.make("preview.unsupported", type=args["type"])
    if reason == "needs_converter":
        return messages.make("preview.needs_converter", type=args["type"])
    if reason == "empty":
        return messages.make("preview.empty")
    if reason == "encrypted":
        return messages.make("preview.encrypted")
    if reason == "memory":
        return messages.make("preview.memory")
    if reason == "too_large":
        return messages.make("preview.image_too_large", megapixels=args["megapixels"], limit=args["limit"])
    if reason == "no_member":
        return messages.make("preview.no_member", member=args["member"], count=args["count"])
    if reason == "needs_extractor":
        return messages.make("preview.needs_extractor", type=args["type"])
    if reason == "too_big":
        return messages.make("preview.too_big", limit=args["limit"])
    if reason == "no_events":
        return messages.make("preview.no_events")
    return messages.make("preview.broken", error_type=args["error_type"])


def _why(run):
    """Почему рабочий процесс не отработал: код возврата или сигнал и последняя строка его stderr (только печатные знаки ASCII)."""
    what = f"signal {-run.code}" if run.code is not None and run.code < 0 else f"exit {run.code}"
    lines = [line.strip() for line in str(run.tail).splitlines() if line.strip()]
    if not lines:
        return what
    return what + ": " + "".join(c if " " <= c <= "~" else "?" for c in lines[-1])[:120]


# ── запуск в песочнице ──────────────────────────────────────────
def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def library_dirs(home, paths=None):
    """Каталоги site-packages и dist-packages, откуда Python берёт PyMuPDF и Pillow, но не из /usr (он виден и так) и не из архива."""
    paths = list(sys.path) + [site.getusersitepackages()] if paths is None else paths
    found, archive = [], os.path.realpath(home) if home else None
    for path in paths:
        if not isinstance(path, str) or os.path.basename(path.rstrip(os.sep)) not in ("site-packages", "dist-packages"):
            continue
        real = os.path.realpath(path)
        if not os.path.isdir(real) or real in found or _inside(real, "/usr") or (archive and _inside(real, archive)):
            continue
        found.append(real)
    return found


def worker_python(home=None, executable=None, base_prefix=None, user_home=None):
    """Интерпретатор рабочего процесса: WorkerPython(путь, каталог для привязки или None, отказ или None).

    Берётся настоящий интерпретатор родителя (`os.path.realpath(sys.executable)`: у виртуального окружения это базовый интерпретатор). Лежит он в
    системных каталогах (VISIBLE_DIRS) — песочница видит его и так. Лежит вне них (свой Python в домашнем каталоге, pyenv, conda) — песочнице отдаётся
    каталог его установки (`sys.base_prefix`) только для чтения. Отказ, если так отдать его небезопасно: интерпретатор лежит не в своём каталоге
    установки (окружение с копией интерпретатора), каталог установки — первого уровня или корень, это домашний каталог владельца или каталог над ним,
    это каталог архива home или каталог над ним или внутри него. executable, base_prefix, user_home — для тестов; по умолчанию свои у процесса."""
    executable = sys.executable if executable is None else executable
    if not executable:
        return WorkerPython(FALLBACK_PYTHON, None, None)
    real = os.path.realpath(executable)
    if any(_inside(real, d) for d in VISIBLE_DIRS):
        return WorkerPython(real, None, None)
    prefix = os.path.realpath(sys.base_prefix if base_prefix is None else base_prefix)
    owner = os.path.realpath(os.path.expanduser("~") if user_home is None else user_home)
    archive = os.path.realpath(home) if home else None
    unsafe = (not _inside(real, prefix) or prefix.count(os.sep) < 2 or _inside(owner, prefix)
              or (archive is not None and (_inside(archive, prefix) or _inside(prefix, archive))))
    if unsafe:
        return WorkerPython(real, None, messages.make("preview.python_outside", python=real))
    return WorkerPython(real, prefix, None)


def _why_unavailable(home):
    """Почему рабочий процесс сейчас не запустить (сообщение) или None: нет bwrap или интерпретатор нельзя отдать песочнице."""
    if bwrap_path() is None:
        return messages.make("preview.no_sandbox")
    return worker_python(home).refusal


def sandbox_command(code_dir, script, args, outdir, fd, libs, python=None):
    """Командная строка bwrap и окружение для неё. Без сети, процесс умирает вместе с родителем; для чтения — система, код, каталог установки
    интерпретатора (если он вне системных каталогов) и библиотеки, входной файл по дескриптору fd; для записи — только выходной каталог.
    Каталога архива и домашнего каталога здесь нет. python — WorkerPython (по умолчанию — интерпретатор родителя в системных каталогах)."""
    python = WorkerPython(PYTHON, None, None) if python is None else python
    argv = [bwrap_path() or "bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv", "--ro-bind", "/usr", "/usr"]
    for d in SYSTEM_DIRS:
        if os.path.islink(d):
            argv += ["--symlink", os.readlink(d), d]
        elif os.path.isdir(d):
            argv += ["--ro-bind", d, d]
    argv += ["--ro-bind", "/etc", "/etc", "--proc", "/proc", "--dev", "/dev", "--size", str(TMP_SIZE), "--tmpfs", "/tmp",
             "--ro-bind", code_dir, "/code"]
    if python.bind:
        argv += ["--ro-bind", python.bind, python.bind]
    for lib in libs:
        if not (python.bind and _inside(lib, python.bind)):         # каталог библиотек внутри каталога установки уже виден
            argv += ["--ro-bind", lib, lib]
    if fd is not None:
        argv += ["--ro-bind-fd", str(fd), "/in/data"]
    argv += ["--bind", outdir, "/out"]
    env = [("HOME", "/tmp"), ("PATH", "/usr/bin:/bin"), ("PYTHONNOUSERSITE", "1"), ("PYTHONDONTWRITEBYTECODE", "1")]
    if libs:
        env.append(("PYTHONPATH", ":".join(libs)))
    for name, value in env:
        argv += ["--setenv", name, value]
    return argv + ["--chdir", "/tmp", "--", python.path, "-s", "-B", f"/code/{script}", *args], {"PATH": "/usr/bin:/bin"}


def _tree_size(folder):
    """Сколько занято под folder; записей больше OUT_ENTRIES считается переполнением."""
    total = seen = 0
    for root, dirs, files in os.walk(folder):
        for name in files + dirs:
            seen += 1
            if seen > OUT_ENTRIES:
                return OUT_MAX + 1
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, name)).st_size
    return total


def _kill(proc):
    for stop in (lambda: os.killpg(proc.pid, signal.SIGKILL), proc.kill):
        with contextlib.suppress(OSError):
            stop()


def _drain(stream, keep):
    """stderr рабочего процесса читается до конца, но хранится только последний килобайт: больше человеку не нужно."""
    while True:
        chunk = stream.read(65_536)
        if not chunk:
            return
        keep += chunk
        del keep[:-1024]


def run_sandboxed(code_dir, script, args, outdir, fd=None, timeout=None, home=None):
    """Запускает script из code_dir в bwrap и ждёт. Срок вышел — группа процессов убивается; выходной каталог вырос больше OUT_MAX —
    тоже. Возвращает Run(код возврата, вышел ли срок, хвост stderr)."""
    argv, env = sandbox_command(code_dir, script, args, outdir, fd, library_dirs(home), worker_python(home))
    timeout = WORKER_TIMEOUT if timeout is None else timeout
    proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            pass_fds=() if fd is None else (fd,), start_new_session=True)
    tail = bytearray()
    reader = threading.Thread(target=_drain, args=(proc.stderr, tail), daemon=True)
    reader.start()
    end, stopped = time.monotonic() + timeout, None
    try:
        while stopped is None:
            try:
                proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() >= end:
                stopped = "timeout"
            elif _tree_size(outdir) > OUT_MAX:
                stopped = "output"
        if stopped:
            _kill(proc)
            proc.wait()
    except BaseException:
        _kill(proc)
        proc.wait()
        raise
    finally:
        reader.join(timeout=2)
        proc.stderr.close()
    if stopped == "timeout":
        return Run(None, True, "")
    if stopped == "output":
        return Run(-signal.SIGKILL, False, "output limit exceeded")
    return Run(proc.returncode, False, tail.decode("utf-8", "replace"))


def _run_worker(home, op, fd, ext, page, outdir):
    """Одно действие рабочего процесса над файлом, который открыт как fd. Здесь подставляют подставной запуск в тестах."""
    args = [op, "/in/data", "/out", ext] + ([] if page is None else [str(page)])
    return run_sandboxed(CODE_DIR, WORKER, args, outdir, fd, WORKER_TIMEOUT, home)


def _open_member(path):
    """Файл вложения, который сложил рабочий процесс: обычный (не ссылка, не канал), не больше MEMBER_MAX. Возвращает дескриптор."""
    try:
        st = os.lstat(path)
    except OSError:
        raise _Bad("missing")
    if not stat.S_ISREG(st.st_mode):
        raise _Bad("not_a_file")
    if st.st_size > MEMBER_MAX:
        raise _Bad("too_big")
    try:
        fd = _open_input(path)
    except OSError:
        raise _Bad("not_a_file")
    if os.fstat(fd).st_size > MEMBER_MAX:
        os.close(fd)
        raise _Bad("too_big")
    return fd


def _execute(home, op, fd, ext, number, kind, stack):
    """Запуск рабочего процесса и приёмка того, что он вернул: (ответ по схеме, PNG, дескриптор вложения или None). Сбой — _Fail с пояснением.
    number — страница (render) или номер вложения (member); kind — вид описания (pages или image) для проверки размера страницы.
    Выходной каталог и дескриптор вложения убирает stack, когда вызывающий закончит: у вложения песочнице нужен сам файл."""
    refusal = worker_python(home).refusal                      # описание могло лежать в кэше от другого интерпретатора: страницу им не нарисовать
    if refusal is not None:
        raise _Fail(refusal)
    try:
        work = tempfile.mkdtemp(prefix=".work-", dir=_cache(home))
    except OSError as e:
        raise _Fail(messages.make("preview.cache_failed", error_type=type(e).__name__))
    stack.callback(shutil.rmtree, work, True)
    run = _run_worker(home, op, fd, ext, number, work)         # непредвиденное исключение запуска ловят show и page
    if run.timed_out:
        raise _Fail(messages.make("preview.timeout", seconds=WORKER_TIMEOUT))
    if run.code != 0:                                          # упавший не доверяется, даже если успел записать ответ
        raise _Fail(messages.make("preview.worker_failed", why=_why(run)))
    try:
        result = _check(_parse(_read_regular(os.path.join(work, "result.json"), RESULT_MAX)), op, number if op == "render" else None)
        png = member = None
        if result["kind"] in ("image", "page"):
            png = _check_png(_read_regular(os.path.join(work, "page.png"), PNG_MAX), "image" if result["kind"] == "image" else kind)
        elif result["kind"] == "member":
            member = _open_member(os.path.join(work, "member.bin"))
            stack.callback(os.close, member)
    except _Bad as bad:
        raise _Fail(messages.make("preview.bad_answer", what=bad.what))
    return result, png, member


# ── кэш: чтение, запись, чистка ─────────────────────────────────
def _read_desc(home, sha):
    """Описание из кэша или None: нет файла, он испорчен, не той версии или не по схеме."""
    try:
        obj = _parse(_read_regular(os.path.join(_cache(home), sha, "desc.json"), RESULT_MAX))
        if obj.pop("v", None) != 1 or obj.get("kind") not in ("text", "pages", "image", "mail", "listing"):
            return None
        return _check(obj, "describe")
    except _Bad:
        return None


def _read_page(home, sha, number, kind):
    try:
        return _check_png(_read_regular(os.path.join(_cache(home), sha, f"page-{number}.png"), PNG_MAX), kind)
    except _Bad:
        return None


def _store(home, sha, desc, png):
    """Описание (и картинка, если её нарисовало описание) в кэш. Описание пишется последним: оно и есть признак готовности."""
    try:
        folder = _mkdirs(os.path.join(_cache(home), sha), home)
        if png is not None:
            _write_atomic(os.path.join(folder, "page-1.png"), png)
        _write_atomic(os.path.join(folder, "desc.json"), json.dumps({"v": 1, **desc}, ensure_ascii=False).encode("utf-8"))
    except OSError as e:
        raise _Fail(messages.make("preview.cache_failed", error_type=type(e).__name__))
    _trim(home, sha)


def _store_page(home, sha, number, png):
    try:
        _write_atomic(os.path.join(_mkdirs(os.path.join(_cache(home), sha), home), f"page-{number}.png"), png)
    except OSError as e:
        raise _Fail(messages.make("preview.cache_failed", error_type=type(e).__name__))
    _trim(home, sha)


def _entries(cache):
    """Объекты кэша — каталоги по sha256: [(имя, путь, время изменения, размер)]."""
    found = []
    try:
        names = os.listdir(cache)
    except OSError:
        return found
    for name in names:
        full = os.path.join(cache, name)
        if name.startswith(".") or os.path.islink(full) or not os.path.isdir(full):
            continue
        try:
            size = sum(os.lstat(os.path.join(root, f)).st_size for root, _, files in os.walk(full) for f in files)
            found.append((name, full, os.lstat(full).st_mtime, size))
        except OSError:                                    # объект убрали, пока его считали
            continue
    return found


def _remove(cache, name, full):
    """Убирает объект кэша, если его не рисуют прямо сейчас (замок объекта свободен)."""
    fd = _acquire(os.path.join(cache, name + ".lock"))
    if fd is None:
        return False
    try:
        shutil.rmtree(full, ignore_errors=True)
        return True
    finally:
        os.close(fd)


def _trim(home, protect=None):
    """Самочистка по объёму: пока кэш больше CACHE_MAX, уходят самые старые объекты, кроме protect."""
    cache = _cache(home)
    entries = _entries(cache)
    total = sum(e[3] for e in entries)
    for name, full, mtime, size in sorted(entries, key=lambda e: e[2]):
        if total <= CACHE_MAX:
            return
        if name != protect and _remove(cache, name, full):
            total -= size


def clean(home, now=None):
    """Чистка кэша: объекты старше CACHE_DAYS, потом самые старые, пока кэш больше CACHE_MAX; брошенные рабочие каталоги и старые замки.
    Объект, который рисуют сейчас, не трогается. Возвращает {"removed": объектов, "freed": байт, "left": байт}."""
    now = time.time() if now is None else now
    cache = _cache(home)
    removed = freed = 0
    for name, full, mtime, size in _entries(cache):
        if now - mtime > CACHE_DAYS * DAY and _remove(cache, name, full):
            removed, freed = removed + 1, freed + size
    left = _entries(cache)
    total = sum(e[3] for e in left)
    for name, full, mtime, size in sorted(left, key=lambda e: e[2]):
        if total <= CACHE_MAX:
            break
        if _remove(cache, name, full):
            removed, freed, total = removed + 1, freed + size, total - size
    try:
        names = os.listdir(cache)
    except OSError:
        names = []
    for name in names:
        full = os.path.join(cache, name)
        try:
            age = now - os.lstat(full).st_mtime
        except OSError:
            continue
        if name.startswith((".work-", ".in-", ".out-")) and age > WORK_DAYS:
            shutil.rmtree(full, ignore_errors=True)
        elif name.endswith(".lock") and not name.startswith(".") and age > CACHE_DAYS * DAY:
            fd = _acquire(full)
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.unlink(full)
                os.close(fd)
    return {"removed": removed, "freed": freed, "left": total}


# ── контейнер: Office, HTML и метафайлы в PDF (FR-78) ───────────
def _private_dir(cache, prefix, stack):
    """Каталог 0700 в кэше, который убирает stack: у контейнера свой входной и свой выходной."""
    path = tempfile.mkdtemp(prefix=prefix, dir=cache)
    stack.callback(shutil.rmtree, path, True)
    return path


def _copy_input(item, indir, type_):
    """Копия байт исходного файла из уже открытого дескриптора в indir/data.<расширение>: путь из очереди контейнер не видит, подмена файла
    после подсчёта sha256 его не касается. sha256 копии сверяется с посчитанным до запуска. Возвращает имя файла."""
    ext = _ext(item)
    name = f"data.{ext if ext in CONVERT_TYPES else type_}"          # имя и тип из файла в путь не попадают, кроме проверенного расширения
    digest, total = hashlib.sha256(), 0
    with os.fdopen(os.open(os.path.join(indir, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
        os.fchmod(out.fileno(), 0o600)
        while total <= item.size:                                 # файл вырос после подсчёта — лишнее прочтётся, и sha256 не сойдётся
            chunk = os.pread(item.fd, CHUNK, total)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
            total += len(chunk)
    if total != item.size or digest.hexdigest() != item.sha:
        raise _Fail(messages.make("preview.input_changed"), type_)
    return name


def _accept_output(outdir, type_):
    """Выход контейнера — данные. Принимается ровно один файл: обычный (не ссылка, не каталог, не канал), открытый без перехода по ссылке,
    не больше PDF_MAX, начинающийся с `%PDF-`. Возвращает открытый дескриптор; имя файла дальше этой функции не идёт."""
    try:
        names = os.listdir(outdir)
    except OSError:
        names = []
    if not names:
        raise _Fail(messages.make("preview.convert_no_pdf"), type_)
    if len(names) > 1:
        raise _Fail(messages.make("preview.convert_several"), type_)
    path = os.path.join(outdir, names[0])
    try:
        st = os.lstat(path)
    except OSError:
        raise _Fail(messages.make("preview.convert_no_pdf"), type_)
    if not stat.S_ISREG(st.st_mode):
        raise _Fail(messages.make("preview.convert_not_a_file"), type_)
    if st.st_size > PDF_MAX:
        raise _Fail(messages.make("preview.convert_output_big", limit=PDF_MAX >> 20), type_)
    try:
        fd = _open_input(path)
    except OSError:
        raise _Fail(messages.make("preview.convert_not_a_file"), type_)
    if os.pread(fd, 5, 0) != b"%PDF-":
        os.close(fd)
        raise _Fail(messages.make("preview.convert_not_pdf"), type_)
    return fd


def _store_base(home, sha, fd):
    """PDF от контейнера в кэш: `<sha256 исходного файла>/base.pdf`, 0600, через временный файл и os.replace. fd — принятый выход."""
    target = os.path.join(_mkdirs(os.path.join(_cache(home), sha), home), "base.pdf")
    tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
            os.fchmod(out.fileno(), 0o600)
            total = 0
            while True:
                chunk = os.pread(fd, CHUNK, total)
                if not chunk:
                    break
                total += len(chunk)
                if total > PDF_MAX:
                    raise OSError(errno.EFBIG, "PDF больше предела")
                out.write(chunk)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _open_base(home, sha):
    """Готовый `base.pdf` объекта: обычный файл без перехода по ссылке. Нет его — _Fail: страница преобразование не запускает."""
    try:
        return _open_input(os.path.join(_cache(home), sha, "base.pdf"))
    except OSError:
        raise _Fail(messages.make("preview.not_converted"))


def _convert(home, item, type_):
    """Файл типа type_ (из CONVERT_TYPES) — PDF в кэше. Порядок отказов: размер, docker, дайджест, общий замок, образ на машине. Общий замок занят —
    Busy: второй контейнер не запускается. Сбой — _Fail с типом файла."""
    if item.size > CONVERT_MAX:
        raise _Fail(messages.make("preview.convert_too_big", limit=CONVERT_MAX >> 20), type_)
    docker = C.docker_path()
    if docker is None:
        raise _Fail(messages.make("preview.no_docker"), type_)
    ref = C.image(home)
    if ref is None:
        raise _Fail(messages.make("preview.no_image"), type_)
    try:
        cache = _mkdirs(_cache(home), home)
    except OSError as e:
        raise _Fail(messages.make("preview.cache_failed", error_type=type(e).__name__), type_)
    render = _acquire(os.path.join(cache, ".render.lock"))
    if render is None:
        raise Busy()
    try:
        with contextlib.ExitStack() as work:
            try:
                C.check_image(docker, ref)
                indir, outdir = _private_dir(cache, ".in-", work), _private_dir(cache, ".out-", work)
                name = _copy_input(item, indir, type_)
                C.run(docker, ref, indir, outdir, name, PDF_MAX, lambda: _tree_size(outdir))
                pdf = _accept_output(outdir, type_)
                work.callback(os.close, pdf)
                _store_base(home, item.sha, pdf)
            except C.ContainerError as e:
                raise _Fail(e.message, type_)
            except OSError as e:
                raise _Fail(messages.make("preview.cache_failed", error_type=type(e).__name__), type_)
    finally:
        os.close(render)


def _forget_base(home, sha):
    folder = os.path.join(_cache(home), sha)
    with contextlib.suppress(OSError):
        os.unlink(os.path.join(folder, "base.pdf"))
    with contextlib.suppress(OSError):
        os.rmdir(folder)


def _describe_base(home, item, type_):
    """Описание PDF от контейнера: то же, что у обычного PDF, рабочим процессом в песочнице. Ответ идёт с типом исходного файла и ложится
    в кэш под sha256 исходного. PDF, который рабочий процесс не принял, из кэша убирается."""
    fd = _open_base(home, item.sha)
    try:
        with _take(_slots(_cache(home)), SLOT_WAIT):
            with contextlib.ExitStack() as work:
                result, _, _ = _execute(home, "describe", fd, "pdf", None, None, work)
    finally:
        os.close(fd)
    if result["kind"] != "pages":
        _forget_base(home, item.sha)
        if result["kind"] == "none":
            return Described(None, _note(result), type_)
        raise _Fail(messages.make("preview.bad_answer", what="schema"), type_)
    desc = {**result, "type": type_}
    try:
        _store(home, item.sha, desc, None)
    except _Fail as fail:
        raise _Fail(fail.message, type_)
    return Described(desc, None, type_)


def _via_container(home, item, type_, convert):
    """Ответ на `needs_converter` для типа из CONVERT_TYPES. convert False (страница) — преобразование не запускается."""
    if not convert:
        return Described(None, messages.make("preview.not_converted"), type_)
    try:
        _convert(home, item, type_)
        return _describe_base(home, item, type_)
    except _Fail as fail:
        _forget_base(home, item.sha)
        return Described(None, fail.message, type_)


# ── описание и страница ─────────────────────────────────────────
def _describe(home, item, wait, convert=True):
    """Описание объекта (файла или вложения): из кэша по sha256 его байтов или от рабочего процесса. wait — сколько ждать чужое описание
    этого же объекта; занято — Busy. Тип из CONVERT_TYPES идёт в контейнер, если convert (показ), и не идёт, если нет (страница)."""
    cached = _read_desc(home, item.sha)
    if cached is not None:
        return Described(cached, None, cached["type"])
    unavailable = _why_unavailable(home)
    if unavailable is not None:
        return Described(None, unavailable, None)
    try:
        cache = _mkdirs(_cache(home), home)
    except OSError as e:
        return Described(None, messages.make("preview.cache_failed", error_type=type(e).__name__), None)
    with _take([os.path.join(cache, item.sha + ".lock")], wait):
        cached = _read_desc(home, item.sha)               # пока ждали замок, описание мог сделать другой запрос
        if cached is not None:
            return Described(cached, None, cached["type"])
        with _take(_slots(cache), SLOT_WAIT):
            try:
                with contextlib.ExitStack() as work:
                    result, png, _ = _execute(home, "describe", item.fd, _ext(item), None, None, work)
            except _Fail as fail:
                return Described(None, fail.message, None)
        if result["kind"] == "none":
            if result["reason"] == "needs_converter" and result["args"]["type"] in CONVERT_TYPES:
                return _via_container(home, item, result["args"]["type"], convert)
            return Described(None, _note(result), result["type"])
        try:
            _store(home, item.sha, result, png)
        except _Fail as fail:
            return Described(None, fail.message, None)
        return Described(result, None, result["type"])


def _enter(home, item, index, stack):
    """Вложение index письма item. Рабочий процесс кладёт его байты в выходной каталог; родитель их не разбирает: считает sha256 потоком
    и открывает файл без перехода по ссылке. Каталог и дескриптор живут, пока жив stack. Занят слот — Busy, сбой — _Fail,
    нет такого номера — PreviewError с кодом."""
    unavailable = _why_unavailable(home)
    if unavailable is not None:
        raise _Fail(unavailable)
    try:
        cache = _mkdirs(_cache(home), home)
    except OSError as e:
        raise _Fail(messages.make("preview.cache_failed", error_type=type(e).__name__))
    with _take(_slots(cache), SLOT_WAIT):
        result, _, member = _execute(home, "member", item.fd, _ext(item), index, None, stack)
    if result["kind"] == "none":
        if result["reason"] == "no_member":
            raise PreviewError(_note(result))
        raise _Fail(_note(result), result["type"])
    sha, size = _sha256(member)
    return Item(member, sha, size, result["name"], True)


def _public(desc):
    """Что из описания идёт в ответ плагину по виду. Сюда же — поля новых видов (media)."""
    if desc["kind"] == "text":
        return {"text": desc["text"], "truncated": desc["truncated"]}
    if desc["kind"] == "pages":
        return {"pages": desc["pages"], "shown": desc["shown"]}
    if desc["kind"] == "mail":
        return {"mail": desc["mail"]}
    if desc["kind"] == "listing":
        return {"listing": desc["listing"], "truncated": desc["truncated"]}
    return {}


def show(home, area, path, members=()):
    """Описание файла (или вложения письма: members — номера, не больше двух) для плагина. Рисуется ли он другим запросом — {"kind": "rendering"};
    сбой рабочего процесса — kind none с пояснением; номера вложения нет — PreviewError."""
    ref = resolve(home, area, path)
    chain = check_members(members)
    fd = _open(ref, path)
    with contextlib.ExitStack() as stack:
        stack.callback(os.close, fd)
        sha, size = _sha256(fd)
        item = shown = Item(fd, sha, size, ref.name, False)
        try:
            for index in chain:
                shown = None                                # пока вложение не достали, о нём ничего не известно
                item = _enter(home, item, index, stack)
                shown = item
            found = _describe(home, item, 0)
        except Busy:
            return {"kind": "rendering"}
        except PreviewError:
            raise
        except _Fail as fail:
            found = Described(None, fail.message, fail.type)
        except Exception as e:                              # непредвиденное тоже становится пояснением, а не падением
            found = Described(None, messages.make("preview.worker_failed", why=type(e).__name__), None)
        meta = _meta(home, ref, shown, found.type)
    if found.desc is None:
        return {"kind": "none", "meta": meta, "note": found.note.to_json()}
    return {"kind": found.desc["kind"], "meta": meta, **_public(found.desc)}


def _page_number(number):
    if isinstance(number, int) and not isinstance(number, bool):
        number = str(number)
    if not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]{0,5}", number):
        raise PreviewError(messages.make("preview.bad_page"))
    return int(number)


def page(home, area, path, number, members=()):
    """PNG одной страницы файла или его вложения (members). Нет страницы, нет описания или сбой — PreviewError. Страницу, которую рисует
    другой запрос, ждёт не дольше PAGE_WAIT. Страница вложения лежит в кэше под sha256 вложения; чтобы его узнать, вложение достаётся снова."""
    ref = resolve(home, area, path)
    chain = check_members(members)
    wanted = _page_number(number)
    fd = _open(ref, path)
    with contextlib.ExitStack() as stack:
        stack.callback(os.close, fd)
        sha, size = _sha256(fd)
        item = Item(fd, sha, size, ref.name, False)
        try:
            for index in chain:
                item = _enter(home, item, index, stack)
            return _page(home, item, wanted)
        except Busy:
            raise PreviewError(messages.make("preview.still_rendering"))
        except PreviewError:
            raise
        except _Fail as fail:
            raise PreviewError(fail.message)
        except Exception as e:
            raise PreviewError(messages.make("preview.worker_failed", why=type(e).__name__))


def _page(home, item, wanted):
    found = _describe(home, item, PAGE_WAIT, convert=False)           # страница преобразование не запускает никогда
    if found.desc is None:
        raise PreviewError(found.note)
    desc = found.desc
    if desc["kind"] not in ("pages", "image"):
        raise PreviewError(messages.make("preview.no_pages"))
    shown = desc["shown"] if desc["kind"] == "pages" else 1
    if not 1 <= wanted <= shown:
        raise PreviewError(messages.make("preview.no_page", page=wanted, shown=shown))
    data = _read_page(home, item.sha, wanted, desc["kind"])
    if data is not None:
        return data
    cache = _cache(home)
    with _take([os.path.join(cache, f"{item.sha}.p{wanted}.lock")], PAGE_WAIT):
        data = _read_page(home, item.sha, wanted, desc["kind"])        # пока ждали замок, страницу мог нарисовать другой запрос
        if data is not None:
            return data
        with _take(_slots(cache), SLOT_WAIT):
            try:
                with contextlib.ExitStack() as work:
                    fd, ext = item.fd, _ext(item)
                    if desc["type"] in CONVERT_TYPES:                     # Office, HTML, метафайл: рисуется готовый PDF от контейнера
                        fd, ext = _open_base(home, item.sha), "pdf"
                        work.callback(os.close, fd)
                    result, data, _ = _execute(home, "render", fd, ext, wanted, desc["kind"], work)
            except _Fail as fail:
                raise PreviewError(fail.message)
        if result["kind"] == "none":
            raise PreviewError(_note(result))
        try:
            _store_page(home, item.sha, wanted, data)
        except _Fail as fail:
            raise PreviewError(fail.message)
        return data

"""Рабочий процесс просмотра файла (FR-76): единственное место, где читаются байты непроверенного файла.

Родитель (`preview.py`) запускает его дочерним процессом в `bwrap` без сети: процесс видит только свой входной файл, этот каталог кода
и библиотеки Python (всё — для чтения) и пустой выходной каталог. Пределы памяти, времени процессора и размера файла процесс ставит на себя
сам, первым делом и до импорта PyMuPDF и Pillow (`apply_limits` зовётся из точки входа).

    python3 preview_worker.py describe ВХОД ВЫХОД РАСШИРЕНИЕ            описание: вид, тип, страницы или текст
    python3 preview_worker.py render   ВХОД ВЫХОД РАСШИРЕНИЕ СТРАНИЦА   одна страница в PNG
    python3 preview_worker.py member   ВХОД ВЫХОД РАСШИРЕНИЕ НОМЕР      вложение письма: байты в member.bin

В выходной каталог пишется `result.json` и, если что-то нарисовано, `page.png` (у вложения — `member.bin`) — больше ничего. Наружу выходит
только то, что родитель готов принять: PNG и простой текст; разметки, сценариев и содержимого программ в ответе нет. Расширение подсказывает
тип там, где `filetype_sniff` по содержимому не различает (emf, wmf, vsd и подобные).

Ответ — словарь с видом `kind`: `text`, `pages`, `image`, `mail`, `listing`, `page`, `member` или `none` (причина `reason` и параметры `args` —
слова каталога сообщений, которые родитель собирает сам). Точка расширения для следующих порций (кадр видео — 17, преобразователь — 15):
правило в `_classify`, обработчик в `DESCRIBE` и `RENDER`, схема ответа — в `preview.SCHEMAS`.

Письма (eml, msg), состав архивов (zip, tar, 7z, rar) и календарь (ics) — тоже здесь: письмо разбирают `email` и `extract_msg`, HTML письма
переводится в текст разбором приёмки (`gate._html_text`), архивы не распаковываются (zip и tar — стандартная библиотека, 7z и rar — `7z l`),
имена и поля из письма и архива — данные: длина обрезается, управляющие знаки заменяются, путём они не становятся никогда.
"""
import collections
import contextlib
import json
import math
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile

LIMIT_AS = 2 * 1024 ** 3                 # адресное пространство
LIMIT_CPU = 60                           # секунд процессора
LIMIT_FSIZE = 200 * 1024 ** 2            # самый большой файл, который процесс может записать

# те же значения проверяет родитель (preview.py): тест сверяет, что они совпадают
TEXT_LIMIT = 20_000
MAX_PAGES = 20
MAX_PAGE_PIXELS = 4_000_000
MAX_PAGE_SIDE = 10_000
MAX_IMAGE_SIDE = 2000
IMAGE_MEGAPIXELS = 100                   # больше заявлено — картинка не декодируется

BASE_ZOOM = 2.0                          # обычная плотность страницы: 144 точки на дюйм
HEAD = 65_536
IMAGE_FORMATS = ["PNG", "JPEG", "GIF", "BMP", "TIFF", "WEBP"]
PNG_MODES = ("1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16")        # что PNG записывает как есть; остальное приводится к RGB или RGBA

# письма, архивы, вложения: те же значения проверяет родитель (preview.py), тест сверяет, что они совпадают
MAIL_PEOPLE = 100                        # получателей в каждом списке
MAIL_LINE = 300                          # знаков в адресе, поле «от кого» и подписи в календаре
MAIL_SUBJECT = 1000
MAX_ATTACHMENTS = 200                    # вложений в списке; номера от 0 до 199
NAME_LIMIT = 255                         # знаков в имени вложения и записи архива
LISTING_MAX = 500                        # строк в составе архива
MEMBER_MAX = 64 * 1024 ** 2              # вложение больше не достаётся (и не открывается)
# управляющие знаки, знаки смены направления письма и разделители строк: в именах они заменяются, а родитель такие имена не принимает
NAME_BAD = r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069\ud800-\udfff]"

MAIL_MAX = 100 * 1024 ** 2               # письмо читается в память целиком: больше этого не разбирается
MAIL_HTML = 1_000_000                    # сколько знаков HTML-части идёт в разбор
ICS_READ = 1 << 20                       # сколько байт календаря читается
NO_PASSWORD = "-pflyarchive-no-password"   # 7z без ключа -p спрашивает пароль с клавиатуры и висит
TYPE_WORD = re.compile(r"[a-z0-9][a-z0-9._+-]{0,19}")

CONVERTER_DOCS = frozenset(("doc", "docx", "rtf", "odt", "xls", "xlsx", "ods", "ppt", "pptx", "odp", "vsdx", "html"))
CONVERTER_TEXT = frozenset(("csv", "bpmn", "drawio"))
# хвост, который по содержимому не различается: решает расширение, но только после того, как файл не назван программой
CONVERTER_EXT = frozenset(("emf", "wmf", "wmz", "emz", "vsd", "xlsm", "xlsb", "pptm", "ppsx", "mp4", "mov", "mkv", "mp3"))


def apply_limits():
    """Пределы процесса. Потолок равен пределу: поднять его процесс не может. Зовётся первым, до импорта библиотек."""
    for name, value in ((resource.RLIMIT_AS, LIMIT_AS), (resource.RLIMIT_CPU, LIMIT_CPU), (resource.RLIMIT_FSIZE, LIMIT_FSIZE),
                        (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(name, (value, value))


def _none(type_, reason, args):
    return {"kind": "none", "type": type_, "reason": reason, "args": args}


def _guarded(type_, fn, *args):
    """Сбой разбора — ответ none с названием сбоя, а не падение процесса."""
    try:
        return fn(*args)
    except MemoryError:
        return _none(type_, "memory", {})
    except Exception as e:                                  # любой сбой библиотеки: файл испорчен или не по зубам
        return _none(type_, "broken", {"error_type": type(e).__name__})


# ── какой это файл ──────────────────────────────────────────────
def _sniff_image(head):
    """GIF, BMP и WEBP: `filetype_sniff` их по содержимому не знает, а Pillow читает."""
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"BM" and head[6:10] == b"\x00\x00\x00\x00" and int.from_bytes(head[14:18], "little") in (12, 40, 52, 56, 64, 108, 124):
        return "bmp"
    return None


def _is_calendar(head):
    return head.lstrip(b"\xef\xbb\xbf \t\r\n")[:15].upper() == b"BEGIN:VCALENDAR"


def _classify(path, ext):
    """(маршрут, тип, причина и параметры для none). Маршрут: text, pdf, svg, image, mail, archive, ics или none."""
    import filetype_sniff
    with open(path, "rb") as f:
        head = f.read(HEAD)
    if not head:
        return "none", "empty", ("empty", {})
    sniffed = _sniff_image(head)
    if sniffed:
        return "image", sniffed, None
    kind = filetype_sniff.detect(path, "x." + ext if ext else "x")
    family, type_ = kind.family, kind.type
    if family == "executable":
        return "none", type_, ("program", {})
    if family == "image":
        return "image", type_, None
    if family == "mail":
        return "mail", type_, None
    if family == "archive" and ext not in CONVERTER_EXT:     # xlsb, wmz и emz по содержимому — zip и gz, но это документы: решает расширение
        return "archive", type_, None
    if type_ == "broken-zip":                                # оглавление zip не читается: файл испорчен, а не неизвестен
        return "none", type_, ("broken", {"error_type": "BadZipFile"})
    if family == "document":
        if type_ == "pdf":
            return "pdf", type_, None
        if type_ in CONVERTER_DOCS:
            return "none", type_, ("needs_converter", {"type": type_})
    elif family == "text":
        if _is_calendar(head):
            return "ics", "ics", None
        if type_ == "svg" and b"<svg" in head.lower():
            return "svg", type_, None
        if type_ in CONVERTER_TEXT:
            return "none", type_, ("needs_converter", {"type": type_})
        return "text", type_, None
    if ext in CONVERTER_EXT:
        return "none", ext, ("needs_converter", {"type": ext})
    return "none", type_, ("unsupported", {"type": type_})


# ── текст ───────────────────────────────────────────────────────
def _decode(data):
    """Как `filetype_sniff`: UTF-16 по метке, иначе UTF-8, иначе cp1251. Оборванный в конце знак не ошибка; испорченное — U+FFFD."""
    import codecs
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        try:
            codecs.getincrementaldecoder("utf-8-sig")().decode(data[:4096], final=False)
            encoding = "utf-8-sig"
        except UnicodeDecodeError:
            encoding = "cp1251"
    return codecs.getincrementaldecoder(encoding)(errors="replace").decode(data, final=False)


def _describe_text(path, out, type_):
    with open(path, "rb") as f:
        data = f.read(TEXT_LIMIT * 4 + 9)            # по четыре байта на знак хватает на TEXT_LIMIT знаков и ещё один
    more = len(data) > TEXT_LIMIT * 4 + 8
    text = "".join(ch if ch >= " " or ch in "\t\n\r" else "\N{REPLACEMENT CHARACTER}" for ch in _decode(data[:TEXT_LIMIT * 4 + 8]))
    return {"kind": "text", "type": type_, "text": text[:TEXT_LIMIT], "truncated": more or len(text) > TEXT_LIMIT}


# ── PDF и SVG ───────────────────────────────────────────────────
def _pymupdf():
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    return pymupdf


def _describe_pages(path, out, type_):
    doc = _pymupdf().open(path, filetype=type_)
    try:
        if doc.needs_pass:
            return _none(type_, "encrypted", {})
        count = doc.page_count
        if count < 1:
            raise ValueError("в документе нет страниц")
        return {"kind": "pages", "type": type_, "pages": count, "shown": min(count, MAX_PAGES)}
    finally:
        doc.close()


def _fit_zoom(width, height):
    """Масштаб страницы width×height пунктов. Плотность выбирается до рисования: обычная, но так, чтобы страница вышла не больше
    MAX_PAGE_PIXELS точек и MAX_PAGE_SIDE по стороне; запас в одну точку — на округление."""
    if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
        raise ValueError("у страницы нет размера")
    zoom = min(BASE_ZOOM, math.sqrt(MAX_PAGE_PIXELS / (width * height)), MAX_PAGE_SIDE / max(width, height))
    for _ in range(200):
        if (math.ceil(width * zoom) + 1) * (math.ceil(height * zoom) + 1) <= MAX_PAGE_PIXELS:
            return zoom
        zoom *= 0.99
    raise ValueError("страницу не вписать в предел")


def _render_pages(path, out, type_, page):
    pymupdf = _pymupdf()
    doc = pymupdf.open(path, filetype=type_)
    try:
        if doc.needs_pass:
            return _none(type_, "encrypted", {})
        if not 1 <= page <= min(doc.page_count, MAX_PAGES):
            raise IndexError("страницы нет среди показываемых")
        sheet = doc.load_page(page - 1)
        zoom = _fit_zoom(sheet.rect.width, sheet.rect.height)
        pixmap = sheet.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        if pixmap.width * pixmap.height > MAX_PAGE_PIXELS or max(pixmap.width, pixmap.height) > MAX_PAGE_SIDE:
            raise ValueError("страница вышла больше предела")
        pixmap.save(os.path.join(out, "page.png"))
        return {"kind": "page", "page": page}
    finally:
        doc.close()


# ── картинки ────────────────────────────────────────────────────
def _draw_image(path, out, type_):
    """Картинка в PNG не больше MAX_IMAGE_SIDE по длинной стороне. Заявленный размер сверяется до чтения точек."""
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = None                    # свой предел — ниже, по заявленному размеру и до декодирования
    with Image.open(path, formats=IMAGE_FORMATS) as image:
        width, height = image.size
        if width * height > IMAGE_MEGAPIXELS * 1_000_000:
            return _none(type_, "too_large", {"megapixels": math.ceil(width * height / 1_000_000), "limit": IMAGE_MEGAPIXELS})
        if image.format == "JPEG":
            image.draft(None, (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))          # JPEG распаковывается сразу уменьшенным
        image.load()                                 # оборванный файл — исключение здесь, а не в середине записи
        if image.mode not in PNG_MODES:
            image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        image = ImageOps.exif_transpose(image)
        image.save(os.path.join(out, "page.png"), "PNG")
    return {"kind": "image", "type": type_}


def _describe_image(path, out, type_):
    return _draw_image(path, out, type_)             # описание картинки и есть её отрисовка: страница один уже готова


def _render_image(path, out, type_, page):
    if page != 1:
        raise IndexError("у картинки одна страница")
    done = _draw_image(path, out, type_)
    return {"kind": "page", "page": 1} if done["kind"] == "image" else done


# ── имена и поля из письма и архива: данные ─────────────────────
def _fit(value, limit=NAME_LIMIT):
    """Строка из чужого файла: управляющие знаки заменены, длина не больше limit (хвост — «…»)."""
    text = re.sub(NAME_BAD, "\N{REPLACEMENT CHARACTER}", value if isinstance(value, str) else str(value))
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _plain(text):
    """Текст письма: как у обычного текста, переводы строк и табуляция целы, остальные управляющие знаки заменены."""
    return "".join(ch if ch >= " " or ch in "\t\n\r" else "\N{REPLACEMENT CHARACTER}" for ch in text)


def _html_text(source):
    """HTML-часть письма в текст — тем же разбором, что в приёмке: второго разбора здесь нет."""
    from gate import _html_text as parse
    return parse(source[:MAIL_HTML], [])


# ── письма ──────────────────────────────────────────────────────
# Письмо eml или msg одним видом: от кого, кому, копия, дата, тема, части текста и вложения. parts — [(имя, функция, отдающая байты)]
Letter = collections.namedtuple("Letter", "sender to cc date subject plain html parts")


def _iso(value):
    """Дата письма как ISO 8601 или None: негодная и несуществующая дата — не сбой."""
    import datetime
    try:
        if isinstance(value, str):
            from email.utils import parsedate_to_datetime
            value = parsedate_to_datetime(value)
        return value.isoformat() if isinstance(value, datetime.datetime) else None
    except (TypeError, ValueError, OverflowError, AttributeError, IndexError):
        return None


def _addresses(msg, name):
    out = []
    for header in msg.get_all(name) or []:
        try:
            found = [str(a) for a in header.addresses]
        except Exception:                                    # заголовок с изъяном: берётся как есть
            found = []
        if not found and str(header).strip():
            found = [str(header).strip()]
        out += found
    return out


def _part_text(part):
    try:
        return part.get_content()
    except Exception:                                        # неизвестная кодировка и прочее: байты читаются как обычный текст
        return _decode(part.get_payload(decode=True) or b"")


def _eml_parts(msg):
    """Вложения письма по порядку: часть с именем или признаком «вложение», а также письмо, приложенное письмом. Не больше MAX_ATTACHMENTS + 1:
    лишнее нужно только затем, чтобы знать, что список обрезан."""
    found = []

    def visit(part):
        if len(found) > MAX_ATTACHMENTS:
            return
        if part.get_content_type() == "message/rfc822":
            found.append(part)
        elif part.is_multipart():
            for sub in part.iter_parts():
                visit(sub)
        elif part.get_filename() or part.get_content_disposition() == "attachment":
            found.append(part)

    visit(msg)
    return found


def _eml_bytes(part):
    if part.get_content_type() == "message/rfc822" and part.is_multipart():
        return part.get_payload(0).as_bytes()
    return part.get_payload(decode=True) or b""


@contextlib.contextmanager
def _eml_letter(path):
    import email
    from email import policy
    with open(path, "rb") as f:
        msg = email.message_from_bytes(f.read(), policy=policy.default)
    plain, html = msg.get_body(preferencelist=("plain",)), msg.get_body(preferencelist=("html",))
    header = lambda name: str(msg.get(name) or "")                          # noqa: E731
    parts = [(part.get_filename() or f"attachment-{number + 1}", lambda part=part: _eml_bytes(part)) for number, part in enumerate(_eml_parts(msg))]
    yield Letter(", ".join(_addresses(msg, "From")), _addresses(msg, "To"), _addresses(msg, "Cc"), _iso(getattr(msg.get("Date"), "datetime", None)),
                 header("Subject"), _part_text(plain) if plain is not None else "", _part_text(html) if html is not None else "", parts)


def _msg_bytes(attachment):
    data = attachment.data
    if isinstance(data, bytes):
        return data
    if data is None:
        return b""
    try:                                                                    # письмо, вложенное в письмо Outlook: собирается обратно в файл .msg
        return data.exportBytes()
    except Exception:
        return data.exportBytes(allowBadEmbed=True)


@contextlib.contextmanager
def _msg_letter(path):
    import extract_msg
    msg = extract_msg.Message(path)
    try:
        people = {1: [], 2: []}                                              # 1 — кому, 2 — копия; скрытая копия не показывается
        for r in msg.recipients:
            with contextlib.suppress(Exception):
                people[int(r.type)].append(r.formatted or r.email or r.name or "")
        to = people[1] or ([msg.to] if msg.to else [])
        parts = [(getattr(a, "longFilename", None) or getattr(a, "shortFilename", None) or f"attachment-{number + 1}", lambda a=a: _msg_bytes(a))
                 for number, a in enumerate(list(msg.attachments)[:MAX_ATTACHMENTS + 1])]
        html = msg.htmlBody
        yield Letter(msg.sender or "", to, people[2], _iso(msg.date), msg.subject or "", msg.body or "",
                     _decode(html) if isinstance(html, bytes) else (html or ""), parts)
    finally:
        msg.close()


def _letter(path, type_):
    return _msg_letter(path) if type_ == "msg" else _eml_letter(path)


def _too_big(path, type_):
    """none для письма, которое не читается целиком, или None, если оно в пределе."""
    return _none(type_, "too_big", {"limit": MAIL_MAX >> 20}) if os.path.getsize(path) > MAIL_MAX else None


def _kind_of(data, name):
    """(тип по содержимому, программа ли). `filetype_sniff.detect` читает путь, поэтому вложение на миг ложится в /tmp песочницы."""
    import filetype_sniff
    program = filetype_sniff.is_executable(name, data[:filetype_sniff.HEAD])
    if len(data) > MEMBER_MAX:
        return "unknown", program
    try:
        with tempfile.TemporaryDirectory(prefix="att-") as folder:
            probe = os.path.join(folder, "data")
            with open(probe, "wb") as f:
                f.write(data)
            kind = filetype_sniff.detect(probe, name)
    except Exception:
        return "unknown", program
    return (kind.type if TYPE_WORD.fullmatch(kind.type) else "unknown"), program or kind.family == "executable"


def _people(items):
    return [_fit(item, MAIL_LINE) for item in items[:MAIL_PEOPLE]]


def _describe_mail(path, out, type_):
    refused = _too_big(path, type_)
    if refused:
        return refused
    with _letter(path, type_) as letter:
        body = letter.plain if letter.plain.strip() or not letter.html.strip() else _html_text(letter.html)
        text = _plain(body[:TEXT_LIMIT + 1])
        rows, cut = [], len(letter.parts) > MAX_ATTACHMENTS               # список обрезан до MAX_ATTACHMENTS: признак truncated, как у текста
        for number, (name, get) in enumerate(letter.parts[:MAX_ATTACHMENTS]):
            failed = False
            try:
                data = get()
            except MemoryError:
                raise
            except Exception:                                # одно испорченное вложение не прячет письмо: в списке оно пустое и неизвестного типа
                data, failed = b"", True
            kind, program = ("unknown", False) if failed else _kind_of(data, name)
            rows.append({"name": _fit(name), "size": len(data), "type": kind, "member": number, "executable": bool(program)})
        mail = {"from": _fit(letter.sender, MAIL_LINE), "to": _people(letter.to), "cc": _people(letter.cc), "date": letter.date,
                "subject": _fit(letter.subject, MAIL_SUBJECT), "text": text[:TEXT_LIMIT], "truncated": len(text) > TEXT_LIMIT or cut, "attachments": rows}
    return {"kind": "mail", "type": type_, "mail": mail}


def extract(path, out, ext, number):
    """Вложение письма номер number: байты — в out/member.bin, ответ — имя. Номера вне списка и файла без вложений — none no_member.
    Байты вложения родитель не разбирает: считает sha256 и, если надо, отдаёт их новому рабочему процессу как обычный файл."""
    route, type_, _ = _classify(path, ext)
    if route != "mail":
        return _none(type_, "no_member", {"member": number, "count": 0})
    return _guarded(type_, _extract_member, path, out, type_, number)


def _extract_member(path, out, type_, number):
    refused = _too_big(path, type_)
    if refused:
        return refused
    with _letter(path, type_) as letter:
        count = min(len(letter.parts), MAX_ATTACHMENTS)
        if number >= count:
            return _none(type_, "no_member", {"member": number, "count": count})
        name, get = letter.parts[number]
        data = get()
        if len(data) > MEMBER_MAX:
            return _none(type_, "too_big", {"limit": MEMBER_MAX >> 20})
        with open(os.path.join(out, "member.bin"), "wb") as f:                # имя вложения в путь не попадает никогда
            f.write(data)
    return {"kind": "member", "name": _fit(name)}


# ── архивы ──────────────────────────────────────────────────────
class ListingFailed(Exception):
    """Программа 7z не смогла показать состав архива."""


def _listing(type_, rows, more):
    return {"kind": "listing", "type": type_, "listing": rows, "truncated": more}


def _list_zip(path):
    import zipfile
    from unpack import _zip_name                                            # русские имена в кодировке DOS читаются так же, как при распаковке
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if any(info.flag_bits & 0x1 for info in infos):
            return _none("zip", "encrypted", {})
        rows, more = [], False
        for info in infos:
            name = _zip_name(info)
            if info.is_dir() or name.endswith(("/", "\\")):
                continue
            if len(rows) == LISTING_MAX:
                more = True
                break
            rows.append({"name": _fit(name), "size": info.file_size})
    return _listing("zip", rows, more)


def _list_tar(path, type_):
    import tarfile
    try:
        archive = tarfile.open(path, "r:*")
    except tarfile.ReadError:
        if type_ in ("gz", "bz2", "xz"):                                    # один сжатый файл, не tar
            return _none(type_, "unsupported", {"type": type_})
        raise
    rows, more, seen = [], False, False
    with archive:
        for member in archive:                                              # только заголовки: ни extract, ни extractfile
            seen = True
            if member.isdir():
                continue
            if len(rows) == LISTING_MAX:
                more = True
                break
            rows.append({"name": _fit(member.name), "size": member.size if member.isreg() else 0})
    if not seen and type_ in ("gz", "bz2", "xz"):                           # поток нулей читается как пустой tar — это не он
        return _none(type_, "unsupported", {"type": type_})
    return _listing(type_, rows, more)


SEVEN_KEY = re.compile(r"([A-Za-z][A-Za-z ]*?) = ?(.*)")


def _list_7z(path, type_):
    """Состав 7z и rar: `7z l -slt`, вывод читается построчно и обрывается на 501-й записи. Распаковки нет."""
    exe = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
    if exe is None:
        return _none(type_, "needs_extractor", {"type": type_})
    proc = subprocess.Popen([exe, "l", "-slt", "-ba", NO_PASSWORD, "--", path], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    rows, errors, current, more, encrypted = [], [], {}, False, False

    def take():
        """Запись закончилась: каталог пропускается, зашифрованная отмечается, остальные — в список. True — список полон."""
        nonlocal encrypted, more
        entry = dict(current)
        current.clear()
        if "Path" not in entry:
            return False
        encrypted = encrypted or entry.get("Encrypted") == "+"
        if entry.get("Folder") == "+" or entry.get("Attributes", "").startswith("D"):
            return False
        if len(rows) == LISTING_MAX:
            more = True
            return True
        size = entry.get("Size", "")
        rows.append({"name": _fit(entry["Path"]), "size": int(size) if size.isdigit() else 0})
        return False

    last = None
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line.strip():
                if take():
                    break
                last = None
                continue
            found = SEVEN_KEY.fullmatch(line)
            if found:
                last = found.group(1)
                current[last] = found.group(2)
            elif last == "Path":                                            # в имени был перевод строки: это продолжение имени
                current["Path"] += " " + line
            elif line.startswith(("ERROR", "WARNING")) and len(errors) < 20:
                errors.append(line[:200])
        else:
            take()
    finally:
        with contextlib.suppress(OSError):
            proc.kill()
        proc.stdout.close()
        proc.wait()
    if encrypted:
        return _none(type_, "encrypted", {})
    if proc.returncode not in (0, 1) and not more:
        text = " ".join(errors).lower()
        if "password" in text or "encrypted" in text:
            return _none(type_, "encrypted", {})
        raise ListingFailed(text[:100])
    return _listing(type_, rows, more)


def _describe_archive(path, out, type_):
    if type_ in ("7z", "rar"):
        return _list_7z(path, type_)
    if type_ == "zip":
        return _list_zip(path)
    return _list_tar(path, type_)


# ── календарь ───────────────────────────────────────────────────
def _ics_split(line):
    """(имя свойства, параметры, значение). Двоеточие и точка с запятой в кавычках параметра не разделители."""
    quoted, parts, start = False, [], 0
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in ";:":
            parts.append(line[start:i])
            start = i + 1
            if ch == ":":
                params = {}
                for one in parts[1:]:
                    key, _, value = one.partition("=")
                    params[key.strip().upper()] = value.strip().strip('"')
                return parts[0].strip().upper(), params, line[i + 1:]
    return "", {}, ""


ICS_WHEN = re.compile(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(?:\d{2})?(Z)?)?")


def _ics_when(value, params):
    value = value.strip()
    found = ICS_WHEN.fullmatch(value)
    if not found:
        return _fit(value, MAIL_LINE) if value else ""
    year, month, day, hour, minute, utc = found.groups()
    text = f"{year}-{month}-{day}"
    if hour is None:
        return text
    text += f" {hour}:{minute}"
    if utc:
        return text + " UTC"
    return text + (f" ({_fit(params['TZID'], MAIL_LINE)})" if params.get("TZID") else "")


def _ics_unescape(value):
    return re.sub(r"\\(.)", lambda m: " " if m.group(1) in "nN" else m.group(1), value)


def _describe_ics(path, out, type_):
    """События календаря: когда, тема, место, число участников. Сырой файл наружу не выходит; напоминания и вложенные части — не события."""
    with open(path, "rb") as f:
        raw = f.read(ICS_READ + 1)
    more = len(raw) > ICS_READ
    events, event, depth = [], None, 0
    for line in re.sub(r"\r?\n[ \t]", "", _decode(raw[:ICS_READ])).splitlines():      # свёрнутые строки разворачиваются
        name, params, value = _ics_split(line)
        if name == "BEGIN":
            if event is not None:
                depth += 1
            elif value.strip().upper() == "VEVENT":
                event, depth = {"attendees": 0}, 0
        elif name == "END":
            if event is not None and depth > 0:
                depth -= 1
            elif event is not None and value.strip().upper() == "VEVENT":
                events.append(event)
                event = None
        elif event is not None and depth == 0:
            if name in ("SUMMARY", "LOCATION"):
                event[name] = _ics_unescape(value)
            elif name in ("DTSTART", "DTEND"):
                event[name] = _ics_when(value, params)
            elif name == "ATTENDEE":
                event["attendees"] += 1
    if not events:
        return _none(type_, "no_events", {})
    blocks, size = [], 0
    for number, e in enumerate(events, 1):
        when = " — ".join(x for x in (e.get("DTSTART"), e.get("DTEND")) if x) or "—"
        blocks.append(f"Event {number}\nWhen: {when}\nSubject: {_fit(e.get('SUMMARY') or '—', MAIL_LINE)}\n"
                      f"Location: {_fit(e.get('LOCATION') or '—', MAIL_LINE)}\nAttendees: {e['attendees']}")
        size += len(blocks[-1]) + 2
        if size > TEXT_LIMIT:
            more = True
            break
    text = "\n\n".join(blocks)
    return {"kind": "text", "type": type_, "text": text[:TEXT_LIMIT], "truncated": more or len(text) > TEXT_LIMIT}


DESCRIBE = {"text": _describe_text, "pdf": _describe_pages, "svg": _describe_pages, "image": _describe_image, "mail": _describe_mail,
            "archive": _describe_archive, "ics": _describe_ics}
RENDER = {"pdf": _render_pages, "svg": _render_pages, "image": _render_image}


def describe(path, out, ext):
    route, type_, why = _classify(path, ext)
    if route == "none":
        return _none(type_, *why)
    return _guarded(type_, DESCRIBE[route], path, out, type_)


def render(path, out, ext, page):
    route, type_, why = _classify(path, ext)
    if route == "none":
        return _none(type_, *why)
    if route not in RENDER:
        return _none(type_, "broken", {"error_type": "NoPages"})
    return _guarded(type_, RENDER[route], path, out, type_, page)


def _arguments(argv):
    """(действие, вход, выход, расширение, страница или номер вложения) или None, если аргументы негодны."""
    if len(argv) == 4 and argv[0] == "describe":
        return argv[0], argv[1], argv[2], argv[3], None
    if len(argv) == 5 and argv[0] == "render" and argv[4].lstrip("-").isdigit():
        return argv[0], argv[1], argv[2], argv[3], int(argv[4])
    if len(argv) == 5 and argv[0] == "member" and argv[4].isascii() and argv[4].isdigit():
        return argv[0], argv[1], argv[2], argv[3], int(argv[4])
    return None


def main(argv):
    """Одно действие; ответ — result.json в выходном каталоге. Сбой записывается ответом none: код возврата 0 значит «ответ есть»."""
    parsed = _arguments(argv)
    if parsed is None:
        return 2
    op, path, out, ext, page = parsed
    try:
        if op == "describe":
            result = describe(path, out, ext)
        elif op == "render":
            result = render(path, out, ext, page)
        else:
            result = extract(path, out, ext, page)
    except MemoryError:
        result = _none("unknown", "memory", {})
    except Exception as e:
        result = _none("unknown", "broken", {"error_type": type(e).__name__})
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    apply_limits()
    sys.exit(main(sys.argv[1:]))

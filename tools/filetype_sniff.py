"""Тип файла по содержимому. Расширению не верим: оно только сверяется с найденным типом.

    detect(path) -> Kind(family, type, ext_ok, macros, detail)

family: document | image | mail | text | archive | executable | other
"""
import codecs
import os
import zipfile
from collections import namedtuple

Kind = namedtuple("Kind", "family type ext_ok macros detail", defaults=(True, False, ""))

HEAD = 4096
# расширения, которые запускаются двойным щелчком или оболочкой: программа, каким бы ни было содержимое
EXEC_EXT = {"exe", "dll", "sys", "scr", "pif", "com", "cpl", "msi", "msp", "bat", "cmd", "ps1", "psm1",
            "sh", "js", "jse", "vbs", "vbe", "wsf", "hta", "reg", "lnk", "jar", "apk"}
TEXT_EXT = {"txt", "md", "csv", "tsv", "json", "xml", "log", "sql", "yaml", "yml", "ini", "conf",
            "properties", "bpmn", "svg", "drawio"}
# какие расширения допустимы для найденного типа
EXT_FOR = {
    "pdf": {"pdf"}, "rtf": {"rtf"}, "html": {"html", "htm"},
    "docx": {"docx", "docm", "dotx", "dotm"}, "xlsx": {"xlsx", "xlsm", "xltx", "xltm"},
    "pptx": {"pptx", "pptm", "ppsx", "potx"}, "vsdx": {"vsdx", "vsdm"},
    "odt": {"odt"}, "ods": {"ods"}, "odp": {"odp"}, "epub": {"epub"},
    "doc": {"doc", "dot"}, "xls": {"xls", "xlt"}, "ppt": {"ppt", "pps"}, "msg": {"msg"},
    "png": {"png"}, "jpg": {"jpg", "jpeg"}, "tiff": {"tif", "tiff"}, "eml": {"eml"},
}
# по расширению: каким документом должен быть zip, у которого не читается оглавление
OFFICE_BY_EXT = {e: t for t, exts in EXT_FOR.items() if t in ("docx", "xlsx", "pptx", "vsdx", "odt", "ods", "odp", "epub") for e in exts}
KNOWN_EXT = set().union(*EXT_FOR.values()) | {"zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "gif", "bmp", "mp4"}
MAIL_HEADERS = (b"from:", b"to:", b"subject:", b"date:", b"message-id:", b"received:", b"return-path:",
                b"mime-version:", b"delivered-to:", b"content-type:", b"x-mailer:", b"reply-to:", b"cc:")
# заголовки, которых не бывает в обычном тексте: одного достаточно
MAIL_ONLY = (b"received:", b"return-path:", b"delivered-to:", b"message-id:", b"mime-version:")
FIELD = __import__("re").compile(rb"^[!-9;-~]+:")       # имя поля по RFC 5322: печатные знаки без пробела и двоеточия
ODF = {"application/vnd.oasis.opendocument.text": "odt", "application/vnd.oasis.opendocument.spreadsheet": "ods",
       "application/vnd.oasis.opendocument.presentation": "odp", "application/epub+zip": "epub"}
OOXML = (("word/", "docx"), ("xl/", "xlsx"), ("ppt/", "pptx"), ("visio/", "vsdx"))


def ext_of(path):
    return os.path.splitext(path)[1].lower().lstrip(".")


def _zip_kind(path):
    """Zip бывает архивом, документом Office, ODF, EPUB, а также программой (jar, apk)."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            first = ""
            if names and names[0] == "mimetype":
                with z.open(names[0]) as f:       # только начало: член может распаковываться в гигабайты
                    first = f.read(80).decode("ascii", "replace").strip()
    except Exception:
        return "other", "broken-zip", False
    low = {n.lower() for n in names}
    if any(n.startswith("index/") and n.endswith(".iwa") for n in low):
        return "other", "iwork", False            # Pages, Numbers, Keynote: читать нечем, но это не архив
    if "androidmanifest.xml" in low and "classes.dex" in low:
        return "executable", "apk", False
    if "meta-inf/manifest.mf" in low or any(n.endswith(".class") for n in low):
        return "executable", "jar", False
    if first in ODF:
        return "document", ODF[first], False
    if "[content_types].xml" in low:
        for prefix, type_ in OOXML:
            if any(n.startswith(prefix) for n in low):
                return "document", type_, any(n.endswith("vbaproject.bin") for n in low)
    return "archive", "zip", False


def _ole_kind(path):
    """Старые форматы Office, письма Outlook и установщики MSI живут в одном контейнере. Возвращает (семейство, тип, макросы, деталь):
    деталь no-olefile — библиотеки нет, и контейнер не разобран; приёмка называет её в отказе, а не пишет «не документ» без причины."""
    try:
        import olefile
    except ImportError:
        return "other", "ole", False, "no-olefile"
    try:
        with olefile.OleFileIO(path) as ole:
            streams = {"/".join(s).lower() for s in ole.listdir()}
            clsid = (ole.root.clsid or "").upper()
    except Exception:
        return "other", "ole", False, ""
    if clsid == "000C1084-0000-0000-C000-000000000046":
        return "executable", "msi", False, ""
    macros = any(s.split("/")[0] in ("macros", "_vba_project_cur") or "/vba/" in "/" + s + "/" for s in streams)
    if "worddocument" in streams:
        return "document", "doc", macros, ""
    if "workbook" in streams or "book" in streams:
        return "document", "xls", macros, ""
    if "powerpoint document" in streams:
        return "document", "ppt", macros, ""
    if any(s.startswith("__properties_version1.0") or s.startswith("__substg1.0_") for s in streams):
        return "mail", "msg", False, ""
    return "other", "ole", macros, ""


def _looks_like_mail(head):
    """Письмо: в заголовочной части до пустой строки не меньше трёх известных заголовков."""
    block = head.replace(b"\r\n", b"\n").split(b"\n\n")[0].lower()
    lines = [l for l in block.split(b"\n") if l and not l.startswith((b" ", b"\t"))]
    # у писем Exchange первые килобайты заняты заголовками Received: до From и Subject кусок не доходит
    if len(lines) > 1 and block.count(b"\n") > 3 and b"\n\n" not in head.replace(b"\r\n", b"\n"):
        lines = lines[:-1]                        # последняя строка куска может быть оборвана
    if not lines or not all(FIELD.match(l) for l in lines[:8]):
        return False
    if any(l.startswith(MAIL_ONLY) for l in lines):
        return True
    return len({h for h in MAIL_HEADERS for l in lines if l.startswith(h)}) >= 3


BIG_HEAD = 65536


def _is_mail(path, head, ext):
    """Письмо ли это. У письма с длинным списком получателей первые килобайты заняты
    заголовками To и CC — тогда решает кусок побольше."""
    if _looks_like_mail(head):
        return True
    flat = head.replace(b"\r\n", b"\n")
    if b"\n\n" in flat:
        return False                              # заголовочная часть кончилась и письмом не оказалась
    lines = [l for l in flat.lower().split(b"\n") if l and not l.startswith((b" ", b"\t"))]
    if not lines or not all(FIELD.match(l) for l in lines[:-1][:8]) or not any(l.startswith(MAIL_HEADERS) for l in lines):
        return False
    with open(path, "rb") as f:
        big = f.read(BIG_HEAD)
    if len(big) > len(head) and _looks_like_mail(big):
        return True
    return ext == "eml" and b"\n\n" not in big.replace(b"\r\n", b"\n")


def _decode(data):
    """Текст ли это. Возвращает строку либо None."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if b"\x00" in data:
        return None
    for enc in ("utf-8-sig", "cp1251"):
        try:
            # кусок файла мог оборваться посреди символа — недописанный хвост не ошибка
            text = codecs.getincrementaldecoder(enc)().decode(data, final=False)
        except UnicodeDecodeError:
            continue
        odd = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r\f\x1b")
        if odd <= len(text) // 200:
            return text
    return None


EXEC_MAGIC = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
              b"\xca\xfe\xba\xbe", b"\x4c\x00\x00\x00\x01\x14\x02\x00")


def is_executable(name, head):
    """Быстрая проверка вложения письма по имени и первым байтам, без записи на диск."""
    if ext_of(name) in EXEC_EXT:
        return True
    if head.startswith(b"MZ") and b"\x00" in head[:256]:
        return True
    if head.startswith(EXEC_MAGIC):
        return True
    return head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"#!")


def detect(path, name=None):
    """name — настоящее имя файла, если он лежит под временным (распакован из архива)."""
    ext = ext_of(name or path)
    with open(path, "rb") as f:
        head = f.read(HEAD)
    if not head:
        return Kind("other", "empty")

    def kind(family, type_, macros=False, detail=""):
        allowed = EXT_FOR.get(type_)
        if family == "text":
            ok = ext not in KNOWN_EXT
        else:
            ok = not ext or allowed is None or ext in allowed
        return Kind(family, type_, ok, macros, detail)

    if head.startswith(b"\x00\x05\x16\x07") and b"Mac OS X" in head[:32]:
        return kind("other", "macos-sidecar")     # спутник «._имя», который macOS кладёт рядом с каждым файлом

    # ── программы по содержимому ────────────────────────────────
    if head.startswith(b"MZ") and b"\x00" in head[:256]:
        return kind("executable", "pe")
    if head.startswith(b"\x7fELF"):
        return kind("executable", "elf")
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        return kind("executable", "macho-or-class")
    if head.startswith(b"\x4c\x00\x00\x00\x01\x14\x02\x00"):
        return kind("executable", "lnk")
    if ext in EXEC_EXT:
        # имя решает, как файл запустит система: ярлык, скрипт и установщик опасны при любом содержимом,
        # в том числе когда оно начинается как zip — Windows пропустит первую строку и выполнит остальные
        if head.startswith(b"PK"):
            family, type_, _ = _zip_kind(path)
            if family == "executable":            # jar и apk называются своим именем
                return kind(family, type_)
        return kind("executable", "by-extension", detail="." + ext)

    # ── контейнеры ──────────────────────────────────────────────
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        family, type_, macros = _zip_kind(path)
        if type_ == "broken-zip" and ext in OFFICE_BY_EXT:
            # оборванное вложение: это испорченный документ, а не подозрительный архив
            return kind("document", OFFICE_BY_EXT[ext], detail="broken")
        return kind(family, type_, macros)
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        family, type_, macros, detail = _ole_kind(path)
        return kind(family, type_, macros, detail)
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return kind("archive", "7z")
    if head.startswith(b"Rar!\x1a\x07"):
        return kind("archive", "rar")
    if head.startswith(b"\x1f\x8b"):
        return kind("archive", "gz")
    if head.startswith(b"BZh"):
        return kind("archive", "bz2")
    if head.startswith(b"\xfd7zXZ\x00"):
        return kind("archive", "xz")
    if len(head) > 262 and head[257:262] == b"ustar":
        return kind("archive", "tar")

    # ── документы и изображения ─────────────────────────────────
    if head.startswith(b"%PDF-"):
        return kind("document", "pdf")
    if head.startswith(b"{\\rtf"):
        return kind("document", "rtf")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return kind("image", "png")
    if head.startswith(b"\xff\xd8\xff"):
        return kind("image", "jpg")
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return kind("image", "tiff")

    # ── текстовое ───────────────────────────────────────────────
    text = _decode(head)
    if text is None:
        return kind("other", "binary")
    start = text.lstrip("﻿ \t\r\n")
    if start.startswith("#!"):
        return kind("executable", "script", detail=start.split("\n")[0][:80])
    if start[:15].lower().startswith(("<!doctype html", "<html")):
        return kind("document", "html")
    if _is_mail(path, head, ext):
        return kind("mail", "eml")
    return kind("text", ext if ext in TEXT_EXT else "txt")

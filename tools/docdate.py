"""Дата документа при приёмке (FR-45).

По порядку: метаданные файла → дата в имени файла → время изменения → дата приёмки с пометкой.
Неправдоподобная дата (до 1990 года, из будущего, несуществующий день) пропускается, берётся следующая.

    date_of(path, "docx", "договор.docx", "2026-10-04", mtime=...) -> ("2024-03-05", "метаданные")
"""
import datetime
import os
import re
import time
import zipfile

MIN_YEAR = 1990
OOXML = ("docx", "xlsx", "pptx", "vsdx")
ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
DOTTED = re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)")
PDF_DATE = (re.compile(rb"/ModDate\s*\(D:(\d{4})(\d{2})(\d{2})"), re.compile(rb"/CreationDate\s*\(D:(\d{4})(\d{2})(\d{2})"))
CORE = (re.compile(r"<dcterms:modified[^>]*>\s*(\d{4})-(\d{2})-(\d{2})"), re.compile(r"<dcterms:created[^>]*>\s*(\d{4})-(\d{2})-(\d{2})"))
PDF_SCAN = 4 * 1024 * 1024


def plausible(year, month, day, latest):
    """Дата существует и лежит между 1990 годом и днём приёмки. Возвращает её строкой либо None."""
    try:
        value = datetime.date(int(year), int(month), int(day)).isoformat()
    except (TypeError, ValueError):
        return None
    return value if f"{MIN_YEAR}-01-01" <= value <= latest else None


def _from_metadata(path, type_, latest):
    if type_ in ("eml", "msg"):
        import known
        date = known.letter_info(path, type_)[2]
        return plausible(*date.split("-"), latest) if date and len(date.split("-")) == 3 else None
    if type_ in OOXML:
        with zipfile.ZipFile(path) as z:
            core = z.read("docProps/core.xml").decode("utf-8", "replace")
        for pattern in CORE:
            m = pattern.search(core)
            date = plausible(*m.groups(), latest) if m else None
            if date:
                return date
        return None
    if type_ == "pdf":
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(PDF_SCAN)
            if size > PDF_SCAN:                    # свойства часто лежат в конце файла
                f.seek(max(PDF_SCAN, size - PDF_SCAN))
                raw += f.read()
        for pattern in PDF_DATE:
            m = pattern.search(raw)
            date = plausible(*(g.decode() for g in m.groups()), latest) if m else None
            if date:
                return date
    return None


def _from_name(name, latest):
    base = os.path.basename(name.replace("\\", "/"))
    for m in ISO.finditer(base):
        date = plausible(*m.groups(), latest)
        if date:
            return date
    for m in DOTTED.finditer(base):
        date = plausible(m.group(3), m.group(2), m.group(1), latest)
        if date:
            return date
    return None


def date_of(path, type_, name, accepted, mtime=None, notes=None):
    """(дата, откуда она взята). accepted — день приёмки, он же верхняя граница правдоподобия. notes — список, куда кладётся замечание
    lib.missing, если дату письма прочесть нечем (нет библиотеки писем): дата тогда берётся из следующего источника, как и раньше, но причина названа."""
    if notes is not None:
        import known
        note = known.missing_note(type_)
        if note is not None:
            notes.append(note)
    try:
        date = _from_metadata(path, type_, accepted)
    except Exception:                              # испорченный файл: метаданных нет, идём дальше
        date = None
    if date:
        return date, "метаданные"
    date = _from_name(name, accepted)
    if date:
        return date, "имя"
    if mtime:
        day = time.localtime(mtime)
        date = plausible(day.tm_year, day.tm_mon, day.tm_mday, accepted)
        if date:
            return date, "время изменения"
    return accepted, "дата приёмки"

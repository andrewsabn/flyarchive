"""Тип файла по содержимому, расширению не верим: FR-20, FR-21, FR-31."""
import pytest

import filetype_sniff as F
import gatekit as K

NL = chr(10)


def kind(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return F.detect(str(p))


def test_модуль_определения_типа_называется_filetype_sniff():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.isfile(os.path.join(root, "tools", "filetype_sniff.py"))
    assert F.__name__ == "filetype_sniff"


# ── документы ───────────────────────────────────────────────────
@pytest.mark.parametrize("name, data, type_", [
    ("a.pdf", K.PDF, "pdf"), ("a.docx", K.ooxml("docx"), "docx"), ("a.xlsx", K.ooxml("xlsx"), "xlsx"),
    ("a.pptx", K.ooxml("pptx"), "pptx"), ("a.vsdx", K.ooxml("vsdx"), "vsdx"),
    ("a.odt", K.odf(), "odt"), ("a.ods", K.odf("application/vnd.oasis.opendocument.spreadsheet"), "ods"),
    ("a.epub", K.odf("application/epub+zip"), "epub"), ("a.rtf", K.RTF, "rtf"),
    ("a.html", b"<!DOCTYPE html><html><body>x</body></html>", "html"),
    ("a.htm", b"\xef\xbb\xbf  <html><body>x</body></html>", "html")])
def test_документ_опознаётся_по_содержимому(tmp_path, name, data, type_):
    k = kind(tmp_path, name, data)
    assert (k.family, k.type) == ("document", type_)


def test_документ_контейнер_не_считается_архивом(tmp_path):
    for name in ("docx", "xlsx", "pptx", "vsdx"):
        assert kind(tmp_path, "файл." + name, K.ooxml(name)).family == "document"
    assert kind(tmp_path, "a.odt", K.odf()).family == "document"


def test_макросы_в_документе_видны_сразу(tmp_path):
    k = kind(tmp_path, "a.docm", K.ooxml("docx", macros=True))
    assert (k.family, k.type, k.macros) == ("document", "docx", True)
    assert kind(tmp_path, "a.docx", K.ooxml("docx")).macros is False


@pytest.mark.parametrize("name, data, type_", [
    ("a.png", K.PNG, "png"), ("a.jpg", K.JPG, "jpg"), ("a.tiff", K.TIFF, "tiff")])
def test_изображение(tmp_path, name, data, type_):
    k = kind(tmp_path, name, data)
    assert (k.family, k.type) == ("image", type_)


def test_письмо(tmp_path):
    k = kind(tmp_path, "письмо.eml", K.EML)
    assert (k.family, k.type) == ("mail", "eml")
    assert kind(tmp_path, "без расширения", K.EML).type == "eml"


@pytest.mark.parametrize("name, data, type_", [
    ("a.txt", "Привет, архив".encode("utf-8"), "txt"), ("a.md", b"# Title\n\ntext", "md"),
    ("a.csv", "а;б\n1;2\n".encode("cp1251"), "csv"), ("a.json", b'{"a": 1}', "json"),
    ("a.log", "строка\n".encode("utf-16"), "log"), ("без_расширения", b"plain text\n", "txt")])
def test_текст(tmp_path, name, data, type_):
    k = kind(tmp_path, name, data)
    assert (k.family, k.type) == ("text", type_)


# ── исполняемое ─────────────────────────────────────────────────
@pytest.mark.parametrize("name, data", [
    ("a.exe", K.PE), ("a.dll", K.PE), ("a.scr", K.PE), ("отчёт.pdf", K.PE), ("отчёт.docx", K.PE),
    ("a", K.ELF), ("a.so", K.ELF), ("a.bin", K.MACHO), ("ярлык.lnk", K.LNK), ("счёт.pdf.lnk", K.LNK),
    ("a.jar", K.jar()), ("a.zip", K.jar()), ("a.apk", K.apk()), ("a.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x34")])
def test_исполняемое_по_содержимому_каким_бы_ни_было_имя(tmp_path, name, data):
    assert kind(tmp_path, name, data).family == "executable"


@pytest.mark.parametrize("name", ["a.bat", "a.cmd", "a.ps1", "a.sh", "a.js", "a.vbs", "a.wsf", "a.hta", "a.jse",
                                  "a.vbe", "a.reg", "a.PS1", "a.Bat", "a.psm1", "a.msi", "a.com", "a.pif", "a.cpl"])
def test_скрипт_по_расширению(tmp_path, name):
    assert kind(tmp_path, name, b"echo hello\r\n").family == "executable"


@pytest.mark.parametrize("data", [b"#!/bin/sh\nrm -rf /\n", b"#!/usr/bin/env python3\nprint(1)\n", b"\xef\xbb\xbf#!/bin/bash\n"])
def test_скрипт_по_первой_строке(tmp_path, data):
    assert kind(tmp_path, "заметка.txt", data).family == "executable"


# ── архивы ──────────────────────────────────────────────────────
@pytest.mark.parametrize("name, data, type_", [
    ("a.zip", K.zip_bytes({"a.txt": b"x"}), "zip"), ("a.dat", K.zip_bytes({"a.txt": b"x"}), "zip"),
    ("a.7z", K.SEVENZ, "7z"), ("a.rar", K.RAR, "rar"), ("a.tar", K.tar_bytes({"a.txt": b"x"}), "tar"),
    ("a.tar.gz", K.tar_bytes({"a.txt": b"x"}, "w:gz"), "gz"), ("a.tar.bz2", K.tar_bytes({"a.txt": b"x"}, "w:bz2"), "bz2"),
    ("a.tar.xz", K.tar_bytes({"a.txt": b"x"}, "w:xz"), "xz"), ("отчёт.docx", K.zip_bytes({"a.txt": b"x"}), "zip")])
def test_архив_по_содержимому(tmp_path, name, data, type_):
    k = kind(tmp_path, name, data)
    assert (k.family, k.type) == ("archive", type_)


def test_пустой_zip_тоже_архив(tmp_path):
    assert kind(tmp_path, "a.zip", K.zip_bytes({})).family == "archive"


# ── прочее ──────────────────────────────────────────────────────
@pytest.mark.parametrize("name, data", [("a.gif", K.GIF), ("a.bin", bytes(range(256)) * 4), ("a.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 40)])
def test_не_документ_и_не_программа(tmp_path, name, data):
    assert kind(tmp_path, name, data).family == "other"


def test_пустой_файл(tmp_path):
    k = kind(tmp_path, "a.docx", b"")
    assert (k.family, k.type) == ("other", "empty")


def test_битый_zip_без_расширения_документа(tmp_path):
    k = kind(tmp_path, "a.zip", b"PK\x03\x04" + b"\x00" * 40)
    assert (k.family, k.type) == ("other", "broken-zip")
    assert kind(tmp_path, "без расширения", b"PK\x03\x04" + b"\x00" * 40).type == "broken-zip"


@pytest.mark.parametrize("name, type_", [("a.docx", "docx"), ("a.pptx", "pptx"), ("a.xlsm", "xlsx"), ("a.PPTX", "pptx"), ("a.odt", "odt")])
def test_повреждённый_документ_office_остаётся_документом(tmp_path, name, type_):
    whole = K.ooxml("docx")
    k = kind(tmp_path, name, whole[:len(whole) // 2])          # файл оборван посередине
    assert (k.family, k.type, k.detail) == ("document", type_, "broken")


# ── расширение против содержимого ───────────────────────────────
@pytest.mark.parametrize("name, data, ok", [
    ("a.pdf", K.PDF, True), ("a.PDF", K.PDF, True), ("a.docx", K.ooxml("docx"), True), ("a.docm", K.ooxml("docx", macros=True), True),
    ("a.jpeg", K.JPG, True), ("a.tif", K.TIFF, True), ("a.htm", b"<html></html>", True), ("a.txt", b"text", True),
    ("письмо.eml", K.EML, True),
    ("a.pdf", K.ooxml("docx"), False), ("a.docx", K.PDF, False), ("a.jpg", K.PNG, False), ("a.txt", K.PDF, False),
    ("a.pdf", b"just text", False), ("a.xlsx", K.ooxml("docx"), False)])
def test_несоответствие_расширения_содержимому(tmp_path, name, data, ok):
    assert kind(tmp_path, name, data).ext_ok is ok


def test_старый_формат_office(tmp_path):
    pytest.importorskip("olefile")
    k = kind(tmp_path, "a.doc", K.OLE)
    assert k.family in ("document", "other")      # заготовка без потоков: главное — не исполняемое и не архив


# ── по итогам разбора настоящего ящика ──────────────────────────
def test_служебный_файл_macos_не_документ_и_не_программа(tmp_path):
    for name in ("._письмо.eml", "._отчёт.pdf", "._run.bat"):
        k = kind(tmp_path, name, K.APPLEDOUBLE)
        assert (k.family, k.type) == ("other", "macos-sidecar")


def test_письмо_exchange_с_длинными_заголовками_received(tmp_path):
    data = K.eml_exchange()
    assert len(data) > 3 * 4096
    assert kind(tmp_path, "письмо.eml", data).type == "eml"
    assert kind(tmp_path, "без расширения", data).type == "eml"


@pytest.mark.parametrize("text", [
    "Note: this is a memo\nTitle: budget\n\nBody text here.",
    "Тема: отчёт за март\nКому: всем\n\nТекст заметки.",
    "From time to time: we meet\n\nBody",
    "key: value\nother: thing\nthird: x\n"])
def test_текст_с_двоеточиями_не_письмо(tmp_path, text):
    assert kind(tmp_path, "заметка.txt", text.encode("utf-8")).family == "text"


def test_документ_iwork_не_архив(tmp_path):
    k = kind(tmp_path, "план.pages", K.iwork())
    assert (k.family, k.type) == ("other", "iwork")


def test_письмо_с_длинным_списком_получателей(tmp_path):
    data = K.eml_many_recipients()
    assert data.index(b"Subject:") > 4096                      # тема и Message-ID дальше первого куска
    assert kind(tmp_path, "письмо.eml", data).type == "eml"
    assert kind(tmp_path, "без расширения", data).type == "eml"


def test_текст_из_одних_строк_с_двоеточиями_без_конца_не_письмо(tmp_path):
    data = ("from: склад" + NL + "to: магазин" + NL) + "".join(f"item{i}: {i}" + NL for i in range(900))
    assert len(data.encode()) > 8192
    assert kind(tmp_path, "накладная.txt", data.encode("utf-8")).family == "text"


# ── запускаемое расширение решает, даже когда начало как у zip ──
@pytest.mark.parametrize("name", ["run.bat", "update.cmd", "x.ps1", "y.vbs", "z.js", "w.hta", "s.sh", "l.lnk", "a.exe", "b.scr"])
@pytest.mark.parametrize("data", [b"PK\x03\x04@echo off\r\ndel /q *\r\n", K.zip_bytes({"a.txt": b"x"}), K.ooxml("docx"),
                                  b"PK\x05\x06" + b"\x00" * 18, b"PK"], ids=["обрывок", "zip", "docx", "пустой-zip", "две-буквы"])
def test_запускаемое_расширение_решает_даже_когда_содержимое_начинается_как_zip(tmp_path, name, data):
    """Windows запустит такой .bat: первая строка — мусор, дальше команды."""
    k = kind(tmp_path, name, data)
    assert (k.family, k.type) == ("executable", "by-extension")


@pytest.mark.parametrize("name, data, type_", [("a.jar", K.jar(), "jar"), ("a.apk", K.apk(), "apk"),
                                               ("a.jar", K.zip_bytes({"a.txt": b"x"}), "by-extension")])
def test_программа_в_zip_называется_своим_именем(tmp_path, name, data, type_):
    k = kind(tmp_path, name, data)
    assert (k.family, k.type) == ("executable", type_)


def test_тип_zip_определяется_без_распаковки_первого_члена_целиком(tmp_path):
    import io
    import tracemalloc
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", b"application/vnd.oasis.opendocument.text" + b" " * (40 * 1024 * 1024))
        z.writestr("content.xml", "<office:document-content/>")
    p = tmp_path / "a.odt"
    p.write_bytes(buf.getvalue())
    assert p.stat().st_size < 200_000                               # сорок мегабайт пробелов сжаты в десятки килобайт
    tracemalloc.start()
    try:
        k = F.detect(str(p))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert (k.family, k.type) == ("document", "odt") and peak < 4 * 1024 * 1024

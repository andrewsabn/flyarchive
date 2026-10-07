"""Рабочий процесс просмотра (FR-76): что он делает с файлом. Здесь он вызывается в этом же процессе на маленьких образцах;
запуск в настоящем bwrap — в test_preview_sandbox.py, пределы процесса — в конце этого файла (отдельные процессы).

Образцы «больших» файлов — маленькие файлы с большим заявленным размером: ничего, что занимает гигабайты, не создаётся.
"""
import json
import os
import shutil
import struct
import subprocess
import sys

import pytest

import gatekit as K
import preview as P
import preview_worker as W

TOOLS = os.path.dirname(os.path.abspath(W.__file__))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def libs():
    pytest.importorskip("pymupdf")
    pytest.importorskip("PIL")


def run(tmp, data, name="а.bin", page=None):
    """Запуск рабочего процесса на файле: (ответ, выходной каталог). Расширение берётся из имени, как делает родитель."""
    src = tmp / "data"
    src.write_bytes(data)
    out = tmp / "out"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    ext = ext if ext.isalnum() and ext.isascii() and len(ext) <= 10 else ""
    result = W.describe(str(src), str(out), ext) if page is None else W.render(str(src), str(out), ext, page)
    return result, out


def dims(out):
    data = (out / "page.png").read_bytes()
    assert data[:8] == PNG_SIGNATURE and data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_пределы_обоих_процессов_совпадают():
    for name in ("TEXT_LIMIT", "MAX_PAGES", "MAX_PAGE_PIXELS", "MAX_PAGE_SIDE", "MAX_IMAGE_SIDE", "IMAGE_MEGAPIXELS"):
        assert getattr(W, name) == getattr(P, name), name
    assert (P.TEXT_LIMIT, P.MAX_PAGES, P.MAX_PAGE_PIXELS, P.MAX_IMAGE_SIDE) == (20_000, 20, 4_000_000, 2000)
    assert (W.LIMIT_AS, W.LIMIT_CPU, W.LIMIT_FSIZE) == (2 * 1024 ** 3, 60, 200 * 1024 ** 2)


# ── текст ───────────────────────────────────────────────────────
@pytest.mark.parametrize("letters,truncated", [(19_999, False), (20_000, False), (20_001, True), (45_000, True)])
@pytest.mark.parametrize("alphabet", ["a", "ж"])
def test_текст_первые_20000_знаков_и_признак_обрезки(tmp_path, letters, truncated, alphabet):
    text = (alphabet * letters)
    result, _ = run(tmp_path, text.encode("utf-8"), "заметка.txt")
    assert result == {"kind": "text", "type": "txt", "text": text[:20_000], "truncated": truncated}
    assert len(result["text"]) == min(letters, 20_000)


def test_большой_текст_читается_только_с_начала(tmp_path):
    big = ("строка номер один\n" * 200_000).encode("utf-8")           # 3,4 МБ
    result, _ = run(tmp_path, big, "журнал.log")
    assert result["kind"] == "text" and result["type"] == "log" and result["truncated"] is True
    assert result["text"] == big.decode("utf-8")[:20_000]


def test_знак_на_границе_чтения_не_портит_текст(tmp_path):
    result, _ = run(tmp_path, ("😀" * 25_000).encode("utf-8"), "смайлы.txt")             # по четыре байта на знак
    assert result["text"] == "😀" * 20_000 and result["truncated"] is True


@pytest.mark.parametrize("encoded", [
    "Привет, мир. Это письмо о договоре.".encode("utf-8-sig"),
    "Привет, мир. Это письмо о договоре.".encode("cp1251"),
    "Привет, мир. Это письмо о договоре.".encode("utf-16"),
    b"\xfe\xff" + "Привет, мир. Это письмо о договоре.".encode("utf-16-be"),
])
def test_текст_в_разных_кодировках(tmp_path, encoded):
    result, _ = run(tmp_path, encoded, "письмо.txt")
    assert result["kind"] == "text" and result["text"] == "Привет, мир. Это письмо о договоре." and result["truncated"] is False


def test_управляющие_знаки_в_тексте_заменены_а_переводы_строк_целы(tmp_path):
    body = ("строка текста\r\n\tотступ\n" * 300) + "а\x01б\x1bв\x00г"          # управляющие знаки — за первыми 4096 байтами, которые читает filetype_sniff
    result, _ = run(tmp_path, body.encode("utf-8"), "а.txt")
    assert result["kind"] == "text"
    assert "\x01" not in result["text"] and "\x1b" not in result["text"] and "\x00" not in result["text"]
    assert "\ufffd" in result["text"] and "\r\n\tотступ\n" in result["text"]


@pytest.mark.parametrize("name,type_", [("а.md", "md"), ("а.json", "json"), ("а.xml", "xml"), ("а.sql", "sql"), ("а.yaml", "yaml"),
                                         ("а.yml", "yml"), ("а.log", "log"), ("а.ini", "ini"), ("а.tsv", "tsv"), ("а.py", "txt"),
                                         ("а.ics", "txt"), ("без-расширения", "txt"), ("а.txt", "txt")])
def test_типы_текста(tmp_path, name, type_):
    result, _ = run(tmp_path, "первая строка\nвторая строка\n".encode("utf-8"), name)
    assert result["kind"] == "text" and result["type"] == type_ and result["text"] == "первая строка\nвторая строка\n"


def test_svg_без_разметки_svg_показывается_как_текст(tmp_path):
    result, _ = run(tmp_path, "просто слова в файле с чужим расширением".encode("utf-8"), "рисунок.svg")
    assert result["kind"] == "text" and result["type"] == "svg"


# ── только сведения и «нужен преобразователь» ───────────────────
@pytest.mark.parametrize("name,data,type_", [
    ("а.bin", K.PE, "pe"), ("а.bin", K.ELF, "elf"), ("а.bin", K.MACHO, "macho-or-class"), ("а.bin", K.LNK, "lnk"),
    ("а.sh", "#!/bin/sh\necho привет\n".encode("utf-8"), "by-extension"), ("а.txt", "#!/usr/bin/env python3\nprint(1)\n".encode("utf-8"), "script"),
    ("setup.exe", "это текст, но имя говорит иное".encode("utf-8"), "by-extension"), ("а.bat", b"@echo off\r\n", "by-extension"),
    ("а.jar", K.jar(), "jar"), ("а.apk", K.apk(), "apk"), ("картинка.emf", K.PE, "pe"), ("клип.mp4", K.ELF, "elf")])
def test_программа_только_сведения_содержимое_не_показывается(tmp_path, name, data, type_):
    result, out = run(tmp_path, data, name)
    assert result == {"kind": "none", "type": type_, "reason": "program", "args": {}}
    assert os.listdir(out) == []


@pytest.mark.parametrize("name,data,type_", [
    ("а.bin", bytes(range(256)) * 4, "binary"), ("а.bin", K.APPLEDOUBLE, "macos-sidecar"), ("а.pages", K.iwork(), "iwork"), ("а.ole", K.OLE, "ole")])
def test_неизвестное_не_поддерживается(tmp_path, name, data, type_):
    result, _ = run(tmp_path, data, name)
    assert result == {"kind": "none", "type": type_, "reason": "unsupported", "args": {"type": type_}}


def test_пустой_файл(tmp_path):
    result, _ = run(tmp_path, b"", "пустой.txt")
    assert result == {"kind": "none", "type": "empty", "reason": "empty", "args": {}}


@pytest.mark.parametrize("name,data,type_", [
    ("а.docx", K.ooxml("docx"), "docx"), ("а.xlsx", K.ooxml("xlsx"), "xlsx"), ("а.pptx", K.ooxml("pptx"), "pptx"),
    ("а.vsdx", K.ooxml("vsdx"), "vsdx"), ("а.odt", K.odf(), "odt"), ("а.ods", K.odf("application/vnd.oasis.opendocument.spreadsheet"), "ods"),
    ("а.odp", K.odf("application/vnd.oasis.opendocument.presentation"), "odp"), ("а.rtf", K.RTF, "rtf"),
    ("а.html", b"<!DOCTYPE html><html><body>hi</body></html>", "html"),
    ("а.csv", "а,б\n1,2\n".encode("utf-8"), "csv"), ("схема.bpmn", b"<definitions/>", "bpmn"), ("схема.drawio", b"<mxfile/>", "drawio"),
    ("а.emf", b"\x01\x00\x00\x00" + bytes(60), "emf"), ("а.wmf", b"\xd7\xcd\xc6\x9a" + bytes(60), "wmf"),
    ("а.wmz", b"\x1f\x8b" + bytes(60), "wmz"), ("а.emz", b"\x1f\x8b" + bytes(60), "emz"), ("а.vsd", bytes(range(1, 90)), "vsd"),
    ("а.xlsb", K.zip_bytes({"xl/workbook.bin": b"x"}), "xlsb"), ("а.pptm", K.ooxml("pptx", macros=True), "pptx"),
    ("а.xlsm", K.ooxml("xlsx", macros=True), "xlsx"), ("а.ppsx", K.ooxml("pptx"), "pptx"),
    ("а.mp4", bytes(range(1, 90)), "mp4"), ("а.mov", bytes(range(1, 90)), "mov"), ("а.mkv", bytes(range(1, 90)), "mkv"),
    ("а.mp3", b"ID3" + bytes(range(1, 90)), "mp3")])
def test_типам_с_контейнером_и_медиа_нужен_преобразователь(tmp_path, name, data, type_):
    result, _ = run(tmp_path, data, name)
    assert result == {"kind": "none", "type": type_, "reason": "needs_converter", "args": {"type": type_}}


# ── PDF ─────────────────────────────────────────────────────────
def test_pdf_число_страниц_и_показанные(tmp_path, libs):
    assert run(tmp_path, K.pdf_pages(1), "а.pdf")[0] == {"kind": "pages", "type": "pdf", "pages": 1, "shown": 1}
    assert run(tmp_path, K.pdf_pages(3), "а.pdf")[0] == {"kind": "pages", "type": "pdf", "pages": 3, "shown": 3}
    assert run(tmp_path, K.pdf_pages(20), "а.pdf")[0] == {"kind": "pages", "type": "pdf", "pages": 20, "shown": 20}
    assert run(tmp_path, K.pdf_pages(25), "а.pdf")[0] == {"kind": "pages", "type": "pdf", "pages": 25, "shown": 20}


def test_pdf_определяется_по_содержимому_а_не_по_имени(tmp_path, libs):
    assert run(tmp_path, K.pdf_pages(2), "счёт.docx")[0] == {"kind": "pages", "type": "pdf", "pages": 2, "shown": 2}
    assert run(tmp_path, K.pdf_pages(2), "без-расширения")[0]["kind"] == "pages"


def test_pdf_страница_рисуется_по_номеру_и_выход_только_png(tmp_path, libs):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for width in (100, 200, 300):
        doc.new_page(width=width, height=100)
    data = doc.tobytes()
    for number, width in ((1, 200), (2, 400), (3, 600)):                # масштаб 2: ширина вдвое больше пунктов
        result, out = run(tmp_path, data, "а.pdf", page=number)
        assert result == {"kind": "page", "page": number} and dims(out) == (width, 200)
        assert sorted(os.listdir(out)) == ["page.png"]


@pytest.mark.parametrize("box", [(612, 792), (14_400, 14_400), (5_000, 5_000), (100_000, 100_000), (200, 14_400), (14_400, 200)])
def test_pdf_страница_в_тысячи_пунктов_рисуется_уменьшенной_до_4_мп(tmp_path, libs, box):
    result, out = run(tmp_path, K.pdf_pages(1, box), "большой.pdf", page=1)
    assert result == {"kind": "page", "page": 1}
    w, h = dims(out)
    assert w * h <= 4_000_000 and max(w, h) <= 10_000 and min(w, h) >= 1
    if box == (612, 792):
        assert (w, h) == (1224, 1584)                                  # обычная страница — без потери плотности
    else:
        assert w * h > 3_000_000 or min(w, h) < 400                    # а большая использует отведённое, не сжимается в точку


def test_pdf_страница_с_нелепым_размером_либо_рисуется_в_пределе_либо_отказ(tmp_path, libs):
    result, out = run(tmp_path, K.pdf_pages(1, (1_000_000_000, 10)), "нелепый.pdf", page=1)
    if result["kind"] == "page":
        w, h = dims(out)
        assert w * h <= 4_000_000 and max(w, h) <= 10_000
    else:
        assert result["kind"] == "none" and result["reason"] in ("broken", "too_large")


def test_pdf_страницы_за_20_не_рисуются(tmp_path, libs):
    data = K.pdf_pages(25)
    assert run(tmp_path, data, "а.pdf", page=20)[0] == {"kind": "page", "page": 20}
    for number in (21, 25, 0, -1):
        result, out = run(tmp_path, data, "а.pdf", page=number)
        assert result["kind"] == "none" and result["reason"] == "broken" and os.listdir(out) == [], number


def test_pdf_с_паролем_отказ_закрыт(tmp_path, libs):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="секрет", owner_pw="хозяин")
    result, out = run(tmp_path, data, "закрытый.pdf")
    assert result == {"kind": "none", "type": "pdf", "reason": "encrypted", "args": {}}
    assert run(tmp_path, data, "закрытый.pdf", page=1)[0]["kind"] == "none" and "page.png" not in os.listdir(out)


def test_pdf_испорченный_не_роняет_процесс(tmp_path, libs):
    for data in (b"%PDF-1.4\n" + bytes(range(256)) * 4, b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n", K.pdf_pages(2)[:150]):
        result, _ = run(tmp_path, data, "поломанный.pdf")
        assert result["kind"] == "none" and result["reason"] == "broken" and result["type"] == "pdf"
        assert result["args"]["error_type"].isidentifier()


# ── SVG ─────────────────────────────────────────────────────────
def test_svg_это_одна_страница(tmp_path, libs):
    assert run(tmp_path, K.svg_bytes(), "схема.svg")[0] == {"kind": "pages", "type": "svg", "pages": 1, "shown": 1}
    result, out = run(tmp_path, K.svg_bytes(200, 100), "схема.svg", page=1)
    assert result == {"kind": "page", "page": 1} and dims(out) == (400, 200)


def test_svg_со_сценарием_наружу_только_png_разметки_нет(tmp_path, libs):
    data = K.svg_bytes(script=True, text="видимая подпись")
    described, _ = run(tmp_path, data, "вредный.svg")
    rendered, out = run(tmp_path, data, "вредный.svg", page=1)
    assert described == {"kind": "pages", "type": "svg", "pages": 1, "shown": 1} and rendered == {"kind": "page", "page": 1}
    assert os.listdir(out) == ["page.png"]
    png = (out / "page.png").read_bytes()
    assert png[:8] == PNG_SIGNATURE and b"<script" not in png and b"alert" not in png and b"<svg" not in png
    for answer in (described, rendered):
        text = json.dumps(answer)
        assert "<" not in text and "script" not in text and "alert" not in text


def test_svg_с_нелепым_размером_уменьшен_до_4_мп(tmp_path, libs):
    result, out = run(tmp_path, K.svg_bytes(1_000_000, 1_000_000), "огромный.svg", page=1)
    assert result == {"kind": "page", "page": 1}
    w, h = dims(out)
    assert w * h <= 4_000_000 and max(w, h) <= 10_000


def test_svg_негодный_не_роняет_процесс(tmp_path, libs):
    result, _ = run(tmp_path, b"<svg xmlns='http://www.w3.org/2000/svg'><rect", "поломанный.svg")
    assert result["kind"] in ("none", "pages")                          # то, что разбор исправил, рисуется; что нет — отказ без падения
    if result["kind"] == "none":
        assert result["reason"] == "broken"


# ── картинки ────────────────────────────────────────────────────
@pytest.mark.parametrize("fmt,name,type_", [("PNG", "а.png", "png"), ("JPEG", "а.jpg", "jpg"), ("GIF", "а.gif", "gif"), ("BMP", "а.bmp", "bmp"),
                                             ("TIFF", "а.tif", "tiff"), ("WEBP", "а.webp", "webp")])
def test_картинка_любого_формата_перекодируется_в_png(tmp_path, libs, fmt, name, type_):
    from PIL import features
    if fmt == "WEBP" and not features.check("webp"):
        pytest.skip("в этом Pillow нет WEBP")
    result, out = run(tmp_path, K.image_bytes(fmt, (40, 30)), name)
    assert result == {"kind": "image", "type": type_}
    assert dims(out) == (40, 30) and sorted(os.listdir(out)) == ["page.png"]


def test_картинка_определяется_по_содержимому_а_не_по_имени(tmp_path, libs):
    assert run(tmp_path, K.image_bytes("GIF"), "фото.txt")[0] == {"kind": "image", "type": "gif"}
    assert run(tmp_path, K.image_bytes("PNG"), "фото")[0] == {"kind": "image", "type": "png"}


@pytest.mark.parametrize("size,expected", [((3000, 1000), (2000, 667)), ((1000, 3000), (667, 2000)), ((2000, 2000), (2000, 2000)),
                                           ((2001, 10), (2000, 10)), ((1999, 1999), (1999, 1999)), ((100, 4000), (50, 2000))])
def test_картинка_не_больше_2000_точек_по_длинной_стороне(tmp_path, libs, size, expected):
    result, out = run(tmp_path, K.image_bytes("PNG", size), "большая.png")
    assert result["kind"] == "image"
    w, h = dims(out)
    assert max(w, h) <= 2000 and (w, h) == expected


def test_картинка_с_заявленным_размером_в_сотни_мегапикселей_отказ_без_чтения_точек(tmp_path, libs):
    data = K.png_declared(16_000, 16_000)                                # 256 Мп по заявке, файл — десятки килобайт
    assert len(data) < 200_000
    result, out = run(tmp_path, data, "бомба.png")
    assert result == {"kind": "none", "type": "png", "reason": "too_large", "args": {"megapixels": 256, "limit": 100}}
    assert os.listdir(out) == []


def test_предел_заявленного_размера_точен(tmp_path, libs, monkeypatch):
    monkeypatch.setattr(W, "IMAGE_MEGAPIXELS", 1)
    assert run(tmp_path, K.png_declared(1000, 1000), "а.png")[0] == {"kind": "image", "type": "png"}
    result, _ = run(tmp_path, K.png_declared(1001, 1000), "а.png")
    assert result["kind"] == "none" and result["reason"] == "too_large" and result["args"] == {"megapixels": 2, "limit": 1}   # заявка округляется вверх


def test_картинка_испорченная_отказ_без_падения(tmp_path, libs):
    for data in (K.png_image(50, 50)[:-40], K.png_image(50, 50)[:40], K.PNG, K.JPG, K.image_bytes("JPEG")[:200]):
        result, out = run(tmp_path, data, "поломанная.png")
        assert result["kind"] == "none" and result["reason"] == "broken" and os.listdir(out) == [], data[:12]
        assert result["args"]["error_type"].isidentifier()


@pytest.mark.parametrize("mode,fmt", [("P", "PNG"), ("L", "PNG"), ("LA", "PNG"), ("RGBA", "PNG"), ("1", "PNG"), ("I;16", "PNG"),
                                       ("CMYK", "JPEG"), ("L", "JPEG"), ("P", "GIF")])
def test_режимы_пикселей_приводятся_к_тем_что_умеет_png(tmp_path, libs, mode, fmt):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new(mode, (30, 20)).save(buf, fmt)
    result, out = run(tmp_path, buf.getvalue(), "а." + fmt.lower())
    assert result["kind"] == "image" and dims(out) == (30, 20)


def test_поворот_из_exif_применяется(tmp_path, libs):
    import io
    from PIL import Image
    exif = Image.Exif()
    exif[0x0112] = 6                                                       # повернуть на 90°
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (1, 2, 3)).save(buf, "JPEG", exif=exif)
    result, out = run(tmp_path, buf.getvalue(), "поворот.jpg")
    assert result["kind"] == "image" and dims(out) == (30, 40)


def test_анимированная_gif_даёт_первый_кадр(tmp_path, libs):
    import io
    from PIL import Image
    buf = io.BytesIO()
    first, second = Image.new("RGB", (20, 10), (255, 0, 0)), Image.new("RGB", (20, 10), (0, 0, 255))
    first.save(buf, "GIF", save_all=True, append_images=[second], duration=100)
    result, out = run(tmp_path, buf.getvalue(), "анимация.gif")
    assert result == {"kind": "image", "type": "gif"} and dims(out) == (20, 10)


def test_страница_картинки_это_её_png(tmp_path, libs):
    result, out = run(tmp_path, K.image_bytes("JPEG", (50, 40)), "а.jpg", page=1)
    assert result == {"kind": "page", "page": 1} and dims(out) == (50, 40)
    result, out = run(tmp_path, K.image_bytes("JPEG", (50, 40)), "а.jpg", page=2)
    assert result["kind"] == "none" and os.listdir(out) == []


# ── точка входа ─────────────────────────────────────────────────
def test_main_пишет_result_json_и_возвращает_нуль(tmp_path, libs):
    src, out = tmp_path / "data", tmp_path / "out"
    src.write_bytes(K.pdf_pages(2))
    out.mkdir()
    assert W.main(["describe", str(src), str(out), "pdf"]) == 0
    assert json.loads((out / "result.json").read_text(encoding="utf-8")) == {"kind": "pages", "type": "pdf", "pages": 2, "shown": 2}
    assert W.main(["render", str(src), str(out), "pdf", "2"]) == 0
    assert json.loads((out / "result.json").read_text(encoding="utf-8")) == {"kind": "page", "page": 2}
    assert (out / "page.png").read_bytes()[:8] == PNG_SIGNATURE


def test_main_сбой_внутри_записан_данными_а_не_исключением(tmp_path, monkeypatch):
    src, out = tmp_path / "data", tmp_path / "out"
    src.write_bytes(b"x")
    out.mkdir()
    assert W.main(["describe", str(tmp_path / "нет-такого"), str(out), "txt"]) == 0
    answer = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert answer["kind"] == "none" and answer["reason"] == "broken" and answer["args"] == {"error_type": "FileNotFoundError"}

    def eat(*a, **k):
        raise MemoryError

    monkeypatch.setattr(W, "describe", eat)
    assert W.main(["describe", str(src), str(out), "txt"]) == 0
    assert json.loads((out / "result.json").read_text(encoding="utf-8")) == {"kind": "none", "type": "unknown", "reason": "memory", "args": {}}


def test_main_негодные_аргументы_код_2_и_ничего_не_записано(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    src = tmp_path / "data"
    src.write_bytes(b"x")
    for argv in ([], ["describe"], ["fly", str(src), str(out), "x"], ["render", str(src), str(out), "x"],
                 ["render", str(src), str(out), "x", "много"], ["describe", str(src), str(out), "x", "лишний", "ещё"]):
        assert W.main(argv) == 2, argv
    assert os.listdir(out) == []


# ── пределы процесса: в отдельных процессах, потому что ставятся на весь процесс ──
def probe_modules(tmp_path):
    """Подставные pymupdf и PIL: при импорте записывают пределы, действовавшие в этот момент, и отказываются грузиться."""
    fake = tmp_path / "fake"
    (fake / "PIL").mkdir(parents=True)
    body = ("import json, os, resource\n"
            "with open(os.environ['PROBE'], 'a') as f:\n"
            "    f.write(json.dumps({'lib': __name__, 'as': resource.getrlimit(resource.RLIMIT_AS), 'cpu': resource.getrlimit(resource.RLIMIT_CPU),\n"
            "                        'fsize': resource.getrlimit(resource.RLIMIT_FSIZE)}) + '\\n')\n"
            "raise ImportError('подставная библиотека')\n")
    (fake / "pymupdf.py").write_text(body, encoding="utf-8")
    (fake / "PIL" / "__init__.py").write_text(body, encoding="utf-8")
    return fake


@pytest.mark.parametrize("sample,name", [(K.pdf_pages(1), "а.pdf"), (K.png_image(4, 4), "а.png")])
def test_пределы_ставятся_до_импорта_библиотек(tmp_path, sample, name):
    fake, probe = probe_modules(tmp_path), tmp_path / "probe.jsonl"
    src, out = tmp_path / "data", tmp_path / "out"
    src.write_bytes(sample)
    out.mkdir()
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "preview_worker.py"), "describe", str(src), str(out), name.split(".")[1]],
                       env={**os.environ, "PYTHONPATH": str(fake), "PROBE": str(probe)}, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    (seen,) = [json.loads(line) for line in probe.read_text(encoding="utf-8").splitlines()]
    assert seen["lib"] in ("pymupdf", "PIL")                              # библиотека действительно была загружена рабочим процессом
    assert seen["as"] == [2 * 1024 ** 3] * 2 and seen["cpu"] == [60, 60] and seen["fsize"] == [200 * 1024 ** 2] * 2
    answer = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert answer["kind"] == "none" and answer["reason"] == "broken" and answer["args"] == {"error_type": "ImportError"}


def test_предел_памяти_действует_и_не_поднимается(tmp_path):
    script = tmp_path / "limits.py"
    script.write_text(
        "import json, mmap, resource, sys\n"
        f"sys.path.insert(0, {TOOLS!r})\n"
        "import preview_worker as W\n"
        "W.apply_limits()\n"
        "try:\n"
        "    mmap.mmap(-1, 3 * 2 ** 30)\n"                                  # адресное пространство, а не настоящая память: не занято, пока не тронуто
        "    taken = 'выделено'\n"
        "except (OSError, MemoryError, ValueError):\n"
        "    taken = 'отказ'\n"
        "try:\n"
        "    resource.setrlimit(resource.RLIMIT_AS, (8 * 2 ** 30, 8 * 2 ** 30))\n"
        "    raised = True\n"
        "except (ValueError, OSError):\n"
        "    raised = False\n"
        "print(json.dumps({'taken': taken, 'raised': raised, 'core': resource.getrlimit(resource.RLIMIT_CORE)}))\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"taken": "отказ", "raised": False, "core": [0, 0]}


def peak_of(tmp_path, data, name, page=None):
    """Пиковая память (КБ) процесса, который выполнил рабочий процесс на этом файле, и его ответ.

    Пик берётся из VmHWM, а не из ru_maxrss: ru_maxrss дочернего процесса в Linux не меньше памяти родителя в момент запуска,
    и в полном прогоне, где сам pytest занимает сотни мегабайт, он показывал память pytest, а не рабочего процесса."""
    src, out = tmp_path / "data", tmp_path / "out"
    src.write_bytes(data)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    script = tmp_path / "peak.py"
    script.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {TOOLS!r})\n"
        "import preview_worker as W\n"
        f"W.main({['describe' if page is None else 'render', str(src), str(out), name.split('.')[1], *([] if page is None else [str(page)])]!r})\n"
        "print(next(int(line.split()[1]) for line in open('/proc/self/status') if line.startswith('VmHWM:')))\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip().splitlines()[-1]), json.loads((out / "result.json").read_text(encoding="utf-8"))


def test_заявленные_сотни_мегапикселей_и_тысячи_пунктов_память_остаётся_малой(tmp_path, libs):
    peak, answer = peak_of(tmp_path, K.png_declared(16_000, 16_000), "бомба.png")
    assert answer["reason"] == "too_large" and peak < 400_000, peak                    # КБ: меньше 400 МБ, а не гигабайты
    peak, answer = peak_of(tmp_path, K.pdf_pages(1, (100_000, 100_000)), "огромная.pdf", page=1)
    assert answer == {"kind": "page", "page": 1} and peak < 600_000, peak
    peak, answer = peak_of(tmp_path, K.svg_bytes(1_000_000, 1_000_000), "огромный.svg", page=1)
    assert answer == {"kind": "page", "page": 1} and peak < 600_000, peak


def run_limited(tmp_path, body):
    """Отдельный процесс, в котором предел поставлен настоящим apply_limits() с малым значением; body — что он делает потом."""
    script = tmp_path / "limited.py"
    script.write_text("import json, os, sys\n" + f"sys.path.insert(0, {TOOLS!r})\n" + "import preview_worker as W\n" + body, encoding="utf-8")
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)


def test_предел_процессорного_времени_останавливает_вечный_цикл(tmp_path):
    import signal
    r = run_limited(tmp_path, "W.LIMIT_CPU = 1\nW.apply_limits()\nwhile True:\n    pass\n")
    assert r.returncode in (-signal.SIGXCPU, -signal.SIGKILL), r


def test_предел_размера_файла_не_даёт_записать_больше(tmp_path):
    target = tmp_path / "большой"
    r = run_limited(tmp_path, "W.LIMIT_FSIZE = 1 << 20\nW.apply_limits()\n"
                              f"try:\n    open({str(target)!r}, 'wb').write(b'x' * (3 << 20))\n    print('записано')\n"
                              "except OSError as e:\n    print(e.errno)\n")
    assert r.returncode == 0 and r.stdout.strip() == "27" and target.stat().st_size <= 1 << 20, r          # 27 — EFBIG: файл слишком велик

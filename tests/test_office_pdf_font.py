"""PDF с русским текстом: сервер документов собирает его шрифтом с кириллицей, а не стандартными шрифтами reportlab.

Стандартные шрифты reportlab кириллицы не содержат: русский заголовок, абзац и таблица выходили в PDF квадратами, и ни ошибки, ни предупреждения.
Теперь `make_document` с видом pdf ищет шрифт с кириллицей (DejaVu Sans, Liberation Sans, Noto Sans, FreeSans в обычных каталогах шрифтов; свой файл .ttf —
необязательная настройка `pdf_font`), регистрирует его и собирает им заголовки, текст, списки и таблицы. Шрифта нет, а в тексте есть знаки вне латиницы —
отказ 503 с кодом `doctor.font_missing` и названием пакета, как у отсутствующей библиотеки; чисто латинский текст без шрифта собирается как раньше.
`flyarchive doctor` называет шрифт строкой «по желанию».

Каталоги шрифтов в тестах отказа подменены пустым перечнем: на машине с настоящими шрифтами «шрифта нет» иначе не изобразить. Тест настоящего PDF
пропускается с названной причиной, если нет reportlab, pymupdf или шрифта.
"""
import json
import os
import shutil

import pytest

import doctor as D
import messages as M
import office_server as O
from test_office_missing import box, http  # noqa: F401 — серверы и каталоги из соседнего теста

TITLE = "Отчёт о ходе работ"
BLOCKS = [{"type": "heading", "value": "Итоги месяца"},
          {"type": "text", "value": "Работы по лодочному приложению идут по плану."},
          {"type": "bullets", "value": ["Записи прогулок сохраняются", "Карта маршрута готова"]},
          {"type": "table", "value": [["Этап", "Срок"], ["Дизайн", "май"], ["Проверка", "июнь"]]}]
LATIN = [{"type": "text", "value": "Plain Latin text, café included."}, {"type": "bullets", "value": ["one", "two"]},
         {"type": "table", "value": [["Stage", "Due"], ["Design", "May"]]}]
DOCUMENT = "/document"


def pdf_text(path):
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open(path) as pdf:
        return " ".join("".join(page.get_text() for page in pdf).split())


def system_font():
    found = D.find_font("")
    if found is None:
        pytest.skip("на этой машине нет шрифта с кириллицей (DejaVu Sans, Liberation Sans, Noto Sans, FreeSans): настоящий PDF собрать нечем")
    return found


def no_places(monkeypatch):
    """Каталогов со шрифтами нет, свой файл не задан: шрифта нет нигде."""
    monkeypatch.setattr(D, "FONT_DIRS", ())
    monkeypatch.setattr(O, "PDF_FONT", "")


def build(http, title, blocks):
    return http("POST", DOCUMENT, {"kind": "pdf", "title": title, "blocks": blocks})


# ── русский текст читается обратно ──────────────────────────────
def test_pdf_с_русским_заголовком_абзацем_списком_и_таблицей_читается_обратно(http, box):
    pytest.importorskip("reportlab")
    system_font()
    status, _, body = build(http, TITLE, BLOCKS)
    answer = json.loads(body)
    assert status == 200 and answer["size"] > 0, answer
    text = pdf_text(answer["file"])
    for line in (TITLE, "Итоги месяца", "Работы по лодочному приложению идут по плану.", "Записи прогулок сохраняются", "Карта маршрута готова",
                 "Этап", "Срок", "Дизайн", "май", "Проверка", "июнь"):
        assert line in text, (line, text)


def test_в_pdf_встроен_шрифт_с_кириллицей_а_не_только_стандартный(http, box):
    """Стандартный Helvetica в PDF числится всегда (шрифт страницы по умолчанию) и не встроен; встроенный шрифт — подмножество с меткой «AAAAAA+»."""
    pytest.importorskip("reportlab")
    system_font()
    answer = json.loads(build(http, TITLE, BLOCKS)[2])
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open(answer["file"]) as pdf:
        fonts = {font[3] for page in pdf for font in page.get_fonts()}
    assert any("+" in name for name in fonts), fonts


def test_шрифт_из_настройки_pdf_font_берётся_а_не_найденный_в_каталогах(http, box, monkeypatch, tmp_path):
    pytest.importorskip("reportlab")
    mine = tmp_path / "мой-шрифт.ttf"
    shutil.copyfile(system_font().regular, mine)
    no_places(monkeypatch)
    monkeypatch.setattr(O, "PDF_FONT", str(mine))
    answer = json.loads(build(http, TITLE, BLOCKS)[2])
    assert "Отчёт о ходе работ" in pdf_text(answer["file"]) and "Дизайн" in pdf_text(answer["file"])


def test_pdf_font_которого_нет_не_роняет_служба_идёт_по_обычным_местам_а_нет_и_там_отказ(http, box, monkeypatch, tmp_path):
    pytest.importorskip("reportlab")
    monkeypatch.setattr(O, "PDF_FONT", str(tmp_path / "нет-такого.ttf"))
    if D.find_font(str(tmp_path / "нет-такого.ttf")) is not None:
        assert build(http, TITLE, BLOCKS)[0] == 200
    monkeypatch.setattr(D, "FONT_DIRS", ())
    status, _, body = build(http, TITLE, BLOCKS)
    assert status == 503 and json.loads(body)["code"] == "doctor.font_missing"


# ── шрифта нет ──────────────────────────────────────────────────
def test_без_шрифта_русский_текст_отказ_503_с_кодом_и_названием_пакета(http, box, monkeypatch, capsys):
    pytest.importorskip("reportlab")
    no_places(monkeypatch)
    status, headers, body = build(http, TITLE, BLOCKS)
    text = body.decode("utf-8")
    assert status == 503 and headers["Content-Type"].startswith("application/json"), text
    answer = json.loads(text)
    assert set(answer) == {"error", "code"} and answer["code"] == "doctor.font_missing"
    assert answer["error"] == str(M.make("doctor.font_missing", package="fonts-dejavu-core"))
    assert "fonts-dejavu-core" in answer["error"] and "pdf_font" in answer["error"] and "Traceback" not in text
    assert capsys.readouterr().err.count("Traceback") == 0
    assert os.listdir(box.out) == [], "отказ оставил недособранный файл"
    status, _, body = http("GET", "/")
    assert status == 200 and "инструменты" in json.loads(body)


@pytest.mark.parametrize("where", ["title", "heading", "text", "bullets", "table"])
def test_знак_вне_латиницы_в_любом_месте_документа_требует_шрифт(http, box, monkeypatch, where):
    pytest.importorskip("reportlab")
    no_places(monkeypatch)
    title = "Report"
    blocks = [{"type": "text", "value": "plain"}]
    if where == "title":
        title = "Отчёт"
    elif where == "heading":
        blocks = [{"type": "heading", "value": "Итоги"}]
    elif where == "text":
        blocks = [{"type": "text", "value": "Привет"}]
    elif where == "bullets":
        blocks = [{"type": "bullets", "value": ["one", "два"]}]
    else:
        blocks = [{"type": "table", "value": [["a", "b"], ["c", "д"]]}]
    assert build(http, title, blocks)[0] == 503


def test_чисто_латинский_текст_без_шрифта_собирается_как_раньше(http, box, monkeypatch):
    pytest.importorskip("reportlab")
    no_places(monkeypatch)
    status, _, body = build(http, "Report", LATIN)
    answer = json.loads(body)
    assert status == 200 and answer["size"] > 0, answer
    assert "Plain Latin text, café included." in pdf_text(answer["file"])


def test_отказ_без_шрифта_не_мешает_другим_видам_документа(http, box, monkeypatch):
    pytest.importorskip("docx")
    no_places(monkeypatch)
    status, _, body = http("POST", DOCUMENT, {"kind": "docx", "title": TITLE, "blocks": BLOCKS})
    assert status == 200 and json.loads(body)["size"] > 0


# ── проверка окружения ──────────────────────────────────────────
def test_doctor_называет_найденный_шрифт_и_ничего_не_требует(monkeypatch):
    found = system_font()
    c = D.check_font({"pdf_font": ""})
    assert (c.id, c.status) == ("font", "ok") and c.message.code == "doctor.font_ok" and c.message.args == {"path": found.regular}


def test_doctor_без_шрифта_строка_по_желанию_с_названием_пакета(monkeypatch):
    monkeypatch.setattr(D, "FONT_DIRS", ())
    c = D.check_font({"pdf_font": ""})
    assert (c.id, c.status) == ("font", "optional_missing")
    assert (c.message.code, c.message.args) == ("doctor.font_missing", {"package": "fonts-dejavu-core"})
    line = [x for x in D.lines([c]) if "шрифт" in x][0]
    assert line.lstrip().startswith("по желанию") and "fonts-dejavu-core" in line


def test_doctor_шрифт_из_настройки_называется_первым(monkeypatch, tmp_path):
    mine = tmp_path / "мой.ttf"
    mine.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 64)
    monkeypatch.setattr(D, "FONT_DIRS", ())
    c = D.check_font({"pdf_font": str(mine)})
    assert c.status == "ok" and c.message.args == {"path": str(mine)}


def test_шрифт_в_общей_проверке_идёт_после_программ_и_не_делает_итог_обязательным(monkeypatch):
    monkeypatch.setattr(D, "FONT_DIRS", ())
    checks = D.collect({"home": "/нет/такого", "embed_url": "http://127.0.0.1:9/api/embed", "embed_model": "m", "embed_dim": 4, "embed_gpu": False,
                        "llm_local_model": "", "pdf_font": ""}, timeout=1)
    ids = [c.id for c in checks]
    assert "font" in ids and ids.index("font") > max(i for i, name in enumerate(ids) if name.startswith("tool.")) and "font" not in [
        c.id for c in D.missing(checks)]


def test_поиск_шрифта_ищет_по_именам_файлов_в_перечисленных_каталогах(monkeypatch, tmp_path):
    folder = tmp_path / "шрифты" / "truetype" / "dejavu"
    folder.mkdir(parents=True)
    (folder / "DejaVuSans.ttf").write_bytes(b"x")
    (folder / "DejaVuSans-Bold.ttf").write_bytes(b"y")
    (tmp_path / "шрифты" / "ShowCard.ttf").write_bytes(b"z")                # шрифт без кириллицы в перечень не входит
    monkeypatch.setattr(D, "FONT_DIRS", (str(tmp_path / "шрифты"),))
    found = D.find_font("")
    assert found.regular == str(folder / "DejaVuSans.ttf") and found.bold == str(folder / "DejaVuSans-Bold.ttf")
    monkeypatch.setattr(D, "FONT_DIRS", (str(tmp_path / "пусто"),))
    assert D.find_font("") is None


def test_без_жирного_начертания_шрифт_всё_равно_найден(monkeypatch, tmp_path):
    (tmp_path / "FreeSans.ttf").write_bytes(b"x")
    monkeypatch.setattr(D, "FONT_DIRS", (str(tmp_path),))
    found = D.find_font("")
    assert found.regular == str(tmp_path / "FreeSans.ttf") and found.bold is None


# ── сообщения ───────────────────────────────────────────────────
def test_сообщения_о_шрифте_в_каталоге_и_называют_пакет_и_настройку():
    assert str(M.make("doctor.font_ok", path="/p/DejaVuSans.ttf")) == "шрифт с кириллицей для pdf: /p/DejaVuSans.ttf"
    text = str(M.make("doctor.font_missing", package="fonts-dejavu-core"))
    assert "fonts-dejavu-core" in text and "sudo apt install fonts-dejavu-core" in text and "pdf_font" in text

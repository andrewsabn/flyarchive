"""Фиксация нынешнего поведения сервера документов (office_server.py)."""
import json
import os
import shutil
import time
import urllib.parse

import pytest

import office_server as O


@pytest.fixture
def box(tmp_path, monkeypatch):
    """Подменяет корпус и папку результатов временными каталогами."""
    corpus, out = tmp_path / "corpus", tmp_path / "out"
    (corpus / "jira" / "IT").mkdir(parents=True)
    out.mkdir()
    (corpus / "jira" / "IT" / "заметка.txt").write_text("  Привет, архив  ", encoding="utf-8")
    (corpus / "jira" / "IT" / "прога.exe").write_bytes(b"MZ\x90\x00")
    (tmp_path / "снаружи.txt").write_text("секрет", encoding="utf-8")
    monkeypatch.setattr(O, "CORPUS", str(corpus))
    monkeypatch.setattr(O, "OUT", str(out))
    return type("Box", (), {"corpus": corpus, "out": out})


@pytest.fixture
def http(serve, fetch, box, access, monkeypatch):
    monkeypatch.setattr(O.Handler, "guard", access.guard("office"))
    base = serve(O.Server, O.Handler)
    auth = access.bearer(access.local)      # вход проверяют tests/test_access_office.py

    def call(route, data=None, **params):
        url = base + route + ("?" + urllib.parse.urlencode(params) if params else "")
        status, headers, body = fetch(url, data=data, headers=auth)
        return status, headers, body

    return call


# ── разбор пути ─────────────────────────────────────────────────
def test_путь_внутри_корпуса_находится(box):
    assert O.resolve("jira/IT/заметка.txt") == str(box.corpus / "jira" / "IT" / "заметка.txt")
    assert O.resolve("jira\\IT\\заметка.txt") == str(box.corpus / "jira" / "IT" / "заметка.txt")


@pytest.mark.parametrize("rel", ["../снаружи.txt", "jira/../../снаружи.txt", "/etc/passwd",
                                 "jira/IT", "jira/IT/нет.txt", "", None])
def test_выход_за_корпус_и_несуществующее_не_находятся(box, rel):
    assert O.resolve(rel) is None


# ── диапазоны страниц ───────────────────────────────────────────
@pytest.mark.parametrize("spec, total, expected", [
    ("1-3", 10, [0, 1, 2]),
    ("2,5", 10, [1, 4]),
    ("3, 1-2", 10, [0, 1, 2]),
    ("", 100, list(range(40))),        # без указания — первые сорок
    ("", 5, [0, 1, 2, 3, 4]),
    (None, 3, [0, 1, 2]),
    ("50-60", 10, []),                 # за границей документа
    ("9-12", 10, [8, 9]),              # хвост диапазона отрезается
    ("0", 10, []),                     # страницы считаются с единицы
    ("3-1", 10, []),                   # перевёрнутый диапазон пуст
    ("abc", 10, []),
    ("1-x", 10, []),
])
def test_разбор_диапазона_страниц(spec, total, expected):
    assert O.parse_pages(spec, total) == expected


# ── чтение документа ────────────────────────────────────────────
def test_текстовый_файл_читается_и_обрезается_по_краям(box):
    r = O.read_document("jira/IT/заметка.txt", "")
    assert r["text"] == "Привет, архив"
    assert (r["name"], r["chars"], r["truncated"]) == ("заметка.txt", 13, False)


def test_отсутствующий_документ_даёт_ошибку_а_не_пустоту(box):
    r = O.read_document("../снаружи.txt", "")
    assert r == {"error": "документ не найден или путь вне архива", "path": "../снаружи.txt"}


def test_неподдерживаемый_формат_называется_прямо(box):
    r = O.read_document("jira/IT/прога.exe", "")
    assert r["error"] == "формат .exe не читается"


def test_длинный_текст_обрезается_с_пометкой(box, monkeypatch):
    monkeypatch.setattr(O, "MAX_CHARS", 20)
    (box.corpus / "длинный.txt").write_text("а" * 50, encoding="utf-8")
    r = O.read_document("длинный.txt", "")
    assert r["truncated"] is True and r["chars"] == 50
    assert r["text"] == "а" * 20 + "\n… обрезано, запроси нужные страницы"


def test_текст_ровно_в_предел_не_обрезается(box, monkeypatch):
    monkeypatch.setattr(O, "MAX_CHARS", 20)
    (box.corpus / "ровно.txt").write_text("а" * 20, encoding="utf-8")
    r = O.read_document("ровно.txt", "")
    assert r["truncated"] is False and r["text"] == "а" * 20


def test_сбой_чтения_возвращается_причиной(box):
    (box.corpus / "битый.pdf").write_bytes(b"not a pdf")           # pdf читает обязательная библиотека: тест не зависит от библиотек по форматам
    r = O.read_document("битый.pdf", "")
    assert r["path"] == "битый.pdf" and ":" in r["error"]


def test_word_читается_с_таблицами(box):
    pytest.importorskip("docx")
    p = box.corpus / "отчёт.docx"
    O.build_docx("Отчёт", [{"type": "heading", "value": "Раздел"},
                           {"type": "text", "value": "Абзац"},
                           {"type": "table", "value": [["а", "б"], ["1", "2"]]}], str(p))
    r = O.read_document("отчёт.docx", "")
    assert "Раздел" in r["text"] and "Абзац" in r["text"]
    assert "а | б" in r["text"] and "1 | 2" in r["text"]
    assert r["info"]["таблиц"] == 1


def test_excel_читается_по_выбранным_листам(box):
    pytest.importorskip("openpyxl")
    p = box.corpus / "книга.xlsx"
    O.build_xlsx("x", [{"name": "Первый", "value": [[1, 2]]},
                       {"name": "Второй", "value": [["x", "y"]]}], str(p))
    только_второй = O.read_document("книга.xlsx", "Второй")
    assert "[лист Второй]" in только_второй["text"] and "x | y" in только_второй["text"]
    assert "1 | 2" not in только_второй["text"]
    assert только_второй["info"] == {"листы": ["Первый", "Второй"], "прочитано": ["Второй"]}
    оба = O.read_document("книга.xlsx", "")
    assert "1 | 2" in оба["text"] and "x | y" in оба["text"]


# ── сборка файлов ───────────────────────────────────────────────
BLOCKS = {
    "docx": [{"type": "heading", "value": "Раздел"}, {"type": "bullets", "value": ["один"]}],
    "pdf": [{"type": "text", "value": "Text"}, {"type": "table", "value": [["a", "b"]]}],
    "xlsx": [{"name": "Лист", "value": [[1, 2], [3, 4]]}],
    "pptx": [{"name": "Слайд", "bullets": ["пункт"]}],
}
LIBS = {"docx": "docx", "pdf": "reportlab", "xlsx": "openpyxl", "pptx": "pptx"}


@pytest.mark.parametrize("kind", ["docx", "xlsx", "pptx", "pdf"])
def test_файл_собирается_и_лежит_в_папке_результатов(box, kind):
    pytest.importorskip(LIBS[kind])
    r = O.make_document(kind, "Проба", BLOCKS[kind])
    assert "error" not in r
    assert os.path.dirname(r["file"]) == str(box.out) and r["file"].endswith("." + kind)
    assert r["size"] == os.path.getsize(r["file"]) > 0
    assert r["url"] == "http://127.0.0.1:8766/file?n=" + urllib.parse.quote(os.path.basename(r["file"]))


def test_неизвестный_вид_файла_перечисляет_доступные(box):
    r = O.make_document("exe", "x", [])
    assert r == {"error": "не умею exe, доступны: docx, xlsx, pptx, pdf"}
    assert os.listdir(box.out) == []


def test_сбой_сборки_возвращается_причиной(box):
    pytest.importorskip("openpyxl")
    r = O.make_document("xlsx", "x", [{"name": "Лист", "value": 5}])
    assert "error" in r and ":" in r["error"]


# ── схема по описанию ───────────────────────────────────────────
@pytest.fixture
def dot(monkeypatch):
    seen = {}

    def fake(source, fmt):
        seen["dot"], seen["fmt"] = source, fmt
        return {"url": "ok"}

    monkeypatch.setattr(O, "make_diagram", fake)
    return seen


GROUPS = [{"name": "Экраны", "nodes": ["Маршрут", "Журнал прогулок"]},
          {"name": "Данные", "nodes": ["Хранилище"]}]


def test_схема_строится_из_групп_и_связей(dot):
    O.make_landscape("Ландшафт", GROUPS, [["Маршрут", "Хранилище"],
                                          ["Журнал прогулок", "Хранилище", "через кэш"]], "lr")
    src = dot["dot"]
    assert dot["fmt"] == "png"
    assert "rankdir=LR" in src and 'label="Ландшафт", labelloc=t' in src
    assert "subgraph cluster_0" in src and "subgraph cluster_1" in src
    assert 'n0 [label="Маршрут"' in src and 'n2 [label="Хранилище"' in src
    assert "n0 -> n2;" in src
    assert 'n1 -> n2 [label="через кэш", fontsize=9];' in src


def test_связь_к_неизвестному_узлу_пропускается(dot):
    O.make_landscape(None, GROUPS, [["Маршрут", "Нет такого"], ["Экраны", "Данные"],
                                    ["одиночка"], "строка", {"from": "Хранилище", "to": "Журнал прогулок"}], None)
    assert dot["dot"].count("->") == 1
    assert "n2 -> n1;" in dot["dot"]


def test_неверное_направление_заменяется_на_сверху_вниз(dot):
    O.make_landscape(None, GROUPS, [], "вбок")
    assert "rankdir=TB" in dot["dot"] and "labelloc" not in dot["dot"]


def test_группа_без_имени_получает_номер(dot):
    O.make_landscape(None, [{"nodes": ["А"]}], [], None)
    assert 'label="Блок 1"' in dot["dot"]


@pytest.mark.parametrize("groups", [None, []])
def test_без_групп_схема_не_строится(dot, groups):
    assert O.make_landscape("x", groups, [], None) == {"error": "нужен хотя бы один блок в groups"}
    assert dot == {}


def test_кавычки_переводы_строк_и_обратный_слэш_обезвреживаются():
    assert O.q('a"b\nc\\d') == '"a\'b c d"'
    assert O.q(5) == '"5"'


@pytest.mark.skipif(shutil.which("dot") is None, reason="нет graphviz")
def test_graphviz_рисует_и_сообщает_об_ошибке_в_исходнике(box):
    ok = O.make_diagram("digraph G { a -> b }", "exe")      # неизвестный формат -> png
    assert ok["file"].endswith(".png")
    assert open(ok["file"], "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    bad = O.make_diagram("digraph G { a -> ", "png")
    assert bad["error"].startswith("graphviz отказался")


def test_график_строится(box):
    pytest.importorskip("matplotlib")
    r = O.make_chart("bar", "Заявки", ["янв", "фев"], {"одобрено": [3, 5]}, "штук")
    assert open(r["file"], "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    assert r["markdown"].startswith("![Заявки](http://127.0.0.1:8766/file?n=")


@pytest.mark.parametrize("series", [None, {}])
def test_график_без_рядов_не_строится(box, series):
    pytest.importorskip("matplotlib")
    assert O.make_chart("bar", "x", ["a"], series, None) == {"error": "нужен хотя бы один ряд в series"}


# ── уборка и выдача файлов ──────────────────────────────────────
def test_уборка_удаляет_файлы_старше_двух_суток(box):
    old, fresh = box.out / "старый.png", box.out / "свежий.png"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    past = time.time() - (O.KEEP_HOURS * 3600 + 60)
    os.utime(old, (past, past))
    O.sweep()
    assert not old.exists() and fresh.exists()


def test_выдача_картинки_и_файла_на_скачивание(http, box):
    (box.out / "diagram-101010-abc123.png").write_bytes(b"\x89PNG")
    (box.out / "document-101010-abc123.docx").write_bytes(b"PK")
    status, headers, body = http("/file", n="diagram-101010-abc123.png")
    assert (status, headers["Content-Type"], body) == (200, "image/png", b"\x89PNG")
    assert "Content-Disposition" not in headers
    status, headers, _ = http("/file", n="document-101010-abc123.docx")
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["Content-Disposition"] == 'attachment; filename="document-101010-abc123.docx"'


def test_картинка_с_русским_именем_выдаётся(http, box):
    (box.out / "схема.png").write_bytes(b"\x89PNG")
    assert http("/file", n="схема.png")[0] == 200


@pytest.mark.xfail(strict=True, raises=Exception,
                   reason="русское имя не помещается в заголовок latin-1, соединение рвётся без ответа")
def test_файл_на_скачивание_с_русским_именем_выдаётся(http, box):
    (box.out / "отчёт.docx").write_bytes(b"PK")
    assert http("/file", n="отчёт.docx")[0] == 200


@pytest.mark.parametrize("name, code", [("a/b.png", 400), ("..\\x.png", 400), (".скрытый", 400),
                                        ("", 400), ("нет.png", 404)])
def test_плохое_имя_файла_отклоняется(http, name, code):
    assert http("/file", n=name)[0] == code


def test_чтение_через_http(http):
    status, _, body = http("/read", path="jira/IT/заметка.txt")
    assert status == 200 and json.loads(body)["text"] == "Привет, архив"


def test_описание_для_модели_перечисляет_шесть_инструментов():
    ids = {v[m]["operationId"] for v in O.spec()["paths"].values() for m in v}
    assert ids == {"read_document", "read_document_rich", "make_chart",
                   "make_landscape", "make_diagram", "make_document"}


def test_неизвестный_адрес_404(http):
    assert http("/nope")[0] == 404
    assert http("/nope", data={"x": 1})[0] == 404

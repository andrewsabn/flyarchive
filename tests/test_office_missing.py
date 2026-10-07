"""Сервер документов без библиотеки по формату или без программы отвечает отказом, а не обрывом соединения (FR-107).

Находка пробы на пустой машине: вызов `make_chart` через MCP без matplotlib оставлял в журнале сервера документов трассировку `ModuleNotFoundError`,
а клиент получал обрыв соединения («RemoteDisconnected»). Теперь каждый инструмент, которому нужна библиотека или программа, отвечает так же, как
служба поиска: код 503, `{"error": текст, "code": код сообщения}`, текст называет библиотеку (сообщение `lib.missing`) или программу
(`doctor.tool_missing`) и что поставить. Служба после такого запроса жива, трассировки в журнале нет, недособранных файлов нет; переходник MCP
отдаёт модели читаемую ошибку инструмента (`isError`). Неожиданный сбой инструмента — 500 с видом сбоя, тоже без обрыва.

Отсутствие библиотеки изображается блокировкой импорта (`sys.modules[имя] = None`), программы — путём поиска без неё: ничего не удаляется.
Сервер и переходник — настоящие обработчики на случайных портах; последний тест — настоящие процессы.
"""
import json
import os
import shutil
import sys
import urllib.parse

import pytest

import doctor as D
import mcp_server as MCP
import messages as M
import office_server as O
import tokens
from archivekit import Archive
from test_created_files_closed import request, running
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов


def lib(package):
    """Сообщение lib.missing для пакета так, как его собирает проверка окружения (та же таблица)."""
    module, name, must, use = next(row for row in D.LIBRARIES if row[1] == package)
    return str(M.make("lib.missing", package=name, use=use, file=D.REQUIRED_FILE if must else D.OPTIONAL_FILE))


DOT = str(M.make("doctor.tool_missing", name="dot", use="dot"))
CHART = {"kind": "bar", "labels": ["а", "б"], "series": {"ряд": [1, 2]}}
GROUPS = {"groups": [{"name": "Экраны", "nodes": ["Маршрут", "Хранилище"]}], "edges": [["Маршрут", "Хранилище"]]}
BLOCKS = [{"type": "text", "value": "абзац"}]
# инструмент сервера -> случаи: (название случая, метод, путь, параметры или тело, что скрыто, ожидаемое сообщение).
# Скрытое — имя модуля для блокировки импорта или "dot" для программы. Перечень инструментов сверяется со схемой сервера ниже.
CASES = {
    "read_document": [("docx", "GET", "/read", {"path": "образцы/а.docx"}, "docx", lib("python-docx")),
                      ("xlsx", "GET", "/read", {"path": "образцы/б.xlsx"}, "openpyxl", lib("openpyxl")),
                      ("pptx", "GET", "/read", {"path": "образцы/в.pptx"}, "pptx", lib("python-pptx")),
                      ("msg", "GET", "/read", {"path": "образцы/г.msg"}, "extract_msg", lib("extract-msg")),
                      ("pdf", "GET", "/read", {"path": "образцы/д.pdf"}, "pymupdf", lib("pymupdf"))],
    "read_document_rich": [("docx", "GET", "/rich", {"path": "образцы/а.docx"}, "docling", lib("docling"))],
    "make_chart": [("график", "POST", "/chart", CHART, "matplotlib", lib("matplotlib"))],
    "make_diagram": [("схема", "POST", "/diagram", {"dot": "digraph G { a -> b }"}, "dot", DOT)],
    "make_landscape": [("ландшафт", "POST", "/landscape", GROUPS, "dot", DOT)],
    "make_document": [("docx", "POST", "/document", {"kind": "docx", "blocks": BLOCKS}, "docx", lib("python-docx")),
                      ("xlsx", "POST", "/document", {"kind": "xlsx", "blocks": [{"name": "Лист", "value": [[1]]}]}, "openpyxl", lib("openpyxl")),
                      ("pptx", "POST", "/document", {"kind": "pptx", "blocks": [{"name": "Слайд", "bullets": ["а"]}]}, "pptx", lib("python-pptx")),
                      ("pdf", "POST", "/document", {"kind": "pdf", "blocks": BLOCKS}, "reportlab", lib("reportlab"))],
}
ALL = [(tool, *case) for tool, cases in CASES.items() for case in cases]
IDS = [f"{tool}-{case[0]}" for tool, *case in ALL]


@pytest.fixture
def box(tmp_path, monkeypatch):
    corpus, out = tmp_path / "corpus", tmp_path / "out"
    (corpus / "образцы").mkdir(parents=True)
    out.mkdir()
    for name in ("а.docx", "б.xlsx", "в.pptx", "г.msg", "д.pdf"):
        (corpus / "образцы" / name).write_bytes(b"PK\x03\x04 not a real file: the library is never reached")
    monkeypatch.setattr(O, "CORPUS", str(corpus))
    monkeypatch.setattr(O, "OUT", str(out))
    return type("Box", (), {"corpus": corpus, "out": out, "tmp": tmp_path})


@pytest.fixture
def http(serve, fetch, box, access, monkeypatch):
    monkeypatch.setattr(O.Handler, "guard", access.guard("office"))
    base = serve(O.Server, O.Handler)

    def call(method, route, data=None):
        if method == "GET":
            url = base + route + ("?" + urllib.parse.urlencode(data) if data else "")
            return fetch(url, headers=access.bearer(access.full))
        return fetch(base + route, data=data, headers=access.bearer(access.full))

    call.base = base
    return call


def hide(monkeypatch, box, what):
    if what == "dot":
        empty = box.tmp / "пустой-путь"
        empty.mkdir(exist_ok=True)
        monkeypatch.setenv("PATH", str(empty))
    else:
        # и уже загруженные части библиотеки: `from reportlab.lib import …` берёт готовую часть из sys.modules, не заходя в корень
        for name in [n for n in sys.modules if n == what or n.startswith(what + ".")] + [what]:
            monkeypatch.setitem(sys.modules, name, None)


def alive(http):
    status, _, body = http("GET", "/")
    assert status == 200 and "инструменты" in json.loads(body)


# ── сам отказ ───────────────────────────────────────────────────
@pytest.mark.parametrize("tool, name, method, route, data, what, message", ALL, ids=IDS)
def test_без_библиотеки_или_программы_инструмент_отвечает_503_с_кодом_именем_и_подсказкой(http, box, monkeypatch, capsys, tool, name, method, route,
                                                                                          data, what, message):
    hide(monkeypatch, box, what)
    status, headers, body = http(method, route, data)
    text = body.decode("utf-8")
    assert status == 503, text
    assert headers["Content-Type"].startswith("application/json")
    answer = json.loads(text)
    code = "doctor.tool_missing" if what == "dot" else "lib.missing"
    assert set(answer) == {"error", "code"} and answer["code"] == code and answer["error"] == message, answer
    assert "Traceback" not in text and "File \"" not in text
    if what != "dot":
        assert "python3 -m pip install -r requirements" in answer["error"]
    assert capsys.readouterr().err.count("Traceback") == 0, "трассировка в журнале службы — единственное, что осталось бы от отказа"
    assert os.listdir(box.out) == [], "отказ оставил недособранный файл"
    alive(http)


def test_отказ_записан_в_журнал_обращений_с_кодом_ответа_и_кодом_сообщения(http, box, access, monkeypatch):
    hide(monkeypatch, box, "matplotlib")
    assert http("POST", "/chart", CHART)[0] == 503
    last = access.journal()[-1]
    assert (last["tool"], last["status"], last["outcome"]) == ("/chart", 503, "отказ: lib.missing")


def test_каждый_инструмент_сервера_есть_в_таблице_случаев():
    spec_ids = {v[m]["operationId"] for v in O.spec()["paths"].values() for m in v}
    assert spec_ids == set(CASES), "новый инструмент без случая «нет библиотеки»: допиши его в CASES"


def test_каждая_библиотека_формата_сервера_названа_в_случаях_а_неизвестное_имя_отказом_не_становится():
    hidden = {case[4] for cases in CASES.values() for case in cases}
    assert {"docx", "openpyxl", "pptx", "extract_msg", "pymupdf", "docling", "matplotlib", "reportlab", "dot"} <= hidden
    assert O.missing_library(ModuleNotFoundError("No module named 'lxml'", name="lxml")) is None
    assert O.missing_library(ImportError("битый импорт")) is None
    for module, package, must, use in D.LIBRARIES:
        refusal = O.missing_library(ModuleNotFoundError("нет", name=module + ".подмодуль"))
        assert refusal is not None and refusal.message.args["package"] == package and refusal.status == 503, module


def test_чужой_сбой_импорта_не_называется_отсутствием_библиотеки_а_отвечает_прежним_объектом_ошибки(http, box, monkeypatch):
    def reader(path, pages):
        raise ModuleNotFoundError("No module named 'lxml'", name="lxml")

    monkeypatch.setitem(O.READERS, ".docx", reader)
    status, _, body = http("GET", "/read", {"path": "образцы/а.docx"})
    assert status == 200 and json.loads(body)["error"] == "ModuleNotFoundError: No module named 'lxml'"
    alive(http)


# ── неожиданный сбой ────────────────────────────────────────────
def test_неожиданный_сбой_инструмента_это_500_с_видом_сбоя_а_не_обрыв_соединения_и_служба_жива(http, monkeypatch, capsys):
    def boom(*a):
        raise RuntimeError("что-то пошло не так")

    monkeypatch.setattr(O, "make_chart", boom)
    status, headers, body = http("POST", "/chart", CHART)
    assert status == 500 and json.loads(body) == {"error": "RuntimeError: что-то пошло не так"}
    assert "Traceback" in capsys.readouterr().err, "подробность для владельца остаётся в журнале службы"
    alive(http)


def test_сбой_чтения_документа_не_из_за_библиотеки_отвечает_как_раньше(http, box):
    (box.corpus / "образцы" / "е.txt").write_text("текст", encoding="utf-8")
    status, _, body = http("GET", "/read", {"path": "образцы/е.txt"})
    assert status == 200 and json.loads(body)["text"] == "текст"
    status, _, body = http("GET", "/read", {"path": "образцы/нет.docx"})
    assert status == 200 and "не найден" in json.loads(body)["error"]


# ── переходник MCP ──────────────────────────────────────────────
@pytest.mark.parametrize("tool, name, method, route, data, what, message", ALL, ids=IDS)
def test_модель_через_mcp_получает_читаемую_ошибку_инструмента_с_названием_библиотеки(http, box, access, monkeypatch, tool, name, method, route,
                                                                                    data, what, message):
    hide(monkeypatch, box, what)
    monkeypatch.setitem(MCP.BY_NAME[tool], "route", (method, http.base + route))          # переходник идёт к серверу этого теста
    call = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": tool, "arguments": data}}
    result = MCP.handle(call, tokens.Client("проба", "local"), token=access.full)["result"]
    assert result["isError"] is True, result
    assert result["content"][0]["text"] == "сбой архива (код 503): " + message
    alive(http)


# ── настоящие процессы ──────────────────────────────────────────
def test_настоящие_службы_без_matplotlib_и_без_dot_график_и_схема_через_mcp_это_isError_служба_жива_трассировки_в_журнале_нет(tmp_path, vectors):
    pytest.importorskip("lancedb")
    archive = Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})
    assert archive("init")[0] == 0
    only_bash = tmp_path / "только-bash"                                   # путь поиска без dot (и без всего прочего, кроме запуска службы)
    only_bash.mkdir()
    os.symlink(shutil.which("bash"), only_bash / "bash")
    with running(archive, PATH=str(only_bash), **archive.hide("matplotlib")) as (ports, token, _):
        office, mcp = (f"http://127.0.0.1:{ports[k]}" for k in ("office", "mcp"))
        status, answer = request(f"{office}/chart", token, CHART)
        assert status == 503 and answer["code"] == "lib.missing" and "matplotlib" in answer["error"], answer
        status, answer = request(f"{office}/diagram", token, {"dot": "digraph G { a -> b }"})
        assert status == 503 and answer["code"] == "doctor.tool_missing" and "dot" in answer["error"], answer
        for tool, arguments, message in (("make_chart", CHART, lib("matplotlib")), ("make_diagram", {"dot": "digraph G { a -> b }"}, DOT)):
            status, answer = request(f"{mcp}/mcp", token, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                          "params": {"name": tool, "arguments": arguments}})
            result = answer["result"]
            assert status == 200 and result["isError"] is True, answer
            assert result["content"][0]["text"] == "сбой архива (код 503): " + message, result
            assert "RemoteDisconnected" not in json.dumps(answer)
        status, _ = request(f"{office}/", token)
        assert status == 200, "служба жива после отказов"
    log = open(os.path.join(str(archive.base), "office_server.py.log"), encoding="utf-8").read()
    assert "Traceback" not in log, log
    assert os.listdir(archive.path("out")) == [], "отказы не оставили файлов"

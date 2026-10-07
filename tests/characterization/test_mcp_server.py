"""Фиксация нынешнего поведения переходника MCP (mcp_server.py)."""
import json
import urllib.error
import urllib.parse

import pytest

import mcp_server as M
import tokens as T

NAMES = ["search_archive", "read_document", "read_document_rich", "make_landscape",
         "make_diagram", "make_chart", "make_document", "submit_document"]


LOCAL = T.Client("dsh-local", "local")


def handle(msg):
    """Вызов от имени локального DSH: права проверяют tests/test_access_mcp.py."""
    return M.handle(msg, LOCAL, "ba_local-test-token")


def rpc(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        msg["id"] = id
    if params is not None:
        msg["params"] = params
    return msg


class FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def upstream(monkeypatch):
    """Перехватывает обращения переходника к серверам поиска и документов."""
    seen = {"requests": [], "answer": b'{"count": 0}', "error": None}

    def fake_urlopen(req, timeout=None):
        seen["requests"].append(req)
        seen["timeout"] = timeout
        if seen["error"]:
            raise seen["error"]
        return FakeResp(seen["answer"])

    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)
    return seen


# ── протокол ────────────────────────────────────────────────────
def test_приветствие_возвращает_версию_клиента_и_имя_сервера():
    r = handle(rpc("initialize", {"protocolVersion": "2099-01-01"}))
    assert r["id"] == 1 and r["jsonrpc"] == "2.0"
    assert r["result"]["protocolVersion"] == "2099-01-01"
    import version                                                    # версия — одна на весь продукт (tools/version.py), а не число службы
    assert r["result"]["serverInfo"] == {"name": "flyarchive", "version": version.VERSION}
    assert r["result"]["capabilities"] == {"tools": {"listChanged": False}}


def test_приветствие_без_версии_подставляет_свою():
    assert handle(rpc("initialize", {}))["result"]["protocolVersion"] == "2025-06-18"


def test_список_инструментов_без_внутренних_полей():
    tools = handle(rpc("tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == NAMES
    for t in tools:
        assert set(t) == {"name", "description", "inputSchema", "annotations"}     # route и need наружу не уходят
        assert t["inputSchema"]["type"] == "object" and t["inputSchema"]["required"]


def test_уведомление_без_номера_остаётся_без_ответа():
    assert handle(rpc("notifications/initialized", id=None)) is None
    assert handle(rpc("tools/list", id=None)) is None


def test_проверка_связи():
    assert handle(rpc("ping"))["result"] == {}


def test_неизвестный_метод_и_неизвестный_инструмент_различаются_кодом():
    assert handle(rpc("чепуха"))["error"]["code"] == -32601
    r = handle(rpc("tools/call", {"name": "нет такого", "arguments": {}}))
    assert r["error"]["code"] == -32602 and "нет такого" in r["error"]["message"]
    assert handle(rpc("tools/call"))["error"]["code"] == -32602


# ── пересылка вызова ────────────────────────────────────────────
def test_поиск_уходит_get_запросом_без_пустых_параметров(upstream):
    upstream["answer"] = '{"count": 1, "результат": "да"}'.encode("utf-8")
    r = handle(rpc("tools/call", {"name": "search_archive",
                                    "arguments": {"q": "реестр платежей", "k": 5,
                                                  "source": "", "since": None}}))
    req, = upstream["requests"]
    assert req.get_method() == "GET" and req.data is None
    url = urllib.parse.urlparse(req.full_url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "http://127.0.0.1:8765/api/search"
    assert urllib.parse.parse_qs(url.query) == {"q": ["реестр платежей"], "k": ["5"]}
    assert r["result"] == {"content": [{"type": "text", "text": '{"count": 1, "результат": "да"}'}]}
    assert upstream["timeout"] == 300


def test_вызов_без_аргументов_уходит_с_пустым_запросом(upstream):
    handle(rpc("tools/call", {"name": "read_document"}))
    assert upstream["requests"][0].full_url == "http://127.0.0.1:8766/read?"


def test_схема_уходит_post_запросом_с_json_в_utf8(upstream):
    args = {"title": "Ландшафт", "groups": [{"name": "Экраны", "nodes": ["Маршрут"]}]}
    handle(rpc("tools/call", {"name": "make_landscape", "arguments": args}))
    req, = upstream["requests"]
    assert req.get_method() == "POST"
    assert req.full_url == "http://127.0.0.1:8766/landscape"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data.decode("utf-8")) == args
    assert "Ландшафт".encode("utf-8") in req.data            # кириллица не экранирована


@pytest.mark.parametrize("name, method, url", [
    ("read_document", "GET", "http://127.0.0.1:8766/read"),
    ("read_document_rich", "GET", "http://127.0.0.1:8766/rich"),
    ("make_diagram", "POST", "http://127.0.0.1:8766/diagram"),
    ("make_chart", "POST", "http://127.0.0.1:8766/chart"),
    ("make_document", "POST", "http://127.0.0.1:8766/document"),
])
def test_каждый_инструмент_ведёт_на_свой_адрес(upstream, name, method, url):
    handle(rpc("tools/call", {"name": name, "arguments": {"path": "a"}}))
    req, = upstream["requests"]
    assert req.get_method() == method and req.full_url.split("?")[0] == url


def test_недоступный_сервер_сообщается_ошибкой_инструмента_а_не_пустотой(upstream):
    upstream["error"] = urllib.error.URLError("connection refused")
    r = handle(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "x"}}))
    assert r["result"]["isError"] is True
    assert r["result"]["content"][0]["text"].startswith("инструмент недоступен:")
    assert "error" not in r


def test_прочий_сбой_пересылки_тоже_ошибка_инструмента(upstream):
    upstream["error"] = ValueError("кривой ответ")
    r = handle(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "x"}}))
    assert r["result"] == {"content": [{"type": "text", "text": "ValueError: кривой ответ"}],
                           "isError": True}


# ── транспорт ───────────────────────────────────────────────────
@pytest.fixture
def mcp(serve, fetch, access, monkeypatch):
    monkeypatch.setattr(M.Handler, "guard", access.guard("mcp"))
    base = serve(M.Server, M.Handler)
    auth = access.bearer(access.local)

    def call(data=None, path="/mcp", method=None, headers=None):
        status, _, body = fetch(base + path, data=data, method=method, headers={**auth, **(headers or {})})
        return status, (json.loads(body.decode("utf-8")) if body else None)

    return call


def test_запрос_по_http_отвечает_json(mcp):
    status, body = mcp(rpc("initialize", {}))
    assert status == 200 and body["result"]["serverInfo"]["name"] == "flyarchive"


def test_уведомление_по_http_принимается_без_тела(mcp):
    assert mcp(rpc("notifications/initialized", id=None)) == (202, None)


def test_пачка_запросов_возвращает_ответы_без_уведомлений(mcp):
    status, body = mcp([rpc("ping", id=1), rpc("notifications/initialized", id=None),
                        rpc("tools/list", id=2)])
    assert status == 200 and [r["id"] for r in body] == [1, 2]
    assert mcp([rpc("notifications/initialized", id=None)]) == (202, None)


def test_get_отвечает_405_поток_событий_не_ведётся(mcp):
    """Ответ 200 клиент принимает за открытый поток и опрашивает без конца."""
    status, body = mcp(path="/mcp")
    assert status == 405 and "error" in body


def test_битый_json_отклоняется(mcp):
    status, body = mcp(data=b"{not json", headers={"Content-Type": "application/json"})
    assert status == 400 and body["error"]["code"] == -32700


def test_чужой_адрес_404(mcp):
    assert mcp(rpc("ping"), path="/other")[0] == 404
    assert mcp(path="/other")[0] == 404


def test_закрытие_сессии_принимается(mcp):
    assert mcp(method="DELETE") == (202, None)


def test_очередь_подключений_расширена():
    assert M.Server.request_queue_size == 128

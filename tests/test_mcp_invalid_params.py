"""Переходник MCP не падает на `params`, которые не объект (FR-93).

Запрос `{"jsonrpc":"2.0","id":1,"method":"initialize","params":[1]}` (то же для `tools/list` и `tools/call`) раньше доходил до `msg.get("params").get(...)` и
ронял обработчик `AttributeError`: по сети — трассировка в журнале службы и обрыв соединения без ответа. Теперь — ответ JSON-RPC с ошибкой «Invalid params»
(код −32602) и тем же `id`; у `tools/call` то же для `arguments`, которые не объект, и для `name`, которое не строка. Код ответа HTTP тот же, что у
неизвестного метода (200: ошибка метода лежит в теле ответа), отказ пишется в журнал обращений, служба жива и до служб за переходником отказанный запрос
не доходит. Отсутствующие `params` и `arguments` (и `null` вместо них) — как раньше: пустой объект.
"""
import json

import pytest

import mcp_server as M
from test_mcp_invalid_request import LOCAL, rpc, srv  # noqa: F401 — переходник по сети с подставными службами за ним

METHODS = ["initialize", "tools/list", "tools/call"]
NOT_OBJECTS = [[1], ["x"], [], [[]], [{}], "строка", "", 5, 0, 1.5, True, False]
IDS = [1, 0, "abc", 7.5]


def invalid(id=1):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": -32602, "message": "Invalid params"}}


def ident(value):
    return json.dumps(value, ensure_ascii=False)


# ── прямой вызов handle ─────────────────────────────────────────
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("params", NOT_OBJECTS, ids=ident)
def test_params_не_объект_handle_отвечает_invalid_params_а_не_падает(method, params):
    assert M.handle(rpc(method, params), LOCAL) == invalid()


@pytest.mark.parametrize("mid", IDS, ids=ident)
def test_ответ_несёт_тот_же_id_что_и_запрос(mid):
    for method in METHODS:
        assert M.handle(rpc(method, [1], id=mid), LOCAL) == invalid(mid)


@pytest.mark.parametrize("arguments", NOT_OBJECTS, ids=ident)
def test_arguments_не_объект_у_tools_call_это_invalid_params_и_до_служб_дело_не_доходит(arguments, monkeypatch):
    monkeypatch.setattr(M, "call_upstream", lambda *a, **k: pytest.fail("отказанный вызов дошёл до служб"))
    assert M.handle(rpc("tools/call", {"name": "search_archive", "arguments": arguments}), LOCAL) == invalid()


@pytest.mark.parametrize("name", [5, 0, 1.5, True, False, None, [], ["search_archive"], {}, {"name": "search_archive"}], ids=ident)
def test_name_не_строка_у_tools_call_это_invalid_params(name, monkeypatch):
    monkeypatch.setattr(M, "call_upstream", lambda *a, **k: pytest.fail("отказанный вызов дошёл до служб"))
    assert M.handle(rpc("tools/call", {"name": name, "arguments": {}}), LOCAL) == invalid()


def test_отказ_записывается_в_журнал_обращений_через_audit_с_названием_метода():
    for method in METHODS:
        records = []
        answer = M.handle(rpc(method, [1]), LOCAL, audit=lambda tool, params, status, outcome: records.append((tool, params, status, outcome)))
        assert answer == invalid()
        (tool, params, status, outcome), = records
        assert (tool, params, status) == ("mcp:invalid_params", {"method": method}, 400) and outcome.startswith("отказ: "), method


def test_без_audit_отказ_тоже_без_падения():
    assert M.handle(rpc("initialize", "x"), LOCAL, audit=None) == invalid()


# ── прежнее поведение ───────────────────────────────────────────
@pytest.mark.parametrize("method", ["initialize", "tools/list"])
def test_params_нет_или_null_или_пустой_объект_запрос_отвечает_как_раньше(method):
    for params in (None, {}):
        answer = M.handle(rpc(method, params), LOCAL)
        assert "result" in answer and answer["id"] == 1, (method, params, answer)
    answer = M.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": None}, LOCAL)
    assert "result" in answer and answer["id"] == 1


def test_initialize_возвращает_версию_протокола_клиента_как_раньше():
    answer = M.handle(rpc("initialize", {"protocolVersion": "2025-03-26"}), LOCAL)
    assert answer["result"]["protocolVersion"] == "2025-03-26" and answer["result"]["serverInfo"]["name"] == "flyarchive"


@pytest.mark.parametrize("info", [5, "строка", [1], True], ids=ident)
def test_clientInfo_не_объект_не_роняет_initialize_и_в_журнал_идёт_запись_без_имени(info):
    seen = []
    answer = M.handle(rpc("initialize", {"clientInfo": info}), LOCAL, audit=lambda *row: seen.append(row))
    assert answer["result"]["serverInfo"]["name"] == "flyarchive"
    assert seen == [("mcp:initialize", {}, 200, "ok")]


def test_неизвестный_метод_с_params_не_объектом_остаётся_методом_которого_нет():
    assert M.handle(rpc("чепуха", [1]), LOCAL)["error"]["code"] == -32601


def test_ping_params_не_разбирает_как_раньше():
    assert M.handle(rpc("ping", [1]), LOCAL) == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_tools_call_без_имени_и_без_arguments_остаётся_отказом_про_инструмент():
    assert M.handle(rpc("tools/call", {}), LOCAL)["error"]["code"] == -32602
    assert M.handle(rpc("tools/call"), LOCAL)["error"]["code"] == -32602


def test_arguments_null_и_пропущенные_arguments_не_отказ_а_пустой_объект(monkeypatch):
    seen = []
    monkeypatch.setattr(M, "call_upstream", lambda tool, args, *rest: seen.append(args) or "ответ")
    for params in ({"name": "search_archive"}, {"name": "search_archive", "arguments": None}, {"name": "search_archive", "arguments": {}}):
        assert M.handle(rpc("tools/call", params), LOCAL)["result"]["content"][0]["text"] == "ответ"
    assert seen == [{}, {}, {}]


# ── по сети ─────────────────────────────────────────────────────
@pytest.mark.parametrize("method", METHODS)
def test_по_сети_params_массив_это_200_и_invalid_params_служба_жива_отказ_в_журнале(srv, method):
    status, body = srv.post(rpc(method, [1], id=5))
    assert status == 200 and body == invalid(5), "тот же код ответа, что у неизвестного метода"
    assert srv.post(rpc("чепуха"))[0] == 200
    assert srv.post(rpc("tools/list"))[0] == 200, "служба жива и отвечает на следующий запрос"
    recs = [r for r in srv.access.journal() if r["tool"] == "mcp:invalid_params"]
    assert [(r["server"], r["client"], r["status"]) for r in recs] == [("mcp", "читатель", 400)] and recs[0]["outcome"].startswith("отказ: ")
    assert srv.upstream == []


def test_по_сети_arguments_строкой_не_проходит_дальше_до_служб(srv):
    status, body = srv.post(rpc("tools/call", {"name": "search_archive", "arguments": "q=перенос"}))
    assert status == 200 and body == invalid()
    assert srv.upstream == [], "строку вместо объекта в службы не передают"


def test_по_сети_name_списком_не_роняет_обработчик(srv):
    status, body = srv.post(rpc("tools/call", {"name": ["search_archive"], "arguments": {}}))
    assert status == 200 and body == invalid()
    assert srv.post(rpc("ping"))[0] == 200


def test_по_сети_в_пакете_у_негодного_запроса_своя_ошибка_с_его_id_а_у_годного_свой_ответ(srv):
    status, answers = srv.post([rpc("initialize", [1], id=1), rpc("ping", id=2)])
    assert status == 200 and answers == [invalid(1), {"jsonrpc": "2.0", "id": 2, "result": {}}]

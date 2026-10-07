"""Переходник MCP не падает на теле запроса, которое не объект (FR-93).

JSON-RPC ждёт в теле объект. Число, строка, null, булево и массив с не-объектами раньше доходили до `msg.get(...)` и роняли обработчик `AttributeError`:
по сети это трассировка в журнале службы и обрыв соединения без ответа. Теперь — ответ JSON-RPC с ошибкой «Invalid Request» (код −32600, id null),
код ответа HTTP тот же, что у негодного JSON (400), в журнал обращений пишется отказ, служба жива. Пакет объектов (массив, где все элементы — объекты)
сервер принимал и принимает как прежде: его держит tests/characterization/test_mcp_server.py; пустой массив и массив с не-объектом — тот же отказ, целиком.
"""
import json

import pytest

import mcp_server as M
import tokens as T

LOCAL = T.Client("dsh-local", "local")
INVALID = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
BODIES = [123, 0, -1, 1.5, True, False, "строка", "", None, [1], ["x"], [None], [[]], [], [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, 7]]


def rpc(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "method": method, "id": id}
    if params is not None:
        msg["params"] = params
    return msg


# ── прямой вызов handle ─────────────────────────────────────────
@pytest.mark.parametrize("body", BODIES, ids=lambda b: json.dumps(b, ensure_ascii=False))
def test_тело_не_объект_handle_отвечает_invalid_request_с_id_null(body):
    assert M.handle(body, LOCAL) == INVALID


def test_отказ_записывается_в_журнал_обращений_через_audit():
    records = []
    answer = M.handle(123, LOCAL, audit=lambda tool, params, status, outcome: records.append((tool, params, status, outcome)))
    assert answer == INVALID
    (tool, params, status, outcome), = records
    assert (tool, params, status) == ("mcp:invalid_request", {}, 400) and outcome.startswith("отказ: ")


def test_объект_обрабатывается_как_раньше_уведомление_без_ответа_и_запрос_с_ответом():
    assert M.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, LOCAL) is None
    assert M.handle(rpc("ping"), LOCAL) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert M.handle({}, LOCAL) is None, "объект без id — уведомление, как и было"


# ── по сети ─────────────────────────────────────────────────────
@pytest.fixture
def srv(serve, fetch, access, monkeypatch):
    monkeypatch.setattr(M.Handler, "guard", access.guard("mcp"))
    base = serve(M.Server, M.Handler)
    upstream = []

    def refuse_upstream(req, timeout=None):
        upstream.append(req)
        raise AssertionError("отказанный запрос до служб за переходником не доходит")

    monkeypatch.setattr(M.urllib.request, "urlopen", refuse_upstream)

    class Srv:
        pass

    s = Srv()
    s.access, s.upstream = access, upstream

    def post(data, token=None):
        headers = {"Content-Type": "application/json", **access.bearer(token or access.read)}
        status, _, body = fetch(base + "/mcp", data=data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8"), headers=headers)
        return status, (json.loads(body.decode("utf-8")) if body else None)

    s.post = post
    return s


def test_по_сети_число_в_теле_это_400_и_invalid_request_служба_жива_отказ_в_журнале(srv):
    status, body = srv.post(123)
    assert status == 400 and body == INVALID
    assert srv.post(rpc("tools/list"))[0] == 200, "служба жива и отвечает на следующий запрос"
    recs = [r for r in srv.access.journal() if r["tool"] == "mcp:invalid_request"]
    assert [(r["server"], r["client"], r["status"]) for r in recs] == [("mcp", "читатель", 400)] and recs[0]["outcome"].startswith("отказ: ")


@pytest.mark.parametrize("body", BODIES, ids=lambda b: json.dumps(b, ensure_ascii=False))
def test_по_сети_любое_тело_не_объект_и_массив_не_из_объектов_отвечают_одним_и_тем_же_отказом(srv, body):
    status, answer = srv.post(body)
    assert status == 400 and answer == INVALID
    assert srv.upstream == []


def test_тот_же_код_ответа_что_у_негодного_json(srv):
    assert srv.post(b"{not json")[0] == 400 and srv.post(123)[0] == 400


def test_пакет_объектов_принимается_как_прежде(srv):
    status, answer = srv.post([rpc("ping", id=1), rpc("notifications/initialized", id=None), rpc("tools/list", id=2)])
    assert status == 200 and [r["id"] for r in answer] == [1, 2]


def test_отказ_без_токена_идёт_раньше_разбора_тела(srv):
    status, body = srv.post(123, token="ba_" + "x" * 43)
    assert status == 401 and set(body) == {"error"}

"""Вход в переходник MCP: FR-01, FR-04, FR-06, FR-07, FR-12."""
import json

import pytest

import mcp_server as M
import webui as W

READ_TOOLS = ["search_archive", "read_document", "read_document_rich"]
ALL_TOOLS = READ_TOOLS + ["make_landscape", "make_diagram", "make_chart", "make_document", "submit_document"]


def rpc(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "method": method, "id": id}
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
def srv(serve, fetch, access, monkeypatch):
    monkeypatch.setattr(M.Handler, "guard", access.guard("mcp"))
    base = serve(M.Server, M.Handler)
    upstream = []

    def fake_urlopen(req, timeout=None):
        upstream.append(req)
        return FakeResp(b'{"count": 0, "results": []}')

    # подменяются только обращения переходника дальше; запросы теста идут своим путём
    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)

    class Srv:
        pass

    s = Srv()
    s.base, s.access, s.upstream = base, access, upstream

    def post(msg, token=None, headers=None, path="/mcp", method=None):
        h = dict(headers or {})
        if token:
            h.update(access.bearer(token))
        status, _, body = fetch(base + path, data=msg, headers=h, method=method)
        return status, (json.loads(body.decode("utf-8")) if body else None)

    s.post = post
    return s


# ── без токена ──────────────────────────────────────────────────
@pytest.mark.parametrize("msg", [
    rpc("initialize", {}), rpc("tools/list"), rpc("ping"),
    rpc("tools/call", {"name": "search_archive", "arguments": {"q": "x"}}),
    [rpc("tools/list", id=1), rpc("ping", id=2)]])
def test_без_токена_401_и_ничего_не_вызвано(srv, msg):
    status, body = srv.post(msg)
    assert status == 401 and set(body) == {"error"}
    assert srv.upstream == []


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_без_токена_get_и_delete_401(srv, method):
    assert srv.post(None, method=method)[0] == 401


def test_неверный_и_отозванный_токен_401(srv):
    assert srv.post(rpc("tools/list"), token="ba_" + "x" * 43)[0] == 401
    assert srv.post(rpc("tools/list"), token=srv.access.read)[0] == 200
    srv.access.store.revoke("читатель")
    assert srv.post(rpc("tools/list"), token=srv.access.read)[0] == 401


def test_чужой_host_403(srv):
    status, _ = srv.post(rpc("tools/list"), token=srv.access.local, headers={"Host": "evil.example"})
    assert status == 403


# ── уровни ──────────────────────────────────────────────────────
@pytest.mark.parametrize("level, names", [("read", READ_TOOLS), ("full", ALL_TOOLS), ("local", ALL_TOOLS)])
def test_список_инструментов_по_уровню(srv, level, names):
    status, body = srv.post(rpc("tools/list"), token=getattr(srv.access, level))
    assert status == 200 and [t["name"] for t in body["result"]["tools"]] == names


@pytest.mark.parametrize("tool", ["make_landscape", "make_diagram", "make_chart", "make_document", "submit_document"])
def test_скрытый_инструмент_по_имени_не_вызывается(srv, tool):
    status, body = srv.post(rpc("tools/call", {"name": tool, "arguments": {"title": "x"}}), token=srv.access.read)
    assert status == 200 and body["error"]["code"] == -32602
    assert "full" in body["error"]["message"]
    assert srv.upstream == []                       # до сервера документов запрос не дошёл
    rec = srv.access.journal()[-1]
    assert (rec["server"], rec["client"], rec["tool"], rec["status"]) == ("mcp", "читатель", "mcp:" + tool, 403)
    assert rec["outcome"].startswith("отказ: ")


def test_полный_токен_вызывает_создание(srv):
    status, body = srv.post(rpc("tools/call", {"name": "make_chart", "arguments": {"kind": "bar"}}), token=srv.access.full)
    assert status == 200 and "error" not in body and len(srv.upstream) == 1


def test_нет_инструментов_для_опасных_действий(srv):
    """Удаление, решения по очереди и карантину, выпуск токенов по MCP не выставляются вообще."""
    _, body = srv.post(rpc("tools/list"), token=srv.access.local)
    names = {t["name"] for t in body["result"]["tools"]}
    assert names == set(ALL_TOOLS)
    assert not [n for n in names if any(w in n for w in ("delete", "remove", "approve", "token", "quarantine", "revoke"))]


def test_уровень_каждого_инструмента_объявлен():
    assert {t["name"]: t["need"] for t in M.TOOLS} == {**{n: "read" for n in READ_TOOLS},
                                                       **{n: "full" for n in ALL_TOOLS[3:]}}
    for t in M.public_tools("local"):
        assert set(t) == {"name", "description", "inputSchema", "annotations"}      # route и need наружу не уходят


# ── пересылка дальше ────────────────────────────────────────────
def test_дальше_уходит_токен_клиента_и_пометка_mcp(srv):
    srv.post(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "реестр"}}), token=srv.access.read)
    (req,) = srv.upstream
    assert req.get_header("Authorization") == "Bearer " + srv.access.read
    assert req.get_header("X-flyarchive-via") == "mcp"


def test_успешные_служебные_запросы_журнал_не_засоряют(srv):
    srv.post(rpc("tools/list"), token=srv.access.read)
    srv.post(rpc("ping"), token=srv.access.read)
    srv.post({"jsonrpc": "2.0", "method": "notifications/initialized"}, token=srv.access.read)
    assert srv.access.journal() == []


def test_проба_потока_событий_журнал_не_засоряет(srv):
    """Клиент при каждом подключении пробует GET и получает 405 — это штатно, а не ошибка."""
    assert srv.post(None, token=srv.access.local, method="GET")[0] == 405
    assert srv.access.journal() == []


def test_подключение_клиента_записано(srv):
    srv.post(rpc("initialize", {"clientInfo": {"name": "dsh", "version": "0.2.0"}}), token=srv.access.full)
    (rec,) = srv.access.journal()
    assert (rec["server"], rec["client"], rec["level"], rec["tool"]) == ("mcp", "писатель", "full", "mcp:initialize")
    assert rec["params"] == {"client": "dsh 0.2.0"}


def test_отказ_без_токена_записан(srv):
    srv.post(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "x"}}))
    (rec,) = srv.access.journal()
    assert (rec["server"], rec["client"], rec["status"]) == ("mcp", "-", 401)


def test_отказ_отозванному_токену_записан_под_его_именем_и_до_служб_ничего_не_дошло(srv):
    srv.access.store.revoke("читатель")
    assert srv.post(rpc("tools/list"), token=srv.access.read)[0] == 401
    assert srv.post(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "x"}}), token=srv.access.read)[0] == 401
    recs = srv.access.journal()
    assert [(r["server"], r["client"], r["level"], r["status"]) for r in recs] == [("mcp", "читатель", "read", 401)] * 2
    assert all(r["outcome"].startswith("отказ: токен отозван ") for r in recs)
    assert srv.upstream == []


def test_значение_токена_в_журнал_не_попадает(srv):
    srv.post(rpc("initialize", {}), token=srv.access.full)
    srv.post(rpc("tools/call", {"name": "make_chart", "arguments": {}}), token=srv.access.read)
    text = srv.access.journal_text()
    assert srv.access.full not in text and srv.access.read not in text


# ── сквозной путь: MCP → сервер поиска ──────────────────────────
def test_вызов_через_mcp_записан_под_именем_клиента_а_не_службы(serve, fetch, access, monkeypatch):
    monkeypatch.setattr(W.S, "search", lambda q, k=10, *a, **kw: [(0.5, {
        "path": "a.md", "chunk": 0, "text": "находка", "updated": "2026-08-31",
        "title": "А", "source": "jira", "space": "IT"})])
    monkeypatch.setattr(W.Handler, "guard", access.guard("search"))
    monkeypatch.setattr(M.Handler, "guard", access.guard("mcp"))
    search_base = serve(W.Server, W.Handler)
    mcp_base = serve(M.Server, M.Handler)
    monkeypatch.setitem(M.BY_NAME["search_archive"], "route", ("GET", search_base + "/api/search"))

    status, _, body = fetch(mcp_base + "/mcp", headers=access.bearer(access.read),
                            data=rpc("tools/call", {"name": "search_archive", "arguments": {"q": "реестр", "k": 3}}))
    result = json.loads(body)["result"]
    assert status == 200 and "isError" not in result
    assert json.loads(result["content"][0]["text"])["results"][0]["path"] == "a.md"
    (rec,) = access.journal()
    assert (rec["server"], rec["client"], rec["via"], rec["tool"]) == ("search", "читатель", "mcp", "/api/search")
    assert rec["params"] == {"q": "реестр", "k": "3"}


def test_отозванный_посреди_работы_токен_дальше_не_проходит(serve, fetch, access, monkeypatch):
    monkeypatch.setattr(W.S, "search", lambda q, k=10, *a, **kw: [])
    monkeypatch.setattr(W.Handler, "guard", access.guard("search"))
    search_base = serve(W.Server, W.Handler)
    monkeypatch.setitem(M.BY_NAME["search_archive"], "route", ("GET", search_base + "/api/search"))
    client = access.store.verify(access.read)
    access.store.revoke("читатель")
    out = M.handle(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "x"}}), client, access.read)
    assert out["result"]["isError"] is True and "401" in out["result"]["content"][0]["text"]


# ── сбой службы за переходником: ответ с кодом и телом ──────────
def upstream_fails(monkeypatch, code, body):
    import io
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "error", {}, io.BytesIO(body))

    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)


def call_search(srv, **args):
    status, body = srv.post(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "x", **args}}), token=srv.access.read)
    assert status == 200
    return body["result"]


def test_отказ_службы_доходит_до_модели_с_причиной(srv, monkeypatch):
    """Модель должна отличать «я ошиблась в аргументах» от «архив лежит»."""
    upstream_fails(monkeypatch, 400, json.dumps({"error": "since: нужна дата вида 2026, 2026-08 или 2026-08-31"}, ensure_ascii=False).encode("utf-8"))
    result = call_search(srv, since="вчера")
    text = result["content"][0]["text"]
    assert result["isError"] is True and "since: нужна дата вида" in text and "запрос отклонён" in text
    assert "недоступен" not in text and "HTTP Error" not in text


def test_сбой_службы_доходит_до_модели_как_сбой_архива(srv, monkeypatch):
    upstream_fails(monkeypatch, 500, json.dumps({"error": "RuntimeError: таблица не открылась"}, ensure_ascii=False).encode("utf-8"))
    result = call_search(srv)
    text = result["content"][0]["text"]
    assert result["isError"] is True and "таблица не открылась" in text and "сбой архива" in text and "запрос отклонён" not in text


@pytest.mark.parametrize("body", [b"<html>Bad Gateway</html>", b"", b"[1, 2]", b'{"detail": "x"}', b"\xff\xfe", b'{"error": {"Bad Gateway": 1}}',
                                  b'{"error": ""}', b'{"error": null}'])
def test_ответ_службы_без_причины_называется_по_коду(srv, monkeypatch, body):
    upstream_fails(monkeypatch, 502, body)
    result = call_search(srv)
    assert result["isError"] is True and "502" in result["content"][0]["text"] and "Bad Gateway" not in result["content"][0]["text"]


def test_служба_лежит_инструмент_недоступен(srv, monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)
    result = call_search(srv)
    assert result["isError"] is True and "инструмент недоступен" in result["content"][0]["text"]

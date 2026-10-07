"""Шлюз в tailnet: FR-11, FR-09. Снаружи доступны только MCP и подписанные ссылки."""
import json
import urllib.parse

import pytest

import foreign
import gateway as GW
import mcp_server as M
import office_server as O
import webui as W

PUBLIC = "http://archive.example.com:8780"
HOST = {"Host": "archive.example.com:8780"}
DOC = "jira/IT/задача.md"
DOC_TEXT = "Согласовать перенос работ"


def rpc(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "method": method, "id": id}
    if params is not None:
        msg["params"] = params
    return msg


@pytest.fixture
def net(serve, fetch, access, monkeypatch, tmp_path):
    """Три службы на петле и шлюз перед ними — как на рабочей машине, только порты случайные."""
    corpus, out = tmp_path / "corpus", tmp_path / "out"
    (corpus / "jira" / "IT").mkdir(parents=True)
    out.mkdir()
    (corpus / "jira" / "IT" / "задача.md").write_text(DOC_TEXT, encoding="utf-8")
    (out / "chart-101010-abc123.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(W, "CORPUS", str(corpus))
    monkeypatch.setattr(O, "CORPUS", str(corpus))
    monkeypatch.setattr(O, "OUT", str(out))
    calls = []

    def fake_search(q, k=10, *a, **kw):
        calls.append(q)
        return [(0.5, {"path": DOC, "chunk": 0, "text": DOC_TEXT, "updated": "2026-08-31", "title": "Задача", "source": "jira", "space": "IT"})]

    monkeypatch.setattr(W.S, "search", fake_search)
    monkeypatch.setattr(W.Handler, "guard", access.guard("search"))
    monkeypatch.setattr(O.Handler, "guard", access.guard("office"))
    mcp_guard = access.guard("mcp")
    mcp_guard.remote_full = True
    monkeypatch.setattr(M.Handler, "guard", mcp_guard)
    search, office, mcp = serve(W.Server, W.Handler), serve(O.Server, O.Handler), serve(M.Server, M.Handler)
    monkeypatch.setitem(M.BY_NAME["search_archive"], "route", ("GET", search + "/api/search"))
    monkeypatch.setitem(M.BY_NAME["read_document"], "route", ("GET", office + "/read"))
    handler = GW.make_handler(backends={"/mcp": mcp, "/doc": search, "/file": office}, public_url=PUBLIC,
                              hosts=["203.0.113.88"], journal=access.guard("gateway").journal)
    gw = serve(GW.Server, handler)

    class Net:
        pass

    n = Net()
    n.gw, n.search, n.mcp, n.access, n.calls = gw, search, mcp, access, calls

    def call(path, token=None, data=None, headers=None, method=None, host=HOST):
        h = {**(host or {}), **(headers or {})}
        if token:
            h.update(access.bearer(token))
        return fetch(gw + path, data=data, headers=h, method=method)

    def mcp_call(msg, token=None, **kw):
        status, _, body = call("/mcp", token=token, data=msg, **kw)
        return status, (json.loads(body) if body else None)

    n.call, n.mcp_call, n.fetch = call, mcp_call, fetch
    return n


# ── что снаружи доступно ────────────────────────────────────────
def test_mcp_через_шлюз_работает_по_токену(net):
    status, body = net.mcp_call(rpc("tools/list"), token=net.access.read)
    assert status == 200 and [t["name"] for t in body["result"]["tools"]] == ["search_archive", "read_document", "read_document_rich"]


def test_без_токена_через_шлюз_401(net):
    status, body = net.mcp_call(rpc("tools/list"))
    assert status == 401 and set(body) == {"error"}


@pytest.mark.parametrize("path", ["/api/search?q=x", "/", "/search?q=x", "/openapi.json", "/ollama/api/tags", "/read?path=a",
                                  "/login?c=x", "/mcp/extra", "/doc/x", "/chart", "/document"])
def test_остальные_адреса_снаружи_не_существуют(net, path):
    for token in (None, net.access.local):
        assert net.call(path, token=token)[0] == 404
    assert net.calls == []


@pytest.mark.parametrize("path, method", [("/doc?p=a", "POST"), ("/file?n=a", "POST"), ("/doc?p=a", "DELETE"), ("/mcp", "PUT")])
def test_неподходящий_метод_отклоняется(net, path, method):
    status = net.call(path, token=net.access.local, data={"x": 1} if method in ("POST", "PUT") else None, method=method)[0]
    assert status in (404, 405)


@pytest.mark.parametrize("host", ["evil.example", "archive.example.com.evil.example", "127.0.0.1:8780", "192.0.2.20:8780", ""])
def test_чужой_host_на_шлюзе_403(net, host):
    status, _, _ = net.call("/mcp", token=net.access.local, data=rpc("tools/list"), host={"Host": host})
    assert status == 403


def test_адрес_узла_tailnet_тоже_принимается(net):
    status, _ = net.mcp_call(rpc("ping"), token=net.access.read, host={"Host": "203.0.113.88:8780"})
    assert status == 200


# ── ссылки для удалённого клиента ───────────────────────────────
def search_via_mcp(net, token=None):
    status, body = net.mcp_call(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "перенос"}}), token=token or net.access.read)
    assert status == 200 and "isError" not in body["result"]
    return json.loads(body["result"]["content"][0]["text"])["results"][0]["url"]


def test_ссылка_в_ответе_ведёт_на_адрес_шлюза_и_открывается_без_токена(net):
    url = search_via_mcp(net)
    assert url.startswith(PUBLIC + "/doc?p=") and "&e=" in url and "&s=" in url
    status, _, body = net.call(url[len(PUBLIC):])
    assert status == 200 and DOC_TEXT in body.decode("utf-8")


def test_локальному_клиенту_ссылка_прежняя(net, fetch):
    status, _, body = fetch(net.mcp + "/mcp", headers=net.access.bearer(net.access.read),
                            data=rpc("tools/call", {"name": "search_archive", "arguments": {"q": "перенос"}}))
    url = json.loads(json.loads(body)["result"]["content"][0]["text"])["results"][0]["url"]
    assert url.startswith("http://127.0.0.1:8765/doc?p=")


def test_изменённая_ссылка_через_шлюз_403(net):
    url = search_via_mcp(net)[len(PUBLIC):]
    assert net.call(url.replace(urllib.parse.quote(DOC), urllib.parse.quote("jira/IT/другая.md")))[0] == 403
    assert net.call(url[:-1] + ("0" if url[-1] != "0" else "1"))[0] == 403


def test_документ_снаружи_не_открывается_токеном_без_подписи(net):
    """Снаружи документ отдаётся только по подписанной ссылке: токен — для MCP, а не для прямого чтения файлов."""
    for token in (net.access.read, net.access.local):
        assert net.call("/doc?p=" + urllib.parse.quote(DOC), token=token)[0] == 403
    assert net.call("/file?n=chart-101010-abc123.png", token=net.access.local)[0] == 403
    assert net.call("/doc?p=" + urllib.parse.quote(DOC))[0] == 403


# ── журнал и подмена заголовков ─────────────────────────────────
def test_вызов_через_шлюз_записан_с_пометкой_tailnet(net):
    search_via_mcp(net)
    rec = [r for r in net.access.journal() if r["tool"] == "/api/search"][-1]
    assert (rec["client"], rec["via"], rec["ip"]) == ("читатель", "tailnet", "127.0.0.1")


def test_клиент_не_может_подменить_служебные_заголовки(net):
    spoof = {"X-Forwarded-For": "198.51.100.8", "X-Flyarchive-Remote": "http://evil.example", "X-Flyarchive-Via": "local"}
    status, body = net.mcp_call(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "перенос"}}),
                                token=net.access.read, headers=spoof)
    url = json.loads(body["result"]["content"][0]["text"])["results"][0]["url"]
    assert url.startswith(PUBLIC + "/doc?")                       # адрес ссылки задаёт шлюз, а не клиент
    rec = [r for r in net.access.journal() if r["tool"] == "/api/search"][-1]
    assert rec["ip"] == "127.0.0.1" and rec["via"] == "tailnet"


@pytest.mark.foreign_name
def test_чужие_служебные_заголовки_службой_не_принимаются_за_запрос_от_шлюза(net, fetch):
    """Шлюз и службы говорят заголовками X-Flyarchive-*. Заголовки с чужой приставкой — не заголовки шлюза: они ничего не значат."""
    old = foreign.HEADER_PREFIX
    read = net.access.bearer(net.access.read)
    url = net.search + "/api/search?q=x"
    assert fetch(url, headers={"X-Flyarchive-Remote": PUBLIC, **read})[0] == 403          # свой заголовок: запрос с другой машины, токена мало
    for headers in ({old + "Remote": PUBLIC}, {old + "Remote": PUBLIC, old + "Via": "mcp"}, {old.lower() + "remote": PUBLIC}):
        assert fetch(url, headers={**headers, **read})[0] == 200, headers           # чужой: обычный запрос, в «удалённый» не превращается
    asked = [r for r in net.access.journal() if r["tool"] == "/api/search"]
    assert [r["via"] for r in asked[-3:]] == [None, None, None]                              # пометку «через mcp» чужой заголовок не ставит
    # через шлюз чужой заголовок клиента до службы не доходит, а значения своего шлюз ставит сам
    spoof = {old + "Remote": "http://evil.example", old + "Via": "local", "X-Flyarchive-Via": "local"}
    status, body = net.mcp_call(rpc("tools/call", {"name": "search_archive", "arguments": {"q": "перенос"}}), token=net.access.read, headers=spoof)
    assert status == 200 and json.loads(body["result"]["content"][0]["text"])["results"][0]["url"].startswith(PUBLIC + "/doc?")
    assert [r["via"] for r in net.access.journal() if r["tool"] == "/api/search"][-1] == "tailnet"


def test_отказы_шлюза_записаны(net):
    net.call("/api/search?q=x", token=net.access.local)
    net.call("/mcp", token=net.access.local, data=rpc("ping"), host={"Host": "evil.example"})
    recs = [r for r in net.access.journal() if r["server"] == "gateway"]
    assert [r["status"] for r in recs] == [404, 403] and all(r["outcome"].startswith("отказ") for r in recs)


def test_слишком_большой_запрос_отклонён(net, monkeypatch):
    assert GW.MAX_BODY >= 22 * 1024 * 1024                          # документ в 16 МБ в base64 должен проходить
    monkeypatch.setattr(GW, "MAX_BODY", 2 * 1024 * 1024)
    big = rpc("tools/call", {"name": "search_archive", "arguments": {"q": "я" * (3 * 1024 * 1024)}})
    assert net.mcp_call(big, token=net.access.read)[0] == 413
    assert net.calls == []


def test_недоступная_служба_за_шлюзом_502(serve, fetch, access):
    handler = GW.make_handler(backends={"/mcp": "http://127.0.0.1:9", "/doc": "http://127.0.0.1:9", "/file": "http://127.0.0.1:9"},
                              public_url=PUBLIC, hosts=[], journal=access.guard("gateway").journal)
    gw = serve(GW.Server, handler)
    status, _, _ = fetch(gw + "/mcp", data=rpc("ping"), headers={**HOST, **access.bearer(access.read)})
    assert status == 502


# ── службы за шлюзом: удалённый запрос ───────────────────────────
def test_служба_поиска_удалённому_отдаёт_только_по_подписи(net, fetch):
    remote = {"X-Flyarchive-Remote": PUBLIC}
    status, _, _ = fetch(net.search + "/api/search?q=x", headers={**remote, **net.access.bearer(net.access.local)})
    assert status == 403
    assert fetch(net.search + "/api/search?q=x", headers=net.access.bearer(net.access.local))[0] == 200


def test_узел_клиента_доходит_до_журнала_службы(net, fetch):
    """Шлюз называет переходнику узел клиента, переходник передаёт его службе поиска."""
    as_gateway = {"X-Flyarchive-Remote": PUBLIC, "X-Forwarded-For": "203.0.113.7", **net.access.bearer(net.access.read)}
    status, _, _ = fetch(net.mcp + "/mcp", headers=as_gateway, data=rpc("tools/call", {"name": "search_archive", "arguments": {"q": "перенос"}}))
    rec = [r for r in net.access.journal() if r["tool"] == "/api/search"][-1]
    assert status == 200 and (rec["ip"], rec["via"], rec["client"]) == ("203.0.113.7", "tailnet", "читатель")

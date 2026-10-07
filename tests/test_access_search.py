"""Вход в сервер поиска: FR-01, FR-04, FR-05, FR-06, FR-07, FR-09, FR-10."""
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import webui as W

DOC = "jira/IT/задача.md"
DOC_TEXT = "Согласовать перенос работ"


@pytest.fixture
def srv(serve, fetch, access, monkeypatch, tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "jira" / "IT").mkdir(parents=True)
    (corpus / "jira" / "IT" / "задача.md").write_text(DOC_TEXT, encoding="utf-8")
    (corpus / "jira" / "IT" / "другая.md").write_text("Другой документ", encoding="utf-8")
    monkeypatch.setattr(W, "CORPUS", str(corpus))
    calls = []

    def fake_search(q, k=10, since=None, source=None, space=None, **kw):
        calls.append(q)
        return [(0.5, {"path": DOC, "chunk": 0, "text": DOC_TEXT, "updated": "2026-08-31",
                       "title": "Задача", "source": "jira", "space": "IT"})]

    monkeypatch.setattr(W.S, "search", fake_search)
    monkeypatch.setattr(W.Handler, "guard", access.guard("search"))
    base = serve(W.Server, W.Handler)

    class Srv:
        pass

    s = Srv()
    s.base, s.calls, s.access = base, calls, access

    def get(path, token=None, headers=None, follow=True, **params):
        url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        h = dict(headers or {})
        if token:
            h.update(access.bearer(token))
        return fetch(url, headers=h, follow=follow)

    def local(url):
        """Ссылка из ответа ведёт на порт 8765 — в тесте сервер на случайном порту."""
        return base + url[len("http://127.0.0.1:8765"):]

    s.get, s.local, s.fetch = get, local, fetch
    return s


# ── без токена ──────────────────────────────────────────────────
@pytest.mark.parametrize("path, params", [
    ("/api/search", {"q": "перенос"}), ("/doc", {"p": DOC}), ("/openapi.json", {}), ("/", {}),
    ("/search", {"q": "перенос"}), ("/nope", {})])
def test_без_токена_401_и_никакого_содержимого(srv, path, params):
    status, headers, body = srv.get(path, **params)
    text = body.decode("utf-8")
    assert status == 401 and headers["WWW-Authenticate"] == "Bearer"
    assert set(json.loads(text)) == {"error"}
    assert DOC_TEXT not in text and "results" not in text and "operationId" not in text
    assert srv.calls == []


def test_без_токена_post_401(srv):
    status, _, _ = srv.fetch(srv.base + "/api/search", data={"q": "перенос"})
    assert status == 401 and srv.calls == []


@pytest.mark.parametrize("spoil", [lambda t: t[:-1], lambda t: t + "x", lambda t: "ba_" + "x" * 43, lambda t: t[3:]])
def test_неверный_токен_401(srv, spoil):
    status, _, body = srv.get("/api/search", token=spoil(srv.access.read), q="перенос")
    assert status == 401 and "results" not in body.decode("utf-8") and srv.calls == []


def test_схема_не_bearer_не_принимается(srv):
    status, _, _ = srv.get("/api/search", headers={"Authorization": "Basic " + srv.access.read}, q="перенос")
    assert status == 401


# ── с токеном ───────────────────────────────────────────────────
@pytest.mark.parametrize("level", ["read", "full", "local"])
def test_с_токеном_любого_уровня_поиск_работает(srv, level):
    status, _, body = srv.get("/api/search", token=getattr(srv.access, level), q="перенос")
    out = json.loads(body)
    assert status == 200 and out["count"] == 1 and out["results"][0]["path"] == DOC
    assert srv.calls == ["перенос"]


def test_с_токеном_документ_и_описание_открываются(srv):
    status, _, body = srv.get("/doc", token=srv.access.read, p=DOC)
    assert status == 200 and DOC_TEXT in body.decode("utf-8")
    assert srv.get("/openapi.json", token=srv.access.read)[0] == 200
    assert srv.get("/nope", token=srv.access.read)[0] == 404


def test_отзыв_действует_со_следующего_запроса(srv):
    assert srv.get("/api/search", token=srv.access.read, q="x")[0] == 200
    srv.access.store.revoke("читатель")
    assert srv.get("/api/search", token=srv.access.read, q="x")[0] == 401


# ── службы векторов за службой поиска нет ───────────────────────
# Службу векторов архив не открывает: токены архива её не защищают, и через порт поиска к ней не дойти. У службы поиска нет и маршрута
# с уровнем «полный»: любой путь отвечает токену «чтение» так же, как токену «полный» (отказ по уровню «чтение мало для создающих
# действий» держит сервер документов: test_access_office.py).
LOGINS = ["none", "read", "full", "local", "session"]


@pytest.fixture
def embed(monkeypatch):
    """Подставная служба векторов: считает обращения. Её адрес вписан туда, куда ударил бы проброс, если бы он вернулся."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def answer(self):
            size = int(self.headers.get("Content-Length") or 0)
            if size:
                self.rfile.read(size)
            seen.append((self.command, self.path))
            data = b'{"models": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = answer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(W, "OLLAMA_UP", url, raising=False)          # прежнее имя адреса проброса: настоящую службу тест не тронет и в красном прогоне
    monkeypatch.setattr(W.S, "OLLAMA", url + "/api/embed")
    yield seen
    server.shutdown()
    server.server_close()


def enter(srv, how):
    """Заголовки входа: без входа, токен одного из трёх уровней или сеанс страницы поиска."""
    if how == "session":
        return {"Cookie": login(srv)[2]["Set-Cookie"].split(";")[0]}
    token = {"read": srv.access.read, "full": srv.access.full, "local": srv.access.local}.get(how)
    return srv.access.bearer(token) if token else {}


def ask(srv, method, path, headers):
    """Что видит клиент: код, заголовок отказа входа и тело. POST несёт тело, как у запроса к службе векторов."""
    body = {"model": "x", "input": ["перенос"]} if method == "POST" else None
    status, got, data = srv.fetch(srv.base + path, data=body, headers=headers, method=method)
    return status, got.get("WWW-Authenticate"), data


def answers_as_unknown(srv, method, path, how):
    """Ответ на путь тот же, что на неизвестный `/nope` при том же входе; а тот — отказ входа без входа и «не найдено» при любом входе."""
    headers = enter(srv, how)
    got = ask(srv, method, path, headers)
    assert got == ask(srv, "GET", "/nope", headers)
    assert got[0] == (401 if how == "none" else 404) and (got[1] == "Bearer") == (how == "none")
    assert how == "none" or got[2] == "не найдено".encode("utf-8")


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("how", LOGINS)
@pytest.mark.parametrize("path", ["/ollama/api/tags", "/ollama/api/chat", "/ollama/", "/ollama/api/tags?stream=1"])
def test_путь_ollama_отвечает_как_неизвестный_и_служба_векторов_не_получает_запроса(srv, embed, method, how, path):
    answers_as_unknown(srv, method, path, how)
    assert embed == [] and srv.calls == []


@pytest.mark.parametrize("how", LOGINS)
@pytest.mark.parametrize("path", ["/api/search?q=" + urllib.parse.quote("перенос"), "/doc?p=" + urllib.parse.quote(DOC), "/openapi.json", "/",
                                  "/search?q=" + urllib.parse.quote("перенос"), "/login?c=x", "/nope"])
def test_post_на_службу_поиска_отвечает_как_на_неизвестный_путь_и_уровня_полный_не_просит(srv, how, path):
    """Маршрутов у метода нет: при любом входе, и токеном «чтение» тоже, это «не найдено», а не отказ по уровню; поиск не запускается."""
    answers_as_unknown(srv, "POST", path, how)
    assert srv.calls == []


# ── Host ────────────────────────────────────────────────────────
@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1.evil.example", "203.0.113.5:8765"])
def test_чужой_host_403_даже_с_верным_токеном(srv, host):
    status, _, body = srv.get("/api/search", token=srv.access.local, headers={"Host": host}, q="перенос")
    assert status == 403 and "Host" in json.loads(body)["error"] and srv.calls == []


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1", "LOCALHOST"])
def test_свой_host_проходит(srv, host):
    assert srv.get("/api/search", token=srv.access.read, headers={"Host": host}, q="перенос")[0] == 200


# ── журнал ──────────────────────────────────────────────────────
def test_успешный_поиск_записан_под_именем_клиента(srv):
    srv.get("/api/search", token=srv.access.read, q="перенос работ", k="5", source="jira")
    (rec,) = srv.access.journal()
    assert (rec["server"], rec["client"], rec["level"], rec["tool"]) == ("search", "читатель", "read", "/api/search")
    assert rec["params"] == {"q": "перенос работ", "k": "5", "source": "jira"}
    assert (rec["status"], rec["outcome"], rec["ip"]) == (200, "ok", "127.0.0.1")


def test_отказы_записаны_в_журнал(srv):
    srv.get("/api/search", q="без токена")
    srv.get("/api/search", token="ba_" + "x" * 43, q="чужой токен")
    srv.get("/api/search", token=srv.access.local, headers={"Host": "evil.example"}, q="чужой host")
    recs = srv.access.journal()
    assert [r["status"] for r in recs] == [401, 401, 403]
    assert all(r["outcome"].startswith("отказ: ") for r in recs)
    assert [r["client"] for r in recs] == ["-", "-", "-"]
    assert recs[0]["params"]["q"] == "без токена"


def test_значение_токена_в_журнал_не_попадает(srv):
    for tok in (srv.access.read, srv.access.read[:-1], "ba_" + "x" * 43):
        srv.get("/api/search", token=tok, q="запрос с " + tok)
    text = srv.access.journal_text()
    assert srv.access.read not in text and srv.access.read[:-1] not in text and "x" * 43 not in text
    assert "ba_***" in text


def test_сбой_поиска_записан_как_ошибка(srv, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ollama лежит")

    monkeypatch.setattr(W.S, "search", boom)
    assert srv.get("/api/search", token=srv.access.read, q="x")[0] == 500
    assert srv.access.journal()[-1]["outcome"] == "ошибка 500"


# ── подписанные ссылки ──────────────────────────────────────────
def link_from_search(srv):
    _, _, body = srv.get("/api/search", token=srv.access.read, q="перенос")
    return json.loads(body)["results"][0]["url"]


def test_ссылка_из_выдачи_открывается_без_токена(srv):
    url = link_from_search(srv)
    assert url.startswith("http://127.0.0.1:8765/doc?p=")
    status, _, body = srv.fetch(srv.local(url))
    assert status == 200 and DOC_TEXT in body.decode("utf-8")
    assert srv.access.journal()[-1]["client"] == "ссылка"


def test_подпись_не_открывает_другой_документ(srv):
    url = srv.local(link_from_search(srv))
    other = url.replace(urllib.parse.quote(DOC), urllib.parse.quote("jira/IT/другая.md"))
    status, _, body = srv.fetch(other)
    assert status == 403 and "Другой документ" not in body.decode("utf-8")


def test_изменённая_подпись_и_продлённый_срок_403(srv):
    url = srv.local(link_from_search(srv))
    parts = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    for change in ({"s": q["s"][:-1] + ("0" if q["s"][-1] != "0" else "1")}, {"e": str(int(q["e"]) + 86400)}, {"s": ""}):
        bad = parts._replace(query=urllib.parse.urlencode({**q, **change})).geturl()
        assert srv.fetch(bad)[0] == 403


def test_подпись_в_журнал_не_пишется(srv):
    url = srv.local(link_from_search(srv))
    srv.fetch(url)
    sig = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["s"]
    assert sig not in srv.access.journal_text()
    assert srv.access.journal()[-1]["params"] == {"p": DOC}


# ── страница поиска для человека ────────────────────────────────
def login(srv):
    code = srv.access.guard("cli").new_login_code()
    status, headers, _ = srv.get("/login", follow=False, c=code)
    return code, status, headers


def test_вход_по_одноразовой_ссылке_открывает_страницу(srv):
    code, status, headers = login(srv)
    assert status == 302 and headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert cookie.startswith("ba_session=") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    session = {"Cookie": cookie.split(";")[0]}
    status, _, body = srv.get("/search", headers=session, q="перенос")
    assert status == 200 and "Задача" in body.decode("utf-8")
    status, _, body = srv.get("/doc", headers=session, p=DOC)
    assert status == 200 and DOC_TEXT in body.decode("utf-8")
    assert code not in srv.access.journal_text()


def test_ссылка_входа_второй_раз_не_работает(srv):
    code, status, _ = login(srv)
    assert status == 302
    assert srv.get("/login", follow=False, c=code)[0] == 401


@pytest.mark.parametrize("code", ["", "чужой", "x" * 32])
def test_чужой_код_входа_401(srv, code):
    status, headers, _ = srv.get("/login", follow=False, c=code)
    assert status == 401 and "Set-Cookie" not in headers


def test_вход_с_чужого_host_403(srv):
    code = srv.access.guard("cli").new_login_code()
    assert srv.get("/login", follow=False, headers={"Host": "evil.example"}, c=code)[0] == 403


def test_сеанс_человека_не_открывает_api_для_моделей(srv):
    _, _, headers = login(srv)
    session = {"Cookie": headers["Set-Cookie"].split(";")[0]}
    assert srv.get("/api/search", headers=session, q="перенос")[0] == 401
    assert srv.get("/openapi.json", headers=session)[0] == 401


def test_чужой_cookie_не_пускает(srv):
    assert srv.get("/search", headers={"Cookie": "ba_session=" + "x" * 43}, q="перенос")[0] == 401


# ── негодный запрос: 400 с причиной, а не сбой сервера ──────────
def test_негодный_запрос_400_с_причиной_а_не_сбой_сервера(srv, monkeypatch):
    def strict(q, k=10, since=None, source=None, space=None, **kw):
        raise W.S.BadQuery("since: нужна дата вида 2026, 2026-08 или 2026-08-31")

    monkeypatch.setattr(W.S, "search", strict)
    status, _, body = srv.get("/api/search", token=srv.access.read, q="перенос", since="2030' OR '1'='1")
    assert status == 400 and "since: нужна дата" in json.loads(body)["error"]


SVG = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(document.cookie)</script><text>схема</text></svg>'
XHTML = '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><script>alert(1)</script></body></html>'


@pytest.mark.parametrize("name, data, sandbox", [
    ("схема.svg", SVG.encode("utf-8"), True), ("page.xhtml", XHTML.encode("utf-8"), True), ("данные.bin", bytes(range(256)), True),
    ("страница.mht", b"MIME-Version: 1.0", True), ("отчёт.pdf", b"%PDF-1.4", False), ("фото.png", b"\x89PNG\r\n\x1a\n", False),
    ("фото.jpg", b"\xff\xd8\xff", False)])
def test_файл_как_есть_отдаётся_без_права_исполнять_сценарии(srv, name, data, sandbox):
    """SVG и XHTML браузер открывает как страницу на том же адресе, где живёт сеанс владельца."""
    with open(os.path.join(W.CORPUS, "jira", "IT", name), "wb") as f:
        f.write(data)
    status, headers, body = srv.get("/doc", token=srv.access.read, p="jira/IT/" + name)
    assert status == 200 and body == data and headers.get("X-Content-Type-Options") == "nosniff"
    assert (headers.get("Content-Security-Policy") == "sandbox") is sandbox


def test_текстовый_документ_отдаётся_страницей_с_запретом_угадывать_тип(srv):
    with open(os.path.join(W.CORPUS, "jira", "IT", "страница.html"), "w", encoding="utf-8") as f:
        f.write("<html><script>alert(1)</script></html>")
    status, headers, body = srv.get("/doc", token=srv.access.read, p="jira/IT/страница.html")
    text = body.decode("utf-8")
    assert status == 200 and "&lt;script&gt;alert(1)" in text and "<script>alert(1)" not in text
    assert headers.get("X-Content-Type-Options") == "nosniff"

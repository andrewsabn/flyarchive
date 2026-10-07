"""Вход в сервер документов: FR-01, FR-04, FR-06, FR-07, FR-09."""
import json
import os
import urllib.parse

import pytest

import office_server as O

DOC_TEXT = "Привет, архив"
DOCX = {"kind": "docx", "title": "Отчёт", "blocks": [{"type": "text", "value": "Абзац"}]}


@pytest.fixture
def srv(serve, fetch, access, monkeypatch, tmp_path):
    corpus, out = tmp_path / "corpus", tmp_path / "out"
    corpus.mkdir()
    out.mkdir()
    (corpus / "заметка.txt").write_text(DOC_TEXT, encoding="utf-8")
    (out / "chart-101010-abc123.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(O, "CORPUS", str(corpus))
    monkeypatch.setattr(O, "OUT", str(out))
    monkeypatch.setattr(O.Handler, "guard", access.guard("office"))
    base = serve(O.Server, O.Handler)

    class Srv:
        pass

    s = Srv()
    s.base, s.out, s.access = base, out, access

    def call(route, token=None, data=None, headers=None, **params):
        url = base + route + ("?" + urllib.parse.urlencode(params) if params else "")
        h = dict(headers or {})
        if token:
            h.update(access.bearer(token))
        return fetch(url, data=data, headers=h)

    s.call, s.fetch = call, fetch
    s.local = lambda url: base + url[len("http://127.0.0.1:8766"):]
    s.made = lambda: sorted(n for n in os.listdir(out) if not n.startswith("chart-101010"))
    return s


GETS = [("/read", {"path": "заметка.txt"}), ("/rich", {"path": "заметка.txt"}),
        ("/file", {"n": "chart-101010-abc123.png"}), ("/openapi.json", {}), ("/", {}), ("/nope", {})]
POSTS = [("/chart", {"kind": "bar", "title": "x", "labels": ["a"], "series": {"s": [1]}}),
         ("/diagram", {"dot": "digraph G { a -> b }"}),
         ("/landscape", {"groups": [{"name": "А", "nodes": ["Б"]}]}),
         ("/document", DOCX)]


# ── без токена ──────────────────────────────────────────────────
@pytest.mark.parametrize("path, params", GETS)
def test_чтение_без_токена_401(srv, path, params):
    status, headers, body = srv.call(path, **params)
    text = body.decode("utf-8", "replace")
    assert status == 401 and headers["WWW-Authenticate"] == "Bearer"
    assert DOC_TEXT not in text and "PNG" not in text and "operationId" not in text


@pytest.mark.parametrize("path, data", POSTS)
def test_создание_без_токена_401_и_файл_не_создан(srv, path, data):
    assert srv.call(path, data=data)[0] == 401
    assert srv.made() == []


# ── уровень «чтение» ────────────────────────────────────────────
def test_чтение_токеном_уровня_чтение(srv):
    status, _, body = srv.call("/read", token=srv.access.read, path="заметка.txt")
    assert status == 200 and json.loads(body)["text"] == DOC_TEXT
    assert srv.call("/openapi.json", token=srv.access.read)[0] == 200
    assert srv.call("/file", token=srv.access.read, n="chart-101010-abc123.png")[0] == 200


@pytest.mark.parametrize("path, data", POSTS)
def test_создание_токеном_уровня_чтение_403_и_файл_не_создан(srv, path, data):
    status, _, body = srv.call(path, token=srv.access.read, data=data)
    err = json.loads(body)["error"]
    assert status == 403 and "full" in err and "read" in err
    assert srv.made() == []


# ── уровень «полный» ────────────────────────────────────────────
@pytest.mark.parametrize("level", ["full", "local"])
def test_создание_полным_токеном(srv, level):
    pytest.importorskip("docx")
    status, _, body = srv.call("/document", token=getattr(srv.access, level), data=DOCX)
    out = json.loads(body)
    assert status == 200 and "error" not in out
    assert len(srv.made()) == 1 and srv.made()[0].endswith(".docx")


def test_ссылка_на_созданный_файл_открывается_без_токена(srv):
    pytest.importorskip("docx")
    _, _, body = srv.call("/document", token=srv.access.full, data=DOCX)
    url = json.loads(body)["url"]
    assert url.startswith("http://127.0.0.1:8766/file?n=") and "&e=" in url and "&s=" in url
    status, _, data = srv.fetch(srv.local(url))
    assert status == 200 and data[:2] == b"PK"
    assert srv.access.journal()[-1]["client"] == "ссылка"


def test_подпись_не_открывает_другой_файл(srv):
    pytest.importorskip("docx")
    _, _, body = srv.call("/document", token=srv.access.full, data=DOCX)
    url = srv.local(json.loads(body)["url"])
    name = srv.made()[0]
    assert srv.fetch(url.replace(urllib.parse.quote(name), "chart-101010-abc123.png"))[0] == 403
    assert srv.fetch(url[:-1] + ("0" if url[-1] != "0" else "1"))[0] == 403


def test_ссылка_в_разметке_графика_подписана(srv):
    pytest.importorskip("matplotlib")
    _, _, body = srv.call("/chart", token=srv.access.full, data=POSTS[0][1])
    out = json.loads(body)
    link = out["markdown"][out["markdown"].index("(") + 1:-1]
    assert link == out["url"] and "&s=" in link
    assert srv.fetch(srv.local(link))[0] == 200


# ── Host и журнал ───────────────────────────────────────────────
def test_чужой_host_403(srv):
    status, _, _ = srv.call("/read", token=srv.access.local, headers={"Host": "evil.example"}, path="заметка.txt")
    assert status == 403
    assert srv.call("/document", token=srv.access.local, headers={"Host": "evil.example"}, data=DOCX)[0] == 403
    assert srv.made() == []


def test_журнал_создание_и_отказ(srv):
    pytest.importorskip("docx")
    srv.call("/document", token=srv.access.full, data=DOCX)
    srv.call("/document", token=srv.access.read, data=DOCX)
    srv.call("/read", token=srv.access.read, path="заметка.txt", pages="1-2")
    ok, denied, read = srv.access.journal()
    assert (ok["client"], ok["tool"], ok["status"], ok["params"]) == ("писатель", "/document", 200, {"kind": "docx", "title": "Отчёт"})
    assert (denied["client"], denied["status"]) == ("читатель", 403) and denied["outcome"].startswith("отказ: ")
    assert (read["tool"], read["params"]) == ("/read", {"path": "заметка.txt", "pages": "1-2"})
    assert srv.access.full not in srv.access.journal_text()


def test_пометка_что_вызов_пришёл_через_mcp(srv):
    srv.call("/read", token=srv.access.read, headers={"X-Flyarchive-Via": "mcp"}, path="заметка.txt")
    assert srv.access.journal()[-1]["via"] == "mcp"

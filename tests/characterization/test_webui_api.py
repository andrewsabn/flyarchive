"""Фиксация нынешнего поведения сервера поиска (webui.py).

Запросы идут с токеном локального DSH: вход проверяют tests/test_access_search.py.
Тест со словом «сейчас» описывает поведение, которое будущая правка изменит намеренно
(ссылки на жёстко заданный адрес); он будет заменён тестом требования, а не починен.
"""
import json
import os
import urllib.parse

import pytest

import corpus_path
import webui as W


def hit(path, text="текст", score=0.0123456, **kw):
    r = {"path": path, "chunk": 0, "text": text, "updated": "2026-08-31",
         "title": "Заголовок", "source": "jira", "space": "IT"}
    r.update(kw)
    return score, r


class Api:
    def __init__(self, base, fetch, headers):
        self.base, self._fetch, self.headers = base, fetch, headers
        self.calls, self.hits, self.error = [], [], None

    def get(self, path, **params):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._fetch(url, headers=self.headers)

    def json(self, path, **params):
        status, _, body = self.get(path, **params)
        return status, json.loads(body.decode("utf-8"))


@pytest.fixture
def api(serve, fetch, monkeypatch, access):
    a = Api(None, fetch, access.bearer(access.local))
    monkeypatch.setattr(W.Handler, "guard", access.guard("search"))

    def fake_search(q, k=10, since=None, source=None, space=None, **kw):
        a.calls.append({"q": q, "k": k, "since": since, "source": source, "space": space})
        if a.error:
            raise a.error
        return a.hits

    monkeypatch.setattr(W.S, "search", fake_search)
    a.base = serve(W.Server, W.Handler)
    return a


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    (root / "jira" / "IT").mkdir(parents=True)
    (root / "jira" / "IT" / "задача.md").write_text("Текст <задачи> & прочее", encoding="utf-8")
    (root / "jira" / "IT" / "скан.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "снаружи.txt").write_text("секрет", encoding="utf-8")
    monkeypatch.setattr(W, "CORPUS", str(root))
    return root


# ── /api/search ─────────────────────────────────────────────────
def test_без_запроса_отказ_400(api):
    status, body = api.json("/api/search")
    assert status == 400
    assert body == {"error": "нужен параметр q"}
    assert api.calls == []


def test_форма_ответа(api):
    api.hits = [hit("jira/IT/задача.md", text="содержимое")]
    status, body = api.json("/api/search", q="реестр")
    assert status == 200
    assert body["query"] == "реестр" and body["count"] == 1
    (res,) = body["results"]
    url = res.pop("url")
    assert res == {"score": 0.01235, "title": "Заголовок", "date": "2026-08-31", "base": "jira",
                   "space": "IT", "path": "jira/IT/задача.md", "text": "содержимое"}
    assert url.startswith("http://127.0.0.1:8765/doc?p=" + urllib.parse.quote("jira/IT/задача.md") + "&e=")


def test_служба_поиска_даёт_ссылку_на_петлю_и_порт_8765(api):
    """Сама служба поиска всегда даёт ссылку на петлю: она слушает только её.
    Клиенту с другой машины адрес меняет переходник MCP (tests/test_gateway.py)."""
    api.hits = [hit("a.md")]
    _, body = api.json("/api/search", q="x")
    assert body["results"][0]["url"].startswith("http://127.0.0.1:8765/doc?p=")
    assert not api.base.endswith(":8765")


@pytest.mark.parametrize("k, passed", [
    (None, 10), ("7", 7), ("20", 20), ("21", 20), ("50", 20),
    ("0", 1), ("-3", 1), ("abc", 10), ("", 10),
])
def test_число_результатов_зажато_от_одного_до_двадцати(api, k, passed):
    params = {"q": "x"} if k is None else {"q": "x", "k": k}
    api.json("/api/search", **params)
    assert api.calls[-1]["k"] == passed


def test_фильтры_передаются_поиску(api):
    api.json("/api/search", q="x", since="2025", source="jira,confluence", space="DEMO")
    assert api.calls[-1] == {"q": "x", "k": 10, "since": "2025",
                             "source": "jira,confluence", "space": "DEMO"}


def test_пустые_фильтры_превращаются_в_отсутствие_фильтра(api):
    api.json("/api/search", q="x", since="", source="  ", space="")
    c = api.calls[-1]
    assert (c["since"], c["source"], c["space"]) == (None, None, None)


def test_фрагмент_сжат_по_пробелам_и_обрезан(api):
    api.hits = [hit("a.md", text="слово\n\n\tслово   " * 200)]
    _, body = api.json("/api/search", q="x")
    text = body["results"][0]["text"]
    assert len(text) == W.API_SNIPPET == 420
    assert "\n" not in text and "\t" not in text and "  " not in text


def test_бюджет_ответа_хвост_находок_без_текста(api):
    """14 фрагментов по 420 символов помещаются в бюджет 6000, пятнадцатый нет.
    Находки при этом не теряются — у них пустеет текст."""
    api.hits = [hit(f"d{i}.md", text="я" * 1000) for i in range(20)]
    _, body = api.json("/api/search", q="x", k="20")
    lengths = [len(r["text"]) for r in body["results"]]
    assert body["count"] == 20
    assert lengths == [420] * 14 + [0] * 6
    assert sum(lengths) <= W.API_BUDGET == 6000


def test_сбой_поиска_отдаётся_как_500_с_причиной(api):
    api.error = RuntimeError("ollama лежит")
    status, body = api.json("/api/search", q="x")
    assert status == 500
    assert body == {"error": "RuntimeError: ollama лежит"}


def test_неизвестный_адрес_404(api):
    status, _, _ = api.get("/nope")
    assert status == 404


def test_описание_для_модели(api):
    status, spec = api.json("/openapi.json")
    assert status == 200
    op = spec["paths"]["/api/search"]["get"]
    assert op["operationId"] == "search_archive"
    assert [p["name"] for p in op["parameters"]] == ["q", "k", "source", "space", "since"]
    assert [p["name"] for p in op["parameters"] if p["required"]] == ["q"]


# ── разбор пути к документу ─────────────────────────────────────
def test_путь_внутри_корпуса_находится(corpus):
    assert W.resolve("jira/IT/задача.md") == str(corpus / "jira" / "IT" / "задача.md")


def test_путь_с_обратным_слэшем_находится(corpus):
    assert W.resolve("jira\\IT\\задача.md") == str(corpus / "jira" / "IT" / "задача.md")


def test_старое_имя_корня_подменяется(corpus, monkeypatch):
    monkeypatch.setattr(corpus_path, "ALIAS", {"sample_jira_export": "jira"})          # старое имя корня — из таблицы источников (FR-99)
    assert W.resolve("sample_jira_export/IT/задача.md") == str(corpus / "jira" / "IT" / "задача.md")


@pytest.mark.parametrize("rel", [
    "../снаружи.txt",
    "jira/../../снаружи.txt",
    "..\\снаружи.txt",
    "/etc/passwd",
    "jira/IT",                 # каталог, не файл
    "jira/IT/нет.md",
    "",
])
def test_выход_за_корпус_и_несуществующее_не_находятся(corpus, rel):
    assert W.resolve(rel) is None


# ── /doc ────────────────────────────────────────────────────────
def test_текстовый_документ_отдаётся_страницей_с_экранированием(api, corpus):
    status, headers, body = api.get("/doc", p="jira/IT/задача.md")
    html = body.decode("utf-8")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Текст &lt;задачи&gt; &amp; прочее" in html


def test_двоичный_документ_отдаётся_как_есть(api, corpus):
    status, headers, body = api.get("/doc", p="jira/IT/скан.pdf")
    assert status == 200
    assert headers["Content-Type"] == "application/pdf"
    assert body == b"%PDF-1.4 fake"


def test_документ_вне_корпуса_404(api, corpus):
    status, _, body = api.get("/doc", p="../снаружи.txt")
    assert status == 404
    assert "секрет" not in body.decode("utf-8")


def test_очередь_подключений_расширена():
    assert W.Server.request_queue_size == 128
    assert os.path.basename(W.__file__) == "webui.py"

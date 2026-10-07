"""Поиск без библиотеки, без таблицы индекса и без службы векторов отвечает сообщением, а не трассировкой (FR-107).

Командная строка (`python3 tools/search.py`) — настоящий процесс в выдуманной машине: отсутствие библиотеки — блокировка импорта в дочернем
процессе, а не удаление. Служба поиска (tools/webui.py) — настоящий обработчик на случайном порту. Ответ службы — объект {"error": текст, "code": код}:
`error` — строка, как у всех служб (переходник MCP читает её), `code` — код сообщения из каталога. Код ответа — по смыслу: нет библиотеки или таблицы — 503
(служба не может ответить, пока это не поставят и не заведут), нет ответа службы векторов — 502. Живая служба векторов не трогается никогда.
Что поиск при установленных библиотеках ничего не потерял — те же тесты ранжирования (tests/test_search_input.py, tests/characterization).
"""
import json
import os
import urllib.parse

import pytest

import ingest
import messages as M
import search as S
import webui as W
from archivekit import DEAD_VECTORS, Archive
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

QUERY = "серверной"


@pytest.fixture
def archive(tmp_path, vectors):
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})


def no_trace(text):
    assert "Traceback" not in text and "File \"" not in text, text


# ── командная строка ────────────────────────────────────────────
def test_без_lancedb_команда_называет_библиотеку_и_что_поставить_код_не_ноль_трассировки_нет(archive):
    code, out, err = archive.tool("search.py", QUERY, **archive.hide("lancedb"))
    assert code == 1 and out == ""
    no_trace(err)
    assert err == "ошибка: " + str(M.make("lib.missing", package="lancedb", use="index", file="requirements.txt")) + "\n"
    assert "python3 -m pip install -r requirements.txt" in err


def test_без_lancedb_команда_bases_тоже_отвечает_сообщением(archive):
    code, out, err = archive.tool("search.py", "--bases", **archive.hide("lancedb"))
    assert code == 1 and out == "" and "lancedb" in err and "requirements.txt" in err
    no_trace(err)


def test_без_таблицы_индекса_команда_называет_init_и_ничего_не_создаёт(archive):
    code, out, err = archive.tool("search.py", QUERY)
    assert code == 1 and out == ""
    no_trace(err)
    assert err == "ошибка: " + str(M.make("index.no_table")) + "\n" and "flyarchive init" in err
    assert not os.path.exists(archive.home), "поиск по незаведённому архиву завёл каталог индекса с правами по умолчанию"


def test_каталог_индекса_есть_а_таблицы_нет_тот_же_отказ_и_таблица_не_заводится(archive):
    os.makedirs(archive.path("index", "lance"))
    code, out, err = archive.tool("search.py", QUERY)
    assert code == 1 and out == "" and err == "ошибка: " + str(M.make("index.no_table")) + "\n"
    assert os.listdir(archive.path("index", "lance")) == []


def test_без_таблицы_индекса_bases_тот_же_отказ(archive):
    code, out, err = archive.tool("search.py", "--bases")
    assert code == 1 and out == "" and "flyarchive init" in err
    no_trace(err)


def test_без_службы_векторов_команда_называет_адрес_и_ollama_pull_код_не_ноль_трассировки_нет(archive):
    assert archive("init")[0] == 0
    code, out, err = archive.tool("search.py", QUERY, FLYARCHIVE_EMBED_URL=DEAD_VECTORS)
    assert code == 1 and out == ""
    no_trace(err)
    assert err.startswith("ошибка: служба векторов не отвечает: " + DEAD_VECTORS) and "ollama pull bge-m3" in err and "embed_url" in err


def test_служба_векторов_ответила_ошибкой_http_причина_названа_кодом_ответа(tmp_path, monkeypatch):
    import urllib.error
    home = str(tmp_path / "архив")
    assert ingest.create_table(home, dim=4)
    monkeypatch.setattr(S, "DB", ingest.index_dir(home))

    def refuse(text):
        raise urllib.error.HTTPError(S.OLLAMA, 404, "Not Found", {}, None)

    monkeypatch.setattr(S, "embed", refuse)
    with pytest.raises(S.SearchError) as e:
        S.search("запрос")
    assert e.value.message.code == "embed.down" and e.value.message.args["why"] == "HTTP 404" and e.value.status == 502


def test_поиск_по_заведённому_пустому_архиву_отвечает_ничего_не_найдено_и_нулём(archive, vectors):
    assert archive("init")[0] == 0
    asked = len(vectors["requests"])
    code, out, err = archive.tool("search.py", QUERY)
    assert code == 0 and out == "ничего не найдено\n" and err == ""
    assert len(vectors["requests"]) == asked + 1


# ── служба поиска ───────────────────────────────────────────────
@pytest.fixture
def srv(serve, fetch, access, monkeypatch, tmp_path):
    """Настоящий обработчик поиска без подставной функции поиска; индекс и служба векторов — по умолчанию пустые и недостижимые."""
    monkeypatch.setattr(S, "DB", str(tmp_path / "нет-индекса"))
    monkeypatch.setattr(S, "OLLAMA", DEAD_VECTORS)
    monkeypatch.setattr(W.Handler, "guard", access.guard("search"))
    base = serve(W.Server, W.Handler)

    class Srv:
        pass

    s = Srv()
    s.base, s.access, s.tmp = base, access, tmp_path

    def get(path, **params):
        url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        return fetch(url, headers=access.bearer(access.read))

    s.get = get
    return s


def refusal(srv, status, code):
    got, headers, body = srv.get("/api/search", q=QUERY)
    text = body.decode("utf-8")
    assert got == status, text
    no_trace(text)
    data = json.loads(text)
    assert set(data) == {"error", "code"} and isinstance(data["error"], str) and data["code"] == code, data
    assert headers["Content-Type"].startswith("application/json")
    return data


def test_служба_без_lancedb_отвечает_503_с_кодом_и_что_поставить(srv, monkeypatch):
    monkeypatch.setattr(S, "lancedb", None)
    data = refusal(srv, 503, "lib.missing")
    assert data["error"] == str(M.make("lib.missing", package="lancedb", use="index", file="requirements.txt"))


def test_служба_без_таблицы_индекса_отвечает_503_и_называет_init_каталог_индекса_не_создан(srv):
    data = refusal(srv, 503, "index.no_table")
    assert data["error"] == str(M.make("index.no_table")) and "flyarchive init" in data["error"]
    assert not os.path.exists(S.DB)


def test_служба_без_службы_векторов_отвечает_502_с_адресом_и_подсказкой(srv, tmp_path, monkeypatch):
    home = str(tmp_path / "архив")
    assert ingest.create_table(home, dim=4)
    monkeypatch.setattr(S, "DB", ingest.index_dir(home))
    data = refusal(srv, 502, "embed.down")
    assert DEAD_VECTORS in data["error"] and "ollama pull" in data["error"] and "embed_url" in data["error"]


def test_ответ_службы_читается_переходником_mcp_как_отказ_с_причиной(srv, monkeypatch):
    import mcp_server

    class Refused:
        code = 503

        def read(self):
            return srv.get("/api/search", q=QUERY)[2]

    monkeypatch.setattr(S, "lancedb", None)
    text = mcp_server._refusal(Refused())
    assert text.startswith("сбой архива (код 503): нет библиотеки lancedb")


def test_страница_поиска_без_библиотеки_показывает_сообщение_а_не_трассировку(srv, monkeypatch, fetch):
    monkeypatch.setattr(S, "lancedb", None)
    cookie_page = srv.get("/", q=QUERY)
    text = cookie_page[2].decode("utf-8")
    assert cookie_page[0] == 503 and "нет библиотеки lancedb" in text and "requirements.txt" in text
    no_trace(text)


def test_неисправный_запрос_и_сбой_архива_отвечают_как_раньше(srv, monkeypatch):
    got, _, body = srv.get("/api/search", q=QUERY, since="вчера")
    assert got == 400 and "since" in json.loads(body)["error"]

    def boom(*a, **k):
        raise RuntimeError("что-то другое")

    monkeypatch.setattr(S, "search", boom)
    got, _, body = srv.get("/api/search", q=QUERY)
    assert got == 500 and json.loads(body) == {"error": "RuntimeError: что-то другое"}


def test_служба_запускается_без_lancedb_и_значит_способна_ответить(archive):
    code, out, err = archive.tool("webui.py", "--selftest", **archive.hide("lancedb"))
    assert code == 0 and out.strip() == "selftest ok", err
    no_trace(err)
    code, out, err = archive.tool("search.py", "--selftest", **archive.hide("lancedb"))
    assert code == 0 and out.strip() == "selftest ok", err


# ── «таблицы нет» не зависит от того, каким исключением библиотека об этом говорит (версии lancedb различаются) ──
class _Fake:
    """Подставная библиотека индекса: connect отдаёт соединение, у которого open_table роняет заданное исключение."""

    def __init__(self, error):
        self.error = error

    def connect(self, path):
        error = self.error

        class Db:
            def open_table(self, name):
                raise error

        return Db()


@pytest.mark.parametrize("error", [ValueError("Table 'docs' was not found"), FileNotFoundError("docs"), RuntimeError("table not found"),
                                   LookupError("нет таблицы"), type("TableNotFoundError", (Exception,), {})("docs")])
def test_таблицы_нет_отказ_с_подсказкой_init_при_любом_виде_исключения_библиотеки(tmp_path, monkeypatch, error):
    monkeypatch.setattr(S, "DB", str(tmp_path / "lance"))
    os.makedirs(S.DB)
    monkeypatch.setattr(S, "lancedb", _Fake(error))
    with pytest.raises(S.SearchError) as e:
        S.open_table()
    assert e.value.message.code == "index.no_table" and e.value.status == 503


@pytest.mark.parametrize("error", [ValueError("битая таблица"), RuntimeError("манифест испорчен"), OSError("диск")])
def test_таблица_есть_но_не_открывается_это_не_таблицы_нет_ошибка_библиотеки_как_есть(tmp_path, monkeypatch, error):
    monkeypatch.setattr(S, "DB", str(tmp_path / "lance"))
    os.makedirs(os.path.join(S.DB, "docs.lance"))
    monkeypatch.setattr(S, "lancedb", _Fake(error))
    with pytest.raises(type(error)):
        S.open_table()

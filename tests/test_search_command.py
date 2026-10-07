"""Поиск командой архива: `flyarchive search ЗАПРОС` (FR-107).

Тот же поиск и тот же вывод, что у сценария `python3 tools/search.py`, и те же отказы с кодами: нет библиотеки, таблицы индекса или службы векторов —
строка «ошибка: …» в stderr и код 1, без трассировки. Ключи те же, что у сценария: -k, --source, --since, --space, --today, --full; плюс --json для
программ. Сценарий остаётся рабочим. Настоящая LanceDB, малая таблица во временном каталоге, служба векторов — подставная (одинаковый вектор на всё:
порядок в выдаче решает полнотекстовая часть). Живая служба векторов и живой архив не трогаются.
"""
import json
import os
import subprocess
import sys

import pytest

import ingest
import messages as M
from archivekit import DEAD_VECTORS, FLYARCHIVE, Archive
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

QUERY = "перенос релиза"
DOCS = [  # (путь, база, раздел, дата, текст)
    ("входящие/п1/протокол.txt", "входящие", "п1", "2026-09-01", "Протокол совещания: перенос релиза на следующую неделю, ответственный назначен."),
    ("wiki/раздел/заметка.txt", "wiki", "раздел", "2025-01-10", "Заметка про архитектуру хранилища и резервные копии, релиза здесь нет."),
    ("wiki/раздел/план.txt", "wiki", "раздел", "2026-03-05", "План миграции хранилища: перенос данных по этапам."),
]


@pytest.fixture
def archive(tmp_path, vectors):
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})


@pytest.fixture
def filled(archive):
    """Заведённый архив с тремя документами в таблице индекса (одинаковые векторы, как отвечает подставная служба)."""
    assert archive("init")[0] == 0
    archive.table().add([{"path": path, "source": source, "space": space, "title": os.path.basename(path), "updated": updated, "url": "",
                          "chunk": 0, "text": text, "vector": [0.25] * ingest.DIM} for path, source, space, updated, text in DOCS])
    return archive


def command(archive, *args, **extra):
    return archive("search", *args, **extra)


def script(archive, *args, **extra):
    return archive.tool("search.py", *args, **extra)


def no_trace(text):
    assert "Traceback" not in text and "File \"" not in text, text


def paths(out):
    """Пути находок из вывода: вторая строка каждой находки."""
    return [line.strip().split("  #")[0] for line in out.splitlines() if line.startswith("    ") and "/" in line.split()[0]]


# ── поиск находит документ ──────────────────────────────────────
def test_команда_находит_документ_и_печатает_первым_тот_где_есть_слова_запроса(filled):
    code, out, err = command(filled, QUERY)
    assert code == 0 and err == "", err
    assert paths(out)[0] == "входящие/п1/протокол.txt"
    assert out.startswith(" 1. [") and "2026-09-01" in out and "протокол.txt" in out and "перенос релиза" in out


def test_вывод_команды_тот_же_что_у_сценария_search_py_байт_в_байт(filled):
    for args in ((QUERY,), (QUERY, "-k", "2", "--source", "wiki", "--today", "20261006"), ("хранилища", "--since", "2026", "--space", "раздел"),
                 (QUERY, "--full")):
        mine, theirs = command(filled, *args), script(filled, *args)
        assert mine == theirs and mine[0] == 0, args


def test_сценарий_search_py_остаётся_рабочим(filled):
    code, out, err = script(filled, QUERY)
    assert code == 0 and err == "" and paths(out)[0] == "входящие/п1/протокол.txt"


def test_ключ_k_ограничивает_число_находок(filled):
    code, out, _ = command(filled, "хранилища", "-k", "1")
    assert code == 0 and len(paths(out)) == 1
    code, out, _ = command(filled, "хранилища", "-k", "5")
    assert len(paths(out)) >= 2


def test_ключ_source_оставляет_только_названные_базы_через_запятую(filled):
    code, out, _ = command(filled, "хранилища", "--source", "wiki")
    assert code == 0 and paths(out) and all(p.startswith("wiki/") for p in paths(out))
    code, out, _ = command(filled, "хранилища", "--source", "входящие")
    assert not any(p.startswith("wiki/") for p in paths(out))
    code, out, _ = command(filled, QUERY, "--source", "входящие,wiki")
    assert {p.split("/")[0] for p in paths(out)} == {"входящие", "wiki"}


def test_ключ_since_отсекает_старые_документы(filled):
    code, out, _ = command(filled, "хранилища", "--since", "2026")
    assert code == 0 and set(paths(out)) == {"входящие/п1/протокол.txt", "wiki/раздел/план.txt"}, "заметка 2025 года отсечена"


def test_ключ_full_печатает_текст_фрагмента_целиком(filled):
    code, out, _ = command(filled, QUERY, "--full")
    assert "Протокол совещания: перенос релиза на следующую неделю, ответственный назначен.\n" in out


def test_ничего_не_найдено_строка_и_код_ноль(filled):
    code, out, err = command(filled, "запрос", "--source", "нет-такой-базы")
    assert code == 0 and out == "ничего не найдено\n" and err == ""


def test_json_отдаёт_находки_для_программ(filled):
    code, out, err = command(filled, QUERY, "--json", "-k", "2")
    assert code == 0 and err == "" and len(out.strip().splitlines()) == 1
    data = json.loads(out)
    assert data["query"] == QUERY and data["count"] == len(data["results"]) == 2
    first = data["results"][0]
    assert set(first) == {"score", "title", "date", "base", "space", "path", "chunk", "text"}
    assert (first["path"], first["base"], first["date"], first["chunk"]) == ("входящие/п1/протокол.txt", "входящие", "2026-09-01", 0)
    assert "перенос релиза" in first["text"]


def test_json_без_находок_пустой_список_и_код_ноль(filled):
    code, out, _ = command(filled, "запрос", "--source", "нет-такой-базы", "--json")
    assert code == 0 and json.loads(out) == {"query": "запрос", "count": 0, "results": []}


# ── отказы те же, что у сценария ────────────────────────────────
def test_без_таблицы_индекса_отказ_с_кодом_как_у_сценария_и_ничего_не_создаётся(archive):
    code, out, err = command(archive, QUERY)
    assert code == 1 and out == ""
    no_trace(err)
    assert err == "ошибка: " + str(M.make("index.no_table")) + "\n" and "flyarchive init" in err
    assert (code, out, err) == script(archive, QUERY), "отказ команды отличается от отказа сценария"
    assert not os.path.exists(archive.home), "поиск по незаведённому архиву завёл каталог"


def test_каталог_индекса_пуст_а_таблицы_нет_тот_же_отказ(archive):
    os.makedirs(archive.path("index", "lance"))
    code, out, err = command(archive, QUERY)
    assert (code, out, err) == (1, "", "ошибка: " + str(M.make("index.no_table")) + "\n")
    assert os.listdir(archive.path("index", "lance")) == []


def test_без_lancedb_отказ_называет_библиотеку_и_что_поставить_как_у_сценария(archive):
    hidden = archive.hide("lancedb")
    code, out, err = command(archive, QUERY, **hidden)
    assert code == 1 and out == ""
    no_trace(err)
    assert err == "ошибка: " + str(M.make("lib.missing", package="lancedb", use="index", file="requirements.txt")) + "\n"
    assert (code, out, err) == script(archive, QUERY, **hidden)


def test_без_службы_векторов_отказ_называет_адрес_и_ollama_pull_как_у_сценария(filled):
    code, out, err = command(filled, QUERY, FLYARCHIVE_EMBED_URL=DEAD_VECTORS)
    assert code == 1 and out == ""
    no_trace(err)
    assert err.startswith("ошибка: служба векторов не отвечает: " + DEAD_VECTORS) and "ollama pull" in err and "embed_url" in err
    assert (code, out, err) == script(filled, QUERY, FLYARCHIVE_EMBED_URL=DEAD_VECTORS)


def test_негодная_дата_отказ_строкой_а_не_трассировкой(filled):
    code, out, err = command(filled, QUERY, "--since", "вчера")
    assert code == 1 and out == ""
    no_trace(err)
    assert err.startswith("ошибка: since: нужна дата") and err.count("\n") == 1


def test_отказ_с_json_объект_с_кодом_в_последней_строке_stderr(archive):
    code, out, err = command(archive, QUERY, "--json")
    assert code == 1 and out == ""
    error = json.loads(err.strip().splitlines()[-1])["error"]
    assert error["code"] == "index.no_table" and "flyarchive init" in error["text"]
    code, out, err = command(archive, QUERY, "--json", **archive.hide("lancedb"))
    assert code == 1 and json.loads(err.strip().splitlines()[-1])["error"]["code"] == "lib.missing"


def test_справка_называет_ключи_и_сценарий_с_командой_делят_один_разбор_вывода():
    r = subprocess.run([sys.executable, FLYARCHIVE, "search", "--help"], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0
    for key in ("-k", "--source", "--since", "--space", "--today", "--full", "--json"):
        assert key in r.stdout, key

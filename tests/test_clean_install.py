"""Архив с пустого каталога до найденного документа, без единого шага вне команд архива (FR-103).

Команда настоящая (процесс, файл tools/flyarchive), каталог архива и домашний каталог — пустые временные. Вместо службы векторов — сервер теста
(настройка embed_url), вместо systemctl — заглушка в PATH, проверка моделью выключена (`--llm off`). Настоящая таблица LanceDB.
"""
import hashlib
import json
import os

import pytest

import doctor
import gatekit as K
import known as N
from archivekit import Archive, error_line
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

# образец — текст: он индексируется без библиотек по форматам (docx без python-docx не индексируется, а этим тестам формат не важен)
CLEAN = "Договор поставки оборудования для серверной.".encode("utf-8")
NOTE = "Согласовать перенос работ на четверг."


@pytest.fixture
def archive(tmp_path, vectors):
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})


def setup_inbox(archive):
    """Входящая папка, проверка моделью выключена, файлы берутся без выстойки."""
    code, out, err = archive("inbox", "set", "--path", archive.box, "--llm", "off", "--cloud", "off", "--period", "30")
    assert code == 0, err
    archive.instant()


def batch_of(archive):
    return json.loads(archive("inbox", "batches", "--json")[1])["batches"][0]["id"]


def install(archive):
    """Весь путь от пустого каталога: init, входящая папка, файл, разбор. Возвращает номер пачки."""
    code, out, err = archive("init")
    assert code == 0, err
    setup_inbox(archive)
    archive.put("договор.txt", CLEAN)
    code, out, err = archive("inbox", "run", "--wait")
    assert code == 0, err
    assert "Принято: 1" in out and "замечание" not in err, out + err
    return batch_of(archive)


def indexed_paths(archive):
    return sorted({r["path"] for r in archive.table().search().limit(1000).to_list()})


def state(archive):
    """Отпечаток рабочего архива: токен, хранилище, база сверки, число строк и содержимое таблицы, версия таблицы."""
    table = archive.table()
    rows = table.search().limit(1000).to_list()
    return {"token": open(archive.path("secrets", "local.token"), "rb").read(), "store": open(archive.path("secrets", "tokens.json"), "rb").read(),
            "known": N.Known(archive.path("index", "known.sqlite")).info(), "version": table.version, "rows": len(rows),
            "content": hashlib.sha256(json.dumps(sorted((r["path"], r["chunk"], r["text"]) for r in rows), ensure_ascii=False).encode()).hexdigest()}


def test_от_пустого_каталога_до_найденного_документа_и_пачки_в_истории_без_замечаний(archive, vectors):
    batch = install(archive)
    rows = archive.table().search().where("source = 'входящие'").to_list()
    assert [r["path"] for r in rows] == [f"входящие/{batch}/договор.txt"] and "серверной" in rows[0]["text"]
    found = archive.table().search("серверной", query_type="fts").limit(5).to_list()                    # полнотекстовый индекс от init работает
    assert [r["path"] for r in found] == [f"входящие/{batch}/договор.txt"]
    asked = [r["input"] for r in vectors["requests"]]                                   # векторы — у подставной службы: сначала проверка окружения в конце init,
    assert len(asked) == 2 and asked[0] == [doctor.PROBE_TEXT] and os.listdir(archive.box) == []        # потом документ; источник убран
    (b,) = json.loads(archive("inbox", "batches", "--json")[1])["batches"]
    assert (b["id"], b["files"], b["problems"], b["counts"]) == (batch, 1, 0, {"accept": 1})
    detail = json.loads(archive("inbox", "batch", batch, "--json")[1])
    assert detail["problems"] == [] and [(f["name"], f["indexed"], f["location"]) for f in detail["files"]] == [("договор.txt", True, "corpus")]
    status = json.loads(archive("inbox", "status", "--json")[1])
    assert (status["pending"], status["waiting"], status["problems"]) == (0, 0, 0)
    code, out, _ = archive("inbox", "batches")
    assert code == 0 and batch in out


def test_повторный_init_на_рабочем_архиве_ничего_не_пересоздаёт_и_не_теряет(archive):
    install(archive)
    before = state(archive)
    assert before["rows"] == 1 and before["known"]["files"] == 1
    code, out, err = archive("init", "--json")
    assert code == 0, err
    answer = json.loads(out)
    assert {s["id"]: s["result"] for s in answer["steps"]} == {i: "exists" for i in
                                                               ("home", "secrets", "token", "logs", "index", "corpus", "known", "table")}
    assert state(archive) == before
    assert indexed_paths(archive) == [f"входящие/{batch_of(archive)}/договор.txt"]


def test_проход_без_init_документ_принят_замечание_называет_init_после_init_долг_отдаётся(archive):
    assert archive("known", "build")[0] == 0                                    # как собирал стенд: база сверки есть, таблицы индекса нет
    setup_inbox(archive)
    archive.put("записка.txt", NOTE)
    code, out, err = archive("inbox", "run", "--wait")
    assert code == 0 and "Принято: 1" in out
    assert "flyarchive init" in err and err.count("замечание:") == 1 and "ValueError" not in err
    assert not os.path.exists(archive.path("index", "lance"))                  # проход таблицу сам не завёл
    batch = batch_of(archive)
    rel = f"входящие/{batch}/записка.txt"
    detail = json.loads(archive("inbox", "batch", batch, "--json")[1])
    (problem,) = detail["problems"]
    assert (problem["code"], problem["args"]) == ("problem.index_no_table", {"rel": rel}) and "flyarchive init" in problem["text"]
    assert [(f["name"], f["indexed"], f["location"]) for f in detail["files"]] == [("записка.txt", False, "corpus")]    # документ не потерян
    assert os.path.isfile(archive.path("corpus", *rel.split("/"))) and os.listdir(archive.box) == []
    assert json.loads(archive("inbox", "status", "--json")[1])["pending"] == 1                                         # и остался долгом
    code, out, _ = archive("inbox", "run")                                                                              # без init долг так и висит
    assert code == 0 and not os.path.exists(archive.path("index", "lance"))
    assert json.loads(archive("inbox", "status", "--json")[1])["pending"] == 1

    assert archive("init")[0] == 0
    code, out, err = archive("inbox", "run")
    assert code == 0 and err == "", err
    assert json.loads(archive("inbox", "status", "--json")[1])["pending"] == 0
    assert indexed_paths(archive) == [rel]
    assert [r["path"] for r in archive.table().search("перенос", query_type="fts").limit(5).to_list()] == [rel]


def test_проход_без_базы_сверки_отказывает_и_называет_init_файл_остаётся_во_входящей_папке(archive):
    assert archive("known", "build")[0] == 0
    setup_inbox(archive)
    os.remove(archive.path("index", "known.sqlite"))
    archive.put("записка.txt", NOTE)
    code, out, err = archive("inbox", "run")
    assert code == 1 and out == "" and err.startswith("ошибка: база сверки с архивом не построена")
    assert "flyarchive known build" in err and "flyarchive init" in err
    assert os.listdir(archive.box) == ["записка.txt"]
    assert archive("init")[0] == 0                                              # init строит базу сверки, и проход идёт дальше
    code, out, err = archive("inbox", "run")
    assert code == 0 and "Принято: 1" in out, err


def test_команда_которой_нужна_таблица_индекса_без_неё_называет_init_а_не_падает_трассировкой(archive):
    assert archive("known", "build")[0] == 0
    code, out, err = archive("vision", "backfill", "--limit", "1", "--json")
    assert code == 1 and out == "" and "Traceback" not in err
    assert error_line(err) == {"code": "index.no_table", "args": {}, "text": "таблицы индекса нет: выполни flyarchive init"}
    assert not os.path.exists(archive.path("index", "lance"))                  # и таблицу эта команда сама не заводит
    assert archive("init")[0] == 0
    code, out, err = archive("vision", "backfill", "--limit", "1", "--json")
    assert code == 0 and json.loads(out)["candidates"] == 0, err

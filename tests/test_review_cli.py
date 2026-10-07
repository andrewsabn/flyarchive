"""Команды решений владельца: flyarchive queue, quarantine, doc delete (FR-52, FR-53, FR-54)."""
import json
import os

import pytest

from test_inbox_cli import CLEAN, EVIL, cli, lance, vectors  # noqa: F401  — общая обвязка команды

EXE = b"MZ" + bytes(200)


@pytest.fixture
def ready(cli):
    """Архив после одной пачки: документ принят, документ в очереди, программа в карантине."""
    lance(cli.home)
    cli("set", "--path", cli.box, "--llm", "off")
    cfg = cli.config()
    cfg["stable_seconds"] = 0
    json.dump(cfg, open(os.path.join(cli.home, "inbox.json"), "w", encoding="utf-8"))
    cli.put("договор.txt", CLEAN)
    cli.put("отчёт.txt", EVIL)
    cli.put("setup.exe", EXE)
    code, out, err = cli("run")
    assert code == 0 and "Принято: 1" in out and "На утверждение: 1" in out and "Карантин: 1" in out, err
    cli.batch = [n for n in os.listdir(os.path.join(cli.home, "квитанции")) if n.endswith(".jsonl")][0][:-len(".jsonl")]
    return cli


def table(cli):
    import lancedb
    return lancedb.connect(os.path.join(cli.home, "index", "lance")).open_table("docs")


def paths(cli):
    return sorted({r["path"] for r in table(cli).search().select(["path"]).limit(1000).to_list()})


def test_очередь_списком_и_для_программ(ready):
    code, out, _ = ready.cmd("queue", "list", "--json")
    (item,) = json.loads(out)
    assert code == 0 and item["path"] == f"очередь/{ready.batch}/отчёт.txt" and item["score"] > 20
    code, out, _ = ready.cmd("queue", "list")
    assert code == 0 and "отчёт.txt" in out and "prompt_injection" in out and str(item["score"]) in out


def test_принять_из_очереди_документ_находится_в_индексе(ready):
    code, out, err = ready.cmd("queue", "accept", f"очередь/{ready.batch}/отчёт.txt", "--json")
    assert code == 0, err
    assert json.loads(out) == {"path": f"входящие/{ready.batch}/отчёт.txt", "indexed": True}
    assert paths(ready) == ["mail/старое.txt", f"входящие/{ready.batch}/договор.txt", f"входящие/{ready.batch}/отчёт.txt"]
    assert json.loads(ready.cmd("queue", "list", "--json")[1]) == []


def test_из_очереди_в_карантин_и_стереть(ready):
    code, out, _ = ready.cmd("queue", "quarantine", f"очередь/{ready.batch}/отчёт.txt", "--json")
    assert code == 0 and json.loads(out) == {"path": f"карантин/{ready.batch}/отчёт.txt"}
    items = json.loads(ready.cmd("quarantine", "list", "--json")[1])
    assert [i["name"] for i in items] == ["setup.exe", "отчёт.txt"]
    code, out, _ = ready.cmd("quarantine", "delete", f"карантин/{ready.batch}/setup.exe", "--json")
    assert code == 0 and json.loads(out) == {"deleted": f"карантин/{ready.batch}/setup.exe"}
    assert [i["name"] for i in json.loads(ready.cmd("quarantine", "list", "--json")[1])] == ["отчёт.txt"]


def test_вернуть_из_карантина_в_папку_возврата(ready):
    code, out, _ = ready.cmd("quarantine", "return", f"карантин/{ready.batch}/setup.exe", "--json")
    assert code == 0 and json.loads(out) == {"returned": f"из-карантина/{ready.batch}/setup.exe"}
    assert open(os.path.join(ready.box + "-возврат", "из-карантина", ready.batch, "setup.exe"), "rb").read() == EXE


def test_удаление_документа_требует_подтверждения(ready):
    rel = f"входящие/{ready.batch}/договор.txt"
    code, out, err = ready.cmd("doc", "delete", rel)
    assert code == 1 and "--yes" in err and rel in paths(ready)
    assert os.path.exists(os.path.join(ready.home, "corpus", *rel.split("/")))


def test_удаление_документа_из_настоящего_индекса_и_корпуса(ready):
    rel = f"входящие/{ready.batch}/договор.txt"
    code, out, err = ready.cmd("doc", "delete", rel, "--yes", "--json")
    assert code == 0, err
    r = json.loads(out)
    assert r["rows"] == 1 and r["path"] == rel and r["moved_to"].startswith("удалённое/")
    assert paths(ready) == ["mail/старое.txt"]
    assert not os.path.exists(os.path.join(ready.home, "corpus", *rel.split("/")))
    assert os.path.exists(os.path.join(ready.home, *r["moved_to"].split("/")))
    code, out, err = ready.cmd("doc", "delete", rel, "--yes")
    assert code == 1 and "нет такого документа" in err


@pytest.mark.parametrize("args", [("queue", "accept", "../secrets/tokens.json"), ("queue", "quarantine", "очередь/нет/x"),
                                  ("quarantine", "delete", "очередь/x/y"), ("quarantine", "return", ""),
                                  ("doc", "delete", "../secrets/link.key", "--yes"), ("queue", "accept")])
def test_негодный_путь_код_возврата_не_ноль(ready, args):
    code, out, err = ready.cmd(*args)
    assert code != 0 and out == ""


def test_слова_для_человека(ready):
    code, out, _ = ready.cmd("queue", "accept", f"очередь/{ready.batch}/отчёт.txt")
    assert code == 0 and "принят" in out.lower() and "входящие/" in out
    code, out, _ = ready.cmd("quarantine", "list")
    assert code == 0 and "setup.exe" in out and "executable" in out
    assert "пуст" in ready.cmd("queue", "list")[1].lower()


# ── FR-73а: отказ команды с --json — последняя строка stderr {"error": {code, args, text}}; без --json — «ошибка: текст» ──
def error_line(err):
    """Объект ошибки из последней строки stderr; строка одна, целая и последняя."""
    assert err.endswith("\n") and err.count("\n") == 1, err
    obj = json.loads(err.rstrip("\n").split("\n")[-1])
    assert list(obj) == ["error"] and set(obj["error"]) == {"code", "args", "text"}
    return obj["error"]


def test_нет_такого_файла_в_очереди_с_json_код_параметры_текст(cli):
    path = "очередь/20261004-120000/нет.txt"
    code, out, err = cli.cmd("queue", "accept", path, "--json")
    assert code == 1 and out == "" and error_line(err) == {"code": "review.no_file", "args": {"path": path},
                                                          "text": f"нет такого файла: {path}"}
    code, out, err = cli.cmd("queue", "accept", path)
    assert code == 1 and out == "" and err == f"ошибка: нет такого файла: {path}\n" and '"error"' not in err


@pytest.mark.parametrize("args, area", [(("queue", "accept"), "очередь"), (("queue", "quarantine"), "очередь"),
                                        (("quarantine", "delete"), "карантин"), (("quarantine", "return"), "карантин")])
def test_путь_не_того_вида_с_json_код_и_каталог(cli, args, area):
    code, out, err = cli.cmd(*args, "../secrets/tokens.json", "--json")
    assert code == 1 and out == "" and error_line(err) == {"code": "review.bad_path", "args": {"area": area},
                                                          "text": f"путь должен быть вида {area}/<пачка>/<имя>"}


def test_удаление_документа_без_подтверждения_и_без_документа_с_json(cli):
    code, out, err = cli.cmd("doc", "delete", "входящие/п/а.txt", "--json")
    assert code == 1 and out == "" and error_line(err) == {
        "code": "cli.confirm_needed", "args": {}, "text": "удаление документа из архива и индекса нужно подтвердить ключом --yes"}
    code, out, err = cli.cmd("doc", "delete", "входящие/п/а.txt", "--yes", "--json")
    assert code == 1 and out == "" and error_line(err) == {"code": "review.no_doc", "args": {"path": "входящие/п/а.txt"},
                                                          "text": "нет такого документа в корпусе: входящие/п/а.txt"}
    code, out, err = cli.cmd("doc", "delete", "входящие/п/а.txt")
    assert code == 1 and err == "ошибка: удаление документа из архива и индекса нужно подтвердить ключом --yes\n"


def test_очередь_без_отказа_stderr_пуст(cli):
    code, out, err = cli.cmd("queue", "list", "--json")
    assert code == 0 and err == "" and json.loads(out) == []


# ── FR-73б: отказ базы известного при решении — строка JSON, а не трассировка ──
def test_принять_без_базы_известного_при_json_последняя_строка_stderr_объект_ошибки(ready):
    db = os.path.join(ready.home, "index", "known.sqlite")
    os.remove(db)
    code, out, err = ready.cmd("queue", "accept", f"очередь/{ready.batch}/отчёт.txt", "--json")
    assert code == 1 and out == "" and err.endswith("\n") and "Traceback" not in err
    last = json.loads(err.rstrip("\n").split("\n")[-1])
    assert list(last) == ["error"] and last["error"] == {"code": "known.db_missing", "args": {"path": db},
                                                          "text": f"базы известного нет: {db}. Собери её: flyarchive known build"}


def test_принять_без_базы_известного_без_json_строка_ошибки_без_трассировки(ready):
    db = os.path.join(ready.home, "index", "known.sqlite")
    os.remove(db)
    code, out, err = ready.cmd("queue", "accept", f"очередь/{ready.batch}/отчёт.txt")
    assert code == 1 and out == "" and err == f"ошибка: базы известного нет: {db}. Собери её: flyarchive known build\n"

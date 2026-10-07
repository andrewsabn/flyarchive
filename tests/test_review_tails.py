"""Хвосты решений: удаление документа попадает в историю пачки; списки очереди и карантина несут причину с кодом.

Е2. `flyarchive doc delete` для файла, пришедшего пачкой (входящие/<пачка>/…), дописывает в квитанции пачки строку решения владельца,
как решения очереди: история показывает decided: delete и место deleted. Для файла не из пачки поведение прежнее.
Е3. queue list и quarantine list отдают у каждой записи reason_msg (как в квитанции); у старых записей — null.
"""
import json
import os

import pytest

import batches as BA
import inbox as B
import review as R
from test_gate import MOJIBAKE, NL
from test_review import CLEAN, T0, env, embed  # noqa: F401
from test_inbox_cli import cli, vectors  # noqa: F401  — общая обвязка команды
from test_review_cli import ready  # noqa: F401  — архив после настоящего разбора


def receipts_text(env):
    with open(os.path.join(env.home, "квитанции", env.batch + ".jsonl"), encoding="utf-8") as f:
        return f.read()


def detail(env, name):
    (f,) = [f for f in BA.batch_detail(env.home, env.batch)["files"] if f["name"] == name]
    return f


# ── Е2: удаление документа и история ────────────────────────────
def test_удаление_документа_пачки_дописывает_решение_владельца_в_квитанции(env):
    before = receipts_text(env)
    R.doc_delete(env.home, env.c("договор.txt"), table=env.table, embed=None, now=T0)
    after = receipts_text(env)
    assert after.startswith(before) and after.count("\n") == before.count("\n") + 1
    last = json.loads(after.splitlines()[-1])
    assert (last["name"], last["decision"], last["by"], last["was"], last["path"]) == ("договор.txt", "delete", "владелец", "corpus", None)
    assert last["time"].endswith("Z")


def test_история_после_удаления_показывает_delete_и_место_deleted(env):
    assert (detail(env, "договор.txt")["decided"], detail(env, "договор.txt")["location"]) == (None, "corpus")
    R.doc_delete(env.home, env.c("договор.txt"), table=env.table, embed=None, now=T0)
    f = detail(env, "договор.txt")
    assert (f["decided"], f["location"]) == ("delete", "deleted") and f["decided_at"].endswith("Z")
    assert f["decision"] == "accept"                                              # итог приёмки не меняется
    assert BA.batch_detail(env.home, env.batch)["counts"] == {"accept": 1, "review": 2, "quarantine": 1}
    assert BA.list_batches(env.home)["batches"][0]["counts"] == {"accept": 1, "review": 2, "quarantine": 1}


def test_удаление_сделанное_раньше_без_строки_решения_тоже_deleted(env):
    """Удалённое до появления решения в квитанциях строки решения не имеет: история находит файл в удалённое/<день>/…"""
    R.doc_delete(env.home, env.c("договор.txt"), table=env.table, embed=None, now=T0)
    path = os.path.join(env.home, "квитанции", env.batch + ".jsonl")
    lines = [line for line in open(path, encoding="utf-8").read().splitlines() if '"decision": "delete"' not in line]
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    f = detail(env, "договор.txt")
    assert (f["decided"], f["location"]) == (None, "deleted")


def test_принятый_из_очереди_и_потом_удалённый_документ_последнее_решение_delete(env):
    R.queue_accept(env.home, env.q("отчёт.txt"), table=env.table, embed=embed)
    assert (detail(env, "отчёт.txt")["decided"], detail(env, "отчёт.txt")["location"]) == ("accept", "corpus")
    R.doc_delete(env.home, env.c("отчёт.txt"), table=env.table, embed=None, now=T0)
    f = detail(env, "отчёт.txt")
    assert (f["decided"], f["location"]) == ("delete", "deleted")


def test_путь_с_обратными_слэшами_тоже_находит_пачку(env):
    R.doc_delete(env.home, env.c("договор.txt").replace("/", chr(92)), table=env.table, embed=None, now=T0)
    assert detail(env, "договор.txt")["decided"] == "delete"


def test_удаление_документа_не_из_пачки_квитанций_не_трогает(env):
    folder = os.path.join(env.corpus, "export-a", "Inbox")
    os.makedirs(folder)
    with open(os.path.join(folder, "письмо.txt"), "w", encoding="utf-8") as f:
        f.write("текст")
    before = receipts_text(env), sorted(os.listdir(os.path.join(env.home, "квитанции")))
    R.doc_delete(env.home, "export-a/Inbox/письмо.txt", table=env.table, embed=None, now=T0)
    assert (receipts_text(env), sorted(os.listdir(os.path.join(env.home, "квитанции")))) == before


@pytest.mark.parametrize("batch", ["20200101-000000", "не-пачка", "20260101-000000-1"])
def test_документ_из_каталога_пачки_у_которой_нет_квитанций_квитанции_не_создаются(env, batch):
    folder = os.path.join(env.corpus, "входящие", batch)
    os.makedirs(folder)
    with open(os.path.join(folder, "старый.txt"), "w", encoding="utf-8") as f:
        f.write("текст")
    before = sorted(os.listdir(os.path.join(env.home, "квитанции")))
    R.doc_delete(env.home, f"входящие/{batch}/старый.txt", table=env.table, embed=None, now=T0)
    assert sorted(os.listdir(os.path.join(env.home, "квитанции"))) == before
    assert not os.path.exists(os.path.join(folder, "старый.txt"))


def test_файл_которого_нет_в_квитанциях_пачки_решения_не_получает(env):
    stray = os.path.join(env.corpus, "входящие", env.batch, "лишний.txt")
    with open(stray, "w", encoding="utf-8") as f:
        f.write("текст")
    before = receipts_text(env)
    R.doc_delete(env.home, env.c("лишний.txt"), table=env.table, embed=None, now=T0)
    assert receipts_text(env) == before


def test_отказ_удаления_ничего_в_квитанции_не_пишет(env):
    before = receipts_text(env)
    with pytest.raises(R.ReviewError):
        R.doc_delete(env.home, env.c("нет-такого.txt"), table=env.table, embed=None, now=T0)
    assert receipts_text(env) == before


def test_удаление_файла_вложенного_в_каталог_пачки(env):
    """Имя в квитанции — путь внутри пачки: письмо.вложения/счёт.txt."""
    name = "письмо.вложения/счёт.txt"
    path = os.path.join(env.corpus, "входящие", env.batch, *name.split("/"))
    os.makedirs(os.path.dirname(path))
    with open(path, "wb") as f:
        f.write(CLEAN)
    with open(os.path.join(env.home, "квитанции", env.batch + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"name": name, "decision": "accept", "path": f"входящие/{env.batch}/{name}", "returned": None}, ensure_ascii=False) + "\n")
    R.doc_delete(env.home, env.c(name), table=env.table, embed=None, now=T0)
    f = detail(env, name)
    assert (f["decided"], f["location"]) == ("delete", "deleted")


# ── Е3: причина с кодом в списках ───────────────────────────────
def test_списки_очереди_и_карантина_несут_reason_msg_как_в_квитанции(env):
    (item,) = R.quarantine_list(env.home)
    assert item["reason"] == "не документ: программа или скрипт"
    assert item["reason_msg"] == {"code": "reason.executable", "args": {}, "text": "не документ: программа или скрипт"}
    receipt = [r for r in env.receipts() if r["name"] == "setup.exe" and "by" not in r][0]
    assert item["reason_msg"] == receipt["reason_msg"]
    for q in R.queue_list(env.home):
        assert "reason_msg" in q and q["reason_msg"] is None and q["reason"] == ""           # у документов с находками причины нет


def test_задержанный_документ_причина_с_кодом_в_очереди(env):
    with open(os.path.join(env.inbox, "сбой.txt"), "w", encoding="utf-8") as f:
        f.write("Привет." + NL + MOJIBAKE * 3 + NL + "пока")
    s = B.process(env.home, env.inbox, now=T0 + 3600, table=env.table, embed=embed, stable_seconds=0)
    assert s.counts == {"review": 1}
    (item,) = [i for i in R.queue_list(env.home) if i["name"] == "сбой.txt"]
    assert item["reason_msg"] == {"code": "reason.held", "args": {"rule": "garbled"}, "text": "задержан до решения владельца: находка garbled"}
    assert item["reason"] == item["reason_msg"]["text"]


def test_у_старой_квитанции_без_reason_msg_поле_null_а_reason_прежний(env):
    path = os.path.join(env.home, "квитанции", env.batch + ".jsonl")
    old = []
    for line in open(path, encoding="utf-8").read().splitlines():
        r = json.loads(line)
        r.pop("reason_msg", None)
        old.append(json.dumps(r, ensure_ascii=False))
    open(path, "w", encoding="utf-8").write("\n".join(old) + "\n")
    (item,) = R.quarantine_list(env.home)
    assert item["reason"] == "не документ: программа или скрипт" and item["reason_msg"] is None


def test_нет_квитанции_reason_msg_null(env):
    os.remove(os.path.join(env.home, "квитанции", env.batch + ".jsonl"))
    (item,) = R.quarantine_list(env.home)
    assert (item["reason"], item["reason_msg"]) == ("", None)


def test_команда_отдаёт_reason_msg_в_обоих_списках(ready):
    (item,) = json.loads(ready.cmd("quarantine", "list", "--json")[1])
    assert item["reason_msg"] == {"code": "reason.executable", "args": {}, "text": "не документ: программа или скрипт"}
    (q,) = json.loads(ready.cmd("queue", "list", "--json")[1])
    assert "reason_msg" in q and q["reason_msg"] is None


# ── Е2 через команду ────────────────────────────────────────────
def test_команда_doc_delete_и_история_пачки(ready):
    rel = f"входящие/{ready.batch}/договор.txt"
    code, out, err = ready.cmd("doc", "delete", rel, "--yes", "--json")
    assert code == 0, err
    code, out, err = ready("batch", ready.batch, "--json")
    (f,) = [f for f in json.loads(out)["files"] if f["name"] == "договор.txt"]
    assert code == 0 and (f["decided"], f["location"]) == ("delete", "deleted")
    code, out, _ = ready("batch", ready.batch)
    assert "договор.txt" in out and "стёрт" in out

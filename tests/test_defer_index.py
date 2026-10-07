"""Принятие без ожидания индексации: queue accept --defer-index (FR-74).

Файл переносится в корпус, пишется в базу известного, долг индексации записан, разбор запускает служба; команда индексации не ждёт,
в ответе indexed: false. Нет службы или разбор уже идёт — долг всё равно записан, это не ошибка.
Настоящий systemctl в тестах не зовётся: функции получают подставной исполнитель, команда — подставную программу в PATH.
"""
import json
import os
import subprocess

import pytest

import gate as G
import inbox as B
import known as N
import messages as M
import review as R
from test_inbox_cli import cli, lance, vectors  # noqa: F401  — общая обвязка команды
from test_review import T0, env, embed  # noqa: F401
from test_review_batch import index_paths, queue_names, queued  # noqa: F401


def never(texts):
    raise AssertionError("индексация не должна идти: принятие отложило её")


def debts(env):
    return B._read_pending(env.home)


# ── функция ─────────────────────────────────────────────────────
def test_принятие_без_индексации_документ_в_корпусе_в_базе_известного_долг_записан(env):
    path = env.q("отчёт.txt")
    r = R.queue_accept(env.home, path, table=env.table, embed=never, defer_index=True)
    rel = env.c("отчёт.txt")
    assert r == {"path": rel, "indexed": False}
    full = os.path.join(env.corpus, *rel.split("/"))
    assert env.tree("corpus")[rel] and N.Known(env.db).find(sha256=G.sha256_of(full)) == rel
    assert rel not in env.table.paths()
    (item,) = debts(env)
    kind = [r for r in env.receipts() if r.get("name") == "отчёт.txt" and "by" not in r][0]["type"]
    assert item == {"path": full, "rel": rel, "updated": item["updated"], "space": env.batch, "type": kind}
    assert [i["name"] for i in R.queue_list(env.home)] == ["2024-03-05_счёт.eml"]


def test_отложенное_принятие_записано_в_квитанции_и_журнал_как_неиндексированное(env):
    R.queue_accept(env.home, env.q("отчёт.txt"), table=env.table, embed=never, defer_index=True)
    last = env.receipts()[-1]
    assert (last["name"], last["decision"], last["by"], last["was"], last["indexed"]) == ("отчёт.txt", "accept", "владелец", "review", False)
    rec = [r for r in env.journal() if r["tool"] == "queue accept"][-1]
    assert (rec["status"], rec["params"]["path"]) == (0, env.q("отчёт.txt"))


def test_отложенное_принятие_письма_берёт_дату_и_ключи_сверки_как_обычное(env):
    R.queue_accept(env.home, env.q("2024-03-05_счёт.eml"), table=env.table, embed=never, defer_index=True)
    k = N.Known(env.db)
    assert k.find(mid="held-1@example.org") == env.c("2024-03-05_счёт.eml") and k.date_of(env.c("2024-03-05_счёт.eml")) == "2024-03-05"
    assert debts(env)[0]["updated"] == "2024-03-05" and debts(env)[0]["type"] == "eml"


def test_долг_отложенного_принятия_той_же_формы_что_у_обычного_при_сбое_индекса(env):
    def broken(texts):
        raise OSError("ollama не отвечает")

    R.queue_accept(env.home, env.q("2024-03-05_счёт.eml"), table=env.table, embed=broken)
    R.queue_accept(env.home, env.q("отчёт.txt"), table=env.table, embed=never, defer_index=True)
    plain, deferred = debts(env)
    assert set(plain) == set(deferred) == {"path", "rel", "updated", "space", "type"} and plain["space"] == deferred["space"] == env.batch


def test_без_индекса_отложенное_принятие_не_открывает_таблицу(env, monkeypatch):
    monkeypatch.setattr(B.ingest, "open_table", lambda home: (_ for _ in ()).throw(AssertionError("таблицу открывать не надо")))
    R.queue_accept(env.home, env.q("отчёт.txt"), embed=never, defer_index=True)
    assert [it["rel"] for it in debts(env)] == [env.c("отчёт.txt")]


def test_следующий_проход_индексирует_отложенное_принятие(env):
    R.queue_accept(env.home, env.q("отчёт.txt"), table=env.table, embed=never, defer_index=True)
    R.queue_accept(env.home, env.q("2024-03-05_счёт.eml"), table=env.table, embed=never, defer_index=True)
    assert env.table.paths() == [env.c("договор.txt")]
    s = B.process(env.home, env.inbox, now=T0 + 600, table=env.table, embed=embed, stable_seconds=0)
    assert s.batch is None and s.problems == []
    assert env.table.paths() == sorted([env.c("договор.txt"), env.c("отчёт.txt"), env.c("2024-03-05_счёт.eml")]) and debts(env) == []


def test_отложенное_принятие_не_заводит_долг_дважды(env):
    R.queue_accept(env.home, env.q("отчёт.txt"), table=env.table, embed=never, defer_index=True)
    B.add_debts(env.home, [debts(env)[0]])
    assert len(debts(env)) == 1


# ── запуск службы ───────────────────────────────────────────────
class Reply:
    def __init__(self, code=0, err=""):
        self.returncode, self.stderr = code, err


def refused(run):
    """Сообщение отказа запуска службы."""
    with pytest.raises(B.InboxError) as e:
        B.start_service(run=run)
    return e.value.message


def test_запуск_службы_одна_команда_без_ожидания():
    seen = []
    assert B.start_service(run=lambda *args: seen.append(args) or Reply()) is None
    assert seen == [("start", "--no-block", "flyarchive-inbox.service")] and B.SERVICE == "flyarchive-inbox.service"


def test_запуск_службы_systemctl_отказал_сообщение_с_причиной():
    trouble = refused(lambda *a: Reply(5, "Unit flyarchive-inbox.service not found.\n"))
    assert (trouble.code, trouble.args) == ("cli.kick_failed", {"why": "Unit flyarchive-inbox.service not found."})
    assert trouble == "разбор не запущен: Unit flyarchive-inbox.service not found."
    assert refused(lambda *a: Reply(5, "я" * 300)).args == {"why": "я" * 200}
    assert refused(lambda *a: Reply(1, None)).code == "cli.kick_failed_silent"
    assert refused(lambda *a: Reply(1, "  ")).code == "cli.kick_failed_silent"


@pytest.mark.parametrize("error", [FileNotFoundError("systemctl"), subprocess.TimeoutExpired("systemctl", 30), PermissionError(13, "нет")])
def test_запуск_службы_нет_systemd_сообщение_с_видом_сбоя(error):
    def run(*args):
        raise error

    trouble = refused(run)
    assert (trouble.code, trouble.args) == ("cli.kick_no_systemd", {"error_type": type(error).__name__})


# ── команда ─────────────────────────────────────────────────────
def starts(cli):
    return [line for line in cli.systemctl().splitlines() if "start --no-block flyarchive-inbox.service" in line]


def stub(cli, script):
    path = os.path.join(os.path.dirname(cli.user), "bin", "systemctl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)


def pending_rels(cli):
    return [it["rel"] for it in B._read_pending(cli.home)]


def test_принять_пачку_без_ожидания_индексации_разбор_запущен_один_раз(queued, vectors):
    paths = [queued.q("отчёт1.txt"), queued.q("отчёт2.txt"), queued.q("отчёт3.txt")]
    code, out, err = queued.cmd("queue", "accept", "--defer-index", "--json", "--", *paths)
    assert code == 0 and err == "", err
    res = json.loads(out)["results"]
    assert [(x["path"], x["ok"], x["indexed"]) for x in res] == [(p, True, False) for p in paths]
    assert [x["to"] for x in res] == [f"входящие/{queued.batch}/отчёт{i}.txt" for i in (1, 2, 3)]
    assert vectors["requests"] == [] and [p for p in index_paths(queued) if p.startswith("входящие/")] == []      # индексацию не ждали и не делали
    assert pending_rels(queued) == [f"входящие/{queued.batch}/отчёт{i}.txt" for i in (1, 2, 3)]
    assert len(starts(queued)) == 1 and queue_names(queued) == []
    known = N.Known(os.path.join(queued.home, "index", "known.sqlite"))
    assert all(known.find(sha256=G.sha256_of(os.path.join(queued.home, "corpus", *r["to"].split("/")))) == r["to"] for r in res)


def test_разбор_запущенный_службой_индексирует_долги_в_настоящий_индекс(queued, vectors):
    paths = [queued.q("отчёт1.txt"), queued.q("отчёт2.txt")]
    queued.cmd("queue", "accept", "--defer-index", "--json", "--", *paths)
    code, out, err = queued("run")                      # то, что делает служба
    assert code == 0, err
    assert [p for p in index_paths(queued) if p.startswith("входящие/")] == [f"входящие/{queued.batch}/отчёт{i}.txt" for i in (1, 2)]
    assert len(vectors["requests"]) == 2 and pending_rels(queued) == []


def test_прежний_вид_один_путь_с_defer_index_отвечает_как_раньше(queued):
    code, out, _ = queued.cmd("queue", "accept", queued.q("отчёт1.txt"), "--defer-index", "--json")
    assert code == 0 and json.loads(out) == {"path": f"входящие/{queued.batch}/отчёт1.txt", "indexed": False}
    assert len(starts(queued)) == 1
    code, out, err = queued.cmd("queue", "accept", queued.q("отчёт2.txt"), "--defer-index")
    assert code == 0 and err == "" and out == (f"Документ принят: входящие/{queued.batch}/отчёт2.txt. "
                                              "В индекс он попадёт следующим разбором.\n")


def test_без_defer_index_служба_не_запускается_и_индексация_идёт(queued):
    code, out, _ = queued.cmd("queue", "accept", "--json", "--", queued.q("отчёт1.txt"))
    assert code == 0 and json.loads(out)["results"][0]["indexed"] is True and starts(queued) == []


def test_ничего_не_принято_служба_не_запускается(queued):
    code, out, _ = queued.cmd("queue", "accept", "--defer-index", "--json", "--", queued.q("нет-такого.txt"))
    assert code == 0 and json.loads(out)["results"][0]["ok"] is False and starts(queued) == [] and pending_rels(queued) == []


def test_служба_не_установлена_долг_записан_это_не_ошибка(queued):
    stub(queued, '#!/bin/bash\necho "$@" >> "$HOME/systemctl.log"\necho "Unit flyarchive-inbox.service not found." >&2\nexit 5\n')
    paths = [queued.q("отчёт1.txt"), queued.q("отчёт2.txt")]
    code, out, err = queued.cmd("queue", "accept", "--defer-index", "--json", "--", *paths)
    assert code == 0 and err == "" and [x["ok"] for x in json.loads(out)["results"]] == [True, True]
    assert len(pending_rels(queued)) == 2 and len(starts(queued)) == 1
    code, out, err = queued.cmd("queue", "accept", queued.q("отчёт3.txt"), "--defer-index")
    assert code == 0 and "Документ принят" in out and err == "замечание: разбор не запущен: Unit flyarchive-inbox.service not found.\n"
    assert len(pending_rels(queued)) == 3


def test_разбор_уже_идёт_долг_записан_это_не_ошибка(queued):
    with B.locked(queued.home):                         # проход держит замок разбора
        code, out, err = queued.cmd("queue", "accept", "--defer-index", "--json", "--", queued.q("отчёт1.txt"))
    assert code == 0 and err == "" and json.loads(out)["results"][0] == {
        "path": queued.q("отчёт1.txt"), "ok": True, "indexed": False, "to": f"входящие/{queued.batch}/отчёт1.txt"}
    assert pending_rels(queued) == [f"входящие/{queued.batch}/отчёт1.txt"]


def test_defer_index_есть_только_у_принятия(queued):
    for args in (("queue", "quarantine"), ("quarantine", "return")):
        code, out, err = queued.cmd(*args, "--defer-index", "--json", "--", queued.q("отчёт1.txt"))
        assert code == 2 and out == "" and "--defer-index" in err
    assert queue_names(queued) == ["отчёт1.txt", "отчёт2.txt", "отчёт3.txt"]


def test_разобрать_сейчас_после_переделки_работает_как_раньше(queued):
    code, out, _ = queued("kick", "--json")
    assert code == 0 and json.loads(out) == {"started": True} and len(starts(queued)) == 1
    stub(queued, '#!/bin/bash\necho "Unit not found." >&2\nexit 5\n')
    code, out, err = queued("kick", "--json")
    assert code == 1 and out == "" and json.loads(err)["error"] == {
        "code": "cli.kick_failed", "args": {"why": "Unit not found."}, "text": "разбор не запущен: Unit not found."}

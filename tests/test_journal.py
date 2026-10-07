"""Журнал обращений: FR-06, NFR-02."""
import json
import os
import stat
import threading

import pytest

import journal as J

TOKEN = "ba_" + "Q" * 43


@pytest.fixture
def jr(tmp_path):
    return J.Journal(str(tmp_path / "logs" / "access.jsonl"), clock=lambda: 1_800_000_000.5)


def lines(jr):
    with open(jr.path, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def test_запись_содержит_всё_что_нужно_для_разбора(jr):
    jr.write(server="search", client="ноутбук", level="read", tool="/api/search",
             params={"q": "реестр платежей", "k": "5"}, status=200, outcome="ok", ms=412, ip="127.0.0.1", via="mcp")
    (rec,) = lines(jr)
    assert rec == {"ts": "2027-01-15T08:00:00Z", "server": "search", "client": "ноутбук", "level": "read",
                   "tool": "/api/search", "params": {"q": "реестр платежей", "k": "5"}, "status": 200,
                   "outcome": "ok", "ms": 412, "ip": "127.0.0.1", "via": "mcp"}


def test_файл_журнала_закрыт_от_чужих(jr):
    jr.write(server="search", client="-", tool="/api/search", params={}, status=401, outcome="отказ: нет токена", ms=0)
    assert stat.S_IMODE(os.stat(jr.path).st_mode) == 0o600


@pytest.mark.parametrize("field", ["client", "tool", "outcome", "ip", "via"])
def test_значение_токена_не_попадает_в_журнал_ни_через_какое_поле(jr, field):
    rec = dict(server="search", client="ноутбук", tool="/api/search", params={"q": "найди " + TOKEN + " в почте"},
               status=200, outcome="ok", ms=1, ip="127.0.0.1", via="mcp")
    rec[field] = "x " + TOKEN
    jr.write(**rec)
    raw = open(jr.path, encoding="utf-8").read()
    assert TOKEN not in raw and "Q" * 20 not in raw
    assert "ba_***" in raw


@pytest.mark.parametrize("key", ["authorization", "Authorization", "token", "cookie", "s", "c"])
def test_служебные_параметры_в_журнал_не_пишутся(jr, key):
    jr.write(server="search", client="ноутбук", tool="/doc", params={key: "секретное", "p": "jira/a.md"},
             status=200, outcome="ok", ms=1)
    assert lines(jr)[0]["params"] == {"p": "jira/a.md"}
    assert "секретное" not in open(jr.path, encoding="utf-8").read()


def test_длинный_параметр_обрезается(jr):
    jr.write(server="office", client="ноутбук", tool="/diagram", params={"dot": "я" * 5000}, status=200, outcome="ok", ms=1)
    assert lines(jr)[0]["params"]["dot"] == "я" * 200 + "…"


def test_перевод_строки_в_запросе_не_ломает_журнал(jr):
    jr.write(server="search", client="ноутбук", tool="/api/search", params={"q": "первая" + chr(10) + '{"client": "подделка"}'},
             status=200, outcome="ok", ms=1)
    jr.write(server="search", client="ноутбук", tool="/api/search", params={"q": "вторая"}, status=200, outcome="ok", ms=1)
    recs = lines(jr)
    assert len(recs) == 2 and recs[0]["client"] == "ноутбук"


def test_нестроковые_параметры_приводятся_к_строке(jr):
    jr.write(server="office", client="ноутбук", tool="/landscape", params={"groups": [{"name": "А"}], "k": 5, "x": None},
             status=200, outcome="ok", ms=1)
    assert lines(jr)[0]["params"] == {"groups": '[{"name": "А"}]', "k": "5"}


def test_одновременные_записи_не_рвут_строки(jr):
    def work(i):
        for j in range(50):
            jr.write(server="search", client=f"клиент{i}", tool="/api/search", params={"q": "запрос " * 20}, status=200, outcome="ok", ms=j)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(lines(jr)) == 400


def test_хвост_журнала_и_отбор_по_клиенту(jr):
    for i in range(10):
        jr.write(server="search", client="ноутбук" if i % 2 else "планшет", tool="/api/search", params={"q": str(i)},
                 status=200, outcome="ok", ms=1)
    assert [r["params"]["q"] for r in jr.tail(3)] == ["7", "8", "9"]
    assert [r["params"]["q"] for r in jr.tail(2, client="планшет")] == ["6", "8"]
    assert jr.tail(5, client="никто") == []


def test_пустой_журнал_читается_как_пустой(jr):
    assert jr.tail(10) == []


def test_сбой_записи_журнала_не_роняет_службу(tmp_path, capsys):
    blocker = tmp_path / "файл"
    blocker.write_text("x")
    jr = J.Journal(str(blocker / "access.jsonl"))
    assert jr.write(server="search", client="-", tool="/x", params={}, status=200, outcome="ok", ms=1) is False
    assert "журнал" in capsys.readouterr().err

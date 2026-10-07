"""Решения пачкой (FR-74): до 200 путей одной командой, ответ {"results": [...]}, сбой одного пути остальных не останавливает.

Часть первая — сама функция review.decide_many и решения над настоящей очередью с подставной таблицей индекса.
Часть вторая — команда flyarchive: настоящий разбор, настоящий индекс LanceDB, подставной сервер векторов.
"""
import json
import os

import pytest

import inbox as B
import messages as M
import review as R
from test_inbox_cli import CLEAN, EVIL, cli, lance, vectors  # noqa: F401  — общая обвязка команды
from test_review import EXE, T0, env, embed  # noqa: F401
from test_review_cli import error_line


# ── функция ─────────────────────────────────────────────────────
@pytest.fixture
def world(env):
    """Вторая пачка поверх первой: четыре документа с находками ждут в очереди."""
    for i in range(1, 5):
        with open(os.path.join(env.inbox, f"отчёт{i}.txt"), "w", encoding="utf-8") as f:
            f.write(EVIL + f" Номер {i}.")
    s = B.process(env.home, env.inbox, now=T0 + 3600, table=env.table, embed=embed, stable_seconds=0)
    assert s.counts == {"review": 4}
    env.second = s.batch
    env.w = lambda name: f"очередь/{s.batch}/{name}"
    env.accept = lambda p: R.queue_accept(env.home, p, table=env.table, embed=embed)
    return env


def test_пачка_принятий_результаты_в_порядке_запроса_с_новым_местом(world):
    paths = [world.w("отчёт3.txt"), world.w("отчёт1.txt"), world.w("отчёт2.txt")]
    r = R.decide_many(paths, world.accept)
    assert list(r) == ["results"] and [x["path"] for x in r["results"]] == paths
    assert [set(x) for x in r["results"]] == [{"path", "ok", "indexed", "to"}] * 3 and [list(x)[:2] for x in r["results"]] == [["path", "ok"]] * 3
    assert all(x["ok"] is True and x["indexed"] is True for x in r["results"])
    assert [x["to"] for x in r["results"]] == [f"входящие/{world.second}/отчёт{i}.txt" for i in (3, 1, 2)]
    assert [i["name"] for i in R.queue_list(world.home) if i["batch"] == world.second] == ["отчёт4.txt"]


def test_сбой_одного_пути_не_останавливает_остальные(world):
    missing = world.w("нет-такого.txt")
    paths = [world.w("отчёт1.txt"), missing, world.w("отчёт2.txt")]
    r = R.decide_many(paths, world.accept)
    ok = [x["ok"] for x in r["results"]]
    assert ok == [True, False, True] and [x["path"] for x in r["results"]] == paths
    assert r["results"][1] == {"path": missing, "ok": False, "error": {"code": "review.no_file", "args": {"path": missing},
                                                                       "text": f"нет такого файла: {missing}"}}
    assert sorted(i["name"] for i in R.queue_list(world.home) if i["batch"] == world.second) == ["отчёт3.txt", "отчёт4.txt"]
    assert world.table.paths().count(f"входящие/{world.second}/отчёт2.txt") == 1       # третий путь сделан, хотя второй не удался


def test_сбой_не_из_каталога_становится_общим_сообщением_и_пачку_не_обрывает(world, monkeypatch):
    real = R.queue_reject

    def reject(home, path):
        if path.endswith("отчёт2.txt"):
            raise OSError(28, "нет места")
        return real(home, path)

    monkeypatch.setattr(R, "queue_reject", reject)
    paths = [world.w("отчёт1.txt"), world.w("отчёт2.txt"), world.w("отчёт3.txt")]
    r = R.decide_many(paths, lambda p: R.queue_reject(world.home, p))
    assert [x["ok"] for x in r["results"]] == [True, False, True]
    assert r["results"][1]["error"] == {"code": "generic.text", "args": {"text": "[Errno 28] нет места"}, "text": "[Errno 28] нет места"}
    assert [x["to"] for x in (r["results"][0], r["results"][2])] == [f"карантин/{world.second}/отчёт1.txt", f"карантин/{world.second}/отчёт3.txt"]


def test_решение_пачкой_в_карантин_и_возврат(world):
    paths = [world.w("отчёт1.txt"), world.w("отчёт2.txt")]
    r = R.decide_many(paths, lambda p: R.queue_reject(world.home, p))
    assert [(x["path"], x["ok"], x["to"]) for x in r["results"]] == [(p, True, p.replace("очередь/", "карантин/")) for p in paths]
    kept = [x["to"] for x in r["results"]]
    back = R.decide_many(kept, lambda p: R.quarantine_return(world.home, p, inbox=world.inbox))
    assert [(x["path"], x["ok"]) for x in back["results"]] == [(p, True) for p in kept]
    assert [x["returned"] for x in back["results"]] == [f"из-карантина/{world.second}/отчёт{i}.txt" for i in (1, 2)]
    assert all("to" not in x for x in back["results"])


def refuses(call):
    with pytest.raises(R.ReviewError) as e:
        call()
    return e.value.message.code, e.value.message.args, str(e.value)


def test_пустой_список_отказ_до_первого_действия():
    calls = []
    assert refuses(lambda: R.decide_many([], calls.append)) == ("review.paths_none", {}, "нужен хотя бы один путь")
    assert calls == []


def test_больше_двухсот_путей_отказ_до_первого_действия_а_ровно_двести_проходят():
    calls = []
    paths = [f"очередь/п/{i}.txt" for i in range(201)]
    assert refuses(lambda: R.decide_many(paths, lambda p: calls.append(p) or {})) == (
        "review.paths_too_many", {"max": 200, "count": 201}, "путей не больше 200 за раз, а их 201")
    assert calls == []
    r = R.decide_many(paths[:200], lambda p: calls.append(p) or {})
    assert len(calls) == 200 and len(r["results"]) == 200 and R.MAX_PATHS == 200


def test_повторный_путь_отказ_до_первого_действия():
    calls = []
    paths = ["очередь/п/а.txt", "очередь/п/б.txt", "очередь/п/а.txt"]
    assert refuses(lambda: R.decide_many(paths, lambda p: calls.append(p) or {})) == (
        "review.path_repeated", {"path": "очередь/п/а.txt"}, "путь назван дважды: очередь/п/а.txt")
    assert calls == []


def test_отказ_пачки_ничего_не_трогает_в_очереди(world):
    before = sorted(i["path"] for i in R.queue_list(world.home))
    paths = [world.w("отчёт1.txt"), world.w("отчёт2.txt"), world.w("отчёт1.txt")]
    with pytest.raises(R.ReviewError):
        R.decide_many(paths, world.accept)
    assert sorted(i["path"] for i in R.queue_list(world.home)) == before and world.table.paths().count(f"входящие/{world.second}/отчёт1.txt") == 0


# ── команда ─────────────────────────────────────────────────────
@pytest.fixture
def queued(cli):
    """Настоящий архив после одного разбора: три документа с находками в очереди, две программы в карантине."""
    lance(cli.home)
    cli("set", "--path", cli.box, "--llm", "off")
    cfg = cli.config()
    cfg["stable_seconds"] = 0
    json.dump(cfg, open(os.path.join(cli.home, "inbox.json"), "w", encoding="utf-8"))
    for i in range(1, 4):
        cli.put(f"отчёт{i}.txt", EVIL + f" Номер {i}.")
    cli.put("setup.exe", EXE)
    cli.put("setup2.exe", EXE + b"2")
    code, out, err = cli("run")
    assert code == 0 and "На утверждение: 3" in out and "Карантин: 2" in out, err
    (cli.batch,) = [n[:-len(".jsonl")] for n in os.listdir(os.path.join(cli.home, "квитанции")) if n.endswith(".jsonl")]
    cli.q = lambda name: f"очередь/{cli.batch}/{name}"
    cli.k = lambda name: f"карантин/{cli.batch}/{name}"
    return cli


def index_paths(cli):
    import lancedb
    table = lancedb.connect(os.path.join(cli.home, "index", "lance")).open_table("docs")
    return sorted({r["path"] for r in table.search().select(["path"]).limit(1000).to_list()})


def queue_names(cli):
    return sorted(i["name"] for i in json.loads(cli.cmd("queue", "list", "--json")[1]))


def test_принять_три_файла_одной_командой_results_в_порядке_запроса(queued):
    paths = [queued.q("отчёт3.txt"), queued.q("отчёт1.txt"), queued.q("отчёт2.txt")]
    code, out, err = queued.cmd("queue", "accept", "--json", "--", *paths)
    assert code == 0 and err == "", err
    data = json.loads(out)
    assert list(data) == ["results"] and [x["path"] for x in data["results"]] == paths
    assert all(x["ok"] is True and x["indexed"] is True for x in data["results"])
    assert [x["to"] for x in data["results"]] == [f"входящие/{queued.batch}/отчёт{i}.txt" for i in (3, 1, 2)]
    assert queue_names(queued) == [] and [p for p in index_paths(queued) if p.startswith("входящие/")] == [
        f"входящие/{queued.batch}/отчёт{i}.txt" for i in (1, 2, 3)]


def test_один_путь_после_двух_дефисов_тоже_отвечает_results(queued):
    code, out, _ = queued.cmd("queue", "accept", "--json", "--", queued.q("отчёт1.txt"))
    assert code == 0 and list(json.loads(out)) == ["results"] and json.loads(out)["results"][0]["ok"] is True
    code, out, _ = queued.cmd("queue", "quarantine", "--json", "--", queued.q("отчёт2.txt"))
    assert code == 0 and json.loads(out)["results"] == [{"path": queued.q("отчёт2.txt"), "ok": True, "to": queued.k("отчёт2.txt")}]
    code, out, _ = queued.cmd("quarantine", "return", "--json", "--", queued.k("setup.exe"))
    assert code == 0 and json.loads(out)["results"] == [{"path": queued.k("setup.exe"), "ok": True,
                                                         "returned": f"из-карантина/{queued.batch}/setup.exe"}]


def test_прежний_вид_один_путь_с_json_без_дефисов_отвечает_как_раньше(queued):
    code, out, _ = queued.cmd("queue", "accept", queued.q("отчёт1.txt"), "--json")
    assert code == 0 and json.loads(out) == {"path": f"входящие/{queued.batch}/отчёт1.txt", "indexed": True}
    code, out, _ = queued.cmd("queue", "quarantine", queued.q("отчёт2.txt"), "--json")
    assert code == 0 and json.loads(out) == {"path": queued.k("отчёт2.txt")}
    code, out, err = queued.cmd("queue", "accept", queued.q("нет.txt"), "--json")
    assert code == 1 and out == "" and error_line(err)["code"] == "review.no_file"


def test_сбой_одного_пути_ok_false_с_сообщением_остальные_сделаны_код_ноль(queued):
    missing = queued.q("нет-такого.txt")
    paths = [queued.q("отчёт1.txt"), missing, queued.k("setup.exe"), queued.q("отчёт2.txt")]
    code, out, err = queued.cmd("queue", "accept", "--json", "--", *paths)
    assert code == 0, err
    res = json.loads(out)["results"]
    assert [x["ok"] for x in res] == [True, False, False, True] and [x["path"] for x in res] == paths
    assert res[1]["error"] == {"code": "review.no_file", "args": {"path": missing}, "text": f"нет такого файла: {missing}"}
    assert res[2]["error"]["code"] == "review.bad_path" and res[2]["error"]["args"] == {"area": "очередь"}
    assert queue_names(queued) == ["отчёт3.txt"]
    assert queued.k("setup.exe") in [i["path"] for i in json.loads(queued.cmd("quarantine", "list", "--json")[1])]


def test_файл_изменён_после_проверки_в_пачке_только_его_запись_с_отказом(queued):
    full = os.path.join(queued.home, *queued.q("отчёт2.txt").split("/"))
    os.chmod(full, 0o600)
    with open(full, "ab") as f:
        f.write(b" tampered")
    paths = [queued.q("отчёт1.txt"), queued.q("отчёт2.txt"), queued.q("отчёт3.txt")]
    code, out, err = queued.cmd("queue", "accept", "--json", "--", *paths)
    res = json.loads(out)["results"]
    assert code == 0 and [x["ok"] for x in res] == [True, False, True]
    assert res[1]["error"]["code"] == "review.sha_mismatch" and res[1]["error"]["args"] == {"path": paths[1]}
    assert queue_names(queued) == ["отчёт2.txt"]


def test_больше_двухсот_путей_отказ_до_первого_действия_код_в_последней_строке(queued):
    paths = [queued.q("отчёт1.txt")] + [queued.q(f"нет-{i}.txt") for i in range(200)]
    code, out, err = queued.cmd("queue", "accept", "--json", "--", *paths)
    assert code == 1 and out == "" and error_line(err) == {
        "code": "review.paths_too_many", "args": {"max": 200, "count": 201}, "text": "путей не больше 200 за раз, а их 201"}
    assert queue_names(queued) == ["отчёт1.txt", "отчёт2.txt", "отчёт3.txt"]
    assert not os.path.exists(os.path.join(queued.home, "index", "pending.jsonl"))


def test_ровно_двести_путей_проходят_каждый_со_своим_итогом(queued):
    paths = [queued.q(f"нет-{i}.txt") for i in range(199)] + [queued.q("отчёт1.txt")]
    code, out, err = queued.cmd("queue", "quarantine", "--json", "--", *paths)
    res = json.loads(out)["results"]
    assert code == 0, err
    assert len(res) == 200 and [x["ok"] for x in res].count(False) == 199 and res[-1] == {
        "path": queued.q("отчёт1.txt"), "ok": True, "to": queued.k("отчёт1.txt")}


@pytest.mark.parametrize("args", [("queue", "accept"), ("queue", "quarantine"), ("quarantine", "return")])
def test_пустой_список_отказ_с_кодом(queued, args):
    code, out, err = queued.cmd(*args, "--json", "--")
    assert code == 1 and out == "" and error_line(err) == {"code": "review.paths_none", "args": {}, "text": "нужен хотя бы один путь"}
    code, out, err = queued.cmd(*args)
    assert code == 1 and out == "" and err == "ошибка: нужен хотя бы один путь\n"


@pytest.mark.parametrize("args, area", [(("queue", "accept"), "q"), (("queue", "quarantine"), "q"), (("quarantine", "return"), "k")])
def test_повтор_пути_отказ_до_первого_действия(queued, args, area):
    pick = queued.q if area == "q" else queued.k
    names = ("отчёт1.txt", "отчёт2.txt", "отчёт1.txt") if area == "q" else ("setup.exe", "setup2.exe", "setup.exe")
    paths = [pick(n) for n in names]
    code, out, err = queued.cmd(*args, "--json", "--", *paths)
    assert code == 1 and out == "" and error_line(err) == {"code": "review.path_repeated", "args": {"path": paths[0]},
                                                          "text": f"путь назван дважды: {paths[0]}"}
    assert queue_names(queued) == ["отчёт1.txt", "отчёт2.txt", "отчёт3.txt"]
    assert sorted(i["name"] for i in json.loads(queued.cmd("quarantine", "list", "--json")[1])) == ["setup.exe", "setup2.exe"]


def test_в_карантин_пачкой_и_вернуть_пачкой(queued):
    paths = [queued.q("отчёт1.txt"), queued.q("отчёт3.txt")]
    code, out, _ = queued.cmd("queue", "quarantine", "--json", "--", *paths)
    got = [(x["path"], x["ok"], x["to"]) for x in json.loads(out)["results"]]
    assert code == 0 and got == [(p, True, queued.k(os.path.basename(p))) for p in paths]
    assert queue_names(queued) == ["отчёт2.txt"]
    kept = [queued.k("setup.exe"), queued.k("нет-такого.exe"), queued.k("отчёт1.txt")]
    code, out, err = queued.cmd("quarantine", "return", "--json", "--", *kept)
    res = json.loads(out)["results"]
    assert code == 0, err
    assert [x["ok"] for x in res] == [True, False, True] and res[0]["returned"] == f"из-карантина/{queued.batch}/setup.exe"
    assert res[1]["error"]["code"] == "review.no_file"
    for name in ("setup.exe", "отчёт1.txt"):
        assert os.path.exists(os.path.join(queued.box + "-возврат", "из-карантина", queued.batch, name))


def test_несколько_путей_без_json_каждый_строкой_как_раньше_а_сбой_в_stderr(queued):
    code, out, err = queued.cmd("queue", "accept", queued.q("отчёт1.txt"), queued.q("отчёт2.txt"))
    assert code == 0 and err == "" and out.count("Документ принят:") == 2
    assert f"входящие/{queued.batch}/отчёт1.txt" in out and f"входящие/{queued.batch}/отчёт2.txt" in out
    code, out, err = queued.cmd("queue", "accept", queued.q("нет.txt"), queued.q("отчёт3.txt"))
    assert code == 1 and out.count("Документ принят:") == 1 and f"ошибка: {queued.q('нет.txt')}: нет такого файла" in err


def test_один_путь_без_json_печать_прежняя(queued):
    code, out, err = queued.cmd("queue", "accept", queued.q("отчёт1.txt"))
    assert code == 0 and err == "" and out == f"Документ принят: входящие/{queued.batch}/отчёт1.txt\n"
    code, out, _ = queued.cmd("queue", "quarantine", queued.q("отчёт2.txt"))
    assert code == 0 and out == f"Документ в карантине: {queued.k('отчёт2.txt')}\n"
    code, out, _ = queued.cmd("quarantine", "return", queued.k("setup.exe"))
    assert code == 0 and out == f"Возвращено в папку возврата: из-карантина/{queued.batch}/setup.exe\n"


def test_стирание_из_карантина_по_одному_как_раньше(queued):
    code, out, _ = queued.cmd("quarantine", "delete", "--json", "--", queued.k("setup.exe"))
    assert code == 0 and json.loads(out) == {"deleted": queued.k("setup.exe")}

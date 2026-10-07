"""Схемы и сканы в поиск (FR-92): команды `flyarchive vision` и настройка `flyarchive inbox set --vision`.

Команда настоящая (процесс), подставные — модель (HTTP-сервер), сервер векторов, systemctl. Таблица индекса — настоящая LanceDB во временном
каталоге. Всё, чему нужна песочница просмотра, — в test_vision_real.py. Настоящая модель и ключ владельца не трогаются.
"""
import hashlib
import json
import os
import site
import subprocess
import sys

import pytest

import gatekit as K
import inbox as B
import ingest
import known as N
import vision as V
from test_inbox_cli import lance, vectors  # noqa: F401 — настоящая таблица и подставные векторы
from test_vision import cloud_server, model_server  # noqa: F401 — подставная модель и «облако»

FLYARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")
KEY = "cli-key-4242-secret"
BATCH = "20260101-000000"


@pytest.fixture
def cli(tmp_path, vectors, model_server, cloud_server):
    user = tmp_path / "user"
    home = user / "flyarchive"
    box = tmp_path / "входящие"
    bin_dir = tmp_path / "bin"
    for d in (home / "corpus", home / "index", box, bin_dir):
        d.mkdir(parents=True)
    keyfile = tmp_path / "ключ-модели"
    keyfile.write_text(KEY, encoding="utf-8")
    table = home / "sources.json"                        # корни страниц с вложениями, какими они были в коде до выноса правил в таблицу источников (FR-99)
    table.write_text(json.dumps({"roots": {"confluence": {"kind": "pages", "source": "confluence", "attachments": "conf-att"},
                                           "jira": {"kind": "pages", "source": "jira", "attachments": "jira-att"}}}), encoding="utf-8")
    table.chmod(0o600)
    N.build(str(home / "corpus"), str(home / "index" / "known.sqlite"))
    stub = bin_dir / "systemctl"
    stub.write_text('#!/bin/bash\necho "$@" >> "$HOME/systemctl.log"\n', encoding="utf-8")
    stub.chmod(0o755)

    def call(*args, local=None, **extra):
        env = {**os.environ, "HOME": str(user), "FLYARCHIVE_HOME": str(home), "PYTHONIOENCODING": "utf-8",
               "PATH": f"{bin_dir}:{os.environ['PATH']}", "FLYARCHIVE_EMBED_URL": vectors["url"],
               "FLYARCHIVE_LLM_LOCAL_URL": local or model_server["url"], "FLYARCHIVE_LLM_CLOUD_URL": cloud_server["url"],
               "FLYARCHIVE_LLM_KEY_FILE": str(keyfile), "FLYARCHIVE_LLM_LOCAL_MODEL": "local-test",
               "PYTHONPATH": os.pathsep.join(p for p in (site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p), **extra}
        env.pop("FLYARCHIVE_LLM_KEY", None)
        r = subprocess.run([sys.executable, FLYARCHIVE, *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
        return r.returncode, r.stdout, r.stderr

    call.home, call.box, call.corpus = str(home), str(box), str(home / "corpus")
    call.config = lambda: json.load(open(home / "inbox.json", encoding="utf-8"))
    call.model, call.cloud = model_server, cloud_server

    def put_corpus(rel, data):
        path = os.path.join(call.corpus, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    call.put_corpus = put_corpus
    return call


def last_error(err):
    """Последняя строка stderr при --json: {"error": {"code", "args", "text"}}."""
    return json.loads(err.strip().splitlines()[-1])["error"]


def png(i=0):
    return K.png_image(300, 200, (10 + i, 40, 90))


# ── настройка ───────────────────────────────────────────────────
def test_настройка_описание_включается_и_выключается_как_проверка_моделью(cli):
    assert cli("inbox", "set", "--path", cli.box, "--vision", "off")[0] == 0 and cli.config()["vision"] is False
    assert cli("inbox", "set", "--vision", "on")[0] == 0 and cli.config()["vision"] is True


def test_настройка_описание_не_on_и_не_off_отказ_с_кодом(cli):
    code, out, err = cli("inbox", "set", "--path", cli.box, "--vision", "maybe", "--json")
    assert code == 1 and out == "" and last_error(err) == {"code": "cli.bad_switch", "args": {"name": "vision"}, "text": "--vision: on или off"}
    code, _, err = cli("inbox", "set", "--path", cli.box, "--vision", "maybe")
    assert code == 1 and "--vision: on или off" in err


def test_настройка_пределы_описания_задаются_и_проверяются(cli):
    code, out, err = cli("inbox", "set", "--path", cli.box, "--vision-pages", "30", "--vision-minutes", "5", "--json")
    assert code == 0, err
    cfg = json.loads(out)
    assert (cfg["vision_pages"], cfg["vision_minutes"], cfg["vision"]) == (30, 5, True)               # в ответе — действующее значение, не null
    assert (cli.config()["vision_pages"], cli.config()["vision_minutes"]) == (30, 5)
    for flag, value, key in (("--vision-pages", "0", "vision_pages"), ("--vision-pages", "1001", "vision_pages"),
                             ("--vision-minutes", "0", "vision_minutes"), ("--vision-minutes", "241", "vision_minutes")):
        code, _, err = cli("inbox", "set", "--path", cli.box, flag, value, "--json")
        error = last_error(err)
        assert code == 1 and error["code"] == "inbox.bad_int" and error["args"]["key"] == key
    assert (cli.config()["vision_pages"], cli.config()["vision_minutes"]) == (30, 5)               # отказ настройку не тронул


def test_настройка_без_ключей_описания_по_умолчанию_как_llm(cli):
    cli("inbox", "set", "--path", cli.box, "--llm", "off")
    assert json.loads(cli("inbox", "status", "--json")[1])["vision"] is False
    cli("inbox", "set", "--llm", "on")
    assert json.loads(cli("inbox", "status", "--json")[1])["vision"] is True


def test_настройка_словами_называет_описание(cli):
    code, out, _ = cli("inbox", "set", "--path", cli.box, "--vision", "off")
    assert code == 0 and "Описание изображений: нет" in out
    assert "Описание изображений: да" in cli("inbox", "set", "--vision", "on")[1]


# ── состояние ───────────────────────────────────────────────────
def test_состояние_показывает_настройку_и_сколько_ждёт_описания(cli):
    cli("inbox", "set", "--path", cli.box)
    code, out, _ = cli("inbox", "status", "--json")
    s = json.loads(out)
    assert code == 0 and (s["vision"], s["vision_pages"], s["vision_minutes"], s["vision_pending"], s["vision_done"]) == (True, 60, 10, 0, 0)
    B.add_debts(cli.home, [{"path": "/x", "rel": "входящие/п/а.png", "sha256": "a" * 64, "updated": "2026-10-05", "space": "п", "type": "png"}],
                B.VISION_PENDING)
    s = json.loads(cli("inbox", "status", "--json")[1])
    assert (s["vision_pending"], s["pending"]) == (1, 0)
    assert "Ждёт описания: 1" in cli("inbox", "status")[1]


def test_vision_status_json_и_словами(cli):
    cli("inbox", "set", "--path", cli.box, "--vision-pages", "40")
    B.add_debts(cli.home, [{"path": "/x", "rel": "входящие/п/а.png", "sha256": "a" * 64, "type": "png"}], B.VISION_PENDING)
    V.save(cli.home, "b" * 64, {"version": 1, "model": "m", "date": "2026-10-05T00:00:00Z", "pages": [], "skipped": []})
    code, out, err = cli("vision", "status", "--json")
    assert code == 0 and err == "" and json.loads(out) == {"enabled": True, "pending": 1, "described": 1, "vision_pages": 40, "vision_minutes": 10}
    code, out, _ = cli("vision", "status")
    assert code == 0 and "Ждёт описания: 1" in out and "Описано документов: 1" in out and "40 страниц" in out


def test_vision_status_пустой_архив_нули(cli):
    assert json.loads(cli("vision", "status", "--json")[1])["pending"] == 0


# ── backfill ────────────────────────────────────────────────────
def old_corpus(cli):
    lance(cli.home)                                              # в таблице одна строка: mail/старое.txt
    for rel, data in {f"входящие/{BATCH}/а.png": png(1), f"входящие/{BATCH}/б.pdf": K.pdf_blank(2), "jira/IT/_attachments/IT-1/схема.png": png(2),
                      "mail/старое.txt": b"text"}.items():
        cli.put_corpus(rel, data)
    return sorted([f"входящие/{BATCH}/а.png", f"входящие/{BATCH}/б.pdf", "jira/IT/_attachments/IT-1/схема.png"])


def test_backfill_без_limit_не_работает_отказ_с_кодом_долгов_нет(cli):
    old_corpus(cli)
    code, out, err = cli("vision", "backfill", "--json")
    error = last_error(err)
    assert code == 1 and out == "" and error["code"] == "vision.limit_needed" and "--limit" in error["text"]
    code, _, err = cli("vision", "backfill")
    assert code == 1 and "--limit" in err and "ошибка:" in err
    assert B._read_pending(cli.home, B.VISION_PENDING) == [] and cli.model["requests"] == []


@pytest.mark.parametrize("bad", ["0", "-1", "x", "1.5", "100001", ""])
def test_backfill_негодный_limit_отказ_с_кодом(cli, bad):
    old_corpus(cli)
    code, out, err = cli("vision", "backfill", "--limit", bad, "--json")
    assert code == 1 and last_error(err)["code"] == "vision.bad_limit" and out == ""


def test_backfill_dry_run_показывает_и_ничего_не_записывает(cli):
    rels = old_corpus(cli)
    code, out, err = cli("vision", "backfill", "--limit", "10", "--dry-run", "--json")
    assert code == 0, err
    r = json.loads(out)
    assert (r["candidates"], r["added"], r["dry_run"], r["files"]) == (3, 0, True, rels) and B._read_pending(cli.home, B.VISION_PENDING) == []


def test_backfill_записывает_долги_с_пределом_и_модель_не_зовёт(cli):
    rels = old_corpus(cli)
    code, out, err = cli("vision", "backfill", "--limit", "2", "--json")
    assert code == 0, err
    r = json.loads(out)
    assert (r["candidates"], r["added"], r["dry_run"], r["files"]) == (3, 2, False, rels[:2])
    assert [d["rel"] for d in B._read_pending(cli.home, B.VISION_PENDING)] == rels[:2]
    assert cli.model["requests"] == [] and cli.cloud["requests"] == [] and not os.path.exists(os.path.join(cli.home, "index", "vision"))
    assert json.loads(cli("vision", "status", "--json")[1])["pending"] == 2


def test_backfill_источник_фильтрует(cli):
    old_corpus(cli)
    code, out, err = cli("vision", "backfill", "--limit", "10", "--source", "jira-att", "--json")
    assert code == 0, err
    assert json.loads(out)["files"] == ["jira/IT/_attachments/IT-1/схема.png"]


def test_backfill_словами(cli):
    old_corpus(cli)
    code, out, _ = cli("vision", "backfill", "--limit", "10", "--dry-run")
    assert code == 0 and "документов без текста в индексе: 3" in out and "ничего не записано" in out


def test_backfill_документ_чей_текст_уже_в_индексе_не_касается(cli):
    old_corpus(cli)
    import lancedb
    table = lancedb.connect(os.path.join(cli.home, "index", "lance")).open_table("docs")
    table.add([{"path": f"входящие/{BATCH}/а.png", "source": "входящие", "space": BATCH, "title": "а", "updated": "2026-01-01", "url": "",
                "chunk": 0, "text": "уже есть", "vector": [0.1] * ingest.DIM}])
    r = json.loads(cli("vision", "backfill", "--limit", "10", "--dry-run", "--json")[1])
    assert f"входящие/{BATCH}/а.png" not in r["files"] and r["candidates"] == 2


# ── run ─────────────────────────────────────────────────────────
def debt_for(cli, rel=f"входящие/{BATCH}/а.png", data=None, **extra):
    data = data or png(1)
    path = cli.put_corpus(rel, data)
    item = {"path": path, "rel": rel, "sha256": hashlib.sha256(data).hexdigest(), "updated": "2026-10-05", "space": BATCH, "type": "png", **extra}
    B.add_debts(cli.home, [item], B.VISION_PENDING)
    return item


def test_run_без_долгов_описывать_нечего(cli):
    code, out, err = cli("vision", "run")
    assert code == 0 and "нечего" in out.lower() and cli.model["requests"] == []
    r = json.loads(cli("vision", "run", "--json")[1])
    assert (r["documents"], r["pages"], r["waiting"], r["stopped"], r["problems"], r["busy"]) == (0, 0, 0, None, [], False)


def test_run_модель_не_загружена_долг_остаётся_замечание_с_кодом_не_ошибка(cli):
    lance(cli.home)
    debt_for(cli)
    cli.model["running"] = []
    code, out, err = cli("vision", "run", "--json")
    r = json.loads(out)
    assert code == 0 and r["stopped"]["code"] == "vision.model_not_loaded" and [p["code"] for p in r["problems"]] == ["vision.model_not_loaded"]
    assert r["documents"] == 0 and r["waiting"] == 1 and [q[1] for q in cli.model["requests"]] == ["/running"]
    code, out, err = cli("vision", "run")
    assert code == 0 and "замечание:" in err and "не загружена" in err and "Ждёт описания: 1" in out


def test_run_модель_выключена_облако_настроено_и_живо_наружу_ничего_не_уходит(cli):
    """Картинка в облако не уходит: локальная модель молчит, облачный путь жив и включён, запросов к нему нет, документ остаётся долгом."""
    lance(cli.home)
    debt_for(cli)
    assert cli("inbox", "set", "--path", cli.box, "--cloud", "on", "--llm", "on")[0] == 0
    code, out, err = cli("vision", "run", "--json", local="http://127.0.0.1:9")
    r = json.loads(out)
    assert code == 0 and r["stopped"]["code"] == "vision.model_down" and r["waiting"] == 1
    assert cli.cloud["requests"] == [] and cli.model["requests"] == []
    assert len(B._read_pending(cli.home, B.VISION_PENDING)) == 1


def test_ключ_модели_не_печатается_ни_в_выводе_ни_в_ошибках_ни_в_файлах(cli):
    lance(cli.home)
    debt_for(cli)
    cli.model.update(status=500, echo=True)
    for args in (("vision", "run"), ("vision", "run", "--json"), ("vision", "status"), ("inbox", "status", "--json")):
        code, out, err = cli(*args)
        assert KEY not in out + err, args
    seen = []
    for folder, _, files in os.walk(cli.home):
        for name in files:
            with open(os.path.join(folder, name), "rb") as f:
                if KEY.encode() in f.read():
                    seen.append(os.path.join(folder, name))
    assert seen == []


def test_run_занят_другим_пропускает_молча_и_долг_остаётся(cli):
    debt_for(cli)
    with B._vision_locked(cli.home):
        code, out, err = cli("vision", "run")
        assert code == 0 and "уже идёт" in out
        assert json.loads(cli("vision", "run", "--json")[1])["busy"] is True
    assert cli.model["requests"] == []


def test_run_негодный_limit_отказ_с_кодом(cli):
    code, out, err = cli("vision", "run", "--limit", "0", "--json")
    assert code == 1 and last_error(err)["code"] == "vision.bad_limit"


# ── удаление документа убирает и описание ───────────────────────
def indexed_doc(cli, name="схема.png", described=True):
    """Документ в корпусе, строки описания в настоящем индексе, файл описания и запись в базе известного."""
    table = lance(cli.home)
    rel = f"входящие/{BATCH}/{name}"
    data = png(5) + name.encode("utf-8")
    cli.put_corpus(rel, data)
    N.build(cli.corpus, os.path.join(cli.home, "index", "known.sqlite"))
    sha = hashlib.sha256(data).hexdigest()
    table.add([{"path": rel, "source": "входящие", "space": BATCH, "title": name, "updated": "2026-10-05", "url": "", "chunk": 0,
                "text": "Описание изображения, сделанное моделью (страница 1):\nСхема.", "vector": [0.1] * ingest.DIM}])
    if described:
        V.save(cli.home, sha, {"version": 1, "model": "local-test", "date": "2026-10-05T00:00:00Z", "seconds": 1.0, "total": 1,
                               "truncated": False, "pages": [{"page": 1, "text": "Схема."}], "skipped": []})
    return rel, sha


def rows_of(cli, rel):
    import lancedb
    table = lancedb.connect(os.path.join(cli.home, "index", "lance")).open_table("docs")
    return table.search().where(f"path = '{rel}'").select(["path", "text"]).limit(50).to_list()


def test_удаление_документа_убирает_фрагменты_описания_и_файл_описания(cli):
    rel, sha = indexed_doc(cli)
    assert len(rows_of(cli, rel)) == 1 and os.path.exists(V.path(cli.home, sha))
    code, out, err = cli("doc", "delete", rel, "--yes", "--json")
    assert code == 0, err
    assert json.loads(out)["rows"] == 1 and rows_of(cli, rel) == [] and not os.path.exists(V.path(cli.home, sha))
    assert not os.path.exists(os.path.join(cli.corpus, *rel.split("/")))


def test_удаление_документа_снимает_и_долг_описания_того_же_содержимого(cli):
    rel, sha = indexed_doc(cli, described=True)
    B.add_debts(cli.home, [{"path": os.path.join(cli.corpus, *rel.split("/")), "rel": rel, "sha256": sha, "type": "png"}], B.VISION_PENDING)
    code, _, err = cli("doc", "delete", rel, "--yes")
    assert code == 0, err
    assert B._read_pending(cli.home, B.VISION_PENDING) == [] and V.count(cli.home) == 0


def test_удаление_документа_без_описания_ничего_лишнего_не_трогает(cli):
    rel, sha = indexed_doc(cli, described=False)
    other = "c" * 64
    V.save(cli.home, other, {"version": 1, "model": "m", "date": "2026-10-05T00:00:00Z", "pages": [], "skipped": []})
    assert cli("doc", "delete", rel, "--yes")[0] == 0 and V.count(cli.home) == 1 and os.path.exists(V.path(cli.home, other))


def test_удаление_документа_чьё_содержимое_лежит_и_под_другим_путём_описание_оставляет(cli):
    rel, sha = indexed_doc(cli)
    twin = f"входящие/{BATCH}/близнец.png"
    cli.put_corpus(twin, png(5) + "схема.png".encode("utf-8"))                       # то же содержимое под другим именем
    N.build(cli.corpus, os.path.join(cli.home, "index", "known.sqlite"))
    assert cli("doc", "delete", rel, "--yes")[0] == 0 and os.path.exists(V.path(cli.home, sha))


def test_удаление_с_отказом_описание_не_трогает(cli):
    rel, sha = indexed_doc(cli)
    code, _, err = cli("doc", "delete", rel)                                           # без --yes
    assert code == 1 and os.path.exists(V.path(cli.home, sha)) and len(rows_of(cli, rel)) == 1
    code, _, err = cli("doc", "delete", "входящие/нет/такого.png", "--yes")
    assert code == 1 and os.path.exists(V.path(cli.home, sha))


# ── история пачки: живое состояние описания ─────────────────────
def accept_png(cli):
    """Картинка принята настоящим проходом при выключенной проверке моделью: шаг описания выключен вместе с ней."""
    lance(cli.home)
    cli("inbox", "set", "--path", cli.box, "--llm", "off")
    cfg = cli.config()
    cfg["stable_seconds"] = 0
    json.dump(cfg, open(os.path.join(cli.home, "inbox.json"), "w", encoding="utf-8"))
    with open(os.path.join(cli.box, "схема.png"), "wb") as f:
        f.write(png(7))
    code, out, err = cli("inbox", "run")
    assert code == 0, err
    return json.loads(cli("inbox", "batches", "--json")[1])["batches"][0]["id"], hashlib.sha256(png(7)).hexdigest()


def test_квитанция_и_пачка_для_плагина_документ_ждёт_описания_шаг_выключен(cli):
    batch, sha = accept_png(cli)
    (f,) = json.loads(cli("inbox", "batch", batch, "--json")[1])["files"]
    assert f["vision"]["state"] == "waiting" and f["vision"]["reason_msg"]["code"] == "vision.off" and f["indexed"] is False
    assert json.loads(cli("inbox", "status", "--json")[1])["vision_pending"] == 1


def test_пачка_для_плагина_живое_состояние_описан_страниц_модель_секунд(cli):
    batch, sha = accept_png(cli)
    B.drop_debts(cli.home, [f"входящие/{batch}/схема.png"], B.VISION_PENDING)                      # так долг снимает шаг после описания
    V.save(cli.home, sha, {"version": 1, "model": "local-test", "date": "2026-10-05T00:00:00Z", "seconds": 7.5, "total": 3, "truncated": False,
                           "pages": [{"page": 1, "text": "а"}, {"page": 2, "text": "б"}, {"page": 3, "text": "в"}], "skipped": []})
    (f,) = json.loads(cli("inbox", "batch", batch, "--json")[1])["files"]
    assert f["vision"] == {"state": "described", "pages": 3, "total": 3, "truncated": False, "model": "local-test", "seconds": 7.5,
                           "date": "2026-10-05T00:00:00Z", "skipped": []}


def test_пачка_без_документов_на_описание_полей_vision_не_получает(cli):
    lance(cli.home)
    cli("inbox", "set", "--path", cli.box, "--llm", "off")
    cfg = cli.config()
    cfg["stable_seconds"] = 0
    json.dump(cfg, open(os.path.join(cli.home, "inbox.json"), "w", encoding="utf-8"))
    with open(os.path.join(cli.box, "записка.txt"), "w", encoding="utf-8") as f:
        f.write("Согласовать перенос работ на четверг.")
    assert cli("inbox", "run")[0] == 0
    batch = json.loads(cli("inbox", "batches", "--json")[1])["batches"][0]["id"]
    (f,) = json.loads(cli("inbox", "batch", batch, "--json")[1])["files"]
    assert "vision" not in f


# ── история пачки по отпечатку документа, а не по отметке в квитанции ──
def detail_files(cli, batch):
    return json.loads(cli("inbox", "batch", batch, "--json")[1])["files"]


def test_история_пачки_документ_проиндексирован_из_долгов_состояние_описания_видно_по_отпечатку(cli):
    """Таблицы индекса не было: картинка принята и осталась долгом индексации, отметки об ожидании описания в квитанции нет. Таблица появилась,
    проход отдал долг, картинка без текста стала долгом описания — история пачки показывает «ждёт», потом «описан»."""
    cli("inbox", "set", "--path", cli.box, "--llm", "off")
    cfg = cli.config()
    cfg["stable_seconds"] = 0
    json.dump(cfg, open(os.path.join(cli.home, "inbox.json"), "w", encoding="utf-8"))
    with open(os.path.join(cli.box, "схема.png"), "wb") as f:
        f.write(png(7))
    code, out, err = cli("inbox", "run")
    assert code == 0, err
    batch = json.loads(cli("inbox", "batches", "--json")[1])["batches"][0]["id"]
    rel, sha = f"входящие/{batch}/схема.png", hashlib.sha256(png(7)).hexdigest()
    (f,) = detail_files(cli, batch)
    assert f["indexed"] is False and "vision" not in f and f["location"] == "corpus"             # описывать пока нечего: документа ещё нет в индексе
    lance(cli.home)                                                                              # таблица появилась
    assert cli("inbox", "run")[0] == 0
    (f,) = detail_files(cli, batch)
    assert f["vision"]["state"] == "waiting" and f["vision"]["reason_msg"]["code"] == "vision.off" and f["indexed"] is False     # в квитанции отметки нет
    B.drop_debts(cli.home, [rel], B.VISION_PENDING)                                              # так долг снимает шаг после описания
    V.save(cli.home, sha, {"version": 1, "model": "local-test", "date": "2026-10-06T00:00:00Z", "seconds": 4.0, "total": 2, "truncated": False,
                           "pages": [{"page": 1, "text": "а"}, {"page": 2, "text": "б"}], "skipped": []})
    (f,) = detail_files(cli, batch)
    assert f["vision"] == {"state": "described", "pages": 2, "total": 2, "truncated": False, "model": "local-test", "seconds": 4.0,
                           "date": "2026-10-06T00:00:00Z", "skipped": []}
    code, out, _ = cli("inbox", "batch", batch)
    assert code == 0 and "описан моделью, страниц: 2" in out


def test_история_пачки_дубликат_описанного_документа_поля_vision_не_получает(cli):
    first, sha = accept_png(cli)                                                                 # принят, ждёт описания
    with open(os.path.join(cli.box, "копия.png"), "wb") as f:
        f.write(png(7))                                                                          # то же содержимое под другим именем
    assert cli("inbox", "run")[0] == 0
    second = json.loads(cli("inbox", "batches", "--json")[1])["batches"][0]["id"]
    assert second != first
    (f,) = detail_files(cli, second)
    assert f["decision"] == "duplicate" and f["sha256"] == sha and "vision" not in f
    (g,) = detail_files(cli, first)
    assert g["vision"]["state"] == "waiting"

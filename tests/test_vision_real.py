"""Схемы и сканы в поиск (FR-92): сквозные случаи с настоящей песочницей просмотра (bwrap), PyMuPDF и Pillow.

Просмотр настоящий: непроверенный файл читает рабочий процесс в `bwrap`, шаг описания получает от него готовый PNG. Модель подставная
(HTTP-сервер), векторы подставные. Если на машине нет bwrap или он не может создать пространство имён, тесты пропускаются с причиной.
Настоящая модель, 127.0.0.1:8080, ollama и видеокарта не трогаются.
"""
import base64
import hashlib
import json
import os
import re

import pytest

import gatekit as K
import llm_check as L
import inbox as B
import vision as V
from sandboxkit import needs_sandbox
from test_inbox import env, embed  # noqa: F401 — проход входящих
from test_vision import DESCRIPTION, cloud_server, env_for, home, model_server, put_doc  # noqa: F401
from test_vision_cli import BATCH, KEY, cli, last_error, lance, rows_of, vectors  # noqa: F401

pytestmark = needs_sandbox


def sent_images(server):
    """PNG, которые получила модель: по одному на запрос."""
    out = []
    for method, path, headers, body in server["posts"]():
        (url,) = [p["image_url"]["url"] for p in body["messages"][-1]["content"] if p["type"] == "image_url"]
        head, data = url.split(",", 1)
        assert head == "data:image/png;base64"
        out.append(base64.b64decode(data))
    return out


def describe(home, server, tmp_path, name, data):
    rel, sha = put_doc(home, name, data)
    out = V.describe(home, rel, sha, V.from_env(env_for(server, tmp_path)))
    return out, rel, sha


def test_картинка_идёт_модели_png_из_настоящего_просмотра(home, tmp_path, model_server):
    out, rel, sha = describe(home, model_server, tmp_path, "схема.png", K.png_image(300, 200, (200, 40, 40)))
    (png,) = sent_images(model_server)
    assert out.complete and out.pages == 1 and png.startswith(b"\x89PNG") and V.png_size(png) == (300, 200)
    assert V.load(home, sha)["pages"] == [{"page": 1, "text": DESCRIPTION}]


def test_большая_картинка_уходит_не_больше_четырёх_мегапикселей(home, tmp_path, model_server):
    pytest.importorskip("PIL")
    out, rel, sha = describe(home, model_server, tmp_path, "большая.png", K.image_bytes("PNG", (2600, 2600), (30, 90, 160)))
    (png,) = sent_images(model_server)
    width, height = V.png_size(png)
    assert out.complete and width * height <= 4_000_000 and min(width, height) >= V.MIN_SIDE


def test_pdf_без_текстового_слоя_каждая_страница_отдельным_запросом(home, tmp_path, model_server):
    out, rel, sha = describe(home, model_server, tmp_path, "скан.pdf", K.pdf_blank(3))
    pngs = sent_images(model_server)
    assert out.complete and out.pages == 3 and len(pngs) == 3 and model_server["most"] == 1
    assert all(V.png_size(p)[0] * V.png_size(p)[1] <= 4_000_000 for p in pngs)
    assert [p["page"] for p in V.load(home, sha)["pages"]] == [1, 2, 3]


def test_pdf_длиннее_двадцати_страниц_описываются_первые_двадцать(home, tmp_path, model_server):
    out, rel, sha = describe(home, model_server, tmp_path, "длинный.pdf", K.pdf_blank(23))
    assert out.complete and out.truncated and out.total == 23 and len(model_server["posts"]()) == 20
    assert V.load(home, sha)["truncated"] is True


def test_маленькая_картинка_моделью_не_описывается(home, tmp_path, model_server):
    out, rel, sha = describe(home, model_server, tmp_path, "значок.png", K.png_image(100, 100))
    assert out.complete and out.pages == 0 and model_server["posts"]() == []
    assert V.load(home, sha)["skipped"] == [{"page": 1, "why": "small"}]


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "WEBP", "TIFF", "JPEG"])
def test_остальные_форматы_картинок_описываются(home, tmp_path, model_server, fmt):
    out, rel, sha = describe(home, model_server, tmp_path, "снимок." + fmt.lower(), K.image_bytes(fmt, (320, 240)))
    assert out.complete and out.pages == 1 and len(sent_images(model_server)) == 1


def test_svg_описывается_как_страница(home, tmp_path, model_server):
    out, rel, sha = describe(home, model_server, tmp_path, "схема.svg", K.svg_bytes(400, 300, text="Начало"))
    assert out.complete and out.pages == 1 and len(sent_images(model_server)) == 1


def test_svg_со_сценарием_описание_идёт_а_сценарий_не_исполняется_и_сеть_не_трогается(home, tmp_path, model_server):
    out, rel, sha = describe(home, model_server, tmp_path, "яд.svg", K.svg_bytes(400, 300, script=True))
    assert out.complete and len(model_server["posts"]()) <= 1


def test_негодная_картинка_закрывается_без_описания_и_модель_не_зовётся(home, tmp_path, model_server):
    out, rel, sha = describe(home, model_server, tmp_path, "битая.png", K.PNG + "мусор".encode("utf-8"))
    assert out.complete and out.pages == 0 and model_server["posts"]() == [] and V.load(home, sha)["pages"] == []


def test_пустой_файл_описывать_нечего(home, tmp_path, model_server):
    out, rel, sha = describe(home, model_server, tmp_path, "пустая.png", b"")
    assert out.complete and out.pages == 0 and model_server["posts"]() == []


# ── сквозь проход входящих ──────────────────────────────────────
def test_проход_принимает_картинку_и_скан_описывает_и_кладёт_в_индекс(env, tmp_path, model_server, cloud_server):
    env.put("схема.png", K.png_image(300, 200, (20, 120, 20)))
    env.put("скан.pdf", K.pdf_blank(2))
    env.put("договор.txt", "Договор поставки оборудования.")                 # текст: индексируется без python-docx, а тесту формат не важен
    vision = V.Model(L.from_env(env_for(model_server, tmp_path, cloud=cloud_server), cloud=True))
    s = env.settle(vision=vision)
    assert s.counts == {"accept": 3} and s.problems == []
    by = {}
    for row in env.table.rows:
        by.setdefault(row["path"].rsplit("/", 1)[1], []).append(row)
    assert sorted(by) == ["договор.txt", "скан.pdf", "схема.png"]
    assert [r["text"].split("\n")[0] for r in by["скан.pdf"]] == ["Описание изображения, сделанное моделью (страница 1):",
                                                                    "Описание изображения, сделанное моделью (страница 2):"]
    assert by["схема.png"][0]["text"].endswith(DESCRIPTION) and len(model_server["posts"]()) == 3 and cloud_server["requests"] == []
    assert B._read_pending(env.home, B.VISION_PENDING) == [] and V.count(env.home) == 2


def test_проход_модель_выключена_документы_приняты_долги_остались_наружу_ничего_не_ушло(env, tmp_path, model_server, cloud_server):
    env.put("схема.png", K.png_image(300, 200))
    off = V.Model(L.from_env({**env_for(model_server, tmp_path, cloud=cloud_server), "FLYARCHIVE_LLM_LOCAL_URL": "http://127.0.0.1:9"},
                                                    cloud=True))
    s = env.settle(vision=off)
    assert s.counts == {"accept": 1} and [p.code for p in s.problems] == ["vision.model_down"]
    assert len(B._read_pending(env.home, B.VISION_PENDING)) == 1 and env.table.rows == []
    assert cloud_server["requests"] == [] and model_server["requests"] == []


# ── сквозь команду ──────────────────────────────────────────────
def prepared(cli, *files):
    lance(cli.home)
    assert cli("inbox", "set", "--path", cli.box, "--llm", "off", "--vision", "on")[0] == 0
    cfg = cli.config()
    cfg["stable_seconds"] = 0
    json.dump(cfg, open(os.path.join(cli.home, "inbox.json"), "w", encoding="utf-8"))
    for name, data in files:
        with open(os.path.join(cli.box, name), "wb") as f:
            f.write(data)


def test_команда_разбор_с_описанием_от_начала_до_конца(cli, vectors):
    png, pdf = K.png_image(300, 200, (90, 20, 20)), K.pdf_blank(2)
    prepared(cli, ("схема.png", png), ("скан.pdf", pdf))
    code, out, err = cli("inbox", "run")
    assert code == 0 and err == "", err
    assert vectors["requests"] and all(r["options"] == {"num_gpu": 0} for r in vectors["requests"])           # векторы описаний — на процессоре
    batch = json.loads(cli("inbox", "batches", "--json")[1])["batches"][0]["id"]
    rel_png, rel_pdf = f"входящие/{batch}/схема.png", f"входящие/{batch}/скан.pdf"
    assert [r["text"].split("\n")[0] for r in rows_of(cli, rel_png)] == ["Описание изображения, сделанное моделью (страница 1):"]
    assert len(rows_of(cli, rel_pdf)) == 2 and len(cli.model["posts"]()) == 3 and cli.cloud["requests"] == []
    status = json.loads(cli("vision", "status", "--json")[1])
    assert (status["pending"], status["described"], status["enabled"]) == (0, 2, True)
    d = json.loads(cli("inbox", "batch", batch, "--json")[1])
    states = {f["name"]: f["vision"] for f in d["files"]}
    assert states["схема.png"]["state"] == "described" and states["схема.png"]["pages"] == 1 and states["схема.png"]["model"] == "local-test"
    assert states["скан.pdf"]["pages"] == 2 and states["скан.pdf"]["total"] == 2 and states["скан.pdf"]["truncated"] is False
    assert isinstance(states["скан.pdf"]["seconds"], (int, float))
    s = json.loads(cli("inbox", "status", "--json")[1])
    assert (s["vision_pending"], s["vision_done"], s["pending"]) == (0, 2, 0)
    for stream in (out, err, json.dumps(d, ensure_ascii=False)):
        assert KEY not in stream


def test_команда_удаление_после_описания_убирает_фрагменты_и_файл_описания(cli):
    png = K.png_image(300, 200, (50, 50, 160))
    prepared(cli, ("схема.png", png))
    assert cli("inbox", "run")[0] == 0
    batch = json.loads(cli("inbox", "batches", "--json")[1])["batches"][0]["id"]
    rel, sha = f"входящие/{batch}/схема.png", hashlib.sha256(png).hexdigest()
    assert len(rows_of(cli, rel)) == 1 and os.path.exists(V.path(cli.home, sha))
    code, out, err = cli("doc", "delete", rel, "--yes", "--json")
    assert code == 0, err
    assert rows_of(cli, rel) == [] and not os.path.exists(V.path(cli.home, sha)) and V.count(cli.home) == 0
    assert json.loads(cli("vision", "status", "--json")[1])["described"] == 0


def test_команда_backfill_и_run_описывают_старый_документ_по_команде_владельца(cli):
    lance(cli.home)
    png = K.png_image(300, 200, (160, 50, 50))
    cli.put_corpus("jira/IT/_attachments/IT-1/схема.png", png)
    code, out, err = cli("vision", "backfill", "--limit", "5", "--json")
    assert code == 0 and json.loads(out)["added"] == 1, err
    assert cli.model["requests"] == []                                         # backfill модель не зовёт
    code, out, err = cli("vision", "run", "--json")
    r = json.loads(out)
    assert code == 0 and (r["documents"], r["pages"], r["waiting"], r["problems"]) == (1, 1, 0, []), err
    (row,) = rows_of(cli, "jira/IT/_attachments/IT-1/схема.png")
    assert row["text"].startswith("Описание изображения, сделанное моделью (страница 1):\n")
    import lancedb
    table = lancedb.connect(os.path.join(cli.home, "index", "lance")).open_table("docs")
    (full,) = table.search().where("path = 'jira/IT/_attachments/IT-1/схема.png'").select(["source", "space", "title"]).limit(5).to_list()
    assert (full["source"], full["space"], full["title"]) == ("jira-att", "IT", "IT-1 · схема.png")
    code, out, _ = cli("vision", "backfill", "--limit", "5", "--json")
    assert json.loads(out)["candidates"] == 0                                    # теперь у документа есть строки в индексе


def test_команда_run_limit_документов_и_остаток_ждёт(cli):
    lance(cli.home)
    for i in range(3):
        cli.put_corpus(f"jira/IT/_attachments/IT-1/схема-{i}.png", K.png_image(300, 200, (10 * i, 90, 90)))
    assert json.loads(cli("vision", "backfill", "--limit", "5", "--json")[1])["added"] == 3
    r = json.loads(cli("vision", "run", "--limit", "2", "--json")[1])
    assert (r["documents"], r["pages"], r["waiting"]) == (2, 2, 1)
    r = json.loads(cli("vision", "run", "--json")[1])
    assert (r["documents"], r["waiting"]) == (1, 0)


def test_команда_run_пределы_настройки_действуют(cli):
    lance(cli.home)
    for i in range(3):
        cli.put_corpus(f"jira/IT/_attachments/IT-1/схема-{i}.png", K.png_image(300, 200, (10 * i, 90, 90)))
    cli("vision", "backfill", "--limit", "5")
    assert cli("inbox", "set", "--path", cli.box, "--vision-pages", "1")[0] == 0
    r = json.loads(cli("vision", "run", "--json")[1])
    assert (r["documents"], r["pages"], r["waiting"]) == (1, 1, 2)

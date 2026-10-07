"""Имена служебных файлов — настройка приёмки, а не правило под одну выгрузку; пропущенное не стирается (FR-43).

Приёмка не считает файл служебным по имени сама: человек кладёт `README.md` или `*.jsonl`, и по умолчанию это обычный документ, а не пропавший файл. Шаблоны имён
лежат в `inbox.json` (`service_names` — в любой папке, `service_root_names` — только в корне входящей папки или архива), по умолчанию их нет. Задаёт их
команда `flyarchive inbox set --service-name ШАБЛОН --service-root-name ШАБЛОН`, видно их в `inbox status`. Что бы ни стало причиной «пропущен»,
исходник, точной копии которого нет в архиве, уходит в папку возврата. Судьбу таких файлов и описаний писем задаёт `--service-fate return|delete`
(по умолчанию return); пустые файлы и следы операционных систем (`__MACOSX`, `.DS_Store`, `Thumbs.db`, `desktop.ini`, `._*` с подписью AppleDouble)
не содержимое: они убираются и возврату не мешают.

Команда — настоящая, дочерним процессом, на пустом каталоге архива; служба векторов — подставная (одинаковый вектор на всё), модель выключена.
"""
import json
import os
import re

import pytest

import gatekit as K
from archivekit import Archive, error_line
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

README = "# Заметки\nОписание проекта и порядок работы с архивом.\n"
NOTES = '{"запись": 1}\n{"запись": 2}\n'
LISTING = "id;name\n1;a.eml\n"


@pytest.fixture
def archive(tmp_path, vectors):
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})


def ready(archive, *keys):
    """Заведённый архив, входящая папка без модели и без таймера, файлы берутся сразу; keys — ключи `inbox set` с шаблонами."""
    assert archive("init")[0] == 0
    code, out, err = archive("inbox", "set", "--path", archive.box, "--llm", "off", "--cloud", "off", "--no-timer", *keys)
    assert code == 0, err
    archive.instant()


def drop(archive):
    archive.put("README.md", README)
    archive.put("notes.jsonl", NOTES)
    archive.put("manifest.csv", LISTING)


def returned(archive):
    """Что лежит в папке возврата: {путь от пачки: содержимое}."""
    root = archive.box + "-возврат"
    out = {}
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            out["/".join(os.path.relpath(path, root).split(os.sep)[1:])] = open(path, encoding="utf-8").read()
    return out


# ── настройка ───────────────────────────────────────────────────
def test_по_умолчанию_шаблонов_имён_нет_и_status_их_показывает_пустыми(archive):
    ready(archive)
    cfg = archive.config()
    assert cfg["service_names"] == [] and cfg["service_root_names"] == []
    status = json.loads(archive("inbox", "status", "--json")[1])
    assert status["service_names"] == [] and status["service_root_names"] == []
    code, out, _ = archive("inbox", "status")
    assert code == 0 and "Служебные имена" in out and "не заданы" in out


def test_ключи_service_name_задают_шаблоны_и_они_видны_в_status(archive):
    ready(archive)
    code, out, err = archive("inbox", "set", "--no-timer", "--service-name", "*.jsonl", "--service-name", "*manifest*.csv",
                             "--service-root-name", "readme*")
    assert code == 0, err
    assert archive.config()["service_names"] == ["*.jsonl", "*manifest*.csv"] and archive.config()["service_root_names"] == ["readme*"]
    status = json.loads(archive("inbox", "status", "--json")[1])
    assert (status["service_names"], status["service_root_names"]) == (["*.jsonl", "*manifest*.csv"], ["readme*"])
    text = archive("inbox", "status")[1]
    assert "*.jsonl" in text and "*manifest*.csv" in text and "readme*" in text
    assert json.loads(archive("inbox", "set", "--no-timer", "--json")[1])["service_names"] == ["*.jsonl", "*manifest*.csv"]


def test_пустое_значение_очищает_только_свой_список(archive):
    ready(archive, "--service-name", "*.jsonl", "--service-root-name", "readme*")
    assert archive("inbox", "set", "--no-timer", "--service-name", "")[0] == 0
    assert archive.config()["service_names"] == [] and archive.config()["service_root_names"] == ["readme*"]
    assert archive("inbox", "set", "--no-timer", "--service-root-name", "")[0] == 0
    assert archive.config()["service_root_names"] == []


def test_другие_ключи_настройки_шаблонов_не_трогают(archive):
    """Плагин оболочки меняет период, порог и прочее, а шаблонов не знает: они сохраняются как есть."""
    ready(archive, "--service-name", "*.jsonl", "--service-root-name", "readme*")
    assert archive("inbox", "set", "--no-timer", "--period", "10", "--threshold", "30")[0] == 0
    cfg = archive.config()
    assert (cfg["period"], cfg["threshold"]) == (10, 30) and cfg["service_names"] == ["*.jsonl"] and cfg["service_root_names"] == ["readme*"]


@pytest.mark.parametrize("key, value", [("--service-name", "папка/*.jsonl"), ("--service-root-name", "a\\b"), ("--service-name", "x" * 201)])
def test_негодный_шаблон_отклоняется_с_кодом_и_настройка_не_меняется(archive, key, value):
    ready(archive, "--service-name", "*.jsonl")
    before = archive.config()
    code, out, err = archive("inbox", "set", "--no-timer", "--json", key, value)
    assert code != 0 and archive.config() == before
    error = error_line(err)
    assert error["code"] == "inbox.bad_service_names" and error["args"]["key"] == key[2:].replace("-", "_") + "s"


def test_шаблонов_не_больше_предела(archive):
    ready(archive)
    keys = [part for i in range(51) for part in ("--service-name", f"*.тип{i}")]
    code, _, err = archive("inbox", "set", "--no-timer", "--json", *keys)
    assert code != 0 and error_line(err)["code"] == "inbox.bad_service_names" and archive.config()["service_names"] == []


# ── разбор целиком: принято, найдено, возвращено ────────────────
def test_по_умолчанию_README_принят_и_найден_поиском_а_таблицы_не_исчезли(archive):
    ready(archive)
    drop(archive)
    code, out, err = archive("inbox", "run", "--wait")
    assert code == 0, err
    assert "Принято: 3" in out and os.listdir(archive.box) == [] and returned(archive) == {}
    rows = archive.table().search().where("source = 'входящие'").limit(10).to_list()
    assert sorted(os.path.basename(r["path"]) for r in rows) == ["README.md", "manifest.csv", "notes.jsonl"]
    code, out, err = archive("search", "описание проекта порядок работы", "--json", "--source", "входящие")
    assert code == 0, err
    assert any(hit["path"].endswith("/README.md") for hit in json.loads(out)["results"])


def test_с_заданными_шаблонами_файлы_пропущены_с_прежней_причиной_и_лежат_в_папке_возврата(archive):
    ready(archive, "--service-name", "*.jsonl", "--service-name", "*manifest*.csv", "--service-root-name", "readme*")
    drop(archive)
    code, out, err = archive("inbox", "run", "--wait")
    assert code == 0, err
    assert "Принято" not in out and os.listdir(archive.box) == []
    assert returned(archive) == {"README.md": README, "notes.jsonl": NOTES, "manifest.csv": LISTING}
    (batch,) = json.loads(archive("inbox", "batches", "--json")[1])["batches"]
    files = {f["name"]: f for f in json.loads(archive("inbox", "batch", batch["id"], "--json")[1])["files"]}
    assert {n: f["decision"] for n, f in files.items()} == {"README.md": "skip", "notes.jsonl": "skip", "manifest.csv": "skip"}
    assert files["notes.jsonl"]["reason"] == "служебный файл выгрузки: перечень, а не документ"
    assert files["README.md"]["reason"] == "служебный файл выгрузки: описание или отчёт о проверке"
    rows = archive.table().search().where("source = 'входящие'").limit(10).to_list()
    assert rows == []


def test_проверка_пачки_командой_check_берёт_те_же_шаблоны(archive, tmp_path):
    ready(archive, "--service-name", "*.jsonl")
    src = tmp_path / "пачка"
    src.mkdir()
    for name, data in (("README.md", README), ("notes.jsonl", NOTES), ("manifest.csv", LISTING)):
        (src / name).write_text(data, encoding="utf-8")
    code, out, err = archive("check", str(src), "--into", str(tmp_path / "разбор"), "--without-archive")
    assert code == 0, err
    with open(tmp_path / "разбор" / "отчёт.jsonl", encoding="utf-8") as f:
        report = {v["name"]: v for v in map(json.loads, f)}
    assert report["notes.jsonl"]["decision"] == "skip" and report["README.md"]["decision"] == "accept" and report["manifest.csv"]["decision"] == "accept"


# ── судьба служебного: service_fate ────────────────
def test_по_умолчанию_судьба_служебного_return_и_status_её_показывает(archive):
    ready(archive)
    assert archive.config()["service_fate"] == "return"
    assert json.loads(archive("inbox", "status", "--json")[1])["service_fate"] == "return"
    code, out, _ = archive("inbox", "status")
    assert code == 0 and "(service_fate): return" in out and "возвращаются в папку возврата" in out


def test_ключ_service_fate_задаёт_судьбу_и_она_видна_в_set_и_status(archive):
    ready(archive)
    code, out, err = archive("inbox", "set", "--no-timer", "--service-fate", "delete")
    assert code == 0, err
    assert "(service_fate): delete" in out and "убираются" in out
    assert archive.config()["service_fate"] == "delete"
    assert json.loads(archive("inbox", "status", "--json")[1])["service_fate"] == "delete"
    assert "(service_fate): delete" in archive("inbox", "status")[1]
    assert json.loads(archive("inbox", "set", "--no-timer", "--json")[1])["service_fate"] == "delete"
    assert archive("inbox", "set", "--no-timer", "--service-fate", "return")[0] == 0 and archive.config()["service_fate"] == "return"


@pytest.mark.parametrize("value", ["стереть", "Delete", "", "delete,return", "delete "])
def test_негодная_судьба_отказ_с_кодом_и_настройка_не_меняется(archive, value):
    ready(archive, "--service-fate", "delete")
    before = archive.config()
    code, out, err = archive("inbox", "set", "--no-timer", "--json", "--service-fate", value)
    assert code != 0 and archive.config() == before
    error = error_line(err)
    assert error["code"] == "inbox.bad_service_fate" and error["args"] == {"values": "return, delete"}
    assert error["text"] == "service_fate: нужно одно из значений: return, delete"


def test_другие_ключи_настройки_судьбу_не_трогают(archive):
    """Плагин оболочки судьбы не показывает: он меняет период и порог, а сохранённая судьба остаётся как есть."""
    ready(archive, "--service-fate", "delete", "--service-name", "*.jsonl")
    assert archive("inbox", "set", "--no-timer", "--period", "10", "--threshold", "30")[0] == 0
    cfg = archive.config()
    assert (cfg["period"], cfg["threshold"], cfg["service_fate"], cfg["service_names"]) == (10, 30, "delete", ["*.jsonl"])


def test_zip_с_Mac_документ_принят_архив_убран_папка_возврата_пуста_следы_названы_в_квитанции(archive):
    ready(archive)
    data = K.zip_bytes({"договор.txt": "Договор поставки оборудования.", "__MACOSX/._договор.txt": K.APPLEDOUBLE,
                        ".DS_Store": b"\x00\x00\x00\x01Bud1" + b"\x00" * 60})
    archive.put("выгрузка.zip", data)
    archive.put("пустой.txt", b"")
    code, out, err = archive("inbox", "run", "--wait")
    assert code == 0, err
    assert os.listdir(archive.box) == [] and returned(archive) == {}
    (batch,) = json.loads(archive("inbox", "batches", "--json")[1])["batches"]
    files = {f["name"]: f for f in json.loads(archive("inbox", "batch", batch["id"], "--json")[1])["files"]}
    assert {n: f["decision"] for n, f in files.items() if f["archive"] is not None or f["name"] == "пустой.txt"} == {
        "выгрузка.zip/договор.txt": "accept", "выгрузка.zip/__MACOSX/._договор.txt": "skip", "выгрузка.zip/.DS_Store": "skip", "пустой.txt": "skip"}
    assert files["выгрузка.zip/__MACOSX/._договор.txt"]["reason"] == "служебный файл macOS рядом с настоящим файлом"
    assert files["выгрузка.zip/.DS_Store"]["reason"] == "служебный след операционной системы, а не содержимое"
    assert files["пустой.txt"]["reason"] == "пустой файл"
    rows = archive.table().search().where("source = 'входящие'").limit(10).to_list()
    assert [os.path.basename(r["path"]) for r in rows] == ["договор.txt"]


def test_с_судьбой_delete_служебное_по_шаблонам_и_описание_письма_убираются_а_с_return_возвращаются(archive):
    ready(archive, "--service-name", "*.jsonl", "--service-name", "*manifest*.csv", "--service-root-name", "readme*", "--service-fate", "delete")
    drop(archive)
    archive.put("письмо.eml", K.EML)
    archive.put("письмо.json", '{"subject": "Договор"}')
    code, out, err = archive("inbox", "run", "--wait")
    assert code == 0, err
    assert os.listdir(archive.box) == [] and returned(archive) == {}
    (batch,) = json.loads(archive("inbox", "batches", "--json")[1])["batches"]
    files = {f["name"]: f for f in json.loads(archive("inbox", "batch", batch["id"], "--json")[1])["files"]}
    assert {n: f["decision"] for n, f in files.items()} == {"README.md": "skip", "notes.jsonl": "skip", "manifest.csv": "skip", "письмо.eml": "accept",
                                                            "письмо.json": "skip"}
    assert files["письмо.json"]["reason"] == "служебный файл: описание письма, которое лежит рядом"
    # то же при return: файлы названы теми же причинами, но лежат в папке возврата
    assert archive("inbox", "set", "--no-timer", "--service-fate", "return")[0] == 0
    drop(archive)
    archive.put("письмо2.eml", K.letter(mid="<second@example.org>", subject="Второе письмо"))
    archive.put("письмо2.json", '{"subject": "Второе"}')
    code, out, err = archive("inbox", "run", "--wait")
    assert code == 0, err
    assert returned(archive) == {"README.md": README, "notes.jsonl": NOTES, "manifest.csv": LISTING, "письмо2.json": '{"subject": "Второе"}'}


# ── образец шаблонов: нейтральный синтаксис, а не чьи-то имена файлов ──
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_KEYS = ("--service-name '*.tmp'", "--service-name '*.bak'", "--service-root-name 'index.*'")
FOREIGN_NAMES = re.compile(r"раньше|used to|manifest|манифест|verify_report|jsonl", re.I)


def squeeze(text):
    return " ".join(text.split())


def section(rel, start, end):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        text = f.read()
    return text[text.index(start):text.index(end, text.index(start))]


def test_образец_шаблонов_в_справке_ключей_нейтральный_и_объясняет_зачем_они_нужны(archive):
    help_text = squeeze(archive("inbox", "set", "--help")[1])
    assert "'*.tmp'" in help_text and "'*.bak'" in help_text and "'index.*'" in help_text, help_text
    assert "спутник" in help_text, "справка говорит, зачем нужны шаблоны: файлы-спутники выгрузок, которые в архиве не нужны"
    assert not FOREIGN_NAMES.search(help_text.split("--service-name", 1)[1].split("--no-timer")[0]), "в справке ключей шаблонов чужие имена файлов"


def test_образец_шаблонов_в_докстроке_приёмки_нейтральный():
    import intake
    doc = squeeze(intake.service_reason.__doc__)
    assert "*.tmp" in doc and "*.bak" in doc and "index.*" in doc and not FOREIGN_NAMES.search(doc), doc


@pytest.mark.parametrize("rel, start, end", [("docs/operations.md", "Служебные файлы.", "Эти параметры лежат"),
                                             ("docs/en/operations.md", "Service files.", "These parameters live")])
def test_образец_шаблонов_в_документе_нейтральный(rel, start, end):
    text = squeeze(section(rel, start, end))
    assert all(key in text for key in SAMPLE_KEYS), (rel, text)
    assert "спутник" in text or "companion" in text, f"{rel}: не сказано, зачем нужны шаблоны"
    assert not FOREIGN_NAMES.search(text.replace("`.jsonl`", "")), f"{rel}: образец называет чужие имена файлов или прошлое"

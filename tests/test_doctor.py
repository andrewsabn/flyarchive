"""Команда flyarchive doctor: проверка окружения (FR-107).

Команда проверяет и называет: систему, версию Python, обязательные библиотеки (requirements.txt), библиотеки по форматам (requirements-optional.txt),
программы (bwrap, 7z, dot, docker, systemctl, dsh, tailscale), службу векторов (один запрос, короткий срок, та ли размерность), заведён ли архив,
есть ли таблица индекса, задана ли локальная модель. Ничего не меняет и не создаёт, даже каталог архива. Код возврата 0, когда обязательное на месте;
служба векторов и библиотеки из requirements.txt — обязательное, незаведённый архив — подсказка, а не отказ. Вывод — через каталог сообщений,
ключей и значений секретов в нём нет. Команда настоящая (процесс), машина выдуманная: свой домашний каталог, подставная служба векторов,
отсутствие библиотеки изображается блокировкой импорта в дочернем процессе, а не удалением.
"""
import ast
import importlib.metadata as metadata
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import doctor as D
import messages as M
import settings as S
from archivekit import DEAD_VECTORS, Archive
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов
from test_requirements_files import OPTIONAL, REQUIRED, read_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUSES = {"ok", "missing", "optional_missing"}
SENTINEL = "СЕКРЕТ-ключа-модели-9f3a"
SENTINEL_FILE = "СЕКРЕТ-из-файла-ключа-4c1d"


@pytest.fixture
def archive(tmp_path, vectors):
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})


def report(archive, *args, **env):
    """(код, объект ответа --json, stderr): ответ разобран, чтобы тест видел проверки по именам."""
    code, out, err = archive("doctor", "--json", *args, **env)
    assert out.strip(), err
    return code, json.loads(out), err


def by_id(answer):
    return {c["id"]: c for c in answer["checks"]}


def snapshot(root):
    """Каждый файл и каталог под root: путь -> (размер, время изменения, содержимое). Любая запись видна."""
    out = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs:
            out[os.path.relpath(os.path.join(folder, name), root)] = None
        for name in files:
            path = os.path.join(folder, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = (os.stat(path).st_size, os.stat(path).st_mtime_ns, f.read())
    return out


class Slow:
    """Служба векторов, которая отвечает не сразу: запоминает запросы, а ответ держит `delay` секунд."""

    def __init__(self, delay, dim=1024):
        self.requests, self.delay, self.dim = [], delay, dim
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                owner.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                time.sleep(owner.delay)
                data = json.dumps({"embeddings": [[0.5] * owner.dim]}).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass                                         # клиент уже ушёл: срок вышел

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/embed"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def slow():
    made = []

    def make(delay, dim=1024):
        made.append(Slow(delay, dim))
        return made[-1]

    yield make
    for one in made:
        one.stop()


def machine_env(tmp_path, url, **extra):
    """Окружение для проверки в этом же процессе: свой домашний каталог и каталог архива, служба векторов — подставная."""
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    return {"HOME": str(user), "FLYARCHIVE_HOME": str(user / "архив"), "FLYARCHIVE_EMBED_URL": url, **extra}


# ── что проверяет команда ───────────────────────────────────────
def test_на_пустой_машине_команда_называет_всё_проверенное_и_отвечает_нулём(archive):
    code, answer, err = report(archive)
    assert code == 0 and err == "" and answer["ok"] is True, err
    ids = list(by_id(answer))
    assert ids == ["system", "python"] + D.library_ids() + D.program_ids() + ["preview_python", "font", "embed", "archive", "model"], ids
    assert all(c["status"] in STATUSES for c in answer["checks"])


def test_каждая_проверка_это_id_итог_и_сообщение_из_каталога_с_кодом_параметрами_и_русским_текстом(archive):
    _, answer, _ = report(archive)
    for check in answer["checks"]:
        assert set(check) == {"id", "status", "message"}, check
        message = check["message"]
        assert set(message) == {"code", "args", "text"} and message["text"] and message["code"] in M.CATALOG, check
        assert str(M.make(message["code"], **message["args"])) == message["text"], check


def test_обычный_вывод_называет_каждую_проверку_тем_же_текстом_что_json_и_итог(archive):
    _, answer, _ = report(archive)
    code, out, err = archive("doctor")
    assert code == 0 and err == "", err
    for check in answer["checks"]:
        assert check["message"]["text"] in out, check["id"]
    assert "flyarchive init" in out                                           # архив не заведён: подсказка
    assert str(M.make("doctor.summary_ok")) in out.splitlines()[-1]


def test_система_и_версия_python_названы_и_в_порядке(archive):
    _, answer, _ = report(archive)
    checks = by_id(answer)
    assert (checks["system"]["status"], checks["system"]["message"]["code"]) == ("ok", "doctor.system_ok")
    assert checks["system"]["message"]["args"] == {"system": "Linux"}
    assert (checks["python"]["status"], checks["python"]["message"]["code"]) == ("ok", "doctor.python_ok")
    args = checks["python"]["message"]["args"]
    assert args["need"] == ".".join(map(str, D.PYTHON_MIN)) and re.fullmatch(r"3\.\d+\.\d+", args["version"])


def test_обязательные_библиотеки_названы_с_версиями(archive):
    _, answer, _ = report(archive)
    for package in ("lancedb", "pyarrow", "pymupdf"):
        check = by_id(answer)["lib." + package.lower()]
        assert check["status"] == "ok" and check["message"]["code"] == "doctor.lib_ok", check
        assert check["message"]["args"] == {"package": package, "version": metadata.version(package)}


@pytest.mark.parametrize("module, package", [("lancedb", "lancedb"), ("pyarrow", "pyarrow"), ("pymupdf", "pymupdf")])
def test_без_обязательной_библиотеки_код_1_имя_что_без_неё_не_работает_и_что_поставить(archive, module, package):
    code, answer, _ = report(archive, **archive.hide(module))
    check = by_id(answer)["lib." + package]
    assert code == 1 and answer["ok"] is False
    assert (check["status"], check["message"]["code"]) == ("missing", "lib.missing")
    assert check["message"]["args"]["package"] == package and check["message"]["args"]["file"] == "requirements.txt"
    text = check["message"]["text"]
    assert package in text and "requirements.txt" in text and "без неё" in text
    lost = [c["id"] for c in answer["checks"] if c["status"] == "missing"]
    # lancedb сама грузит pyarrow: без него она не загружается, и проверка называет и её (lib.broken: обязательные библиотеки загружаются)
    assert lost == (["lib.lancedb", "lib.pyarrow"] if package == "pyarrow" else ["lib." + package])
    assert package != "pyarrow" or by_id(answer)["lib.lancedb"]["message"]["code"] == "lib.broken"
    code, out, err = archive("doctor", **archive.hide(module))
    assert code == 1 and text in out and package in out.splitlines()[-1] and err == ""            # итог называет, чего не хватает


@pytest.mark.parametrize("module, package", [("PIL", "Pillow"), ("docx", "python-docx"), ("openpyxl", "openpyxl"), ("pptx", "python-pptx"),
                                             ("extract_msg", "extract-msg"), ("olefile", "olefile"), ("docling", "docling"),
                                             ("matplotlib", "matplotlib"), ("reportlab", "reportlab")])
def test_без_библиотеки_по_формату_код_0_имя_и_что_без_неё_не_работает(archive, module, package):
    code, answer, _ = report(archive, **archive.hide(module))
    check = by_id(answer)["lib." + package.lower()]
    assert code == 0 and answer["ok"] is True
    assert (check["status"], check["message"]["code"]) == ("optional_missing", "lib.missing")
    assert check["message"]["args"]["file"] == "requirements-optional.txt"
    assert package in check["message"]["text"] and "без неё" in check["message"]["text"]
    # скрытая названа среди нехватающих по желанию; остальные необязательные могут не стоять и на настоящей машине (тест от них не зависит)
    assert "lib." + package.lower() in [c["id"] for c in answer["checks"] if c["status"] == "optional_missing"]
    assert all(c["status"] != "missing" for c in answer["checks"] if c["id"].startswith("lib.")), "обязательные на месте"


def test_у_каждой_библиотеки_по_формату_свои_слова_о_том_что_без_неё_не_работает(archive):
    uses = M.WORDS["lib.missing"]["use"]
    assert set(uses) == {d[3] for d in D.LIBRARIES} and len(set(uses.values())) == len(uses)
    assert all(len(text) > 15 for text in uses.values())


def test_таблица_библиотек_команды_совпадает_с_файлами_зависимостей():
    required = {package for _, package, must, _ in D.LIBRARIES if must}
    optional = {package for _, package, must, _ in D.LIBRARIES if not must}
    assert {p.lower() for p in required} == {p.lower() for p in read_file(REQUIRED)}
    assert {p.lower() for p in optional} == {p.lower() for p in read_file(OPTIONAL)}


def test_программы_названы_ни_одна_не_обязательна_и_у_каждой_сказано_что_без_неё(archive):
    code, answer, _ = report(archive, PATH=str(archive.bin))                  # в PATH только заглушка systemctl
    checks = by_id(answer)
    assert code == 0, "ни одна программа не обязательна: поиск без них работает"
    assert checks["tool.systemctl"]["status"] == "ok"
    for name in ("bwrap", "7z", "dot", "docker", "dsh", "tailscale"):
        check = checks["tool." + name]
        assert (check["status"], check["message"]["code"]) == ("optional_missing", "doctor.tool_missing"), name
        assert "без неё" in check["message"]["text"], name
    assert "7z" in checks["tool.7z"]["message"]["text"] and "7za" in checks["tool.7z"]["message"]["text"], "названы все три имени"


def test_распаковщик_находится_под_любым_из_трёх_имён_7z_7za_7zz(archive):
    for name in ("7z", "7za", "7zz"):
        stub = archive.base / f"только-{name}"
        stub.mkdir()
        (stub / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (stub / name).chmod(0o755)
        _, answer, _ = report(archive, PATH=str(stub))
        check = by_id(answer)["tool.7z"]
        assert (check["status"], check["message"]["args"]) == ("ok", {"name": name}), name


def test_каждая_программа_названа_словом_в_таблице_слов(archive):
    uses = M.WORDS["doctor.tool_missing"]["use"]
    assert set(uses) == {d[2] for d in D.PROGRAMS}


# ── служба векторов ─────────────────────────────────────────────
def test_служба_векторов_отвечает_та_размерность_один_запрос_и_названа(archive, vectors):
    code, answer, _ = report(archive)
    check = by_id(answer)["embed"]
    assert code == 0 and (check["status"], check["message"]["code"]) == ("ok", "doctor.embed_ok")
    assert check["message"]["args"] == {"url": vectors["url"], "model": S.SCHEMA["embed_model"].default, "dim": 1024}
    assert len(vectors["requests"]) == 1, "один запрос, не больше"
    assert vectors["requests"][0]["model"] == "bge-m3" and vectors["requests"][0]["options"] == {"num_gpu": 0}


def test_службы_векторов_нет_код_1_адрес_причина_и_что_сделать(archive):
    code, answer, _ = report(archive, FLYARCHIVE_EMBED_URL=DEAD_VECTORS)
    check = by_id(answer)["embed"]
    assert code == 1 and answer["ok"] is False
    assert (check["status"], check["message"]["code"]) == ("missing", "embed.down")
    assert check["message"]["args"]["url"] == DEAD_VECTORS and check["message"]["args"]["model"] == "bge-m3"
    assert "ollama pull bge-m3" in check["message"]["text"] and "embed_url" in check["message"]["text"]
    code, out, err = archive("doctor", FLYARCHIVE_EMBED_URL=DEAD_VECTORS)
    assert code == 1 and err == ""
    assert "embed_url" in out and "embed_url" not in out.splitlines()[-1], "имя настройки — в подробной строке, не в итоге"
    assert "служба векторов" in out.splitlines()[-1], out.splitlines()[-1]


def test_размерность_службы_не_та_что_в_настройке_embed_dim_отказ_с_обеими_цифрами(archive):
    code, answer, _ = report(archive, FLYARCHIVE_EMBED_DIM="16")
    check = by_id(answer)["embed"]
    assert code == 1 and (check["status"], check["message"]["code"]) == ("missing", "doctor.embed_dim")
    assert check["message"]["args"]["got"] == 1024 and check["message"]["args"]["want"] == 16
    assert "embed_dim" in check["message"]["text"] and "1024" in check["message"]["text"] and "16" in check["message"]["text"]


def test_служба_векторов_не_ответила_в_срок_это_отдельное_сообщение_про_первый_запрос_а_не_нет_соединения(tmp_path, slow):
    one = slow(3)
    s = S.load(env=machine_env(tmp_path, one.url))
    started = time.time()
    check = D.check_embed(s, timeout=0.4)
    assert time.time() - started < 2 and check.status == "missing" and len(one.requests) == 1
    assert (check.message.code, check.message.args) == ("embed.slow", {"seconds": 0.4}), check.message
    assert str(check.message) == "служба векторов не ответила за 0.4 с: первый запрос может поднимать модель — повтори проверку"
    assert "не отвечает" not in check.message


def test_срок_целым_числом_секунд_в_сообщении_без_дробной_части(tmp_path, slow):
    one = slow(5)
    s = S.load(env=machine_env(tmp_path, one.url))
    check = D.check_embed(s, timeout=1)
    assert check.message.code == "embed.slow" and check.message.args == {"seconds": 1}
    assert str(check.message).startswith("служба векторов не ответила за 1 с:")


def test_обрыв_при_соединении_по_сроку_тоже_не_ответила_а_не_отвергнуто(tmp_path, monkeypatch):
    import urllib.error

    def stuck(*a, **k):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(D.urllib.request, "urlopen", stuck)
    check = D.check_embed(S.load(env=machine_env(tmp_path, "http://127.0.0.1:9/api/embed")), timeout=2)
    assert (check.status, check.message.code) == ("missing", "embed.slow")


def test_соединение_отвергнуто_сразу_это_прежнее_не_отвечает_без_ожидания(tmp_path):
    s = S.load(env=machine_env(tmp_path, DEAD_VECTORS))
    started = time.time()
    check = D.check_embed(s)                                           # срок по умолчанию — минута, но отказ приходит сразу
    assert time.time() - started < 3 and (check.status, check.message.code) == ("missing", "embed.down")
    assert "не отвечает" in check.message and check.message.args["why"] == "ConnectionRefusedError"


def test_срок_ожидания_первого_ответа_службы_векторов_минута():
    assert D.EMBED_TIMEOUT_S == 60


def test_пока_служба_думает_дольше_нескольких_секунд_называется_одна_строка_ожидания(tmp_path, slow, monkeypatch):
    monkeypatch.setattr(D, "NOTICE_AFTER_S", 0.2)
    one = slow(0.8)
    s = S.load(env=machine_env(tmp_path, one.url))
    said = []
    check = D.check_embed(s, timeout=5, notify=said.append)
    assert check.status == "ok" and len(said) == 1, said
    assert (said[0].code, said[0].args) == ("doctor.embed_waiting", {"seconds": 5}) and "не зависла" in said[0], said[0]


def test_быстрый_ответ_не_печатает_строку_ожидания(tmp_path, slow, monkeypatch):
    monkeypatch.setattr(D, "NOTICE_AFTER_S", 0.3)
    one = slow(0)
    said = []
    assert D.check_embed(S.load(env=machine_env(tmp_path, one.url)), timeout=5, notify=said.append).status == "ok"
    time.sleep(0.5)
    assert said == [], "таймер строки не снят после ответа"


def test_если_весь_срок_ожидания_короче_порога_строка_ожидания_не_печатается(tmp_path, slow, monkeypatch):
    monkeypatch.setattr(D, "NOTICE_AFTER_S", 3)
    one = slow(2)
    said = []
    assert D.check_embed(S.load(env=machine_env(tmp_path, one.url)), timeout=0.3, notify=said.append).message.code == "embed.slow"
    time.sleep(0.2)
    assert said == []


def waiting_line(seconds=D.EMBED_TIMEOUT_S):
    return str(M.make("doctor.embed_waiting", seconds=seconds))


def test_команда_в_текстовом_режиме_на_долгом_ответе_пишет_одну_строку_в_stderr_и_отвечает_нулём(archive, vectors):
    vectors["delay"] = D.NOTICE_AFTER_S + 0.7
    code, out, err = archive("doctor")
    assert code == 0, (out, err)
    assert err.strip().splitlines() == [waiting_line()], err
    assert str(M.make("doctor.summary_ok")) in out.splitlines()[-1] and waiting_line() not in out


def test_команда_с_json_на_долгом_ответе_молчит_и_в_stdout_один_json(archive, vectors):
    vectors["delay"] = D.NOTICE_AFTER_S + 0.7
    code, out, err = archive("doctor", "--json")
    assert code == 0 and err == "", err
    answer = json.loads(out)                                                   # весь stdout — один JSON: строка ожидания в него не попала
    assert answer["ok"] is True and waiting_line() not in out


def test_init_в_хвосте_ждёт_ту_же_проверку_и_пишет_ту_же_строку_в_stderr(archive, vectors):
    vectors["delay"] = D.NOTICE_AFTER_S + 0.7
    code, out, err = archive("init")
    assert code == 0, (out, err)
    assert err.strip().splitlines() == [waiting_line()], err
    assert out.rstrip("\n").splitlines()[-1] == str(M.make("doctor.tail_ok"))


def test_init_с_json_службу_векторов_не_спрашивает_и_строки_ожидания_нет(archive, vectors):
    code, out, err = archive("init", "--json")
    assert code == 0 and err == "" and vectors["requests"] == [], vectors["requests"]


def test_установка_в_хвосте_ждёт_ту_же_проверку_а_с_json_и_в_пробном_прогоне_службу_не_спрашивает(archive, vectors):
    assert archive("init")[0] == 0
    asked = len(vectors["requests"])
    code, out, err = archive("install", "--dry-run")
    assert code == 0 and len(vectors["requests"]) == asked and "Окружение:" not in out and waiting_line() not in err, err
    code, out, err = archive("install", "--no-start", "--json")
    assert code == 0 and len(vectors["requests"]) == asked and waiting_line() not in err, err
    vectors["delay"] = D.NOTICE_AFTER_S + 0.7
    code, out, err = archive("install", "--no-start")
    assert code == 0, (out, err)
    assert waiting_line() in err.splitlines() and out.rstrip("\n").splitlines()[-1] == str(M.make("doctor.tail_ok"))


def test_служба_векторов_ответила_не_то_что_ждали_отказ_с_причиной(tmp_path):
    class Odd(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            data = b'{"error": "model not found"}'
            self.send_response(404)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Odd)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s = S.load(env=machine_env(tmp_path, f"http://127.0.0.1:{server.server_address[1]}/api/embed"))
        check = D.check_embed(s, timeout=2)
    finally:
        server.shutdown()
        server.server_close()
    assert check.status == "missing" and check.message.code == "embed.down" and "404" in check.message.args["why"]


def test_если_embed_gpu_включена_запрос_идёт_без_ограничения_процессором(archive, vectors):
    assert report(archive, FLYARCHIVE_EMBED_GPU="true")[0] == 0
    assert "options" not in vectors["requests"][0]


# ── архив, таблица, модель ──────────────────────────────────────
def test_архив_не_заведён_это_подсказка_а_не_отказ_и_каталог_не_создан(archive):
    code, answer, _ = report(archive)
    check = by_id(answer)["archive"]
    assert code == 0 and (check["status"], check["message"]["code"]) == ("optional_missing", "doctor.archive_missing")
    assert "flyarchive init" in check["message"]["text"] and archive.home in check["message"]["text"]
    assert not os.path.exists(archive.home) and "table" not in by_id(answer)


def test_команда_ничего_не_создаёт_на_пустой_машине_и_ничего_не_меняет_на_заведённом_архиве(archive):
    before = snapshot(archive.base)
    assert archive("doctor")[0] == 0 and report(archive)[0] == 0
    assert snapshot(archive.base) == before, "doctor что-то записал или создал"
    assert archive("init")[0] == 0
    before = snapshot(archive.base)
    assert archive("doctor")[0] == 0 and report(archive)[0] == 0
    assert snapshot(archive.base) == before, "doctor изменил заведённый архив"


def test_заведённый_архив_и_таблица_названы_а_модели_нет_подсказка_называет_оба_пути(archive):
    assert archive("init")[0] == 0
    code, answer, _ = report(archive)
    checks = by_id(answer)
    assert code == 0
    assert (checks["archive"]["status"], checks["archive"]["message"]["code"]) == ("ok", "doctor.archive_ok")
    assert (checks["table"]["status"], checks["table"]["message"]["code"]) == ("ok", "doctor.table_ok")
    model = checks["model"]
    assert (model["status"], model["message"]["code"]) == ("optional_missing", "init.todo_model")
    assert "llm_local_model" in model["message"]["text"] and "flyarchive inbox set --llm off" in model["message"]["text"]


def test_модель_задана_название_без_ключа(archive):
    assert archive("init")[0] == 0
    _, answer, _ = report(archive, FLYARCHIVE_LLM_LOCAL_MODEL="модель-образец")
    model = by_id(answer)["model"]
    assert (model["status"], model["message"]["code"], model["message"]["args"]) == ("ok", "doctor.model_ok", {"model": "модель-образец"})


def test_архив_есть_а_таблицы_индекса_нет_подсказка_про_init_тем_же_сообщением_что_у_поиска(archive):
    assert archive("init")[0] == 0
    import shutil
    shutil.rmtree(archive.path("index", "lance"))
    code, answer, _ = report(archive)
    table = by_id(answer)["table"]
    assert code == 0 and (table["status"], table["message"]["code"]) == ("optional_missing", "index.no_table")
    assert "flyarchive init" in table["message"]["text"] and not os.path.exists(archive.path("index", "lance"))


def test_без_lancedb_таблица_не_проверяется_и_ничего_не_падает(archive):
    assert archive("init")[0] == 0
    code, answer, err = report(archive, **archive.hide("lancedb"))
    assert code == 1 and "Traceback" not in err and "table" not in by_id(answer) and by_id(answer)["lib.lancedb"]["status"] == "missing"


# ── секреты ─────────────────────────────────────────────────────
def test_ключей_и_значений_секретов_в_выводе_нет(archive):
    assert archive("init")[0] == 0
    token = open(archive.path("secrets", "local.token"), encoding="utf-8").read().strip()
    key_file = archive.base / "ключ-модели"
    key_file.write_text(SENTINEL_FILE + "\n", encoding="utf-8")
    key_file.chmod(0o600)
    env = {"FLYARCHIVE_LLM_KEY": SENTINEL, "FLYARCHIVE_LLM_KEY_FILE": str(key_file), "FLYARCHIVE_LLM_LOCAL_MODEL": "модель-образец",
           "FLYARCHIVE_LLM_CLOUD_URL": "http://127.0.0.1:9", "FLYARCHIVE_LLM_CLOUD_MODEL": "облако-образец", "FLYARCHIVE_TOKEN": token}
    for args in ((), ("--json",)):
        code, out, err = archive("doctor", *args, **env)
        assert code == 0, err
        for secret in (SENTINEL, SENTINEL_FILE, token):
            assert secret not in out + err, secret
        assert str(key_file) not in out + err, "и путь к файлу ключа не нужен"


# ── сама проверка как функция ───────────────────────────────────
def test_python_ниже_нижней_границы_отказ_с_версиями(tmp_path, vectors):
    s = S.load(env=machine_env(tmp_path, vectors["url"]))
    old = (D.PYTHON_MIN[0], D.PYTHON_MIN[1] - 1, 7)
    checks = {c.id: c for c in D.collect(s, python=old)}
    assert (checks["python"].status, checks["python"].message.code) == ("missing", "doctor.python_old")
    assert checks["python"].message.args == {"version": ".".join(map(str, old)), "need": ".".join(map(str, D.PYTHON_MIN))}
    now = tuple(D.PYTHON_MIN)
    assert {c.id: c for c in D.collect(s, python=now)}["python"].status == "ok"


@pytest.mark.parametrize("system", ["Windows", "Darwin", "FreeBSD"])
def test_не_linux_система_это_отказ_одной_и_той_же_строкой(tmp_path, vectors, system):
    s = S.load(env=machine_env(tmp_path, vectors["url"]))
    check = {c.id: c for c in D.collect(s, system=system)}["system"]
    assert (check.status, check.message.code) == ("missing", "cli.not_linux")
    assert str(check.message) == "FlyArchive работает на Linux; под Windows — в WSL2"


def test_итог_называет_недостающее_обязательное_по_именам_и_только_его(tmp_path, vectors):
    s = S.load(env=machine_env(tmp_path, DEAD_VECTORS))
    checks = D.collect(s, python=(3, 9, 0))
    summary = D.summary(checks)
    assert summary.code == "doctor.summary_missing"
    names = [n.strip() for n in summary.args["names"].split(",")]
    assert names == ["Python", "служба векторов"], names
    assert D.exit_code(checks) == 1 and D.exit_code([c for c in checks if c.status != "missing"]) == 0


def test_коротко_для_init_и_install_одна_строка_и_та_же_проверка(tmp_path, vectors):
    s = S.load(env=machine_env(tmp_path, vectors["url"]))
    assert D.tail(D.collect(s)).code == "doctor.tail_ok"
    s = S.load(env=machine_env(tmp_path, DEAD_VECTORS))
    line = D.tail(D.collect(s))
    assert line.code == "doctor.tail_missing" and line.args == {"names": "служба векторов"} and "flyarchive doctor" in line and "embed_url" not in line


def test_проверка_собирается_без_сети_к_чужому_адресу_только_адрес_из_настройки(tmp_path):
    with open(os.path.join(ROOT, "tools", "doctor.py"), encoding="utf-8") as f:
        source = f.read()
    assert not re.search(r"https?://", source), "адрес службы векторов — только из настройки embed_url"


def test_весь_вывод_команды_идёт_через_каталог_а_голых_строк_ответа_в_коде_нет():
    with open(os.path.join(ROOT, "tools", "doctor.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "make":
            assert isinstance(node.args[0], ast.Constant) and node.args[0].value in M.CATALOG, ast.dump(node)[:80]


# ── нижняя граница Python ───────────────────────────────────────
def test_нижняя_граница_python_записана_константой_и_по_ней_читается_весь_код_tools():
    assert D.PYTHON_MIN == (3, 10)
    tools = os.path.join(ROOT, "tools")
    for name in sorted(os.listdir(tools)):
        path = os.path.join(tools, name)
        if os.path.isfile(path) and (name.endswith(".py") or name == "flyarchive"):
            with open(path, encoding="utf-8") as f:
                ast.parse(f.read(), feature_version=D.PYTHON_MIN)            # синтаксис не новее нижней границы


def test_обязательные_библиотеки_на_нижней_границе_не_требуют_python_новее_нижней_границы():
    for package in ("lancedb", "pyarrow", "pymupdf"):
        try:
            need = metadata.metadata(package).get("Requires-Python", "")
        except metadata.PackageNotFoundError:
            pytest.skip(f"{package} не стоит")
        m = re.search(r">=\s*(\d+)\.(\d+)", need)
        assert m and (int(m[1]), int(m[2])) <= D.PYTHON_MIN, f"{package}: {need}"


# ── команда в списке команд ─────────────────────────────────────
def test_команда_названа_в_справке_и_в_начале_файла_команды_и_у_неё_есть_ключ_json(archive):
    code, out, _ = archive("--help")
    assert code == 0 and "doctor" in out
    code, out, _ = archive("doctor", "--help")
    assert code == 0 and "--json" in out
    with open(os.path.join(ROOT, "tools", "flyarchive"), encoding="utf-8") as f:
        assert "flyarchive doctor" in f.read().split('"""')[1]


# ── итог init ───────────────────────────────────────────────────
def test_init_в_конце_одной_строкой_говорит_что_обязательное_на_месте(archive):
    code, out, err = archive("init")
    assert code == 0 and err == "", err
    assert out.rstrip("\n").splitlines()[-1] == str(M.make("doctor.tail_ok"))


def test_init_в_конце_называет_чего_обязательного_не_хватает_и_кода_возврата_не_меняет(archive):
    code, out, err = archive("init", FLYARCHIVE_EMBED_URL=DEAD_VECTORS)
    assert code == 0 and err == "", err
    assert out.rstrip("\n").splitlines()[-1] == str(M.make("doctor.tail_missing", names="служба векторов"))
    assert os.path.isdir(archive.path("index", "lance")), "архив заведён и без службы векторов"


def test_init_json_остаётся_одним_json_без_строк_проверки(archive):
    code, out, err = archive("init", "--json", FLYARCHIVE_EMBED_URL=DEAD_VECTORS)
    assert code == 0 and err == ""
    assert set(json.loads(out)) == {"home", "steps", "todo"} and "flyarchive doctor" not in out


def test_init_без_обязательной_библиотеки_называет_её_в_итоге(archive):
    code, out, err = archive("init", **archive.hide("pymupdf"))
    assert code == 0, err
    assert out.rstrip("\n").splitlines()[-1] == str(M.make("doctor.tail_missing", names="pymupdf"))

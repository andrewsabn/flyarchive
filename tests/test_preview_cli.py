"""Команда `flyarchive preview show|page|clean` (FR-76): так её зовёт плагин — через execFile, путь после `--`, ответ JSON или PNG.

Отказ без песочницы и отказы по ссылке проверяются без bwrap; где нужна песочница, тесты пропускаются с причиной.
"""
import json
import os
import subprocess
import sys

import pytest

import gatekit as K
from sandboxkit import needs_sandbox
from test_preview_kit import BATCH, env  # noqa: F401  — общая обвязка

FLYARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")


@pytest.fixture
def call(env):
    def run(*args, sandbox=True):
        empty = env.tmp / "пустой-PATH"
        empty.mkdir(exist_ok=True)
        environ = {**os.environ, "FLYARCHIVE_HOME": env.home, "PYTHONIOENCODING": "utf-8"}
        if not sandbox:
            environ["PATH"] = str(empty)                                        # bwrap в PATH нет: песочницы на этой машине «нет»
        r = subprocess.run([sys.executable, FLYARCHIVE, *args], env=environ, capture_output=True, timeout=180)
        return r.returncode, r.stdout, r.stderr.decode("utf-8")

    return run


def last_error(err):
    """Последняя строка stderr — {"error": {code, args, text}}: так плагин узнаёт отказ."""
    line = err.strip().splitlines()[-1]
    return json.loads(line)["error"]


# ── show ────────────────────────────────────────────────────────
@needs_sandbox
def test_show_текст_json_один_объект_в_stdout(call, env):
    path = env.put("queue", "договор.txt", "Договор поставки.".encode("utf-8"))
    code, out, err = call("preview", "show", "--area", "queue", "--json", "--", path)
    assert code == 0 and err == ""
    answer = json.loads(out.decode("utf-8"))
    assert answer["kind"] == "text" and answer["text"] == "Договор поставки." and answer["truncated"] is False
    assert answer["meta"]["name"] == "договор.txt" and answer["meta"]["batch"] == BATCH and len(answer["meta"]["sha256"]) == 64
    assert len(out.decode("utf-8").strip().splitlines()) == 1


@needs_sandbox
def test_show_ключи_в_том_порядке_как_зовёт_плагин_и_member_до_area(call, env):
    path = env.put("quarantine", "письмо.eml", K.eml_with([("а.txt", b"x"), ("б.txt", "привет".encode("utf-8"))]))
    code, out, err = call("preview", "show", "--member", "1", "--area", "quarantine", "--json", "--", path)
    assert code == 0, err
    answer = json.loads(out.decode("utf-8"))
    assert answer["kind"] == "text" and answer["text"] == "привет" and answer["meta"]["name"] == "б.txt"


def test_show_без_песочницы_none_с_пояснением_и_код_0(call, env):
    path = env.put("queue", "а.pdf", K.pdf_pages(1))
    code, out, err = call("preview", "show", "--area", "queue", "--json", "--", path, sandbox=False)
    assert code == 0, err
    answer = json.loads(out.decode("utf-8"))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.no_sandbox" and len(answer["meta"]["sha256"]) == 64
    assert env.cached() == []


@pytest.mark.parametrize("area,path", [("queue", "очередь/20261004-120000/../../секрет"), ("queue", "/etc/passwd"), ("corpus", "../секрет"),
                                       ("queue", "карантин/20261004-120000/а.txt"), ("queue", "очередь/20261004-120000/нет.txt")])
def test_show_отказ_по_ссылке_код_1_stdout_пуст_последняя_строка_stderr_json(call, env, area, path):
    code, out, err = call("preview", "show", "--area", area, "--json", "--", path)
    assert code == 1 and out == b""
    error = last_error(err)
    assert set(error) == {"code", "args", "text"} and error["code"].startswith("review.") and error["text"]


def test_show_область_не_названа_или_неверна_отказ_с_кодом_а_не_usage(call, env):
    code, out, err = call("preview", "show", "--area", "everything", "--json", "--", "x")
    assert code == 1 and out == b"" and last_error(err)["code"] == "preview.bad_area"
    code, out, err = call("preview", "show", "--json", "--", "x")
    assert code == 1 and last_error(err)["code"] == "preview.bad_area"


def test_show_три_ключа_member_и_негодный_member_отказ(call, env):
    path = env.put("queue", "а.eml", K.EML)
    for members in (["3", "1", "2"], ["-1"], ["x"], ["1.5"]):
        args = [a for m in members for a in ("--member", m)]
        code, out, err = call("preview", "show", *args, "--area", "queue", "--json", "--", path)
        assert code == 1 and out == b"" and last_error(err) == {"code": "preview.bad_member", "args": {"max": 2},
                                                                 "text": "--member: целое число от 0, не больше 2 ключей"}


def test_show_без_json_отказ_печатается_человеку(call, env):
    code, out, err = call("preview", "show", "--area", "queue", "--", "../x")
    assert code == 1 and out == b"" and err.startswith("ошибка: ") and "{" not in err


@needs_sandbox
def test_show_без_json_печатает_для_человека(call, env):
    path = env.put("queue", "заметка.txt", "первая строка\nвторая".encode("utf-8"))
    code, out, err = call("preview", "show", "--area", "queue", "--", path)
    text = out.decode("utf-8")
    assert code == 0 and "заметка.txt" in text and "первая строка\nвторая" in text
    code, out, err = call("preview", "show", "--area", "queue", "--", env.put("queue", "setup.bin", K.PE))
    assert code == 0 and "программа или скрипт" in out.decode("utf-8")


# ── page ────────────────────────────────────────────────────────
@needs_sandbox
def test_page_png_в_stdout_и_ничего_в_stderr(call, env):
    pytest.importorskip("pymupdf")
    path = env.put("queue", "отчёт.pdf", K.pdf_pages(2))
    code, out, err = call("preview", "page", "--area", "queue", "--", path, "2")
    assert code == 0 and err == "" and out[:8] == b"\x89PNG\r\n\x1a\n" and out[-12:] == b"\x00\x00\x00\x00IEND\xaeB`\x82"
    assert call("preview", "page", "--area", "queue", "--", path, "2")[1] == out               # из кэша: те же байты


@needs_sandbox
def test_page_картинка_страница_один(call, env):
    pytest.importorskip("PIL")
    path = env.put("corpus", "фото.jpg", K.image_bytes("JPEG", (30, 20)))
    code, out, err = call("preview", "page", "--area", "corpus", "--", path, "1")
    assert code == 0 and out[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("number", ["0", "-1", "abc", "1.5", ""])
def test_page_негодный_номер_отказ_код_1_stdout_пуст_и_json_в_stderr(call, env, number):
    path = env.put("queue", "а.pdf", b"x")
    code, out, err = call("preview", "page", "--area", "queue", "--", path, number)
    assert code == 1 and out == b"" and last_error(err)["code"] == "preview.bad_page"


@needs_sandbox
def test_page_вне_диапазона_и_у_объекта_без_страниц_отказ_код_1_stdout_пуст(call, env):
    pytest.importorskip("pymupdf")
    pdf = env.put("queue", "а.pdf", K.pdf_pages(2))
    code, out, err = call("preview", "page", "--area", "queue", "--", pdf, "3")
    assert code == 1 and out == b"" and last_error(err) == {"code": "preview.no_page", "args": {"page": 3, "shown": 2}, "text": last_error(err)["text"]}
    text = env.put("queue", "а.txt", b"hello")
    code, out, err = call("preview", "page", "--area", "queue", "--", text, "1")
    assert code == 1 and out == b"" and last_error(err)["code"] == "preview.no_pages"


def test_page_без_песочницы_отказ_код_1_stdout_пуст(call, env):
    path = env.put("queue", "а.pdf", K.pdf_pages(1))
    code, out, err = call("preview", "page", "--area", "queue", "--", path, "1", sandbox=False)
    assert code == 1 and out == b"" and last_error(err)["code"] == "preview.no_sandbox"


def test_page_отказ_по_ссылке(call, env):
    code, out, err = call("preview", "page", "--area", "queue", "--", "очередь/20261004-120000/../../секрет", "1")
    assert code == 1 and out == b"" and last_error(err)["code"] == "review.bad_path"
    code, out, err = call("preview", "page", "--member", "0", "--area", "queue", "--", "очередь/20261004-120000/../../секрет", "1")
    assert code == 1 and out == b"" and last_error(err)["code"] == "review.bad_path"


def test_page_ключа_json_нет_но_ошибка_всегда_json(call, env):
    code, out, err = call("preview", "page", "--area", "queue", "--", "../x", "1")
    assert code == 1 and last_error(err)["code"] == "review.bad_path"
    code, out, err = call("preview", "page", "--area", "queue", "--json", "--", "x", "1")        # ключ --json у page не принимается
    assert code == 2


# ── clean ───────────────────────────────────────────────────────
def test_clean_чистит_старое_и_печатает_итог(call, env):
    old = os.path.join(env.cache, "a" * 64)
    os.makedirs(old)
    with open(os.path.join(old, "page-1.png"), "wb") as f:
        f.write(b"p" * 1000)
    long_ago = 1_500_000_000
    for p in (old, os.path.join(old, "page-1.png")):
        os.utime(p, (long_ago, long_ago))
    code, out, err = call("preview", "clean", "--json")
    assert code == 0 and json.loads(out.decode("utf-8")) == {"removed": 1, "freed": 1000, "left": 0} and not os.path.exists(old)
    code, out, err = call("preview", "clean")
    assert code == 0 and "Убрано: 0" in out.decode("utf-8")


def test_clean_без_кэша_не_падает(call, env):
    code, out, err = call("preview", "clean", "--json")
    assert code == 0 and json.loads(out.decode("utf-8")) == {"removed": 0, "freed": 0, "left": 0}

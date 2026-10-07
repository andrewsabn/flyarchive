"""Просмотр писем, архивов и календаря: проверки настоящим bwrap и настоящей командой `flyarchive preview` (FR-77).

Песочница настоящая, рабочий процесс настоящий, PyMuPDF, Pillow, extract_msg и 7z настоящие, образцы маленькие. Если на машине нет bwrap,
тесты пропускаются с причиной. «Большое» — только заявленным размером: письмо с вложением в гигабайт — это файл в килобайты.
"""
import datetime
import json
import os
import shutil
import struct
import subprocess
import time

import pytest

import gatekit as K
import preview as P
from test_preview_cli import call, last_error  # noqa: F401  — как плагин зовёт команду
from test_preview_kit import BATCH, env  # noqa: F401
from sandboxkit import needs_sandbox
from test_preview_sandbox import listener, sandboxed, spy  # noqa: F401

pytestmark = needs_sandbox
needs_7z = pytest.mark.skipif(shutil.which("7z") is None, reason="на машине нет программы 7z")


def dims(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def show(env, path, members=(), area="queue"):
    return P.show(env.home, area, path, list(members))


def tree_of(root, skip=()):
    found = set()
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if os.path.join(folder, d) not in skip]
        found |= {os.path.relpath(os.path.join(folder, n), root) for n in dirs + files}
    return found


# ── письмо с вложениями ─────────────────────────────────────────
def letter_with_everything():
    inner = K.eml_message(subject="Пересланное", body="Текст пересланного письма.", attachments=[("глубокое.pdf", K.pdf_pages(2))])
    return K.eml_with([("записка.txt", "Привет из вложения.".encode("utf-8")), ("фото.png", K.image_bytes("PNG", (60, 40))), ("отчёт.pdf", K.pdf_pages(3)),
                       ("пересланное.eml", inner), ("настройка.exe", K.PE)])


def test_письмо_описание_и_открытие_каждого_вложения_настоящим_запуском(env, spy):
    pytest.importorskip("pymupdf")
    pytest.importorskip("PIL")
    mail = env.put("queue", "письмо.eml", letter_with_everything())
    answer = show(env, mail)
    assert answer["kind"] == "mail" and answer["meta"]["type"] == "eml"
    body = answer["mail"]
    assert body["from"] == "Petrov <petrov@example.org>" and body["to"] == ["ivanov@example.org"] and body["subject"] == "Договор" and body["truncated"] is False
    assert [(a["name"], a["type"], a["member"], a["executable"]) for a in body["attachments"]] == [
        ("записка.txt", "txt", 0, False), ("фото.png", "png", 1, False), ("отчёт.pdf", "pdf", 2, False), ("пересланное.eml", "eml", 3, False),
        ("настройка.exe", "pe", 4, True)]
    text = show(env, mail, [0])
    assert text["kind"] == "text" and text["text"] == "Привет из вложения." and text["meta"]["name"] == "записка.txt" and text["meta"]["type"] == "txt"
    picture = show(env, mail, [1])
    assert picture["kind"] == "image" and dims(P.page(env.home, "queue", mail, "1", [1])) == (60, 40)
    report = show(env, mail, [2])
    assert report["kind"] == "pages" and (report["pages"], report["shown"]) == (3, 3) and dims(P.page(env.home, "queue", mail, "2", [2])) == (1224, 1584)
    forwarded = show(env, mail, [3])
    assert forwarded["kind"] == "mail" and forwarded["mail"]["subject"] == "Пересланное" and forwarded["mail"]["attachments"][0]["name"] == "глубокое.pdf"
    deep = show(env, mail, [3, 0])
    assert deep["kind"] == "pages" and deep["pages"] == 2 and deep["meta"]["name"] == "глубокое.pdf" and dims(P.page(env.home, "queue", mail, "1", [3, 0])) == (1224, 1584)
    program = show(env, mail, [4])
    assert program["kind"] == "none" and program["note"]["code"] == "preview.program" and program["meta"]["type"] == "pe" and "text" not in program
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", mail, "1", [4])
    assert e.value.message.code == "preview.program"
    assert not [n for n in os.listdir(env.cache) if n.startswith(".work")]


def test_номер_вложения_вне_списка_отказ_настоящим_запуском_и_ничего_не_лежит_в_кэше(env, spy):
    mail = env.put("queue", "письмо.eml", K.eml_with([("а.txt", b"first"), ("б.txt", b"second")]))
    for number in (2, 5, 199, 200, 10 ** 6):
        with pytest.raises(P.PreviewError) as e:
            show(env, mail, [number])
        assert e.value.message.code == "preview.no_member" and e.value.message.args == {"member": number, "count": 2}
    with pytest.raises(P.PreviewError) as e:
        show(env, env.put("queue", "заметка.txt", "просто текст".encode("utf-8")), [0])
    assert e.value.message.args == {"member": 0, "count": 0} and env.cached() == []
    assert show(env, mail, [0])["text"] == "first" and show(env, mail, [1])["text"] == "second"           # свои вложения, а не соседние


def test_два_вложения_одного_письма_и_одно_вложение_из_двух_писем_кэш_по_sha256_вложения(env, spy):
    first = env.put("queue", "письмо-1.eml", K.eml_with([("а.txt", b"same attachment"), ("б.txt", b"another one")]))
    second = env.put("queue", "письмо-2.eml", K.eml_with([("в.txt", b"unrelated"), ("г.txt", b"same attachment")], subject="Другое"))
    assert show(env, first, [0])["text"] == "same attachment" and show(env, first, [1])["text"] == "another one"
    before = spy.count("describe")
    answer = show(env, second, [1])
    assert answer["text"] == "same attachment" and spy.count("describe") == before                       # то же вложение из другого письма: описание из кэша
    shas = [show(env, first, [0])["meta"]["sha256"], show(env, first, [1])["meta"]["sha256"], show(env, second, [0])["meta"]["sha256"]]
    assert len(set(shas)) == 3 and show(env, second, [1])["meta"]["sha256"] == shas[0]
    assert sorted(n for n in os.listdir(env.cache) if len(n) == 64) == sorted(shas)                       # три разных вложения — три папки, а не четыре


def test_письмо_msg_настоящим_запуском_описание_и_вложения(env, spy):
    pytest.importorskip("extract_msg")
    pytest.importorskip("pymupdf")
    inner = K.msg_tree(subject="Пересланное", body="внутри", attachments=[("глубокое.txt", b"deep text")], embedded=True)
    data = K.msg_bytes(subject="Договор", body="Добрый день из Outlook.", sender="Иван", date=datetime.datetime(2026, 10, 5, 10, 0, tzinfo=datetime.timezone.utc),
                       recipients=[("to", "Анна", "ann@example.test"), ("cc", "Борис", "bob@example.test")],
                       attachments=[("записка.txt", "привет из msg".encode("utf-8")), ("пересланное.msg", inner), ("настройка.exe", K.PE)])
    mail = env.put("queue", "письмо.msg", data)
    answer = show(env, mail)
    body = answer["mail"]
    assert answer["kind"] == "mail" and answer["meta"]["type"] == "msg" and body["subject"] == "Договор" and body["to"] == ["Анна <ann@example.test>"]
    assert body["cc"] == ["Борис <bob@example.test>"] and [(a["name"], a["type"], a["executable"]) for a in body["attachments"]] == [
        ("записка.txt", "txt", False), ("пересланное.msg", "msg", False), ("настройка.exe", "pe", True)]
    assert show(env, mail, [0])["text"] == "привет из msg"
    assert show(env, mail, [1])["mail"]["subject"] == "Пересланное" and show(env, mail, [1, 0])["text"] == "deep text"
    assert show(env, mail, [2])["note"]["code"] == "preview.program"
    with pytest.raises(P.PreviewError) as e:
        show(env, mail, [3])
    assert e.value.message.code == "preview.no_member" and e.value.message.args == {"member": 3, "count": 3}


def test_html_письма_настоящим_запуском_наружу_текст_без_разметки(env, spy):
    html = "<html><body><script>alert(1)</script><p>Видимый <b>текст</b></p><img src=\"http://127.0.0.1:9/x.png\"></body></html>"
    answer = show(env, env.put("queue", "письмо.eml", K.eml_with(body=" ", html=html)))
    text = answer["mail"]["text"]
    assert "Видимый" in text and "текст" in text and "<" not in text and "alert" not in text and "127.0.0.1" not in text


def test_письмо_с_вложением_объявленным_в_гигабайт_но_без_тела_быстро_и_без_падения(env, spy):
    started = time.monotonic()
    eml = env.put("queue", "объявлено.eml", K.eml_declared(1 << 30))
    answer = show(env, eml)
    eml_sha = answer["meta"]["sha256"]
    assert answer["kind"] == "mail" and answer["mail"]["attachments"] == [{"name": "big.pdf", "size": 0, "type": "empty", "member": 0, "executable": False}]
    empty = show(env, eml, [0])
    assert empty["kind"] == "none" and empty["note"]["code"] == "preview.empty" and empty["meta"]["size"] == 0
    pytest.importorskip("extract_msg")
    giant = env.put("queue", "объявлено.msg", K.msg_bytes(attachments=[("большое.bin", (b"y" * 5000, 1 << 30))]))
    answer = show(env, giant)
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.broken" and answer["note"]["args"] == {"error_type": "OleFileError"}
    refused = show(env, giant, [0])
    assert refused["kind"] == "none" and refused["note"]["code"] == "preview.broken" and refused["meta"]["sha256"] is None
    assert time.monotonic() - started < 60 and env.cached() == [f"{eml_sha}/desc.json"]                    # в кэше только описание письма: отказы не кэшируются


def test_письмо_с_именами_вложений_из_путей_ничего_не_пишет_вне_кэша(env, spy):
    mail = env.put("queue", "письмо.eml", K.eml_with([("../../escape.txt", b"payload"), ("/etc/cron.d/x", b"payload 2")]))
    before = tree_of(env.tmp, skip={os.path.join(env.tmp, "flyarchive", "cache")})
    assert [a["name"] for a in show(env, mail)["mail"]["attachments"]] == ["../../escape.txt", "/etc/cron.d/x"]
    assert show(env, mail, [0])["text"] == "payload" and show(env, mail, [1])["text"] == "payload 2"
    assert tree_of(env.tmp, skip={os.path.join(env.tmp, "flyarchive", "cache")}) == before and not os.path.exists(os.path.join(env.tmp, "escape.txt"))


# ── архивы ──────────────────────────────────────────────────────
def test_zip_с_именем_за_каталог_и_zip_с_тысячей_файлов_настоящим_запуском(env, spy):
    names = ["../x", "/abs/path.txt", "..\\win.txt", "a/../../b"]
    archive = env.put("queue", "архив.zip", K.zip_raw([(name, b"1") for name in names]))
    before = tree_of(env.tmp, skip={os.path.join(env.tmp, "flyarchive", "cache")})
    answer = show(env, archive)
    assert answer["kind"] == "listing" and [row["name"] for row in answer["listing"]] == names and answer["truncated"] is False
    assert tree_of(env.tmp, skip={os.path.join(env.tmp, "flyarchive", "cache")}) == before          # на диск из архива не вышло ничего
    many = show(env, env.put("queue", "тысяча.zip", K.zip_many(1000)))
    assert many["kind"] == "listing" and len(many["listing"]) == 500 and many["truncated"] is True and many["listing"][0] == {"name": "файл-0.txt", "size": 1}
    exact = show(env, env.put("queue", "пятьсот.zip", K.zip_many(500)))
    assert len(exact["listing"]) == 500 and exact["truncated"] is False
    assert not os.path.exists("/abs") and not os.path.exists(os.path.join(os.path.dirname(env.tmp), "x"))


def test_tar_со_ссылкой_и_сжатый_tar_настоящим_запуском(env, spy):
    data = K.tar_bytes({"а.txt": b"hello", "папка/б.bin": bytes(100)}, links={"ссылка": "/etc/passwd"})
    answer = show(env, env.put("queue", "архив.tar", data))
    assert answer["kind"] == "listing" and [(r["name"], r["size"]) for r in answer["listing"]] == [("а.txt", 5), ("папка/б.bin", 100), ("ссылка", 0)]
    packed = show(env, env.put("queue", "архив.tar.gz", K.tar_bytes({"в.txt": b"x" * 40}, mode="w:gz")))
    assert packed["kind"] == "listing" and packed["listing"] == [{"name": "в.txt", "size": 40}]


@needs_7z
def test_7z_и_rar_настоящим_запуском_программа_7z_видна_в_песочнице(env, spy):
    answer = show(env, env.put("queue", "архив.7z", K.SEVENZ_PLAIN))
    assert answer["kind"] == "listing" and [(r["name"], r["size"]) for r in answer["listing"]] == [("а.txt", 12), ("папка/б.bin", 100)]
    answer = show(env, env.put("queue", "архив.rar", K.rar5([("а.txt", "привет".encode("utf-8")), ("папка/б.bin", bytes(100))])))
    assert answer["kind"] == "listing" and answer["meta"]["type"] == "rar" and [(r["name"], r["size"]) for r in answer["listing"]] == [("а.txt", 12), ("папка/б.bin", 100)]
    for blob in (K.SEVENZ_PASSWORD, K.SEVENZ_HEADERS):
        answer = show(env, env.put("queue", f"закрытый-{len(blob)}.7z", blob))
        assert answer["kind"] == "none" and answer["note"]["code"] == "preview.encrypted"
    answer = show(env, env.put("queue", "поломанный.7z", K.SEVENZ))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.broken" and answer["note"]["args"] == {"error_type": "ListingFailed"}
    assert len(env.cached()) == 2                                                                         # в кэше два состава; отказы не кэшируются


def test_zip_с_паролем_и_оборванный_zip_настоящим_запуском(env, spy):
    answer = show(env, env.put("queue", "закрытый.zip", K.zip_encrypted_flag({"а.txt": b"x"})))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.encrypted"
    answer = show(env, env.put("queue", "оборванный.zip", K.zip_bytes({"а.txt": b"x" * 200})[:-12]))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.broken" and answer["note"]["args"] == {"error_type": "BadZipFile"}
    assert env.cached() == []


# ── календарь ───────────────────────────────────────────────────
def test_календарь_настоящим_запуском_поля_событий(env, spy):
    answer = show(env, env.put("queue", "события.ics", K.ics([{"summary": "Встреча по договору", "start": "20261005T100000Z", "end": "20261005T110000Z",
                                                                "location": "Комната 5", "attendees": 3}])))
    assert answer["kind"] == "text" and answer["meta"]["type"] == "ics" and answer["truncated"] is False
    assert answer["text"] == "Event 1\nWhen: 2026-10-05 10:00 UTC — 2026-10-05 11:00 UTC\nSubject: Встреча по договору\nLocation: Комната 5\nAttendees: 3"
    empty = show(env, env.put("queue", "пустой.ics", b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"))
    assert empty["kind"] == "none" and empty["note"]["code"] == "preview.no_events"


# ── песочница та же: сети нет, архива не видно, 7z видна ─────────
PROBE_7Z = '''
import json, os, socket, subprocess, sys
port, archive = int(sys.argv[1]), sys.argv[2]
rep = {}
try:
    socket.create_connection(("127.0.0.1", port), timeout=2).close()
    rep["connect"] = "ok"
except Exception as e:
    rep["connect"] = type(e).__name__
rep["archive"] = os.path.exists(archive) or os.path.exists(os.path.dirname(archive))
r = subprocess.run(["7z", "l", "-slt", "-ba", "-pflyarchive-no-password", "--", "/in/data"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
rep["seven"] = [r.returncode, r.stdout.count("Path = ")]
rep["write_in"] = None
try:
    open("/in/data", "ab")
except Exception as e:
    rep["write_in"] = type(e).__name__
json.dump(rep, open("/out/report.json", "w"))
'''


@needs_7z
def test_изнутри_песочницы_7z_работает_а_сети_нет_и_архив_не_виден(env, listener, tmp_path, monkeypatch):
    monkeypatch.setenv("FLYARCHIVE_HOME", env.home)
    code = tmp_path / "код"
    code.mkdir()
    (code / "probe.py").write_text(PROBE_7Z, encoding="utf-8")
    source = tmp_path / "вход.7z"
    source.write_bytes(K.SEVENZ_PLAIN)
    with open(source, "rb") as fd:
        run, out = sandboxed(env, code, "probe.py", [str(listener.port), env.home], fd=fd.fileno())
    assert run.code == 0, run
    rep = json.loads(open(os.path.join(out, "report.json"), encoding="utf-8").read())
    assert rep["seven"] == [0, 3] and rep["connect"] != "ok" and not listener.accepted() and rep["archive"] is False and rep["write_in"] is not None


def test_рабочий_процесс_письма_настоящий_запуск_без_сети_даже_с_html_ссылкой_и_сценарием(env, listener, spy):
    html = f"<html><body><img src=\"http://127.0.0.1:{listener.port}/x.png\"><script>fetch('http://127.0.0.1:{listener.port}/')</script><p>Видимый текст</p></body></html>"
    answer = show(env, env.put("queue", "письмо.eml", K.eml_with(body=" ", html=html, attachments=[("вредный.svg", K.svg_bytes(script=True).replace(
        b"127.0.0.1:9", f"127.0.0.1:{listener.port}".encode()))])))
    assert "Видимый текст" in answer["mail"]["text"] and not listener.accepted()
    pytest.importorskip("pymupdf")
    svg = show(env, env.put("queue", "письмо.eml", K.eml_with(body=" ", html=html, attachments=[("вредный.svg", K.svg_bytes(script=True).replace(
        b"127.0.0.1:9", f"127.0.0.1:{listener.port}".encode()))])), [0])
    assert svg["kind"] == "pages" and not listener.accepted()


# ── команда flyarchive preview ────────────────────────────────────
def test_команда_show_member_json_и_page_member_png(call, env):
    pytest.importorskip("pymupdf")
    path = env.put("queue", "письмо.eml", K.eml_with([("записка.txt", "Привет".encode("utf-8")), ("отчёт.pdf", K.pdf_pages(2))]))
    code, out, err = call("preview", "show", "--area", "queue", "--json", "--", path)
    answer = json.loads(out.decode("utf-8"))
    assert code == 0 and err == "" and answer["kind"] == "mail" and [a["member"] for a in answer["mail"]["attachments"]] == [0, 1]
    code, out, err = call("preview", "show", "--member", "0", "--area", "queue", "--json", "--", path)
    assert code == 0 and err == "" and json.loads(out.decode("utf-8"))["text"] == "Привет"
    code, out, err = call("preview", "show", "--member", "1", "--area", "queue", "--json", "--", path)
    assert json.loads(out.decode("utf-8"))["kind"] == "pages"
    code, out, err = call("preview", "page", "--member", "1", "--area", "queue", "--", path, "2")
    assert code == 0 and err == "" and out[:8] == b"\x89PNG\r\n\x1a\n" and out[-12:] == b"\x00\x00\x00\x00IEND\xaeB`\x82"


def test_команда_номер_вложения_вне_списка_код_1_stdout_пуст_json_в_stderr(call, env):
    path = env.put("queue", "письмо.eml", K.eml_with([("а.txt", b"x")]))
    for args in (("show", "--member", "5", "--area", "queue", "--json", "--", path), ("page", "--member", "5", "--area", "queue", "--", path, "1"),
                 ("show", "--member", "0", "--member", "3", "--area", "queue", "--json", "--", path)):
        code, out, err = call("preview", *args)
        assert code == 1 and out == b"", args
        error = last_error(err)
        assert error["code"] == "preview.no_member" and error["args"] in ({"member": 5, "count": 1}, {"member": 3, "count": 0}) and error["text"]
    code, out, err = call("preview", "show", "--member", "0", "--area", "queue", "--json", "--", env.put("queue", "заметка.txt", "текст".encode("utf-8")))
    assert code == 1 and last_error(err)["args"] == {"member": 0, "count": 0}


def test_команда_без_песочницы_вложение_none_с_пояснением_код_0(call, env):
    path = env.put("queue", "письмо.eml", K.eml_with([("а.txt", b"x")]))
    code, out, err = call("preview", "show", "--member", "0", "--area", "queue", "--json", "--", path, sandbox=False)
    answer = json.loads(out.decode("utf-8"))
    assert code == 0 and answer["kind"] == "none" and answer["note"]["code"] == "preview.no_sandbox" and answer["meta"]["sha256"] is None
    code, out, err = call("preview", "page", "--member", "0", "--area", "queue", "--", path, "1", sandbox=False)
    assert code == 1 and out == b"" and last_error(err)["code"] == "preview.no_sandbox"


def test_команда_без_json_письмо_архив_и_календарь_для_человека(call, env):
    mail = env.put("queue", "письмо.eml", K.eml_with([("записка.txt", "Привет".encode("utf-8")), ("настройка.exe", K.PE)], cc="Борис <bob@example.org>"))
    code, out, err = call("preview", "show", "--area", "queue", "--", mail)
    text = out.decode("utf-8")
    assert code == 0 and err == "" and "письмо.eml" in text and "Petrov <petrov@example.org>" in text and "Договор" in text and "Борис <bob@example.org>" in text
    assert "Добрый день." in text and "записка.txt" in text and "настройка.exe" in text and "--member" in text
    archive = env.put("queue", "архив.zip", K.zip_raw([("а.txt", b"hello"), ("папка/б.bin", bytes(100))]))
    code, out, err = call("preview", "show", "--area", "queue", "--", archive)
    text = out.decode("utf-8")
    assert code == 0 and "а.txt" in text and "папка/б.bin" in text and "100" in text
    many = env.put("queue", "тысяча.zip", K.zip_many(1000))
    code, out, err = call("preview", "show", "--area", "queue", "--", many)
    assert code == 0 and "500" in out.decode("utf-8") and "файл-499.txt" in out.decode("utf-8") and "файл-500.txt" not in out.decode("utf-8")
    code, out, err = call("preview", "show", "--area", "queue", "--", env.put("queue", "события.ics", K.ics([{"summary": "Встреча", "start": "20261005T100000Z"}])))
    assert code == 0 and "Встреча" in out.decode("utf-8") and "BEGIN" not in out.decode("utf-8")
    code, out, err = call("preview", "show", "--member", "1", "--area", "queue", "--", mail)
    assert code == 0 and "программа" in out.decode("utf-8")

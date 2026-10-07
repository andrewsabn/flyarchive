"""Рабочий процесс просмотра: состав архивов без распаковки и календарь (FR-77). Рабочий процесс вызывается в этом же процессе
на маленьких образцах; запуск в настоящем bwrap — в test_preview_mail_sandbox.py.

Архив — данные: на диск из него не пишется ничего, имена не становятся путями. «Большое» — только заявленным размером.
"""
import gzip
import json
import os
import shutil
import stat
import tarfile
import zipfile

import pytest

import gatekit as K
import preview as P
import preview_worker as W
from test_preview_worker import run

HAS_7Z = shutil.which("7z") is not None
needs_7z = pytest.mark.skipif(not HAS_7Z, reason="на машине нет программы 7z")


def listing_of(tmp, data, name):
    result, out = run(tmp, data, name)
    assert result["kind"] == "listing", result
    assert P._check(json.loads(json.dumps(result, ensure_ascii=False)), "describe")["kind"] == "listing"        # то, что родитель примет
    assert os.listdir(out) == []
    return result


def rows(result):
    return [(row["name"], row["size"]) for row in result["listing"]]


# ── zip ─────────────────────────────────────────────────────────
def test_zip_имена_и_размеры_без_каталогов(tmp_path):
    data = K.zip_raw([("а.txt", "привет".encode("utf-8")), ("папка/", b""), ("папка/б.bin", bytes(100)), ("виндовая\\", b"")])       # последняя — каталог из Windows
    result = listing_of(tmp_path, data, "архив.zip")
    assert result == {"kind": "listing", "type": "zip", "listing": [{"name": "а.txt", "size": 12}, {"name": "папка/б.bin", "size": 100}],
                      "truncated": False}


@pytest.mark.parametrize("count,shown,truncated", [(0, 0, False), (1, 1, False), (499, 499, False), (500, 500, False), (501, 500, True), (1000, 500, True)])
def test_zip_не_больше_500_строк_и_признак_обрезки(tmp_path, count, shown, truncated):
    result = listing_of(tmp_path, K.zip_many(count), "архив.zip") if count else listing_of(tmp_path, K.zip_bytes({}), "архив.zip")
    assert len(result["listing"]) == shown and result["truncated"] is truncated
    if shown:
        assert result["listing"][0]["name"] == "файл-0.txt" and result["listing"][-1]["name"] == f"файл-{shown - 1}.txt"


def test_zip_с_именами_выходящими_за_каталог_имена_данные_и_на_диск_ничего_не_пишется(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    names = ["../x", "/abs/path.txt", "..\\win.txt", "a/../../b", "C:/Windows/system32/x.dll", "./точка.txt"]
    result = listing_of(tmp_path, K.zip_raw([(name, b"1") for name in names]), "архив.zip")
    assert [row["name"] for row in result["listing"]] == names
    assert os.listdir(work) == [] and sorted(os.listdir(tmp_path)) == ["cwd", "data", "out"]
    assert not os.path.exists(tmp_path.parent / "x") and not os.path.exists("/abs")


def test_zip_ни_распаковки_ни_чтения_содержимого(tmp_path, monkeypatch):
    data = K.zip_bytes({"а.txt": b"x"})

    def forbidden(*a, **k):
        raise AssertionError("содержимое архива не читается")

    for name in ("extract", "extractall", "read", "open", "testzip"):
        monkeypatch.setattr(zipfile.ZipFile, name, forbidden)
    assert listing_of(tmp_path, data, "архив.zip")["listing"] == [{"name": "а.txt", "size": 1}]


def test_zip_имена_без_управляющих_знаков_и_не_длиннее_255(tmp_path):
    result = listing_of(tmp_path, K.zip_raw([("a\x1bb\nc.txt", b"x"), ("\u202eexe.txt", b"x"), ("я" * 400, b"x"), ("я" * 255, b"x"), ("я" * 256, b"x")]), "архив.zip")
    names = [row["name"] for row in result["listing"]]
    assert names[0] == "a\ufffdb\ufffdc.txt" and names[1] == "\ufffdexe.txt"
    assert len(names[2]) == 255 and names[2].endswith("…") and names[3] == "я" * 255 and len(names[4]) == 255 and names[4].endswith("…")


def test_zip_русские_имена_в_кодировке_dos(tmp_path):
    assert rows(listing_of(tmp_path, K.zip_cp866("договор.txt", b"x"), "архив.zip")) == [("договор.txt", 1)]


def test_zip_с_паролем_none_encrypted_без_падения(tmp_path):
    result, out = run(tmp_path, K.zip_encrypted_flag({"а.txt": b"x"}), "закрытый.zip")
    assert result == {"kind": "none", "type": "zip", "reason": "encrypted", "args": {}} and os.listdir(out) == []
    assert W.main(["describe", str(tmp_path / "data"), str(out), "zip"]) == 0
    assert json.loads((out / "result.json").read_text(encoding="utf-8"))["reason"] == "encrypted"


def test_zip_с_паролем_на_одном_файле_из_многих_тоже_none(tmp_path):
    data = bytearray(K.zip_bytes({"а.txt": b"x", "б.txt": b"y"}))
    last = data.rfind(b"PK\x01\x02")
    data[last + 8] |= 1                                                           # признак шифрования только у последней записи оглавления
    assert run(tmp_path, bytes(data), "частично.zip")[0]["reason"] == "encrypted"


def test_zip_оборванный_none_broken(tmp_path):
    data = K.zip_bytes({"а.txt": b"x" * 200})
    result, out = run(tmp_path, data[:-12], "оборванный.zip")
    assert result == {"kind": "none", "type": "broken-zip", "reason": "broken", "args": {"error_type": "BadZipFile"}} and os.listdir(out) == []


def test_zip_документ_office_и_программа_архивами_не_считаются(tmp_path):
    assert run(tmp_path, K.ooxml("docx"), "а.docx")[0]["reason"] == "needs_converter"
    assert run(tmp_path, K.jar(), "а.jar")[0]["reason"] == "program"


# ── tar и сжатые tar ────────────────────────────────────────────
def test_tar_имена_размеры_и_ссылки_без_перехода_по_ним(tmp_path):
    data = K.tar_bytes({"а.txt": b"hello", "папка/б.bin": bytes(100)}, links={"ссылка": "/etc/passwd", "наверх": "../../x"})
    result = listing_of(tmp_path, data, "архив.tar")
    assert result["type"] == "tar" and result["truncated"] is False
    assert rows(result) == [("а.txt", 5), ("папка/б.bin", 100), ("ссылка", 0), ("наверх", 0)]


def test_tar_ни_распаковки_ни_чтения_содержимого_и_на_диск_ничего_не_пишется(tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("архив не распаковывается")

    for name in ("extract", "extractall", "extractfile", "_extract_member"):
        monkeypatch.setattr(tarfile.TarFile, name, forbidden)
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    data = K.tar_bytes({"../x": b"1", "/abs/y.txt": b"2", "a/../../b": b"3"}, links={"л": "../../z"})
    assert [row["name"] for row in listing_of(tmp_path, data, "архив.tar")["listing"]] == ["../x", "/abs/y.txt", "a/../../b", "л"]
    assert os.listdir(work) == [] and sorted(os.listdir(tmp_path)) == ["cwd", "data", "out"]


@pytest.mark.parametrize("mode,type_", [("w:gz", "gz"), ("w:bz2", "bz2"), ("w:xz", "xz")])
def test_сжатый_tar_читается_так_же(tmp_path, mode, type_):
    result = listing_of(tmp_path, K.tar_bytes({"а.txt": b"hello", "б/в.txt": b"x" * 40}, mode=mode), "архив.tar." + type_)
    assert result["type"] == type_ and rows(result) == [("а.txt", 5), ("б/в.txt", 40)]


@pytest.mark.parametrize("count,shown,truncated", [(500, 500, False), (501, 500, True), (1000, 500, True)])
def test_tar_не_больше_500_строк_и_признак_обрезки(tmp_path, count, shown, truncated):
    result = listing_of(tmp_path, K.tar_bytes({f"ф{i}": b"x" for i in range(count)}), "архив.tar")
    assert len(result["listing"]) == shown and result["truncated"] is truncated


def test_tar_каталоги_не_строки_списка(tmp_path):
    import io
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        folder = tarfile.TarInfo("папка")
        folder.type = tarfile.DIRTYPE
        t.addfile(folder)
        info = tarfile.TarInfo("папка/а.txt")
        info.size = 3
        t.addfile(info, io.BytesIO(b"abc"))
    assert rows(listing_of(tmp_path, buf.getvalue(), "архив.tar")) == [("папка/а.txt", 3)]


def test_одиночный_сжатый_файл_не_архив_и_не_поддерживается_как_раньше(tmp_path):
    for name, data in (("а.gz", gzip.compress("просто сжатый текст".encode("utf-8"))),):
        assert run(tmp_path, data, name)[0] == {"kind": "none", "type": "gz", "reason": "unsupported", "args": {"type": "gz"}}


def test_tar_оборванный_none_broken_без_исключения(tmp_path):
    result, out = run(tmp_path, K.tar_bytes({"а.txt": b"x" * 2000})[:300], "оборванный.tar")
    assert result["kind"] == "none" and result["reason"] == "broken" and result["args"]["error_type"].isidentifier() and os.listdir(out) == []


# ── 7z и rar через программу 7z ─────────────────────────────────
@needs_7z
def test_7z_состав_настоящим_архивом(tmp_path):
    result = listing_of(tmp_path, K.SEVENZ_PLAIN, "архив.7z")
    assert result["type"] == "7z" and rows(result) == [("а.txt", 12), ("папка/б.bin", 100)] and result["truncated"] is False


@needs_7z
def test_rar_состав_настоящим_архивом(tmp_path):
    result = listing_of(tmp_path, K.rar5([("а.txt", "привет".encode("utf-8")), ("папка/б.bin", bytes(100))]), "архив.rar")
    assert result["type"] == "rar" and rows(result) == [("а.txt", 12), ("папка/б.bin", 100)]


@needs_7z
@pytest.mark.parametrize("blob", [K.SEVENZ_PASSWORD, K.SEVENZ_HEADERS], ids=["пароль на содержимом", "пароль и на оглавлении"])
def test_7z_с_паролем_none_encrypted_без_падения(tmp_path, blob):
    result, out = run(tmp_path, blob, "закрытый.7z")
    assert result == {"kind": "none", "type": "7z", "reason": "encrypted", "args": {}} and os.listdir(out) == []
    assert W.main(["describe", str(tmp_path / "data"), str(out), "7z"]) == 0
    assert json.loads((out / "result.json").read_text(encoding="utf-8"))["reason"] == "encrypted"


@needs_7z
@pytest.mark.parametrize("data,name,type_", [(K.SEVENZ, "поломанный.7z", "7z"), (K.RAR, "поломанный.rar", "rar"), (K.SEVENZ_PLAIN[:60], "оборванный.7z", "7z")])
def test_7z_и_rar_негодные_none_broken_без_исключения(tmp_path, data, name, type_):
    result, out = run(tmp_path, data, name)
    assert result == {"kind": "none", "type": type_, "reason": "broken", "args": {"error_type": "ListingFailed"}} and os.listdir(out) == []


def fake_7z(tmp_path, monkeypatch, body, rc=0):
    """Подставная программа 7z первой в PATH: пишет свои аргументы в args.log и печатает body."""
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    (tmp_path / "canned.txt").write_text(body, encoding="utf-8")
    script = folder / "7z"
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {tmp_path}/args.log\ncat {tmp_path}/canned.txt\nexit {rc}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{folder}:{os.environ['PATH']}")
    return tmp_path / "args.log"


def block(path, size=1, extra="Folder = -\nEncrypted = -\n"):
    return f"Path = {path}\nSize = {size}\n{extra}\n"


def test_7z_только_список_а_не_распаковка_и_без_запроса_пароля(tmp_path, monkeypatch):
    log = fake_7z(tmp_path, monkeypatch, block("а.txt", 5))
    result = listing_of(tmp_path, K.SEVENZ_PLAIN, "архив.7z")
    argv = log.read_text(encoding="utf-8").splitlines()
    assert argv[0] == "l" and "x" not in argv[:-1] and "e" not in argv and "-slt" in argv and argv[-2] == "--" and argv[-1] == str(tmp_path / "data")
    assert any(a.startswith("-p") and len(a) > 2 for a in argv)                  # пароль задан, чтобы 7z не спрашивал его с клавиатуры
    assert rows(result) == [("а.txt", 5)]


def test_7z_не_больше_500_строк_и_признак_обрезки(tmp_path, monkeypatch):
    fake_7z(tmp_path, monkeypatch, "".join(block(f"ф{i}", i) for i in range(5000)))
    result = listing_of(tmp_path, K.SEVENZ_PLAIN, "архив.7z")
    assert len(result["listing"]) == 500 and result["truncated"] is True and result["listing"][499] == {"name": "ф499", "size": 499}
    fake_7z(tmp_path, monkeypatch, "".join(block(f"ф{i}", i) for i in range(500)))
    result = listing_of(tmp_path, K.SEVENZ_PLAIN, "архив.7z")
    assert len(result["listing"]) == 500 and result["truncated"] is False


def test_7z_каталоги_пропущены_а_имена_очищены(tmp_path, monkeypatch):
    body = (block("папка", 0, "Folder = +\nEncrypted = -\n") + block("a\x1bb\u202ec.txt", 7) + block("я" * 300, 3)
            + block("пустой", "", "Folder = -\nEncrypted = -\n") + block("мусор", "много", ""))
    fake_7z(tmp_path, monkeypatch, body)
    names = rows(listing_of(tmp_path, K.SEVENZ_PLAIN, "архив.7z"))
    assert names[0] == ("a\ufffdb\ufffdc.txt", 7) and len(names[1][0]) == 255 and names[1][0].endswith("…")
    assert names[2:] == [("пустой", 0), ("мусор", 0)] and all(name != "папка" for name, _ in names)


def test_7z_имя_с_переводом_строки_не_ломает_список(tmp_path, monkeypatch):
    fake_7z(tmp_path, monkeypatch, "Path = первая\nвторая строка имени\nSize = 4\nFolder = -\nEncrypted = -\n\n" + block("следом", 9))
    names = rows(listing_of(tmp_path, K.SEVENZ_PLAIN, "архив.7z"))
    assert names == [("первая вторая строка имени", 4), ("следом", 9)]


def test_7z_зашифрованная_запись_none_encrypted(tmp_path, monkeypatch):
    fake_7z(tmp_path, monkeypatch, block("а.txt", 1) + block("б.txt", 1, "Folder = -\nEncrypted = +\n"))
    assert run(tmp_path, K.SEVENZ_PLAIN, "архив.7z")[0]["reason"] == "encrypted"


def test_7z_код_возврата_и_текст_ошибки(tmp_path, monkeypatch):
    fake_7z(tmp_path, monkeypatch, "ERROR: /in/data : Cannot open encrypted archive. Wrong password?\n", rc=2)
    assert run(tmp_path, K.SEVENZ_PLAIN, "архив.7z")[0] == {"kind": "none", "type": "7z", "reason": "encrypted", "args": {}}
    fake_7z(tmp_path, monkeypatch, "ERROR: Unsupported Method\n", rc=2)
    assert run(tmp_path, K.SEVENZ_PLAIN, "архив.7z")[0] == {"kind": "none", "type": "7z", "reason": "broken", "args": {"error_type": "ListingFailed"}}
    fake_7z(tmp_path, monkeypatch, block("а.txt", 1) + "WARNING: что-то мелкое\n", rc=1)
    assert rows(listing_of(tmp_path, K.SEVENZ_PLAIN, "архив.7z")) == [("а.txt", 1)]           # предупреждение (код 1) — не отказ
    fake_7z(tmp_path, monkeypatch, block("а.txt", 1), rc=2)
    assert run(tmp_path, K.SEVENZ_PLAIN, "архив.7z")[0]["reason"] == "broken"


def test_нет_программы_7z_нужен_распаковщик_а_не_падение(tmp_path, monkeypatch):
    empty = tmp_path / "пусто"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert run(tmp_path, K.SEVENZ_PLAIN, "архив.7z")[0] == {"kind": "none", "type": "7z", "reason": "needs_extractor", "args": {"type": "7z"}}
    assert run(tmp_path, K.RAR, "архив.rar")[0] == {"kind": "none", "type": "rar", "reason": "needs_extractor", "args": {"type": "rar"}}
    assert run(tmp_path, K.zip_bytes({"а.txt": b"x"}), "архив.zip")[0]["kind"] == "listing"           # zip и tar 7z не нужен


# ── календарь ───────────────────────────────────────────────────
CAL = [{"summary": "Встреча по договору", "start": "20261005T100000Z", "end": "20261005T110000Z", "location": "Комната 5", "attendees": 3},
       {"summary": "Обед", "start": "20261006T120000Z"}]


def calendar_text(tmp, data, name="события.ics"):
    result, out = run(tmp, data, name)
    assert result["kind"] == "text" and result["type"] == "ics", result
    assert os.listdir(out) == [] and P._check(json.loads(json.dumps(result, ensure_ascii=False)), "describe")["kind"] == "text"
    return result


def test_календарь_поля_событий_а_не_сырой_файл(tmp_path):
    result = calendar_text(tmp_path, K.ics(CAL))
    assert result["truncated"] is False
    assert result["text"] == ("Event 1\nWhen: 2026-10-05 10:00 UTC — 2026-10-05 11:00 UTC\nSubject: Встреча по договору\nLocation: Комната 5\nAttendees: 3\n\n"
                              "Event 2\nWhen: 2026-10-06 12:00 UTC\nSubject: Обед\nLocation: —\nAttendees: 0")
    for raw in ("BEGIN:", "VEVENT", "UID", "PRODID", "mailto:", "напоминание", "TRIGGER", "DTSTART", "ATTENDEE"):
        assert raw not in result["text"], raw


@pytest.mark.parametrize("params,start,end,expected", [
    (";VALUE=DATE", "20261005", "20261006", "2026-10-05 — 2026-10-06"),
    ("", "20261005T100000", "20261005T113000", "2026-10-05 10:00 — 2026-10-05 11:30"),
    (";TZID=Europe/Moscow", "20261005T100000", None, "2026-10-05 10:00 (Europe/Moscow)"),
    (";TZID=\"Custom: Zone\"", "20261005T100000", None, "2026-10-05 10:00 (Custom: Zone)"),
    ("", "не дата", None, "не дата"), ("", "", None, "—")])
def test_календарь_виды_даты(tmp_path, params, start, end, expected):
    event = {"summary": "х", "start": start, "params": params}
    if end:
        event["end"] = end
    text = calendar_text(tmp_path, K.ics([event]))["text"]
    assert f"When: {expected}\n" in text + "\n"


def test_календарь_свёрнутые_строки_и_экранирование(tmp_path):
    body = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20261005T100000Z\r\nSUMMARY:Длинная тема, которая\r\n  свернута на\r\n\t три строки\r\n"
            "LOCATION:Зал\\, этаж 5\\; корпус Б\\nвход со двора\\\\\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode("utf-8")
    text = calendar_text(tmp_path, body)["text"]
    assert "Subject: Длинная тема, которая свернута на три строки\n" in text
    assert "Location: Зал, этаж 5; корпус Б вход со двора\\\n" in text + "\n"


def test_календарь_участники_числом_а_не_вложенные_напоминания(tmp_path):
    body = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20261005T100000Z\r\nSUMMARY:х\r\nATTENDEE;CN=\"А: Б\":mailto:a@example.test\r\nATTENDEE:mailto:b@example.test\r\n"
            "BEGIN:VALARM\r\nATTENDEE:mailto:alarm@example.test\r\nSUMMARY:не тема\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode("utf-8")
    text = calendar_text(tmp_path, body)["text"]
    assert "Attendees: 2\n" in text + "\n" and "Subject: х\n" in text + "\n" and "alarm" not in text and "не тема" not in text


def test_календарь_без_событий_none_no_events(tmp_path):
    for body in (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n", "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nSUMMARY:дело\r\nEND:VTODO\r\nEND:VCALENDAR\r\n".encode("utf-8")):
        result, out = run(tmp_path, body, "события.ics")
        assert result == {"kind": "none", "type": "ics", "reason": "no_events", "args": {}} and os.listdir(out) == []


def test_календарь_определяется_по_содержимому_а_не_по_имени(tmp_path):
    assert calendar_text(tmp_path, K.ics(CAL), "заметка.txt")["text"].startswith("Event 1")
    assert calendar_text(tmp_path, b"\xef\xbb\xbf" + K.ics(CAL), "без-расширения")["text"].startswith("Event 1")
    assert calendar_text(tmp_path, b"\r\n\r\n" + K.ics(CAL), "а.ics")["text"].startswith("Event 1")


def test_календарь_много_событий_текст_до_20000_и_признак_обрезки(tmp_path):
    many = [{"summary": f"Событие номер {i}", "start": "20261005T100000Z", "location": "Комната"} for i in range(1500)]
    result = calendar_text(tmp_path, K.ics(many))
    assert len(result["text"]) == 20_000 and result["truncated"] is True and result["text"].startswith("Event 1\n")
    few = calendar_text(tmp_path, K.ics(many[:3]))
    assert few["truncated"] is False and few["text"].count("Event ") == 3


def test_календарь_слова_события_остаются_данными_и_без_управляющих_знаков(tmp_path):
    event = {"summary": "<script>alert(1)</script> и \x1b[31m красный \u202e", "start": "20261005T100000Z", "location": "{{шаблон}} ${x} $(id)"}
    text = calendar_text(tmp_path, K.ics([event]))["text"]
    assert "<script>alert(1)</script>" in text and "{{шаблон}} ${x} $(id)" in text          # как есть: это данные, их никто не исполняет
    assert "\x1b" not in text and "\u202e" not in text and "\ufffd" in text


def test_календарь_читается_с_начала_файла_а_не_целиком(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "ICS_READ", 2000)
    many = [{"summary": f"Событие номер {i}", "start": "20261005T100000Z"} for i in range(200)]
    result = calendar_text(tmp_path, K.ics(many))
    assert result["truncated"] is True and 0 < result["text"].count("Event ") < 200


def test_обычный_текст_с_именем_ics_остаётся_текстом(tmp_path):
    result, _ = run(tmp_path, "первая строка\nвторая строка\n".encode("utf-8"), "а.ics")
    assert result == {"kind": "text", "type": "txt", "text": "первая строка\nвторая строка\n", "truncated": False}


# ── память ──────────────────────────────────────────────────────
def peak(tmp_path, data, name):
    from test_preview_worker import peak_of
    return peak_of(tmp_path, data, name)


def test_архив_в_тысячу_записей_и_письма_с_заявкой_в_гигабайт_память_остаётся_малой(tmp_path):
    kb, answer = peak(tmp_path, K.zip_many(1000), "архив.zip")
    assert answer["kind"] == "listing" and len(answer["listing"]) == 500 and kb < 250_000, kb              # КБ: меньше 250 МБ
    kb, answer = peak(tmp_path, K.eml_declared(1 << 30), "письмо.eml")
    assert answer["kind"] == "mail" and answer["mail"]["attachments"][0]["size"] == 0 and kb < 250_000, kb
    kb, answer = peak(tmp_path, K.msg_bytes(attachments=[("большое.bin", (b"y" * 5000, 1 << 30))]), "письмо.msg")
    assert answer["kind"] == "none" and kb < 250_000, kb

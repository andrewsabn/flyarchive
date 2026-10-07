"""Сверка с архивом: FR-28. База известного, ключи писем, обновление."""
import os

import pytest

import gatekit as K
import known as N


letter, RECEIVED = K.letter, K.RECEIVED
NL = chr(10)


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"

    def put(rel, data):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return str(p)

    root.mkdir()
    put.root = str(root)
    put.db = str(tmp_path / "index" / "known.sqlite")
    return put


def keys(tmp_path, data, name="письмо.eml"):
    p = tmp_path / name
    p.write_bytes(data)
    return N.mail_keys(str(p), "eml")


# ── ключи письма ────────────────────────────────────────────────
@pytest.mark.parametrize("raw, mid", [
    ("<abc123@example.org>", "abc123@example.org"), ("<ABC123@Example.ORG>", "abc123@example.org"),
    ("  <abc123@example.org>  ", "abc123@example.org"), ("abc123@example.org", "abc123@example.org")])
def test_message_id_приводится_к_одному_виду(tmp_path, raw, mid):
    assert keys(tmp_path, letter(mid=raw))[0] == mid


def test_письмо_без_message_id(tmp_path):
    mid, fp = keys(tmp_path, letter(mid=None))
    assert mid is None and fp


def test_отпечаток_один_у_копий_письма_из_разных_выгрузок(tmp_path):
    base = keys(tmp_path, letter(mid=None))[1]
    assert keys(tmp_path, letter(mid=None, extra=RECEIVED))[1] == base            # другие служебные заголовки
    assert keys(tmp_path, letter(mid=None, crlf=False))[1] == base                # другие переводы строк
    assert keys(tmp_path, letter(mid=None, sender="petrov@example.org"))[1] == base  # имя отправителя записано иначе
    assert keys(tmp_path, letter(mid=None, date="Tue, 05 Mar 2024 09:32:40 +0000"))[1] == base  # тот же момент в другом поясе
    assert keys(tmp_path, letter(mid=None, html="<p>Добрый день. Направляю договор на согласование.</p>"))[1] == base
    wrapped = "Добрый день." + NL + NL + "Направляю  договор" + NL + "на согласование."
    assert keys(tmp_path, letter(mid=None, body=wrapped))[1] == base             # текст перенесён по строкам иначе


@pytest.mark.parametrize("change", [
    {"body": "Добрый день. Направляю счёт на оплату."}, {"subject": "Счёт"},
    {"sender": "sidorov@example.org"}, {"date": "Tue, 05 Mar 2024 14:40:00 +0500"}])
def test_отпечаток_разный_у_разных_писем(tmp_path, change):
    assert keys(tmp_path, letter(mid=None))[1] != keys(tmp_path, letter(mid=None, **change))[1]


def test_два_уведомления_в_одну_минуту_с_одной_темой_различаются_по_тексту(tmp_path):
    a = keys(tmp_path, letter(mid=None, subject="Ticket 1 changed", body="Status: Open -> In progress"))[1]
    b = keys(tmp_path, letter(mid=None, subject="Ticket 1 changed", body="Status: In progress -> Done"))[1]
    assert a != b


def test_битое_письмо_ключей_не_даёт(tmp_path):
    assert N.mail_keys(str(tmp_path / "нет.eml"), "eml") == (None, None)
    assert keys(tmp_path, b"\x00\x01\x02 not a letter") == (None, None)


# ── база известного ─────────────────────────────────────────────
def test_база_находит_файл_по_содержимому_и_письмо_по_message_id(corpus):
    doc = K.ooxml("docx", "Договор.")
    corpus("jira/IT/договор.docx", doc)
    corpus("export-a/Inbox/письмо.eml", letter())
    stats = N.build(corpus.root, corpus.db)
    assert (stats["files"], stats["mail"], stats["added"]) == (2, 1, 2)
    kn = N.Known(corpus.db)
    assert kn.find(sha256=N.sha256_of_bytes(doc)) == "jira/IT/договор.docx"
    assert kn.find(mid="abc123@example.org") == "export-a/Inbox/письмо.eml"
    assert kn.find(sha256="0" * 64, mid="other@example.org", fp="f" * 32) is None


def test_письмо_без_message_id_находится_по_отпечатку(corpus, tmp_path):
    corpus("export-a/Inbox/письмо.eml", letter(mid=None))
    N.build(corpus.root, corpus.db)
    mid, fp = keys(tmp_path, letter(mid=None, extra=RECEIVED, crlf=False))
    assert N.Known(corpus.db).find(mid=mid, fp=fp) == "export-a/Inbox/письмо.eml"


def test_разные_message_id_при_одинаковом_тексте_это_разные_письма(corpus, tmp_path):
    corpus("export-a/Inbox/первое.eml", letter(mid="<first@example.org>"))
    N.build(corpus.root, corpus.db)
    kn = N.Known(corpus.db)
    mid, fp = keys(tmp_path, letter(mid="<second@example.org>"))
    assert fp == keys(tmp_path, letter(mid="<first@example.org>"))[1]      # отпечатки совпадают
    assert kn.find(mid=mid, fp=fp) is None                                 # но письмо другое
    assert kn.find(mid=None, fp=fp) == "export-a/Inbox/первое.eml"            # без Message-ID сверка идёт по отпечатку


def test_карантин_и_спутники_macos_в_базу_не_попадают(corpus):
    corpus("export-a/письмо.eml", letter())
    corpus("_карантин/секрет.txt", "пароль")
    corpus("export-a/_карантин/x.txt", "x")
    corpus("export-a/._письмо.eml", K.APPLEDOUBLE)
    corpus("export-a/пустой.txt", b"")
    assert N.build(corpus.root, corpus.db)["files"] == 1


def test_повторная_сборка_читает_только_новое_и_изменённое(corpus):
    corpus("a.txt", "один")
    corpus("b.txt", "два")
    assert N.build(corpus.root, corpus.db)["added"] == 2
    assert N.build(corpus.root, corpus.db)["added"] == 0
    p = corpus("c.txt", "три")
    stats = N.build(corpus.root, corpus.db)
    assert (stats["added"], stats["files"]) == (1, 3)
    with open(p, "w", encoding="utf-8") as f:
        f.write("три, исправлено")
    os.utime(p, (2_000_000_000, 2_000_000_000))
    assert N.build(corpus.root, corpus.db)["added"] == 1
    kn = N.Known(corpus.db)
    assert kn.find(sha256=N.sha256_of_bytes("три".encode())) is None
    assert kn.find(sha256=N.sha256_of_bytes("три, исправлено".encode())) == "c.txt"


def test_удалённое_из_корпуса_уходит_из_базы(corpus):
    p = corpus("a.txt", "один")
    corpus("b.txt", "два")
    N.build(corpus.root, corpus.db)
    os.unlink(p)
    stats = N.build(corpus.root, corpus.db)
    assert (stats["removed"], stats["files"]) == (1, 1)
    assert N.Known(corpus.db).find(sha256=N.sha256_of_bytes("один".encode())) is None


def test_несколько_процессов_дают_ту_же_базу(corpus):
    for i in range(30):
        corpus(f"export-a/письмо{i}.eml", letter(mid=f"<m{i}@example.org>", body=f"Текст письма {i}"))
    assert N.build(corpus.root, corpus.db, jobs=4)["mail"] == 30
    assert N.Known(corpus.db).find(mid="m17@example.org") == "export-a/письмо17.eml"


def test_сведения_о_базе(corpus):
    assert N.Known(corpus.db).info() is None                     # базы нет
    corpus("export-a/письмо.eml", letter())
    corpus("a.txt", "один")
    N.build(corpus.root, corpus.db)
    info = N.Known(corpus.db).info()
    assert (info["files"], info["mail"]) == (2, 1) and info["built"] and info["corpus"] == corpus.root


def test_файл_базы_закрыт_от_чужих(corpus):
    corpus("a.txt", "один")
    N.build(corpus.root, corpus.db)
    assert os.stat(corpus.db).st_mode & 0o077 == 0


def test_после_смены_правил_опознания_база_перечитывается_целиком(corpus, monkeypatch):
    corpus("a.txt", "один")
    corpus("export-a/письмо.eml", letter())
    assert N.build(corpus.root, corpus.db)["added"] == 2
    assert N.build(corpus.root, corpus.db)["added"] == 0
    monkeypatch.setattr(N, "VERSION", N.VERSION + 1)         # изменились правила: что считать письмом, как считать ключи
    assert N.build(corpus.root, corpus.db)["added"] == 2
    assert N.build(corpus.root, corpus.db)["added"] == 0


# ── дата письма ─────────────────────────────────────────────────
def info(tmp_path, data, name="письмо.eml"):
    p = tmp_path / name
    p.write_bytes(data)
    return N.letter_info(str(p), "eml")


@pytest.mark.parametrize("header, date", [
    ("Tue, 05 Mar 2024 14:32:00 +0500", "2024-03-05"),
    ("Tue, 05 Mar 2024 23:30:00 +0500", "2024-03-05"),          # день по часам отправителя, без пересчёта пояса
    ("Wed, 06 Mar 2024 00:10:00 +0000", "2024-03-06"),
    ("5 Mar 2024 14:32:00 +0500", "2024-03-05")])
def test_дата_письма_из_заголовка_date(tmp_path, header, date):
    mid, fp, got = info(tmp_path, letter(date=header))
    assert got == date and mid == "abc123@example.org" and fp


def test_без_заголовка_date_берётся_время_приёма_сервером(tmp_path):
    raw = (b"Received: from mx2 by mx3; Wed, 6 Mar 2024 09:00:00 +0500\r\n"
           b"Received: from mx1 by mx2; Tue, 5 Mar 2024 23:59:00 +0500\r\n"
           b"From: petrov@example.org\r\nSubject: Plan\r\nMessage-ID: <x@example.org>\r\n\r\nBody\r\n")
    assert info(tmp_path, raw)[2] == "2024-03-05"               # самый ранний приём — ближе всего к отправке


@pytest.mark.parametrize("header", ["не дата", "Mon, 01 Jan 1601 00:00:00 +0000", "Fri, 01 Jan 2100 00:00:00 +0000", ""])
def test_негодная_дата_не_принимается(tmp_path, header):
    raw = b"From: petrov@example.org\r\nSubject: Plan\r\nDate: " + header.encode("utf-8") + b"\r\nMessage-ID: <x@example.org>\r\n\r\nBody\r\n"
    assert info(tmp_path, raw)[2] is None


def test_дата_письма_хранится_в_базе(corpus):
    corpus("export-a/Inbox/письмо.eml", letter(date="Tue, 05 Mar 2024 14:32:00 +0500"))
    corpus("jira/IT/договор.docx", K.ooxml("docx", "Договор."))
    N.build(corpus.root, corpus.db)
    kn = N.Known(corpus.db)
    assert kn.date_of("export-a/Inbox/письмо.eml") == "2024-03-05"
    assert kn.date_of("jira/IT/договор.docx") is None and kn.date_of("нет/такого.eml") is None


def test_база_прежнего_устройства_пересобирается_сама(corpus):
    """База, собранная до появления дат, не имеет нужного столбца: сборка должна её заменить, а не упасть."""
    import sqlite3
    corpus("export-a/Inbox/письмо.eml", letter())
    os.makedirs(os.path.dirname(corpus.db))
    con = sqlite3.connect(corpus.db)
    con.executescript("CREATE TABLE files(path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, sha TEXT, mid TEXT, fp TEXT);"
                      "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT); INSERT INTO meta VALUES ('version', '2');"
                      "INSERT INTO files VALUES ('старое.eml', 1, 1, 'x', 'old@example.org', 'f');")
    con.commit()
    con.close()
    assert N.build(corpus.root, corpus.db)["added"] == 1
    kn = N.Known(corpus.db)
    assert kn.date_of("export-a/Inbox/письмо.eml") == "2024-03-05" and kn.find(mid="old@example.org") is None


# ── FR-73а: отказ без базы несёт код и путь, русский текст прежний ──
@pytest.mark.parametrize("call", [lambda db: N.add(db, []), lambda db: N.remove(db, [])])
def test_нет_базы_отказ_с_кодом_и_путём(corpus, call):
    with pytest.raises(N.KnownError) as e:
        call(corpus.db)
    assert (e.value.message.code, e.value.message.args) == ("known.db_missing", {"path": corpus.db})
    assert str(e.value) == f"базы известного нет: {corpus.db}. Собери её: flyarchive known build"

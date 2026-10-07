"""История пачек разбора (FR-70): список пачек и подробности одной пачки по квитанциям.

Источник — `квитанции/<пачка>.jsonl` (записи приёмки и строки решений владельца с полем `by`) и `квитанции/<пачка>.meta.json`
(длительность и замечания прохода). Часть тестов пишет квитанции руками — так видны границы и порядок; часть идёт по настоящему
проходу и настоящим решениям владельца — так видно, что форма записей сошлась с тем, что пишут inbox и review.
"""
import json
import os
from collections import Counter
from datetime import date, timedelta

import pytest

import batches as BT
import gatekit as K
import inbox as B
import known as N
import review as R
from test_review import CLEAN, EVIL, EXE, LETTER, Table, embed

T0 = 1_790_000_000.0
# образец — текст: он индексируется без библиотек по форматам (docx без python-docx не индексируется, а этим тестам формат не важен)
OTHER = "Приложение к договору.".encode("utf-8")
BLOB = bytes(range(256)) * 20
MESSAGE = {"code": None, "args": {}}


def rec(name, decision="accept", time="2026-10-04T12:00:00Z", **kw):
    """Запись приёмки в том виде, как её пишет inbox._settle."""
    r = {"name": name, "sha256": "0" * 64, "size": 10, "type": "txt", "decision": decision, "score": 0, "findings": [], "reason": "",
         "checked_by": None, "path": None, "batch": None, "time": time, "date": None, "date_source": None, "verified": None,
         "indexed": False, "archive": None, "archive_sha256": None, "inner": None, "duplicate_of": None, "returned": None}
    r.update(kw)
    return r


def owner(name, decision, time="2026-10-05T08:00:00Z", **kw):
    """Решение владельца, как его дописывает review._note."""
    return {"name": name, "decision": decision, "was": "review", "path": None, "by": "владелец", "time": time, **kw}


def say(text):
    return {**MESSAGE, "text": text}


def write(home, batch, *lines, meta=None, raw_meta=None):
    """Квитанции пачки руками: строка — словарь (в JSON) или байты как есть. meta — словарь, raw_meta — текст как есть."""
    folder = os.path.join(home, "квитанции")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, batch + ".jsonl"), "wb") as f:
        for line in lines:
            f.write(line if isinstance(line, bytes) else json.dumps(line, ensure_ascii=False).encode("utf-8"))
            f.write(b"\n")
    if meta is not None:
        raw_meta = json.dumps(meta, ensure_ascii=False)
    if raw_meta is not None:
        with open(os.path.join(folder, batch + ".meta.json"), "w", encoding="utf-8") as f:
            f.write(raw_meta)


def days(n, start=date(2026, 8, 1)):
    """n номеров пачек по одной на день, от старой к новой."""
    return [(start + timedelta(days=i)).strftime("%Y%m%d") + "-100000" for i in range(n)]


@pytest.fixture
def home(tmp_path):
    return str(tmp_path / "flyarchive")


class Spy:
    """Подставка вместо os в модуле: помнит, что модуль пытался читать."""
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        real = getattr(os, name)
        if name in ("listdir", "scandir", "walk", "stat", "lstat") and callable(real):
            def watched(*a, **kw):
                self.calls.append((name, a[:1]))
                return real(*a, **kw)
            return watched
        return real


@pytest.fixture
def spy(monkeypatch):
    """Всё, что batches читает с диска: listdir, scandir, walk, stat, open."""
    s = Spy()
    monkeypatch.setattr(BT, "os", s)

    def watched_open(*a, **kw):
        s.calls.append(("open", a[:1]))
        return open(*a, **kw)

    monkeypatch.setattr(BT, "open", watched_open, raising=False)
    return s


# ── список: порядок, поля, счёт ─────────────────────────────────
def test_три_пачки_список_от_новой_к_старой_с_полями(home):
    a, b, c = "20261001-090000", "20261003-101500", "20261004-120000"
    write(home, b, rec("а", "review", "2026-10-03T10:15:00Z"), rec("б", "review", "2026-10-03T10:15:00Z"),
          rec("в", "quarantine", "2026-10-03T10:15:00Z"),
          meta={"started": "2026-10-03T10:15:00Z", "finished": "2026-10-03T10:15:40Z", "seconds": 40,
                "problems": [say("первое"), say("второе")]})
    write(home, c, rec("г", "accept"), rec("д", "skip"), rec("е", "skip"))
    write(home, a, rec("ж", "accept", "2026-10-01T09:00:00Z"), rec("з", "accept", "2026-10-01T09:00:00Z"),
          rec("и", "duplicate", "2026-10-01T09:00:00Z"),
          meta={"started": "2026-10-01T09:00:00Z", "finished": "2026-10-01T09:00:05Z", "seconds": 5, "problems": []})
    out = BT.list_batches(home)
    assert set(out) == {"batches", "more"} and out["more"] is False
    assert out["batches"] == [
        {"id": c, "time": "2026-10-04T12:00:00Z", "seconds": None, "counts": {"accept": 1, "skip": 2}, "problems": 0, "files": 3},
        {"id": b, "time": "2026-10-03T10:15:00Z", "seconds": 40, "counts": {"review": 2, "quarantine": 1}, "problems": 2, "files": 3},
        {"id": a, "time": "2026-10-01T09:00:00Z", "seconds": 5, "counts": {"accept": 2, "duplicate": 1}, "problems": 0, "files": 3}]


def test_решения_владельца_в_счёт_не_идут(home):
    """Строки с полем by дописаны в тот же файл: они не файлы пачки, не решения приёмки и не задают время пачки."""
    write(home, "20261004-120000", rec("а", "review"), rec("б", "review"), rec("в", "quarantine"),
          owner("а", "accept", "2026-10-06T09:00:00Z"), owner("б", "quarantine", "2026-10-06T09:00:01Z"),
          owner("в", "deleted", "2026-10-06T09:00:02Z"), owner("б", "deleted", "2026-10-06T09:00:03Z"))
    (one,) = BT.list_batches(home)["batches"]
    assert one["counts"] == {"review": 2, "quarantine": 1} and one["files"] == 3 and one["time"] == "2026-10-04T12:00:00Z"
    detail = BT.batch_detail(home, "20261004-120000")
    assert detail["counts"] == {"review": 2, "quarantine": 1} and detail["time"] == "2026-10-04T12:00:00Z"
    assert [f["name"] for f in detail["files"]] == ["а", "б", "в"]


def test_пустой_файл_квитанций_это_пачка_без_записей(home):
    write(home, "20261004-120000", meta={"seconds": 3, "problems": []})
    assert BT.list_batches(home)["batches"] == [
        {"id": "20261004-120000", "time": None, "seconds": 3, "counts": {}, "problems": 0, "files": 0}]


def test_пачки_одной_секунды_идут_по_номеру_а_не_по_строке(home):
    """-10 новее, чем -9: по строке было бы наоборот. Без номера — самая ранняя из своей секунды."""
    ids = ["20261004-120000", "20261004-120000-2", "20261004-120000-9", "20261004-120000-10", "20261004-120000-11", "20261003-235959"]
    for i in (3, 0, 5, 1, 4, 2):                      # записаны вразнобой: порядок создания не должен влиять
        write(home, ids[i], rec("а"))
    assert [b["id"] for b in BT.list_batches(home)["batches"]] == ["20261004-120000-11", "20261004-120000-10", "20261004-120000-9",
                                                                  "20261004-120000-2", "20261004-120000", "20261003-235959"]


def test_нет_папки_квитанций_пусто(home):
    assert BT.list_batches(home) == {"batches": [], "more": False}
    os.makedirs(os.path.join(home, "квитанции"))
    assert BT.list_batches(home) == {"batches": [], "more": False}


def test_лишние_файлы_в_квитанциях_пачками_не_считаются(home):
    write(home, "20261004-120000", rec("а"))
    folder = os.path.join(home, "квитанции")
    for stray in ("notes.jsonl", "20261003-100000.meta.json", "20261002-100000.jsonl.tmp", "20261001-100000.json", "20260930-100000-1.jsonl",
                  "20260929-100000-.jsonl", "x20260928-100000.jsonl", ".jsonl"):
        with open(os.path.join(folder, stray), "w", encoding="utf-8") as f:
            f.write(json.dumps(rec("чужая")) + "\n")
    assert [b["id"] for b in BT.list_batches(home)["batches"]] == ["20261004-120000"]


# ── список: --limit и --before ──────────────────────────────────
@pytest.fixture
def many(home):
    """Тридцать пять пачек по одной на день; ids — от новой к старой."""
    ids = days(35)
    for i in ids:
        write(home, i, rec("а", time=f"{i[:4]}-{i[4:6]}-{i[6:8]}T10:00:00Z"))
    return list(reversed(ids))


def test_тридцать_пять_пачек_лимит_тридцать_и_остаток_по_before(home, many):
    first = BT.list_batches(home, limit=30)
    assert [b["id"] for b in first["batches"]] == many[:30] and first["more"] is True
    rest = BT.list_batches(home, limit=30, before=first["batches"][-1]["id"])
    assert [b["id"] for b in rest["batches"]] == many[30:] and len(rest["batches"]) == 5 and rest["more"] is False


def test_лимит_по_умолчанию_тридцать(home, many):
    out = BT.list_batches(home)
    assert [b["id"] for b in out["batches"]] == many[:30] and out["more"] is True


@pytest.mark.parametrize("limit,shown,more", [(1, 1, True), (2, 2, True), (34, 34, True), (35, 35, False), (36, 35, False), (200, 35, False)])
def test_лимит_границы_и_признак_что_есть_ещё(home, many, limit, shown, more):
    for given in (limit, str(limit)):                 # из командной строки приходит строка
        out = BT.list_batches(home, limit=given)
        assert [b["id"] for b in out["batches"]] == many[:shown] and out["more"] is more


def test_ровно_лимит_пачек_ещё_нет(home):
    ids = days(3)
    for i in ids:
        write(home, i, rec("а"))
    assert BT.list_batches(home, limit=3)["more"] is False and BT.list_batches(home, limit=2)["more"] is True


@pytest.mark.parametrize("limit", [0, 201, -1, 10 ** 6, "0", "201", "-1", "abc", "", "1.5", " 5", "5 ", "٣", 2.5, True])
def test_негодный_лимит_отказ_и_ничего_не_прочитано(home, many, spy, limit):
    with pytest.raises(BT.BatchError):
        BT.list_batches(home, limit=limit)
    assert spy.calls == []


def test_before_не_включает_названную_пачку(home, many):
    out = BT.list_batches(home, before=many[0])
    assert [b["id"] for b in out["batches"]] == many[1:31] and out["more"] is True
    out = BT.list_batches(home, limit=4, before=many[10])
    assert [b["id"] for b in out["batches"]] == many[11:15] and out["more"] is True
    out = BT.list_batches(home, limit=4, before=many[31])
    assert [b["id"] for b in out["batches"]] == many[32:] and out["more"] is False
    out = BT.list_batches(home, limit=3, before=many[31])
    assert [b["id"] for b in out["batches"]] == many[32:35] and out["more"] is False


def test_before_старейшей_пачки_пусто(home, many):
    assert BT.list_batches(home, before=many[-1]) == {"batches": [], "more": False}


def test_before_может_называть_несуществующую_пачку(home, many):
    """Берутся пачки старше названного номера, существует ли он — не важно."""
    between = many[5][:9] + "120000"                   # тот же день, но позже 10:00:00: старше его только следующий день вниз
    assert between > many[5] and between < many[4]
    out = BT.list_batches(home, limit=2, before=between)
    assert [b["id"] for b in out["batches"]] == many[5:7]
    assert BT.list_batches(home, before="20300101-000000")["batches"][0]["id"] == many[0]
    assert BT.list_batches(home, before="20000101-000000") == {"batches": [], "more": False}


def test_before_с_номером_внутри_секунды(home):
    ids = ["20261004-120000", "20261004-120000-2", "20261004-120000-3"]
    for i in ids:
        write(home, i, rec("а"))
    assert [b["id"] for b in BT.list_batches(home, before="20261004-120000-3")["batches"]] == ["20261004-120000-2", "20261004-120000"]
    assert [b["id"] for b in BT.list_batches(home, before="20261004-120000-2")["batches"]] == ["20261004-120000"]
    assert BT.list_batches(home, before="20261004-120000")["batches"] == []


BAD_IDS = ["", "abc", "../x", "..", "/etc/passwd", "20261004-120000/../x", "20261004-120000.jsonl", "20261004-12000", "2026100-120000",
           "20261004-1200000", "20261004_120000", "20261004120000", "20261004-120000-", "20261004-120000-a", "20261004-120000-2-3",
           "20261004-120000\n", " 20261004-120000", "20261004-120000 ", "٢٠٢٦١٠٠٤-١٢٠٠٠٠", "20261004-120000-1", "20261004-120000-0",
           "20261004-120000-02", "20261004-120000.meta.json", "квитанции/20261004-120000", "20261004-120000\x00", None, 20261004]


@pytest.mark.parametrize("bad", [b for b in BAD_IDS if b is not None])         # None — «before не задан»
def test_негодная_before_отказ_и_ничего_не_прочитано(home, many, spy, bad):
    with pytest.raises(BT.BatchError):
        BT.list_batches(home, before=bad)
    assert spy.calls == []


# ── длительность и замечания: файл .meta.json ───────────────────
def test_пачка_без_meta_старая_секунды_null_замечаний_нет(home):
    """Квитанции, записанные до появления .meta.json: всё остальное как у прочих."""
    write(home, "20261004-120000", rec("а", "accept"), rec("б", "review"))
    write(home, "20261003-120000", rec("а", "accept"), rec("б", "review"), meta={"seconds": 9, "problems": [say("х")]})
    new, old = BT.list_batches(home)["batches"]
    assert (new["seconds"], new["problems"], new["counts"], new["files"]) == (None, 0, {"accept": 1, "review": 1}, 2)
    assert (old["seconds"], old["problems"], old["counts"], old["files"]) == (9, 1, {"accept": 1, "review": 1}, 2)
    detail = BT.batch_detail(home, "20261004-120000")
    assert detail["seconds"] is None and detail["problems"] == []


@pytest.mark.parametrize("raw", ["", "не json", "[]", "42", "null", '{"seconds": "12", "problems": "х"}', '{"seconds": true, "problems": {}}',
                                 '{"seconds": -3, "problems": [1, "а", null]}', '{"seconds": NaN, "problems": 5}', '{"problems": [[]]}', "\xff"])
def test_негодный_meta_как_будто_его_нет(home, raw):
    write(home, "20261004-120000", rec("а"), raw_meta=raw)
    (one,) = BT.list_batches(home)["batches"]
    assert (one["seconds"], one["problems"], one["files"]) == (None, 0, 1)
    detail = BT.batch_detail(home, "20261004-120000")
    assert (detail["seconds"], detail["problems"]) == (None, [])


def test_meta_с_нулевой_длительностью_это_ноль_а_не_нет(home):
    write(home, "20261004-120000", rec("а"), meta={"seconds": 0, "problems": []})
    assert BT.list_batches(home)["batches"][0]["seconds"] == 0


def test_в_списке_замечания_числом_в_подробностях_сообщениями(home):
    notes = [say("первое замечание"), {"code": "x", "args": {"n": 1}, "text": "второе"}, say("третье")]
    write(home, "20261004-120000", rec("а"), meta={"started": "2026-10-04T12:00:00Z", "finished": "2026-10-04T12:00:07Z", "seconds": 7,
                                                      "problems": notes})
    assert BT.list_batches(home)["batches"][0]["problems"] == 3
    detail = BT.batch_detail(home, "20261004-120000")
    assert detail["problems"] == notes and detail["seconds"] == 7


# ── строки, которые не разбираются ──────────────────────────────
JUNK = [b"\xff\xfe\x00 not utf8", b"{not json", '{"name": "обрыв", "decision": "acc'.encode("utf-8"), b"42", b"[1, 2]", b"null",
        '"строка"'.encode("utf-8"), b"true", b"   ", b""]


def test_строка_которая_не_разбирается_пропускается(home):
    lines = [rec("а", "accept"), *JUNK[:5], rec("б", "review"), *JUNK[5:], owner("б", "accept"), b"\xc3\x28", rec("в", "accept")]
    write(home, "20261004-120000", *lines)
    (one,) = BT.list_batches(home)["batches"]
    assert one["counts"] == {"accept": 2, "review": 1} and one["files"] == 3 and one["time"] == "2026-10-04T12:00:00Z"
    detail = BT.batch_detail(home, "20261004-120000")
    assert [f["name"] for f in detail["files"]] == ["а", "б", "в"] and detail["counts"] == {"accept": 2, "review": 1}
    assert [f["decided"] for f in detail["files"]] == [None, "accept", None]


def test_запись_без_решения_или_с_чужим_видом_не_ломает_счёт(home):
    write(home, "20261004-120000", rec("а", "accept"), {"name": "б"}, {"name": "в", "decision": None}, {"name": "г", "decision": ["x"]})
    (one,) = BT.list_batches(home)["batches"]
    assert one["counts"] == {"accept": 1} and one["files"] == 4
    assert json.dumps(one)                              # ответ записывается как JSON


# ── подробности одной пачки ─────────────────────────────────────
def test_подробности_файлы_в_порядке_имён_со_всеми_полями(home):
    names = ["б.txt", "в/г.txt", "а.txt", "B.txt", "10.txt", "9.txt"]
    full = {n: rec(n, "duplicate", sha256=f"{i:064d}", size=100 + i, findings=[{"rule": "r", "level": "low"}], score=i, note="примечание")
            for i, n in enumerate(names)}
    write(home, "20261004-120000", *[full[n] for n in names], meta={"seconds": 11, "problems": [say("х")]})
    d = BT.batch_detail(home, "20261004-120000")
    assert set(d) == {"id", "time", "seconds", "counts", "problems", "files"}
    assert d["id"] == "20261004-120000" and d["time"] == "2026-10-04T12:00:00Z" and d["seconds"] == 11
    assert d["counts"] == {"duplicate": 6} and d["problems"] == [say("х")]
    assert [f["name"] for f in d["files"]] == sorted(names)
    for f in d["files"]:
        assert f == {**full[f["name"]], "decided": None, "decided_at": None, "location": None}


def test_подробности_каждая_запись_приёмки_отдельной_строкой(home):
    write(home, "20261004-120000", rec("а", "review"), rec("а", "failed"), rec("б"))
    assert [(f["name"], f["decision"]) for f in BT.batch_detail(home, "20261004-120000")["files"]] == [("а", "review"), ("а", "failed"), ("б", "accept")]


def test_решение_последней_записи_владельца_действует_по_порядку_строк_а_не_времени(home):
    write(home, "20261004-120000", rec("а", "quarantine", path="карантин/20261004-120000/а"), rec("б", "review"), rec("в", "review"),
          owner("а", "returned", "2026-10-05T09:00:00Z"), owner("б", "accept", "2026-10-05T09:00:01Z"),
          owner("а", "deleted", "2026-10-05T08:00:00Z"),            # раньше по часам, но позже в файле: она и действует
          owner("б", "что-то-новое", "2026-10-05T10:00:00Z"))         # непонятное решение ничего не меняет
    by = {f["name"]: f for f in BT.batch_detail(home, "20261004-120000")["files"]}
    assert (by["а"]["decided"], by["а"]["decided_at"]) == ("delete", "2026-10-05T08:00:00Z")
    assert (by["б"]["decided"], by["б"]["decided_at"]) == ("accept", "2026-10-05T09:00:01Z")
    assert (by["в"]["decided"], by["в"]["decided_at"]) == (None, None)


@pytest.mark.parametrize("written,decided", [("accept", "accept"), ("quarantine", "quarantine"), ("returned", "return"), ("return", "return"),
                                             ("deleted", "delete"), ("delete", "delete")])
def test_значения_решений_владельца(home, written, decided):
    write(home, "20261004-120000", rec("а", "review"), owner("а", written, "2026-10-05T09:30:00Z"))
    (f,) = BT.batch_detail(home, "20261004-120000")["files"]
    assert (f["decided"], f["decided_at"]) == (decided, "2026-10-05T09:30:00Z")


def test_решение_про_другой_файл_и_строка_без_имени_не_в_счёт(home):
    write(home, "20261004-120000", rec("а", "review"), rec("б", "review"), owner("б", "accept"),
          {"decision": "accept", "by": "владелец", "time": "2026-10-05T09:00:00Z"},
          {"name": ["а"], "decision": "accept", "by": "владелец", "time": "2026-10-05T09:00:00Z"},
          {"name": "а", "decision": ["accept"], "by": "владелец", "time": "2026-10-05T09:00:00Z"})
    by = {f["name"]: f["decided"] for f in BT.batch_detail(home, "20261004-120000")["files"]}
    assert by == {"а": None, "б": "accept"}


# ── номер пачки: вид проверяется до имени файла ─────────────────
@pytest.mark.parametrize("bad", BAD_IDS)
def test_негодный_номер_пачки_отказ_и_ничего_не_прочитано(home, spy, bad):
    write(home, "20261004-120000", rec("а"))
    spy.calls.clear()
    with pytest.raises(BT.BatchError):
        BT.batch_detail(home, bad)
    assert spy.calls == []


def test_номер_верного_вида_но_такой_пачки_нет_отказ(home):
    write(home, "20261004-120000", rec("а"))
    for missing in ("20261004-120001", "20261004-120000-2", "19990101-000000"):
        with pytest.raises(BT.BatchError):
            BT.batch_detail(home, missing)
    with pytest.raises(BT.BatchError):
        BT.batch_detail(str(home) + "-нет", "20261004-120000")


def test_имя_файла_из_номера_не_складывается_пока_вид_не_проверен(home):
    """Файл вне папки квитанций с содержимым пачки: если бы вид не проверялся, `../x` его бы открыл."""
    write(home, "20261004-120000", rec("а"))
    with open(os.path.join(home, "x.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(rec("снаружи")) + "\n")
    with open(os.path.join(home, "квитанции", "20261004-120000.meta.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    for bad in ("../x", "../x.jsonl", "../квитанции/20261004-120000", "20261004-120000/../../x"):
        with pytest.raises(BT.BatchError):
            BT.batch_detail(home, bad)
    with pytest.raises(BT.BatchError):
        BT.batch_detail(home, "20261004-120000.meta")      # имя без расширения в квитанциях не пачка


def test_мета_файл_отдельно_без_квитанций_пачкой_не_является(home):
    os.makedirs(os.path.join(home, "квитанции"))
    with open(os.path.join(home, "квитанции", "20261004-120000.meta.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"seconds": 3, "problems": []}))
    assert BT.list_batches(home)["batches"] == []
    with pytest.raises(BT.BatchError):
        BT.batch_detail(home, "20261004-120000")


# ── расположение файла: настоящий проход и настоящие решения владельца ──
@pytest.fixture
def arch(tmp_path):
    """Архив после одной пачки: два документа приняты, три ждут решения, две программы в карантине, остальное не взято."""
    class Env:
        home = str(tmp_path / "flyarchive")
        inbox = str(tmp_path / "входящие")
        back = str(tmp_path / "входящие-возврат")
        corpus = os.path.join(home, "corpus")
        db = os.path.join(home, "index", "known.sqlite")
        table = Table()

        def where(self):
            return {f["name"]: f["location"] for f in BT.batch_detail(self.home, self.batch)["files"]}

        def file(self, name):
            (f,) = [f for f in BT.batch_detail(self.home, self.batch)["files"] if f["name"] == name]
            return f

        def raw(self):
            with open(os.path.join(self.home, "квитанции", self.batch + ".jsonl"), encoding="utf-8") as f:
                return [json.loads(line) for line in f]

        def append(self, record):
            with open(os.path.join(self.home, "квитанции", self.batch + ".jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    e = Env()
    os.makedirs(os.path.join(e.corpus, "mail"))
    os.makedirs(e.inbox)
    with open(os.path.join(e.corpus, "mail", "старое.txt"), "wb") as f:
        f.write(OTHER)
    N.build(e.corpus, e.db)
    files = {"договор.txt": CLEAN, "отчёт.txt": EVIL.encode("utf-8"), "отчёт-два.txt": (EVIL + " Второй.").encode("utf-8"),
             "2024-03-05_счёт.eml": LETTER, "setup.exe": EXE, "утилита.exe": EXE + b"\x01", "копия.txt": OTHER, "данные.bin": BLOB,
             "пачка.zip": K.zip_bytes({"записка.txt": "Согласовать перенос работ."}), "пусто.txt": b""}
    for name, data in files.items():
        with open(os.path.join(e.inbox, name), "wb") as f:
            f.write(data)
    s = B.process(e.home, e.inbox, now=T0, table=e.table, embed=embed, stable_seconds=0)
    assert s.counts == {"accept": 2, "review": 3, "quarantine": 2, "duplicate": 1, "skip": 2, "unpacked": 1}, s.counts
    e.batch = s.batch
    e.q = lambda name: f"очередь/{s.batch}/{name}"
    e.k = lambda name: f"карантин/{s.batch}/{name}"
    e.c = lambda name: f"входящие/{s.batch}/{name}"
    return e


AFTER_INTAKE = {"2024-03-05_счёт.eml": "queue", "setup.exe": "quarantine", "договор.txt": "corpus", "данные.bin": "returned",
                "копия.txt": None, "отчёт-два.txt": "queue", "отчёт.txt": "queue", "пачка.zip": None, "пачка.zip/записка.txt": "corpus",
                "пусто.txt": None, "утилита.exe": "quarantine"}                # пустой файл — не содержимое: убран, хранить и возвращать нечего


def test_после_приёмки_расположение_каждого_файла(arch):
    assert arch.where() == AFTER_INTAKE
    for f in BT.batch_detail(arch.home, arch.batch)["files"]:
        assert (f["decided"], f["decided_at"]) == (None, None)


def test_подробности_настоящей_пачки_все_поля_квитанции_плюс_три(arch):
    d = BT.batch_detail(arch.home, arch.batch)
    raw = {r["name"]: r for r in arch.raw()}
    assert [f["name"] for f in d["files"]] == sorted(raw) and len(d["files"]) == 11
    for f in d["files"]:
        assert f == {**raw[f["name"]], "decided": None, "decided_at": None, "location": AFTER_INTAKE[f["name"]]}
    assert d["id"] == arch.batch and d["time"] == raw["договор.txt"]["time"] and d["counts"] == dict(Counter(r["decision"] for r in raw.values()))
    assert d["seconds"] is not None and d["problems"] == []


def test_список_настоящей_пачки(arch):
    (one,) = BT.list_batches(arch.home)["batches"]
    assert one["id"] == arch.batch and one["files"] == 11 and one["problems"] == 0 and one["time"] == arch.raw()[0]["time"]
    assert one["counts"] == {"accept": 2, "review": 3, "quarantine": 2, "duplicate": 1, "skip": 2, "unpacked": 1}
    assert isinstance(one["seconds"], (int, float)) and not isinstance(one["seconds"], bool) and one["seconds"] >= 0


def test_принято_владельцем_лежит_в_архиве(arch):
    R.queue_accept(arch.home, arch.q("отчёт.txt"), table=arch.table, embed=embed)
    f = arch.file("отчёт.txt")
    assert (f["decided"], f["location"], f["decided_at"]) == ("accept", "corpus", arch.raw()[-1]["time"])
    assert arch.where() == {**AFTER_INTAKE, "отчёт.txt": "corpus"}


def test_отправлено_в_карантин_владельцем(arch):
    R.queue_reject(arch.home, arch.q("отчёт-два.txt"))
    f = arch.file("отчёт-два.txt")
    assert (f["decided"], f["location"], f["decided_at"]) == ("quarantine", "quarantine", arch.raw()[-1]["time"])
    assert arch.where() == {**AFTER_INTAKE, "отчёт-два.txt": "quarantine"}


def test_возвращено_из_карантина(arch):
    R.quarantine_return(arch.home, arch.k("setup.exe"), inbox=arch.inbox)
    f = arch.file("setup.exe")
    assert (f["decided"], f["location"]) == ("return", "returned")
    assert arch.where() == {**AFTER_INTAKE, "setup.exe": "returned"}


def test_стёрто_из_карантина(arch):
    R.quarantine_delete(arch.home, arch.k("утилита.exe"))
    f = arch.file("утилита.exe")
    assert (f["decided"], f["location"]) == ("delete", "deleted")
    assert arch.where() == {**AFTER_INTAKE, "утилита.exe": "deleted"}


def test_удалено_из_архива_командой_doc_delete(arch):
    """doc delete не пишет решение в квитанции; но файл ушёл в удалённое/ — это и есть «удалён из архива»."""
    R.doc_delete(arch.home, arch.c("договор.txt"), table=arch.table, embed=None, now=T0)
    assert not os.path.exists(os.path.join(arch.corpus, "входящие", arch.batch, "договор.txt"))
    assert arch.file("договор.txt")["location"] == "deleted"
    assert arch.where() == {**AFTER_INTAKE, "договор.txt": "deleted"}


def test_принятое_а_потом_удалённое_из_архива_удалено(arch):
    R.queue_accept(arch.home, arch.q("отчёт.txt"), table=arch.table, embed=embed)
    R.doc_delete(arch.home, arch.c("отчёт.txt"), table=arch.table, embed=None, now=T0)
    f = arch.file("отчёт.txt")
    assert (f["decided"], f["location"]) == ("delete", "deleted")          # удаление документа — решение владельца в квитанциях пачки


def test_удалённое_из_архива_в_другой_день_тоже_удалено(arch):
    R.doc_delete(arch.home, arch.c("пачка.zip/записка.txt"), table=arch.table, embed=None, now=T0 + 3 * 86400)
    assert arch.file("пачка.zip/записка.txt")["location"] == "deleted"
    assert arch.where() == {**AFTER_INTAKE, "пачка.zip/записка.txt": "deleted"}


def test_последнее_решение_действует_карантин_потом_возврат(arch):
    R.queue_reject(arch.home, arch.q("отчёт.txt"))
    R.quarantine_return(arch.home, arch.k("отчёт.txt"), inbox=arch.inbox)
    f = arch.file("отчёт.txt")
    assert (f["decided"], f["location"]) == ("return", "returned") and f["decided_at"] == arch.raw()[-1]["time"]


def test_последнее_решение_действует_карантин_потом_стирание(arch):
    R.queue_reject(arch.home, arch.q("отчёт-два.txt"))
    R.quarantine_delete(arch.home, arch.k("отчёт-два.txt"))
    f = arch.file("отчёт-два.txt")
    assert (f["decided"], f["location"]) == ("delete", "deleted")


def test_файла_на_месте_нет_пропал(arch):
    """По квитанции должен лежать в корпусе, очереди, карантине — а там пусто."""
    os.remove(os.path.join(arch.home, "очередь", arch.batch, "отчёт.txt"))
    os.remove(os.path.join(arch.home, "карантин", arch.batch, "setup.exe"))
    os.remove(os.path.join(arch.corpus, "входящие", arch.batch, "договор.txt"))
    assert arch.where() == {**AFTER_INTAKE, "отчёт.txt": "missing", "setup.exe": "missing", "договор.txt": "missing"}
    assert arch.file("отчёт.txt")["decided"] is None


def test_решено_принять_но_файла_в_корпусе_нет(arch):
    R.queue_accept(arch.home, arch.q("отчёт.txt"), table=arch.table, embed=embed)
    os.remove(os.path.join(arch.corpus, "входящие", arch.batch, "отчёт.txt"))
    f = arch.file("отчёт.txt")
    assert (f["decided"], f["location"]) == ("accept", "missing")


def test_решено_в_карантин_но_файла_там_нет(arch):
    R.queue_reject(arch.home, arch.q("отчёт.txt"))
    os.remove(os.path.join(arch.home, "карантин", arch.batch, "отчёт.txt"))
    f = arch.file("отчёт.txt")
    assert (f["decided"], f["location"]) == ("quarantine", "missing")


def test_файл_в_другом_месте_не_считается_лежащим_здесь(arch):
    """Очередь уехала в корпус руками, решения владельца нет: по квитанции файл в очереди, а там его нет."""
    os.rename(os.path.join(arch.home, "очередь", arch.batch, "отчёт.txt"), os.path.join(arch.corpus, "входящие", arch.batch, "отчёт.txt"))
    assert arch.file("отчёт.txt")["location"] == "missing"


def test_ссылка_и_каталог_на_месте_файла_не_файл(arch):
    queue = os.path.join(arch.home, "очередь", arch.batch)
    os.remove(os.path.join(queue, "отчёт.txt"))
    os.symlink(os.path.join(queue, "отчёт-два.txt"), os.path.join(queue, "отчёт.txt"))
    os.remove(os.path.join(arch.home, "карантин", arch.batch, "setup.exe"))
    os.mkdir(os.path.join(arch.home, "карантин", arch.batch, "setup.exe"))
    assert arch.where() == {**AFTER_INTAKE, "отчёт.txt": "missing", "setup.exe": "missing"}


def test_непонятное_решение_владельца_ничего_не_меняет(arch):
    arch.append(owner("отчёт.txt", "что-то-новое"))
    f = arch.file("отчёт.txt")
    assert (f["decided"], f["decided_at"], f["location"]) == (None, None, "queue")
    R.queue_accept(arch.home, arch.q("отчёт.txt"), table=arch.table, embed=embed)
    arch.append(owner("отчёт.txt", "что-то-новое"))
    assert (arch.file("отчёт.txt")["decided"], arch.file("отчёт.txt")["location"]) == ("accept", "corpus")


def test_имя_с_выходом_наверх_не_выводит_за_каталог(arch):
    """Квитанция с именем `../../x` не должна находить файл вне очереди: расположение — missing."""
    with open(os.path.join(arch.home, "x"), "w", encoding="utf-8") as f:
        f.write("снаружи")
    arch.append(rec("../../x", "review", path=f"очередь/{arch.batch}/../../x"))
    arch.append(rec("а//б", "review", path=f"очередь/{arch.batch}/а//б"))
    arch.append(rec("./x", "review", path=f"очередь/{arch.batch}/./x"))
    where = arch.where()
    assert where["../../x"] == "missing" and where["а//б"] == "missing" and where["./x"] == "missing"


def test_возврат_приёмкой_и_письмо_с_другими_байтами(tmp_path):
    """Квитанция с returned — файл в папке возврата; дубликат без returned хранить было нечего."""
    home = str(tmp_path / "flyarchive")
    write(home, "20261004-120000", rec("а.bin", "skip", returned="20261004-120000/а.bin"),
          rec("б.eml", "duplicate", returned="20261004-120000/б.eml"), rec("в.txt", "duplicate", duplicate_of="mail/в.txt"),
          rec("г.zip", "unpacked"), rec("д.txt", "skip"), rec("е.txt", "failed", returned="20261004-120000/е.txt"),
          rec("ж.txt", "accept", verified=False, path=None))
    by = {f["name"]: f["location"] for f in BT.batch_detail(home, "20261004-120000")["files"]}
    assert by == {"а.bin": "returned", "б.eml": "returned", "в.txt": None, "г.zip": None, "д.txt": None, "е.txt": "returned", "ж.txt": None}


def test_нет_места_хранения_в_квитанции_но_решение_владельца_есть(tmp_path):
    """Решение владельца определяет расположение и тогда, когда у записи приёмки места нет."""
    home = str(tmp_path / "flyarchive")
    write(home, "20261004-120000", rec("а", "skip"), rec("б", "skip"), owner("а", "returned"), owner("б", "deleted"))
    by = {f["name"]: f["location"] for f in BT.batch_detail(home, "20261004-120000")["files"]}
    assert by == {"а": "returned", "б": "deleted"}


# ── FR-73а: отказы истории несут код и параметры, русский текст прежний; замечания пишутся с кодом ──
def batch_refusal(call):
    with pytest.raises(BT.BatchError) as e:
        call()
    return e.value.message.code, e.value.message.args, str(e.value)


@pytest.mark.parametrize("bad", BAD_IDS[:-2] + [None, 20261004, b"20261004-120000"])
def test_негодный_номер_с_кодом_и_номером_как_его_видит_человек(home, bad):
    write(home, "20261004-120000", rec("а"))
    want = f"номер пачки должен быть вида ГГГГММДД-ЧЧММСС, при совпадении с -N на конце: {bad!r}"
    assert batch_refusal(lambda: BT.batch_detail(home, bad)) == ("batch.bad_id", {"value": repr(bad)}, want)


def test_негодный_before_с_тем_же_кодом(home):
    assert batch_refusal(lambda: BT.list_batches(home, before="вчера"))[:2] == ("batch.bad_id", {"value": "'вчера'"})


@pytest.mark.parametrize("limit", [0, 201, "abc", 2.5, True])
def test_негодный_лимит_с_кодом_и_пределом(home, limit):
    assert batch_refusal(lambda: BT.list_batches(home, limit=limit)) == ("batch.bad_limit", {"max": 200}, "--limit: целое число от 1 до 200")


def test_нет_такой_пачки_с_кодом_и_номером(home):
    write(home, "20261004-120000", rec("а"))
    assert batch_refusal(lambda: BT.batch_detail(home, "20261004-120001")) == (
        "batch.no_batch", {"batch": "20261004-120001"}, "нет такой пачки: 20261004-120001")


def test_запись_замечаний_кладёт_в_файл_код_параметры_и_текст(home):
    import messages
    os.makedirs(os.path.join(home, "квитанции"))
    notes = [messages.make("problem.copy_mismatch", name="договор.txt"), messages.make("problem.meta_not_written", error="OSError: диск")]
    BT.write_meta(home, "20261004-120000", 1_790_000_000.0, 1_790_000_007.0, notes)
    with open(os.path.join(home, "квитанции", "20261004-120000.meta.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert data["problems"] == [n.to_json() for n in notes] and data["seconds"] == 7
    assert [p["code"] for p in data["problems"]] == ["problem.copy_mismatch", "problem.meta_not_written"]
    assert data["problems"][0] == {"code": "problem.copy_mismatch", "args": {"name": "договор.txt"},
                                   "text": "договор.txt: sha256 копии не совпал с исходником — исходник оставлен во входящей папке"}


def test_запись_замечаний_без_кода_получает_общий_код_а_не_null(home):
    os.makedirs(os.path.join(home, "квитанции"))
    BT.write_meta(home, "20261004-120000", 1_790_000_000.0, 1_790_000_001.0, ["голая строка"])
    (p,) = BT.read_meta(home, "20261004-120000")["problems"]
    assert p == {"code": "generic.text", "args": {"text": "голая строка"}, "text": "голая строка"}


def test_старые_замечания_с_code_null_читаются_рядом_с_новыми(home):
    old, new = say("старое замечание"), {"code": "problem.copy_mismatch", "args": {"name": "а"}, "text": "новое"}
    write(home, "20261004-120000", rec("а"), meta={"started": "2026-10-04T12:00:00Z", "finished": "2026-10-04T12:00:03Z", "seconds": 3,
                                                      "problems": [old, new]})
    assert BT.read_meta(home, "20261004-120000")["problems"] == [old, new]
    assert BT.batch_detail(home, "20261004-120000")["problems"] == [old, new]
    assert BT.list_batches(home)["batches"][0]["problems"] == 2


# ══ FR-73б: причины, находки и примечания с сообщениями отдаются как есть; старые записи читаются ══
def coded(code, text, **args):
    return {"code": code, "args": args, "text": text}


NEW_FINDING = {"rule": "prompt_injection", "level": "HIGH", "where": "строка 1", "quote": "отмена прежних указаний: ignore all",
               "msg": coded("finding.prompt_injection", "отмена прежних указаний", kind="cancel_instructions", quoted=True),
               "where_msg": coded("where.line", "строка 1", line=1)}
OLD_FINDING = {"rule": "macros", "level": "HIGH", "where": "весь файл", "quote": "в документе макросы"}


def test_квитанция_с_новыми_полями_отдаётся_как_есть(home):
    batch = "20261004-120000"
    new = rec("а.txt", "review", reason="задержан до решения владельца: находка garbled",
              reason_msg=coded("reason.held", "задержан до решения владельца: находка garbled", rule="garbled"),
              findings=[NEW_FINDING], notes=["внутри был исполняемый файл: x.exe"],
              notes_msg=[coded("note.executable_inside", "внутри был исполняемый файл: x.exe", name="x.exe")])
    write(home, batch, new, meta={"started": "2026-10-04T12:00:00Z", "finished": "2026-10-04T12:00:01Z", "seconds": 1, "problems": []})
    (f,) = BT.batch_detail(home, batch)["files"]
    assert {k: f[k] for k in new} == new                                      # прежние и новые поля — без изменений
    assert f["reason_msg"]["code"] == "reason.held" and f["findings"][0]["msg"]["args"]["quoted"] is True
    assert f["findings"][0]["where_msg"] == coded("where.line", "строка 1", line=1) and f["notes_msg"][0]["args"] == {"name": "x.exe"}
    assert (f["decided"], f["location"]) == (None, None) and set(f) == set(new) | {"decided", "decided_at", "location"}


def test_квитанция_старого_образца_без_новых_полей_читается_и_ничего_не_добавляется(home):
    batch = "20261004-120000"
    old = rec("б.txt", "review", reason="повреждённый файл", findings=[OLD_FINDING], notes=["внутри был исполняемый файл: x.exe"])
    new = rec("а.txt", "review", reason="пустой файл", reason_msg=coded("reason.empty_file", "пустой файл"), findings=[NEW_FINDING])
    write(home, batch, new, old)
    d = BT.batch_detail(home, batch)
    files = {f["name"]: f for f in d["files"]}
    assert "reason_msg" not in files["б.txt"] and "notes_msg" not in files["б.txt"] and files["б.txt"]["findings"] == [OLD_FINDING]
    assert files["б.txt"]["reason"] == "повреждённый файл" and files["а.txt"]["reason_msg"]["code"] == "reason.empty_file"
    assert d["counts"] == {"review": 2}
    assert BT.list_batches(home)["batches"][0]["files"] == 2


def test_новые_поля_пустые_и_null_отдаются_как_есть(home):
    batch = "20261004-120000"
    write(home, batch, rec("а.txt", reason_msg=None, findings=[{**OLD_FINDING, "msg": None, "where_msg": None}], notes=[], notes_msg=[]))
    (f,) = BT.batch_detail(home, batch)["files"]
    assert f["reason_msg"] is None and f["notes_msg"] == [] and f["findings"][0]["msg"] is None and f["findings"][0]["where_msg"] is None


def test_решение_владельца_не_портит_новые_поля_записи(home):
    batch = "20261004-120000"
    new = rec("а.txt", "review", reason_msg=coded("reason.held", "задержан до решения владельца: находка garbled", rule="garbled"),
              findings=[NEW_FINDING])
    write(home, batch, new, owner("а.txt", "accept"))
    (f,) = BT.batch_detail(home, batch)["files"]
    assert f["decided"] == "accept" and f["reason_msg"] == new["reason_msg"] and f["findings"] == [NEW_FINDING]

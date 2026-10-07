"""Основа пополнения архива: дата документа (FR-45), запись в базу известного, индексация принятого (FR-42)."""
import os
import time

import pytest

import docdate as D
import gatekit as K
import ingest as G
import known as N

ACCEPTED = "2026-10-04"


def put(tmp_path, name, data):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return str(p)


def office(core):
    """docx со свойствами документа: дата лежит в docProps/core.xml."""
    doc = K.ooxml("docx", "Договор поставки.")
    import io
    import zipfile
    buf = io.BytesIO(doc)
    with zipfile.ZipFile(buf, "a") as z:
        z.writestr("docProps/core.xml", core)
    return buf.getvalue()


CORE = ('<cp:coreProperties xmlns:cp="x" xmlns:dcterms="y"><dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified></cp:coreProperties>')


# ── дата документа: метаданные → имя → время изменения → дата приёмки ──
def test_дата_письма_из_заголовка(tmp_path):
    p = put(tmp_path, "письмо.eml", K.letter())
    date, how = D.date_of(p, "eml", "письмо.eml", ACCEPTED, mtime=time.time())
    assert how == "метаданные" and len(date) == 10 and date != ACCEPTED


def test_дата_документа_office_из_свойств(tmp_path):
    p = put(tmp_path, "2020-01-01_договор.docx", office(CORE.format(created="2023-05-10T08:00:00Z", modified="2024-03-05T10:20:30Z")))
    assert D.date_of(p, "docx", "2020-01-01_договор.docx", ACCEPTED, mtime=1.0) == ("2024-03-05", "метаданные")


def test_нет_даты_изменения_берётся_дата_создания(tmp_path):
    core = '<cp:coreProperties><dcterms:created xsi:type="dcterms:W3CDTF">2023-05-10T08:00:00Z</dcterms:created></cp:coreProperties>'
    p = put(tmp_path, "договор.docx", office(core))
    assert D.date_of(p, "docx", "договор.docx", ACCEPTED) == ("2023-05-10", "метаданные")


def test_дата_pdf_из_свойств(tmp_path):
    p = put(tmp_path, "отчёт.pdf", K.pdf_plain(tail=b"\n1 0 obj\n<< /CreationDate (D:20230917120000+06'00') /ModDate (D:20231002090000Z) >>\nendobj\n"))
    assert D.date_of(p, "pdf", "отчёт.pdf", ACCEPTED) == ("2023-10-02", "метаданные")


@pytest.mark.parametrize("name, date", [
    ("2021-05-17_отчёт.txt", "2021-05-17"), ("отчёт_17.05.2021.txt", "2021-05-17"), ("Протокол 2019-12-31 итог.txt", "2019-12-31"),
])
def test_дата_из_имени_когда_в_файле_её_нет(tmp_path, name, date):
    p = put(tmp_path, name, "текст")
    assert D.date_of(p, "txt", name, ACCEPTED, mtime=1_600_000_000) == (date, "имя")


def test_дата_из_имени_берётся_по_имени_файла_а_не_каталога(tmp_path):
    p = put(tmp_path, "2018-01-01_пачка/отчёт.txt", "текст")
    date, how = D.date_of(p, "txt", "2018-01-01_пачка/отчёт.txt", ACCEPTED, mtime=1_600_000_000)
    assert (date, how) == ("2020-09-13", "время изменения")


def test_время_изменения_когда_нет_ни_свойств_ни_даты_в_имени(tmp_path):
    p = put(tmp_path, "заметка.txt", "текст")
    assert D.date_of(p, "txt", "заметка.txt", ACCEPTED, mtime=1_600_000_000) == ("2020-09-13", "время изменения")


def test_дата_приёмки_с_пометкой_когда_взять_неоткуда(tmp_path):
    p = put(tmp_path, "заметка.txt", "текст")
    assert D.date_of(p, "txt", "заметка.txt", ACCEPTED, mtime=None) == (ACCEPTED, "дата приёмки")


@pytest.mark.parametrize("bad", ["1601-01-01T00:00:00Z", "0001-01-01T00:00:00Z", "2099-01-01T00:00:00Z", "2024-13-45T00:00:00Z", "мусор"])
def test_неправдоподобная_дата_в_свойствах_пропускается(tmp_path, bad):
    p = put(tmp_path, "2022-02-02_договор.docx", office(CORE.format(created=bad, modified=bad)))
    assert D.date_of(p, "docx", "2022-02-02_договор.docx", ACCEPTED) == ("2022-02-02", "имя")


@pytest.mark.parametrize("name", ["2021-13-45_отчёт.txt", "отчёт_99.99.2021.txt", "1850-01-01.txt", "2099-01-01.txt"])
def test_неправдоподобная_дата_в_имени_пропускается(tmp_path, name):
    p = put(tmp_path, name, "текст")
    assert D.date_of(p, "txt", name, ACCEPTED, mtime=None) == (ACCEPTED, "дата приёмки")


def test_время_изменения_из_будущего_или_нулевое_не_берётся(tmp_path):
    p = put(tmp_path, "заметка.txt", "текст")
    for mtime in (0, 4_102_444_800):
        assert D.date_of(p, "txt", "заметка.txt", ACCEPTED, mtime=mtime) == (ACCEPTED, "дата приёмки")


def test_испорченный_файл_не_роняет_определение_даты(tmp_path):
    p = put(tmp_path, "2022-02-02_договор.docx", b"PK\x03\x04 broken")
    assert D.date_of(p, "docx", "2022-02-02_договор.docx", ACCEPTED) == ("2022-02-02", "имя")


# ── база известного: запись принятого ───────────────────────────
@pytest.fixture
def base(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "mail").mkdir(parents=True)
    (corpus / "mail" / "старое.txt").write_text("уже в архиве", encoding="utf-8")
    db = str(tmp_path / "index" / "known.sqlite")
    N.build(str(corpus), db)
    return db


def test_принятое_сразу_находится_в_базе_известного(base):
    N.add(base, [("входящие/20261004-120000/договор.docx", 100, 1700000000, "a" * 64, None, None, None),
                 ("входящие/20261004-120000/письмо.eml", 200, 1700000000, "b" * 64, "id-1@example.org", "f" * 32, "2024-03-05")])
    k = N.Known(base)
    assert k.find(sha256="a" * 64) == "входящие/20261004-120000/договор.docx"
    assert k.find(mid="id-1@example.org") == "входящие/20261004-120000/письмо.eml"
    assert k.date_of("входящие/20261004-120000/письмо.eml") == "2024-03-05"
    assert k.info()["files"] == 3 and k.info()["mail"] == 1


def test_повторная_запись_того_же_пути_не_плодит_строки(base):
    row = ("входящие/x/договор.docx", 100, 1700000000, "a" * 64, None, None, None)
    N.add(base, [row])
    N.add(base, [row[:3] + ("c" * 64,) + row[4:]])
    k = N.Known(base)
    assert k.info()["files"] == 2 and k.find(sha256="c" * 64) and k.find(sha256="a" * 64) is None


def test_без_базы_известного_запись_отклоняется(tmp_path):
    with pytest.raises(N.KnownError):
        N.add(str(tmp_path / "нет.sqlite"), [("a", 1, 1, "a" * 64, None, None, None)])


def test_файл_базы_после_записи_закрыт_от_других(base):
    N.add(base, [("входящие/x/a.txt", 1, 1, "a" * 64, None, None, None)])
    assert os.stat(base).st_mode & 0o077 == 0


# ── индексация принятого ────────────────────────────────────────
class Table:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def add(self, rows):
        if self.fail:
            raise RuntimeError("таблица недоступна")
        self.rows.extend(rows)


def embed(texts):
    return [[float(len(t))] * G.DIM for t in texts]


def item(tmp_path, rel, data, **kw):
    path = put(tmp_path / "corpus", rel, data)
    return {"path": path, "rel": rel, "title": kw.get("title", os.path.basename(rel)), "updated": kw.get("updated", "2024-03-05"),
            "space": kw.get("space", rel.split("/")[1])}


def test_принятый_документ_попадает_в_индекс_базой_входящие(tmp_path):
    table = Table()
    it = item(tmp_path, "входящие/20261004-120000/записка.txt", "Согласовать перенос работ на четверг.")
    r = G.index_documents([it], table, embed)
    (row,) = table.rows
    assert row == {"path": "входящие/20261004-120000/записка.txt", "source": "входящие", "space": "20261004-120000",
                   "title": "записка.txt", "updated": "2024-03-05", "url": "", "chunk": 0,
                   "text": "Согласовать перенос работ на четверг.", "vector": [37.0] * G.DIM}
    assert r.indexed == ["входящие/20261004-120000/записка.txt"] and r.chunks == 1 and r.skipped == [] and r.failed == []


def test_длинный_документ_режется_на_фрагменты_по_порядку(tmp_path):
    table = Table()
    r = G.index_documents([item(tmp_path, "входящие/b/длинный.txt", "слово " * 3000)], table, embed)
    assert [row["chunk"] for row in table.rows] == list(range(len(table.rows))) and len(table.rows) >= 4
    assert r.chunks == len(table.rows) and all(len(row["text"]) <= 4000 for row in table.rows)


def test_у_письма_заголовком_становится_тема(tmp_path):
    table = Table()
    it = item(tmp_path, "входящие/b/2024-03-05_письмо.eml", K.letter(subject="Договор поставки"))
    it.pop("title")
    G.index_documents([it], table, embed)
    assert table.rows[0]["title"] == "Договор поставки" and "Тема: Договор поставки" in table.rows[0]["text"]


def test_картинка_и_пустой_файл_в_индекс_не_идут_и_это_названо(tmp_path):
    table = Table()
    items = [item(tmp_path, "входящие/b/снимок.png", b"\x89PNG\r\n\x1a\n"), item(tmp_path, "входящие/b/пусто.txt", "   \n")]
    r = G.index_documents(items, table, embed)
    assert table.rows == [] and r.indexed == []
    assert dict(r.skipped) == {"входящие/b/снимок.png": "текст из такого файла не извлекается",
                               "входящие/b/пусто.txt": "в документе нет текста"}


def test_сбой_вычисления_вектора_документ_остаётся_в_долгах_остальные_идут(tmp_path):
    table = Table()

    def flaky(texts):
        if any("плохой" in t for t in texts):
            raise OSError("ollama не отвечает")
        return embed(texts)

    items = [item(tmp_path, "входящие/b/хороший.txt", "хороший документ"), item(tmp_path, "входящие/b/плохой.txt", "плохой документ"),
             item(tmp_path, "входящие/b/ещё.txt", "ещё один документ")]
    r = G.index_documents(items, table, flaky)
    assert r.indexed == ["входящие/b/хороший.txt", "входящие/b/ещё.txt"]
    assert [rel for rel, _ in r.failed] == ["входящие/b/плохой.txt"] and "OSError" in r.failed[0][1]
    assert sorted(row["path"] for row in table.rows) == ["входящие/b/ещё.txt", "входящие/b/хороший.txt"]


def test_сбой_записи_в_таблицу_ничего_не_считается_проиндексированным(tmp_path):
    r = G.index_documents([item(tmp_path, "входящие/b/а.txt", "текст")], Table(fail=True), embed)
    assert r.indexed == [] and r.chunks == 0 and len(r.failed) == 1


def test_нечитаемый_документ_не_роняет_остальные(tmp_path):
    table = Table()
    items = [item(tmp_path, "входящие/b/битый.docx", b"PK\x03\x04 broken"), item(tmp_path, "входящие/b/целый.txt", "текст")]
    r = G.index_documents(items, table, embed)
    assert r.indexed == ["входящие/b/целый.txt"] and r.failed[0][0] == "входящие/b/битый.docx"


def test_документ_пишется_в_таблицу_целиком_одним_вызовом(tmp_path):
    calls = []

    class Counting(Table):
        def add(self, rows):
            calls.append(len(rows))
            super().add(rows)

    G.index_documents([item(tmp_path, "входящие/b/длинный.txt", "слово " * 3000)], Counting(), embed)
    assert len(calls) == 1 and calls[0] >= 4


def test_каталог_входящих_учтён_в_полной_пересборке_индекса():
    """Иначе после пересборки индекса принятое через входящие пропало бы из поиска."""
    import index_more as M
    roots = [(source, os.path.basename(root)) for source, root, _ in M.ROOTS]
    assert ("входящие", "входящие") in roots
    root = os.path.join(os.sep, "tmp", "corpus", "входящие")
    source, space, title, updated = M.meta_for("входящие", root, os.path.join(root, "20261004-120000", "2024-03-05_записка.txt"), "текст")
    assert (source, space, title, updated) == ("входящие", "20261004-120000", "2024-03-05_записка.txt", "2024-03-05")


def test_страница_поиска_предлагает_базу_входящие():
    import webui
    assert '<option value="входящие">' in webui.form() and '<option selected value="входящие">' in webui.form(source="входящие")


def test_файл_без_расширения_индексируется_по_определённому_типу(tmp_path):
    """Вложение с испорченным именем: приёмка узнала тип по содержимому, индексатор должен верить ей."""
    table = Table()
    plain = item(tmp_path, "входящие/b/=_koi8-r_Q_договор_=", "Договор поставки оборудования.")
    plain["type"] = "txt"
    r = G.index_documents([plain], table, embed)
    assert r.indexed == ["входящие/b/=_koi8-r_Q_договор_="] and "Договор поставки" in table.rows[0]["text"]


def test_docx_без_расширения_индексируется_по_определённому_типу(tmp_path):
    pytest.importorskip("docx")                      # тип docx индексатор читает через python-docx
    table = Table()
    it = item(tmp_path, "входящие/b/=_koi8-r_Q_договор_=", K.ooxml("docx", "Договор поставки оборудования."))
    it["type"] = "docx"
    r = G.index_documents([it], table, embed)
    assert r.indexed == ["входящие/b/=_koi8-r_Q_договор_="] and "Договор поставки" in table.rows[0]["text"]


def test_известное_расширение_важнее_типа(tmp_path):
    """Схема в xml по типу — просто текст, но у расширения свой разбор: без тегов, с подписями."""
    table = Table()
    it = item(tmp_path, "входящие/b/схема.xml", '<root><task name="Согласование договора">шаг первый</task></root>')
    it["type"] = "txt"
    assert G.index_documents([it], table, embed).indexed == ["входящие/b/схема.xml"]
    assert "<" not in table.rows[0]["text"] and "Согласование договора" in table.rows[0]["text"]


def test_неизвестный_тип_без_расширения_в_индекс_не_идёт(tmp_path):
    it = item(tmp_path, "входящие/b/нечто", b"\x00\x01\x02")
    it["type"] = "binary"
    r = G.index_documents([it], Table(), embed)
    assert r.indexed == [] and r.skipped[0][1] == "текст из такого файла не извлекается"


def test_таблица_excel_с_испорченным_расширением_читается(tmp_path):
    """Найдено на живом приёме: openpyxl сам смотрит на расширение и файл `….xlsx_=` открывать отказывается."""
    openpyxl = pytest.importorskip("openpyxl")
    import io
    wb = openpyxl.Workbook()
    wb.active.append(["Статья", "Бюджет"])
    wb.active.append(["Перераспределение бюджета", 1200])
    buf = io.BytesIO()
    wb.save(buf)
    table = Table()
    it = item(tmp_path, "входящие/b/2025-05-13_staff.xlsx_=", buf.getvalue())
    it["type"] = "xlsx"
    r = G.index_documents([it], table, embed)
    assert r.indexed == ["входящие/b/2025-05-13_staff.xlsx_="] and r.failed == []
    assert "Перераспределение бюджета" in table.rows[0]["text"]
    assert sorted(os.listdir(os.path.dirname(it["path"]))) == ["2025-05-13_staff.xlsx_="]      # рядом ничего не появилось


def test_удалённое_из_архива_больше_не_считается_известным(base):
    N.add(base, [("входящие/x/а.txt", 1, 1, "a" * 64, None, None, None), ("входящие/x/б.txt", 1, 1, "b" * 64, None, None, None)])
    N.remove(base, ["входящие/x/а.txt", "нет/такого.txt"])
    k = N.Known(base)
    assert k.find(sha256="a" * 64) is None and k.find(sha256="b" * 64) == "входящие/x/б.txt"
    with pytest.raises(N.KnownError):
        N.remove(base + ".нет", ["a"])

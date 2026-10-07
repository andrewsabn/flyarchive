"""Чистка индекса от повторов: FR-29. Письма по Message-ID, файлы почты по содержимому."""
import os

import pytest

import dedupe_index as D
import gatekit as K
import known as N

DOC = K.ooxml("docx", "Договор поставки.")
LOGO = K.PNG + b"logo"
BS = chr(92)


@pytest.fixture(autouse=True)
def owner_table(monkeypatch):
    """Корни, псевдонимы и места выгрузок берутся из таблицы источников; здесь заданы такими, какими были в коде до выноса правил в таблицу источников (FR-99)."""
    monkeypatch.setattr(D, "ALIAS", {"sample_confluence_export": "confluence", "sample_jira_export": "jira"})
    monkeypatch.setattr(D, "ROOTS", ("export-a", "export-b", "export-c"))
    monkeypatch.setattr(D, "ROOT_RANK", {"export-c": 0, "export-b": 1})


@pytest.fixture
def arch(tmp_path):
    root = tmp_path / "corpus"
    db = str(tmp_path / "index" / "known.sqlite")

    def put(rel, data):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def ready():
        N.build(str(root), db)
        return db

    root.mkdir()
    put.ready = ready
    return put


def win(path):
    """Путь в том виде, как его записал первый индексатор под Windows."""
    return path.replace("/", BS)


# ── план ────────────────────────────────────────────────────────
def test_письмо_из_трёх_выгрузок_остаётся_одно(arch):
    arch("export-a/Outlook_box_in/Inbox/a.eml", K.letter())
    arch("export-a/Outlook_box_out/Inbox/a.eml", K.letter(extra=K.RECEIVED))            # те же письма, другие байты
    arch("export-a/20200101_sample/Inbox/a.eml", K.letter(crlf=False))
    arch("export-a/Outlook_box_in/Inbox/b.eml", K.letter(mid="<other@example.org>", body="Другое письмо."))
    paths = [win(p) for p in ("export-a/Outlook_box_in/Inbox/a.eml", "export-a/Outlook_box_out/Inbox/a.eml",
                              "export-a/20200101_sample/Inbox/a.eml", "export-a/Outlook_box_in/Inbox/b.eml")]
    plan = D.plan(paths, arch.ready())
    assert sorted(plan.drop) == sorted([win("export-a/Outlook_box_in/Inbox/a.eml"), win("export-a/Outlook_box_out/Inbox/a.eml")])
    assert (plan.letters, plan.files, plan.groups) == (2, 0, 1)


def test_остаётся_копия_из_более_полной_выгрузки(arch):
    arch("export-a/aaa/a.eml", K.letter())
    arch("export-b/bbb/a.eml", K.letter(extra=K.RECEIVED))
    arch("export-c/zzz/a.eml", K.letter(crlf=False))                 # по алфавиту последняя, но выгрузка самая полная
    plan = D.plan(["export-a/aaa/a.eml", "export-b/bbb/a.eml", "export-c/zzz/a.eml"], arch.ready())
    assert sorted(plan.drop) == ["export-a/aaa/a.eml", "export-b/bbb/a.eml"]
    assert D.plan(["export-a/aaa/a.eml", "export-b/bbb/a.eml"], arch.ready()).drop == ["export-a/aaa/a.eml"]


def test_вложения_повторных_выгрузок_тоже_остаются_в_одном_месте(arch):
    for box in ("20200101_sample", "Outlook_box_in", "Outlook_box_out"):
        arch(f"export-a/{box}/Inbox/a.eml", K.letter(extra=box.encode() + b": 1\r\n"))
        arch(f"export-a/{box}/Inbox/_attachments/a/договор.docx", DOC)
        arch(f"export-a/{box}/Inbox/_attachments/a/image001.png", LOGO)
    paths = [f"export-a/{box}/Inbox/{rel}" for box in ("20200101_sample", "Outlook_box_in", "Outlook_box_out")
             for rel in ("a.eml", "_attachments/a/договор.docx", "_attachments/a/image001.png")]
    plan = D.plan(paths, arch.ready())
    kept = sorted(set(paths) - set(plan.drop))
    assert kept == [p for p in sorted(paths) if "20200101_sample" in p]                # письмо и его вложения — из одной выгрузки
    assert (plan.letters, plan.files) == (2, 4)


def test_внутри_одной_выгрузки_остаётся_более_полная_копия(arch):
    """Одно письмо лежит дважды: с вложением внутри и без него. Остаётся то, что полнее."""
    from email.message import EmailMessage

    def with_attachment():
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = "Petrov <petrov@example.org>", "ivanov@example.org", "Договор"
        m["Date"], m["Message-ID"] = "Tue, 05 Mar 2024 14:32:00 +0500", "<abc123@example.org>"
        m.set_content("Добрый день. Направляю договор на согласование.")
        m.add_attachment(DOC * 3, maintype="application", subtype="octet-stream", filename="договор.docx")
        return m.as_bytes()

    arch("export-a/Outlook_acme/Inbox/a_краткая.eml", K.letter())
    arch("export-a/Outlook_acme/Sent/я_полная.eml", with_attachment())
    arch("export-a/Outlook_box_in/Inbox/a.eml", K.letter(extra=K.RECEIVED * 200))        # другая выгрузка, файл больше
    paths = ["export-a/Outlook_acme/Inbox/a_краткая.eml", "export-a/Outlook_acme/Sent/я_полная.eml", "export-a/Outlook_box_in/Inbox/a.eml"]
    plan = D.plan(paths, arch.ready())
    assert set(paths) - set(plan.drop) == {"export-a/Outlook_acme/Sent/я_полная.eml"}


def test_остаётся_копия_с_настоящей_датой_а_не_с_датой_копирования(arch):
    """У копии без даты в имени в индексе стоит день копирования. Самая ранняя дата среди копий — настоящая."""
    arch("export-a/20200101_sample/Sent/_attachments/a/отчёт.docx", DOC)
    arch("export-a/Outlook_box_in/Inbox/_attachments/2019-01-09_b/отчёт.docx", DOC)
    arch("export-a/Outlook_box_out/Inbox/_attachments/2019-03-01_c/отчёт.docx", DOC)
    paths = ["export-a/20200101_sample/Sent/_attachments/a/отчёт.docx", "export-a/Outlook_box_in/Inbox/_attachments/2019-01-09_b/отчёт.docx",
             "export-a/Outlook_box_out/Inbox/_attachments/2019-03-01_c/отчёт.docx"]
    dates = dict(zip(paths, ["2026-08-31", "2019-01-09", "2019-03-01"]))
    db = arch.ready()
    assert set(paths) - set(D.plan(paths, db, dates=dates).drop) == {paths[1]}
    assert set(paths) - set(D.plan(paths, db).drop) == {paths[0]}                     # без дат — по порядку выгрузок
    undated = dict(zip(paths, ["", None, "2019-03-01"]))
    assert set(paths) - set(D.plan(paths, db, dates=undated).drop) == {paths[2]}      # пустая дата хуже любой настоящей


def test_при_равных_датах_порядок_выгрузок_прежний(arch):
    arch("export-a/Outlook_box_in/Inbox/a.eml", K.letter())
    arch("export-a/20200101_sample/Inbox/a.eml", K.letter(crlf=False))
    paths = ["export-a/Outlook_box_in/Inbox/a.eml", "export-a/20200101_sample/Inbox/a.eml"]
    plan = D.plan(paths, arch.ready(), dates={p: "2020-02-20" for p in paths})
    assert plan.drop == ["export-a/Outlook_box_in/Inbox/a.eml"]


def test_разные_письма_с_одинаковым_текстом_остаются_оба(arch):
    arch("export-a/x/a.eml", K.letter(mid="<first@example.org>"))
    arch("export-a/x/b.eml", K.letter(mid="<second@example.org>"))
    assert D.plan(["export-a/x/a.eml", "export-a/x/b.eml"], arch.ready()).drop == []


def test_письма_без_message_id_сливаются_только_при_полном_совпадении(arch):
    arch("export-a/x/a.eml", K.letter(mid=None))
    arch("export-a/y/a.eml", K.letter(mid=None))                                        # байт в байт
    arch("export-a/z/a.eml", K.letter(mid=None, extra=K.RECEIVED))                      # те же поля, другие байты
    plan = D.plan(["export-a/x/a.eml", "export-a/y/a.eml", "export-a/z/a.eml"], arch.ready())
    assert plan.drop == ["export-a/y/a.eml"]


def test_вне_почты_ничего_не_трогается(arch):
    arch("jira/IT/_attachments/IT-1/схема.docx", DOC)
    arch("jira/IT/_attachments/IT-2/схема.docx", DOC)                                # тот же файл при другой задаче
    arch("confluence/DEMO/страница.md", b"text")
    arch("export-a/x/_attachments/a/схема.docx", DOC)
    paths = [win("jira/IT/_attachments/IT-1/схема.docx"), win("jira/IT/_attachments/IT-2/схема.docx"),
             "confluence/DEMO/страница.md", "export-a/x/_attachments/a/схема.docx"]
    plan = D.plan(paths, arch.ready())
    assert plan.drop == [] and plan.outside == 3


def test_один_файл_записанный_в_индекс_двумя_видами_пути(arch):
    arch("export-a/x/a.eml", K.letter())
    arch("export-a/x/_attachments/a/договор.docx", DOC)
    paths = ["export-a/x/a.eml", win("export-a/x/a.eml"), "export-a/x/_attachments/a/договор.docx", win("export-a/x/_attachments/a/договор.docx")]
    plan = D.plan(paths, arch.ready())
    assert len(plan.drop) == 2 and plan.same_file == 2
    assert {D.norm(p) for p in set(paths) - set(plan.drop)} == {"export-a/x/a.eml", "export-a/x/_attachments/a/договор.docx"}


def test_старые_имена_корней_приводятся_к_нынешним():
    assert D.norm("sample_jira_export" + BS + "IT" + BS + "a.md") == "jira/IT/a.md"
    assert D.norm("sample_confluence_export/DEMO/a.md") == "confluence/DEMO/a.md"
    assert D.norm(BS + "export-a" + BS + "x" + BS + "a.eml") == "export-a/x/a.eml"


def test_путь_которого_нет_в_корпусе_не_трогается(arch):
    arch("export-a/x/a.eml", K.letter())
    plan = D.plan(["export-a/x/a.eml", "export-a/x/пропавший.eml", "export-a/y/пропавший.eml"], arch.ready())
    assert plan.drop == [] and plan.unknown == 2


def test_повторный_план_после_чистки_пуст(arch):
    arch("export-a/x/a.eml", K.letter())
    arch("export-a/y/a.eml", K.letter(extra=K.RECEIVED))
    db = arch.ready()
    first = D.plan(["export-a/x/a.eml", "export-a/y/a.eml"], db)
    left = [p for p in ("export-a/x/a.eml", "export-a/y/a.eml") if p not in first.drop]
    assert len(left) == 1 and D.plan(left, db).drop == []


# ── применение к настоящей таблице ──────────────────────────────
@pytest.fixture
def table(tmp_path):
    lancedb = pytest.importorskip("lancedb")
    if not hasattr(lancedb, "connect") or getattr(lancedb.connect, "__name__", "") == "_no_db":
        pytest.skip("нет настоящей lancedb")
    rows = []
    for path in ("export-a" + BS + "x" + BS + "a.eml", "export-a" + BS + "y" + BS + "a.eml", "export-a/y/O'Brien's письмо.eml",
                 "jira" + BS + "IT" + BS + "задача.md"):
        for chunk in range(3):
            rows.append({"path": path, "source": "mail", "chunk": chunk, "text": f"{path} {chunk}", "vector": [0.0, 1.0]})
    db = lancedb.connect(str(tmp_path / "lance"))
    return db.create_table("docs", rows)


def test_удаляются_все_фрагменты_названных_путей_и_только_они(table):
    drop = ["export-a" + BS + "y" + BS + "a.eml", "export-a/y/O'Brien's письмо.eml"]
    assert D.indexed_paths(table) == {"export-a" + BS + "x" + BS + "a.eml", "export-a" + BS + "y" + BS + "a.eml",
                                      "export-a/y/O'Brien's письмо.eml", "jira" + BS + "IT" + BS + "задача.md"}
    assert D.apply(table, drop, batch=1) == 6
    assert table.count_rows() == 6
    assert D.indexed_paths(table) == {"export-a" + BS + "x" + BS + "a.eml", "jira" + BS + "IT" + BS + "задача.md"}


def test_пустой_список_ничего_не_удаляет(table):
    assert D.apply(table, []) == 0 and table.count_rows() == 12


def test_число_фрагментов_по_путям(table):
    counts = D.row_counts(table)
    assert counts["export-a/y/O'Brien's письмо.eml"] == 3 and sum(counts.values()) == 12


def test_даты_путей_из_индекса(tmp_path):
    lancedb = pytest.importorskip("lancedb")
    rows = [{"path": "a.eml", "updated": "2020-05-01", "chunk": 0, "vector": [0.0, 1.0]},
            {"path": "a.eml", "updated": "2020-05-01", "chunk": 1, "vector": [0.0, 1.0]},
            {"path": "b.eml", "updated": "", "chunk": 0, "vector": [0.0, 1.0]}]
    table = lancedb.connect(str(tmp_path / "l")).create_table("docs", rows)
    counts, dates = D.index_meta(table)
    assert (counts["a.eml"], counts["b.eml"]) == (2, 1) and dates == {"a.eml": "2020-05-01", "b.eml": ""}

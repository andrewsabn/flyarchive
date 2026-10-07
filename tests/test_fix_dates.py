"""Настоящие даты в индексе: FR-45а. Письму — дата из заголовка Date, вложению — дата того, к чему оно приложено."""
import os

import pytest

import dedupe_index as D
import fix_dates as F
import gatekit as K
import known as N

BS = chr(92)
COPY = "2026-08-31"          # день копирования архива: такая дата стоит у файлов без даты в имени
DOC = K.ooxml("docx", "Договор поставки.")


@pytest.fixture(autouse=True)
def owner_table(monkeypatch):
    """Корни писем и страниц и старые имена корней берутся из таблицы источников; здесь заданы такими, какими были в коде до выноса правил в таблицу источников (FR-99)."""
    monkeypatch.setattr(F, "MAIL", ("export-a", "export-b", "export-c"))
    monkeypatch.setattr(F, "PAGES", ("jira", "confluence"))
    monkeypatch.setattr(D, "ALIAS", {"sample_confluence_export": "confluence", "sample_jira_export": "jira"})


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
    return path.replace("/", BS)


def letter(day, mid="<a@example.org>"):
    return K.letter(mid=mid, date=f"{day} Mar 2024 14:32:00 +0500")


# ── письма ──────────────────────────────────────────────────────
def test_письму_ставится_дата_из_заголовка(arch):
    arch("export-a/x/Inbox/RE__План.eml", letter(5))
    plan = F.plan({win("export-a/x/Inbox/RE__План.eml"): COPY}, arch.ready())
    assert plan.new == {win("export-a/x/Inbox/RE__План.eml"): "2024-03-05"} and plan.letters == 1


def test_верная_дата_не_трогается(arch):
    arch("export-a/x/Inbox/2024-03-05_petrov_План_0a1b2c3d.eml", letter(5))
    plan = F.plan({"export-a/x/Inbox/2024-03-05_petrov_План_0a1b2c3d.eml": "2024-03-05"}, arch.ready())
    assert plan.new == {} and plan.same == 1


def test_дата_из_имени_файла_уступает_заголовку(arch):
    """В имени письма стоит день выгрузки, в заголовке — день отправки. Верить надо заголовку."""
    arch("export-c/export/Inbox/2026-04-20_petrov_План_0a1b2c3d.eml", letter(5))
    plan = F.plan({"export-c/export/Inbox/2026-04-20_petrov_План_0a1b2c3d.eml": "2026-04-20"}, arch.ready())
    assert list(plan.new.values()) == ["2024-03-05"]


def test_письмо_без_даты_в_заголовках_остаётся_как_было(arch):
    arch("export-a/x/Inbox/a.eml", b"From: petrov@example.org\r\nSubject: Plan\r\nMessage-ID: <x@example.org>\r\n\r\nBody\r\n")
    plan = F.plan({"export-a/x/Inbox/a.eml": COPY}, arch.ready())
    assert plan.new == {} and plan.unknown == 1


# ── вложения писем ──────────────────────────────────────────────
def test_вложению_ставится_дата_его_письма(arch):
    arch("export-a/x/Inbox/2024-03-05_petrov_План_0a1b2c3d.eml", letter(5))
    arch("export-a/x/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/договор.docx", DOC)
    arch("export-a/x/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/image001.png", K.PNG)
    paths = {win("export-a/x/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/договор.docx"): COPY,
             win("export-a/x/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/image001.png"): COPY}
    plan = F.plan(paths, arch.ready())
    assert set(plan.new.values()) == {"2024-03-05"} and len(plan.new) == 2 and plan.by_parent == 2


def test_дата_письма_важнее_даты_в_имени_каталога_вложений(arch):
    arch("export-a/x/Inbox/2024-03-06_petrov_План_0a1b2c3d.eml", letter(5))               # в имени день приёма, в заголовке день отправки
    arch("export-a/x/Inbox/_attachments/2024-03-06_petrov_Пл_0a1b2c3d/договор.docx", DOC)
    plan = F.plan({"export-a/x/Inbox/_attachments/2024-03-06_petrov_Пл_0a1b2c3d/договор.docx": COPY}, arch.ready())
    assert list(plan.new.values()) == ["2024-03-05"]


def test_письма_нет_но_дата_есть_в_имени_каталога_вложений(arch):
    arch("export-a/x/Inbox/_attachments/2019-04-08_V_HA___646821be/image002.png", K.PNG)
    arch("export-a/x/Inbox/_attachments/без_даты_646821bf/image003.png", K.PNG + b"x")
    paths = {"export-a/x/Inbox/_attachments/2019-04-08_V_HA___646821be/image002.png": COPY,
             "export-a/x/Inbox/_attachments/без_даты_646821bf/image003.png": COPY}
    plan = F.plan(paths, arch.ready())
    assert plan.new == {"export-a/x/Inbox/_attachments/2019-04-08_V_HA___646821be/image002.png": "2019-04-08"}
    assert (plan.by_name, plan.unknown) == (1, 1)


def test_вложенное_письмо_получает_свою_дату(arch):
    """Пересланное письмо двухлетней давности лежит вложением: у него своя дата, а не дата пересылки."""
    arch("export-a/x/Inbox/2024-03-05_petrov_План_0a1b2c3d.eml", letter(5))
    arch("export-a/x/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/старое.eml",
         K.letter(mid="<old@example.org>", date="Mon, 07 Mar 2022 10:00:00 +0500"))
    plan = F.plan({"export-a/x/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/старое.eml": COPY}, arch.ready())
    assert list(plan.new.values()) == ["2022-03-07"]


def test_письмо_ищется_только_в_своей_папке(arch):
    arch("export-a/x/Sent/2024-03-09_petrov_План_0a1b2c3d.eml", letter(9, mid="<other@example.org>"))   # тот же код в другой папке
    arch("export-a/x/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/договор.docx", DOC)
    plan = F.plan({"export-a/x/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/договор.docx": COPY}, arch.ready())
    assert list(plan.new.values()) == ["2024-03-05"] and plan.by_name == 1


# ── вложения задач и страниц ────────────────────────────────────
def test_вложению_задачи_ставится_дата_задачи(arch):
    db = arch.ready()
    paths = {win("jira/TEAM/TEAM-26__Метрики.md"): "2025-06-15",
             win("sample_jira_export/TEAM/_attachments/TEAM-26/Метрики.pptx"): "2026-06-16",
             win("jira/TEAM/_attachments/TEAM-26/image-1.png"): "2026-06-17",
             win("jira/TEAM/_attachments/TEAM-99/без-задачи.png"): "2026-06-17"}
    plan = F.plan(paths, db)
    assert plan.new == {win("sample_jira_export/TEAM/_attachments/TEAM-26/Метрики.pptx"): "2025-06-15",
                        win("jira/TEAM/_attachments/TEAM-26/image-1.png"): "2025-06-15"}
    assert (plan.by_parent, plan.unknown) == (2, 1)


def test_вложению_страницы_ставится_дата_страницы(arch):
    paths = {"confluence/DEMO/1001__Sample-page/1002__Практики.md": "2024-11-02",
             win("sample_confluence_export/DEMO/1001__Sample-page/_attachments/1002/Стратегия.pdf"): "2026-06-16",
             "confluence/DEMO/1001__Sample-page/1003__Roles.md": ""}
    plan = F.plan(paths, arch.ready())
    assert plan.new == {win("sample_confluence_export/DEMO/1001__Sample-page/_attachments/1002/Стратегия.pdf"): "2024-11-02"}


def test_страницы_и_прочие_файлы_не_трогаются(arch):
    arch("export-c/style.md", b"text")
    paths = {"jira/TEAM/TEAM-26__Метрики.md": "2025-06-15", "onedrive/SAMPLE/отчёт.pdf": "2019-05-11",
             "product/kb/PROCESS.md": "", "export-c/style.md": "2026-06-19", "converted/long/x.doc.docx": COPY}
    plan = F.plan(paths, arch.ready())
    assert plan.new == {}


# ── перезапись таблицы ──────────────────────────────────────────
@pytest.fixture
def lance(tmp_path):
    lancedb = pytest.importorskip("lancedb")
    rows = []
    for path, day in (("export-a" + BS + "x" + BS + "a.eml", COPY), ("export-a/x/O'Brien's.eml", COPY), ("jira" + BS + "IT" + BS + "задача.md", "2025-06-15")):
        for chunk in range(3):
            rows.append({"path": path, "source": "mail", "space": "x", "title": "т", "updated": day, "url": "",
                         "chunk": chunk, "text": f"текст {path} {chunk}", "vector": [float(chunk), 1.0]})
    src = str(tmp_path / "lance")
    lancedb.connect(src).create_table("docs", rows)
    return lancedb, src, str(tmp_path / "lance.new")


def test_перезапись_меняет_только_дату_названных_путей(lance):
    lancedb, src, dst = lance
    n = F.rewrite(src, dst, {"export-a" + BS + "x" + BS + "a.eml": "2024-03-05", "export-a/x/O'Brien's.eml": "2019-04-08"}, indexes=False)
    assert n == 9
    old = lancedb.connect(src).open_table("docs").search().limit(100).to_list()
    new = lancedb.connect(dst).open_table("docs").search().limit(100).to_list()
    key = lambda r: (r["path"], r["chunk"])
    old, new = sorted(old, key=key), sorted(new, key=key)
    assert [key(r) for r in old] == [key(r) for r in new]
    for a, b in zip(old, new):
        assert (a["text"], a["source"], a["title"], list(a["vector"])) == (b["text"], b["source"], b["title"], list(b["vector"]))
    assert {r["path"]: r["updated"] for r in new} == {"export-a" + BS + "x" + BS + "a.eml": "2024-03-05",
                                                      "export-a/x/O'Brien's.eml": "2019-04-08",
                                                      "jira" + BS + "IT" + BS + "задача.md": "2025-06-15"}
    assert {r["updated"] for r in old} == {COPY, "2025-06-15"}                        # исходная таблица не изменилась


def test_перезапись_в_занятое_место_отказ(lance):
    lancedb, src, dst = lance
    os.makedirs(dst)
    open(os.path.join(dst, "чужое"), "w").close()
    with pytest.raises(F.DatesError):
        F.rewrite(src, dst, {}, indexes=False)


def test_даты_и_базы_путей_из_индекса(lance):
    lancedb, src, _ = lance
    meta = F.index_dates(lancedb.connect(src).open_table("docs"))
    assert meta == {"export-a" + BS + "x" + BS + "a.eml": COPY, "export-a/x/O'Brien's.eml": COPY, "jira" + BS + "IT" + BS + "задача.md": "2025-06-15"}

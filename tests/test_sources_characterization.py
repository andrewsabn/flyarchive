"""Таблица источников (FR-99): записанный эталон поведения. Тесты фиксации держат результат правил на выдуманных образцах.

Что держит этот файл: по этим правилам считается, в какой базе лежит документ и какие повторы убираются из индекса, поэтому результат обязан быть
один и тот же для каждого случая. Эталон записан в самом файле: у каждого случая есть входной образец и ожидаемый ответ (база, раздел, название и дата;
план чистки повторов; план починки дат; разбор пути в файл корпуса), и менять его нужно только вместе с правилом.

Образцы выдуманные: в них нет настоящих названий. Всё, что зависит от названий, — две почтовые выгрузки по имени каталога, подстроки пути для остальных,
приставка первого каталога и база «файлов с названием раздел · имя» — задаётся таблицей `WORLD`, а не зашито в код.

Мир (`World`) собирает `sources.json` из `WORLD` во временном каталоге архива, читает его модулем `sources` и подставляет значения в модули так же,
как они заполняются при загрузке: `dedupe_index.ALIAS/ROOTS/ROOT_RANK`, `fix_dates.MAIL/PAGES`, `corpus_path.ALIAS`, `search.LABELS`, `index_more.ROOTS`.
"""
import json
import os
import time
from collections import Counter

import pytest

import gatekit as K
import known as N

LONG = "я" * 250

# ── мир: выдуманные источники ───────────────────────────────────
# Сверху вниз: простой корень файлов; три почтовых корня (два правила по порядку, одно правило, ни одного); файлы с приставкой первого каталога
# и названием «раздел · имя»; два корня страниц с вложениями. Порядок корней — порядок обхода и порядок почтовых корней в чистке повторов.
WORLD = {
    "roots": {
        "plain": {"kind": "files", "source": "plain"},
        "box2": {"kind": "mail", "source": "box-other",
                 "rules": [{"contains": ["alpha.example.com"], "source": "box-alpha"},
                           {"contains": ["beta.example.org", "outlook_b", "2020_sample"], "source": "box-beta"}]},
        "box3": {"kind": "mail", "source": "box-live", "rules": [{"contains": ["gamma-team"], "source": "box-gamma"}], "rank": 1},
        "box4": {"kind": "mail", "source": "box-archive", "rank": 0},
        "docs": {"kind": "files", "source": "docs-work", "rules": [{"first_part_prefix": "_Notes", "source": "docs-notes"}], "title": "folder"},
        "wiki": {"kind": "pages", "source": "wiki", "attachments": "wiki-att"},
        "tracker": {"kind": "pages", "source": "tracker", "attachments": "tracker-att"},
    },
    "labels": {"wiki": "Wiki pages", "wiki-att": "Wiki attachments", "tracker": "Tracker tickets", "tracker-att": "Tracker attachments",
               "box-other": "Other mail", "docs-work": "Work folders"},
    "aliases": {"old_wiki_dump": "wiki", "old_tracker_dump": "tracker", "older_wiki_dump": "wiki", "old_box_dump": "box2"},
}

# ── эталон: база, раздел, название и дата документа (корень, путь от корня, текст письма) ──
META = [
    # почта: корень без правил
    ('box4', 'Mailbox_x/Inbox/2023-08-22_idm_Привет_ab.eml', 'Тема: Реальная тема\n\nтело',
     ('box-archive', 'Mailbox_x', 'Реальная тема', '2023-08-22')),
    ('box4', 'gamma-team/alpha.example.com/beta.example.org/z.eml', '', ('box-archive', 'gamma-team', 'z', '')),
    ('box4', 'Sent/2021-01-02_a_b.eml', '', ('box-archive', 'Sent', 'a_b', '2021-01-02')),
    # почта: корень с одним правилом
    ('box3', 'Box/gamma-team/Inbox/m.eml', '', ('box-gamma', 'Box', 'm', '')),
    ('box3', 'Box/GAMMA-TEAM/Inbox/m.eml', '', ('box-gamma', 'Box', 'm', '')),
    ('box3', 'Box/Inbox/m.eml', '', ('box-live', 'Box', 'm', '')),
    ('box3', 'Box/alpha.example.com/Inbox/m.eml', '', ('box-live', 'Box', 'm', '')),
    ('box3', 'Box/beta.example.org/Inbox/m.eml', '', ('box-live', 'Box', 'm', '')),
    # почта: корень с двумя правилами, по порядку
    ('box2', 'Box/alpha.example.com/Inbox/x.eml', '', ('box-alpha', 'Box', 'x', '')),
    ('box2', 'Box/ALPHA.EXAMPLE.COM/Inbox/x.eml', '', ('box-alpha', 'Box', 'x', '')),
    ('box2', 'Box/Alpha.example.com/Inbox/x.eml', '', ('box-alpha', 'Box', 'x', '')),
    ('box2', 'Box/beta.example.org/Inbox/x.eml', '', ('box-beta', 'Box', 'x', '')),
    ('box2', 'Outlook_b_in/Local/Inbox/2023-08-22_idm_Привет_ab.eml', 'Тема: Реальная тема\n\nтело',
     ('box-beta', 'Outlook_b_in', 'Реальная тема', '2023-08-22')),
    ('box2', '2020_sample/Accounts/Inbox/z.eml', '', ('box-beta', '2020_sample', 'z', '')),
    ('box2', 'Box/Inbox/x.eml', '', ('box-other', 'Box', 'x', '')),
    ('box2', 'alpha.example.com/beta.example.org/x.eml', '', ('box-alpha', 'alpha.example.com', 'x', '')),
    ('box2', 'Box/gamma-team/x.eml', '', ('box-other', 'Box', 'x', '')),
    ('box2', 'Box/x_alpha.example.company/x.eml', '', ('box-alpha', 'Box', 'x', '')),
    # название письма
    ('box2', 'Box/a.b.eml', '', ('box-other', 'Box', 'a.b', '')),
    ('box2', 'Box/2023-08-22.eml', '', ('box-other', 'Box', '2023-08-22', '2023-08-22')),
    ('box2', 'Box/2023-08-22_report_2022-01-02_x.eml', '', ('box-other', 'Box', 'report_2022-01-02_x', '2023-08-22')),
    ('box2', 'Box/x_2021-03-04_y.eml', '', ('box-other', 'Box', 'x_2021-03-04_y', '2021-03-04')),
    ('box2', 'Box/2023-08-22-x.eml', '', ('box-other', 'Box', '2023-08-22-x', '2023-08-22')),
    ('box2', 'Box/' + 'я' * 250 + '.eml', '', ('box-other', 'Box', 'я' * 200, '')),
    ('box2', 'Box/a.eml', 'От: x\nТема: Первая\nТема: Вторая\n\nтело', ('box-other', 'Box', 'Первая', '')),
    ('box2', 'Box/a.eml', 'Тема: ' + 'я' * 250, ('box-other', 'Box', 'я' * 200, '')),
    ('box2', 'Box/a.eml', 'Письмо. Тема: не в начале строки', ('box-other', 'Box', 'a', '')),
    ('box2', 'Box/a.eml', 'Тема: \nтело', ('box-other', 'Box', 'a', '')),
    ('box2', 'Box/a.eml', 'тема: строчная', ('box-other', 'Box', 'a', '')),
    ('box2', 'Box/a.eml', 'Тема: Привет  \nтело', ('box-other', 'Box', 'Привет  ', '')),
    ('box2', 'Box/a.eml', 'Тема: Привет\r\nтело', ('box-other', 'Box', 'Привет\r', '')),
    ('box2', 'Box/a.eml', 'Тема:Без пробела', ('box-other', 'Box', 'a', '')),
    ('box2', 'Box/a.eml', '', ('box-other', 'Box', 'a', '')),
    # раздел
    ('box2', 'x.eml', '', ('box-other', 'x.eml', 'x', '')),
    ('box2', 'a/b/c/d.eml', '', ('box-other', 'a', 'd', '')),
    ('box2', 'a\\b\\c.eml', '', ('box-other', 'a\\b\\c.eml', 'a\\b\\c', '')),
    # файлы: приставка первого каталога, название «раздел · имя»
    ('docs', '_Notes_tools/vpn/a.md', '', ('docs-notes', '_Notes_tools', '_Notes_tools · a.md', '')),
    ('docs', '_Notes/a.md', '', ('docs-notes', '_Notes', '_Notes · a.md', '')),
    ('docs', 'Work/d.pdf', '', ('docs-work', 'Work', 'Work · d.pdf', '')),
    ('docs', 'x_Notes/a.md', '', ('docs-work', 'x_Notes', 'x_Notes · a.md', '')),
    ('docs', '_notes/a.md', '', ('docs-work', '_notes', '_notes · a.md', '')),
    ('docs', 'a.md', '', ('docs-work', 'a.md', 'a.md · a.md', '')),
    ('docs', '_Notes.md', '', ('docs-notes', '_Notes.md', '_Notes.md · _Notes.md', '')),
    ('docs', 'Work/_attachments/5/f.pdf', '', ('docs-work', 'Work', 'Work · f.pdf', '')),
    ('docs', 'Work/2021-02-03 plan.pdf', '', ('docs-work', 'Work', 'Work · 2021-02-03 plan.pdf', '2021-02-03')),
    ('docs', 'Work/' + 'я' * 250 + '.pdf', '', ('docs-work', 'Work', 'Work · ' + 'я' * 193, '')),
    ('docs', '_Notes_' + 'ж' * 100 + '/' + 'д' * 150 + '.md', '',
     ('docs-notes', '_Notes_' + 'ж' * 100, '_Notes_' + 'ж' * 100 + ' · ' + 'д' * 90, '')),
    # файлы без правил
    ('plain', 'A/b.md', '', ('plain', 'A', 'b.md', '')),
    ('plain', 'A/_attachments/9/z.pdf', '', ('plain', 'A', '9 · z.pdf', '')),
    ('plain', '2020-05-06_note.txt', '', ('plain', '2020-05-06_note.txt', '2020-05-06_note.txt', '2020-05-06')),
    # страницы и вложения
    ('wiki', 'Space/page.md', '', ('wiki', 'Space', 'page.md', '')),
    ('wiki', 'Space/_attachments/123/f.pdf', '', ('wiki-att', 'Space', '123 · f.pdf', '')),
    ('tracker', 'IT/_attachments/IT-1/a.png', '', ('tracker-att', 'IT', 'IT-1 · a.png', '')),
    ('tracker', 'IT/IT-1__Task.md', '', ('tracker', 'IT', 'IT-1__Task.md', '')),
    ('wiki', 'Space/_attachments/f.pdf', '', ('wiki-att', 'Space', 'f.pdf · f.pdf', '')),
    ('wiki', 'x_attachments_old/a.md', '', ('wiki-att', 'x_attachments_old', 'a.md', '')),
    ('wiki', 'Space/my_attachments.md', '', ('wiki', 'Space', 'my_attachments.md', '')),
    ('wiki', 'Space/a/_attachments/1/b/c.pdf', '', ('wiki-att', 'Space', '1 · c.pdf', '')),
    ('wiki', 'Space/_attachments/1/_attachments/2/c.pdf', '', ('wiki-att', 'Space', '1 · c.pdf', '')),
    ('wiki', 'Space\\_attachments\\9\\f.pdf', '', ('wiki', 'Space\\_attachments\\9\\f.pdf', '9 · Space\\_attachments\\9\\f.pdf', '')),
    ('wiki', 'Space/Sub/_attachments/77/' + 'я' * 250 + '.pdf', '', ('wiki-att', 'Space', '77 · ' + 'я' * 195, '')),
    # принятое через входящую папку
    ('входящие', '20261004-120000/2024-03-05_записка.txt', 'текст',
     ('входящие', '20261004-120000', '2024-03-05_записка.txt', '2024-03-05')),
    ('входящие', 'b/_attachments/1/x.txt', '', ('входящие', 'b', '1 · x.txt', '')),
]

MTIME = {"mail": ('box-other', 'Box', 'nodate', '2023-06-15'), "folder": ('docs-work', 'Work', 'Work · nodate.pdf', '2022-01-31')}

# корпус, в пути которого выше корней есть каталог с _attachments: каждый документ корня страниц идёт в базу вложений
ABOVE = [
    ('wiki', 'Space/page.md', '', ('wiki-att', 'Space', 'page.md', '')),
    ('tracker', 'IT/a.md', '', ('tracker-att', 'IT', 'a.md', '')),
    ('plain', 'A/b.md', '', ('plain', 'A', 'b.md', '')),
    ('docs', 'Work/d.pdf', '', ('docs-work', 'Work', 'Work · d.pdf', '')),
    ('box2', 'Box/x.eml', '', ('box-other', 'Box', 'x', '')),
]

# корень, которого нет в таблице: база та, что назвал обход, название с ключом вложений (источник, корень, путь, эталон)
OTHER = [
    ('x-base', 'elsewhere', 'A/_attachments/7/f.pdf', ('x-base', 'A', '7 · f.pdf', '')),
    ('x-base', 'elsewhere', '2022-03-04_a.txt', ('x-base', '2022-03-04_a.txt', '2022-03-04_a.txt', '2022-03-04')),
]

# обход корпуса: (корень, путь от корня, расширение); .json в почте и «._» пропущены, карантин, чужие корни и картинки не берутся, вложения один раз
WALKED = [
    ('box2', 'Box/a.eml', '.eml'),
    ('box2', 'Box/b.txt', '.txt'),
    ('docs', 'Work/a.json', '.json'),
    ('docs', 'Work/a.md', '.md'),
    ('plain', 'A/n.txt', '.txt'),
    ('tracker', 'IT/_attachments/IT-1/a.docx', '.docx'),
    ('tracker', 'IT/t.md', '.md'),
    ('wiki', 'Space/_attachments/1/f.pdf', '.pdf'),
    ('wiki', 'Space/p.md', '.md'),
    ('входящие', 'b/x.txt', '.txt'),
]

BASES_OUTPUT = (
    'база                чанков  что это\n'
    'wiki                   120  Wiki pages\n'
    'box-other                3  Other mail\n'
    'stray                    1  \n'
)


# ── обвязка ─────────────────────────────────────────────────────
class World:
    """Мир через таблицу источников: выдуманный sources.json в каталоге архива теста и значения модулей из неё."""

    def __init__(self, tmp_path, monkeypatch, corpus_dir=("gamma-team", "alpha.example.com", "corpus")):
        import corpus_path
        import dedupe_index
        import fix_dates
        import index_more
        import inbox
        import search
        import sources
        home = tmp_path / "arch"
        home.mkdir()
        file = home / "sources.json"
        file.write_text(json.dumps(WORLD, ensure_ascii=False), encoding="utf-8")
        file.chmod(0o600)
        self.table = sources.load(env={"HOME": str(tmp_path / "user")}, home=str(home))
        self.monkeypatch = monkeypatch
        self.modules = dict(corpus_path=corpus_path, dedupe_index=dedupe_index, fix_dates=fix_dates, index_more=index_more, inbox=inbox, search=search)
        self.corpus = os.path.join(str(tmp_path), *corpus_dir)
        os.makedirs(self.corpus, exist_ok=True)
        table = self.table
        self.mail_roots = table.mail_roots()
        monkeypatch.setattr(dedupe_index, "ALIAS", dict(table.aliases))
        monkeypatch.setattr(dedupe_index, "ROOTS", table.mail_roots())
        monkeypatch.setattr(dedupe_index, "ROOT_RANK", table.ranks())
        monkeypatch.setattr(fix_dates, "MAIL", table.mail_roots())
        monkeypatch.setattr(fix_dates, "PAGES", table.pages_roots())
        monkeypatch.setattr(corpus_path, "ALIAS", dict(table.aliases))
        monkeypatch.setattr(search, "LABELS", dict(table.labels))
        self.roots_list = table.walk(self.corpus)
        monkeypatch.setattr(index_more, "ROOTS", self.roots_list)
        monkeypatch.setattr(index_more, "SOURCES", table)
        monkeypatch.setattr(inbox, "SOURCES", table)

    def _path(self, root, rel):
        return os.path.join(self.corpus, root) + "/" + rel

    def _source(self, root, path):
        """Какая запись обхода берёт этот файл: по имени корня и по каталогу файла, как в inbox._old_meta."""
        where = os.path.dirname(path)
        for source, folder, needle in self.roots_list:
            if os.path.basename(folder) != root:
                continue
            if needle:
                if needle.startswith("!"):
                    if needle[1:] in where:
                        continue
                elif needle not in where:
                    continue
            return source
        return None

    def put(self, root, rel, data=b"x", mtime=None):
        path = self._path(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        if mtime:
            stamp = time.mktime(tuple(mtime) + (12, 0, 0, 0, 0, -1))            # полдень: день не зависит от пояса
            os.utime(path, (stamp, stamp))
        return path

    # ── что мир умеет ───────────────────────────────────────────
    def meta(self, root, rel, text="", mtime=None):
        path = self.put(root, rel, mtime=mtime) if mtime else self._path(root, rel)
        source = self._source(root, path)
        assert source is not None, root
        return tuple(self.modules["index_more"].meta_for(source, os.path.join(self.corpus, root), path, text))

    def old_meta(self, root, rel):
        got = self.modules["inbox"]._old_meta(self.corpus, root + "/" + rel, self._path(root, rel))
        return None if got is None else tuple(got)

    def meta_other(self, source, root, rel):
        return tuple(self.modules["index_more"].meta_for(source, os.path.join(self.corpus, root), self._path(root, rel), ""))

    def walked(self):
        found = [(os.path.basename(root), os.path.relpath(path, root).replace(os.sep, "/"), ext)
                 for _, root, path, ext in self.modules["index_more"].work_items(set())]
        return sorted(found)

    def norm(self, path):
        return self.modules["dedupe_index"].norm(path)

    def dedupe(self, paths, db, dates=None):
        return self.modules["dedupe_index"].plan(paths, db, roots=self.mail_roots, dates=dates)

    def fix(self, index_dates, db):
        return self.modules["fix_dates"].plan(index_dates, db)

    def resolve(self, rel):
        return self.modules["corpus_path"].resolve(rel, self.corpus)

    def forms(self, rel):
        import review
        return review._index_forms(rel)

    def preview_full(self, home, path):
        import preview
        return preview.resolve(home, "corpus", path).full

    def labels(self):
        return dict(self.modules["search"].LABELS)

    def bases_output(self, counts, capsys):
        import sys
        search = self.modules["search"]
        self.monkeypatch.setattr(search, "bases", lambda: counts)
        self.monkeypatch.setattr(sys, "argv", ["search.py", "--bases"])
        search.main()
        return capsys.readouterr().out


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.fixture
def arch(world, tmp_path):
    """Корпус мира на диске и база известного по нему."""
    db = str(tmp_path / "index" / "known.sqlite")

    def put(rel, data):
        root, _, rest = rel.partition("/")
        world.put(root, rest, data)

    def ready():
        N.build(world.corpus, db)
        return db

    put.ready = ready
    return put


BS = chr(92)


def win(path):
    """Путь в том виде, как его записал первый индексатор под Windows."""
    return path.replace("/", BS)


# ── база, раздел, название и дата документа ─────────────────────
@pytest.mark.parametrize("root, rel, text, expected", META, ids=[f"{n:02d}-{case[0]}" for n, case in enumerate(META)])
def test_база_раздел_название_и_дата_документа_совпадают_с_эталоном(world, root, rel, text, expected):
    assert world.meta(root, rel, text) == expected


@pytest.mark.parametrize("root, rel, text, expected", [case for case in META if case[2] == ""], ids=[f"{n:02d}" for n in range(len([c for c in META if c[2] == ""]))])
def test_приёмка_берёт_у_старого_документа_то_же_что_основной_индексатор(world, root, rel, text, expected):
    assert world.old_meta(root, rel) == expected


def test_дата_из_времени_файла_когда_в_имени_её_нет(world):
    assert world.meta("box2", "Box/nodate.eml", "", mtime=(2023, 6, 15)) == MTIME["mail"]
    assert world.meta("docs", "Work/nodate.pdf", "", mtime=(2022, 1, 31)) == MTIME["folder"]
    assert world.meta("box2", "Box/2020-01-02_dated.eml", "", mtime=(2023, 6, 15))[3] == "2020-01-02"       # дата в имени сильнее времени файла
    assert world.meta("box2", "Box/absent.eml", "")[3] == ""                                                   # файла нет, даты в имени нет


def test_каталог_с_вложениями_выше_корней_отправляет_в_базу_вложений_каждый_документ_страниц(tmp_path, monkeypatch):
    above = World(tmp_path, monkeypatch, corpus_dir=("x_attachments_y", "corpus"))
    assert [above.meta(root, rel, text) for root, rel, text, _ in ABOVE] == [expected for *_, expected in ABOVE]


@pytest.mark.parametrize("source, root, rel, expected", OTHER, ids=[f"{n}" for n in range(len(OTHER))])
def test_корень_которого_нет_в_таблице_база_та_что_назвал_обход(world, source, root, rel, expected):
    assert world.meta_other(source, root, rel) == expected


def test_старый_документ_в_корне_которого_нет_приёмка_пропускает(world):
    assert world.old_meta("elsewhere", "a.md") is None
    assert world.old_meta("_карантин", "a.md") is None


def test_обход_берёт_каждый_файл_один_раз_и_пропускает_лишнее(world):
    for root, rel in (("box2", "Box/a.eml"), ("box2", "Box/a.json"), ("box2", "Box/b.txt"), ("docs", "Work/a.md"), ("docs", "Work/a.json"),
                      ("wiki", "Space/p.md"), ("wiki", "Space/_attachments/1/f.pdf"), ("tracker", "IT/_attachments/IT-1/a.docx"),
                      ("tracker", "IT/t.md"), ("plain", "A/n.txt"), ("plain", "A/._skip.txt"), ("plain", "A/pic.png"), ("входящие", "b/x.txt"),
                      ("входящие", "_карантин/q.txt"), ("plain", "A/_карантин/q.txt"), ("elsewhere", "z.txt")):
        world.put(root, rel)
    assert world.walked() == WALKED


# ── нормализация пути (псевдонимы) ──────────────────────────────
@pytest.mark.parametrize("given, expected", [
    ("wiki/Space/a.md", "wiki/Space/a.md"),
    ("old_wiki_dump/Space/a.md", "wiki/Space/a.md"),
    (win("old_wiki_dump/Space/a.md"), "wiki/Space/a.md"),
    ("/old_tracker_dump/IT/a.md", "tracker/IT/a.md"),
    ("///older_wiki_dump/x", "wiki/x"),
    ("old_wiki_dump", "wiki"),
    ("old_wiki_dump/", "wiki/"),
    ("Old_Wiki_Dump/a.md", "Old_Wiki_Dump/a.md"),
    ("wiki/old_wiki_dump/a.md", "wiki/old_wiki_dump/a.md"),
    ("old_wiki_dump_x/a.md", "old_wiki_dump_x/a.md"),
    ("old_box_dump" + BS + "Box" + BS + "a.eml", "box2/Box/a.eml"),
    ("", ""),
    ("/", ""),
    ("a" + BS + "b" + BS + "c", "a/b/c"),
])
def test_нормализация_пути_совпадает_с_эталоном(world, given, expected):
    assert world.norm(given) == expected


# ── чистка повторов ─────────────────────────────────────────────
def test_письмо_из_трёх_выгрузок_остаётся_из_самой_полной(world, arch):
    arch("box2/Box/Inbox/a.eml", K.letter())
    arch("box3/Box/Inbox/a.eml", K.letter(extra=K.RECEIVED))
    arch("box4/Box/Inbox/a.eml", K.letter(crlf=False))
    plan = world.dedupe(["box2/Box/Inbox/a.eml", "box3/Box/Inbox/a.eml", "box4/Box/Inbox/a.eml"], arch.ready())
    assert tuple(plan) == (["box2/Box/Inbox/a.eml", "box3/Box/Inbox/a.eml"], 2, 0, 0, 1, 0, 0)


def test_из_двух_выгрузок_остаётся_та_у_которой_место_выше(world, arch):
    arch("box2/aaa/a.eml", K.letter())
    arch("box3/zzz/a.eml", K.letter(extra=K.RECEIVED))
    assert world.dedupe(["box2/aaa/a.eml", "box3/zzz/a.eml"], arch.ready()).drop == ["box2/aaa/a.eml"]
    arch("box4/mmm/a.eml", K.letter(crlf=False))
    assert world.dedupe(["box3/zzz/a.eml", "box4/mmm/a.eml"], arch.ready()).drop == ["box3/zzz/a.eml"]


def test_самая_ранняя_дата_сильнее_места_выгрузки(world, arch):
    for root in ("box2", "box4"):
        arch(f"{root}/Box/Inbox/a.eml", K.letter(extra=root.encode() + b": 1\r\n"))
    paths = ["box2/Box/Inbox/a.eml", "box4/Box/Inbox/a.eml"]
    db = arch.ready()
    assert world.dedupe(paths, db, dates={paths[0]: "2020-01-02", paths[1]: "2024-03-05"}).drop == ["box4/Box/Inbox/a.eml"]
    assert world.dedupe(paths, db, dates={paths[0]: "2024-03-05", paths[1]: "2024-03-05"}).drop == ["box2/Box/Inbox/a.eml"]
    assert world.dedupe(paths, db, dates={paths[0]: "2020-01-02T23:59:59", paths[1]: "2020-01-02"}).drop == ["box2/Box/Inbox/a.eml"]      # сравнивается день
    assert world.dedupe(paths, db, dates={paths[0]: "2020-01-02"}).drop == ["box4/Box/Inbox/a.eml"]      # без даты — самая поздняя
    assert world.dedupe(paths, db).drop == ["box2/Box/Inbox/a.eml"]


def test_внутри_одной_выгрузки_остаётся_больший_файл(world, arch):
    arch("box2/Box/Inbox/a.eml", K.letter(body="коротко"))
    arch("box2/Box/Sent/a.eml", K.letter(body="длинно " * 400))
    plan = world.dedupe(["box2/Box/Inbox/a.eml", "box2/Box/Sent/a.eml"], arch.ready())
    assert tuple(plan) == (["box2/Box/Inbox/a.eml"], 1, 0, 0, 1, 0, 0)


def test_вложения_идут_по_содержимому_и_остаются_из_той_же_выгрузки(world, arch):
    logo = K.PNG + b"logo"
    for root in ("box2", "box3"):
        arch(f"{root}/Box/Inbox/a.eml", K.letter(extra=root.encode() + b": 1\r\n"))
        arch(f"{root}/Box/Inbox/_attachments/a/logo.png", logo)
    paths = [f"{root}/Box/Inbox/{rel}" for root in ("box2", "box3") for rel in ("a.eml", "_attachments/a/logo.png")]
    plan = world.dedupe(paths, arch.ready())
    assert tuple(plan) == (["box2/Box/Inbox/_attachments/a/logo.png", "box2/Box/Inbox/a.eml"], 1, 1, 0, 2, 0, 0)


def test_вне_почты_ничего_не_чистится_и_неизвестное_считается(world, arch):
    arch("wiki/Space/_attachments/1/f.png", K.PNG + b"x")
    arch("wiki/Space/_attachments/2/f.png", K.PNG + b"x")
    arch("docs/Work/f.png", K.PNG + b"x")
    arch("box2/Box/Inbox/a.eml", K.letter())
    paths = ["wiki/Space/_attachments/1/f.png", "wiki/Space/_attachments/2/f.png", "docs/Work/f.png", "box2/Box/Inbox/a.eml",
             "box2/Box/Inbox/gone.eml", win("old_wiki_dump/Space/_attachments/1/f.png")]
    plan = world.dedupe(paths, arch.ready())
    assert tuple(plan) == ([], 0, 0, 0, 0, 4, 1)


def test_один_файл_под_двумя_видами_пути_считается_двойной_записью(world, arch):
    arch("box2/Box/Inbox/a.eml", K.letter())
    old = "old_box_dump" + BS + "Box" + BS + "Inbox" + BS + "a.eml"
    plan = world.dedupe([old, "box2/Box/Inbox/a.eml"], arch.ready())
    assert tuple(plan) == ([old], 0, 0, 1, 1, 0, 0)


# ── починка дат ─────────────────────────────────────────────────
COPY = "2026-08-31"          # день копирования архива: такая дата стоит у файлов без даты в имени
DOC = K.ooxml("docx", "Договор поставки.")


def letter(day, mid="<a@example.org>"):
    return K.letter(mid=mid, date=f"{day} Mar 2024 14:32:00 +0500")


def test_письму_ставится_дата_из_заголовка_и_верная_не_трогается(world, arch):
    arch("box2/Box/Inbox/RE__План.eml", letter(5))
    arch("box2/Box/Inbox/2024-03-05_petrov_План_0a1b2c3d.eml", letter(5, "<b@example.org>"))
    paths = {win("box2/Box/Inbox/RE__План.eml"): COPY, "box2/Box/Inbox/2024-03-05_petrov_План_0a1b2c3d.eml": "2024-03-05"}
    plan = world.fix(paths, arch.ready())
    assert tuple(plan) == ({win("box2/Box/Inbox/RE__План.eml"): "2024-03-05"}, 1, 0, 0, 1, 0)


def test_путь_под_старым_корнем_читается_как_нынешний(world, arch):
    arch("box2/Box/Inbox/RE__План.eml", letter(5))
    plan = world.fix({win("old_box_dump/Box/Inbox/RE__План.eml"): COPY}, arch.ready())
    assert plan.new == {win("old_box_dump/Box/Inbox/RE__План.eml"): "2024-03-05"}


def test_вложению_письма_дата_письма_затем_дата_из_имени_каталога(world, arch):
    arch("box2/Box/Inbox/2024-03-06_petrov_План_0a1b2c3d.eml", letter(5))
    arch("box2/Box/Inbox/_attachments/2024-03-06_petrov_Пл_0a1b2c3d/договор.docx", DOC)
    arch("box2/Box/Inbox/_attachments/2019-04-08_V_HA___646821be/image002.png", K.PNG)
    arch("box2/Box/Inbox/_attachments/без_даты_646821bf/image003.png", K.PNG + b"x")
    paths = {"box2/Box/Inbox/_attachments/2024-03-06_petrov_Пл_0a1b2c3d/договор.docx": COPY,
             "box2/Box/Inbox/_attachments/2019-04-08_V_HA___646821be/image002.png": COPY,
             "box2/Box/Inbox/_attachments/без_даты_646821bf/image003.png": COPY}
    plan = world.fix(paths, arch.ready())
    assert tuple(plan) == ({"box2/Box/Inbox/_attachments/2024-03-06_petrov_Пл_0a1b2c3d/договор.docx": "2024-03-05",
                            "box2/Box/Inbox/_attachments/2019-04-08_V_HA___646821be/image002.png": "2019-04-08"}, 0, 1, 1, 0, 1)


def test_вложенное_письмо_и_письмо_без_даты(world, arch):
    arch("box2/Box/Inbox/2024-03-05_petrov_План_0a1b2c3d.eml", letter(5))
    arch("box2/Box/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/старое.eml", K.letter(mid="<old@example.org>", date="Mon, 07 Mar 2022 10:00:00 +0500"))
    arch("box2/Box/Inbox/nodate.eml", b"From: petrov@example.org\r\nSubject: Plan\r\nMessage-ID: <x@example.org>\r\n\r\nBody\r\n")
    paths = {"box2/Box/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/старое.eml": COPY, "box2/Box/Inbox/nodate.eml": COPY}
    plan = world.fix(paths, arch.ready())
    assert tuple(plan) == ({"box2/Box/Inbox/_attachments/2024-03-05_petrov_Пл_0a1b2c3d/старое.eml": "2022-03-07"}, 1, 0, 0, 0, 1)


def test_вложению_страницы_и_задачи_дата_страницы_в_том_числе_под_старым_корнем(world, arch):
    arch("tracker/IT/IT-1__Task.md", b"text")
    arch("wiki/Space/12__Page/12__Page.md", b"text")
    paths = {win("tracker/IT/IT-1__Task.md"): "2025-06-15",
             win("old_tracker_dump/IT/_attachments/IT-1/deck.pptx"): "2026-06-16",
             win("tracker/IT/_attachments/IT-1/image-1.png"): "2026-06-17",
             win("tracker/IT/_attachments/IT-99/orphan.png"): "2026-06-17",
             "wiki/Space/12__Page/12__Page.md": "2024-11-02",
             win("old_wiki_dump/Space/12__Page/_attachments/12/plan.pdf"): "2026-06-16",
             "wiki/Space/12__Page/_attachments/12/same.pdf": "2024-11-02",
             "wiki/Space/13__Empty/13__Empty.md": ""}
    plan = world.fix(paths, arch.ready())
    assert tuple(plan) == ({win("old_tracker_dump/IT/_attachments/IT-1/deck.pptx"): "2025-06-15", win("tracker/IT/_attachments/IT-1/image-1.png"): "2025-06-15",
                            win("old_wiki_dump/Space/12__Page/_attachments/12/plan.pdf"): "2024-11-02"}, 0, 3, 0, 1, 1)


def test_прочие_корни_починка_дат_не_трогает(world, arch):
    arch("docs/Work/a.pdf", b"x")
    arch("plain/A/b.md", b"x")
    plan = world.fix({"docs/Work/a.pdf": COPY, "plain/A/b.md": COPY, "elsewhere/a.eml": COPY, "tracker/IT/IT-1__Task.md": "2025-06-15"}, arch.ready())
    assert tuple(plan) == ({}, 0, 0, 0, 0, 0)


# ── разбор пути в файл корпуса ──────────────────────────────────
@pytest.fixture
def tree(world):
    for rel in ("wiki/Space/a.md", "old_wiki_dump/Space/a.md", "wiki/Space/b.md", "tracker/IT/c.md", "docs/x.txt", "wiki/Space/d.md"):
        root, _, rest = rel.partition("/")
        world.put(root, rest)
    return world


@pytest.mark.parametrize("given, expected", [
    ("wiki/Space/a.md", "wiki/Space/a.md"),
    ("old_wiki_dump/Space/a.md", "old_wiki_dump/Space/a.md"),                  # есть и под старым именем: первым берётся то, что назвали
    ("old_wiki_dump/Space/b.md", "wiki/Space/b.md"),
    ("older_wiki_dump/Space/b.md", "wiki/Space/b.md"),
    ("old_tracker_dump" + BS + "IT" + BS + "c.md", "tracker/IT/c.md"),
    (win("wiki/Space/d.md"), "wiki/Space/d.md"),
    ("//wiki/Space/d.md", "wiki/Space/d.md"),
    ("old_wiki_dump/Space/gone.md", None),
    ("Old_Wiki_Dump/Space/b.md", None),
    ("WIKI/Space/b.md", None),
    ("wiki/Space", None),
    ("old_wiki_dump", None),
    ("old_wiki_dump/../wiki/Space/b.md", "wiki/Space/b.md"),
    ("old_wiki_dump/../../x", None),
    ("wiki/../../x.txt", None),
    ("../wiki/Space/b.md", None),
    ("/etc/passwd", None),
    ("", None),
    (None, None),
])
def test_разбор_пути_в_файл_корпуса_совпадает_с_эталоном(tree, given, expected):
    got = tree.resolve(given)
    assert got == (None if expected is None else os.path.join(tree.corpus, *expected.split("/")))


def test_старый_корень_не_выводит_за_корпус(tree):
    with open(os.path.join(os.path.dirname(tree.corpus), "x.txt"), "w", encoding="utf-8") as f:       # рядом с корпусом, снаружи него
        f.write("секрет")
    for given in ("old_wiki_dump/../../x.txt", "older_wiki_dump/../../x.txt", "wiki/../../x.txt", "../x.txt", "old_wiki_dump/../../../x.txt"):
        assert tree.resolve(given) is None


def test_просмотр_читает_старый_корень_как_нынешний(world, tmp_path):
    home = tmp_path / "ph"
    (home / "corpus" / "wiki" / "Space").mkdir(parents=True)
    (home / "corpus" / "wiki" / "Space" / "b.md").write_text("x", encoding="utf-8")
    want = os.path.realpath(home / "corpus" / "wiki" / "Space" / "b.md")
    assert world.preview_full(str(home), "old_wiki_dump/Space/b.md") == want == world.preview_full(str(home), "wiki/Space/b.md")
    with pytest.raises(Exception) as caught:
        world.preview_full(str(home), "old_wiki_dump/Space/gone.md")
    assert caught.value.message.code == "review.no_doc"


def test_решение_владельца_ищет_документ_в_индексе_под_всеми_видами_пути(world):
    assert world.forms("wiki/Space/a.md") == ["wiki/Space/a.md", "wiki" + BS + "Space" + BS + "a.md", "old_wiki_dump/Space/a.md",
                                              "old_wiki_dump" + BS + "Space" + BS + "a.md", "older_wiki_dump/Space/a.md",
                                              "older_wiki_dump" + BS + "Space" + BS + "a.md"]
    assert world.forms("docs/x.txt") == ["docs/x.txt", "docs" + BS + "x.txt"]
    assert world.forms("wiki") == ["wiki", "old_wiki_dump", "older_wiki_dump"]


# ── подписи баз ─────────────────────────────────────────────────
def test_подписи_баз_как_заданы_и_в_том_же_порядке(world):
    assert world.labels() == WORLD["labels"] and list(world.labels()) == list(WORLD["labels"])


def test_перечень_баз_подписывает_известные_и_оставляет_пустой_подпись_неизвестной(world, capsys):
    out = world.bases_output(Counter({"wiki": 120, "box-other": 3, "stray": 1}), capsys)
    assert out == BASES_OUTPUT

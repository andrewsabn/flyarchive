"""Таблица источников (FR-99): файл sources.json в каталоге архива, модуль sources, отказы, права, пустая таблица, пример, описания для программ.

Все образцы выдуманные. Главное, что держат тесты: правила базы документа и чистки повторов лежат в таблице, а не в коде; без таблицы архив работает
с одной базой (принятое через входящую папку); файл с ошибкой, чужой или открытый на запись другим не читается, а значения из него не попадают
ни в текст отказа, ни в описания для программ; негодная таблица останавливает службу и команду кодом 2 одной строкой.
Сами эталоны поведения («до» и «после» одинаковы) — в test_sources_characterization.py.
Окружение в тестах — явный словарь `env=`: переменные той машины, где идут тесты, на результат не влияют.
"""
import ast
import json
import os
import re
import signal
import subprocess
import sys

import pytest

import foreign
import messages as M
import publication_gate as G
import sources as S
from test_sources_characterization import WORLD

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")
EXAMPLE = os.path.join(ROOT, "sources.example.json")
MODULE = os.path.join(TOOLS, "sources.py")
LEAK = "LEAKq7Zk9"                       # метка «значения»: нигде в тексте отказа и в описаниях её быть не должно
FOREIGN_NAME = foreign.NAME.lower()      # имя чужой программы: в текстах и в примере таблицы его нет
GITIGNORE = "secrets/\nlogs/\nreports/\nindex/\ncorpus/\ncache/\n__pycache__/\n"
INTAKE = "входящие"
EXAMPLE_ROOTS = frozenset(json.load(open(EXAMPLE, encoding="utf-8"))["roots"])      # названия корней из образца таблицы: в самопроверках кода их нет
SOURCES_CODES = {code for code in M.CATALOG if code.startswith("sources.")}


# ── обвязка ─────────────────────────────────────────────────────
@pytest.fixture
def home(tmp_path):
    d = tmp_path / "arch"
    d.mkdir()
    return d


@pytest.fixture
def env(tmp_path, home):
    """Явное окружение: свой HOME (его нет на диске) и свой каталог архива; больше ничего."""
    return {"HOME": str(tmp_path / "user"), "FLYARCHIVE_HOME": str(home)}


def put(folder, data=None, mode=0o600, raw=None):
    """Файл sources.json в каталоге архива: JSON из data или сырое содержимое raw (строка или байты)."""
    p = folder / "sources.json"
    if raw is None:
        raw = json.dumps(data, ensure_ascii=False)
    if isinstance(raw, bytes):
        p.write_bytes(raw)
    else:
        p.write_text(raw, encoding="utf-8")
    p.chmod(mode)
    return p


def refusal(data):
    """Отказ разбора таблицы; если отказа нет, тест красный."""
    with pytest.raises(S.SourcesError) as caught:
        S.parse(data)
    return caught.value


def code_of(error):
    return error.message.code


def where_of(error):
    return error.message.args["where"]


def load_refusal(env):
    with pytest.raises(S.SourcesError) as caught:
        S.load(env=env)
    return caught.value


def everywhere(error):
    message = error.message
    return [str(error), repr(error), json.dumps(message.to_json(), ensure_ascii=False), repr(message.args), str(message), repr(error.args)]


def root(**keys):
    return {"roots": {"alpha": {"kind": "files", "source": "alpha-docs", **keys}}}


def mail(**keys):
    return {"roots": {"alpha": {"kind": "mail", "source": "alpha-mail", **keys}}}


def pages(**keys):
    return {"roots": {"alpha": {"kind": "pages", "source": "alpha-pages", "attachments": "alpha-att", **keys}}}


def tree(path):
    """Снимок каталога: все пути и время изменения; по нему видно, что load ничего не создал и не тронул."""
    out = {}
    for base, dirs, files in os.walk(path):
        for name in dirs + files:
            full = os.path.join(base, name)
            out[os.path.relpath(full, path)] = os.stat(full, follow_symlinks=False).st_mtime_ns
    return out


@pytest.fixture
def reads(monkeypatch):
    """Чтения файла: модуль читает его через os.fdopen, каждое обращение — одно чтение."""
    calls = []
    real = os.fdopen

    def spy(fd, *args, **kwargs):
        calls.append(fd)
        return real(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", spy)
    return calls


def child(code, home=None, extra=None, user=None):
    """Дочерний процесс с явным окружением: ни одной переменной набора тестов, своё каталог архива и свой HOME."""
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "WATCH_TOOLS": TOOLS, "HOME": user or "/nonexistent-home", **(extra or {})}
    if home is not None:
        environment["FLYARCHIVE_HOME"] = str(home)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", env=environment, cwd=ROOT)


# ── разбор: что хранит таблица ──────────────────────────────────
def test_образец_разбирается_и_таблица_хранит_всё_что_в_нём_есть():
    t = S.parse(WORLD)
    assert list(t.roots) == ["plain", "box2", "box3", "box4", "docs", "wiki", "tracker"]
    box2, docs, wiki = t.roots["box2"], t.roots["docs"], t.roots["wiki"]
    assert (box2.name, box2.kind, box2.source, box2.attachments, box2.title, box2.rank) == ("box2", "mail", "box-other", None, None, None)
    assert [(r.contains, r.prefix, r.source) for r in box2.rules] == [(("alpha.example.com",), None, "box-alpha"),
                                                                       (("beta.example.org", "outlook_b", "2020_sample"), None, "box-beta")]
    assert (docs.kind, docs.source, docs.title, docs.rank) == ("files", "docs-work", "folder", None)
    assert [(r.contains, r.prefix, r.source) for r in docs.rules] == [(None, "_Notes", "docs-notes")]
    assert (wiki.kind, wiki.source, wiki.attachments, wiki.rules) == ("pages", "wiki", "wiki-att", ())
    assert (t.roots["box3"].rank, t.roots["box4"].rank, t.roots["plain"].rank) == (1, 0, None)
    assert t.labels == WORLD["labels"] and list(t.labels) == list(WORLD["labels"])
    assert t.aliases == WORLD["aliases"] and list(t.aliases) == list(WORLD["aliases"])


def test_умолчания_ключей_правил_названия_и_ранга():
    r = S.parse(root()).roots["alpha"]
    assert (r.kind, r.source, r.attachments, r.rules, r.title, r.rank) == ("files", "alpha-docs", None, (), None, None)


@pytest.mark.parametrize("data", [{}, {"roots": {}}, {"labels": {}}, {"aliases": {}}, {"roots": {}, "labels": {}, "aliases": {}}])
def test_пустой_объект_и_пустые_разделы_дают_пустую_таблицу(data):
    t = S.parse(data)
    assert (dict(t.roots), t.labels, t.aliases) == ({}, {}, {})
    assert (t.mail_roots(), t.pages_roots(), t.ranks()) == ((), (), {})


def test_таблица_неизменяема_а_выданные_словари_копии():
    t = S.parse(WORLD)
    with pytest.raises(AttributeError):
        t.labels = {}
    with pytest.raises(AttributeError):
        t.extra = 1
    t.aliases["x"] = "y"
    t.labels.clear()
    assert t.aliases == WORLD["aliases"] and t.labels == WORLD["labels"]
    with pytest.raises((AttributeError, TypeError)):
        t.roots["wiki"].source = "z"


def test_почтовые_корни_страницы_и_ранги_в_порядке_файла():
    t = S.parse(WORLD)
    assert (t.mail_roots(), t.pages_roots(), t.ranks()) == (("box2", "box3", "box4"), ("wiki", "tracker"), {"box3": 1, "box4": 0})
    reordered = {"roots": {name: WORLD["roots"][name] for name in ("box4", "tracker", "box2", "wiki", "box3")}}
    t = S.parse(reordered)
    assert (t.mail_roots(), t.pages_roots()) == (("box4", "box2", "box3"), ("tracker", "wiki"))


def test_обход_корней_вложения_страниц_раньше_самих_страниц_входящие_последними():
    t = S.parse(WORLD)
    c = os.path.join(os.sep, "arch", "corpus")
    assert t.walk(c) == [("wiki-att", c + "/wiki", "_attachments"), ("tracker-att", c + "/tracker", "_attachments"), ("plain", c + "/plain", None),
                         ("mail", c + "/box2", None), ("mail", c + "/box3", None), ("mail", c + "/box4", None), ("docs-work", c + "/docs", None),
                         ("wiki", c + "/wiki", "!_attachments"), ("tracker", c + "/tracker", "!_attachments"), (INTAKE, c + "/" + INTAKE, None)]


def test_без_таблицы_в_обходе_одни_входящие_а_встроенный_корень_ведёт_как_обычные_файлы():
    t = S.parse({})
    c = os.path.join(os.sep, "arch", "corpus")
    assert t.walk(c) == [(INTAKE, c + "/" + INTAKE, None)]
    r = t.root(INTAKE)
    assert (r.name, r.kind, r.source, r.attachments, r.rules, r.title, r.rank) == (INTAKE, "files", INTAKE, None, (), None, None)
    assert t.root("elsewhere") is None and S.parse(WORLD).root("box3").kind == "mail" and "wiki" in S.parse(WORLD).roots


def test_известный_корень_берёт_базу_по_таблице_а_названную_обходом_не_слушает():
    t = S.parse(WORLD)
    c = os.path.join(os.sep, "arch", "corpus")
    for source in ("mail", "box-other", "что-угодно", ""):
        assert t.meta_for(source, c + "/box2", c + "/box2/Box/Inbox/x.eml", "") == ("box-other", "Box", "x", "")
    assert t.meta_for("что-угодно", c + "/elsewhere", c + "/elsewhere/A/x.md", "") == ("что-угодно", "A", "x.md", "")      # корня нет в таблице: база названная
    assert t.meta_for("mail", c + "/elsewhere", c + "/elsewhere/Box/x.eml", "") == ("mail", "Box", "x.eml", "")             # и «почта» сама по себе правил не даёт


# ── разбор: отказы ──────────────────────────────────────────────
CASES = {
    # (данные, код, место)
    "лишний ключ в самом верху": ({"roots": {}, "colour": 1}, "sources.unknown_key", "colour"),
    "корни не объектом": ({"roots": []}, "sources.not_object", "roots"),
    "подписи не объектом": ({"labels": [LEAK]}, "sources.not_object", "labels"),
    "псевдонимы не объектом": ({"aliases": [LEAK]}, "sources.not_object", "aliases"),
    "корень не объектом": ({"roots": {"alpha": [LEAK]}}, "sources.not_object", "roots.alpha"),
    "корень строкой": ({"roots": {"alpha": LEAK}}, "sources.not_object", "roots.alpha"),
    "лишний ключ у корня": (root(colour=LEAK), "sources.unknown_key", "roots.alpha.colour"),
    "нет вида": ({"roots": {"alpha": {"source": LEAK}}}, "sources.missing_key", "roots.alpha.kind"),
    "негодный вид": ({"roots": {"alpha": {"kind": LEAK, "source": "s"}}}, "sources.bad_kind", "roots.alpha.kind"),
    "вид не строкой": ({"roots": {"alpha": {"kind": ["mail"], "source": "s"}}}, "sources.bad_kind", "roots.alpha.kind"),
    "вид из другого регистра": ({"roots": {"alpha": {"kind": "Mail", "source": "s"}}}, "sources.bad_kind", "roots.alpha.kind"),
    "нет базы": ({"roots": {"alpha": {"kind": "files"}}}, "sources.missing_key", "roots.alpha.source"),
    "база числом": (root(source=5), "sources.not_text", "roots.alpha.source"),
    "база пустая": (root(source=""), "sources.not_text", "roots.alpha.source"),
    "база с переводом строки": (root(source=LEAK + "\nx"), "sources.not_text", "roots.alpha.source"),
    "база с нулевым знаком": (root(source=LEAK + "\x00"), "sources.not_text", "roots.alpha.source"),
    "база с разделителем строк": (root(source=LEAK + " "), "sources.not_text", "roots.alpha.source"),
    "база слишком длинная": (root(source=LEAK * 40), "sources.not_text", "roots.alpha.source"),
    "база — встроенная база входящих": (root(source=INTAKE), "sources.reserved_base", "roots.alpha.source"),
    "вложения у почты": (mail(attachments="att"), "sources.key_not_for_kind", "roots.alpha.attachments"),
    "вложения у файлов": (root(attachments="att"), "sources.key_not_for_kind", "roots.alpha.attachments"),
    "у страниц нет базы вложений": ({"roots": {"alpha": {"kind": "pages", "source": "p"}}}, "sources.missing_key", "roots.alpha.attachments"),
    "база вложений числом": (pages(attachments=5), "sources.not_text", "roots.alpha.attachments"),
    "база вложений пустая": (pages(attachments=""), "sources.not_text", "roots.alpha.attachments"),
    "база вложений — входящие": (pages(attachments=INTAKE), "sources.reserved_base", "roots.alpha.attachments"),
    "правила не списком": (mail(rules={"contains": [LEAK]}), "sources.not_list", "roots.alpha.rules"),
    "правила строкой": (mail(rules=LEAK), "sources.not_list", "roots.alpha.rules"),
    "правила у страниц": (pages(rules=[{"contains": ["x"], "source": "s"}]), "sources.key_not_for_kind", "roots.alpha.rules"),
    "правило не объектом": (mail(rules=[LEAK]), "sources.not_object", "roots.alpha.rules.1"),
    "лишний ключ у правила": (mail(rules=[{"contains": ["x"], "source": "s", "colour": LEAK}]), "sources.unknown_key", "roots.alpha.rules.1.colour"),
    "правило без признака": (mail(rules=[{"source": "s"}]), "sources.bad_rule", "roots.alpha.rules.1"),
    "правило с двумя признаками": (mail(rules=[{"contains": ["x"], "first_part_prefix": "p", "source": "s"}]), "sources.bad_rule", "roots.alpha.rules.1"),
    "пустое правило": (mail(rules=[{}]), "sources.bad_rule", "roots.alpha.rules.1"),
    "правило без базы": (mail(rules=[{"contains": [LEAK]}]), "sources.rule_no_source", "roots.alpha.rules.1"),
    "база правила числом": (mail(rules=[{"contains": ["x"], "source": 5}]), "sources.not_text", "roots.alpha.rules.1.source"),
    "база правила с управляющим знаком": (mail(rules=[{"contains": ["x"], "source": LEAK + "\x07"}]), "sources.not_text", "roots.alpha.rules.1.source"),
    "база правила — входящие": (mail(rules=[{"contains": ["x"], "source": INTAKE}]), "sources.reserved_base", "roots.alpha.rules.1.source"),
    "второе правило негодное — в месте номер два": (mail(rules=[{"contains": ["x"], "source": "s"}, {"contains": ["y"]}]),
                                                    "sources.rule_no_source", "roots.alpha.rules.2"),
    "подстроки пустым списком": (mail(rules=[{"contains": [], "source": "s"}]), "sources.not_list", "roots.alpha.rules.1.contains"),
    "подстроки строкой": (mail(rules=[{"contains": LEAK, "source": "s"}]), "sources.not_list", "roots.alpha.rules.1.contains"),
    "подстрока числом": (mail(rules=[{"contains": ["x", 5], "source": "s"}]), "sources.not_text", "roots.alpha.rules.1.contains.2"),
    "подстрока пустая": (mail(rules=[{"contains": [""], "source": "s"}]), "sources.not_text", "roots.alpha.rules.1.contains.1"),
    "подстрока с управляющим знаком": (mail(rules=[{"contains": [LEAK.lower() + "\n"], "source": "s"}]), "sources.not_text", "roots.alpha.rules.1.contains.1"),
    "подстрока не в нижнем регистре": (mail(rules=[{"contains": [LEAK], "source": "s"}]), "sources.not_lowercase", "roots.alpha.rules.1.contains.1"),
    "подстрока с одной заглавной": (mail(rules=[{"contains": ["abc", "abC"], "source": "s"}]), "sources.not_lowercase", "roots.alpha.rules.1.contains.2"),
    "приставка у почты": (mail(rules=[{"first_part_prefix": "p", "source": "s"}]), "sources.key_not_for_kind", "roots.alpha.rules.1.first_part_prefix"),
    "подстроки у файлов": (root(rules=[{"contains": ["x"], "source": "s"}]), "sources.key_not_for_kind", "roots.alpha.rules.1.contains"),
    "приставка числом": (root(rules=[{"first_part_prefix": 5, "source": "s"}]), "sources.bad_prefix", "roots.alpha.rules.1.first_part_prefix"),
    "приставка пустая": (root(rules=[{"first_part_prefix": "", "source": "s"}]), "sources.bad_prefix", "roots.alpha.rules.1.first_part_prefix"),
    "приставка с косой чертой": (root(rules=[{"first_part_prefix": LEAK + "/x", "source": "s"}]), "sources.bad_prefix", "roots.alpha.rules.1.first_part_prefix"),
    "приставка с обратной косой": (root(rules=[{"first_part_prefix": LEAK + chr(92) + "x", "source": "s"}]), "sources.bad_prefix", "roots.alpha.rules.1.first_part_prefix"),
    "приставка с управляющим знаком": (root(rules=[{"first_part_prefix": LEAK + "\x1b", "source": "s"}]), "sources.bad_prefix", "roots.alpha.rules.1.first_part_prefix"),
    "название не из одного слова": (root(title=LEAK), "sources.bad_title", "roots.alpha.title"),
    "название из другого регистра": (root(title="Folder"), "sources.bad_title", "roots.alpha.title"),
    "название числом": (root(title=1), "sources.bad_title", "roots.alpha.title"),
    "название пустым": (root(title=""), "sources.bad_title", "roots.alpha.title"),
    "название у почты": (mail(title="folder"), "sources.key_not_for_kind", "roots.alpha.title"),
    "название у страниц": (pages(title="folder"), "sources.key_not_for_kind", "roots.alpha.title"),
    "ранг булевым": (mail(rank=True), "sources.bad_rank", "roots.alpha.rank"),
    "ранг отрицательный": (mail(rank=-1), "sources.bad_rank", "roots.alpha.rank"),
    "ранг дробный": (mail(rank=1.5), "sources.bad_rank", "roots.alpha.rank"),
    "ранг строкой": (mail(rank="1"), "sources.bad_rank", "roots.alpha.rank"),
    "ранг пустой": (mail(rank=None), "sources.bad_rank", "roots.alpha.rank"),
    "ранг слишком большой": (mail(rank=S.MAX_RANK + 1), "sources.bad_rank", "roots.alpha.rank"),
    "ранг у файлов": (root(rank=1), "sources.key_not_for_kind", "roots.alpha.rank"),
    "ранг у страниц": (pages(rank=1), "sources.key_not_for_kind", "roots.alpha.rank"),
    "имя корня пустое": ({"roots": {"": {"kind": "files", "source": "s"}}}, "sources.bad_name", "roots."),
    "имя корня — точка": ({"roots": {".": {"kind": "files", "source": "s"}}}, "sources.bad_name", "roots.."),
    "имя корня — две точки": ({"roots": {"..": {"kind": "files", "source": "s"}}}, "sources.bad_name", "roots..."),
    "имя корня с косой чертой": ({"roots": {"a/b": {"kind": "files", "source": "s"}}}, "sources.bad_name", "roots.a/b"),
    "имя корня с обратной косой": ({"roots": {"a" + chr(92) + "b": {"kind": "files", "source": "s"}}}, "sources.bad_name", "roots.a" + chr(92) + "b"),
    "имя корня абсолютным путём": ({"roots": {"/etc": {"kind": "files", "source": "s"}}}, "sources.bad_name", "roots./etc"),
    "имя корня с переводом строки": ({"roots": {"a\nb": {"kind": "files", "source": "s"}}}, "sources.bad_name", "roots.a?b"),
    "имя корня длиннее имени файла": ({"roots": {"x" * 300: {"kind": "files", "source": "s"}}}, "sources.bad_name", "roots." + "x" * 64 + "…"),
    "имя корня — встроенный корень входящих": ({"roots": {INTAKE: {"kind": "files", "source": "s"}}}, "sources.reserved_root", "roots." + INTAKE),
    "подпись не строкой": ({"labels": {"b": 5}}, "sources.not_text", "labels.b"),
    "подпись пустая": ({"labels": {"b": ""}}, "sources.not_text", "labels.b"),
    "подпись с управляющим знаком": ({"labels": {"b": LEAK + "\x00"}}, "sources.not_text", "labels.b"),
    "подпись слишком длинная": ({"labels": {"b": LEAK * 100}}, "sources.not_text", "labels.b"),
    "база подписи пустая": ({"labels": {"": "x"}}, "sources.not_text", "labels."),
    "база подписи с управляющим знаком": ({"labels": {"a\nb": "x"}}, "sources.not_text", "labels.a?b"),
    "псевдоним значением не строкой": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"old": 5}}, "sources.not_text", "aliases.old"),
    "псевдоним на два уровня вверх": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"old": ".."}}, "sources.bad_name", "aliases.old"),
    "псевдоним на текущий каталог": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"old": "."}}, "sources.bad_name", "aliases.old"),
    "псевдоним на путь с подъёмом": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"old": "../alpha"}}, "sources.bad_name", "aliases.old"),
    "псевдоним на абсолютный путь": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"old": "/etc"}}, "sources.bad_name", "aliases.old"),
    "псевдоним на путь вглубь": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"old": "alpha/sub"}}, "sources.bad_name", "aliases.old"),
    "псевдоним на пустое имя": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"old": ""}}, "sources.bad_name", "aliases.old"),
    "псевдоним на несуществующий корень": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"old": "beta"}}, "sources.alias_target", "aliases.old"),
    "псевдоним без единого корня": ({"aliases": {"old": "alpha"}}, "sources.alias_target", "aliases.old"),
    "старое имя совпадает с корнем": ({"roots": {"alpha": {"kind": "files", "source": "s"}, "beta": {"kind": "files", "source": "t"}},
                                       "aliases": {"beta": "alpha"}}, "sources.alias_clash", "aliases.beta"),
    "старое имя — встроенный корень входящих": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {INTAKE: "alpha"}}, "sources.alias_clash", "aliases." + INTAKE),
    "старое имя с косой чертой": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"a/b": "alpha"}}, "sources.bad_name", "aliases.a/b"),
    "старое имя — две точки": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"..": "alpha"}}, "sources.bad_name", "aliases..."),
    "старое имя пустое": ({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"": "alpha"}}, "sources.bad_name", "aliases."),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_ошибка_в_таблице_отказ_с_кодом_и_местом_значения_в_тексте_нет(name):
    data, code, where = CASES[name]
    error = refusal(data)
    assert code_of(error) == code and where_of(error) == where, (code_of(error), where_of(error))
    assert isinstance(error, M.CodedError) and error.message.code in M.CATALOG
    for shown in everywhere(error):
        assert LEAK not in shown and LEAK.lower() not in shown, shown


def test_каждый_код_таблицы_источников_достижим_отказом_из_разбора_или_файла():
    reached = {code for _, code, _ in CASES.values()}
    file_level = {"sources.file_open", "sources.file_unreadable", "sources.bad_json", "sources.duplicate_key"}
    assert reached | file_level == SOURCES_CODES, SOURCES_CODES ^ (reached | file_level)


@pytest.mark.parametrize("value", [[], [1, 2], "строка", 5, None, True, 1.5])
def test_верх_таблицы_не_объект_отказ_с_названием_файла(value):
    error = refusal(value)
    assert code_of(error) == "sources.not_object" and where_of(error) == "sources.json"


def test_имя_из_файла_в_отказе_называется_безопасно_управляющие_знаки_заменены_длинное_обрезано():
    error = refusal({"roots": {"a\nb\x1b[31mc": {"kind": "files", "source": "s"}}})
    assert "\n" not in str(error) and "\x1b" not in str(error) and "\n" not in where_of(error)
    error = refusal({"roots": {"alpha": {"kind": "files", "source": "s", "x" * 500: 1}}})
    assert len(where_of(error)) <= 90 and where_of(error).startswith("roots.alpha." + "x" * 60)


def test_значения_подряд_ничего_лишнего_не_ломают_правильная_таблица_с_границами_принимается():
    t = S.parse({"roots": {"x" * 255: {"kind": "mail", "source": "s" * S.MAX_TEXT, "rank": S.MAX_RANK,
                                       "rules": [{"contains": ["я" * S.MAX_TEXT], "source": "t"}]}}, "labels": {"s": "я" * S.MAX_TEXT}})
    assert t.roots["x" * 255].rank == S.MAX_RANK and t.labels == {"s": "я" * S.MAX_TEXT}
    assert S.parse({"roots": {"alpha": {"kind": "files", "source": "s"}}, "aliases": {"old": "alpha"}}).aliases == {"old": "alpha"}
    assert S.parse({"aliases": {"old": INTAKE}}).aliases == {"old": INTAKE}            # встроенный корень существует: на него псевдоним допустим


# ── файл ────────────────────────────────────────────────────────
def test_файла_нет_не_ошибка_таблица_пуста_и_каталог_не_тронут(env, home):
    before = tree(home)
    t = S.load(env=env)
    assert (dict(t.roots), t.labels, t.aliases) == ({}, {}, {}) and tree(home) == before and not (home / "sources.json").exists()


def test_файл_читается_из_каталога_архива_того_что_в_настройках(env, home, tmp_path):
    put(home, WORLD)
    assert S.load(env=env).mail_roots() == ("box2", "box3", "box4")
    other = tmp_path / "другой"
    other.mkdir()
    assert dict(S.load(env={**env, "FLYARCHIVE_HOME": str(other)}).roots) == {}
    put(other, root())
    assert list(S.load(env={**env, "FLYARCHIVE_HOME": str(other)}).roots) == ["alpha"] and list(S.load(home=str(other), env=env).roots) == ["alpha"]


def test_load_ничего_не_пишет_и_читает_файл_один_раз(env, home, reads):
    put(home, WORLD)
    before = tree(home)
    S.load(env=env)
    assert tree(home) == before and len(reads) == 1


def test_битый_json_отказ_с_местом_ошибки(env, home):
    put(home, raw='{"roots": {},\n  "labels": }')
    e = load_refusal(env)
    assert code_of(e) == "sources.bad_json" and e.message.args["line"] == 2 and e.message.args["column"] >= 1 and e.message.args["path"] == str(home / "sources.json")
    for broken in ("", "   ", "{", "[1,", "{'a': 1}", '{"a": 1,}', '{"a" 1}', "nul", "1" * 5000):
        put(home, raw=broken)
        assert code_of(load_refusal(env)) == "sources.bad_json", repr(broken)


def test_глубокая_вложенность_отказ_а_не_падение_интерпретатора(env, home):
    put(home, raw="[" * 100_000)
    assert code_of(load_refusal(env)) == "sources.bad_json"


@pytest.mark.parametrize("raw", ["[]", "[1, 2]", '"строка"', "5", "null", "true", "1.5"])
def test_файл_не_объект_отказ(env, home, raw):
    put(home, raw=raw)
    e = load_refusal(env)
    assert code_of(e) == "sources.not_object" and where_of(e) == "sources.json"


def test_ключ_повторён_в_файле_отказ_иначе_последний_молча_закрыл_бы_первый(env, home):
    put(home, raw='{"roots": {"alpha": {"kind": "files", "source": "a"}, "alpha": {"kind": "files", "source": "b"}}}')
    e = load_refusal(env)
    assert code_of(e) == "sources.duplicate_key" and where_of(e) == "alpha"
    put(home, raw='{"roots": {}, "roots": {}}')
    assert code_of(load_refusal(env)) == "sources.duplicate_key"
    put(home, raw='{"roots": {"alpha": {"kind": "files", "kind": "mail", "source": "b"}}}')
    assert code_of(load_refusal(env)) == "sources.duplicate_key"


def test_ошибка_внутри_файла_отказ_с_местом_и_без_значений(env, home):
    put(home, {"roots": {"alpha": {"kind": "files", "source": LEAK, "colour": LEAK}}})
    e = load_refusal(env)
    assert code_of(e) == "sources.unknown_key" and where_of(e) == "roots.alpha.colour" and all(LEAK not in shown for shown in everywhere(e))


def test_файл_не_в_utf8_и_файл_слишком_большой_отказ(env, home, reads):
    put(home, raw=b'{"labels": {"a": "\xff\xfe"}}')
    e = load_refusal(env)
    assert code_of(e) == "sources.file_unreadable" and e.message.args["error_type"] == "UnicodeDecodeError"
    del reads[:]
    put(home, raw='{"labels": {"a": "' + "x" * (S.MAX_FILE_BYTES + 1) + '"}}')
    e = load_refusal(env)
    assert code_of(e) == "sources.file_unreadable" and e.message.args["error_type"] == "TooLarge"
    assert reads == [], "слишком большой файл прочитан в память"


def test_файл_с_BOM_читается_как_обычный(env, home):
    put(home, raw=b"\xef\xbb\xbf" + json.dumps(WORLD).encode("utf-8"))
    assert S.load(env=env).mail_roots() == ("box2", "box3", "box4")


def test_каталог_вместо_файла_и_именованный_канал_отказ_без_зависания(env, home):
    (home / "sources.json").mkdir()
    e = load_refusal(env)
    assert code_of(e) == "sources.file_unreadable" and e.message.args["error_type"] == "NotAFile"
    (home / "sources.json").rmdir()
    os.mkfifo(home / "sources.json", 0o600)

    def hung(*_):
        raise TimeoutError("load завис на именованном канале")

    old = signal.signal(signal.SIGALRM, hung)
    signal.alarm(10)
    try:
        e = load_refusal(env)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert code_of(e) == "sources.file_unreadable" and e.message.args["error_type"] == "NotAFile"


# ── права на файл ───────────────────────────────────────────────
@pytest.mark.parametrize("mode", [0o620, 0o660, 0o602, 0o606, 0o622, 0o664, 0o666, 0o770, 0o777])
def test_файл_с_записью_для_группы_или_остальных_не_читается_отказ(env, home, reads, mode):
    put(home, WORLD, mode=mode)
    e = load_refusal(env)
    assert code_of(e) == "sources.file_open" and e.message.args["path"] == str(home / "sources.json")
    assert reads == [], "содержимое файла прочитано до проверки прав"


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644, 0o400, 0o440, 0o444, 0o604])
def test_файл_без_записи_для_других_читается(env, home, reads, mode):
    put(home, WORLD, mode=mode)
    assert S.load(env=env).mail_roots() == ("box2", "box3", "box4") and len(reads) == 1


def test_файл_чужого_владельца_не_читается_отказ(env, home, reads, monkeypatch):
    put(home, WORLD, mode=0o600)
    monkeypatch.setattr(S, "_owner_uid", lambda: os.geteuid() + 1)
    e = load_refusal(env)
    assert code_of(e) == "sources.file_open" and reads == []


def test_ссылка_на_открытый_файл_не_обходит_проверку_проверяется_сам_файл(env, home, tmp_path, reads):
    elsewhere = tmp_path / "где-то-ещё"
    elsewhere.mkdir()
    target = elsewhere / "таблица.json"
    target.write_text(json.dumps(WORLD))
    target.chmod(0o666)
    os.symlink(target, home / "sources.json")
    assert code_of(load_refusal(env)) == "sources.file_open" and reads == []
    target.chmod(0o600)                                                                    # собственные права ссылки (777) не в счёт
    assert S.load(env=env).mail_roots() == ("box2", "box3", "box4")


def test_ссылка_на_файл_чужого_владельца_не_обходит_проверку(env, home, tmp_path, monkeypatch):
    target = tmp_path / "чужой.json"
    target.write_text(json.dumps(WORLD))
    target.chmod(0o600)
    os.symlink(target, home / "sources.json")
    assert S.load(env=env).mail_roots() == ("box2", "box3", "box4")
    monkeypatch.setattr(S, "_owner_uid", lambda: os.geteuid() + 1)
    assert code_of(load_refusal(env)) == "sources.file_open"


def test_проверка_прав_только_там_где_они_есть_не_на_posix_пропускается(env, home, monkeypatch):
    put(home, WORLD, mode=0o666)
    monkeypatch.setattr(S, "POSIX", False)
    assert S.load(env=env).mail_roots() == ("box2", "box3", "box4")


# ── startup: служба и команда выходят кодом 2 одной строкой ─────
def test_startup_негодная_таблица_одна_строка_и_код_два_без_трассировки(env, home, capsys):
    put(home, {"roots": {"alpha": {"kind": "files", "source": LEAK, "colour": LEAK}}})
    with pytest.raises(SystemExit) as caught:
        S.startup(env=env)
    err = capsys.readouterr().err
    assert caught.value.code == 2 and err.startswith("таблица источников: ") and len(err.splitlines()) == 1 and "Traceback" not in err and LEAK not in err
    assert "roots.alpha.colour" in err


def test_startup_годная_таблица_и_её_отсутствие_отказа_не_дают(env, home):
    assert dict(S.startup(env=env).roots) == {}
    put(home, WORLD)
    assert S.startup(env=env).pages_roots() == ("wiki", "tracker")


def test_startup_негодные_настройки_остаются_за_настройками(env, home, capsys):
    (home / "settings.json").write_text('{"нет_такой": 1}', encoding="utf-8")
    (home / "settings.json").chmod(0o600)
    with pytest.raises(SystemExit) as caught:
        S.startup(env=env)
    assert caught.value.code == 2 and capsys.readouterr().err.startswith("настройки: ")


MODULES = ["corpus_path", "dedupe_index", "fix_dates", "search", "index_more", "inbox", "review", "preview", "office_server", "webui", "mcp_server"]
IMPORT = "import os, sys, types\nsys.path.insert(0, os.environ['WATCH_TOOLS'])\ntry:\n    import lancedb\nexcept Exception:\n    sys.modules['lancedb'] = types.ModuleType('lancedb')\nimport %s\n"


@pytest.mark.parametrize("module", MODULES)
def test_негодная_таблица_останавливает_каждый_модуль_кодом_два_одной_строкой(tmp_path, module):
    folder = tmp_path / "arch"
    folder.mkdir()
    put(folder, {"roots": {"alpha": {"kind": "files", "source": LEAK, "colour": LEAK}}})
    r = child(IMPORT % module, home=folder)
    assert r.returncode == 2 and r.stdout == "", (r.stdout, r.stderr[-300:])
    assert r.stderr.startswith("таблица источников: ") and len(r.stderr.splitlines()) == 1 and LEAK not in r.stderr


def test_негодная_таблица_останавливает_команду_кодом_два_одной_строкой(tmp_path):
    folder = tmp_path / "arch"
    folder.mkdir()
    put(folder, {"roots": {"alpha": {"kind": "files", "source": LEAK, "colour": LEAK}}})
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "HOME": "/nonexistent-home", "FLYARCHIVE_HOME": str(folder)}
    for args in (("vision", "backfill", "--limit", "1", "--dry-run"), ("inbox", "status"), ("queue", "list"), ("vision", "status")):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "flyarchive"), *args], capture_output=True, text=True, encoding="utf-8", env=environment)
        assert r.returncode == 2, (args, r.returncode, r.stderr[-300:])
        assert r.stderr.startswith("таблица источников: ") and len(r.stderr.splitlines()) == 1 and "Traceback" not in r.stderr and LEAK not in r.stderr, args


# ── значения модулей заполняются таблицей при загрузке ──────────
DUMP = r'''
import json, os, sys, types
sys.path.insert(0, os.environ["WATCH_TOOLS"])
try:
    import lancedb
except Exception:
    sys.modules["lancedb"] = types.ModuleType("lancedb")
import corpus_path, dedupe_index, fix_dates, index_more, inbox, search
corpus = os.path.join(os.environ["FLYARCHIVE_HOME"], "corpus")
print(json.dumps({
    "corpus_path.ALIAS": corpus_path.ALIAS, "dedupe_index.ALIAS": dedupe_index.ALIAS, "dedupe_index.ROOTS": list(dedupe_index.ROOTS),
    "dedupe_index.ROOT_RANK": dedupe_index.ROOT_RANK, "fix_dates.MAIL": list(fix_dates.MAIL), "fix_dates.PAGES": list(fix_dates.PAGES),
    "search.LABELS": search.LABELS, "index_more.ROOTS": [[s, os.path.relpath(r, corpus), n] for s, r, n in index_more.ROOTS],
    "types": [type(corpus_path.ALIAS).__name__, type(dedupe_index.ROOTS).__name__, type(fix_dates.MAIL).__name__, type(search.LABELS).__name__,
              type(index_more.ROOTS).__name__],
    "separate": [corpus_path.ALIAS is not dedupe_index.ALIAS],
}, ensure_ascii=False))
'''


def dump(home):
    r = child(DUMP, home=home)
    assert r.returncode == 0, r.stderr[-500:]
    return json.loads(r.stdout)


def test_модули_берут_значения_из_таблицы_при_загрузке(tmp_path):
    folder = tmp_path / "arch"
    folder.mkdir()
    put(folder, WORLD)
    got = dump(folder)
    assert got["corpus_path.ALIAS"] == got["dedupe_index.ALIAS"] == WORLD["aliases"] and list(got["dedupe_index.ALIAS"]) == list(WORLD["aliases"])
    assert (got["dedupe_index.ROOTS"], got["fix_dates.MAIL"]) == (["box2", "box3", "box4"], ["box2", "box3", "box4"])
    assert got["dedupe_index.ROOT_RANK"] == {"box3": 1, "box4": 0} and got["fix_dates.PAGES"] == ["wiki", "tracker"]
    assert got["search.LABELS"] == WORLD["labels"] and list(got["search.LABELS"]) == list(WORLD["labels"])
    assert got["index_more.ROOTS"] == [["wiki-att", "wiki", "_attachments"], ["tracker-att", "tracker", "_attachments"], ["plain", "plain", None],
                                       ["mail", "box2", None], ["mail", "box3", None], ["mail", "box4", None], ["docs-work", "docs", None],
                                       ["wiki", "wiki", "!_attachments"], ["tracker", "tracker", "!_attachments"], [INTAKE, INTAKE, None]]
    assert got["types"] == ["dict", "tuple", "tuple", "dict", "list"] and got["separate"] == [True]            # те же виды значений, что были в коде


def test_без_таблицы_модули_пусты_а_в_обходе_одни_входящие(tmp_path):
    folder = tmp_path / "arch"
    folder.mkdir()
    got = dump(folder)
    assert got["corpus_path.ALIAS"] == got["dedupe_index.ALIAS"] == got["dedupe_index.ROOT_RANK"] == got["search.LABELS"] == {}
    assert got["dedupe_index.ROOTS"] == got["fix_dates.MAIL"] == got["fix_dates.PAGES"] == []
    assert got["index_more.ROOTS"] == [[INTAKE, INTAKE, None]]


# ── пустая таблица: архив работает с одной базой ────────────────
@pytest.fixture
def blank(tmp_path, monkeypatch, home, env):
    """Модули ядра с пустой таблицей: значения модулей — из S.load без файла."""
    import corpus_path
    import dedupe_index
    import fix_dates
    import index_more
    import inbox
    import search
    t = S.load(env=env)
    for module, name, value in ((dedupe_index, "ALIAS", dict(t.aliases)), (dedupe_index, "ROOTS", t.mail_roots()), (dedupe_index, "ROOT_RANK", t.ranks()),
                                (fix_dates, "MAIL", t.mail_roots()), (fix_dates, "PAGES", t.pages_roots()), (corpus_path, "ALIAS", dict(t.aliases)),
                                (search, "LABELS", dict(t.labels)), (index_more, "ROOTS", t.walk(str(tmp_path / "corpus"))), (index_more, "SOURCES", t),
                                (inbox, "SOURCES", t)):
        monkeypatch.setattr(module, name, value)
    return t


def test_без_таблицы_чистка_повторов_ничего_не_удаляет_а_путей_вне_почты_считает_всех(blank, tmp_path):
    import dedupe_index as D
    import gatekit as K
    import known as N
    corpus = tmp_path / "corpus"
    for rel in ("box2/Box/Inbox/a.eml", "box3/Box/Inbox/a.eml", "box4/Box/Inbox/a.eml"):
        (corpus / rel).parent.mkdir(parents=True, exist_ok=True)
        (corpus / rel).write_bytes(K.letter(extra=rel.replace("/", "_").encode() + b": 1\r\n"))
    db = str(tmp_path / "index" / "known.sqlite")
    N.build(str(corpus), db)
    paths = ["box2/Box/Inbox/a.eml", "box3/Box/Inbox/a.eml", "box4/Box/Inbox/a.eml"]
    assert tuple(D.plan(paths, db)) == ([], 0, 0, 0, 0, 3, 0)           # без аргумента roots берётся значение модуля: оно пусто


def test_с_таблицей_чистка_повторов_без_аргумента_roots_берёт_почтовые_корни_таблицы(blank, tmp_path, monkeypatch):
    import dedupe_index as D
    import gatekit as K
    import known as N
    t = S.parse(WORLD)
    monkeypatch.setattr(D, "ROOTS", t.mail_roots())
    monkeypatch.setattr(D, "ROOT_RANK", t.ranks())
    corpus = tmp_path / "corpus"
    for rel in ("box2/Box/Inbox/a.eml", "box4/Box/Inbox/a.eml"):
        (corpus / rel).parent.mkdir(parents=True, exist_ok=True)
        (corpus / rel).write_bytes(K.letter(extra=rel.replace("/", "_").encode() + b": 1\r\n"))
    db = str(tmp_path / "index" / "known.sqlite")
    N.build(str(corpus), db)
    assert D.plan(["box2/Box/Inbox/a.eml", "box4/Box/Inbox/a.eml"], db).drop == ["box2/Box/Inbox/a.eml"]
    assert D.plan(["box2/Box/Inbox/a.eml", "box4/Box/Inbox/a.eml"], db, roots=()).drop == []           # явный пустой перечень — тоже ничего


def test_корень_без_ранга_стоит_после_корней_с_рангом_каким_бы_большим_тот_ни_был(tmp_path, monkeypatch):
    import dedupe_index as D
    import gatekit as K
    import known as N
    t = S.parse({"roots": {"m1": {"kind": "mail", "source": "a", "rank": 7}, "m2": {"kind": "mail", "source": "b"},
                           "m3": {"kind": "mail", "source": "c", "rank": 9}}})
    monkeypatch.setattr(D, "ROOTS", t.mail_roots())
    monkeypatch.setattr(D, "ROOT_RANK", t.ranks())
    corpus = tmp_path / "corpus"
    for rel in ("m1/x/a.eml", "m2/x/a.eml", "m3/x/a.eml"):
        (corpus / rel).parent.mkdir(parents=True, exist_ok=True)
        (corpus / rel).write_bytes(K.letter(extra=rel.replace("/", "_").encode() + b": 1\r\n"))
    db = str(tmp_path / "index" / "known.sqlite")
    N.build(str(corpus), db)
    assert D.plan(["m2/x/a.eml", "m3/x/a.eml"], db).drop == ["m2/x/a.eml"]               # у m3 ранг 9, у m2 его нет: остаётся m3
    assert D.plan(["m1/x/a.eml", "m3/x/a.eml"], db).drop == ["m3/x/a.eml"]               # меньше — предпочтительнее
    assert D.plan(["m1/x/a.eml", "m2/x/a.eml", "m3/x/a.eml"], db).drop == ["m2/x/a.eml", "m3/x/a.eml"]


def test_без_таблицы_починка_дат_разбор_пути_и_просмотр_работают_без_псевдонимов(blank, tmp_path):
    import corpus_path
    import fix_dates as F
    import known as N
    corpus = tmp_path / "corpus"
    (corpus / "wiki" / "Space").mkdir(parents=True)
    (corpus / "wiki" / "Space" / "a.md").write_text("x", encoding="utf-8")
    db = str(tmp_path / "index" / "known.sqlite")
    N.build(str(corpus), db)
    assert tuple(F.plan({"wiki/Space/a.md": "2026-08-31", "old_wiki_dump/Space/a.md": "2026-08-31"}, db)) == ({}, 0, 0, 0, 0, 0)
    assert corpus_path.resolve("wiki/Space/a.md", str(corpus)) == str(corpus / "wiki" / "Space" / "a.md")
    assert corpus_path.resolve("old_wiki_dump/Space/a.md", str(corpus)) is None


def test_без_таблицы_приёмка_разбирает_только_входящие_а_подписей_нет(blank, tmp_path, capsys, monkeypatch):
    import inbox
    import search
    corpus = str(tmp_path / "corpus")
    assert inbox._old_meta(corpus, "wiki/Space/a.md", corpus + "/wiki/Space/a.md") is None
    assert inbox._old_meta(corpus, INTAKE + "/b/_attachments/1/x.txt", corpus + "/" + INTAKE + "/b/_attachments/1/x.txt") == (INTAKE, "b", "1 · x.txt", "")
    assert search.LABELS == {}
    from collections import Counter
    monkeypatch.setattr(search, "bases", lambda: Counter({INTAKE: 4}))
    monkeypatch.setattr(sys, "argv", ["search.py", "--bases"])
    search.main()
    # подписей владельца нет, а у входящих встроенная подпись — та же, что на странице поиска
    assert capsys.readouterr().out.splitlines()[1].split() == [INTAKE, "4", *S.INTAKE_LABEL.split()]


@pytest.mark.foreign_name
def test_без_таблицы_описания_для_программ_говорят_о_принятом_через_входящую_папку():
    t = S.parse({})
    assert S.bases_text(t) == INTAKE and "входящ" in S.scope_text(t) and S.PRODUCT == "FlyArchive"
    for text in (S.scope_text(t), S.bases_text(t)):
        assert not re.search(r"\d", text) and FOREIGN_NAME not in text.lower()


# ── один разбор пути и одна реализация правил ───────────────────
def test_нормализация_пути_одна_функция_а_псевдонимы_ей_передаются():
    aliases = {"old": "new"}
    assert S.normalize("old/a", aliases) == "new/a" and S.normalize("\\old\\a", aliases) == "new/a" and S.normalize("old", aliases) == "new"
    assert S.normalize("old/a", {}) == "old/a" and S.normalize("", aliases) == "" and S.normalize("x/old/a", aliases) == "x/old/a"


def module_tree(name):
    with open(os.path.join(TOOLS, name), encoding="utf-8") as f:
        return ast.parse(f.read())


VALUES = {"index_more.py": ["ROOTS"], "dedupe_index.py": ["ALIAS", "ROOTS", "ROOT_RANK"], "fix_dates.py": ["MAIL", "PAGES"], "corpus_path.py": ["ALIAS"],
          "search.py": ["LABELS"]}


@pytest.mark.parametrize("name", sorted(VALUES))
def test_в_модуле_нет_литерального_списка_корней_псевдонимов_и_подписей_значения_приходят_из_таблицы(name):
    tree_ = module_tree(name)
    found = {}
    for node in tree_.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in VALUES[name]:
            found[node.targets[0].id] = node.value
    assert sorted(found) == sorted(VALUES[name]), name
    for key, value in found.items():
        assert not isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Constant)), f"{name}: {key} записан литералом"
        used = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
        assert used & {"sources", "SOURCES", "_SOURCES", "_TABLE", "TABLE", "table"}, f"{name}: {key} не из таблицы"


def function(name, func):
    return [n for n in ast.walk(module_tree(name)) if isinstance(n, ast.FunctionDef) and n.name == func][0]


def test_meta_for_индексатора_тонкая_обёртка_над_таблицей_а_приёмка_не_перебирает_корни_сама():
    fn = function("index_more.py", "meta_for")
    assert [a.arg for a in fn.args.args] == ["source", "root", "path", "text"] and len(fn.body) <= 2
    calls = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "meta_for" in calls
    old = function("inbox.py", "_old_meta")
    assert "old_meta" in {n.func.attr for n in ast.walk(old) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "ROOTS" not in {n.attr for n in ast.walk(old) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(old) if isinstance(n, ast.Name)}


def test_самопроверки_не_держат_названий_корней_из_таблицы():
    for name, func in (("index_more.py", "selftest"), ("webui.py", "selftest")):
        fn = function(name, func)
        subscripts = [n for n in ast.walk(fn) if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str)
                      and isinstance(n.value, ast.Attribute) and n.value.attr == "ALIAS"]
        assert subscripts == [], name
        meta_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "meta_for"]
        for call in meta_calls:
            texts = [c.value for c in ast.walk(call) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
            assert not any(t in EXAMPLE_ROOTS for t in texts) and not any("@" in t for t in texts), name


@pytest.mark.parametrize("script", ["index_more.py", "webui.py"])
def test_самопроверка_проходит_без_таблицы(tmp_path, script):
    folder = tmp_path / "arch"
    folder.mkdir()
    code = ("import os, sys, types, runpy\nsys.path.insert(0, os.environ['WATCH_TOOLS'])\ntry:\n    import lancedb\nexcept Exception:\n"
            "    sys.modules['lancedb'] = types.ModuleType('lancedb')\nsys.argv = [%r, '--selftest']\nrunpy.run_path(os.path.join(os.environ['WATCH_TOOLS'], %r), run_name='__main__')\n"
            % (script, script))
    r = child(code, home=folder)
    assert r.returncode == 0 and "selftest ok" in r.stdout, r.stderr[-500:]


# ── модуль: стандартная библиотека и каталог сообщений ──────────
def test_модуль_использует_только_стандартную_библиотеку_настройки_и_каталог_сообщений():
    with open(MODULE, encoding="utf-8") as f:
        tree_ = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree_):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"messages", "settings"}, imported


def test_отказ_таблицы_несёт_сообщение_из_каталога_и_все_коды_объявлены():
    assert issubclass(S.SourcesError, M.CodedError) and len(SOURCES_CODES) >= 15
    for code in SOURCES_CODES:
        template, params = M.CATALOG[code]
        assert re.search("[а-яё]", template, re.I) and set(params) <= {"path", "error_type", "line", "column", "where", "kind", "high"}, code


def test_исходник_модуля_проходит_ворота_публикации_ноль_находок(tmp_path):
    with open(MODULE, encoding="utf-8") as f:
        text = f.read()
    rep = workdir(tmp_path, {"sources.py": text})
    assert rep.findings == [], rep.findings


# ── пример sources.example.json ─────────────────────────────────
def workdir(tmp_path, files, words=("слово-которого-нет-ни-в-одном-файле",)):
    root_ = tmp_path / "snap"
    root_.mkdir(exist_ok=True)
    for name, text in {".gitignore": GITIGNORE, **files}.items():
        (root_ / name).write_text(text, encoding="utf-8")
    (tmp_path / "words.txt").write_text("\n".join(words) + "\n", encoding="utf-8")
    return G.scan(root=str(root_), words_path=str(tmp_path / "words.txt"))


def test_пример_лежит_в_корне_репозитория_и_это_чистый_json_без_комментариев():
    with open(EXAMPLE, encoding="utf-8") as f:
        text = f.read()

    def no_duplicates(pairs):
        names = [k for k, _ in pairs]
        assert len(names) == len(set(names)), "ключ повторён"
        return dict(pairs)

    data = json.loads(text, object_pairs_hook=no_duplicates)
    assert isinstance(data, dict) and not text.startswith("﻿") and text.endswith("\n") and not re.search(r"^\s*(//|#|/\*)", text, re.M)
    assert set(data) == {"roots", "labels", "aliases"}


def test_пример_загружается_как_файл_таблицы_и_показывает_каждый_вид_корня_и_каждый_ключ(env, home):
    with open(EXAMPLE, encoding="utf-8") as f:
        put(home, raw=f.read())
    t = S.load(env=env)
    kinds = {r.kind for r in t.roots.values()}
    assert kinds == {"mail", "pages", "files"}
    mails = [r for r in t.roots.values() if r.kind == "mail"]
    files = [r for r in t.roots.values() if r.kind == "files"]
    assert any(len(r.rules) >= 2 for r in mails) and any(r.rank is not None for r in mails) and any(r.rank is None for r in mails)
    assert any(len(rule.contains) >= 2 for r in mails for rule in r.rules) and any(not r.rules for r in mails)
    assert any(r.title == "folder" and any(rule.prefix for rule in r.rules) for r in files) and any(not r.rules and r.title is None for r in files)
    assert any(r.attachments for r in t.roots.values() if r.kind == "pages")
    assert t.labels and len(t.aliases) == 1 and INTAKE not in t.roots


@pytest.mark.foreign_name
def test_в_примере_имена_выдуманные_и_нет_ни_названий_ни_адресов_ни_путей():
    with open(EXAMPLE, encoding="utf-8") as f:
        text = f.read()
    assert FOREIGN_NAME not in text.lower() and "@" not in text and not re.search(r"(?i)(/home/|/Users/|[a-z]:\\\\)", text)
    for item in re.findall(r'"([^"]*\.[a-z]{2,})"', text):
        assert re.search(r"(^|\.)example\.(com|org|net)$", item), f"в примере адрес не из диапазона для документации: {item}"


def test_пример_проходит_ворота_публикации_ноль_находок(tmp_path):
    with open(EXAMPLE, encoding="utf-8") as f:
        text = f.read()
    rep = workdir(tmp_path, {"sources.example.json": text})
    assert rep.findings == [] and rep.files_checked == 2


def test_ворота_на_каталоге_с_примером_живые_домашний_путь_и_чужой_адрес_в_нём_находятся(tmp_path):
    with open(EXAMPLE, encoding="utf-8") as f:
        data = json.load(f)
    first = next(iter(data["roots"]))
    data["roots"][first]["rules"] = [{"first_part_prefix": "/home" + "/ivan/x", "source": "x"}]
    data["labels"]["leak"] = "see http://192.168." + "1.5:8080"
    rep = workdir(tmp_path, {"sources.example.json": json.dumps(data, indent=1) + "\n"})
    assert {f["kind"] for f in rep.findings} >= {"home_path", "ip"} and {f["file"] for f in rep.findings} == {"sources.example.json"}


# ── описания для программ строятся из таблицы ───────────────────
@pytest.mark.foreign_name
def test_перечень_для_описаний_подписи_и_базы_а_названий_корней_подстрок_приставок_и_псевдонимов_в_нём_нет():
    data = json.loads(json.dumps(WORLD))
    data["roots"] = {"leakroot": {"kind": "mail", "source": "leak-base", "rules": [{"contains": ["leaksub"], "source": "leak-second"}]},
                     "leakdocs": {"kind": "files", "source": "leak-docs", "rules": [{"first_part_prefix": "_leakpre", "source": "leak-tech"}], "title": "folder"},
                     "leakwiki": {"kind": "pages", "source": "leak-wiki", "attachments": "leak-att"}}
    data["labels"] = {"leak-base": "Labelled mail", "leak-wiki": "Labelled pages"}
    data["aliases"] = {"leakold": "leakwiki"}
    t = S.parse(data)
    scope, hint = S.scope_text(t), S.bases_text(t)
    for base in ("leak-base", "leak-second", "leak-docs", "leak-tech", "leak-wiki", "leak-att", INTAKE):
        assert base in hint.split(", "), base
    assert "Labelled mail" in scope and "Labelled pages" in scope and "входящ" in scope and "leak-base" in scope
    for text in (scope, hint):
        for hidden in ("leakroot", "leakdocs", "leaksub", "_leakpre", "leakold", "folder", "_attachments"):
            assert hidden not in text, hidden
        assert FOREIGN_NAME not in text.lower() and not re.search(r"\d+\s*(тыс|фрагмент|организац)", text)


def test_перечень_баз_в_порядке_подписей_затем_баз_корней_входящие_последними_без_повторов():
    t = S.parse(WORLD)
    hint = S.bases_text(t).split(", ")
    assert hint[:6] == list(WORLD["labels"]) and hint[-1] == INTAKE and len(hint) == len(set(hint))
    assert set(hint) == {"wiki", "wiki-att", "tracker", "tracker-att", "box-other", "docs-work", "box-alpha", "box-beta", "box-live", "box-gamma", "box-archive",
                         "docs-notes", "plain", INTAKE}


def test_название_продукта_одно():
    assert S.PRODUCT == "FlyArchive"
    with open(MODULE, encoding="utf-8") as f:
        assert f.read().count('PRODUCT = "FlyArchive"') == 1


DESCRIBE = r'''
import json, os, sys, types
sys.path.insert(0, os.environ["WATCH_TOOLS"])
try:
    import lancedb
except Exception:
    sys.modules["lancedb"] = types.ModuleType("lancedb")
import mcp_server, webui
tool = mcp_server.BY_NAME["search_archive"]
spec = webui.openapi()
get = spec["paths"]["/api/search"]["get"]
print(json.dumps({"mcp": tool["description"], "mcp_source": tool["inputSchema"]["properties"]["source"]["description"],
                  "title": spec["info"]["title"], "about": spec["info"]["description"], "summary": get["summary"],
                  "source": [p["description"] for p in get["parameters"] if p["name"] == "source"][0]}, ensure_ascii=False))
'''


def described(home):
    r = child(DESCRIBE, home=home)
    assert r.returncode == 0, r.stderr[-500:]
    return json.loads(r.stdout)


@pytest.mark.foreign_name
def test_описания_инструментов_и_поиска_строятся_из_таблицы_и_названия_продукта(tmp_path):
    folder = tmp_path / "arch"
    folder.mkdir()
    data = json.loads(json.dumps(WORLD))
    put(folder, data)
    got = described(folder)
    assert "FlyArchive" in got["mcp"] and "FlyArchive" in got["title"] and "FlyArchive" in got["about"]
    for label in WORLD["labels"].values():
        assert label in got["mcp"] and label in got["about"], label
    for base in ("wiki", "wiki-att", "box-other", INTAKE):
        assert base in got["mcp_source"] and base in got["source"], base
    for text in got.values():
        for hidden in ("box2", "box3", "alpha.example.com", "_Notes", "old_wiki_dump", "outlook_b"):
            assert hidden not in text, hidden
        assert FOREIGN_NAME not in text.lower() and not re.search(r"\d+\s*(тыс|фрагмент|организац)", text) and "четырёх" not in text


@pytest.mark.foreign_name
def test_без_таблицы_описания_говорят_о_документах_принятых_через_входящую_папку(tmp_path):
    folder = tmp_path / "arch"
    folder.mkdir()
    got = described(folder)
    for key in ("mcp", "about"):
        assert "FlyArchive" in got[key] and "входящ" in got[key], key
    assert "FlyArchive" in got["title"] and got["mcp_source"].count(INTAKE) == 1
    for text in got.values():
        for hidden in ("Jira", "Confluence", "mail-", "почт"):
            assert hidden not in text, hidden
        assert FOREIGN_NAME not in text.lower() and not re.search(r"\d+\s*(тыс|фрагмент|организац)", text)


def test_значения_из_таблицы_сверх_подписей_в_описания_не_попадают(tmp_path):
    folder = tmp_path / "arch"
    folder.mkdir()
    data = {"roots": {LEAK.lower() + "root": {"kind": "mail", "source": "plain-base", "rules": [{"contains": [LEAK.lower() + "sub"], "source": "second-base"}]},
                      "docs": {"kind": "files", "source": "docs-base", "rules": [{"first_part_prefix": LEAK + "pre", "source": "tech-base"}]}},
            "labels": {"plain-base": "Some label"}, "aliases": {LEAK.lower() + "old": LEAK.lower() + "root"}}
    put(folder, data)
    got = described(folder)
    for text in got.values():
        assert LEAK not in text and LEAK.lower() not in text
    assert "Some label" in got["mcp"] and "second-base" in got["mcp_source"] and "tech-base" in got["mcp_source"]

"""Открытая часть проходит ворота публикации (FR-106): тест держит итог, а не сами ворота.

Что держат тесты: ворота (`tests/publication_gate.py`) по файлам открытой части не дают ни одной находки видов ip, host, email, secret,
home_path, working_name, tracked_private, too_large и allowlist; вид word проверяется, когда задан список слов владельца (переменная
`FLYARCHIVE_GATE_WORDS`) и файл читается, иначе тест пропускается с причиной, а в отказе называются файл, строка и вид, но не найденный
текст. Список исключений (`tests/publication_allow.txt`): у каждой строки есть причина не короче двадцати знаков, строк «на весь файл» для
адресов и узлов нет, устаревших строк нет (находка, которую строка снимала, исчезла, а строка осталась).

Файлы открытой части — те, что лягут в снимок (`tests/openpart.py`): в самом снимке это все файлы дерева; в репозитории, из которого снимок
собирается, перечень файлов «в снимок не идёт» приходит файлом, путь к которому задаёт переменная окружения FLYARCHIVE_NOT_PUBLISHED. Документ
требований (FR-112) — открытый и проверяется как все: без исключений, ни по одной строке. Образцы в самом тесте выдуманные: тест лежит в
проверяемой части, и ни один образец не должен читаться как настоящая находка.
"""
import fnmatch
import os
import re

import pytest

import openpart
import publication_gate as G
from openpart import open_files

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

REQUIREMENTS = "docs/requirements.md"                # документ требований открытый: ворота проверяют его без исключений
NEVER = (re.compile(r"(?!x)x"), re.compile(r"(?!x)x"))      # списка слов нет: слова не ищутся
MIN_REASON = 20                                      # знаков в причине исключения
GITIGNORE = "secrets/\nlogs/\nreports/\nindex/\ncorpus/\ncache/\n__pycache__/\n"


# ── какие файлы и что находят ворота ────────────────────────────
def scan(root, files, words=NEVER, names=None):
    """(находки без исключений, находки после исключений, исключения): так же, как ворота, но по заданному списку файлов."""
    allow = G.Allow(root)
    rules = G.Rules(words, allow.hosts, names)
    raw = []
    for rel in files:
        raw.extend(G.check_file(rel, os.path.join(root, rel), rules))
    raw.extend(G.gitignore_findings(root))
    kept = [f for f in raw if allow.covers(f) is None] + allow.problems
    return raw, kept, allow


def shown(findings, with_text=True):
    """Строки для сообщения об отказе. Без текста — файл, строка и вид: так не выходят слова списка."""
    return ["%s:%d: %s%s" % (f["file"], f["line"], f["kind"], ": " + f["text"] if with_text else "")
            for f in sorted(findings, key=lambda f: (f["file"], f["line"], f["kind"], f["text"]))]


def allow_rows(root):
    """Строки файла исключений как их читают ворота: [(номер строки, образец, вид, текст, причина)]; нет частей — пустые."""
    with open(os.path.join(root, G.ALLOW_FILE), encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    rows = []
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if line and not line.startswith("#"):
            parts = [p.strip() for p in line.split("|", 3)] + [""] * 4
            rows.append((n, *parts[:4]))
    return rows


def whole_file_rows(rows):
    """Строки, снимающие адреса или узлы с файла целиком."""
    return [(n, pattern, kind) for n, pattern, kind, text, _ in rows if text == "*" and kind in ("ip", "host")]


def short_reasons(rows):
    return [(n, len(reason)) for n, _, _, _, reason in rows if len(reason) < MIN_REASON]


def stale_rows(allow, raw, with_words):
    """Строки исключений, под которые не подошла ни одна находка вне файла исключений (его строки покрывают сами себя)."""
    real = [f for f in raw if f["file"] != G.ALLOW_FILE]
    out = []
    for n, pattern, kind, text in allow.entries:
        if kind == "word" and not with_words:
            continue                                  # без списка слов судить о такой строке нечем
        if not any(f["kind"] == kind and (text == "*" or text == f["text"]) and fnmatch.fnmatchcase(f["file"], pattern) for f in real):
            out.append((n, pattern, kind))
    return out


def build(tmp_path, files):
    for name, data in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
    return str(tmp_path)


# ── сами проверки находят подсаженное и не трогают чистое ───────
PRIVATE_IP = "192.168." + "1.5"
TAILNET_NAME = "desktop.tail." + "ts" + ".net"
HOME_DIR = "/home" + "/ivanov/archive"


def test_прогон_находит_подсаженное_снимает_исключением_и_замечает_устаревшую_строку(tmp_path):
    allow = ("# образцы\n"
             "tests/a.py | ip | " + PRIVATE_IP + " | адрес другой машины в образце теста\n"
             "tests/a.py | host | " + TAILNET_NAME + " | такого имени в файле уже нет, строка устарела\n")
    root = build(tmp_path, {".gitignore": GITIGNORE, G.ALLOW_FILE: allow,
                            "tests/a.py": "x = 1\nip = '" + PRIVATE_IP + "'\n",
                            "tools/b.py": "host = '" + TAILNET_NAME + "'\np = '" + HOME_DIR + "'\n"})
    raw, kept, loaded = scan(root, ["tests/a.py", "tools/b.py"])
    assert ("tests/a.py", 2, "ip") in [(f["file"], f["line"], f["kind"]) for f in raw]
    assert [(f["file"], f["line"], f["kind"]) for f in kept if f["file"] != G.ALLOW_FILE] == [("tools/b.py", 1, "host"), ("tools/b.py", 2, "home_path")]
    assert stale_rows(loaded, raw, with_words=False) == [(3, "tests/a.py", "host")]


def test_прогон_без_находок_и_без_исключений_чист(tmp_path):
    root = build(tmp_path, {".gitignore": GITIGNORE, G.ALLOW_FILE: "# пусто\n", "tests/a.py": "ip = '203.0.113.5'\nurl = 'http://archive.example.com'\n"})
    raw, kept, loaded = scan(root, ["tests/a.py"])
    assert raw == [] and kept == [] and stale_rows(loaded, raw, with_words=False) == []


def test_проверки_списка_исключений_находят_весь_файл_для_адресов_и_короткую_причину():
    rows = [(1, "tests/a.py", "host", "*", "причина достаточной длины для строки"), (2, "tests/b.py", "ip", "*", "причина достаточной длины для строки"),
            (3, "tests/c.py", "secret", "*", "образцы base64, не ключи; секреты здесь не про адреса"), (4, "tests/d.py", "host", "node.example.com", "короткая"),
            (5, "tests/e.py", "ip", "203.0.113.5", "")]
    assert whole_file_rows(rows) == [(1, "tests/a.py", "host"), (2, "tests/b.py", "ip")]
    assert short_reasons(rows) == [(4, 8), (5, 0)]
    assert whole_file_rows([]) == [] and short_reasons([]) == []


# ── итог: открытая часть проходит ворота ────────────────────────
def test_файлов_открытой_части_достаточно_и_главные_на_месте():
    files = open_files()
    assert len(files) > 150, f"проверено слишком мало файлов ({len(files)}): список файлов не прочитан"
    assert {"README.md", "tests/publication_gate.py", "tests/test_open_part_gate.py", "docs/operations.md", "dsh-plugin/lib/index.js",
            REQUIREMENTS} <= set(files)


def test_без_перечня_исключений_открытая_часть_это_все_файлы_дерева(monkeypatch):
    monkeypatch.delenv(openpart.NOT_PUBLISHED_ENV, raising=False)
    on_disk = [rel for rel in openpart.tree_files() if os.path.isfile(os.path.join(ROOT, rel))]
    assert open_files() == on_disk and len(on_disk) > 150
    assert not [rel for rel in on_disk if rel.split("/")[0] in openpart.SKIP_DIRS]


def test_перечень_исключений_из_файла_убирает_названные_образцы_и_пропускает_комментарии(tmp_path, monkeypatch):
    listing = tmp_path / "не-идёт-в-снимок.txt"
    listing.write_text("# причина строки\n\nexamples/*\ndocs/operations.md\n   # отступ и комментарий\n", encoding="utf-8")
    monkeypatch.setenv(openpart.NOT_PUBLISHED_ENV, str(listing))
    assert openpart.not_published_patterns() == ["examples/*", "docs/operations.md"]
    files = open_files()
    assert "docs/operations.md" not in files and not [rel for rel in files if rel.startswith("examples/")]
    assert {"README.md", "docs/architecture.md", "tests/openpart.py"} <= set(files)


def test_образцы_исключений_работают_на_заданном_списке_файлов():
    names = ["README.md", "docs/operations.md", "tools/flyarchive", "нет/такого/файла.txt"]
    assert openpart.open_files(names, patterns=["docs/**"]) == ["README.md", "tools/flyarchive"], "файла нет на диске: в открытую часть он не входит"
    assert openpart.open_files(names, patterns=[]) == ["README.md", "docs/operations.md", "tools/flyarchive"]
    assert openpart.open_files(names, patterns=["*.md"]) == ["tools/flyarchive"]


def test_без_git_открытая_часть_берётся_обходом_каталога(monkeypatch):
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(openpart.subprocess, "run", no_git)
    openpart.tree_files.cache_clear()
    try:
        walked = openpart.tree_files()
    finally:
        monkeypatch.undo()
        openpart.tree_files.cache_clear()
    assert {"README.md", "tools/flyarchive", "tests/openpart.py"} <= set(walked) and not [rel for rel in walked if rel.startswith(".git/")]


def test_документ_требований_проходит_ворота_без_единого_исключения():
    """Не «после исключений», а до них: ни одна строка списка исключений документу требований не нужна и не должна его касаться."""
    assert REQUIREMENTS in open_files()
    raw, _, allow = scan(ROOT, [REQUIREMENTS])
    assert shown([f for f in raw if f["file"] == REQUIREMENTS]) == []
    assert [(n, pattern) for n, pattern, _, _ in allow.entries if fnmatch.fnmatchcase(REQUIREMENTS, pattern)] == []


def test_ворота_по_открытой_части_не_дают_находок_кроме_слов():
    _, kept, _ = scan(ROOT, open_files())
    left = [f for f in kept if f["kind"] != "word"]
    if left:
        pytest.fail("находки ворот в открытой части: %d\n%s" % (len(left), "\n".join(shown(left))))


def test_рабочие_имена_в_открытой_части_не_встречаются():
    path = G.resolve_names_path(None)
    if not path:
        pytest.skip("не задана переменная %s: рабочие имена не проверялись" % G.NAMES_ENV)
    try:
        names = G.load_names(path)
    except G.GateError:
        pytest.skip("список имён из переменной %s не читается: рабочие имена не проверялись" % G.NAMES_ENV)
    _, kept, _ = scan(ROOT, open_files(), NEVER, names)
    left = [f for f in kept if f["kind"] == "working_name"]
    if left:
        pytest.fail("рабочее имя найдено в открытой части: %d; файл:строка: вид, текст не печатается\n%s" % (len(left), "\n".join(shown(left, with_text=False))))


def test_слова_владельца_в_открытой_части_не_встречаются():
    path = os.environ.get(G.WORDS_ENV)
    if not path:
        pytest.skip("не задана переменная %s: слова владельца не проверялись" % G.WORDS_ENV)
    try:
        words, count = G.load_words(os.path.expanduser(path))
    except G.GateError:
        pytest.skip("список слов из переменной %s не читается: слова владельца не проверялись" % G.WORDS_ENV)
    _, kept, _ = scan(ROOT, open_files(), words)
    left = [f for f in kept if f["kind"] == "word"]
    if left:
        pytest.fail("слов из списка (%d записей) найдено в открытой части: %d; файл:строка: вид, текст не печатается\n%s"
                    % (count, len(left), "\n".join(shown(left, with_text=False))))


# ── список исключений ───────────────────────────────────────────
def test_в_списке_исключений_нет_строк_на_весь_файл_для_адресов_и_узлов():
    rows = allow_rows(ROOT)
    assert len(rows) > 10, "список исключений не прочитан"
    assert whole_file_rows(rows) == []


def test_у_каждой_строки_исключений_есть_причина_не_короче_двадцати_знаков():
    assert short_reasons(allow_rows(ROOT)) == []


def test_каждая_строка_исключений_чему_то_соответствует():
    """Ворота устаревших строк не ищут: строка, у которой пропала находка, тихо остаётся и однажды снимет настоящую."""
    raw, _, allow = scan(ROOT, open_files())
    assert len(allow.entries) == len(allow_rows(ROOT)) and allow.problems == [], "строки исключений приняты не все"
    assert stale_rows(allow, raw, with_words=False) == []

"""Форма поиска знает только базы этого архива (FR-110).

Список баз на странице поиска строится из таблицы источников (подписи — оттуда же) и из того, что есть в индексе (search.bases); зашитых
в код имён нет. Порядок устойчив: сначала «везде», потом базы таблицы в её порядке, потом базы, которые есть только в индексе, по алфавиту, последней —
база входящих. У базы без подписи показывается её имя. На свежем архиве — «везде» и входящие. Страница — настоящий обработчик на случайном порту,
индекс — подставной список баз либо настоящая таблица LanceDB во временном каталоге.
"""
import ast
import collections
import json
import os
import re
import sys
import urllib.parse

import pytest

import ingest
import search as S
import sources
import webui as W
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

OPTION = re.compile(r'<option( selected)? value="([^"]*)">([^<]*)</option>')
LABELS_TWO = {"roots": {"wiki": {"kind": "pages", "source": "wiki", "attachments": "wiki-att"}},
              "labels": {"wiki": "Страницы вики", "wiki-att": "Вложения вики"}}


@pytest.fixture
def page(serve, fetch, access, monkeypatch):
    """page(таблица источников, базы индекса или исключение, **запрос) -> варианты списка баз [(значение, подпись)] со страницы поиска."""
    monkeypatch.setattr(W.Handler, "guard", access.guard("search"))
    monkeypatch.setattr(W, "BASES_TTL", 0)                           # каждая страница читает индекс заново: тесты друг другу не мешают
    base = serve(W.Server, W.Handler)

    def get(table=None, indexed=(), selected="", **query):
        monkeypatch.setattr(W, "SOURCES", sources.parse(table or {}))

        def fake():
            if isinstance(indexed, BaseException):
                raise indexed
            return collections.Counter({name: 1 for name in indexed})

        monkeypatch.setattr(S, "bases", fake)
        params = {"source": selected, **query} if selected else query
        url = base + "/" + ("?" + urllib.parse.urlencode(params) if params else "")
        status, _, body = fetch(url, headers=access.bearer(access.read))
        assert status == 200, body[:300]
        text = body.decode("utf-8")
        select = re.search(r'<select name="source">(.*?)</select>', text, re.S).group(1)
        return [(value, label) for _, value, label in OPTION.findall(select)], text

    return get


# ── свежий архив ────────────────────────────────────────────────
def test_на_свежем_архиве_в_списке_только_везде_и_входящие(page):
    options, _ = page(None, S.SearchError(S.messages.make("index.no_table")))
    assert options == [("", "везде"), ("входящие", sources.INTAKE_LABEL)]


def test_на_свежем_архиве_с_пустым_индексом_то_же(page):
    options, _ = page(None, ())
    assert options == [("", "везде"), ("входящие", sources.INTAKE_LABEL)]


def test_форма_без_аргументов_и_без_индекса_тоже_только_везде_и_входящие(monkeypatch):
    monkeypatch.setattr(W, "SOURCES", sources.parse({}))
    monkeypatch.setattr(W, "BASES_TTL", 0)
    monkeypatch.setattr(S, "bases", lambda: (_ for _ in ()).throw(S.SearchError(S.messages.make("index.no_table"))))
    assert OPTION.findall(W.form()) == [(" selected", "", "везде"), ("", "входящие", sources.INTAKE_LABEL)]     # без выбора отмечено «везде»


# ── таблица источников ──────────────────────────────────────────
def test_таблица_с_двумя_базами_даёт_обе_с_подписями_из_таблицы_и_входящие_последними(page):
    options, _ = page(LABELS_TWO, ())
    assert options == [("", "везде"), ("wiki", "Страницы вики"), ("wiki-att", "Вложения вики"), ("входящие", sources.INTAKE_LABEL)]


def test_подпись_базы_входящих_из_таблицы_важнее_подписи_по_умолчанию(page):
    options, _ = page({"labels": {"входящие": "Принято от коллег"}}, ())
    assert options[-1] == ("входящие", "Принято от коллег")


def test_порядок_баз_таблицы_порядок_файла_а_не_алфавит(page):
    table = {"roots": {"z": {"kind": "files", "source": "zulu"}, "a": {"kind": "files", "source": "alpha"}}}
    options, _ = page(table, ())
    assert [v for v, _ in options] == ["", "zulu", "alpha", "входящие"]


# ── индекс ──────────────────────────────────────────────────────
def test_база_из_индекса_без_подписи_показывается_под_своим_именем(page):
    options, _ = page(LABELS_TWO, ["wiki", "zeta"])
    assert ("zeta", "zeta") in options and [v for v, _ in options] == ["", "wiki", "wiki-att", "zeta", "входящие"]


def test_базы_только_из_индекса_идут_по_алфавиту_между_таблицей_и_входящими(page):
    options, _ = page(LABELS_TWO, ["omega", "входящие", "beta", "wiki"])
    assert [v for v, _ in options] == ["", "wiki", "wiki-att", "beta", "omega", "входящие"]


def test_порядок_устойчив_от_порядка_ответа_индекса(page):
    first, _ = page(None, ["c", "a", "b"])
    second, _ = page(None, ["b", "c", "a"])
    assert first == second and [v for v, _ in first] == ["", "a", "b", "c", "входящие"]


def test_база_из_индекса_с_подписью_из_таблицы_берёт_подпись(page):
    options, _ = page({"labels": {"mail-x": "Почта X"}}, ["mail-x"])
    assert ("mail-x", "Почта X") in options and [v for v, _ in options].count("mail-x") == 1


def test_базы_которой_нет_ни_в_таблице_ни_в_индексе_на_странице_нет(page):
    options, text = page(LABELS_TWO, ["wiki"])
    names = {v for v, _ in options}
    assert not names & {"tasks", "mail-a", "tasks-att", "wiki-img", "mail"}
    assert not re.search(r"(?i)tasks|mail-a", text)


def test_база_из_строки_запроса_которой_нет_ни_в_таблице_ни_в_индексе_в_список_не_попадает(page):
    options, text = page(None, (), selected="tasks")                   # старая закладка ?source=tasks на свежем архиве
    assert [v for v, _ in options] == ["", "входящие"] and "tasks" not in text.split("<select", 1)[1].split("</select>", 1)[0]


def test_пустое_имя_базы_в_индексе_в_список_не_попадает(page):
    options, _ = page(None, ["", "alpha"])
    assert [v for v, _ in options] == ["", "alpha", "входящие"] and ("", "") not in options


def test_сбой_чтения_индекса_не_роняет_страницу_список_только_по_таблице(page):
    options, _ = page(LABELS_TWO, RuntimeError("таблица испорчена"))
    assert [v for v, _ in options] == ["", "wiki", "wiki-att", "входящие"]


def test_выбранная_база_отмечена_в_списке(page):
    options, text = page(LABELS_TWO, ["zeta"], selected="zeta")
    assert re.search(r'<option selected value="zeta">zeta</option>', text)
    assert len(re.findall(r"<option selected", text)) == 1


def test_имя_базы_и_подпись_экранируются(page):
    options, text = page({"labels": {"b<i>": 'Мой "архив" <b>'}}, ['x"><script>'])
    assert "<script>" not in text and "<i>" not in text and "<b>" not in text.split("<select", 1)[1].split("</select>", 1)[0]
    assert ('x&quot;&gt;&lt;script&gt;', 'x&quot;&gt;&lt;script&gt;') in options


# ── чтение индекса не на каждый запрос ──────────────────────────
def test_список_баз_индекса_читается_раз_в_ttl_а_не_на_каждую_страницу(monkeypatch):
    reads, now = [], [1000.0]

    def fake():
        reads.append(1)
        return collections.Counter({"alpha": 5})

    monkeypatch.setattr(S, "bases", fake)
    monkeypatch.setattr(S, "DB", "/нет/индекса-ttl")
    monkeypatch.setattr(W, "BASES_TTL", 30)
    monkeypatch.setattr(W, "_clock", lambda: now[0])
    W._seen.clear()
    assert W.index_bases() == ["alpha"] and W.index_bases() == ["alpha"] and len(reads) == 1
    now[0] += 31
    W.index_bases()
    assert len(reads) == 2
    W._seen.clear()


def test_отказ_индекса_не_запоминается_следующая_страница_спрашивает_заново(monkeypatch):
    answers = [S.SearchError(S.messages.make("index.no_table")), collections.Counter({"alpha": 1})]

    def fake():
        got = answers.pop(0)
        if isinstance(got, BaseException):
            raise got
        return got

    monkeypatch.setattr(S, "bases", fake)
    monkeypatch.setattr(S, "DB", "/нет/индекса-отказ")
    monkeypatch.setattr(W, "BASES_TTL", 30)
    W._seen.clear()
    assert W.index_bases() == [] and W.index_bases() == ["alpha"]
    W._seen.clear()


# ── настоящая таблица LanceDB ───────────────────────────────────
@pytest.fixture
def real_index(tmp_path, monkeypatch):
    lancedb = pytest.importorskip("lancedb")
    if not hasattr(lancedb, "__version__"):
        pytest.skip("нет lancedb: в тестах подставной")
    home = str(tmp_path / "архив")
    assert ingest.create_table(home, dim=4)
    table = ingest.open_table(home)
    names = ["wiki"] * 3 + ["zeta"] * 2 + ["входящие"]
    table.add([{"path": f"{b}/{n}.txt", "source": b, "space": "", "title": f"док {n}", "updated": "2026-10-01", "url": "", "chunk": 0,
                "text": f"слово{n}", "vector": [0.5, 0.5, 0.5, 0.5]} for n, b in enumerate(names)])
    monkeypatch.setattr(S, "DB", ingest.index_dir(home))
    monkeypatch.setattr(W, "BASES_TTL", 0)
    return home


def test_базы_индекса_настоящая_таблица_считает_строки_по_базам(real_index):
    assert S.bases() == collections.Counter({"wiki": 3, "zeta": 2, "входящие": 1})


def test_форма_по_настоящей_таблице_и_таблице_источников(real_index, monkeypatch):
    monkeypatch.setattr(W, "SOURCES", sources.parse({"labels": {"wiki": "Страницы вики"}}))
    got = [(v, label) for _, v, label in OPTION.findall(W.form())]
    assert got == [("", "везде"), ("wiki", "Страницы вики"), ("zeta", "zeta"), ("входящие", sources.INTAKE_LABEL)]


# ── перечень баз командой: подписи те же, что на странице ───────
def bases_table(monkeypatch, capsys, counts, labels=None):
    """Строки перечня `search.py --bases` как {база: подпись}: настоящий main, подставлены только число строк по базам и подписи."""
    monkeypatch.setattr(sys, "argv", ["search.py", "--bases"])
    monkeypatch.setattr(S, "bases", lambda: collections.Counter(counts))
    monkeypatch.setattr(S, "LABELS", dict(labels or {}))
    assert S.main() == 0
    rows = capsys.readouterr().out.splitlines()[1:]
    return {row.split()[0]: row.split(None, 2)[2].strip() if len(row.split(None, 2)) > 2 else "" for row in rows}


def test_перечень_баз_подписывает_входящие_как_страница_поиска(monkeypatch, capsys):
    got = bases_table(monkeypatch, capsys, {"входящие": 3})
    assert got == {"входящие": sources.INTAKE_LABEL}, got


def test_подпись_владельца_у_входящих_главнее_встроенной_и_в_перечне_и_на_странице(monkeypatch, capsys):
    table = sources.parse({"labels": {"входящие": "Принятое мной"}})
    assert bases_table(monkeypatch, capsys, {"входящие": 1}, table.labels) == {"входящие": "Принятое мной"}
    assert dict(W.base_choices(table, ())).get("входящие") == "Принятое мной"


def test_у_прочих_баз_перечень_прежний_подпись_из_таблицы_или_пусто(monkeypatch, capsys):
    got = bases_table(monkeypatch, capsys, {"wiki": 3, "zeta": 2, "входящие": 1}, {"wiki": "Страницы вики"})
    assert got == {"wiki": "Страницы вики", "zeta": "", "входящие": sources.INTAKE_LABEL}


def test_каждая_база_перечня_с_подписью_подписана_так_же_как_в_списке_страницы(monkeypatch, capsys):
    table = sources.parse({"labels": {"wiki": "Страницы вики"}})
    on_page = dict(W.base_choices(table, ["wiki", "zeta"]))
    listed = bases_table(monkeypatch, capsys, {"wiki": 1, "zeta": 1, "входящие": 1}, table.labels)
    for base, label in listed.items():
        if label:
            assert on_page[base] == label, base
    assert listed["входящие"] == on_page["входящие"]


def test_перечень_настоящим_процессом_на_свежем_архиве_с_принятым_документом_называет_входящие(tmp_path, vectors):
    from archivekit import Archive
    pytest.importorskip("lancedb")
    archive = Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})
    assert archive("init")[0] == 0
    archive.table().add([{"path": "входящие/п/а.txt", "source": "входящие", "space": "", "title": "а", "updated": "2026-10-01", "url": "",
                          "chunk": 0, "text": "слово", "vector": [0.5] * ingest.DIM}])
    code, out, err = archive.tool("search.py", "--bases")
    assert code == 0 and err == "", err
    assert [line for line in out.splitlines() if line.startswith("входящие")][0].rstrip().endswith(sources.INTAKE_LABEL), out


# ── зашитых имён нет ────────────────────────────────────────────
# Имена баз из образца таблицы источников и условные имена из тестов формы: в коде страницы поиска их быть не должно.
EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sources.example.json")
with open(EXAMPLE, encoding="utf-8") as _f:
    _EXAMPLE = json.load(_f)
SAMPLE_BASES = ({"tasks", "mail-a", "tasks-att", "wiki-img", "mail"} | set(_EXAMPLE["roots"]) | set(_EXAMPLE["labels"])
                | {root["source"] for root in _EXAMPLE["roots"].values()})
SAMPLE_LABELS = ("Tasks", "Wiki pages", "вложения Tasks") + tuple(_EXAMPLE["labels"].values())


def test_в_исходнике_webui_нет_зашитых_имён_баз_и_подписей():
    with open(W.__file__, encoding="utf-8") as f:
        source = f.read()
    strings = [node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    for old in sorted(SAMPLE_BASES) + list(SAMPLE_LABELS):
        assert old not in strings, f"в webui.py зашито имя базы или подпись: {old!r}"
    assert not re.search(r"(?i)\btasks\b|mail-a|mail-b|mail-c", re.sub(r'"""(.|\n)*?"""', "", source)), "в коде webui.py остались имена баз из образцов"
    assert not any(isinstance(value, str) and value == sources.INTAKE for value in strings), "база входящих берётся из sources.INTAKE, а не литералом"

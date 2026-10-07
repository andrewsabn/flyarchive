"""Что поиск принимает на вход: дата, кавычки, сегодняшний день.

Исправлено по итогам ревью: дата подставлялась в условие отбора как есть; запрос в кавычках ронял поиск;
«сегодня» для веса свежести было зашито.
"""
import time

import pytest

import search as S


class Query:
    def __init__(self, rows, log, kind):
        self.rows, self.log, self.kind = rows, log, kind

    def where(self, flt):
        self.log.append((self.kind, "where", flt))
        return self

    def nprobes(self, n):
        return self

    def refine_factor(self, n):
        return self

    def limit(self, n):
        return self

    def to_list(self):
        return list(self.rows)


class Table:
    def __init__(self, vec, fts):
        self.vec, self.fts, self.log = vec, fts, []

    def search(self, q, vector_column_name=None, query_type=None):
        kind = "fts" if query_type == "fts" else "vec"
        self.log.append((kind, "search", q))
        return Query(self.fts if kind == "fts" else self.vec, self.log, kind)


class DB:
    def __init__(self, table):
        self.table = table

    def open_table(self, name):
        return self.table


def row(path, updated="2026-08-31"):
    return {"path": path, "chunk": 0, "updated": updated, "title": path, "source": "jira", "space": "IT", "text": "текст " + path}


@pytest.fixture
def table(monkeypatch, tmp_path):
    def make(vec=(), fts=()):
        t = Table(list(vec), list(fts))
        t.embedded = []
        monkeypatch.setattr(S, "DB", str(tmp_path))                  # каталог индекса есть: без него поиск отказывает до обращения к библиотеке (FR-107)
        monkeypatch.setattr(S.lancedb, "connect", lambda path: DB(t))
        monkeypatch.setattr(S, "embed", lambda text: t.embedded.append(text) or [0.0] * 4)
        return t

    return make


# ── дата ────────────────────────────────────────────────────────
@pytest.mark.parametrize("since", ["2030-01-01' OR updated >= '1", "x' AND regexp_match(path, 'secr') AND '1'='1", "вчера",
                                   "2026-8-1", "20260801", "2026-08-31T00:00", "2026-13", "2026-00-10", "2026-02-32", "2026-",
                                   "2026-08-", "'", "2026 OR 1=1", "２０２６"])
def test_негодная_дата_отклоняется_до_обращения_к_таблице_и_к_модели_векторов(table, since):
    t = table(vec=[row("a")])
    with pytest.raises(ValueError) as e:
        S.search("запрос", since=since, today=20260831)
    assert "since" in str(e.value) and "2026-08-31" in str(e.value)       # в отказе — как писать дату правильно
    assert t.log == [] and t.embedded == []


@pytest.mark.parametrize("since, clean", [("2026", "2026"), ("2026-08", "2026-08"), ("2026-08-31", "2026-08-31"),
                                          (" 2026-08 ", "2026-08"), ("1999-12-01", "1999-12-01")])
def test_дата_годом_месяцем_или_днём_идёт_в_условие(table, since, clean):
    t = table(vec=[row("a")])
    S.search("запрос", since=since, today=20260831)
    assert [x for x in t.log if x[1] == "where"] == [("vec", "where", f"updated >= '{clean}'"), ("fts", "where", f"updated >= '{clean}'")]


@pytest.mark.parametrize("since", [None, "", "   "])
def test_без_даты_условия_нет(table, since):
    t = table(vec=[row("a")])
    S.search("запрос", since=since, today=20260831)
    assert [x for x in t.log if x[1] == "where"] == []


# ── кавычки ──────────────────────────────────────────────
@pytest.mark.parametrize("query, words", [('"кредитный лимит"', "кредитный лимит"), ('кредитный "лимит" договор', "кредитный лимит договор"),
                                          ('"a" "b"', "a b"), ('  "лимит', "лимит"), ("кредитный лимит", "кредитный лимит")])
def test_кавычки_снимаются_перед_поиском_по_словам(table, query, words):
    """Индекс слов собран без позиций: запрос-фразу в кавычках он выполнить не может и отвечает ошибкой."""
    t = table(vec=[row("a")], fts=[row("b")])
    out = S.search(query, today=20260831)
    assert ("fts", "search", words) in t.log and t.embedded == [query]    # смыслу кавычки не мешают: вектор — от исходного запроса
    assert [r["path"] for _, r in out] == ["a", "b"]


@pytest.mark.parametrize("query", ['""', '"', ' " " ', '"" ""'])
def test_запрос_из_одних_кавычек_ищется_только_по_смыслу(table, query):
    t = table(vec=[row("a")], fts=[row("b")])
    out = S.search(query, today=20260831)
    assert [x for x in t.log if x[0] == "fts"] == [] and [r["path"] for _, r in out] == ["a"]


# ── сегодня ───────────────────────────────────────────────
def test_сегодня_берётся_из_календаря(table, monkeypatch):
    table(vec=[row("a", updated="2026-08-31")])
    monkeypatch.setattr(S, "_today", lambda: 20280221)              # 540 дней спустя: свежесть упала вдвое
    (score, _), = S.search("запрос")
    assert score == pytest.approx((1 / 60) * (0.55 + 0.45 * 0.5))


def test_сегодня_это_сегодня():
    before = int(time.strftime("%Y%m%d"))
    assert before <= S._today() <= int(time.strftime("%Y%m%d"))


def test_названный_день_сильнее_календаря(table, monkeypatch):
    table(vec=[row("a", updated="2026-08-31")])
    monkeypatch.setattr(S, "_today", lambda: 20280221)
    (score, _), = S.search("запрос", today=20260831)
    assert score == pytest.approx(1 / 60)

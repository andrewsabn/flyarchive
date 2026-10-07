"""Фиксация нынешнего поведения ранжирования в search.py.

Эти тесты описывают, как поиск считает выдачу сейчас. Они должны быть зелёными
до любых правок и оставаться зелёными после правок, не меняющих ранжирование.
"""
import pytest

import search as S


class FakeQuery:
    def __init__(self, rows, log, kind):
        self.rows, self.log, self.kind = rows, log, kind

    def where(self, flt):
        self.log.append((self.kind, "where", flt))
        return self

    def nprobes(self, n):
        self.log.append((self.kind, "nprobes", n))
        return self

    def refine_factor(self, n):
        self.log.append((self.kind, "refine_factor", n))
        return self

    def limit(self, n):
        self.log.append((self.kind, "limit", n))
        return self

    def to_list(self):
        return list(self.rows)


class FakeTable:
    def __init__(self, vec, fts):
        self.vec, self.fts, self.log = vec, fts, []

    def search(self, q, vector_column_name=None, query_type=None):
        kind = "fts" if query_type == "fts" else "vec"
        return FakeQuery(self.fts if kind == "fts" else self.vec, self.log, kind)


class FakeDB:
    def __init__(self, table):
        self.table = table

    def open_table(self, name):
        assert name == "docs"
        return self.table


@pytest.fixture
def table(monkeypatch, tmp_path):
    def make(vec=(), fts=()):
        t = FakeTable(list(vec), list(fts))
        monkeypatch.setattr(S, "DB", str(tmp_path))                  # каталог индекса есть: без него поиск отказывает до обращения к библиотеке (FR-107)
        monkeypatch.setattr(S.lancedb, "connect", lambda path: FakeDB(t))
        monkeypatch.setattr(S, "embed", lambda text: [0.0, 0.0, 0.0, 0.0])
        return t

    return make


def row(path, chunk=0, text=None, updated="2026-08-31"):
    return {"path": path, "chunk": chunk, "updated": updated, "title": path,
            "source": "jira", "space": "IT",
            "text": f"текст {path} {chunk}" if text is None else text}


TODAY = 20260831


# ── возраст и свежесть ──────────────────────────────────────────
@pytest.mark.parametrize("updated, days", [
    ("2026-08-31", 0),
    ("2025-08-31", 365),
    ("2026-07-31", 30),
    ("2026-08-01T10:00:00", 30),      # время после даты не мешает
    ("2027-01-01", 0),                # дата из будущего не даёт отрицательный возраст
])
def test_возраст_в_днях(updated, days):
    assert S.days_old(updated, TODAY) == days


@pytest.mark.parametrize("updated", [None, "", "без даты", "31.08.2026"])
def test_без_даты_документ_считается_десятилетним(updated):
    assert S.days_old(updated, TODAY) == 3650


def test_свежесть_падает_вдвое_за_период_полураспада():
    assert S.HALF_LIFE_DAYS == 540
    assert S.recency("2026-08-31", TODAY) == 1.0
    # 540 дней назад по грубому счёту модуля: 1 год и 6 месяцев минус 5 дней
    assert S.days_old("2025-03-06", TODAY) == 540
    assert S.recency("2025-03-06", TODAY) == pytest.approx(0.5)


# ── слияние рангов ──────────────────────────────────────────────
def test_документ_из_обоих_списков_обгоняет_найденный_только_векторами(table):
    a, b = row("a"), row("b")
    table(vec=[a, b], fts=[b])
    out = S.search("запрос", k=10, today=TODAY)
    assert [r["path"] for _, r in out] == ["b", "a"]
    assert out[0][0] == pytest.approx(1.0 / 61 + 0.8 / 60)
    assert out[1][0] == pytest.approx(1.0 / 60)


def test_вес_полнотекстового_списка_ноль_восемь(table):
    table(vec=[], fts=[row("a")])
    (score, r), = S.search("запрос", today=TODAY)
    assert r["path"] == "a"
    assert score == pytest.approx(0.8 / 60)


def test_пустые_списки_дают_пустую_выдачу(table):
    table()
    assert S.search("запрос", today=TODAY) == []


def test_без_даты_множитель_чуть_выше_ноль_пятьдесят_пять(table):
    table(vec=[row("a", updated="")])
    (score, _), = S.search("запрос", today=TODAY)
    factor = 0.55 + 0.45 * 0.5 ** (3650 / 540)
    assert score == pytest.approx((1.0 / 60) * factor)
    assert 0.55 < factor < 0.56


def test_граница_свежести_первый_без_даты_уступает_свежим_до_сорок_восьмого_ранга(table):
    """Первый по векторам документ без даты проигрывает свежим с рангов 1-48
    и обгоняет свежий с ранга 49. Свежесть весит очень много — это зафиксировано."""
    rows = [row("старый", updated="")] + [row(f"свежий{i}") for i in range(1, 51)]
    table(vec=rows)
    out = S.search("запрос", k=60, today=TODAY)
    paths = [r["path"] for _, r in out]
    assert paths.index("старый") == 48
    assert paths[47] == "свежий48" and paths[49] == "свежий49"


# ── схлопывание повторов ────────────────────────────────────────
def test_одинаковый_текст_из_разных_файлов_остаётся_один_раз(table):
    table(vec=[row("ящик1/письмо", text="одно и то же"),
               row("ящик2/письмо", text="одно и то же"),
               row("другое", text="иное")])
    out = S.search("запрос", today=TODAY)
    assert [r["path"] for _, r in out] == ["ящик1/письмо", "другое"]


def test_повтор_определяется_по_первым_четырёмстам_символам(table):
    head = "о" * 400
    table(vec=[row("a", text=head + " хвост один"), row("b", text=head + " хвост два")])
    assert len(S.search("запрос", today=TODAY)) == 1


def test_различие_внутри_первых_четырёхсот_символов_повтором_не_считается(table):
    head = "о" * 399
    table(vec=[row("a", text=head + "а"), row("b", text=head + "б")])
    assert len(S.search("запрос", today=TODAY)) == 2


def test_пустые_тексты_повторами_не_считаются(table):
    table(vec=[row("a", text=""), row("b", text="   ")])
    assert len(S.search("запрос", today=TODAY)) == 2


def test_число_результатов_ограничено_k(table):
    table(vec=[row(f"d{i}") for i in range(5)])
    assert len(S.search("запрос", k=2, today=TODAY)) == 2


# ── фильтры ─────────────────────────────────────────────────────
def wheres(t):
    return [(kind, arg) for kind, op, arg in t.log if op == "where"]


def test_без_фильтров_условие_не_ставится(table):
    t = table(vec=[row("a")])
    S.search("запрос", today=TODAY)
    assert wheres(t) == []


def test_одна_база(table):
    t = table()
    S.search("запрос", source="jira", today=TODAY)
    assert wheres(t) == [("vec", "source = 'jira'"), ("fts", "source = 'jira'")]


def test_несколько_баз_через_запятую(table):
    t = table()
    S.search("запрос", source="jira, confluence", today=TODAY)
    assert wheres(t)[0] == ("vec", "(source = 'jira' OR source = 'confluence')")


def test_кавычка_в_имени_базы_вырезается(table):
    t = table()
    S.search("запрос", source="ji'ra", today=TODAY)
    assert wheres(t)[0] == ("vec", "source = 'jira'")


def test_база_из_одних_запятых_условия_не_даёт(table):
    t = table()
    S.search("запрос", source=" , ", today=TODAY)
    assert wheres(t) == []


def test_все_фильтры_вместе_идут_в_порядке_дата_база_раздел(table):
    t = table()
    S.search("запрос", since="2025", source="jira", space="DEMO", today=TODAY)
    assert wheres(t)[0] == ("vec", "updated >= '2025' AND source = 'jira' AND space = 'DEMO'")


# ── параметры обращения к индексу ───────────────────────────────
def test_расширенный_просмотр_разделов_только_у_векторного_поиска(table):
    t = table(vec=[row("a")], fts=[row("a")])
    S.search("запрос", today=TODAY)
    # 400 разделов и уточнение 30: первые десять настоящих ближайших находятся в 93% случаев вместо 63% (замер 2026-10-04)
    assert ("vec", "nprobes", 400) in t.log
    assert ("vec", "refine_factor", 30) in t.log
    assert (S.NPROBES, S.REFINE) == (400, 30)
    assert not [e for e in t.log if e[0] == "fts" and e[1] in ("nprobes", "refine_factor")]


def test_из_каждого_списка_берётся_по_сто_двадцать_кандидатов(table):
    t = table()
    S.search("запрос", today=TODAY)
    assert ("vec", "limit", 120) in t.log and ("fts", "limit", 120) in t.log

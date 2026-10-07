"""Мера расстояния одна у запроса и у приближённого индекса (FR-109).

Опыт на LanceDB 0.38: запрос без указания меры считает евклидовым расстоянием (L2) пока приближённого индекса нет, а когда индекс построен — мерой индекса
(у нас косинусной): `flyarchive index optimize` менял меру запроса молча. На векторах единичной длины (служба векторов отдаёт такие) порядок у мер один,
на ненормированных — разный; поиск берёт из расстояния только порядок (баллы считаются по месту в выдаче, самого значения расстояния он не читает), поэтому
до явной меры баллы не менялись, а порядок мог. Теперь мера — одна константа `ingest.METRIC`: её читают запрос (`search.py`), построение индекса
(`index_optimize.py`) и пересборка таблицы с датами (`fix_dates.py`). Настоящая LanceDB на малой таблице, порог малой таблицы занижен, как в
tests/test_index_optimize.py; служба векторов — подставная функция (настоящая не трогается).
"""
import random

import pytest

import fix_dates
import index_optimize as O
import ingest
import search as S

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

ROWS, DIM = 300, 8                      # PQ-индексу нужно не меньше 256 строк для обучения
TODAY = 20261007
NOWHERE = "нигде-не-встречается"        # запрос по словам ничего не находит: в выдаче остаётся чистый порядок по вектору


def make_vectors(seed=5):
    """Векторы разной длины: у ненормированных L2 и косинус упорядочивают по-разному."""
    rng = random.Random(seed)
    out = []
    for _ in range(ROWS):
        scale = rng.uniform(0.2, 6.0)
        out.append([rng.gauss(0, 1) * scale for _ in range(DIM)])
    return out


def cosine_order(vectors, query):
    def distance(v):
        dot = sum(a * b for a, b in zip(v, query))
        return 1 - dot / ((sum(a * a for a in v) ** 0.5) * (sum(a * a for a in query) ** 0.5))

    return sorted(range(ROWS), key=lambda n: distance(vectors[n]))


def l2_order(vectors, query):
    return sorted(range(ROWS), key=lambda n: sum((a - b) ** 2 for a, b in zip(vectors[n], query)))


@pytest.fixture
def table_home(tmp_path, monkeypatch):
    """Малая таблица docs в каталоге архива: (каталог, векторы по номеру строки). Поиск смотрит на неё, служба векторов отвечает заданным вектором."""
    monkeypatch.setattr(ingest, "DIM", DIM)
    home = str(tmp_path / "архив")
    assert ingest.create_table(home, dim=DIM)
    vectors = make_vectors()
    ingest.open_table(home).add([{"path": f"входящие/пачка/{n}.txt", "source": "входящие", "space": "пачка", "title": f"карточка {n}", "updated": "2026-10-01",
                                  "url": "", "chunk": 0, "text": f"учёт карточка{n}", "vector": vectors[n]} for n in range(ROWS)])
    monkeypatch.setattr(S, "DB", ingest.index_dir(home))
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    return home, vectors


def ask(monkeypatch, query):
    monkeypatch.setattr(S, "embed", lambda text: list(query))
    return [(r["path"], round(score, 9)) for score, r in S.search(NOWHERE, k=10, today=TODAY)]


def paths_of(order, count=10):
    return [f"входящие/пачка/{n}.txt" for n in order[:count]]


PROBES = [make_vectors(seed)[0] for seed in (11, 12, 13)]       # три запроса, не совпадающие ни с одной строкой


# ── константа ───────────────────────────────────────────────────
def test_мера_задана_одной_константой_косинусной():
    assert ingest.METRIC == "cosine"


class FakeTable:
    """Таблица, которая только записывает построение индексов: пересборка с датами строит их по размерности 1024, на малой таблице теста она не годится."""

    def __init__(self):
        self.configs = []

    def count_rows(self):
        return 5

    def create_index(self, column, config=None, replace=False):
        self.configs.append(config.distance_type)

    def create_fts_index(self, column, replace=False):
        pass


def test_запрос_и_оба_построения_индекса_читают_одну_константу(table_home, monkeypatch):
    home, vectors = table_home
    from lancedb.query import LanceVectorQueryBuilder
    from lancedb.table import LanceTable
    asked, built = [], []
    real_type, real_index = LanceVectorQueryBuilder.distance_type, LanceTable.create_index
    monkeypatch.setattr(LanceVectorQueryBuilder, "distance_type", lambda self, value: (asked.append(value), real_type(self, value))[1])
    monkeypatch.setattr(LanceTable, "create_index", lambda self, *a, **kw: (built.append(kw["config"].distance_type), real_index(self, *a, **kw))[1])
    monkeypatch.setattr(ingest, "METRIC", "dot")                      # другое значение: читать его должны все три места
    ask(monkeypatch, PROBES[0])
    O.run(home, say=lambda message: None)
    fake = FakeTable()
    fix_dates.build_indexes(fake, log=lambda text: None)
    assert asked == ["dot"] and built == ["dot"] and fake.configs == ["dot"], (asked, built, fake.configs)


# ── выдача не зависит от того, построен ли индекс ───────────────
@pytest.mark.parametrize("probe", PROBES, ids=["запрос-1", "запрос-2", "запрос-3"])
def test_порядок_и_баллы_находок_те_же_до_и_после_построения_приближённого_индекса(table_home, monkeypatch, probe):
    home, _ = table_home
    before = ask(monkeypatch, probe)
    result = O.run(home, say=lambda message: None)
    assert result["ann"] == "built"
    assert ask(monkeypatch, probe) == before and len(before) == 10


@pytest.mark.parametrize("probe", PROBES, ids=["запрос-1", "запрос-2", "запрос-3"])
def test_порядок_по_вектору_косинусный_а_не_евклидов_и_без_индекса_и_с_ним(table_home, monkeypatch, probe):
    home, vectors = table_home
    want = paths_of(cosine_order(vectors, probe))
    assert [p for p, _ in ask(monkeypatch, probe)] == want, "без индекса"
    O.run(home, say=lambda message: None)
    assert [p for p, _ in ask(monkeypatch, probe)] == want, "с индексом"
    assert paths_of(l2_order(vectors, probe)) != want, "образец различает меры: иначе тест ничего не проверял бы"


def test_баллы_не_зависят_от_значения_расстояния_только_от_места_в_выдаче(table_home, monkeypatch):
    home, vectors = table_home
    got = ask(monkeypatch, PROBES[0])
    first, second = got[0][1], got[1][1]
    assert first == round((1.0 / 60) * (0.55 + 0.45 * S.recency("2026-10-01", TODAY)), 9)
    assert second == round((1.0 / 61) * (0.55 + 0.45 * S.recency("2026-10-01", TODAY)), 9)
    with open(S.__file__, encoding="utf-8") as f:
        assert "_distance" not in f.read(), "поиск значение расстояния не читает: меру менять можно, не трогая баллы"

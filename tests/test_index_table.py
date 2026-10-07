"""Таблицы индекса нет: она не открывается и не создаётся молча, замечание называет причину и команду (FR-103).

Создаёт таблицу только `ingest.create_table` (его зовёт `flyarchive init`); проход, принятие из очереди и удаление документа — никогда.
Различаются «таблицы нет» (каталога индекса нет, он пуст или в нём другие таблицы) и «индекс не открылся по другой причине» (замечание прежнее).
Настоящая LanceDB.
"""
import os
import stat

import pytest

import inbox as B
import ingest as G
import review as R
from test_inbox import CLEAN, env, said, written  # noqa: F401 — разбор настоящим проходом на временном корпусе

lancedb = pytest.importorskip("lancedb")
pa = pytest.importorskip("pyarrow")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "flyarchive"
    folder.mkdir()
    return str(folder)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ── open_table и create_table ───────────────────────────────────
def test_каталога_индекса_нет_открытие_отказывает_особым_отказом_и_ничего_не_создаёт(home):
    with pytest.raises(G.TableMissing) as e:
        G.open_table(home)
    assert isinstance(e.value, ValueError)                                            # как и прежний отказ библиотеки: прежние перехватчики работают
    assert (e.value.message.code, e.value.message.args) == ("index.no_table", {})
    assert str(e.value) == "таблицы индекса нет: выполни flyarchive init"
    assert os.listdir(home) == []                                                     # каталог индекса не появился даже пустым


def test_каталог_индекса_пуст_таблицы_нет_и_после_попытки_он_пуст(home):
    folder = os.path.join(home, "index", "lance")
    os.makedirs(folder)
    with pytest.raises(G.TableMissing):
        G.open_table(home)
    assert os.listdir(folder) == []


def test_в_каталоге_индекса_только_чужая_таблица_docs_нет_и_её_не_заводят(home):
    folder = os.path.join(home, "index", "lance")
    lancedb.connect(folder).create_table("other", schema=pa.schema([("a", pa.string())]))
    with pytest.raises(G.TableMissing):
        G.open_table(home)
    assert sorted(os.listdir(folder)) == ["other.lance"]


def test_испорченная_таблица_docs_не_значит_что_таблицы_нет(home):
    os.makedirs(os.path.join(home, "index", "lance", "docs.lance"))                   # каталог таблицы есть, самой таблицы в нём нет
    with pytest.raises(Exception) as e:
        G.open_table(home)
    assert not isinstance(e.value, G.TableMissing)


def test_на_месте_каталога_индекса_файл_это_не_таблицы_нет(home):
    os.makedirs(os.path.join(home, "index"))
    with open(os.path.join(home, "index", "lance"), "w", encoding="utf-8") as f:
        f.write("не каталог")
    with pytest.raises(Exception) as e:
        G.open_table(home)
    assert not isinstance(e.value, G.TableMissing)


def test_create_table_заводит_таблицу_со_схемой_и_полнотекстовым_индексом_с_правами_владельца(home):
    assert G.create_table(home) is True
    table = G.open_table(home)
    assert table.count_rows() == 0 and table.schema == G.index_schema()
    assert [(i.index_type, list(i.columns)) for i in table.list_indices()] == [("FTS", ["text"])]
    assert mode(os.path.join(home, "index")) == 0o700 and mode(os.path.join(home, "index", "lance")) == 0o700


def test_create_table_размерность_из_настройки_или_явная(home):
    assert G.create_table(home, dim=9) is True
    assert G.open_table(home).schema.field("vector").type == pa.list_(pa.float32(), 9)


def test_create_table_готовую_таблицу_не_трогает_и_не_перезаписывает(home):
    G.create_table(home)
    table = G.open_table(home)
    table.add([{"path": "входящие/п/а.txt", "source": "входящие", "space": "п", "title": "а", "updated": "2026-10-06", "url": "", "chunk": 0,
                "text": "договор", "vector": [0.5] * G.DIM}])
    version = table.version
    assert G.create_table(home) is False and G.create_table(home, dim=5) is False
    again = G.open_table(home)
    assert again.version == version and again.count_rows() == 1 and again.schema.field("vector").type == pa.list_(pa.float32(), G.DIM)


# ── проход без таблицы ──────────────────────────────────────────
def settled_without_table(env):
    env.table = None                                                                  # настоящий open_table на временном архиве, таблицы в нём нет
    env.put("договор.txt", CLEAN)
    return env.settle()


def test_таблицы_нет_документ_принят_замечание_называет_причину_и_команду_долг_остаётся(env):
    s = settled_without_table(env)
    rel = f"входящие/{s.batch}/договор.txt"
    assert s.counts == {"accept": 1}
    (p,) = s.problems
    said(p, "problem.index_no_table", {"rel": rel},
         f"индекс: {rel} лежит в корпусе, но таблицы индекса нет — документ остаётся в долгах индексации. Выполни: flyarchive init")
    written(env, s)
    assert env.receipts(s)["договор.txt"]["indexed"] is False and env.left() == []                    # источник убран: копия цела в корпусе
    assert os.path.isfile(os.path.join(env.corpus, "входящие", s.batch, "договор.txt"))
    assert [d["rel"] for d in B._read_pending(env.home)] == [rel]
    assert not os.path.exists(os.path.join(env.home, "index", "lance"))              # проход таблицу не создал


def test_таблицы_нет_следующий_проход_замечание_то_же_долг_не_теряется(env):
    s = settled_without_table(env)
    rel = f"входящие/{s.batch}/договор.txt"
    env.now += 60
    again = env.run()
    assert again.batch is None
    (p,) = again.problems
    assert p.code == "problem.index_no_table" and p.args == {"rel": rel} and "flyarchive init" in p
    assert [d["rel"] for d in B._read_pending(env.home)] == [rel] and not os.path.exists(os.path.join(env.home, "index", "lance"))


def test_после_создания_таблицы_следующий_проход_отдаёт_долг_и_замечаний_нет(env):
    s = settled_without_table(env)
    rel = f"входящие/{s.batch}/договор.txt"
    assert G.create_table(env.home) is True
    env.now += 60
    again = env.run()
    assert again.problems == [] and B._read_pending(env.home) == []
    table = G.open_table(env.home)
    assert sorted({r["path"] for r in table.search().limit(100).to_list()}) == [rel]


@pytest.mark.parametrize("error", [OSError("нет доступа"), ValueError("не та таблица"), RuntimeError("сбой"),
                                   FileExistsError("файл вместо каталога")])
def test_индекс_не_открылся_по_другой_причине_замечание_прежнее_без_init(env, monkeypatch, error):
    def broken(home):
        raise error

    monkeypatch.setattr(B.ingest, "open_table", broken)
    s = settled_without_table(env)
    rel = f"входящие/{s.batch}/договор.txt"
    (p,) = s.problems
    said(p, "problem.index_failed", {"rel": rel, "why": f"индекс не открылся: {type(error).__name__}"},
         f"индекс: {rel} лежит в корпусе, но не проиндексирован (индекс не открылся: {type(error).__name__}); будет повтор")
    assert "init" not in p and [d["rel"] for d in B._read_pending(env.home)] == [rel]
    env.now += 60
    (q,) = env.run().problems
    said(q, "problem.index_still", {"rel": rel, "why": f"индекс не открылся: {type(error).__name__}"},
         f"индекс: {rel} всё ещё не проиндексирован (индекс не открылся: {type(error).__name__})")


# ── удаление документа без таблицы ──────────────────────────────
def test_удаление_документа_без_таблицы_индекса_отказ_документ_не_тронут_таблица_не_создана(home):
    folder = os.path.join(home, "corpus", "входящие", "п")
    os.makedirs(folder)
    with open(os.path.join(folder, "а.txt"), "w", encoding="utf-8") as f:
        f.write("текст")
    with pytest.raises(R.ReviewError) as e:
        R.doc_delete(home, "входящие/п/а.txt")
    assert e.value.message.code == "review.index_unavailable" and e.value.message.args == {"error_type": "TableMissing"}
    assert os.path.isfile(os.path.join(folder, "а.txt")) and not os.path.exists(os.path.join(home, "index"))

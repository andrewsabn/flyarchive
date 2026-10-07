"""Обслуживание индекса есть в открытой части: `flyarchive index optimize` (FR-109).

Команда строит полнотекстовый индекс заново и приближённый индекс по векторам поверх таблицы и, по явному ключу --compact, уплотняет таблицу и убирает её
старые версии. Тесты идут на настоящей LanceDB, на малых таблицах (сотни строк, размерность векторов 8–48): поиск по словам и по вектору до и после
возвращает те же документы. Порог малой таблицы в тестах снижается: настоящий — 20 тысяч строк. Видеокарта и служба векторов не трогаются:
построение «на ускорителе» проверяется по аргументам вызова подставного метода, служба векторов команде не нужна. Каталог архива — временный.
"""
import json
import os
import random

import pytest

import inbox
import index_optimize as O
import ingest
import messages as M
import settings
from archivekit import Archive

lancedb = pytest.importorskip("lancedb")
pa = pytest.importorskip("pyarrow")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

ROWS = 300                              # PQ-индексу нужно не меньше 256 строк для обучения
WORDS = ["альфа", "бета", "гамма", "дельта", "эпсилон", "дзета", "эта", "тета", "йота", "каппа"]


def unit(rng, dim):
    """Вектор единичной длины: у такого косинусное и евклидово расстояние упорядочивают одинаково."""
    v = [rng.gauss(0, 1) for _ in range(dim)]
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


def rows_of(rng, dim, start, count):
    return [{"path": f"входящие/пачка/{n}.txt", "source": "входящие", "space": "пачка", "title": f"карточка {n}", "updated": "2026-10-01",
             "url": "", "chunk": 0, "text": f"{WORDS[n % len(WORDS)]} учёт карточка{n}", "vector": unit(rng, dim)}
            for n in range(start, start + count)]


@pytest.fixture
def make(tmp_path, monkeypatch):
    """Таблица во временном каталоге: make(dim=8, rows=300, fts=True) -> (каталог архива, таблица, векторы по номеру строки)."""
    def build(dim=8, rows=ROWS, fts=True, batches=3):
        monkeypatch.setattr(ingest, "DIM", dim)                      # размерность — настройка embed_dim: ingest.DIM берётся из неё
        home = str(tmp_path / f"архив-{dim}-{rows}-{fts}")
        rng = random.Random(dim * 1000 + rows)
        if fts:
            assert ingest.create_table(home, dim=dim)
        else:                                                         # таблица без полнотекстового индекса: так её заводили до FR-103
            os.makedirs(ingest.index_dir(home))
            lancedb.connect(ingest.index_dir(home)).create_table(ingest.TABLE, schema=ingest.index_schema(dim))
        table = ingest.open_table(home)
        vectors, step = {}, rows // batches
        for b in range(batches):                                      # несколько записей — несколько фрагментов и версий таблицы
            first = b * step
            chunk = rows_of(rng, dim, first, step if b < batches - 1 else rows - first)
            vectors.update({first + i: row["vector"] for i, row in enumerate(chunk)})
            table.add(chunk)
        return home, table, vectors
    return build


def answers(home, vectors, probes=(3, 77, 150, 299)):
    """Что отвечает таблица: по вектору (ближайшие пять, как у поиска: все разделы и уточнение) и по слову, для каждой пробы."""
    table = ingest.open_table(home)
    out = {}
    for n in probes:
        near = table.search(vectors[n], vector_column_name="vector").nprobes(64).refine_factor(100).limit(5).to_list()
        out[("вектор", n)] = [r["path"] for r in near]
        out[("слово", n)] = sorted(r["path"] for r in table.search(f"карточка{n}", query_type="fts").limit(5).to_list())
    return out


def kinds(home):
    return sorted(i.index_type for i in ingest.open_table(home).list_indices())


def versions(home):
    return len(ingest.open_table(home).list_versions())


@pytest.fixture
def calls(monkeypatch):
    """Записывает каждое построение индекса по векторам (config из вызова create_index) и пропускает его к настоящему методу."""
    from lancedb.table import LanceTable
    real = LanceTable.create_index
    seen = []

    def spy(self, *args, **kwargs):
        seen.append(kwargs.get("config"))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(LanceTable, "create_index", spy)
    return seen


def no_deleting(monkeypatch):
    """Всё, чем LanceDB убирает старое или уплотняет, при вызове роняет тест."""
    from lancedb.table import LanceTable

    def forbidden(*args, **kwargs):
        raise AssertionError("без --compact ничего не удаляется и не уплотняется")

    for name in ("optimize", "cleanup_old_versions", "compact_files"):
        monkeypatch.setattr(LanceTable, name, forbidden)


def said(run):
    """Сообщения прохода run(): {код: параметры}."""
    return {m.code: m.args for m in run}


# ── размерность, число разбиений и подвекторов ──────────────────
def test_число_разбиений_корень_из_числа_строк_в_пределах_от_64_до_4096():
    for rows, expect in ((10_000, 100), (543_016, 736), (100, 64), (100_000_000, 4096), (0, 64)):
        assert O.partitions(rows) == expect, rows


@pytest.mark.parametrize("dim, expect", [(1024, 64), (768, 48), (384, 24), (1536, 96), (32, 2), (48, 3), (8, 1), (4, 1), (12, 3), (7, 7)])
def test_подвекторов_столько_чтобы_на_каждый_приходилось_по_шестнадцать_значений_а_иначе_по_меньшему_делителю(dim, expect):
    assert O.sub_vectors(dim) == expect
    assert dim % O.sub_vectors(dim) == 0


def test_порог_малой_таблицы_двадцать_тысяч_строк_замер_записан_в_коде():
    assert O.ANN_MIN_ROWS == 20_000
    with open(O.__file__, encoding="utf-8") as f:
        source = f.read()
    assert "мс" in source and "Замер" in source, "у порога должен быть записан замер: откуда число"


# ── малая таблица ───────────────────────────────────────────────
def test_на_малой_таблице_приближённый_индекс_не_строится_команда_говорит_об_этом_и_завершается_успехом(make, calls, monkeypatch):
    home, _, vectors = make()
    before = answers(home, vectors)
    run = []
    result = O.run(home, say=run.append)
    got = said(run)
    assert "optimize.ann_small" in got and got["optimize.ann_small"] == {"rows": ROWS, "min": O.ANN_MIN_ROWS}
    assert "optimize.ann_built" not in got and calls == [], "приближённый индекс на малой таблице строиться не должен"
    assert kinds(home) == ["FTS"]
    assert result["ann"] == "skipped"
    assert answers(home, vectors) == before


def test_печатает_число_строк_и_размер_каталога_индекса_до_и_после(make):
    home, _, _ = make()
    run = []
    result = O.run(home, say=run.append)
    assert result["rows_before"] == result["rows_after"] == ROWS
    assert result["bytes_before"] > 0 and result["bytes_after"] == O.folder_size(ingest.index_dir(home))
    codes = [m.code for m in run]
    assert codes[0] == "optimize.before" and codes[-1] == "optimize.after"
    assert run[0].args["rows"] == ROWS and run[-1].args["rows"] == ROWS
    assert run[0].args["mb"] >= 0 and run[-1].args["mb"] > 0


def test_размер_каталога_это_сумма_всех_файлов_вглубь(tmp_path):
    os.makedirs(tmp_path / "а" / "б")
    (tmp_path / "а" / "x").write_bytes(b"1" * 10)
    (tmp_path / "а" / "б" / "y").write_bytes(b"2" * 5)
    assert O.folder_size(str(tmp_path)) == 15
    assert O.folder_size(str(tmp_path / "нет")) == 0


# ── приближённый индекс, когда строк достаточно ─────────────────
def test_с_порогом_ниже_числа_строк_приближённый_индекс_строится_и_поиск_находит_то_же(make, calls, monkeypatch):
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    home, _, vectors = make()
    before = answers(home, vectors)
    run = []
    result = O.run(home, say=run.append)
    got = said(run)
    assert got["optimize.ann_built"]["partitions"] == O.partitions(ROWS) and got["optimize.ann_built"]["sub_vectors"] == O.sub_vectors(8)
    assert "optimize.ann_small" not in got and result["ann"] == "built"
    assert kinds(home) == ["FTS", "IvfPq"]
    (config,) = calls
    assert (config.distance_type, config.num_partitions, config.num_sub_vectors) == ("cosine", O.partitions(ROWS), O.sub_vectors(8))
    assert answers(home, vectors) == before


@pytest.mark.parametrize("dim", [32, 48])
def test_подвекторы_выводятся_из_размерности_настройки_а_не_заданы_числом(make, calls, monkeypatch, dim):
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    home, _, vectors = make(dim=dim)
    before = answers(home, vectors)
    O.run(home, say=lambda m: None)
    (config,) = calls
    assert config.num_sub_vectors == dim // 16
    assert kinds(home) == ["FTS", "IvfPq"] and answers(home, vectors) == before


def test_размерность_берётся_из_настройки_embed_dim_на_момент_вызова(make, calls, monkeypatch):
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    home, _, _ = make(dim=32)
    O.run(home, say=lambda m: None)
    assert calls[-1].num_sub_vectors == 2
    monkeypatch.setattr(ingest, "DIM", 16)                           # настройку поменяли: подвекторов выводится по новой размерности
    O.run(home, say=lambda m: None)
    assert calls[-1].num_sub_vectors == 1


def test_размерность_настройки_не_совпала_с_таблицей_отказ_с_кодом_шага_полнотекстовый_индекс_цел(make, calls, monkeypatch):
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    home, _, vectors = make(dim=32)
    before = answers(home, vectors)
    monkeypatch.setattr(ingest, "DIM", 48)                           # 32 значения на 3 подвектора не делятся
    with pytest.raises(O.OptimizeError) as e:
        O.run(home, say=lambda m: None)
    assert e.value.message.code == "optimize.ann_failed" and calls[-1].num_sub_vectors == 3
    assert kinds(home) == ["FTS"] and answers(home, vectors) == before


# ── ускоритель ──────────────────────────────────────────────────
def accelerators(calls):
    return [c.accelerator for c in calls]


def test_без_ключа_gpu_ускоритель_не_запрашивается_никогда_даже_при_включённой_embed_gpu(make, calls, monkeypatch):
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    real = settings.startup

    def gpu_on(*args, **kwargs):
        return {**real(*args, **kwargs), "embed_gpu": True}           # векторы запросов на видеокарте: построению индекса это не разрешение

    monkeypatch.setattr(settings, "startup", gpu_on)
    home, _, _ = make()
    O.run(home, say=lambda m: None)
    assert calls and accelerators(calls) == [None] * len(calls)


def test_с_ключом_gpu_ускоритель_запрашивается_cuda_и_об_этом_сказано(make, monkeypatch):
    from lancedb.table import LanceTable
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    seen = []

    def fake(self, *args, config=None, **kwargs):                     # настоящее построение на видеокарте в тесте не запускается
        seen.append(config.accelerator)

    monkeypatch.setattr(LanceTable, "create_index", fake)
    home, _, _ = make()
    run = []
    result = O.run(home, gpu=True, say=run.append)
    assert seen == ["cuda"] and result["ann"] == "built_gpu" and "optimize.ann_built_gpu" in said(run)


def test_видеокарта_не_подошла_индекс_строится_на_процессоре_и_об_этом_сказано(make, monkeypatch):
    from lancedb.table import LanceTable
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    real, seen = LanceTable.create_index, []

    def cuda_refuses(self, *args, config=None, **kwargs):
        seen.append(config.accelerator)
        if config.accelerator:
            raise RuntimeError("нет cuda")
        return real(self, *args, config=config, **kwargs)

    monkeypatch.setattr(LanceTable, "create_index", cuda_refuses)
    home, _, vectors = make()
    before = answers(home, vectors)
    run = []
    result = O.run(home, gpu=True, say=run.append)
    assert seen == ["cuda", None] and result["ann"] == "built"
    assert said(run)["optimize.gpu_failed"]["why"].startswith("RuntimeError") and "optimize.ann_built" in said(run)
    assert kinds(home) == ["FTS", "IvfPq"] and answers(home, vectors) == before


# ── без --compact ничего не удаляется ───────────────────────────
def test_без_compact_число_версий_таблицы_не_уменьшается_и_уплотнение_не_зовётся(make, monkeypatch):
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    home, _, vectors = make()
    start = versions(home)
    no_deleting(monkeypatch)
    run = []
    result = O.run(home, say=run.append)
    assert versions(home) >= start and result["compact"] is None
    kept = said(run)["optimize.kept"]
    assert kept["versions"] == versions(home) and "optimize.compacted" not in said(run)


def test_с_compact_фрагменты_сливаются_старые_версии_убираются_предупреждение_печатается_первым(make, monkeypatch):
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    home, _, vectors = make()
    before = answers(home, vectors)
    start = versions(home)
    run = []
    result = O.run(home, compact=True, say=run.append)
    codes = [m.code for m in run]
    assert codes[0] == "optimize.compact_warning" and codes.index("optimize.before") > 0
    done = result["compact"]
    assert done["versions_before"] >= start and done["versions_after"] < done["versions_before"] and versions(home) == done["versions_after"]
    assert done["fragments_before"] == 3 and done["fragments_after"] == 1
    assert said(run)["optimize.compacted"] == done
    assert "optimize.kept" not in said(run)
    assert kinds(home) == ["FTS", "IvfPq"] and answers(home, vectors) == before and ingest.open_table(home).count_rows() == ROWS


def test_с_compact_при_идущем_разборе_входящих_отказ_ничего_не_удалено(make, monkeypatch):
    home, _, vectors = make()
    before, start = answers(home, vectors), versions(home)
    no_deleting(monkeypatch)
    run = []
    with inbox.locked(home):                                          # замок разбора: его держит проход, пока идёт
        with pytest.raises(O.OptimizeError) as e:
            O.run(home, compact=True, say=run.append)
    assert (e.value.message.code, e.value.message.args) == ("optimize.busy", {})
    assert "flyarchive inbox status" in str(e.value)
    assert "optimize.compact_warning" in said(run), "предупреждение печатается до проверки"
    assert versions(home) == start and answers(home, vectors) == before
    assert "optimize.fts_built" not in said(run), "при отказе индексы не перестраиваются: он случился до первого шага"


def test_без_compact_идущий_разбор_не_мешает_индексы_строятся(make, monkeypatch):
    home, _, vectors = make()
    before = answers(home, vectors)
    with inbox.locked(home):
        run = []
        O.run(home, say=run.append)
    assert "optimize.fts_built" in said(run) and answers(home, vectors) == before


def test_после_compact_замок_разбора_свободен(make):
    home, _, _ = make()
    O.run(home, compact=True, say=lambda m: None)
    with inbox.locked(home):                                          # не Busy: команда замок отпустила
        pass


# ── нет таблицы ─────────────────────────────────────────────────
def test_нет_таблицы_тот_же_отказ_с_кодом_и_ничего_не_создаётся(tmp_path):
    home = str(tmp_path / "архив")
    with pytest.raises(ingest.TableMissing) as e:
        O.run(home, say=lambda m: None)
    assert (e.value.message.code, e.value.message.args) == ("index.no_table", {})
    assert not os.path.exists(home), "команда обслуживания не заводит ни каталог, ни таблицу"


def test_каталог_индекса_пуст_а_таблицы_нет_отказ_и_таблица_не_заводится(tmp_path):
    home = str(tmp_path / "архив")
    os.makedirs(ingest.index_dir(home))
    with pytest.raises(ingest.TableMissing):
        O.run(home, compact=True, say=lambda m: None)
    assert os.listdir(ingest.index_dir(home)) == []


# ── сбой шага не оставляет таблицу без полнотекстового индекса ──
def test_сбой_полнотекстового_индекса_прежний_индекс_цел_поиск_по_словам_работает_остальное_не_выполняется(make, monkeypatch):
    from lancedb.table import LanceTable
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    home, _, vectors = make()
    before = answers(home, vectors)

    def refuse(self, *args, **kwargs):
        raise RuntimeError("полнотекстовый индекс не построился")

    monkeypatch.setattr(LanceTable, "create_fts_index", refuse)
    no_deleting(monkeypatch)
    run = []
    with pytest.raises(O.OptimizeError) as e:
        O.run(home, compact=True, say=run.append)
    assert e.value.message.code == "optimize.fts_failed" and "полнотекстовый индекс не построился" in str(e.value)
    assert kinds(home) == ["FTS"], "после сбоя индекса по векторам нет, а полнотекстовый на месте"
    assert answers(home, vectors) == before


def test_сбой_приближённого_индекса_полнотекстовый_уже_построен_таблица_цела_уплотнения_нет(make, monkeypatch):
    from lancedb.table import LanceTable
    monkeypatch.setattr(O, "ANN_MIN_ROWS", 100)
    home, _, vectors = make(fts=False)
    assert kinds(home) == [], "в начале у таблицы нет никакого индекса"

    def refuse(self, *args, **kwargs):
        raise RuntimeError("обучение не вышло")

    monkeypatch.setattr(LanceTable, "create_index", refuse)
    no_deleting(monkeypatch)
    with pytest.raises(O.OptimizeError) as e:
        O.run(home, compact=True, say=lambda m: None)
    assert e.value.message.code == "optimize.ann_failed" and "обучение не вышло" in str(e.value)
    assert kinds(home) == ["FTS"], "полнотекстовый индекс строится первым: сбой следующего шага его не отнимает"
    table = ingest.open_table(home)
    assert [r["path"] for r in table.search("карточка77", query_type="fts").limit(3).to_list()] == ["входящие/пачка/77.txt"]
    assert table.count_rows() == ROWS


def test_сбой_уплотнения_индексы_построены_отказ_с_кодом(make, monkeypatch):
    from lancedb.table import LanceTable
    home, _, vectors = make()
    before = answers(home, vectors)

    def refuse(self, *args, **kwargs):
        raise RuntimeError("диск полон")

    monkeypatch.setattr(LanceTable, "optimize", refuse)
    with pytest.raises(O.OptimizeError) as e:
        O.run(home, compact=True, say=lambda m: None)
    assert e.value.message.code == "optimize.compact_failed" and "диск полон" in str(e.value)
    assert kinds(home) == ["FTS"] and answers(home, vectors) == before


# ── сама команда ────────────────────────────────────────────────
@pytest.fixture
def archive(tmp_path):
    """Архив во временном каталоге с временным домашним каталогом; размерность векторов — 8 (настройка embed_dim), службы векторов нет."""
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_DIM": "8"})


def filled(archive, rows=ROWS):
    """Заведённый командой init архив с rows строк в таблице."""
    assert archive("init")[0] == 0
    rng = random.Random(5)
    table = archive.table()
    table.add(rows_of(rng, 8, 0, rows))
    return table


def no_trace(text):
    assert "Traceback" not in text and "File \"" not in text, text


def test_команда_на_малой_таблице_печатает_строки_и_размер_до_и_после_и_завершается_нулём(archive):
    filled(archive)
    code, out, err = archive("index", "optimize")
    assert code == 0 and err == "", err
    lines = out.splitlines()
    assert lines[0].startswith(f"до: строк {ROWS}, каталог индекса ") and lines[0].endswith(" МБ")
    assert lines[-1].startswith(f"после: строк {ROWS}, каталог индекса ") and lines[-1].endswith(" МБ")
    assert any("полнотекстовый индекс" in line and "построен заново" in line for line in lines)
    assert any("приближённый индекс не нужен" in line and f"строк {ROWS}" in line and "20000" in line for line in lines)
    assert any("flyarchive index optimize --compact" in line for line in lines), "без ключа — подсказка, как убрать старые версии"
    assert kinds(archive.home) == ["FTS"]


def test_команда_с_json_отдаёт_факты_и_сообщения_для_программ(archive):
    filled(archive)
    code, out, err = archive("index", "optimize", "--json")
    assert code == 0 and err == ""
    data = json.loads(out)
    assert data["rows_before"] == data["rows_after"] == ROWS and data["ann"] == "skipped" and data["compact"] is None
    assert data["bytes_after"] > 0 and data["bytes_before"] > 0
    codes = [m["code"] for m in data["messages"]]
    assert codes[0] == "optimize.before" and "optimize.ann_small" in codes and codes[-1] == "optimize.after"
    assert len(out.strip().splitlines()) == 1


def test_команда_с_compact_печатает_предупреждение_и_итог_уплотнения(archive):
    filled(archive)
    code, out, err = archive("index", "optimize", "--compact")
    assert code == 0 and err == "", err
    lines = out.splitlines()
    assert "не делай его при идущей записи" in lines[0]
    assert any(line.startswith("таблица уплотнена: фрагментов было ") for line in lines)
    assert not any("flyarchive index optimize --compact" in line for line in lines)
    assert len(ingest.open_table(archive.home).list_versions()) <= 3


def test_уплотнение_не_выпускает_наружу_предупреждение_библиотеки_индекса(monkeypatch, tmp_path):
    """Свежая версия библиотеки индекса на уплотнение со снятием старых версий выдаёт своё предупреждение — о том же, о чём команда уже сказала
    сама и что закрыто замком разбора. В вывод команды оно не идёт: человек видит одно предупреждение, а не два, и не английской строкой."""
    import warnings

    class Table:
        def stats(self):
            return {"fragment_stats": {"num_fragments": 1}}

        def list_versions(self):
            return [1]

        def optimize(self, **kw):
            warnings.warn("optimize(cleanup_older_than=0) removes every version except the latest", UserWarning)

    monkeypatch.setattr(ingest, "open_table", lambda home: Table())
    told = []
    with warnings.catch_warnings():
        warnings.simplefilter("error")                       # любое выпущенное предупреждение стало бы отказом уплотнения
        O._compact(str(tmp_path), told.append)
    assert [message.code for message in told] == ["optimize.compacted"]


def test_команда_с_compact_при_идущем_разборе_отказывает_строкой_а_не_трассировкой(archive):
    filled(archive)
    start = len(archive.table().list_versions())
    with inbox.locked(archive.home):
        code, out, err = archive("index", "optimize", "--compact")
    assert code == 1
    no_trace(err)
    assert err.endswith("\n") and err.count("\n") == 1 and err.startswith("ошибка: идёт разбор входящих")
    assert "не делай его при идущей записи" in out, "предупреждение напечатано до отказа"
    assert len(archive.table().list_versions()) == start


def test_команда_с_compact_и_json_при_идущем_разборе_отказ_объектом_в_stderr(archive):
    filled(archive)
    with inbox.locked(archive.home):
        code, out, err = archive("index", "optimize", "--compact", "--json")
    assert code == 1 and out == ""
    assert json.loads(err.strip().splitlines()[-1])["error"]["code"] == "optimize.busy"


def test_команда_без_таблицы_отказывает_с_кодом_и_ничего_не_создаёт(archive):
    code, out, err = archive("index", "optimize")
    assert code == 1 and out == ""
    no_trace(err)
    assert err == "ошибка: " + str(M.make("index.no_table")) + "\n" and "flyarchive init" in err
    assert not os.path.exists(archive.home), "команда обслуживания завела каталог архива или таблицу"


def test_команда_без_таблицы_с_json_отдаёт_ошибку_с_кодом_в_stderr(archive):
    code, out, err = archive("index", "optimize", "--compact", "--json")
    assert code == 1 and out == ""
    assert json.loads(err.strip().splitlines()[-1])["error"]["code"] == "index.no_table"
    assert not os.path.exists(archive.home)


def test_ключи_команды_названы_в_справке():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive"),
                        "index", "optimize", "--help"], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0
    for key in ("--compact", "--gpu", "--json"):
        assert key in r.stdout, key

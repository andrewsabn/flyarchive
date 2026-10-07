"""Обслуживание индекса (FR-109): `flyarchive index optimize` строит поисковые индексы поверх таблицы и, по явному ключу, уплотняет её.

Без индексов LanceDB перебирает все строки подряд, а на сотнях тысяч фрагментов запрос занимает секунды вместо миллисекунд. Шаги по порядку,
каждый — один вызов LanceDB:

    1. полнотекстовый индекс по тексту — table.create_fts_index("text", replace=True). Первым: он нужен поиску по словам всегда, а сбой следующих
       шагов его не отнимает. Замена атомарна — не вышло, прежний индекс (если был) остаётся и поиск по словам работает;
    2. приближённый индекс по векторам (IVF-PQ, мера расстояния — ingest.METRIC, косинусная, та же, что у запроса поиска) — table.create_index("vector", config=IvfPq(...), replace=True). Число
       разбиений — корень из числа строк (partitions), подвекторов — из размерности (sub_vectors), размерность — настройка embed_dim (ingest.DIM).
       На малой таблице (меньше ANN_MIN_ROWS строк) не строится: перебор там быстрее индекса, и об этом сказано, это не сбой. На видеокарте —
       только с явным gpu=True (accelerator="cuda"); настройка embed_gpu относится к векторам запросов и здесь не читается. Видеокарта не подошла —
       индекс строится на процессоре;
    3. только с compact=True — table.optimize(cleanup_older_than=timedelta(0)): фрагменты сливаются в один, индексы доводятся до всех строк,
       убираются все версии таблицы, кроме последней. Перед этим — предупреждение «не делать при идущей записи», а при идущем разборе входящих
       (замок inbox.locked) — отказ optimize.busy: замок держится до конца работы, и таймер входящих в это время свой проход пропускает.
       delete_unverified не ставится: файлы чужой незавершённой записи не трогаются и без замка.

Без compact ничего не удаляется и не уплотняется. Таблицы нет — отказ index.no_table (ingest.TableMissing): ни каталога, ни таблицы команда не заводит.
Сообщения шагов идут в say по мере готовности (шаги длинные), итог — словарь run().

    run(home, compact=False, gpu=False, say=print) -> {"rows_before", "rows_after", "bytes_before", "bytes_after", "ann", "compact"}
"""
import contextlib
import os
import time
import warnings
from datetime import timedelta

import ingest
import messages

# Порог малой таблицы. Замер: процессор, векторы 1024, запрос как у search.py (nprobes 400, refine 30, 120 кандидатов):
#   10 тыс. строк — перебор 35 мс, индекс 55 мс;  30 тыс. — перебор 98 мс, индекс 38 мс;  60 тыс. — перебор 118 мс, индекс 60 мс.
# Время перебора растёт с числом строк, время запроса по индексу — нет, и кривые пересекаются около 15-20 тыс. строк: берём 20 тыс.
# (nprobes 400 больше числа разбиений, пока строк меньше 160 тыс., поэтому на малых таблицах индекс просматривает всё и выигрывает лишь на сжатии).
ANN_MIN_ROWS = 20_000
PARTITIONS_MIN, PARTITIONS_MAX = 64, 4096       # разбиений IVF: корень из числа строк, обычная эвристика, в этих пределах
SUB_VECTOR_WIDTHS = (16, 8, 4, 2)                # по сколько значений вектора приходится на подвектор PQ: по 16, а если размерность не делится — меньше


class OptimizeError(messages.CodedError):
    """Шаг обслуживания не удался или его нельзя начинать: сообщение из каталога, коды optimize.*."""


def partitions(rows):
    """Число разбиений IVF: корень из числа строк, не меньше PARTITIONS_MIN и не больше PARTITIONS_MAX."""
    return max(PARTITIONS_MIN, min(PARTITIONS_MAX, int(rows ** 0.5)))


def sub_vectors(dim):
    """Подвекторов PQ для вектора размерности dim: dim делится на них нацело, по 16 значений на подвектор (1024 — 64, 768 — 48); размерность,
    которая на 16 не делится, — по 8, 4 или 2 значения, а иначе по одному."""
    for width in SUB_VECTOR_WIDTHS:
        if dim % width == 0:
            return dim // width
    return dim


def folder_size(path):
    """Сумма размеров всех файлов каталога вглубь, байт; каталога нет — 0. Файл, ушедший между обходом и чтением (идёт запись), не считается."""
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total


def _why(error):
    return f"{type(error).__name__}: {error}"[:200]


def _state(home):
    """(число строк, байт в каталоге индекса) по заново открытой таблице: ту, что держит вызывающий, чужая запись не обновляет."""
    return ingest.open_table(home).count_rows(), folder_size(ingest.index_dir(home))


def _hold_intake(stack, home):
    """Замок разбора входящих на всё время работы: занят — разбор идёт, уплотнять нельзя."""
    import inbox
    try:
        stack.enter_context(inbox.locked(home))
    except inbox.Busy:
        raise OptimizeError(messages.make("optimize.busy")) from None


def _build_fts(table):
    with warnings.catch_warnings():                      # create_fts_index помечен устаревшим, замены во всех версиях библиотеки нет (как в ingest)
        warnings.simplefilter("ignore", DeprecationWarning)
        table.create_fts_index("text", replace=True)


def _build_ann(table, rows, gpu, say):
    """Приближённый индекс по векторам: "built" (процессор), "built_gpu"; сбой — OptimizeError."""
    from lancedb.index import IvfPq
    parts, subs = partitions(rows), sub_vectors(ingest.DIM)

    def build(accelerator):
        started = time.monotonic()
        table.create_index("vector", config=IvfPq(distance_type=ingest.METRIC, num_partitions=parts, num_sub_vectors=subs, accelerator=accelerator),
                           replace=True)
        return round(time.monotonic() - started, 1)

    try:
        if gpu:
            try:
                seconds = build("cuda")
            except Exception as e:                       # нет библиотеки ускорения или карта занята: счёт на процессоре, а не отказ
                say(messages.make("optimize.gpu_failed", why=_why(e)))
            else:
                say(messages.make("optimize.ann_built_gpu", seconds=seconds, partitions=parts, sub_vectors=subs))
                return "built_gpu"
        seconds = build(None)
    except Exception as e:
        raise OptimizeError(messages.make("optimize.ann_failed", why=_why(e))) from e
    say(messages.make("optimize.ann_built", seconds=seconds, partitions=parts, sub_vectors=subs))
    return "built"


def _compact(home, say):
    """Уплотнение: фрагменты сливаются, индексы доводятся до всех строк, старые версии таблицы убираются. Возвращает {фрагментов и версий до и после}."""
    table = ingest.open_table(home)
    fragments, versions = table.stats()["fragment_stats"]["num_fragments"], len(table.list_versions())
    try:
        with warnings.catch_warnings():                  # библиотека предупреждает о том же, о чём команда уже сказала сама и что закрыто замком разбора
            warnings.simplefilter("ignore", UserWarning)
            table.optimize(cleanup_older_than=timedelta(0))
    except Exception as e:
        raise OptimizeError(messages.make("optimize.compact_failed", why=_why(e))) from e
    table = ingest.open_table(home)
    done = {"fragments_before": fragments, "fragments_after": table.stats()["fragment_stats"]["num_fragments"],
            "versions_before": versions, "versions_after": len(table.list_versions())}
    say(messages.make("optimize.compacted", **done))
    return done


def run(home, compact=False, gpu=False, say=None):
    """Строит индексы поверх таблицы архива home; compact — ещё уплотняет её; gpu — приближённый индекс строить на видеокарте. say(сообщение) зовётся
    по мере готовности шагов. Нет таблицы — ingest.TableMissing, остальные отказы — OptimizeError."""
    say = say or (lambda message: None)
    table = ingest.open_table(home)                      # нет таблицы — отказ до всего остального: ничего не создано и замок не взят
    with contextlib.ExitStack() as stack:
        if compact:
            say(messages.make("optimize.compact_warning"))
            _hold_intake(stack, home)
        rows, size = _state(home)
        say(messages.make("optimize.before", rows=rows, mb=round(size / 1048576, 2)))
        result = {"rows_before": rows, "bytes_before": size, "ann": None, "compact": None}

        started = time.monotonic()
        try:
            _build_fts(table)
        except Exception as e:
            raise OptimizeError(messages.make("optimize.fts_failed", why=_why(e))) from e
        say(messages.make("optimize.fts_built", seconds=round(time.monotonic() - started, 1)))

        if rows < ANN_MIN_ROWS:
            say(messages.make("optimize.ann_small", rows=rows, min=ANN_MIN_ROWS))
            result["ann"] = "skipped"
        else:
            result["ann"] = _build_ann(table, rows, gpu, say)

        if compact:
            result["compact"] = _compact(home, say)
        else:
            say(messages.make("optimize.kept", versions=len(ingest.open_table(home).list_versions())))
        rows, size = _state(home)
        say(messages.make("optimize.after", rows=rows, mb=round(size / 1048576, 2)))
        result.update(rows_after=rows, bytes_after=size)
    return result

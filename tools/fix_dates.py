"""Настоящие даты в индексе.

Поле updated у многих строк — день копирования архива, а не дата документа: индексатор брал дату
из имени файла, а если её там нет — время изменения файла. Свежая дата даёт полный вес свежести,
и такие строки обходят в выдаче документы с настоящими датами.

Правило:
- письму — дата из заголовка Date (хранится в базе известного, см. known.py);
- вложению письма — дата его письма; письма рядом нет — дата из имени каталога вложений;
- вложению задачи и страницы (корни страниц таблицы источников) — дата этой задачи или страницы;
- вложенному письму — его собственная дата;
- страницы, задачи и прочие файлы не трогаются; где дату взять неоткуда, остаётся прежняя.

Таблица переписывается целиком в новый каталог, там же строятся поисковые индексы;
прежняя таблица не изменяется, пока каталоги не поменяют местами.

    plan(даты путей из индекса, база известного) -> Plan(new, …)
    rewrite(каталог индекса, новый каталог, новые даты) -> число строк
"""
import os
import re
import sqlite3
from collections import namedtuple

import ingest
import sources
from dedupe_index import norm

SOURCES = sources.startup()
MAIL = SOURCES.mail_roots()                     # корни писем и корни страниц с вложениями — из таблицы источников
PAGES = SOURCES.pages_roots()
ATT = "/_attachments/"
CODE = re.compile(r"^[0-9a-f]{8}$")
DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})")
LETTER_NAME = re.compile(r"_([0-9a-f]{8})\.(?:eml|msg)$")
MIN_INDEX_ROWS = 10_000        # на меньшей таблице поисковые индексы не строятся: перебор быстрее

Plan = namedtuple("Plan", "new letters by_parent by_name same unknown")


class DatesError(Exception):
    pass


def plan(index_dates, known_db):
    """Какую дату поставить каким путям. index_dates — {путь как в индексе: нынешняя дата}."""
    con = sqlite3.connect(known_db)
    letters_at = {}                               # (папка, код из имени письма) -> дата письма
    for path, date in con.execute("SELECT path, date FROM files WHERE date IS NOT NULL"):
        folder, _, name = path.rpartition("/")
        m = LETTER_NAME.search(name)
        if m:
            letters_at[(folder, m.group(1))] = date
    normed = {raw: norm(raw) for raw in index_dates}
    pages_at = {}                                 # (папка, номер задачи или страницы) -> её дата в индексе
    for raw, n in normed.items():
        if n.split("/", 1)[0] in PAGES and ATT not in n and index_dates[raw]:
            folder, _, name = n.rpartition("/")
            if "__" in name:
                pages_at[(folder, name.split("__", 1)[0])] = index_dates[raw][:10]

    new, letters, by_parent, by_name, same, unknown = {}, 0, 0, 0, 0, 0
    for raw, n in normed.items():
        root = n.split("/", 1)[0]
        date, how = None, None
        if root in MAIL:
            row = con.execute("SELECT date, mid, fp FROM files WHERE path = ?", (n,)).fetchone()
            is_letter = bool(row and (row[1] or row[2]))
            if is_letter and row[0]:
                date, how = row[0], "letter"
            elif ATT in n and not is_letter:
                base, _, rest = n.partition(ATT)
                folder = rest.split("/", 1)[0]
                code = folder.rsplit("_", 1)[-1]
                if CODE.match(code) and (base, code) in letters_at:
                    date, how = letters_at[(base, code)], "parent"
                elif DAY.match(folder):
                    date, how = DAY.match(folder).group(1), "name"
                else:
                    unknown += 1
                    continue
            elif is_letter:
                unknown += 1                      # письмо без даты в заголовках
                continue
            else:
                continue                          # прочий файл почты: не письмо и не вложение
        elif root in PAGES and ATT in n:
            base, _, rest = n.partition(ATT)
            date, how = pages_at.get((base, rest.split("/", 1)[0])), "parent"
            if not date:
                unknown += 1
                continue
        else:
            continue
        if date == (index_dates[raw] or "")[:10]:
            same += 1
            continue
        new[raw] = date
        letters += how == "letter"
        by_parent += how == "parent"
        by_name += how == "name"
    con.close()
    return Plan(new, letters, by_parent, by_name, same, unknown)


def index_dates(table):
    """{путь: дата} по всем путям индекса."""
    tab = table.search().select(["path", "updated"]).limit(50_000_000).to_arrow()
    dates = {}
    for p, u in zip(tab.column("path").to_pylist(), tab.column("updated").to_pylist()):
        dates.setdefault(p, u or "")
    return dates


def build_indexes(table, log=print):
    """Поисковые индексы таблицы (векторный и полнотекстовый), только на процессоре: видеокарта занята моделью."""
    from lancedb.index import IvfPq
    rows = table.count_rows()
    parts = max(64, min(4096, int(rows ** 0.5)))
    log(f"векторный индекс: {rows} строк, разбиений {parts}")
    table.create_index("vector", config=IvfPq(distance_type=ingest.METRIC, num_partitions=parts, num_sub_vectors=64), replace=True)
    log("полнотекстовый индекс")
    table.create_fts_index("text", replace=True)


def rewrite(src_dir, dst_dir, new_dates, indexes=True, log=print):
    """Переписывает таблицу docs в новый каталог с новыми датами. Исходная таблица не изменяется."""
    import lancedb
    import pyarrow as pa
    if os.path.exists(dst_dir) and os.listdir(dst_dir):
        raise DatesError(f"каталог для новой таблицы не пуст: {dst_dir}")
    src = lancedb.connect(src_dir).open_table("docs")
    names = [f.name for f in src.schema]
    log("читаю таблицу")
    tab = src.search().select(names).limit(50_000_000).to_arrow().select(names)
    updated = [new_dates.get(p, u) for p, u in zip(tab.column("path").to_pylist(), tab.column("updated").to_pylist())]
    tab = tab.set_column(names.index("updated"), "updated", pa.array(updated, pa.string()))
    log(f"пишу {tab.num_rows} строк в {dst_dir}")
    dst = lancedb.connect(dst_dir).create_table("docs", data=tab, schema=src.schema)
    if dst.count_rows() != src.count_rows():
        raise DatesError(f"в новой таблице {dst.count_rows()} строк, в исходной {src.count_rows()}")
    if indexes and dst.count_rows() >= MIN_INDEX_ROWS:
        build_indexes(dst, log)
    return dst.count_rows()

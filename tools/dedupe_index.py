"""Чистка индекса от повторов.

В проиндексированной почте одно письмо может лежать по нескольку раз: ящик выгружали не один раз, и копии
отличаются байтами. Здесь из индекса убираются фрагменты лишних копий; файлы в корпусе не трогаются.

Правило:
- письмо узнаётся по Message-ID (берётся из базы известного, см. known.py);
- письмо без Message-ID и любой другой файл почты — по sha256 содержимого;
- из каждой группы остаётся одна копия. Сначала та, у которой в индексе самая ранняя дата: у копии без
  даты в имени стоит день копирования, и самая ранняя дата среди копий — настоящая. При равных датах —
  из корня с меньшим рангом в таблице источников (корень без ранга — последний),
  дальше по алфавиту имени выгрузки — так письмо и его вложения остаются из одной выгрузки;
  внутри одной выгрузки остаётся больший файл: письмо с вложением внутри полнее письма без него;
- чистится только почта (корни вида mail в таблице источников): одинаковый файл при двух задачах трекера
  или на двух страницах вики остаётся при обеих. Без таблицы почтовых корней нет и чистить нечего.

    plan(пути из индекса, база известного) -> Plan(drop, …)
    apply(таблица, пути) -> число удалённых фрагментов
"""
import os
import sqlite3
from collections import Counter, namedtuple

import sources

SOURCES = sources.startup()
ALIAS = dict(SOURCES.aliases)                   # старые имена корней -> нынешние: из таблицы источников
ROOTS = SOURCES.mail_roots()                    # почтовые корни по порядку таблицы: только их чистка трогает
ROOT_RANK = SOURCES.ranks()                     # место корня: меньше — предпочтительнее; корня здесь нет — он последний

Plan = namedtuple("Plan", "drop letters files same_file groups outside unknown")


def norm(path):
    """Путь из индекса в том виде, как он записан в базе известного: прямые слэши, нынешнее имя корня."""
    return sources.normalize(path, ALIAS)


def _rank(n, raw, size, date, last=None):
    parts = n.split("/")
    if last is None:
        last = max(ROOT_RANK.values(), default=-1) + 1
    return date or "9999", ROOT_RANK.get(parts[0], last), parts[1] if len(parts) > 2 else "", -size, n, raw


def plan(index_paths, known_db, roots=None, dates=None):
    """Какие пути убрать из индекса. index_paths — пути так, как они записаны в индексе,
    dates — дата каждого пути в индексе (поле updated). roots — почтовые корни; не названы — те, что в таблице источников."""
    roots = ROOTS if roots is None else roots
    dates = dates or {}
    con = sqlite3.connect(known_db)
    groups, outside, unknown = {}, 0, 0
    for raw in sorted(set(index_paths)):
        n = norm(raw)
        if n.split("/", 1)[0] not in roots:
            outside += 1
            continue
        row = con.execute("SELECT sha, mid, size FROM files WHERE path = ?", (n,)).fetchone()
        if row is None:
            unknown += 1                          # файла нет в корпусе или он пуст: судить не о чем
            continue
        sha, mid, size = row
        groups.setdefault(("mid", mid) if mid else ("sha", sha), []).append((n, raw, size, (dates.get(raw) or "")[:10]))
    con.close()

    drop, letters, files, same_file, with_drops = [], 0, 0, 0, 0
    last = max(ROOT_RANK.values(), default=-1) + 1              # корень без ранга стоит после всех, у кого он есть
    for (kind, _), members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: _rank(*m, last))
        kept = members[0][0]
        with_drops += 1
        for n, raw, _, _ in members[1:]:
            drop.append(raw)
            if n == kept:
                same_file += 1                    # тот же файл, записанный в индекс под двумя видами пути
            elif kind == "mid":
                letters += 1
            else:
                files += 1
    return Plan(sorted(drop), letters, files, same_file, with_drops, outside, unknown)


def row_counts(table):
    """Сколько фрагментов лежит в индексе под каждым путём."""
    tab = table.search().select(["path"]).limit(50_000_000).to_arrow()
    return Counter(tab.column("path").to_pylist())


def index_meta(table):
    """(число фрагментов по путям, дата каждого пути) — за один проход по индексу."""
    tab = table.search().select(["path", "updated"]).limit(50_000_000).to_arrow()
    paths, updated = tab.column("path").to_pylist(), tab.column("updated").to_pylist()
    dates = {}
    for p, u in zip(paths, updated):
        dates.setdefault(p, u or "")
    return Counter(paths), dates


def indexed_paths(table):
    """Все пути, под которыми в индексе лежат фрагменты."""
    return set(row_counts(table))


def apply(table, drop, batch=500, progress=None):
    """Удаляет из индекса все фрагменты названных путей. Возвращает число удалённых строк."""
    before = table.count_rows()
    drop = list(drop)
    for i in range(0, len(drop), batch):
        values = ", ".join("'" + p.replace("'", "''") + "'" for p in drop[i:i + batch])
        table.delete(f"path IN ({values})")
        if progress:
            progress(min(i + batch, len(drop)), len(drop))
    return before - table.count_rows()

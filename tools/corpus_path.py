"""Путь из индекса -> файл корпуса. Один разбор на сервер поиска и сервер документов:
у обоих один результат, поэтому документ под псевдонимом корня и находится поиском, и читается.
"""
import os

import sources

SOURCES = sources.startup()
# документ может быть проиндексирован под другим именем корня, чем лежит на диске: псевдонимы — из таблицы источников (без неё их нет)
ALIAS = dict(SOURCES.aliases)


def resolve(rel, corpus):
    """Относительный путь из индекса -> реальный файл, с защитой от выхода за корпус."""
    # в таблице индекса могут быть пути с прямой и с обратной косой чертой: оба вида приводятся к одному
    rel = (rel or "").replace("\\", "/").replace("/", os.sep).lstrip(os.sep)
    root = os.path.abspath(corpus)
    cands = [rel]
    head, _, rest = rel.partition(os.sep)
    if head in ALIAS:
        cands.append(os.path.join(ALIAS[head], rest))
    for c in cands:
        p = os.path.abspath(os.path.join(root, c))
        if os.path.commonpath([p, root]) == root and os.path.isfile(p):
            return p
    return None

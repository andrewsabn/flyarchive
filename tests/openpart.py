"""Какие файлы входят в открытую часть: перечень для тестов, которые читают дерево репозитория.

В опубликованном снимке открытая часть — все файлы дерева, кроме каталога `.git` и кэшей: перечень берётся из `git ls-files` (отслеживаемые и
новые файлы, кроме перечисленных в `.gitignore`), а без git — обходом каталога. В репозитории, из которого собирается снимок, часть файлов в него не идёт.
Их образцы (fnmatch от корня, по одному в строке, `#` в начале строки — комментарий) лежат в необязательном файле, путь к которому задаёт
переменная окружения FLYARCHIVE_NOT_PUBLISHED; файл и переменную готовит та часть, которая собирает снимок. Без переменной открытая часть — все
файлы дерева. Имён файлов, которых в снимке нет, здесь и в тестах нет.
"""
import fnmatch
import functools
import os
import subprocess

import publication_gate as G

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOT_PUBLISHED_ENV = "FLYARCHIVE_NOT_PUBLISHED"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".out", ".venv"}      # .venv — окружение новичка в каталоге клона


@functools.cache
def tree_files():
    """Файлы дерева: отслеживаемые и новые, не из .gitignore; без git (так проверяется копия без .git) — обход каталога."""
    try:
        out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True, timeout=120,
                             env=G.clean_git_env())
        if out.returncode == 0 and out.stdout:
            return sorted(n.decode("utf-8") for n in out.stdout.split(b"\0") if n)
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/")
                  for d, dirs, files in os.walk(ROOT) for f in files if not SKIP_DIRS & set(os.path.relpath(d, ROOT).split(os.sep)))


def not_published_patterns(path=None):
    """Образцы путей, которые в снимок не идут: из файла, названного переменной окружения (или из path); нет переменной — пусто."""
    path = path or os.environ.get(NOT_PUBLISHED_ENV)
    if not path:
        return []
    with open(os.path.expanduser(path), encoding="utf-8-sig") as f:
        lines = [line.strip() for line in f.read().splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def open_files(files=None, patterns=None):
    """Файлы открытой части, как они лягут в снимок: файлы дерева без образцов из перечня «в снимок не идёт»."""
    patterns = not_published_patterns() if patterns is None else patterns
    names = tree_files() if files is None else files
    return [rel for rel in names if os.path.isfile(os.path.join(ROOT, rel)) and not any(fnmatch.fnmatchcase(rel, p) for p in patterns)]

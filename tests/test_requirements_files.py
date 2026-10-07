"""Файлы зависимостей сверены с кодом (FR-107): ни пропущенной библиотеки, ни лишней.

`requirements.txt` — обязательное: без него не заведёшь архив, не примешь и не найдёшь текстовый документ и pdf. `requirements-optional.txt` —
по форматам и возможностям, у каждой строки комментарий, что без неё не работает. `requirements-dev.txt` — для тестов. Сторонние импорты
всех файлов `tools/` (разбор AST, в том числе импорты внутри функций) должны совпасть с пакетами двух первых файлов; соответствие
«имя импорта — имя пакета» записано одной таблицей ниже.
"""
import ast
import importlib.metadata as metadata
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")
REQUIRED, OPTIONAL, DEV = "requirements.txt", "requirements-optional.txt", "requirements-dev.txt"

# Одна таблица: имя при импорте -> имя пакета на PyPI. PyMuPDF импортируется и как pymupdf, и под старым именем fitz.
PACKAGE_OF = {
    "lancedb": "lancedb", "pyarrow": "pyarrow", "pymupdf": "pymupdf", "fitz": "pymupdf", "PIL": "Pillow", "docx": "python-docx",
    "openpyxl": "openpyxl", "pptx": "python-pptx", "extract_msg": "extract-msg", "olefile": "olefile", "docling": "docling",
    "matplotlib": "matplotlib", "reportlab": "reportlab", "pytest": "pytest",
}
# Нижние границы: версии, на которых проект проверен.
BOUNDS = {"lancedb": "0.38", "pyarrow": "25.0", "pymupdf": "1.28", "Pillow": "10.2", "python-docx": "1.2", "openpyxl": "3.1",
          "python-pptx": "1.0", "extract-msg": "0.56", "olefile": "0.47", "docling": "2.126", "matplotlib": "3.11", "reportlab": "5.0",
          "pytest": "9.1"}
MUST_HAVE = {"lancedb", "pyarrow", "pymupdf"}                  # без них нет таблицы индекса, поиска и pdf: остальное — по форматам
LINE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)>=(?P<version>[0-9]+(?:\.[0-9]+)*)(?:\s+#\s*(?P<why>.*\S))?\s*$")
MIN_WHY = 15                                                    # знаков в комментарии: «нужна для pdf» уже не отписка, а пустое слово — да


def norm(name):
    """Имя пакета по PEP 503: регистр, точка, подчёркивание и дефис не различаются."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lines(name):
    """Строки файла зависимостей: [(номер, текст)] без пустых и без строк-комментариев."""
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        rows = list(enumerate(f.read().splitlines(), 1))
    return [(n, line.strip()) for n, line in rows if line.strip() and not line.strip().startswith("#")]


def read_file(name):
    """{имя пакета: (версия, комментарий)}; строка не вида «имя>=версия  # комментарий» — отказ с номером строки."""
    out = {}
    for number, line in read_lines(name):
        m = LINE.match(line)
        assert m, f"{name}:{number}: строка не вида «имя>=версия  # комментарий»: {line}"
        assert norm(m["name"]) not in map(norm, out), f"{name}:{number}: пакет назван дважды: {m['name']}"
        out[m["name"]] = (m["version"], m["why"])
    return out


def imports_of(source, local=()):
    """Сторонние модули, которые импортирует исходник: верхние имена, в том числе из импортов внутри функций; стандартная библиотека,
    свои модули (local) и относительные импорты не в счёт."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        found |= {n for n in names if n not in sys.stdlib_module_names and n not in local}
    return found


def code_files(folder):
    """Файлы кода каталога: *.py и команда flyarchive без расширения (сам каталог, без подкаталогов)."""
    return [os.path.join(folder, n) for n in sorted(os.listdir(folder))
            if os.path.isfile(os.path.join(folder, n)) and (n.endswith(".py") or n == "flyarchive")]


def local_modules(folder):
    return {n[:-3] for n in os.listdir(folder) if n.endswith(".py")} | {"flyarchive"}


def tools_imports():
    """{имя при импорте: [файлы tools/]} по всем файлам каталога tools."""
    local, out = local_modules(TOOLS), {}
    for path in code_files(TOOLS):
        with open(path, encoding="utf-8") as f:
            for name in imports_of(f.read(), local):
                out.setdefault(name, []).append(os.path.basename(path))
    return out



# ── сам разбор ──────────────────────────────────────────────────
def test_разбор_видит_импорт_внутри_функции_и_из_пакета_и_не_считает_стандартное_и_своё():
    source = ("import os, json\nimport lancedb\nimport tokens\nfrom PIL import Image\nfrom . import sibling\n"
              "def f():\n    import extract_msg\n    from docling.document_converter import DocumentConverter\n    import pymupdf as fitz\n")
    assert imports_of(source, local={"tokens"}) == {"lancedb", "PIL", "extract_msg", "docling", "pymupdf"}


def test_разбор_читает_команду_flyarchive_без_расширения_и_все_py_каталога():
    names = {os.path.basename(p) for p in code_files(TOOLS)}
    assert {"flyarchive", "search.py", "gate.py", "install.py", "office_server.py"} <= names
    assert "dsh-url" not in names and "dsh-web-start" not in names, "сценарии shell — не Python"


def test_нашлись_все_двенадцать_сторонних_библиотек_ядра():
    # разбор не может быть пустым: иначе «совпадение с файлами» было бы совпадением пустого с пустым
    packages = {PACKAGE_OF[name] for name in tools_imports() if name in PACKAGE_OF}
    assert len(packages) >= 12, sorted(packages)


# ── файлы ───────────────────────────────────────────────────────
@pytest.mark.parametrize("name", [REQUIRED, OPTIONAL, DEV])
def test_файл_зависимостей_есть_и_каждая_строка_имя_и_нижняя_граница(name):
    assert os.path.isfile(os.path.join(ROOT, name)), f"нет файла {name}"
    assert read_file(name), f"{name} пуст"


def test_нижние_границы_это_версии_на_которых_проект_проверен():
    found = {}
    for name in (REQUIRED, OPTIONAL, DEV):
        found.update({norm(pkg): version for pkg, (version, _) in read_file(name).items()})
    assert found == {norm(pkg): version for pkg, version in BOUNDS.items()}


def test_обязательные_только_те_без_которых_нет_таблицы_индекса_приёмки_и_pdf():
    assert {norm(p) for p in read_file(REQUIRED)} == {norm(p) for p in MUST_HAVE}


def test_один_пакет_лежит_только_в_одном_файле():
    seen = {}
    for name in (REQUIRED, OPTIONAL, DEV):
        for pkg in read_file(name):
            assert norm(pkg) not in seen, f"{pkg}: и в {seen[norm(pkg)]}, и в {name}"
            seen[norm(pkg)] = name


def test_у_каждой_строки_необязательных_комментарий_что_без_неё_не_работает():
    for pkg, (_, why) in read_file(OPTIONAL).items():
        assert why and len(why) >= MIN_WHY, f"{OPTIONAL}: у {pkg} нет комментария о том, что без неё не работает"


def test_у_каждой_строки_обязательных_комментарий_зачем_она():
    for pkg, (_, why) in read_file(REQUIRED).items():
        assert why and len(why) >= MIN_WHY, f"{REQUIRED}: у {pkg} нет комментария"


def test_файл_необязательных_не_повторяет_обязательное_и_тестовое():
    assert not {norm(p) for p in read_file(OPTIONAL)} & ({norm(p) for p in read_file(REQUIRED)} | {norm(p) for p in read_file(DEV)})


# ── сверка с кодом ──────────────────────────────────────────────
def test_сторонние_импорты_кода_совпадают_с_пакетами_двух_файлов_ни_пропущенного_ни_лишнего():
    imported = tools_imports()
    unknown = sorted(set(imported) - set(PACKAGE_OF))
    assert not unknown, f"в таблицу «имя импорта — пакет» не внесены: {unknown} (файлы: {[imported[n] for n in unknown]})"
    needed = {norm(PACKAGE_OF[name]) for name in imported}
    listed = {norm(p) for name in (REQUIRED, OPTIONAL) for p in read_file(name)}
    assert needed - listed == set(), f"код импортирует, а в файлах нет: {sorted(needed - listed)}"
    assert listed - needed == set(), f"в файлах есть, а код не импортирует: {sorted(listed - needed)}"


def test_каждое_имя_таблицы_импортируется_в_tools_кроме_имён_для_тестов():
    unused = sorted(set(PACKAGE_OF) - set(tools_imports()) - {"pytest"})
    assert not unused, f"в таблице имена, которых в tools нет: {unused}"


def test_сторонние_импорты_тестов_покрыты_тремя_файлами():
    local = local_modules(TOOLS) | {n[:-3] for n in os.listdir(HERE) if n.endswith(".py")} | {"conftest"}
    found = set()
    for folder in (HERE, os.path.join(HERE, "characterization")):
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    found |= imports_of(f.read(), local)
    unknown = sorted(found - set(PACKAGE_OF))
    assert not unknown, f"тесты импортируют то, чего нет в таблице: {unknown}"
    listed = {norm(p) for name in (REQUIRED, OPTIONAL, DEV) for p in read_file(name)}
    assert {norm(PACKAGE_OF[n]) for n in found} <= listed


def test_pytest_в_файле_для_тестов():
    assert [norm(p) for p in read_file(DEV)] == ["pytest"]


# ── версии ──────────────────────────────────────────────────────
def as_tuple(version):
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


@pytest.mark.parametrize("package", sorted(BOUNDS))
def test_установленная_версия_не_ниже_нижней_границы(package):
    try:
        have = metadata.version(package)
    except metadata.PackageNotFoundError:
        pytest.skip(f"{package} на этой машине не стоит")
    assert as_tuple(have) >= as_tuple(BOUNDS[package]), f"{package} {have} ниже границы {BOUNDS[package]}: граница не проверена"

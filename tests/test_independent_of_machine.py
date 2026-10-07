"""Набор тестов не падает на чужой машине: необязательной библиотеки нет, программы нет, клон лежит где угодно (FR-107).

Правило для открытого репозитория: на свежем клоне `python3 -m pytest` не показывает красного. Тест, которому нужна библиотека по формату или
программа, которой нет, пропускается с причиной, называющей её; где формат тесту не важен, образец берётся такой, что индексируется без библиотек.
Весь набор в двух окружениях — только обязательные библиотеки и с библиотеками по форматам — прогоняют отдельно; здесь держится то, что видно по
исходникам: модуль теста не импортирует необязательную библиотеку на верхнем уровне (иначе без неё не собирается весь файл, а не пропускается один
тест).
"""
import ast
import os

import doctor

HERE = os.path.dirname(os.path.abspath(__file__))
OPTIONAL = {module for module, _, must, _ in doctor.LIBRARIES if not must} | {"tomllib"}      # tomllib — стандартная библиотека с Python 3.11, а нижняя граница — 3.10


def top_level_imports(source):
    """Имена верхнего уровня модулей, которые файл импортирует безусловно: только прямые операторы тела модуля (под try, if и внутри функций — не в счёт)."""
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            names += [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module.split(".")[0])
    return names


def test_разбор_видит_безусловный_импорт_и_не_видит_условный():
    source = "import os\nimport docx\nfrom PIL import Image\ntry:\n    import openpyxl\nexcept ImportError:\n    openpyxl = None\n\n\ndef f():\n    import pptx\n"
    assert top_level_imports(source) == ["os", "docx", "PIL"]


def test_ни_один_файл_тестов_не_импортирует_необязательную_библиотеку_на_верхнем_уровне():
    found = []
    for folder, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    bad = sorted(OPTIONAL & set(top_level_imports(f.read())))
                if bad:
                    found.append(f"{os.path.relpath(os.path.join(folder, name), HERE)}: {', '.join(bad)}")
    assert not found, "без этих библиотек файл не собирается: импортируй их в теле теста или бери pytest.importorskip: " + "; ".join(found)


def test_необязательные_библиотеки_берутся_из_той_же_таблицы_что_у_проверки_окружения():
    assert {"docx", "openpyxl", "pptx", "PIL", "extract_msg", "olefile", "matplotlib", "reportlab", "docling"} <= OPTIONAL
    assert not OPTIONAL & {"lancedb", "pyarrow", "pymupdf"}, "обязательные библиотеки в набор необязательных не входят"

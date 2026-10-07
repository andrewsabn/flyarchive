"""Команды индекса без библиотеки индекса отвечают отказом с кодом, а не трассировкой (FR-107).

Раньше понятный отказ «нет библиотеки lancedb» был только у поиска: `flyarchive index optimize|dedupe|dates`, `flyarchive vision backfill` и `python3 tools/index_more.py` без неё падали
`ModuleNotFoundError` на пол-экрана трассировки, а сам `index_more` не загружался вовсе (импорт на верхнем уровне). Теперь одна строка отказа с сообщением
`lib.missing` (то же, что у поиска), код возврата 1, трассировки нет, ничего не создано и не изменено. Отсутствие библиотеки — блокировка импорта
в дочернем процессе (sitecustomize), а не удаление. С установленной библиотекой команды те же — это держат их собственные тесты (test_index_optimize,
test_dedupe_index, test_fix_dates, test_ingest).
"""
import os
import subprocess
import sys

import pytest

import ingest
import messages as M
from archivekit import ROOT, Archive, error_line

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

TOOLS = os.path.join(ROOT, "tools")
REFUSAL = "ошибка: " + str(M.make("lib.missing", package="lancedb", use="index", file="requirements.txt")) + "\n"
COMMANDS = [("index", "optimize"), ("index", "optimize", "--compact"), ("index", "optimize", "--gpu"),
            ("index", "dedupe"), ("index", "dedupe", "--apply"), ("index", "dates"), ("index", "dates", "--apply"),
            ("vision", "backfill", "--limit", "1", "--dry-run")]       # ищет по индексу документы без текста: тоже читает таблицу


@pytest.fixture
def archive(tmp_path):
    return Archive(tmp_path)


@pytest.fixture
def ready(archive):
    """Заведённый архив с таблицей и базой известного: команды индекса отказывают не потому, что архива нет."""
    assert archive("init")[0] == 0
    return archive


def no_trace(text):
    assert "Traceback" not in text and "File \"" not in text and "ModuleNotFoundError" not in text, text


def snapshot(home):
    """Каждый файл и каталог архива: путь -> размер. Любое создание или запись видны."""
    found = {}
    for folder, dirs, files in os.walk(home):
        for name in dirs:
            found[os.path.relpath(os.path.join(folder, name), home)] = None
        for name in files:
            found[os.path.relpath(os.path.join(folder, name), home)] = os.path.getsize(os.path.join(folder, name))
    return found


@pytest.mark.parametrize("args", COMMANDS, ids=lambda a: " ".join(a))
def test_команда_индекса_без_lancedb_отвечает_одной_строкой_с_кодом_а_не_трассировкой(ready, args):
    before = snapshot(ready.home)
    code, out, err = ready(*args, **ready.hide("lancedb"))
    assert code == 1 and out == "", (out, err)
    no_trace(err)
    assert err == REFUSAL and "python3 -m pip install -r requirements.txt" in err
    assert snapshot(ready.home) == before, "отказ ничего не создаёт и не меняет"


def test_optimize_с_json_без_lancedb_отвечает_объектом_ошибки_с_кодом(ready):
    code, out, err = ready("index", "optimize", "--json", **ready.hide("lancedb"))
    assert code == 1 and out == ""
    no_trace(err)
    error = error_line(err)
    assert error["code"] == "lib.missing" and error["args"] == {"package": "lancedb", "use": "index", "file": "requirements.txt"}


def test_библиотека_проверяется_раньше_базы_известного_а_не_после_неё(ready):
    os.remove(ready.path("index", "known.sqlite"))
    for command in ("dedupe", "dates"):
        code, out, err = ready("index", command, **ready.hide("lancedb"))
        assert (code, out, err) == (1, "", REFUSAL), command
    code, out, err = ready("index", "dedupe")
    assert code == 1 and "flyarchive known build" in err, "с библиотекой — прежний отказ про базу известного"


# ── index_more.py и ingest.py ───────────────────────────────────
def test_index_more_без_lancedb_при_прямом_запуске_отвечает_строкой_с_кодом(ready):
    code, out, err = ready.tool("index_more.py", **ready.hide("lancedb"))
    assert code == 1 and out == "", (out, err)
    no_trace(err)
    assert err == REFUSAL


def test_index_more_без_lancedb_ничего_не_создаёт_и_не_пишет_в_список_пройденного(ready):
    before = snapshot(ready.home)
    ready.tool("index_more.py", **ready.hide("lancedb"))
    assert snapshot(ready.home) == before and not os.path.exists(ready.path("index", "ingested.txt"))


@pytest.mark.parametrize("module", ["index_more", "ingest"])
def test_модуль_индекса_загружается_и_без_lancedb(archive, module):
    code = f"import sys; sys.path.insert(0, {TOOLS!r}); import {module}; print('загружен')"
    r = subprocess.run([sys.executable, "-c", code], env=archive.env(**archive.hide("lancedb")), capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and r.stdout.strip() == "загружен", r.stderr
    no_trace(r.stderr)


def test_ingest_отдаёт_библиотеку_индекса_или_отказ_с_кодом(monkeypatch):
    assert ingest.require_lancedb() is lancedb
    monkeypatch.setitem(sys.modules, "lancedb", None)
    with pytest.raises(M.CodedError) as e:
        ingest.require_lancedb()
    assert e.value.message.code == "lib.missing" and e.value.message.args == {"package": "lancedb", "use": "index", "file": "requirements.txt"}
    assert isinstance(e.value, M.CodedError) and str(e.value) == str(M.make("lib.missing", package="lancedb", use="index", file="requirements.txt"))


def test_чужой_сбой_импорта_отказом_про_отсутствие_не_называется(tmp_path, monkeypatch):
    """Не хватает зависимости самой библиотеки — это не «нет lancedb»: ошибка остаётся как есть, а не уводит человека ставить уже стоящее."""
    folder = tmp_path / "путь" / "lancedb"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("import dependency_that_is_not_installed_anywhere\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "путь"))
    monkeypatch.delitem(sys.modules, "lancedb")
    with pytest.raises(ModuleNotFoundError) as e:
        ingest.require_lancedb()
    assert e.value.name == "dependency_that_is_not_installed_anywhere"


def test_с_библиотекой_index_more_доходит_до_работы_и_без_таблицы_отказывает_как_раньше(archive):
    """Прежний отказ при установленной библиотеке не менялся: нет таблицы — трассировка прежняя не нужна, но и «нет библиотеки» тоже."""
    code, out, err = archive.tool("index_more.py")
    assert "lib.missing" not in err and "нет библиотеки" not in err


def test_поиск_отказывает_тем_же_сообщением_что_и_команды_индекса(ready):
    code, out, err = ready("search", "запрос", **ready.hide("lancedb"))
    assert (code, out, err) == (1, "", REFUSAL), "одно сообщение на все команды индекса"

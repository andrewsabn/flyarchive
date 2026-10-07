"""Проверка окружения загружает обязательные библиотеки, а не только находит их (FR-107).

Сломанная установка — файлы библиотеки на месте, а загрузка падает (не хватает системной библиотеки, не та версия соседа) — раньше проходила
как «есть»: поиск по пути находил пакет и не загружал его, а упасть предстояло при первом поиске или приёмке. Теперь обязательная библиотека
(requirements.txt) загружается, и отказ загрузки — строка «НЕТ» с именем библиотеки и первой строкой ошибки, код возврата 1, сообщение `lib.broken`.
Необязательные остаются проверкой наличия: загрузка некоторых занимает десятки секунд, и их поломка работу ядра не останавливает.
Сломанная установка в тестах — подставной пакет с именем библиотеки в каталоге на пути импорта: при загрузке он бросает ошибку. Команда — настоящий процесс.
"""
import json
import os
import site

import pytest

import doctor as D
import messages as M
from archivekit import Archive
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

FIRST = "libarrow.so.99: cannot open shared object file"
SECOND = "вторая строка трассировки не должна попасть в вывод"
BROKEN_SOURCE = "raise ImportError(%r)\n" % (FIRST + "\n" + SECOND)


@pytest.fixture
def archive(tmp_path, vectors):
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})


def broken(archive, *modules, source=BROKEN_SOURCE):
    """Переменные окружения процесса команды: перечисленные библиотеки найдутся на пути импорта и бросят ошибку при загрузке."""
    folder = os.path.join(str(archive.base), "сломано")
    for module in modules:
        os.makedirs(os.path.join(folder, module), exist_ok=True)
        with open(os.path.join(folder, module, "__init__.py"), "w", encoding="utf-8") as f:
            f.write(source)
    return {"PYTHONPATH": os.pathsep.join(p for p in (folder, site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p)}


def report(archive, **env):
    code, out, err = archive("doctor", "--json", **env)
    assert out.strip(), err
    return code, {c["id"]: c for c in json.loads(out)["checks"]}, json.loads(out)["ok"], err


@pytest.mark.parametrize("module, package", [("lancedb", "lancedb"), ("pyarrow", "pyarrow"), ("pymupdf", "pymupdf")])
def test_обязательная_библиотека_которая_не_загружается_это_нет_с_кодом_1_именем_и_первой_строкой_ошибки(archive, module, package):
    code, checks, ok, err = report(archive, **broken(archive, module))
    check = checks["lib." + package]
    assert code == 1 and ok is False and "Traceback" not in err
    assert check["status"] == "missing", "раньше сломанная установка проходила как «есть»"
    assert check["message"]["code"] == "lib.broken" and check["message"]["args"]["package"] == package
    assert check["message"]["args"]["file"] == "requirements.txt"
    assert check["message"]["args"]["error"] == "ImportError: " + FIRST
    text = check["message"]["text"]
    assert package in text and FIRST in text and SECOND not in text and "requirements.txt" in text
    lost = [c["id"] for c in checks.values() if c["status"] == "missing"]
    assert lost == ["lib." + package] or (package == "pyarrow" and lost == ["lib.lancedb", "lib.pyarrow"]), lost     # lancedb сама грузит pyarrow
    assert str(M.make("lib.broken", **check["message"]["args"])) == text


def test_обычный_вывод_называет_строкой_нет_имя_и_ошибку_а_итог_и_код_возврата_называют_библиотеку(archive):
    code, out, err = archive("doctor", **broken(archive, "lancedb"))
    assert code == 1 and err == ""
    line = next(l for l in out.splitlines() if "lancedb" in l and FIRST in l)
    assert line.split()[0] == "НЕТ" and SECOND not in out
    assert "lancedb" in out.splitlines()[-1] and str(M.make("doctor.summary_missing", names="lancedb")) == out.splitlines()[-1]


def test_таблица_индекса_при_сломанной_lancedb_не_проверяется_и_лишнего_сбоя_нет(archive):
    assert archive("init")[0] == 0
    code, checks, ok, err = report(archive, **broken(archive, "lancedb"))
    assert code == 1 and "Traceback" not in err and "table" not in checks, "таблицу открыла бы та же сломанная библиотека"
    assert checks["lib.lancedb"]["status"] == "missing"


def test_итог_init_называет_сломанную_обязательную_библиотеку(archive):
    code, out, err = archive("init", **broken(archive, "pymupdf"))
    assert code == 0, err
    assert out.rstrip("\n").splitlines()[-1] == str(M.make("doctor.tail_missing", names="pymupdf"))


def test_ошибка_длиннее_предела_обрезается_а_пустая_называется_видом_исключения(archive):
    long = "Е" * 1000
    code, checks, _, _ = report(archive, **broken(archive, "pyarrow", source="raise ImportError(%r)\n" % long))
    error = checks["lib.pyarrow"]["message"]["args"]["error"]
    assert code == 1 and error.startswith("ImportError: ЕЕЕ") and len(error) <= D.ERROR_MAX
    code, checks, _, _ = report(archive, **broken(archive, "pyarrow", source="raise RuntimeError()\n"))
    assert checks["lib.pyarrow"]["message"]["args"]["error"] == "RuntimeError"


def test_не_только_importerror_любой_сбой_при_загрузке_считается_поломкой(archive):
    code, checks, _, _ = report(archive, **broken(archive, "lancedb", source="import os\nos.nonexistent_function()\n"))
    assert code == 1 and checks["lib.lancedb"]["status"] == "missing"
    assert checks["lib.lancedb"]["message"]["args"]["error"].startswith("AttributeError: ")


def test_необязательная_библиотека_проверяется_наличием_и_её_поломка_не_замечается(archive):
    code, checks, ok, err = report(archive, **broken(archive, "docx", "openpyxl", "matplotlib"))
    assert code == 0 and ok is True and err == ""
    for package in ("python-docx", "openpyxl", "matplotlib"):
        assert checks["lib." + package]["status"] == "ok" and checks["lib." + package]["message"]["code"] == "doctor.lib_ok", package


def test_исправные_библиотеки_называются_как_раньше_с_версией(archive):
    code, checks, ok, _ = report(archive)
    assert code == 0 and ok is True
    for package in ("lancedb", "pyarrow", "pymupdf"):
        assert (checks["lib." + package]["status"], checks["lib." + package]["message"]["code"]) == ("ok", "doctor.lib_ok"), package


# ── та же проверка без процесса ─────────────────────────────────
def fake(tmp_path, monkeypatch, name, source):
    folder = tmp_path / "путь"
    (folder / name).mkdir(parents=True)
    (folder / name / "__init__.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(folder))


def test_проверка_библиотеки_загружает_обязательную_и_не_загружает_необязательную(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "dummy_required_lib", "raise OSError('сломано')\n")
    fake(tmp_path, monkeypatch, "dummy_optional_lib", "raise OSError('сломано')\n")
    must = D.check_library("dummy_required_lib", "dummy-required-lib", True, "index")
    assert (must.id, must.status, must.message.code) == ("lib.dummy-required-lib", "missing", "lib.broken")
    assert must.message.args == {"package": "dummy-required-lib", "error": "OSError: сломано", "file": "requirements.txt"}
    optional = D.check_library("dummy_optional_lib", "dummy-optional-lib", False, "docx")
    assert (optional.status, optional.message.code) == ("ok", "doctor.lib_ok")


def test_исправная_обязательная_библиотека_загружается_и_остаётся_в_таблице_загруженных(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "dummy_fine_lib", "LOADED = True\n")
    import sys
    check = D.check_library("dummy_fine_lib", "dummy-fine-lib", True, "index")
    assert (check.status, check.message.code) == ("ok", "doctor.lib_ok") and "dummy_fine_lib" in sys.modules
    sys.modules.pop("dummy_fine_lib", None)


def test_нет_библиотеки_вовсе_это_прежнее_сообщение_а_не_поломка():
    check = D.check_library("dummy_absent_lib_x", "dummy-absent-lib-x", True, "index")
    assert (check.status, check.message.code) == ("missing", "lib.missing")

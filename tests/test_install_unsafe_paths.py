"""Установка отказывает на путях, которые systemd разобрал бы по-своему (FR-104).

Путь, который systemd разобрал бы по-своему, в файл службы не попадает: кроме пробела это знаки процента, кавычек, обратной черты и доллара.
Отказ приходит до любой записи. Одинарная кавычка в адресе переходника не ломает файл настройки оболочки.
Команда настоящая (процесс, файл tools/flyarchive), машина выдуманная и пустая; живой архив и настоящие службы не трогаются.
"""
import json
import os
import site
import subprocess
import sys

import pytest

from archivekit import DEAD_VECTORS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLYARCHIVE = os.path.join(ROOT, "tools", "flyarchive")


def install(tmp_path, home, *args, **extra):
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    env = {"HOME": str(user), "FLYARCHIVE_HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8", "LANG": "C.UTF-8",
           # домашний каталог в тесте подменён, а библиотеки стоят в домашнем каталоге настоящего пользователя
           "PYTHONPATH": os.pathsep.join(p for p in (site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p),
           "FLYARCHIVE_EMBED_URL": DEAD_VECTORS}                # установка в конце спрашивает службу векторов: до живой службы владельца она не дойдёт
    env.update(extra)
    return subprocess.run([sys.executable, FLYARCHIVE, "install", *args], env=env, capture_output=True, text=True, encoding="utf-8",
                          cwd=str(tmp_path), timeout=120)


@pytest.mark.parametrize("sign", ["%", '"', "'", "\\", "$", " "], ids=["процент", "двойная кавычка", "одинарная кавычка", "обратная черта",
                                                                       "доллар", "пробел"])
@pytest.mark.parametrize("keys", [("--no-start",), ("--dry-run",)], ids=["установка", "пробный прогон"])
def test_знак_который_systemd_разбирает_по_своему_в_каталоге_архива_отказ_до_любой_записи(tmp_path, sign, keys):
    home = tmp_path / f"арх{sign}ив"
    r = install(tmp_path, home, *keys)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "нельзя записать в файл службы" in r.stderr and "Traceback" not in r.stderr
    assert not home.exists(), "каталог архива создан до отказа"
    assert os.listdir(tmp_path / "user") == [], "в домашнем каталоге что-то записано до отказа"


SYSTEM_PATH = "%h/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def put_dsh(folder):
    folder.mkdir(parents=True)
    (folder / "dsh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (folder / "dsh").chmod(0o755)


def dsh_unit_path(report):
    unit = next(f for f in report["files"] if f["path"].endswith("flyarchive-dsh.service"))["content"]
    lines = [line for line in unit.splitlines() if line.startswith("Environment=PATH=")]
    assert len(lines) == 1, unit
    return lines[0][len("Environment=PATH="):]


@pytest.mark.parametrize("name", ["Program Files", "сто%процентов", "it's", 'ка"вычка', "до$лар", "черта\\назад"],
                         ids=["пробел", "процент", "одинарная кавычка", "двойная кавычка", "доллар", "обратная черта"])
def test_каталог_dsh_со_знаком_который_systemd_разбирает_по_своему_в_PATH_службы_не_идёт_и_установка_это_называет(tmp_path, name):
    """Под WSL в PATH стоят каталоги Windows с пробелами. Каталог с пробелом оборвал бы строку окружения службы: systemd взял бы
    начало до пробела, остальное отбросил, и служба оболочки осталась бы без системных каталогов."""
    folder = tmp_path / name / "bin"
    put_dsh(folder)
    r = install(tmp_path, tmp_path / "архив", "--dry-run", "--json", PATH=f"{folder}:/usr/bin:/bin")
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert dsh_unit_path(report) == SYSTEM_PATH
    assert any(str(folder) in note and "dsh" in note and "нельзя записать" in note for note in report["notes"]), report["notes"]
    shown = install(tmp_path, tmp_path / "архив", "--dry-run", PATH=f"{folder}:/usr/bin:/bin")
    assert shown.returncode == 0 and "Команда dsh найдена в каталоге" in shown.stdout and f"PATH={SYSTEM_PATH}" in shown.stdout


def test_каталог_dsh_без_таких_знаков_стоит_в_PATH_службы_первым_и_замечания_о_нём_нет(tmp_path):
    folder = tmp_path / "npm" / "bin"
    put_dsh(folder)
    r = install(tmp_path, tmp_path / "архив", "--dry-run", "--json", PATH=f"{folder}:/usr/bin:/bin")
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert dsh_unit_path(report) == f"{folder}:{SYSTEM_PATH}"
    assert not any("dsh найдена" in note for note in report["notes"]), report["notes"]


def test_одинарная_кавычка_в_адресе_переходника_не_ломает_файл_настройки_оболочки(tmp_path):
    r = install(tmp_path, tmp_path / "архив", "--dry-run", "--json", FLYARCHIVE_MCP_URL="http://127.0.0.1:9777/mcp?x='y")
    assert r.returncode == 0, r.stderr
    patch = next(f for f in json.loads(r.stdout)["files"] if f["path"].endswith("dsh.patch.yml"))["content"]
    assert "url: 'http://127.0.0.1:9777/mcp?x=''y'" in patch, "кавычка в значении YAML в одинарных кавычках удваивается"

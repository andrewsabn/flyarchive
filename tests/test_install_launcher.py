"""Запускалка команды: после постоянной установки команда и плагин работают тем Python, в котором стоят библиотеки (FR-107).

Документы велят ставить библиотеки в виртуальное окружение, а ссылка в каталоге команд вела на файл с `#!/usr/bin/env python3`: в новом терминале без
включённого окружения команда отвечала «нет библиотеки», а плагин оболочки (он запускает файл напрямую, без оболочки) оставался без библиотек вовсе.
Теперь установка пишет в каталог архива запускалку — маленький файл sh, который запускает `tools/flyarchive` интерпретатором установки; ссылка в каталоге
команд и строка `command` в файле настройки оболочки ведут на неё. Машина выдуманная и пустая (свой домашний каталог, подставной systemctl); подставной
`python3`, сразу завершающийся ошибкой, стоит в начале PATH и пишет метку, если его позвали.
"""
import json
import os
import re
import subprocess
import sys

import pytest

import install
from test_install import FLYARCHIVE, TOOLS, Machine, mode, sha, tree

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: init заводит настоящую таблицу индекса")


def machine(tmp_path):
    return Machine(tmp_path, dsh=True)


def launcher_of(m):
    return m.home / "bin" / "flyarchive"


def link_of(m):
    return m.user / ".local" / "bin" / "flyarchive"


def decoy(tmp_path):
    """Каталог с подставными python3 и python, которые сразу падают и пишут метку: (каталог, файл метки)."""
    folder, mark = tmp_path / "подставной-python", tmp_path / "подставной-python-вызван"
    folder.mkdir()
    for name in ("python3", "python"):
        stub = folder / name
        stub.write_text(f'#!/bin/sh\necho "$0 $*" >> "{mark}"\nexit 97\n', encoding="utf-8")
        stub.chmod(0o755)
    return folder, mark


def call(m, path, *args, front):
    """Команда как её зовёт плагин: файл напрямую, без оболочки; в начале PATH — подставной python3."""
    return subprocess.run([str(path), *args], env=m.env(PATH=f"{front}:{m.bin}:/usr/bin:/bin"), capture_output=True, text=True, encoding="utf-8",
                          cwd=str(m.base), timeout=120)


def command_of_patch(m):
    """Путь из строки `command:` файла настройки оболочки."""
    found = re.search(r"command: '([^']*)'", (m.home / "dsh.patch.yml").read_text(encoding="utf-8"))
    assert found, "в файле настройки оболочки нет строки command"
    return found.group(1)


def statements(path):
    """Строки запускалки без комментариев и пустых."""
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]


# ── что пишет установка ─────────────────────────────────────────
def test_установка_пишет_запускалку_в_каталог_архива_исполняемую_только_владельцу(tmp_path):
    m = machine(tmp_path)
    r = m.run("--no-start")
    assert r.code == 0, r.err
    launcher = launcher_of(m)
    assert launcher.is_file() and not launcher.is_symlink()
    assert mode(launcher) == 0o700 and os.access(launcher, os.X_OK), "исполняемая и закрытая"
    assert mode(launcher.parent) == 0o700, "каталог запускалки закрыт, как остальной архив"
    assert launcher.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


def test_запускалка_зовёт_интерпретатор_установки_и_файл_команды_из_репозитория_с_теми_же_аргументами(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start").code == 0
    assert statements(launcher_of(m)) == [f"exec '{sys.executable}' '{TOOLS}/flyarchive' \"$@\""], "одна строка: запуск, python3 из пути поиска нет"


def test_ссылка_в_каталоге_команд_ведёт_на_запускалку_а_не_на_файл_команды_из_репозитория(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start").code == 0
    assert link_of(m).is_symlink() and os.readlink(link_of(m)) == str(launcher_of(m))


def test_строка_command_в_файле_настройки_оболочки_называет_запускалку_а_не_файл_в_каталоге_кода(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start").code == 0
    assert command_of_patch(m) == str(launcher_of(m)) and not command_of_patch(m).startswith(TOOLS)


def test_запускалка_пишется_и_без_каталога_команд_файл_настройки_оболочки_ссылается_на_неё(tmp_path):
    m = machine(tmp_path)
    r = m.run("--no-start", FLYARCHIVE_BIN_DIR="")
    assert r.code == 0 and not (m.user / ".local").exists()
    assert launcher_of(m).is_file() and command_of_patch(m) == str(launcher_of(m))


# ── команда работает интерпретатором установки ──────────────────
def test_команда_по_ссылке_работает_а_python3_из_пути_поиска_не_зовётся(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start").code == 0
    front, mark = decoy(tmp_path)
    version = call(m, link_of(m), "--version", front=front)
    assert version.returncode == 0 and version.stdout.strip(), version.stderr
    check = call(m, link_of(m), "doctor", "--json", front=front)
    answer = json.loads(check.stdout)
    states = {c["id"]: c["status"] for c in answer["checks"]}
    assert states["lib.lancedb"] == "ok" and states["python"] == "ok", "библиотеки видны: команда пошла интерпретатором установки"
    assert not mark.exists(), mark.read_text(encoding="utf-8") if mark.exists() else ""


def test_команда_из_строки_command_файла_настройки_оболочки_существует_исполняема_и_работает_тем_же_интерпретатором(tmp_path):
    m = machine(tmp_path)
    environment = tmp_path / "окружение" / "bin"
    environment.mkdir(parents=True)
    interpreter = environment / "python"
    interpreter.symlink_to(sys.executable)                                    # как у виртуального окружения: интерпретатор — ссылка
    # ссылка в другом каталоге теряет библиотеки окружения, в котором идёт сам тест (Python ищет их рядом со своим путём): называем их явно
    libraries = [p for p in sys.path if os.path.basename(p) in ("site-packages", "dist-packages")]
    env = {**m.env(), "PYTHONPATH": os.pathsep.join(filter(None, libraries + [m.env().get("PYTHONPATH", "")]))}
    done = subprocess.run([str(interpreter), FLYARCHIVE, "install", "--no-start"], env=env, capture_output=True, text=True, encoding="utf-8",
                          cwd=str(m.base), timeout=300)
    assert done.returncode == 0, done.stderr
    path = command_of_patch(m)
    assert os.path.isfile(path) and os.access(path, os.X_OK)
    assert statements(m.home / "bin" / "flyarchive")[0].startswith(f"exec '{interpreter}' "), "в запускалке — интерпретатор, которым шла установка"
    used = tmp_path / "интерпретатор-вызван"
    interpreter.unlink()                                                      # теперь интерпретатор считает, сколько раз его позвали
    interpreter.write_text(f'#!/bin/sh\necho "$0" >> "{used}"\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    interpreter.chmod(0o755)
    front, mark = decoy(tmp_path)
    got = call(m, path, "--version", front=front)
    assert got.returncode == 0 and got.stdout.strip(), got.stderr
    assert used.read_text(encoding="utf-8").strip() == str(interpreter), "команду запустил интерпретатор установки"
    assert not mark.exists()


def test_аргументы_доходят_до_команды_целыми_без_разбора_оболочкой(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start").code == 0
    front, mark = decoy(tmp_path)
    got = call(m, launcher_of(m), "token", "add", "имя; touch взломано", front=front)
    assert got.returncode == 1 and "имя клиента" in got.stderr, (got.stdout, got.stderr)
    assert not (m.base / "взломано").exists() and not mark.exists()


def test_прямой_запуск_файла_команды_работает_как_раньше(tmp_path):
    got = subprocess.run([sys.executable, FLYARCHIVE, "--version"], capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path), timeout=60)
    assert got.returncode == 0 and got.stdout.strip(), got.stderr


# ── повторная установка и пробный прогон ────────────────────────
def test_повторная_установка_переписывает_запускалку_тем_же_содержимым_и_возвращает_права(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start").code == 0
    launcher = launcher_of(m)
    first = launcher.read_bytes()
    launcher.write_text("#!/bin/sh\necho испорчено\n", encoding="utf-8")
    launcher.chmod(0o600)                                                     # испорчена и лишена права запуска
    assert m.run("--no-start").code == 0
    assert launcher.read_bytes() == first and mode(launcher) == 0o700


def test_пробный_прогон_показывает_запускалку_среди_записываемого_и_ничего_не_пишет(tmp_path):
    m = machine(tmp_path)
    before = tree(tmp_path)
    r = m.run("--dry-run")
    assert r.code == 0, r.err
    assert tree(tmp_path) == before
    assert not m.home.exists() and not (m.user / ".local").exists()
    assert f"записал бы {launcher_of(m)} (права 0700" in r.out
    assert f"exec '{sys.executable}' '{TOOLS}/flyarchive' \"$@\"" in r.out, "содержимое запускалки напечатано"


def test_json_называет_запускалку_путём_правами_отпечатком_и_содержимым_в_пробном_прогоне(tmp_path):
    m = machine(tmp_path)
    plan = m.run("--dry-run", "--json").json()
    entry = next(f for f in plan["files"] if f["path"] == str(launcher_of(m)))
    assert entry["mode"] == "0700" and entry["sha256"] == sha(entry["content"].encode("utf-8"))
    assert plan["launcher"] == str(launcher_of(m)) and plan["command"]["target"] == str(launcher_of(m))
    done = m.run("--no-start", "--json").json()
    written = next(f for f in done["files"] if f["path"] == str(launcher_of(m)))
    assert "content" not in written and written["sha256"] == sha(launcher_of(m).read_bytes()) == entry["sha256"], "показанное и записанное совпали"
    assert done["launcher"] == str(launcher_of(m))


# ── прежняя ссылка ──────────────────────────────────────────────
def test_прежняя_ссылка_на_файл_команды_из_репозитория_заменяется_ссылкой_на_запускалку(tmp_path):
    m = machine(tmp_path)
    link_of(m).parent.mkdir(parents=True)
    link_of(m).symlink_to(FLYARCHIVE)                                         # так клала ссылку более ранняя установка
    r = m.run("--no-start", "--json").json()
    assert os.readlink(link_of(m)) == str(launcher_of(m)) and r["command"]["state"] == "replaced"
    again = m.run("--no-start", "--json").json()
    assert again["command"]["state"] == "exists" and os.readlink(link_of(m)) == str(launcher_of(m))


def test_прежняя_ссылка_через_другой_путь_к_каталогу_кода_тоже_заменяется_и_установка_это_говорит(tmp_path):
    m = machine(tmp_path)
    link_of(m).parent.mkdir(parents=True)
    other = tmp_path / "обход"
    other.symlink_to(os.path.dirname(FLYARCHIVE))
    link_of(m).symlink_to(other / "flyarchive")
    r = m.run("--no-start")
    assert r.code == 0 and os.readlink(link_of(m)) == str(launcher_of(m)) and "заменена" in r.out


def test_пробный_прогон_с_прежней_ссылкой_говорит_что_заменил_бы_и_ссылку_не_трогает(tmp_path):
    m = machine(tmp_path)
    link_of(m).parent.mkdir(parents=True)
    link_of(m).symlink_to(FLYARCHIVE)
    r = m.run("--dry-run")
    assert "заменил бы" in r.out and os.readlink(link_of(m)) == FLYARCHIVE
    assert m.run("--dry-run", "--json").json()["command"]["state"] == "legacy"


def test_чужая_ссылка_и_чужой_файл_по_прежнему_не_трогаются(tmp_path):
    m = machine(tmp_path)
    link_of(m).parent.mkdir(parents=True)
    other = tmp_path / "чужая-команда"
    other.write_text("#!/bin/sh\n", encoding="utf-8")
    link_of(m).symlink_to(other)
    assert m.run("--no-start").code == 0 and os.readlink(link_of(m)) == str(other)
    link_of(m).unlink()
    link_of(m).write_text("чужое", encoding="utf-8")
    assert m.run("--no-start").code == 0 and not link_of(m).is_symlink() and link_of(m).read_text(encoding="utf-8") == "чужое"


# ── пути, которые в запускалку не записать ──────────────────────
@pytest.mark.parametrize("bad", ["/opt/мой python/bin/python", "/opt/it's/python", "/opt/a$b/python", "/opt/a\"b/python", "/opt/a\\b/python",
                                 "/opt/a%b/python", "/opt/a\nb/python"], ids=["пробел", "кавычка", "доллар", "двойная кавычка", "обратная черта", "процент", "перевод строки"])
def test_путь_который_нельзя_записать_в_одинарные_кавычки_sh_запускалку_не_получает(bad):
    with pytest.raises(install.InstallError):
        install.launcher_text(bad, TOOLS)
    with pytest.raises(install.InstallError):
        install.launcher_text(sys.executable, bad)


def test_запускалка_из_безопасных_путей_собирается_одной_строкой_exec():
    text = install.launcher_text("/opt/venv/bin/python", "/srv/flyarchive/tools")
    assert text.startswith("#!/bin/sh\n") and text.endswith("\n")
    assert [line for line in text.splitlines() if not line.startswith("#")] == ["exec '/opt/venv/bin/python' '/srv/flyarchive/tools/flyarchive' \"$@\""]

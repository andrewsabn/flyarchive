"""Тесты, которые запускают git, не трогают чужой репозиторий.

У человека, который работает в нескольких рабочих копиях, в окружении бывают `GIT_DIR`, `GIT_WORK_TREE` и родственные переменные. Тест, который
зовёт `git init` во временном каталоге, с ними переписал бы конфиг его настоящего репозитория. Что держат тесты: общий перечень переменных
(`publication_gate.GIT_LOCAL_VARIABLES`) назван целиком; очистка окружения (`publication_gate.clean_git_env`) убирает их и больше ничего; ворота
зовут git без них; на время каждого теста набор их убирает сам (автоматическая фикстура в `tests/conftest.py`). Сторож запускает один лёгкий
тест с `git init` отдельным процессом pytest при выставленных `GIT_DIR` и `GIT_WORK_TREE`, которые указывают на подставной репозиторий, и
сверяет подставной репозиторий до и после байт в байт.
"""
import os
import shutil
import subprocess
import sys

import pytest

import publication_gate as G

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="нет git")
VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE", "GIT_PREFIX")
LIGHT_CASE = "tests/test_publication_gate.py::test_отслеживаемый_но_удалённый_с_диска_файл_не_роняет_ворота"     # git init, add и проверка файлов
ABSENT_CASE = "tests/test_git_environment.py::test_набор_убирает_переменные_git_на_время_каждого_теста"          # переменных в окружении теста нет


def bytes_of(folder):
    """{путь: содержимое} для всех файлов каталога: подставной репозиторий до и после."""
    found = {}
    for base, _, files in os.walk(folder):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                found[os.path.relpath(path, folder)] = f.read()
    return found


def plain_git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if k not in VARIABLES}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, env=env, capture_output=True, check=True)


def test_общий_перечень_называет_все_семь_переменных_git():
    assert tuple(G.GIT_LOCAL_VARIABLES) == VARIABLES


def test_очистка_окружения_убирает_переменные_git_и_ничего_больше_не_трогает():
    source = {**{name: "/чужое/" + name for name in VARIABLES}, "PATH": "/usr/bin", "HOME": "/h", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": "/dev/null"}
    cleaned = G.clean_git_env(source)
    assert cleaned == {"PATH": "/usr/bin", "HOME": "/h", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": "/dev/null"}
    assert all(name in source for name in VARIABLES), "исходное окружение не должно меняться"


def test_очистка_без_аргумента_берёт_окружение_процесса(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/чужое/.git")
    monkeypatch.setenv("FLYARCHIVE_SAMPLE_VARIABLE", "да")
    cleaned = G.clean_git_env()
    assert "GIT_DIR" not in cleaned and cleaned["FLYARCHIVE_SAMPLE_VARIABLE"] == "да"


@needs_git
def test_набор_убирает_переменные_git_на_время_каждого_теста():
    assert [name for name in VARIABLES if name in os.environ] == []


@needs_git
def test_ворота_зовут_git_без_переменных_окружения(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    plain_git("init", "-q", cwd=repo)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    plain_git("add", "a.txt", cwd=repo)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "нет-такого" / ".git"))          # так git не нашёл бы свой репозиторий
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "нет-такого"))
    names, _ = G.git_files(str(repo))
    assert names == ["a.txt"]


@needs_git
def test_git_init_в_тесте_не_меняет_подставной_репозиторий_из_переменных_окружения(tmp_path):
    fake, decoy = tmp_path / "подставной", tmp_path / "чужое-дерево"
    fake.mkdir()
    decoy.mkdir()
    plain_git("init", "-q", cwd=fake)
    before = bytes_of(fake / ".git")
    env = {k: v for k, v in os.environ.items() if k not in VARIABLES}
    env.update(GIT_DIR=str(fake / ".git"), GIT_WORK_TREE=str(decoy), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "pytest", LIGHT_CASE, ABSENT_CASE, "-p", "no:cacheprovider", "-p", "no:warnings"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert bytes_of(fake / ".git") == before, "git init из теста переписал подставной репозиторий"
    assert not os.listdir(decoy), "в дерево, названное переменной, что-то записано"
    assert r.returncode == 0, (r.stdout + r.stderr)[-600:]

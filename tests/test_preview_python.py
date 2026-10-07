"""Рабочий процесс просмотра идёт настоящим интерпретатором родителя, а не зашитым `/usr/bin/python3` (FR-76).

Родитель запускает рабочий процесс в песочнице и передаёт ему пути библиотек (PyMuPDF, Pillow) из своего окружения. Раньше интерпретатор был один —
`/usr/bin/python3`: просмотр работал, только когда окружение создано из того же системного Python. Теперь берётся настоящий интерпретатор родителя
(`os.path.realpath(sys.executable)`: у виртуального окружения это базовый интерпретатор). Лежит он в системных каталогах, которые песочница и так
видит, — всё как раньше; лежит вне них (свой Python в домашнем каталоге, pyenv, conda) — песочнице отдаётся только для чтения каталог его установки;
если отдать его нельзя безопасно (каталог первого уровня, корень, домашний каталог владельца или каталог над ним, каталог архива или над ним,
интерпретатор вне своего каталога установки), просмотр отвечает понятным отказом. Песочница здесь не запускается: проверяются выбор интерпретатора,
командная строка bwrap и ответы родителя.
"""
import os
import sys
from pathlib import Path

import pytest

import messages as M
import preview as P
from test_preview_kit import env, worker  # noqa: F401

SYSTEM = os.path.realpath("/usr/bin/python3")


def binds(argv):
    """Привязки командной строки bwrap: [(ключ, источник, назначение)]."""
    return [(argv[i], argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a in ("--bind", "--ro-bind", "--ro-bind-fd")]


def pyenv(tmp_path, name="3.12.1"):
    """Свой Python в домашнем каталоге, как у pyenv: (каталог установки, интерпретатор, домашний каталог владельца)."""
    user = tmp_path / "user"
    prefix = user / ".pyenv" / "versions" / name
    (prefix / "bin").mkdir(parents=True)
    interpreter = prefix / "bin" / "python3.12"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o755)
    return prefix, interpreter, user


# ── какой интерпретатор ─────────────────────────────────────────
def test_по_умолчанию_интерпретатор_настоящий_интерпретатор_родителя_а_не_зашитый_путь():
    assert P.PYTHON == os.path.realpath(sys.executable)
    assert P.worker_python().path == P.PYTHON


@pytest.mark.parametrize("path", ["/usr/bin/python3.12", "/usr/local/bin/python3.12", "/usr/lib/python3.12/../../bin/python3"])
def test_интерпретатор_в_системных_каталогах_виден_песочнице_и_так_привязки_нет(path):
    found = P.worker_python(executable=path, base_prefix="/usr")
    assert found.path == os.path.realpath(path) and found.bind is None and found.refusal is None


@pytest.mark.skipif(not os.path.exists("/usr/bin/python3"), reason="нет /usr/bin/python3: системного Python для окружения не из чего брать")
def test_у_виртуального_окружения_берётся_базовый_интерпретатор(tmp_path):
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to("/usr/bin/python3")
    found = P.worker_python(executable=str(venv / "python"), base_prefix="/usr")
    assert found.path == SYSTEM and found.bind is None and found.refusal is None


def test_свой_python_в_домашнем_каталоге_отдаётся_песочнице_каталогом_установки_только_для_чтения(env):
    prefix, interpreter, user = pyenv(env.tmp)
    found = P.worker_python(env.home, executable=str(interpreter), base_prefix=str(prefix), user_home=str(user))
    assert found == P.WorkerPython(os.path.realpath(interpreter), os.path.realpath(prefix), None)


def test_окружение_поверх_своего_python_берёт_интерпретатор_из_каталога_установки(env):
    prefix, interpreter, user = pyenv(env.tmp)
    venv = env.tmp / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(interpreter)
    found = P.worker_python(env.home, executable=str(venv / "python"), base_prefix=str(prefix), user_home=str(user))
    assert found.path == os.path.realpath(interpreter) and found.bind == os.path.realpath(prefix) and found.refusal is None


def test_нет_пути_интерпретатора_берётся_системный():
    found = P.worker_python(executable="")
    assert found == P.WorkerPython("/usr/bin/python3", None, None)


# ── когда отдать каталог установки нельзя ───────────────────────
def refusal_for(env, prefix, executable=None, user=None):
    user = user or env.tmp / "user"
    found = P.worker_python(env.home, executable=str(executable or prefix / "bin" / "python3"), base_prefix=str(prefix), user_home=str(user))
    assert found.bind is None and found.refusal is not None, found
    assert found.refusal.code == "preview.python_outside" and found.refusal.args == {"python": found.path}
    return found


def test_каталог_установки_это_домашний_каталог_владельца_отказ(env):
    user = env.tmp / "user"
    (user / "bin").mkdir(parents=True)
    refusal_for(env, user, user=user)


def test_каталог_установки_над_домашним_каталогом_владельца_отказ(env):
    (env.tmp / "дом" / "владелец").mkdir(parents=True)
    refusal_for(env, env.tmp / "дом", user=env.tmp / "дом" / "владелец")


def test_каталог_установки_содержит_каталог_архива_или_лежит_в_нём_отказ(env):
    elsewhere = Path("/нет-такого-дома")                   # домашний каталог владельца не мешает: причина только в архиве
    refusal_for(env, env.tmp, user=elsewhere)
    inside = os.path.join(env.home, "python")
    os.makedirs(os.path.join(inside, "bin"))
    refusal_for(env, Path(inside), user=elsewhere)


def test_каталог_установки_каталог_первого_уровня_или_корень_отказ(env):
    refusal_for(env, Path("/opt"), executable="/opt/bin/python3")
    refusal_for(env, Path("/"), executable="/bin-нет/python3")


def test_интерпретатор_лежит_не_в_своём_каталоге_установки_отказ(env):
    prefix, interpreter, user = pyenv(env.tmp)
    copied = env.tmp / "копия" / "bin"
    copied.mkdir(parents=True)
    (copied / "python").write_text("#!/bin/sh\n", encoding="utf-8")            # окружение с копией интерпретатора, а не ссылкой
    refusal_for(env, prefix, executable=copied / "python", user=user)


def test_отказ_называет_интерпретатор_и_что_просмотр_работает_с_python_из_системных_каталогов(env):
    prefix, _, user = pyenv(env.tmp)
    found = refusal_for(env, user, executable=user / "bin" / "python3", user=user)
    text = str(found.refusal)
    assert text.startswith("просмотр работает с Python из системных каталогов:") and found.path in text


# ── командная строка песочницы ──────────────────────────────────
def command(tmp_path, python=None, libs=()):
    return P.sandbox_command(str(tmp_path / "код"), "preview_worker.py", ["describe", "/in/data", "/out", "pdf"], str(tmp_path / "выход"), 7, list(libs), python)


def test_без_привязки_командная_строка_прежняя_и_идёт_интерпретатором_по_умолчанию(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "bwrap_path", lambda: "/usr/bin/bwrap")
    argv, _ = command(tmp_path)
    assert argv[argv.index("--") + 1:] == [P.PYTHON, "-s", "-B", "/code/preview_worker.py", "describe", "/in/data", "/out", "pdf"]
    assert {dst for _, _, dst in binds(argv)} <= {"/usr", "/etc", "/code", "/in/data", "/out", *P.SYSTEM_DIRS}, "лишних привязок нет"


def test_свой_python_привязан_только_для_чтения_и_запущен_по_своему_пути(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "bwrap_path", lambda: "/usr/bin/bwrap")
    prefix, interpreter, _ = pyenv(tmp_path)
    python = P.WorkerPython(str(interpreter), str(prefix), None)
    argv, _ = command(tmp_path, python)
    assert ("--ro-bind", str(prefix), str(prefix)) in binds(argv)
    assert argv[argv.index("--") + 1:][:4] == [str(interpreter), "-s", "-B", "/code/preview_worker.py"]
    writable = [dst for key, src, dst in binds(argv) if key in ("--bind", "--bind-try", "--bind-fd")]
    assert writable == ["/out"], "каталог установки — только для чтения"


def test_каталог_библиотек_внутри_каталога_установки_отдельно_не_привязывается(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "bwrap_path", lambda: "/usr/bin/bwrap")
    prefix, interpreter, _ = pyenv(tmp_path)
    inner = str(prefix / "lib" / "python3.12" / "site-packages")
    outer = str(tmp_path / "свой-venv" / "lib" / "site-packages")
    argv, _ = command(tmp_path, P.WorkerPython(str(interpreter), str(prefix), None), libs=[inner, outer])
    sources = [src for _, src, _ in binds(argv)]
    assert inner not in sources and outer in sources and sources.count(str(prefix)) == 1


def test_run_sandboxed_берёт_интерпретатор_у_worker_python_родителя(env, monkeypatch, tmp_path):
    prefix, interpreter, _ = pyenv(tmp_path)
    seen = []

    def popen(argv, **kw):
        seen.append(argv)
        raise OSError("настоящий запуск не нужен")

    asked = []
    monkeypatch.setattr(P, "bwrap_path", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(P, "worker_python", lambda home=None, **k: asked.append(home) or P.WorkerPython(str(interpreter), str(prefix), None))
    monkeypatch.setattr(P.subprocess, "Popen", popen)
    with pytest.raises(OSError):
        P.run_sandboxed(str(tmp_path), "preview_worker.py", ["describe"], str(tmp_path), None, 5, env.home)
    (argv,) = seen
    assert asked == [env.home] and str(interpreter) in argv and ("--ro-bind", str(prefix), str(prefix)) in binds(argv)


# ── ответы родителя ─────────────────────────────────────────────
def refuse(monkeypatch, python="/opt/свой/bin/python3"):
    refusal = M.make("preview.python_outside", python=python)
    monkeypatch.setattr(P, "worker_python", lambda home=None, **k: P.WorkerPython(python, None, refusal))
    return refusal


def test_интерпретатор_вне_системных_каталогов_просмотр_отвечает_none_с_пояснением_и_рабочий_процесс_не_запущен(env, worker, monkeypatch):
    refuse(monkeypatch)
    path = env.put("queue", "а.pdf", b"x")
    answer = P.show(env.home, "queue", path)
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.python_outside"
    assert answer["note"]["args"] == {"python": "/opt/свой/bin/python3"}
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "1")
    assert e.value.message.code == "preview.python_outside" and worker.calls == [] and env.cached() == []
    assert not os.path.exists(env.cache), "отказ раньше, чем заводится кэш, замки и слоты"


def test_описание_из_кэша_отдаётся_а_страницу_рисовать_негде_отказ_с_пояснением(env, worker, monkeypatch):
    path = env.put("queue", "а.pdf", b"x")
    assert P.show(env.home, "queue", path)["kind"] == "pages" and worker.ops == ["describe"]
    refuse(monkeypatch)
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "1")
    assert e.value.message.code == "preview.python_outside" and worker.ops == ["describe"]


def test_вложение_письма_при_отказе_по_интерпретатору_не_открывается_с_тем_же_пояснением(env, worker, monkeypatch):
    refuse(monkeypatch)
    path = env.put("queue", "письмо.eml", b"x")
    answer = P.show(env.home, "queue", path, [0])
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.python_outside" and worker.calls == []

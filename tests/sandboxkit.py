"""Общий помощник тестов, которым нужна настоящая песочница bwrap: можно ли на этой машине её создать (FR-61, FR-76).

Программа bwrap на машине ещё не значит песочницу: на обычной Ubuntu 24.04 (не WSL2) и в контейнерах непривилегированные пространства имён могут быть
запрещены, и bwrap, найденный в PATH, отказывает на первом же запуске. Тест с настоящей песочницей в такой среде не красный, а пропущенный, с причиной,
которая называет, что именно не работает: нет программы, она не запускается или создать пространство имён она не может (и что сказал сам bwrap).
Условие одно на все такие тесты: `OK`, `WHY` и `needs_sandbox` отсюда, а не своя проверка `shutil.which("bwrap")` в каждом файле.
"""
import shutil
import subprocess

import pytest

# запуск той же формы, что у просмотра: свои пространства имён, система только для чтения, /proc и /dev; ничего, кроме true, внутри не идёт
PROBE = ["--unshare-all", "--die-with-parent", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
         "--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev", "/usr/bin/true"]
REASON_MAX = 120


def sandbox_state(path=None):
    """(можно ли создать песочницу, причина по-русски, если нельзя). path — какой bwrap пробовать (по умолчанию первый в PATH)."""
    path = path or shutil.which("bwrap")
    if path is None:
        return False, "нет bwrap: песочницы на этой машине нет"
    try:
        r = subprocess.run([path, *PROBE], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"bwrap не запускается: {type(e).__name__}"
    if r.returncode != 0:
        return False, "bwrap не может создать пространство имён: " + r.stderr.decode("utf-8", "replace").strip()[:REASON_MAX]
    return True, ""


OK, WHY = sandbox_state()
needs_sandbox = pytest.mark.skipif(not OK, reason=WHY)

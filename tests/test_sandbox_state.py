"""Тесты с настоящей песочницей пропускаются там, где её создать нельзя, а не краснеют (FR-61, FR-76).

На обычной Ubuntu 24.04 (не WSL2) и в контейнерах непривилегированные пространства имён могут быть запрещены: bwrap есть в PATH, но отказывает. Условие
пропуска общее (`sandboxkit`) и называет причину. Здесь оно проверяется на подставном bwrap, а три файла с настоящей песочницей — настоящим запуском
pytest в дочернем процессе, где первым в PATH стоит подставной bwrap, который не может создать пространство имён: всё, что требует песочницы, пропущено
с названной причиной, остальное идёт.
"""
import os
import re
import subprocess
import sys

import pytest

import sandboxkit as K

HERE = os.path.dirname(os.path.abspath(__file__))
DENIED = "bwrap: No permissions to create new namespace"


def stub(tmp_path, body, mode=0o755):
    folder = tmp_path / "подставной-bwrap"
    folder.mkdir(exist_ok=True)
    path = folder / "bwrap"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_bwrap_нет_причина_называет_что_нет_программы(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    ok, why = K.sandbox_state()
    assert not ok and why.startswith("нет bwrap")


def test_bwrap_создаёт_песочницу_можно_и_причины_нет(tmp_path):
    assert K.sandbox_state(str(stub(tmp_path, "exit 0"))) == (True, "")


def test_bwrap_есть_но_пространство_имён_создать_не_может_причина_называет_это_и_слова_самого_bwrap(tmp_path):
    ok, why = K.sandbox_state(str(stub(tmp_path, f"echo '{DENIED}' >&2\nexit 1")))
    assert not ok and "не может создать пространство имён" in why and DENIED in why


def test_bwrap_не_запускается_причина_называет_вид_сбоя(tmp_path):
    ok, why = K.sandbox_state(str(stub(tmp_path, "exit 0", mode=0o644)))
    assert not ok and "не запускается" in why and "PermissionError" in why


def test_причина_пропуска_не_длиннее_и_без_перевода_строки(tmp_path):
    ok, why = K.sandbox_state(str(stub(tmp_path, "yes 'очень длинная строка причины' | head -n 50 >&2\nexit 1")))
    assert not ok and len(why) <= len("bwrap не может создать пространство имён: ") + K.REASON_MAX


@pytest.mark.parametrize("name", ["test_sandbox.py", "test_preview_sandbox.py", "test_preview_mail_sandbox.py"])
def test_там_где_песочницу_создать_нельзя_тесты_с_настоящим_bwrap_пропущены_с_причиной_а_не_красные(name, tmp_path):
    path = stub(tmp_path, f"echo '{DENIED}' >&2\nexit 1").parent
    env = {**os.environ, "PATH": f"{path}:{os.environ['PATH']}"}
    r = subprocess.run([sys.executable, "-m", "pytest", os.path.join(HERE, name), "-rs", "-p", "no:cacheprovider"], env=env, capture_output=True,
                       text=True, encoding="utf-8", cwd=os.path.dirname(HERE), timeout=600)
    out = r.stdout
    assert r.returncode == 0, out[-3000:]
    assert not re.search(r"\d+ failed|\d+ error", out), out[-3000:]
    assert re.search(r"SKIPPED.*bwrap не может создать пространство имён: " + re.escape(DENIED), out), out[-3000:]
    if name == "test_sandbox.py":
        assert re.search(r"\d+ passed", out), "остальные тесты файла идут: пропущены только те, что требуют песочницы"

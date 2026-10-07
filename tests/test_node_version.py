"""Тесты плагина пропускаются с причиной, называющей найденную и нужную версии node, а не краснеют на старой версии (FR-50).

Тесты плагина `test_dsh_plugin.py` запускают `node --test --test-reporter=tap` и считают прошедшие и упавшие по отчёту вида tap. Ключ `--test-reporter`
есть в node с 18.15 (в ветке 19 — с 19.6); тесты самого плагина используют только `node:test` (`test`, `after`) и `Array.prototype.at`, они идут и на 18.
Раньше проверялось одно присутствие node в PATH: на старой версии тест был красным вместо пропущенного. Здесь причина пропуска проверяется на подставных
ответах `node --version`, а само пропускание — настоящим запуском pytest в дочернем процессе, где первым в PATH стоит подставной node старой версии.
"""
import os
import re
import subprocess
import sys

import pytest

from test_dsh_plugin import NODE_NEED, node_problem

HERE = os.path.dirname(os.path.abspath(__file__))


def said(text, code=0):
    def run(argv, **kw):
        assert argv[1:] == ["--version"] and kw.get("timeout"), argv
        return subprocess.CompletedProcess(argv, code, stdout=text + "\n", stderr="")
    return run


@pytest.mark.parametrize("version", ["v18.15.0", "v18.20.4", "v19.6.0", "v20.0.0", "v22.23.2", "v24.1.0", "v23.0.0-nightly202410010000"])
def test_версия_с_ключом_test_reporter_подходит(version):
    assert node_problem("/usr/bin/node", run=said(version)) is None


@pytest.mark.parametrize("version", ["v16.20.2", "v17.9.1", "v18.0.0", "v18.14.1", "v19.0.0", "v19.5.0"])
def test_версия_без_ключа_test_reporter_причина_называет_найденную_и_нужную(version):
    reason = node_problem("/usr/bin/node", run=said(version))
    assert reason is not None and f"найден node {version[1:]}" in reason and NODE_NEED in reason and "18.15" in reason and "19.6" in reason


def test_нет_node_причина_называет_нужную_версию():
    reason = node_problem(None)
    assert reason.startswith("нет node") and NODE_NEED in reason


@pytest.mark.parametrize("text", ["", "node", "version 22", "v22"])
def test_ответ_не_похожий_на_версию_причина_это_говорит_и_называет_нужную(text):
    reason = node_problem("/usr/bin/node", run=said(text))
    assert "версию" in reason and NODE_NEED in reason


def test_node_не_запускается_причина_называет_вид_сбоя():
    def run(argv, **kw):
        raise PermissionError(13, "нет прав")

    reason = node_problem("/usr/bin/node", run=run)
    assert "не запускается" in reason and "PermissionError" in reason and NODE_NEED in reason


def test_на_старом_node_тесты_плагина_пропущены_с_названными_версиями_а_не_красные(tmp_path):
    folder = tmp_path / "старый-node"
    folder.mkdir()
    stub = folder / "node"
    stub.write_text('#!/bin/sh\n[ "$1" = "--version" ] && { echo v16.20.2; exit 0; }\necho "bad option: $1" >&2\nexit 9\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{folder}:{os.environ['PATH']}"}
    r = subprocess.run([sys.executable, "-m", "pytest", os.path.join(HERE, "test_dsh_plugin.py"), "-rs", "-p", "no:cacheprovider"], env=env,
                       capture_output=True, text=True, encoding="utf-8", cwd=os.path.dirname(HERE), timeout=600)
    out = r.stdout
    assert r.returncode == 0, out[-3000:]
    assert not re.search(r"\d+ failed|\d+ error", out), out[-3000:]
    assert re.search(r"SKIPPED \[\d+\].*найден node 16\.20\.2, нужен node 18\.15", out), out[-3000:]
    assert re.search(r"\d+ passed", out), "остальные тесты файла (без node) идут"

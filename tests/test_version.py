"""Версия записана в одном месте (FR-111): её называют команда, службы в своих ответах, пакет плагина и список изменений.

Раньше службы отвечали своим числом, плагин — своим, а у команды версии не было вовсе. Теперь число стоит в `tools/version.py`,
а остальные места берут его оттуда или сверяются с ним этим тестом.
"""
import json
import os
import re
import subprocess
import sys

import mcp_server
import tokens
import version

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
LITERAL = re.compile(r"""["']version["']\s*:\s*["']\d+\.\d+""")     # версия продукта строкой; номера форматов данных — целые, их не касается


def test_версия_вида_три_числа():
    assert re.fullmatch(r"\d+\.\d+\.\d+", version.VERSION)


def test_команда_называет_версию(tmp_path):
    env = {"HOME": str(tmp_path / "user"), "FLYARCHIVE_HOME": str(tmp_path / "архив"), "PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "flyarchive"), "--version"], env=env, capture_output=True, text=True, encoding="utf-8",
                       timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == f"FlyArchive {version.VERSION}", (r.stdout, r.stderr)
    assert not (tmp_path / "архив").exists(), "вопрос о версии ничего не создаёт"


def test_переходник_mcp_называет_ту_же_версию():
    answer = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, tokens.Client("selftest", "local"))
    assert answer["result"]["serverInfo"] == {"name": "flyarchive", "version": version.VERSION}


def test_описания_служб_поиска_и_документов_называют_ту_же_версию():
    import office_server
    import webui
    assert webui.openapi()["info"]["version"] == version.VERSION
    assert office_server.spec()["info"]["version"] == version.VERSION


def test_в_коде_служб_версия_числом_не_записана_нигде_кроме_одного_места():
    found = []
    for name in sorted(os.listdir(TOOLS)):
        path = os.path.join(TOOLS, name)
        if os.path.isfile(path) and name != "version.py" and (name.endswith(".py") or name == "flyarchive"):
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, 1):
                    if LITERAL.search(line):
                        found.append(f"{name}:{number}")
    assert found == [], "версия записана числом мимо tools/version.py"


def test_пакет_плагина_той_же_версии():
    with open(os.path.join(ROOT, "dsh-plugin", "package.json"), encoding="utf-8") as f:
        assert json.load(f)["version"] == version.VERSION


def test_список_изменений_начинается_с_этой_версии():
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        headings = [line.strip() for line in f if line.startswith("## ")]
    assert headings and version.VERSION in headings[0], headings[:1]

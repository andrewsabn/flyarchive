"""Продукт называется FlyArchive: команда, пакет, службы, адреса и заголовки (FR-102).

Что держат тесты: файл команды `tools/flyarchive`; справка команды начинается с её имени; настройка оболочки называет команду, переменную
и сервер MCP именем проекта; имя сервера MCP — flyarchive, а список инструментов перечислен целиком; пакет плагина и его идентификаторы называются так же
и совпадают между собой; адреса и идентификаторы плагина; службы и таймер, которые пишет установка; приставка контейнеров просмотра.
Имя стоит во многих местах, и связка команды, плагина и служб работает, только когда оно одно везде: тест называет каждое место, а не одно.
"""
import json
import os
import re
import subprocess
import sys

import pytest

import connect
import inbox
import mcp_server
import preview_container
import sandbox
import tokens

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")
PLUGIN = os.path.join(ROOT, "dsh-plugin")
NEW = "flyarchive"
CLI = os.path.join(TOOLS, NEW)


def run_cli(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=120)


def test_файл_команды_называется_flyarchive_и_готового_профиля_dsh_в_репозитории_нет():
    assert os.path.isfile(CLI)
    assert os.path.isfile(os.path.join(TOOLS, "templates", "dsh.patch.yml"))             # профиль DSH из репозитория — шаблон (FR-104)
    assert not os.path.exists(os.path.join(TOOLS, "dsh-" + NEW + ".cordis.patch.yml")), "в репозитории нет готового профиля DSH: он собирается установкой из шаблона"


def test_справка_команды_начинается_с_имени_команды():
    r = run_cli("--help")
    assert r.returncode == 0 and r.stdout.startswith("usage: " + NEW + " "), r.stdout[:80] + r.stderr[-200:]
    sub = run_cli("token", "--help")
    assert sub.returncode == 0 and sub.stdout.startswith("usage: " + NEW + " token"), sub.stdout[:80] + sub.stderr[-200:]


def test_подключение_оболочки_называет_команду_переменную_и_сервер_именем_проекта():
    text = connect.guide("claude", "http://127.0.0.1:8767/mcp", "FLYARCHIVE_TOKEN", remote=False)
    assert NEW + " token add" in text and NEW + " run --name" in text and "FLYARCHIVE_TOKEN" in text
    where, snippet = connect.snippet("claude", "http://127.0.0.1:8767/mcp", "FLYARCHIVE_TOKEN")
    assert list(json.loads(snippet)["mcpServers"]) == [NEW]
    assert connect.snippet("dsh", "http://127.0.0.1:8767/mcp", "FLYARCHIVE_TOKEN")[1].count("mcp-" + NEW) == 1
    assert connect.RESERVED == ("FLYARCHIVE_LOCAL_TOKEN",) and sandbox.BLOCKED_ENV == ("FLYARCHIVE_LOCAL_TOKEN",)
    with pytest.raises(connect.ConnectError):
        connect.snippet("claude", "http://127.0.0.1:8767/mcp", "FLYARCHIVE_LOCAL_TOKEN")


def test_имя_сервера_mcp_flyarchive_и_список_инструментов_назван():
    me = tokens.Client("selftest", "local")
    hello = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, me)
    import version
    assert hello["result"]["serverInfo"] == {"name": NEW, "version": version.VERSION}
    listing = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, me)
    assert [t["name"] for t in listing["result"]["tools"]] == ["search_archive", "read_document", "read_document_rich", "make_landscape",
                                                              "make_diagram", "make_chart", "make_document", "submit_document"]


def test_пакет_плагина_и_его_идентификаторы_называются_именем_проекта_и_совпадают_между_собой():
    with open(os.path.join(PLUGIN, "package.json"), encoding="utf-8") as f:
        pkg = json.load(f)
    assert pkg["name"] == NEW + "-dsh-plugin"
    for lib in ("client.js", "client.messages.js"):
        with open(os.path.join(PLUGIN, "lib", lib), encoding="utf-8") as f:
            assert 'id: "' + pkg["name"] + '"' in f.read(), lib          # имя модуля для браузера совпадает с именем пакета


def test_адреса_и_идентификаторы_плагина_называются_именем_проекта():
    with open(os.path.join(PLUGIN, "lib", "index.js"), encoding="utf-8") as f:
        server = f.read()
    paths = re.findall(r"export const (\w+_PATH) = '([^']+)'", server)
    assert len(paths) == 12 and all(address.startswith("/api/" + NEW + ".") for _, address in paths), paths
    assert "export const name = '" + NEW + "-admin'" in server
    assert "env.FLYARCHIVE_CLI" in server and "env.FLYARCHIVE_CLI || '" + NEW + "'" in server      # запасное значение — имя команды из пути поиска
    with open(os.path.join(PLUGIN, "lib", "client.js"), encoding="utf-8") as f:
        client = f.read()
    assert 'const NAMESPACE = "' + NEW + '"' in client and 'const TAB_KIND = "' + NEW + '-archive"' in client
    assert len(re.findall(r'"api/' + NEW + r'\.[a-z.]+"', client)) == 12
    with open(os.path.join(TOOLS, "templates", "dsh.patch.yml"), encoding="utf-8") as f:
        patch = f.read()
    assert "id: mcp-" + NEW in patch and "serverName: " + NEW in patch and "id: " + NEW + "-admin" in patch and "name: '" + NEW + "-dsh-plugin'" in patch
    assert "FLYARCHIVE_LOCAL_TOKEN" in patch


def test_службы_и_таймер_называются_именем_проекта_в_коде_и_в_том_что_пишет_установка(tmp_path):
    import install
    assert inbox.SERVICE == NEW + "-inbox.service" and inbox.TIMER == NEW + "-inbox.timer"
    assert install.PREFIX == NEW + "-" and (install.INBOX_SERVICE, install.INBOX_TIMER) == (inbox.SERVICE, inbox.TIMER)
    assert not os.path.exists(os.path.join(ROOT, "systemd")), "в репозитории нет готовых файлов служб: их пишет установка из шаблонов"
    shell = tmp_path / "bin"                           # оболочка «есть»: без команды dsh в PATH её служба не пишется (FR-107)
    shell.mkdir()
    (shell / "dsh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (shell / "dsh").chmod(0o755)
    env = {"PATH": f"{shell}:/usr/bin:/bin", "HOME": str(tmp_path / "user"), "FLYARCHIVE_HOME": str(tmp_path / "архив"), "PYTHONIOENCODING": "utf-8",
           "FLYARCHIVE_GATEWAY_BIND": "203.0.113.10", "FLYARCHIVE_PUBLIC_URL": "http://archive.example.com:8780"}      # шлюз задан: пишутся все семь
    r = subprocess.run([sys.executable, CLI, "install", "--dry-run", "--json"], env=env, capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    written = sorted(os.path.basename(f["path"]) for f in json.loads(r.stdout)["files"] if f["path"].endswith((".service", ".timer")))
    assert written == sorted(NEW + "-" + tail for tail in ("dsh.service", "gateway.service", "inbox.service", "inbox.timer", "mcp.service",
                                                            "office.service", "search.service"))


def test_приставка_контейнеров_просмотра_названа_по_проекту():
    assert preview_container.NAME_PREFIX == NEW + "-preview-"

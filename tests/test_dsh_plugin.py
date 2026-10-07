"""Плагин DSH «Архив»: FR-50, вкладка в правой панели — FR-80, FR-81, история пачек на ней — FR-82, просмотр содержимого — FR-83,
раздел настроек по-новому — FR-84, счётчик в заголовке вкладки — FR-85, всплывающее сообщение об окончании пачки — FR-86.
Тесты самого плагина написаны на JavaScript и гоняются отсюда."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "dsh-plugin")
PATCH = os.path.join(ROOT, "tools", "templates", "dsh.patch.yml")          # файл настройки оболочки из репозитория — шаблон: его собирает установка (FR-104)
NODE_NEED = "18.15 или новее (в ветке 19 — 19.6)"      # ключ --test-reporter, по отчёту вида tap которого node_tests считает прошедшие и упавшие


def node_problem(path, run=subprocess.run):
    """Почему тесты плагина нельзя запустить на этом node (причина по-русски, с найденной и нужной версиями) или None, если можно."""
    if path is None:
        return f"нет node: нужен node {NODE_NEED}"
    try:
        said = run([path, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"node не запускается ({type(e).__name__}): нужен node {NODE_NEED}"
    found = re.fullmatch(r"v(\d+)\.(\d+)\.\d+\S*", said)
    if not found:
        return f"node назвал версию непонятно ({said[:30]!r}): нужен node {NODE_NEED}"
    version = (int(found.group(1)), int(found.group(2)))
    if (18, 15) <= version < (19, 0) or version >= (19, 6):
        return None
    return f"найден node {said[1:]}, нужен node {NODE_NEED}: без ключа --test-reporter у него нет отчёта, по которому тест считает результат"


NODE_PROBLEM = node_problem(shutil.which("node"))
needs_node = pytest.mark.skipif(NODE_PROBLEM is not None, reason=NODE_PROBLEM or "node подходит")
# модули основы платформы DSH (таблица PLATFORM_MODULES оболочки): их плагин получает через require без объявления в package.json
DSH_BASELINE = {"react", "react/jsx-runtime", "react-dom", "react-dom/client", "@deepseek-ai/cordis", "@deepseek-ai/dsh-client-store",
                "@deepseek-ai/dsh-client-ui-slots", "@deepseek-ai/dsh-client-ui-primitives", "@deepseek-ai/dsh-client-ui-dockkit"}


def node_tests(pattern):
    r = subprocess.run(["node", "--test", "--test-reporter=tap", *sorted(
        os.path.join(PLUGIN, "test", f) for f in os.listdir(os.path.join(PLUGIN, "test")) if pattern in f)],
        capture_output=True, text=True, encoding="utf-8", cwd=PLUGIN)
    counts = {line.split()[1]: int(line.split()[2]) for line in r.stdout.splitlines()
              if line.startswith("# ") and len(line.split()) == 3 and line.split()[2].isdigit()}
    return r, counts


@needs_node
def test_серверная_половина_плагина_все_тесты_зелёные():
    r, counts = node_tests("host")
    assert r.returncode == 0, r.stdout[-3000:]
    assert counts["fail"] == 0 and counts["skipped"] == 0 and counts["pass"] >= 450


@needs_node
def test_половина_плагина_для_браузера_все_тесты_зелёные():
    r, counts = node_tests("client")
    assert r.returncode == 0, r.stdout[-3000:]
    assert counts["fail"] == 0 and counts["skipped"] == 0 and counts["pass"] >= 440


def test_пакет_плагина_объявлен_так_как_ждёт_dsh():
    with open(os.path.join(PLUGIN, "package.json"), encoding="utf-8") as f:
        pkg = json.load(f)
    assert pkg["name"] == "flyarchive-dsh-plugin" and pkg["type"] == "module" and pkg["private"] is True
    assert pkg["exports"]["."] == "./lib/index.js" and pkg["exports"]["./client"] == "./lib/client.js"
    assert pkg["dsh"]["client"]["platform"] == "web"
    assert "dependencies" not in pkg                    # плагин ничего не тянет из сети
    for rel in ("lib/index.js", "lib/client.js", "lib/client.messages.js"):
        assert os.path.isfile(os.path.join(PLUGIN, rel))
        assert rel in pkg["files"]


def test_пакет_плагина_называет_пакеты_правой_панели():
    """Вкладка Archive встаёт в правую панель: пакет панели назван в dsh.client.inject (читается без node)."""
    with open(os.path.join(PLUGIN, "package.json"), encoding="utf-8") as f:
        pkg = json.load(f)
    inject = pkg["dsh"]["client"]["inject"]
    assert "@deepseek-ai/dsh-client-ui-settings" in inject        # раздел настроек остался
    assert "@deepseek-ai/dsh-client-ui-sidebar-right" in inject   # правая панель и реестр типов вкладок


@needs_node
def test_браузерная_половина_плагина_называет_службы_и_просит_только_модули_основы():
    """Службы названы в inject браузерной половины; загрузка модуля — настоящая, в node."""
    script = """
        globalThis.window = { __ModuleLoader__: { load: (d) => { globalThis.definition = d } } }
        await import(process.argv[1])
        const asked = []
        const exports = globalThis.definition.factory((id) => {
          asked.push(id)
          if (id !== 'react') throw new Error('модуля нет: ' + id)
          return {}
        })
        console.log(JSON.stringify({ inject: exports.inject, asked }))
    """
    r = subprocess.run(["node", "--input-type=module", "-e", script, "file://" + os.path.join(PLUGIN, "lib", "client.js")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-2000:]
    answer = json.loads(r.stdout)
    assert sorted(answer["inject"]) == ["locale", "sidebarRight", "sidebarRightTabs", "slots"]
    # синхронно плагин просит только модули основы DSH; примитивы и react-dom/client нужны сообщению об окончании пачки, их нет — значит
    # плагин работает без сообщения (проверено в client-toast.test.mjs), а не падает
    assert set(answer["asked"]) <= DSH_BASELINE, answer["asked"]
    assert {"@deepseek-ai/dsh-client-ui-primitives", "react-dom/client"} <= set(answer["asked"])


def test_модули_основы_dsh_в_package_json_не_объявляются():
    """`dsh.client.external` — только то, чего нет в основе платформы; примитивы и react-dom/client там уже есть."""
    with open(os.path.join(PLUGIN, "package.json"), encoding="utf-8") as f:
        pkg = json.load(f)
    assert not DSH_BASELINE & set(pkg["dsh"]["client"].get("external", []))


def test_описание_пакета_плагина_английское():
    """Описание плагина DSH показывает в списке плагинов: по-русски его быть не должно."""
    with open(os.path.join(PLUGIN, "package.json"), encoding="utf-8") as f:
        pkg = json.load(f)
    assert pkg["description"] and not re.search("[Ѐ-ӿ]", pkg["description"])


def test_имя_модуля_для_браузера_совпадает_с_именем_пакета():
    with open(os.path.join(PLUGIN, "lib", "client.js"), encoding="utf-8") as f:
        assert 'id: "flyarchive-dsh-plugin"' in f.read()


def test_словарь_сообщений_лежит_соседним_файлом_которого_ждёт_хост_dsh():
    """Хост отдаёт client.js и соседей client.<имя>.js; сосед регистрируется под id пакета с полем chunk."""
    with open(os.path.join(PLUGIN, "lib", "client.messages.js"), encoding="utf-8") as f:
        text = f.read()
    assert 'id: "flyarchive-dsh-plugin"' in text and 'chunk: "client.messages.js"' in text
    with open(os.path.join(PLUGIN, "lib", "client.js"), encoding="utf-8") as f:
        client = f.read()
    assert 'require.async("./client.messages.js")' in client


def test_настройка_dsh_подключает_плагин():
    with open(PATCH, encoding="utf-8") as f:
        text = f.read()
    assert "name: 'flyarchive-dsh-plugin'" in text and "id: flyarchive-admin" in text


def test_в_плагине_нет_секретов_и_адресов_этой_машины():
    for rel in ("lib/index.js", "lib/client.js", "lib/client.messages.js", "package.json"):
        with open(os.path.join(PLUGIN, rel), encoding="utf-8") as f:
            text = f.read()
        assert not re.search(r"/home/[A-Za-z]|/Users/[A-Za-z]|\.ts\.net\b|\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d", text), rel

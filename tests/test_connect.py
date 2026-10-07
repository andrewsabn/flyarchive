"""Одна команда на подключение: flyarchive connect <оболочка> (FR-60, FR-61 п.9).

Печатает готовую настройку MCP для оболочки модели: адрес и имя переменной окружения с токеном.
Самого токена в настройке нет.
"""
import builtins
import inspect
import json
import os
import re
import subprocess
import sys

import pytest

import connect as C
import foreign

FLYARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")
# Файл чужой службы шлюза с внешним адресом в строке окружения: команда подключения его не читает (FR-105).
FOREIGN_UNIT = foreign.UNIT
FOREIGN_ENV = foreign.PREFIX
LOCAL = "http://127.0.0.1:8767/mcp"
REMOTE ="http://archive.example.com:8780/mcp"
TOKEN = re.compile(r"ba_[A-Za-z0-9_-]{20,}")


# ── настройка для каждой оболочки ───────────────────────────────
def test_claude_code_настройка_в_mcp_json_с_переменной_окружения():
    name, text = C.snippet("claude", LOCAL, "FLYARCHIVE_TOKEN")
    cfg = json.loads(text)
    server = cfg["mcpServers"]["flyarchive"]
    assert name == ".mcp.json" and server == {"type": "http", "url": LOCAL, "headers": {"Authorization": "Bearer ${FLYARCHIVE_TOKEN}"}}


def test_codex_настройка_в_config_toml_с_именем_переменной():
    tomllib = pytest.importorskip("tomllib")                    # разбор toml в стандартной библиотеке — с Python 3.11; на 3.10 тест пропускается
    name, text = C.snippet("codex", LOCAL, "FLYARCHIVE_TOKEN")
    cfg = tomllib.loads(text)
    assert name == "~/.codex/config.toml" and cfg == {"mcp_servers": {"flyarchive": {"url": LOCAL, "bearer_token_env_var": "FLYARCHIVE_TOKEN"}}}


def test_opencode_настройка_в_opencode_json():
    name, text = C.snippet("opencode", REMOTE, "MY_TOKEN")
    server = json.loads(text)["mcp"]["flyarchive"]
    assert name == "opencode.json" and server["type"] == "remote" and server["url"] == REMOTE and server["enabled"] is True
    assert server["headers"] == {"Authorization": "Bearer {env:MY_TOKEN}"}


def test_dsh_настройка_накладкой_на_профиль():
    name, text = C.snippet("dsh", LOCAL, "FLYARCHIVE_TOKEN")
    assert name == "cordis.patch.yml" and "name: '@deepseek-ai/dsh-mcp-client'" in text and f"url: {LOCAL}" in text
    assert "transport: streamable-http" in text and "Authorization: !!js '`Bearer ${process.env.FLYARCHIVE_TOKEN}`'" in text


def test_прочие_оболочки_адрес_и_заголовок_словами():
    name, text = C.snippet("generic", REMOTE, "FLYARCHIVE_TOKEN")
    assert name is None and REMOTE in text and "Authorization: Bearer" in text and "$FLYARCHIVE_TOKEN" in text and "curl" in text


@pytest.mark.parametrize("harness", C.HARNESSES)
def test_токена_в_настройке_нет_только_имя_переменной(harness):
    _, text = C.snippet(harness, LOCAL, "FLYARCHIVE_TOKEN")
    assert "FLYARCHIVE_TOKEN" in text and not TOKEN.search(text)


def test_неизвестная_оболочка_и_негодное_имя_переменной_отклоняются():
    with pytest.raises(C.ConnectError):
        C.snippet("emacs", LOCAL, "FLYARCHIVE_TOKEN")
    for bad in ("ba_secret-value", "my token", "", "1TOKEN", "TOKEN;rm", "FLYARCHIVE_LOCAL_TOKEN"):
        with pytest.raises(C.ConnectError):
            C.snippet("claude", LOCAL, bad)


# ── адрес ───────────────────────────────────────────────────────
def test_на_этой_машине_адрес_переходника_на_петле():
    assert C.address(remote=False) == LOCAL


def test_для_другой_машины_адрес_шлюза_из_настройки_public_url(monkeypatch):
    monkeypatch.setattr(C, "PUBLIC_URL", "http://archive.example.com:8780")
    assert C.address(remote=True) == REMOTE
    monkeypatch.setattr(C, "PUBLIC_URL", "http://archive.example.com:8780/")
    assert C.address(remote=True) == REMOTE, "косая черта в конце адреса не удваивается"


def test_шлюза_нет_для_другой_машины_настройку_не_выдаём(monkeypatch):
    monkeypatch.setattr(C, "PUBLIC_URL", "")
    with pytest.raises(C.ConnectError) as e:
        C.address(remote=True)
    assert "шлюз" in str(e.value) and "flyarchive install --gateway auto" in str(e.value)


@pytest.mark.foreign_name
def test_без_настройки_файл_службы_шлюза_не_открывается_и_каталог_служб_не_читается_отказ_тот_же(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "PUBLIC_URL", "")
    with pytest.raises(C.ConnectError) as clean:
        C.address(remote=True)
    unit = tmp_path / FOREIGN_UNIT
    unit.write_text(f"[Service]\nEnvironment={FOREIGN_ENV}GATEWAY_BIND=203.0.113.5:8780\nEnvironment={FOREIGN_ENV}PUBLIC_URL=http://archive.example.com:8780\n",
                    encoding="utf-8")
    monkeypatch.setattr(C, "UNITS", str(tmp_path), raising=False)        # если чтение каталога служб вернут, оно пойдёт сюда
    seen = []

    def spy(owner, name):
        real = getattr(owner, name)

        def wrapper(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)) and str(tmp_path) in os.fsdecode(path):
                seen.append((name, os.fsdecode(path)))
            return real(path, *args, **kwargs)
        monkeypatch.setattr(owner, name, wrapper)

    for owner, name in ((builtins, "open"), (os, "listdir"), (os, "scandir"), (os, "stat")):
        spy(owner, name)
    with pytest.raises(C.ConnectError) as with_file:
        C.address(remote=True)
    assert str(with_file.value) == str(clean.value), "файл службы изменил отказ"
    assert seen == [], f"команда обратилась к файлу или каталогу служб: {seen}"


def test_у_address_нет_параметра_каталога_служб_а_в_модуле_нет_ничего_от_уступки_прежней_службе():
    assert list(inspect.signature(C.address).parameters) == ["remote"]
    for name in ("UNITS", "GATEWAY_UNIT", "_public_from_unit"):
        assert not hasattr(C, name), name


# ── полный текст ────────────────────────────────────────────────
def test_для_этой_машины_сказано_про_токен_и_песочницу():
    text = C.guide("codex", LOCAL, "FLYARCHIVE_TOKEN", remote=False)
    assert "flyarchive token add codex" in text and "export FLYARCHIVE_TOKEN" in text
    assert "flyarchive run --name codex --env FLYARCHIVE_TOKEN" in text and "песочниц" in text
    assert LOCAL in text and "~/.codex/config.toml" in text and not TOKEN.search(text)


def test_для_другой_машины_песочницы_нет_а_про_tailnet_сказано():
    text = C.guide("claude", REMOTE, "FLYARCHIVE_TOKEN", remote=True)
    assert "flyarchive run" not in text and "tailnet" in text and REMOTE in text
    assert "на машине архива" in text and "flyarchive token add" in text


def test_передача_документов_требует_полного_уровня_и_об_этом_сказано():
    text = C.guide("claude", LOCAL, "FLYARCHIVE_TOKEN", remote=False)
    assert "--level full" in text and "submit_document" in text


# ── команда ─────────────────────────────────────────────────────
def run(tmp_path, *args, **extra):
    home = tmp_path / "user"
    (home / "flyarchive").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home), "FLYARCHIVE_HOME": str(home / "flyarchive"), "PYTHONIOENCODING": "utf-8", **extra}
    r = subprocess.run([sys.executable, FLYARCHIVE, "connect", *args], env=env, capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout, r.stderr, home


def test_команда_печатает_настройку_и_не_печатает_токен(tmp_path):
    home = tmp_path / "user"
    (home / "flyarchive").mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "FLYARCHIVE_HOME": str(home / "flyarchive"), "PYTHONIOENCODING": "utf-8"}
    issued = subprocess.run([sys.executable, FLYARCHIVE, "token", "add", "codex"], env=env, capture_output=True, text=True, encoding="utf-8").stdout
    (value,) = TOKEN.findall(issued)
    code, out, err, _ = run(tmp_path, "codex")
    assert code == 0 and err == "" and LOCAL in out and 'bearer_token_env_var = "FLYARCHIVE_TOKEN"' in out
    assert value not in out and not TOKEN.search(out)


def test_команда_для_другой_машины_берёт_адрес_шлюза_из_настройки(tmp_path):
    code, out, _, _ = run(tmp_path, "claude", "--remote", "--env", "ARCHIVE_TOKEN", FLYARCHIVE_PUBLIC_URL="http://archive.example.com:8780")
    assert code == 0 and REMOTE in out and "${ARCHIVE_TOKEN}" in out and "127.0.0.1" not in out


@pytest.mark.foreign_name
def test_команда_для_другой_машины_не_читает_файл_службы_шлюза_отказ_тот_же_что_без_файла(tmp_path):
    clean = run(tmp_path / "без-файла", "claude", "--remote")
    units = tmp_path / "user" / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / FOREIGN_UNIT).write_text(f"Environment={FOREIGN_ENV}PUBLIC_URL=http://archive.example.com:8780\n", encoding="utf-8")
    code, out, err, _ = run(tmp_path, "claude", "--remote")
    assert (code, out, err) == clean[:3] and code == 1 and out == "" and "шлюз" in err and REMOTE not in err


def test_команда_без_шлюза_для_другой_машины_ошибка(tmp_path):
    code, out, err, _ = run(tmp_path, "claude", "--remote")
    assert code == 1 and out == "" and "шлюз" in err


@pytest.mark.parametrize("args", [("emacs",), (), ("claude", "--env", "ba_value")])
def test_команда_негодный_вызов(tmp_path, args):
    code, out, _, _ = run(tmp_path, *args)
    assert code != 0 and out == ""


# ── Open WebUI убран из стенда (FR-62) ──────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_в_описании_устройства_open_webui_нет():
    with open(os.path.join(ROOT, "docs", "architecture.md"), encoding="utf-8") as f:
        text = f.read()
    assert "Open WebUI" not in text and "8090" not in text
    assert "8780" in text and "входящая папка" in text.lower()          # зато есть шлюз и пополнение


def test_скрипты_подключения_open_webui_убраны_из_рабочих():
    """Старая связка не лежит ни среди рабочих сценариев, ни в открытой части вовсе."""
    tools = os.path.join(ROOT, "tools")
    for name in ("connect_tool_server.py", "connect_office_server.py", "setup_models.py", "owui_run.sh"):
        assert not os.path.exists(os.path.join(tools, name)), name
        assert not os.path.exists(os.path.join(tools, "legacy", "openwebui", name)), name
    assert not os.path.exists(os.path.join(tools, "legacy"))


def test_службы_автозапуска_не_упоминают_open_webui(tmp_path):
    """Готовых файлов служб в репозитории нет (FR-104): проверяются шаблоны и всё, что записала бы установка (пробный прогон с задвинутым шлюзом)."""
    templates = os.path.join(ROOT, "tools", "templates")
    for name in os.listdir(templates):
        with open(os.path.join(templates, name), encoding="utf-8") as f:
            assert "webui" not in f.read().lower().replace("webui.py", ""), name
    shell = tmp_path / "bin"                                         # оболочка в PATH есть: без неё служба оболочки не ставится (FR-107), а здесь нужны все семь
    shell.mkdir()
    (shell / "dsh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (shell / "dsh").chmod(0o755)
    env = {"PATH": f"{shell}:/usr/bin:/bin", "HOME": str(tmp_path / "user"), "FLYARCHIVE_HOME": str(tmp_path / "архив"), "PYTHONIOENCODING": "utf-8",
           "FLYARCHIVE_GATEWAY_BIND": "203.0.113.10", "FLYARCHIVE_PUBLIC_URL": "http://archive.example.com:8780"}
    r = subprocess.run([sys.executable, FLYARCHIVE, "install", "--dry-run", "--json"], env=env, capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
    assert r.returncode == 0, r.stderr
    units = [f for f in json.loads(r.stdout)["files"] if f["path"].endswith((".service", ".timer"))]
    assert len(units) == 7
    for unit in units:
        assert "webui" not in unit["content"].lower().replace("webui.py", ""), unit["path"]

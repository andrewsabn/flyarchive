"""Одна команда на подключение оболочки модели к архиву (FR-60).

    flyarchive connect claude | codex | opencode | dsh | generic [--remote] [--env ИМЯ]

Печатает готовую настройку MCP: адрес и имя переменной окружения, из которой оболочка возьмёт токен.
Самого токена в настройке нет и быть не должно: настройки попадают в репозитории и резервные копии.
"""
import json
import re

import settings

_SETTINGS = settings.startup()
HARNESSES = ("claude", "codex", "opencode", "dsh", "generic")
# адрес переходника для клиентов: настройка mcp_url; пока её никто не задал, он выводится из порта переходника
# (смена mcp_port не оставляет прежний адрес)
LOCAL_URL = _SETTINGS["mcp_url"] if _SETTINGS.source("mcp_url") != "default" else f"http://127.0.0.1:{_SETTINGS['mcp_port']}/mcp"
PUBLIC_URL = _SETTINGS["public_url"]      # внешний адрес шлюза в tailnet; пусто — не задан
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
RESERVED = ("FLYARCHIVE_LOCAL_TOKEN",)      # служебный токен локального DSH другим оболочкам не отдаётся


class ConnectError(Exception):
    pass


def address(remote):
    """Адрес MCP: на этой машине — переходник на петле, с другой — шлюз в tailnet (его внешний адрес — настройка public_url)."""
    if not remote:
        return LOCAL_URL
    if not PUBLIC_URL:
        raise ConnectError("шлюз в tailnet не поставлен: с другой машины архив не виден. "
                           "Поставь его: flyarchive install --gateway auto (нужен tailscale)")
    return PUBLIC_URL.rstrip("/") + "/mcp"


def snippet(harness, url, env, name="flyarchive"):
    """(в какой файл, текст настройки). Токен в текст не попадает — только имя переменной окружения."""
    if harness not in HARNESSES:
        raise ConnectError(f"оболочка: {', '.join(HARNESSES)}")
    if not isinstance(env, str) or not ENV_NAME.fullmatch(env) or env in RESERVED:
        raise ConnectError("--env — имя переменной окружения заглавными буквами, например FLYARCHIVE_TOKEN; не само значение токена")
    if harness == "claude":
        cfg = {"mcpServers": {name: {"type": "http", "url": url, "headers": {"Authorization": "Bearer ${" + env + "}"}}}}
        return ".mcp.json", json.dumps(cfg, ensure_ascii=False, indent=2)
    if harness == "codex":
        return "~/.codex/config.toml", f'[mcp_servers.{name}]\nurl = "{url}"\nbearer_token_env_var = "{env}"'
    if harness == "opencode":
        cfg = {"mcp": {name: {"type": "remote", "url": url, "enabled": True, "headers": {"Authorization": "Bearer {env:" + env + "}"}}}}
        return "opencode.json", json.dumps(cfg, ensure_ascii=False, indent=2)
    if harness == "dsh":
        return "cordis.patch.yml", "\n".join([
            "- insert:",
            f"    - id: mcp-{name}",
            "      name: '@deepseek-ai/dsh-mcp-client'",
            "      config:",
            f"        serverName: {name}",
            "        transport: streamable-http",
            f"        url: {url}",
            "        headers:",
            "          Authorization: !!js '`Bearer ${process.env." + env + "}`'",
            "        toolCallTimeoutMs: 300000"])
    return None, "\n".join([
        f"Адрес MCP (streamable HTTP): {url}",
        f"Заголовок каждого запроса:  Authorization: Bearer <значение ${env}>",
        "Проверка:",
        f"  curl -s -X POST -H \"Authorization: Bearer ${env}\" -H 'Content-Type: application/json' \\",
        f"    -d '{{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}}' {url}"])


def guide(harness, url, env, remote):
    """Полный текст для человека: токен, настройка, запуск."""
    where, text = snippet(harness, url, env)
    client = harness if harness != "generic" else "<имя клиента>"
    side = "на машине архива" if remote else "здесь"
    lines = [
        f"Подключение оболочки «{harness}» к архиву" + (" с другой машины tailnet" if remote else " на этой машине") + ".",
        "",
        f"1. Токен. Выпусти его {side}; значение показывается один раз:",
        f"     flyarchive token add {client}                 # поиск и чтение",
        f"     flyarchive token add {client} --level full    # ещё создание файлов и передача документов (submit_document)",
        f"   Положи значение в переменную окружения на машине оболочки, не в файлы настройки:",
        f"     export {env}=…",
        "",
        "2. Настройка MCP" + (f" — в файл {where}:" if where else ":"),
        "",
        text,
        ""]
    if remote:
        lines += ["3. Машина оболочки должна быть в том же tailnet. С неё виден только MCP; ссылки на документы из ответов",
                  "   подписаны и открываются в браузере без токена в течение суток."]
    else:
        command = {"claude": "claude", "codex": "codex", "opencode": "opencode", "dsh": "dsh"}.get(harness, "<команда оболочки>")
        lines += ["3. Запуск. Оболочку внешней модели на этой машине запускай в песочнице: файлов архива она не увидит,",
                  "   только MCP:",
                  f"     flyarchive run --name {client} --env {env} --dir <рабочий каталог> -- {command}",
                  "   Если программа оболочки стоит в домашнем каталоге, покажи её ключом --ro, например --ro ~/.npm-global."]
    return "\n".join(lines)

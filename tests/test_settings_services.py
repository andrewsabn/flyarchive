"""Адреса служб, порты и модели берутся из настроек (FR-97).

Главное, что держат тесты: порты и адреса служб (поиск, документы, MCP, шлюз), служба векторов, локальная и запасная модели не записаны в коде
под одну машину, а берутся из `tools/settings.py`; каждая настройка меняется и это видно в поведении: служба слушает новый порт, сосед
обращается по новому адресу, запрос к службе векторов несёт новые адрес, модель и видеокарту. Умолчания не привязаны к машине: имя локальной
модели пусто — тогда проверка моделью и описание изображений недоступны с понятной причиной и без запроса с пустым именем; запасная модель
без заданного адреса или имени не используется вовсе, даже когда она включена флажком. Ключ модели не попадает в сообщения и выводы,
файл ключа читается только когда он задан настройкой. Переменные окружения другой программы, её переменная ключа и файл её службы шлюза
не действуют (FR-105).

Дочерние процессы идут через обвязку `test_settings_paths.py`: пустой `HOME`, крюк аудита, явное окружение. Крюк не даёт ни занять, ни вызвать
настоящие порты служб, модели и ollama (8765–8767, 8780, 3080, 8080, 11434): подставные службы и соседи — на случайных портах.
Этих же портов не касаются и тесты внутри процесса: сокет к ним отвечает отказом теста, а не соединением.
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import auth as A
import llm_check as L
import messages as M
import settings as S
import vision as V
import webui as W
import foreign
from test_settings_paths import CLI, LEAK, lab, untouched, watch    # noqa: F401  обвязка дочерних процессов

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
D = {key: spec.default for key, spec in S.SCHEMA.items()}            # умолчания схемы: единственное место, где лежат порты и адреса
REAL_PORTS = {8765, 8766, 8767, 8780, 3080, 8080, 11434}
FOREIGN_PREFIX = foreign.PREFIX                                        # приставка переменных окружения другой программы
FOREIGN_KEY_VARIABLE = foreign.KEY                                    # переменная значения ключа, которую знает чужая библиотека, но не проект
KEY = "KEYq7Zk9xW3mR5"                                               # метка ключа: нигде в сообщениях и выводах её быть не должно
LOCAL_MODEL, CLOUD_MODEL = "локальная-образец", "облачная-образец"


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch, tmp_path):
    """Сокет к настоящему порту службы, модели или ollama — отказ теста: ни одно обращение к ним не доходит до сети.

    Домашний каталог процесса на время теста — временный: код, который по старинке полез бы за ключом в ~, найдёт там пусто, а не ключ владельца."""
    monkeypatch.setenv("HOME", str(tmp_path / "дом-теста"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "дом-теста"))
    real = socket.socket.connect

    def connect(self, address):
        if isinstance(address, tuple) and len(address) > 1 and address[1] in REAL_PORTS:
            raise AssertionError(f"тест обратился к настоящему порту службы {address[1]}")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def archive(base, **file_settings):
    """Каталог архива во временном каталоге; если заданы настройки — с файлом settings.json."""
    arch = base / "архив"
    arch.mkdir(parents=True, exist_ok=True)
    if file_settings:
        path = arch / "settings.json"
        path.write_text(json.dumps(file_settings), encoding="utf-8")
        path.chmod(0o600)
    return arch


def text_of(value):
    return ",".join(value) if isinstance(value, list) else ("true" if value is True else "false" if value is False else str(value))


LAYERS = ("env", "file")


def put(base, layer, **values):
    """Окружение процесса для настроек values, заданных слоем: переменной FLYARCHIVE_* или файлом настроек."""
    env = {"FLYARCHIVE_HOME": str(archive(base, **(values if layer == "file" else {})))}
    if layer == "env":
        env.update({S.env_name(key): text_of(value) for key, value in values.items()})
    return env


# ── подставные службы в дочернем процессе ───────────────────────
PRELUDE = '''
import http.server, json, os, threading, urllib.error, urllib.request


class Stub:
    """Подставной сосед или служба: отвечает заданным JSON и запоминает запросы. Порт случайный."""

    def __init__(self, reply=None, status=200):
        self.requests, self.reply, self.status = [], {"ok": True} if reply is None else reply, status
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def serve(self):
                size = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(size).decode("utf-8", "replace") if size else ""
                outer.requests.append({"method": self.command, "path": self.path, "body": body,
                                       "headers": {k.lower(): v for k, v in self.headers.items()}})
                data = json.dumps(outer.reply).encode("utf-8")
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = serve

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def paths(self):
        return [r["path"].split("?")[0] for r in self.requests]


def status_of(url, headers=None, data=None, method=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return reply.status
    except urllib.error.HTTPError as e:
        return e.code


CASE = json.loads(os.environ.get("CASE", "{}"))
'''

MCP_UPSTREAM = '''
search, office = Stub({"results": []}), Stub({"text": "документ"})
os.environ["FLYARCHIVE_SEARCH_PORT"], os.environ["FLYARCHIVE_OFFICE_PORT"] = str(search.port), str(office.port)
import mcp_server
for name, args in (("search_archive", {"q": "образец"}), ("read_document", {"path": "а.txt"}), ("make_document", {"title": "т"})):
    mcp_server.call_upstream(mcp_server.BY_NAME[name], args, token="sample-token")
result["search"], result["office"] = search.paths, office.paths
result["token"] = search.requests[0]["headers"].get("authorization")
'''

GATEWAY_RELAY = '''
mcp, search, office = Stub(), Stub(), Stub()
os.environ["FLYARCHIVE_MCP_PORT"], os.environ["FLYARCHIVE_SEARCH_PORT"], os.environ["FLYARCHIVE_OFFICE_PORT"] = (str(mcp.port), str(search.port),
                                                                                                                   str(office.port))
import gateway, journal
handler = gateway.make_handler(gateway.BACKENDS, "http://archive.example:8780", [], journal.Journal())
server = gateway.Server(("127.0.0.1", 0), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base, host = "http://127.0.0.1:%d" % server.server_address[1], {"Host": "archive.example:8780"}
result["status"] = [status_of(base + "/doc?p=1", host), status_of(base + "/file?n=2", host),
                    status_of(base + "/mcp", {**host, "Content-Type": "application/json"}, b"{}")]
result["seen"] = {"mcp": mcp.paths, "search": search.paths, "office": office.paths}
'''

EMBED_BODIES = '''
embed = Stub({"embeddings": [[0.5, 0.25]]})
os.environ["FLYARCHIVE_EMBED_URL"] = "http://127.0.0.1:%d%s" % (embed.port, CASE["path"])
os.environ["FLYARCHIVE_EMBED_MODEL"], os.environ["FLYARCHIVE_EMBED_GPU"] = CASE["model"], CASE["gpu"]
import index_more, ingest, search
search.embed("запрос")
index_more.embed(["а"])
ingest._default_embed(["б"])
result["requests"] = [{"path": r["path"], "body": json.loads(r["body"])} for r in embed.requests]
'''

KEY_BY_DEFAULT = '''
import llm_check
checker = llm_check.from_env()
result["key"], result["name"] = checker.local.key, checker.local.name
'''


def run_snippet(lab, base, code, env):
    path = base / "snippet.py"
    path.write_text(PRELUDE + code, encoding="utf-8")
    r = lab("snippet", str(path), env=env)
    assert r.data.get("blocked", []) == [], f"код обратился к настоящему порту службы: {r.data.get('blocked')}"
    assert r.code == 0, r.err[-1500:]
    return r


# ══ порты и адреса служб ═════════════════════════════════════════
def expected_ports(search=D["search_port"], office=D["office_port"], mcp=D["mcp_port"]):
    local = "http://127.0.0.1:%d"
    return {"webui.PORT": search, "office_server.PORT": office, "mcp_server.PORT": mcp,
            "mcp_server.SEARCH": local % search, "mcp_server.OFFICE": local % office,
            "gateway.BACKENDS": {"/mcp": local % mcp, "/doc": local % search, "/file": local % office},
            "connect.LOCAL_URL": local % mcp + "/mcp"}


def test_значения_модулей_без_настроек_берутся_из_умолчаний_схемы(lab, tmp_path):
    want = expected_ports()
    r = lab("attrs", env=put(tmp_path, "env"), attrs=list(want))
    assert r.code == 0, r.err
    assert r.data["values"] == want
    untouched(r)


@pytest.mark.parametrize("layer", LAYERS)
@pytest.mark.parametrize("key, argument", [("search_port", "search"), ("office_port", "office"), ("mcp_port", "mcp")])
def test_порт_из_настройки_доходит_до_значений_всех_модулей(lab, tmp_path, layer, key, argument):
    port = free_port()
    want = expected_ports(**{argument: port})
    r = lab("attrs", env=put(tmp_path, layer, **{key: port}), attrs=list(want))
    assert r.code == 0, r.err
    assert r.data["values"] == want, "порт изменён настройкой: служба и все соседи берут новый, прежний нигде не остаётся"
    untouched(r)


def test_адрес_mcp_для_клиентов_из_настройки_или_выводится_из_порта_переходника(lab, tmp_path):
    port = free_port()
    cases = [({"mcp_url": "https://archive.example/mcp"}, "https://archive.example/mcp"),
             ({"mcp_port": port}, f"http://127.0.0.1:{port}/mcp"),
             ({"mcp_port": port, "mcp_url": "https://archive.example/mcp"}, "https://archive.example/mcp")]
    for number, (values, url) in enumerate(cases):
        r = lab("attrs", env=put(tmp_path / str(number), "env", **values), attrs=["connect.LOCAL_URL"])
        assert r.code == 0, r.err
        assert r.data["values"] == {"connect.LOCAL_URL": url}, values


# ── служба слушает порт и адрес из настроек ─────────────────────
SERVICES = [("office_server.py", "office_port"), ("webui.py", "search_port"), ("mcp_server.py", "mcp_port")]


@pytest.mark.parametrize("script, key", SERVICES)
def test_служба_без_настроек_слушает_петлю_и_свой_порт_из_схемы(lab, tmp_path, script, key):
    r = lab("entry", script, env=put(tmp_path, "env"))
    assert r.code == 99, r.err[-300:]
    assert r.data["bound"][0] == ["127.0.0.1", D[key]] and r.data["blocked"] == []


@pytest.mark.parametrize("layer", LAYERS)
@pytest.mark.parametrize("script, key", SERVICES)
def test_служба_слушает_порт_из_настроек_и_только_на_петле(lab, tmp_path, script, key, layer):
    port = free_port()
    r = lab("entry", script, env=put(tmp_path, layer, **{key: port}))
    assert r.code == 99, r.err[-300:]
    assert r.data["bound"] == [["127.0.0.1", port]], "служба занимает порт из настроек, адрес — петля"
    assert r.data["blocked"] == []


# Адрес прослушивания не настраивается: сервер поиска и сервер документов, выставленные в сеть напрямую, обходили бы шлюз:
# правило «с других машин токен действует только в MCP, документ — только по подписанной ссылке» держит заголовок, который ставит шлюз.
ALL_ADDRESSES = ["0.0.0.0", "192.0.2.7"]


@pytest.mark.parametrize("address", ALL_ADDRESSES)
@pytest.mark.parametrize("script, key", SERVICES)
def test_службы_не_слушают_ничего_кроме_петли_при_любых_переменных_адреса(lab, tmp_path, script, key, address):
    env = {**put(tmp_path, "env"), "FLYARCHIVE_BIND_HOST": address}
    r = lab("entry", script, env=env)
    assert r.code == 99, r.err[-300:]
    assert r.data["bound"] == [["127.0.0.1", D[key]]], "переменные «все адреса» и чужой адрес службы ничего не меняют"
    r = lab("attrs", env=env, attrs=["webui.HOST", "office_server.HOST", "mcp_server.HOST"])
    assert r.code == 0, r.err
    assert r.data["values"] == {"webui.HOST": "127.0.0.1", "office_server.HOST": "127.0.0.1", "mcp_server.HOST": "127.0.0.1"}


@pytest.mark.parametrize("script", [s for s, _ in SERVICES])
@pytest.mark.parametrize("address", ["0.0.0.0", "127.0.0.2"])
def test_ключ_bind_host_в_файле_настроек_службу_не_запускает_неизвестная_настройка(lab, tmp_path, script, address):
    r = lab("entry", script, env=put(tmp_path, "file", bind_host=address))
    assert r.code == 2 and "bound" not in r.data, "служба с таким файлом не занимает ничего"
    assert r.err.startswith("настройки: ") and "bind_host" in r.err and "Traceback" not in r.err and len(r.err.strip().splitlines()) == 1
    assert address not in r.err


def test_настройки_адреса_прослушивания_у_служб_нет_но_у_шлюза_gateway_bind_остаётся():
    assert "bind_host" not in S.SCHEMA and "gateway_bind" in S.SCHEMA


@pytest.mark.parametrize("script, key, probe", [("office_server.py", "office_port", "/openapi.json"), ("webui.py", "search_port", "/"),
                                                ("mcp_server.py", "mcp_port", "/mcp")])
def test_служба_в_самом_деле_отвечает_на_порту_из_настройки(lab, tmp_path, script, key, probe):
    port = free_port()
    env = put(tmp_path, "env", **{key: port})
    env["WATCH_LIVE"] = json.dumps([{"path": probe}])
    r = lab("live", script, str(port), env=env)
    assert r.code == 0, r.err[-300:]
    (reply,) = r.data["replies"]
    assert reply["status"] in (200, 401, 403, 404, 405), f"на порту {port} никто не ответил: {reply}"
    assert r.data["blocked"] == []


# ── соседи обращаются по адресам из настроек ────────────────────
def test_переходник_mcp_ходит_к_поиску_и_документам_по_портам_из_настроек(lab, tmp_path):
    r = run_snippet(lab, tmp_path, MCP_UPSTREAM, put(tmp_path, "env"))
    assert r.data["search"] == ["/api/search"] and r.data["office"] == ["/read", "/document"]
    assert r.data["token"] == "Bearer sample-token", "дальше уходит токен самого клиента"


def test_шлюз_передаёт_запросы_службам_по_портам_из_настроек(lab, tmp_path):
    r = run_snippet(lab, tmp_path, GATEWAY_RELAY, put(tmp_path, "env"))
    assert r.data["status"] == [200, 200, 200]
    assert r.data["seen"] == {"mcp": ["/mcp"], "search": ["/doc"], "office": ["/file"]}


@pytest.mark.parametrize("layer", LAYERS)
def test_команда_печатает_ссылку_входа_на_порт_поиска_из_настройки(lab, tmp_path, layer):
    port = free_port()
    r = lab("cli", "open", env=put(tmp_path, layer, search_port=port))
    assert r.code == 0, r.err
    assert f"http://127.0.0.1:{port}/login?c=" in r.out
    assert r.data["blocked"] == []
    untouched(r)


def test_команда_без_настроек_печатает_ссылку_входа_на_порт_из_схемы(lab, tmp_path):
    r = lab("cli", "open", env=put(tmp_path, "env"))
    assert r.code == 0, r.err
    assert f"http://127.0.0.1:{D['search_port']}/login?c=" in r.out


def connect_url(out):
    return re.search(r"Адрес MCP \(streamable HTTP\): (\S+)", out).group(1)


def test_команда_connect_называет_адрес_mcp_из_настроек(lab, tmp_path):
    port = free_port()
    cases = [({}, f"http://127.0.0.1:{D['mcp_port']}/mcp"), ({"mcp_port": port}, f"http://127.0.0.1:{port}/mcp"),
             ({"mcp_url": "https://archive.example/mcp"}, "https://archive.example/mcp")]
    for number, (values, url) in enumerate(cases):
        r = lab("cli", "connect", "generic", env=put(tmp_path / str(number), "env", **values))
        assert r.code == 0, r.err
        assert connect_url(r.out) == url, values


@pytest.mark.parametrize("layer", LAYERS)
def test_команда_connect_для_другой_машины_берёт_внешний_адрес_из_настроек_а_файл_службы_не_читает(lab, tmp_path, layer):
    r = lab("cli", "connect", "generic", "--remote", env=put(tmp_path, layer, public_url="https://archive.example:8443/"))
    assert r.code == 0, r.err
    assert connect_url(r.out) == "https://archive.example:8443/mcp"
    untouched(r, why="файл службы шлюза при заданном public_url не нужен")


FOREIGN_UNIT = foreign.UNIT                    # служба шлюза другой программы: адрес в ней — строка окружения с чужой приставкой


def unit_in_default_dir(lab, name, prefix, public):
    """Файл службы шлюза в каталоге служб по умолчанию (в домашнем каталоге дочернего процесса) с внешним адресом в строке окружения."""
    units = lab.home / ".config" / "systemd" / "user"
    units.mkdir(parents=True, exist_ok=True)
    (units / name).write_text(f"[Service]\nEnvironment={prefix}GATEWAY_BIND=203.0.113.5:8780\nEnvironment={prefix}PUBLIC_URL={public}\n", encoding="utf-8")
    return units


@pytest.mark.foreign_name
@pytest.mark.parametrize("name, prefix", [(FOREIGN_UNIT, FOREIGN_PREFIX), ("flyarchive-gateway.service", "FLYARCHIVE_")], ids=["чужая служба", "служба установки"])
def test_команда_connect_для_другой_машины_без_public_url_отказывает_как_без_файла_а_файл_службы_не_открывает(lab, tmp_path, name, prefix):
    env = put(tmp_path, "env")
    clean = lab("cli", "connect", "generic", "--remote", env=env)
    assert clean.code == 1 and clean.out == "" and "шлюз" in clean.err and "flyarchive install --gateway auto" in clean.err
    units = unit_in_default_dir(lab, name, prefix, "http://archive.tail.example:8780")
    r = lab("cli", "connect", "generic", "--remote", env=env)
    assert (r.code, r.out, r.err) == (clean.code, clean.out, clean.err), "файл службы шлюза изменил ответ команды"
    assert r.touched == [], f"команда обратилась к каталогу служб или к файлу службы: {r.touched}"
    assert (units / name).is_file(), "образец файла службы на месте: тест что-то проверяет"


@pytest.mark.foreign_name
def test_адрес_из_настройки_берётся_и_когда_рядом_лежит_файл_службы_с_другим_адресом(lab, tmp_path):
    unit_in_default_dir(lab, FOREIGN_UNIT, FOREIGN_PREFIX, "http://старый.example:8780")
    r = lab("cli", "connect", "generic", "--remote", env=put(tmp_path, "env", public_url="https://archive.example"))
    assert r.code == 0, r.err
    assert connect_url(r.out) == "https://archive.example/mcp"
    assert r.touched == [], r.touched


def test_ни_в_настройках_адреса_нет_команда_отказывает_и_называет_установку_шлюза(lab, tmp_path):
    r = lab("cli", "connect", "generic", "--remote", env=put(tmp_path, "env"))
    assert r.code == 1 and r.out == "" and "шлюз" in r.err and "flyarchive install --gateway auto" in r.err
    untouched(r)


# ── шлюз ────────────────────────────────────────────────────────
@pytest.mark.parametrize("layer", LAYERS)
@pytest.mark.parametrize("form", ["host", "host:port"])
def test_шлюз_слушает_адрес_и_порт_из_настроек(lab, tmp_path, layer, form):
    common, own = free_port(), free_port()
    values = {"gateway_bind": "127.0.0.3" if form == "host" else f"127.0.0.3:{own}", "gateway_port": common,
              "public_url": f"http://archive.example:{common}"}
    r = lab("entry", "gateway.py", env=put(tmp_path, layer, **values))
    assert r.code == 99, r.err[-300:]
    assert r.data["bound"] == [["127.0.0.3", common if form == "host" else own]], "порт — из gateway_port, а в виде «адрес:порт» — свой"
    assert r.data["blocked"] == []


@pytest.mark.parametrize("values", [{}, {"gateway_bind": "127.0.0.3"}, {"public_url": "http://archive.example:8780"}, {"gateway_bind": "127.0.0.3", "public_url": ""}])
def test_шлюз_без_адреса_или_внешнего_адреса_не_стартует_и_ничего_не_слушает(lab, tmp_path, values):
    r = lab("entry", "gateway.py", env=put(tmp_path, "env", **values))
    assert r.code == 1 and "bound" not in r.data, "шлюз не занял адрес по умолчанию"
    assert r.err.startswith("шлюз не настроен") and "gateway_bind" in r.err and "public_url" in r.err
    assert "Traceback" not in r.err and len(r.err.strip().splitlines()) == 1


@pytest.mark.parametrize("bind", ["0.0.0.0", "0.0.0.0:8780", ":8780", "::"])
def test_шлюз_не_слушает_все_адреса(lab, tmp_path, bind):
    r = lab("entry", "gateway.py", env=put(tmp_path, "env", gateway_bind=bind, public_url="http://archive.example:8780"))
    assert r.code == 1 and "bound" not in r.data and "tailnet" in r.err and "Traceback" not in r.err


def test_шлюз_принимает_имена_из_public_url_и_public_hosts_и_свой_адрес_а_чужие_нет(lab, tmp_path):
    port = free_port()
    env = put(tmp_path, "env", gateway_bind="127.0.0.1", gateway_port=port, public_url=f"http://archive.example:{port}", public_hosts=["alias.example"])
    hosts = [f"archive.example:{port}", "alias.example", f"127.0.0.1:{port}", "evil.example"]
    env["WATCH_LIVE"] = json.dumps([{"path": "/nothing", "headers": {"Host": h}} for h in hosts])
    r = lab("live", "gateway.py", str(port), env=env)
    assert r.code == 0, r.err[-300:]
    assert [reply["status"] for reply in r.data["replies"]] == [404, 404, 404, 403], r.data["replies"]
    assert r.data["blocked"] == []


# ══ проверка заголовка Host ══════════════════════════════════════
def guard_with(base, monkeypatch, layer, hosts):
    arch = archive(base, **({"hosts": hosts} if layer == "file" else {}))
    if layer == "env":
        monkeypatch.setenv("FLYARCHIVE_HOSTS", ",".join(hosts))
    return A.Guard("search", home=str(arch))


@pytest.mark.parametrize("layer", LAYERS)
def test_имя_из_настройки_hosts_принимается_чужое_нет(tmp_path, monkeypatch, layer):
    g = guard_with(tmp_path, monkeypatch, layer, ["archive.example", "Alias.Example"])
    assert g.host_ok("archive.example:8765") and g.host_ok("ARCHIVE.example") and g.host_ok("alias.example")
    assert g.host_ok("127.0.0.1:8765") and g.host_ok("localhost"), "петля принимается всегда"
    for foreign in ("evil.example", "archive.example.evil.example", "evil.example:8765", None, "", "other.example"):
        assert g.host_ok(foreign) is False, foreign


def test_пустая_настройка_hosts_пускает_только_петлю(tmp_path, monkeypatch):
    monkeypatch.delenv("FLYARCHIVE_HOSTS", raising=False)
    g = A.Guard("search", home=str(archive(tmp_path)))
    assert g.hosts == set(A.LOOPBACK) and not g.host_ok("archive.example")
    g = A.Guard("search", home=str(archive(tmp_path / "2", hosts=[])))
    assert g.hosts == set(A.LOOPBACK)


@pytest.mark.foreign_name
def test_имя_из_чужой_переменной_hosts_не_принимается_а_своя_переменная_сильнее_неё(tmp_path, monkeypatch):
    monkeypatch.delenv("FLYARCHIVE_HOSTS", raising=False)
    monkeypatch.setenv(FOREIGN_PREFIX + "HOSTS", "archive.example")
    g = A.Guard("search", home=str(archive(tmp_path)))
    assert g.hosts == set(A.LOOPBACK) and not g.host_ok("archive.example"), "имя из переменной с чужой приставкой принято"
    monkeypatch.setenv("FLYARCHIVE_HOSTS", "alias.example")
    g = A.Guard("search", home=str(archive(tmp_path / "2")))
    assert g.host_ok("alias.example") and not g.host_ok("archive.example")


def test_имена_заданные_вызывающим_сильнее_настроек(tmp_path):
    g = A.Guard("search", home=str(archive(tmp_path, hosts=["archive.example"])), hosts=["other.example"])
    assert g.host_ok("other.example") and not g.host_ok("archive.example")


def test_служба_отвечает_403_на_чужое_имя_и_принимает_имя_из_настройки(serve, fetch, tmp_path, monkeypatch):
    guard = A.Guard("search", home=str(archive(tmp_path, hosts=["archive.example"])))
    token = guard.store.issue("образец", "read")
    monkeypatch.setattr(W.Handler, "guard", guard)
    monkeypatch.setattr(W.S, "search", lambda *a, **k: [])
    url = serve(W.Server, W.Handler)
    answers = {host: fetch(url + "/api/search?q=x", headers={"Host": host, "Authorization": "Bearer " + token})[0]
               for host in ("archive.example", "evil.example", "127.0.0.1")}
    assert answers == {"archive.example": 200, "evil.example": 403, "127.0.0.1": 200}


# ══ векторы: одно место ═══════════════════════════════════════════
def test_векторы_значения_модулей_из_схемы_и_из_настроек(lab, tmp_path):
    keys = ["search.OLLAMA", "search.MODEL", "search.EMBED_OPTIONS", "index_more.OLLAMA", "index_more.MODEL", "index_more.DIM", "ingest.DIM"]
    r = lab("attrs", env=put(tmp_path, "env"), attrs=keys)
    assert r.code == 0, r.err
    assert r.data["values"] == {"search.OLLAMA": D["embed_url"], "search.MODEL": D["embed_model"], "search.EMBED_OPTIONS": {"num_gpu": 0},
                                "index_more.OLLAMA": D["embed_url"], "index_more.MODEL": D["embed_model"], "index_more.DIM": D["embed_dim"],
                                "ingest.DIM": D["embed_dim"]}, "умолчание — процессор, адрес и модель из схемы"
    for number, url in enumerate(["http://127.0.0.1:19111/api/embed", "https://embed.example.com:8443/v1/embed"], 1):
        r = lab("attrs", env=put(tmp_path / str(number), "file", embed_url=url, embed_model="модель-образец", embed_dim=768, embed_gpu=True), attrs=keys)
        assert r.code == 0, r.err
        assert r.data["values"] == {"search.OLLAMA": url, "search.MODEL": "модель-образец", "search.EMBED_OPTIONS": {}, "index_more.OLLAMA": url,
                                    "index_more.MODEL": "модель-образец", "index_more.DIM": 768, "ingest.DIM": 768}


@pytest.mark.parametrize("gpu", ["false", "true"])
def test_адрес_модель_и_видеокарта_доходят_до_тела_запроса_к_службе_векторов(lab, tmp_path, gpu):
    env = put(tmp_path, "env")
    env["CASE"] = json.dumps({"path": "/custom/embed", "model": "модель-образец", "gpu": gpu})
    r = run_snippet(lab, tmp_path, EMBED_BODIES, env)
    on_gpu = gpu == "true"
    want = [{"path": "/custom/embed", "body": {"model": "модель-образец", "input": ["запрос"], "options": {} if on_gpu else {"num_gpu": 0}}},
            {"path": "/custom/embed", "body": {"model": "модель-образец", "input": ["а"], **({} if on_gpu else {"options": {"num_gpu": 0}})}},
            {"path": "/custom/embed", "body": {"model": "модель-образец", "input": ["б"], **({} if on_gpu else {"options": {"num_gpu": 0}})}}]
    assert r.data["requests"] == want


def test_векторы_без_настроек_считаются_на_процессоре_модель_из_схемы(lab, tmp_path):
    env = put(tmp_path, "env")
    env["CASE"] = json.dumps({"path": "/api/embed", "model": D["embed_model"], "gpu": "false"})
    r = run_snippet(lab, tmp_path, EMBED_BODIES, env)
    assert [q["body"].get("options") for q in r.data["requests"]] == [{"num_gpu": 0}] * 3


# ══ модели ═══════════════════════════════════════════════════════
class ModelStub:
    """Подставная модель по протоколу OpenAI: запоминает запросы, отвечает чистой проверкой или заданным сбоем."""

    def __init__(self, running=()):
        self.requests, self.status, self.running, self.content = [], 200, list(running), json.dumps({"findings": []})
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, status, data):
                body = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.requests.append(("GET", self.path, dict(self.headers), None))
                self.send(200, {"running": [{"model": m} for m in outer.running]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(("POST", self.path, dict(self.headers), body))
                if outer.status != 200:
                    return self.send(outer.status, {"error": "сбой"})
                self.send(200, {"choices": [{"message": {"role": "assistant", "content": outer.content}}]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]


@pytest.fixture
def model_stub():
    made = []

    def make(**kw):
        made.append(ModelStub(**kw))
        return made[-1]

    yield make
    for stub in made:
        stub.server.shutdown()
        stub.server.server_close()


def env_of(base, **variables):
    """Окружение для from_env: свой каталог архива, пустой домашний каталог и заданные переменные (по полным именам)."""
    return {"FLYARCHIVE_HOME": str(archive(base)), "HOME": str(base / "дом"), **variables}


def png():
    Image = pytest.importorskip("PIL.Image")
    import io
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, "PNG")
    return buf.getvalue()


# ── имя локальной модели ────────────────────────────────────────
def test_имя_локальной_модели_по_умолчанию_пусто_а_адрес_петля():
    assert D["llm_local_model"] == "" and D["llm_cloud_model"] == "" and D["llm_cloud_url"] == "" and D["llm_key_file"] == ""
    assert D["llm_local_url"].startswith("http://127.0.0.1:")


def test_адреса_моделей_в_примере_без_v1_а_в_описании_сказано_что_v1_дописывается_само(tmp_path):
    with open(os.path.join(ROOT, "settings.example.json"), encoding="utf-8") as f:
        text = f.read()
    example = json.loads(text)
    for key in ("llm_local_url", "llm_cloud_url"):
        assert example[key] and not example[key].rstrip("/").endswith("/v1"), f"{key}: в примере адрес без /v1 на конце"
        note = S.SCHEMA[key].note
        assert "/v1" in note and "само" in note and "\n" not in note, f"{key}: в описании должно быть сказано, что /v1 дописывается само"
    assert not D["llm_local_url"].rstrip("/").endswith("/v1")
    arch = archive(tmp_path)
    (arch / "settings.json").write_text(text, encoding="utf-8")
    (arch / "settings.json").chmod(0o600)
    c = L.from_env({"FLYARCHIVE_HOME": str(arch), "HOME": str(tmp_path / "дом")})
    assert c.local.url == example["llm_local_url"].rstrip("/") + "/v1" and c.fallback.url == example["llm_cloud_url"].rstrip("/") + "/v1", \
        "пример настроек работает как есть: ни /v1/v1, ни потерянного /v1"


def test_заданные_имя_и_адрес_локальной_модели_доходят_до_запроса(tmp_path, model_stub):
    stub = model_stub(running=[LOCAL_MODEL])
    c = L.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=stub.url, FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL, FLYARCHIVE_LLM_KEY=KEY), cloud=False)
    r = c.check_text("Обычный текст.", "записка.txt", "txt")
    assert (r.status, r.model) == ("ok", LOCAL_MODEL)
    (post,) = stub.posts
    assert post[1] == "/v1/chat/completions" and post[3]["model"] == LOCAL_MODEL and post[2]["Authorization"] == "Bearer " + KEY
    assert ("GET", "/running") in [(m, p) for m, p, _, _ in stub.requests], "готовность модели спрашивается у того же адреса"


def test_адрес_настройки_с_суффиксом_v1_не_удваивает_его(tmp_path, model_stub):
    stub = model_stub(running=[LOCAL_MODEL])
    c = L.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=stub.url + "/v1/", FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL,
                          FLYARCHIVE_LLM_CLOUD_URL="https://llm.example.com/v1", FLYARCHIVE_LLM_CLOUD_MODEL=CLOUD_MODEL))
    assert c.local.url == stub.url + "/v1" and c.fallback.url == "https://llm.example.com/v1"
    assert c.check_text("текст", "a.txt", "txt").status == "ok"
    assert [(m, p) for m, p, _, _ in stub.requests] == [("GET", "/running"), ("POST", "/v1/chat/completions")]


def test_без_имени_локальной_модели_проверка_недоступна_запроса_нет(tmp_path, model_stub):
    stub = model_stub(running=[LOCAL_MODEL])
    c = L.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=stub.url, FLYARCHIVE_LLM_KEY=KEY), cloud=False)
    assert c.local.name == "" and c.fallback is None
    r = c.check_text("Текст документа.", "записка.txt", "txt")
    assert (r.status, r.model) == ("unchecked", None)
    (f,) = r.findings
    assert (f.rule, f.level) == ("llm_unchecked", "HIGH") and f.msg.code == "finding.llm_unchecked"
    assert f.msg.args["reason"] == str(M.make("llm.not_configured")), "понятная причина — из каталога сообщений"
    assert stub.requests == [], "ни проверки готовности, ни запроса с пустым именем"
    assert KEY not in json.dumps([f.msg.to_json(), f.where, f.quote], ensure_ascii=False)


def test_без_имени_локальной_модели_запасная_проверяет_если_она_задана(tmp_path, model_stub):
    local, cloud = model_stub(), model_stub()
    c = L.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=local.url, FLYARCHIVE_LLM_CLOUD_URL=cloud.url, FLYARCHIVE_LLM_CLOUD_MODEL=CLOUD_MODEL))
    r = c.check_text("Текст документа.", "записка.txt", "txt")
    assert (r.status, r.model) == ("ok", CLOUD_MODEL) and local.requests == []
    (post,) = cloud.posts
    assert post[3]["model"] == CLOUD_MODEL and post[2]["Authorization"] == "Bearer ollama"


def test_без_имени_локальной_модели_скан_распознаётся_на_месте_изображение_никуда_не_уходит(tmp_path, model_stub):
    stub = model_stub()
    c = L.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=stub.url), cloud=False)
    recognized = []
    r = c.check_images([png()], "скан.png", ocr=lambda: recognized.append(1) or "Распознанный текст скана")
    assert (r.status, r.model, r.text) == ("unchecked", None, "Распознанный текст скана") and recognized == [1]
    (f,) = r.findings
    assert str(M.make("llm.not_configured")) in f.msg.args["reason"] and not f.msg.args["reason"].startswith(":")
    assert stub.requests == []
    r = c.check_images([png()], "скан.png")
    assert r.status == "unchecked" and str(M.make("llm.not_configured")) in r.findings[0].msg.args["reason"] and stub.requests == []


def test_без_имени_локальной_модели_описание_изображений_стоит_с_понятной_причиной(tmp_path, model_stub):
    stub = model_stub(running=[LOCAL_MODEL])
    m = V.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=stub.url, FLYARCHIVE_LLM_KEY=KEY))
    stop = m.ready()
    assert isinstance(stop, M.Message) and stop.code == "vision.model_not_configured" and stop.args == {}
    with pytest.raises(V.Stop) as e:
        m.ask(b"\x89PNG\r\n\x1a\n")
    assert e.value.message.code == "vision.model_not_configured"

    class Viewer:
        def show(self, *a, **k):
            raise AssertionError("просмотр документа не должен запускаться, пока описывать нечем")

    out = V.describe(str(tmp_path / "архив"), "corpus/схема.png", "a" * 64, m, viewer=Viewer())
    assert out.complete is False and out.asked == 0 and out.stop.code == "vision.model_not_configured"
    assert stub.requests == [] and KEY not in str(out.stop)


def test_с_заданным_именем_описание_изображений_спрашивает_готовность_у_модели(tmp_path, model_stub):
    stub = model_stub(running=[LOCAL_MODEL])
    m = V.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=stub.url, FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL))
    assert m.ready() is None and m.name == LOCAL_MODEL
    assert [(method, path) for method, path, _, _ in stub.requests] == [("GET", "/running")]


# ── запасная модель ─────────────────────────────────────────────
@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("named", [True, False])
@pytest.mark.parametrize("addressed", [True, False])
def test_запасная_модель_есть_только_если_заданы_и_адрес_и_имя_и_она_включена(tmp_path, flag, named, addressed):
    variables = {**({"FLYARCHIVE_LLM_CLOUD_URL": "http://127.0.0.1:19901"} if addressed else {}),
                 **({"FLYARCHIVE_LLM_CLOUD_MODEL": CLOUD_MODEL} if named else {})}
    c = L.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL, **variables), cloud=flag)
    if flag and named and addressed:
        assert c.fallback == L.Endpoint(CLOUD_MODEL, "http://127.0.0.1:19901/v1", "ollama", False, False, {"reasoning_effort": "low"})
    else:
        assert c.fallback is None


@pytest.mark.parametrize("variables", [{}, {"FLYARCHIVE_LLM_CLOUD_MODEL": CLOUD_MODEL}])
def test_запасной_путь_без_заданного_адреса_не_делает_ни_одного_запроса(tmp_path, model_stub, variables):
    local = model_stub(running=[LOCAL_MODEL])
    local.status = 500
    c = L.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=local.url, FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL, **variables), cloud=True)
    calls = []
    real = c.transport
    c.transport = lambda endpoint, payload, timeout: calls.append(endpoint.name) or real(endpoint, payload, timeout)
    r = c.check_text("Текст документа.", "записка.txt", "txt")
    assert r.status == "unchecked" and calls == [LOCAL_MODEL], "транспорт вызван один раз: для локальной модели; запасного пути нет"
    assert len(local.posts) == 1


def test_запасная_модель_с_адресом_и_именем_берёт_работу_когда_локальная_молчит(tmp_path, model_stub):
    local, cloud = model_stub(running=[LOCAL_MODEL]), model_stub()
    local.status = 500
    c = L.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=local.url, FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL, FLYARCHIVE_LLM_CLOUD_URL=cloud.url,
                          FLYARCHIVE_LLM_CLOUD_MODEL=CLOUD_MODEL))
    r = c.check_text("Текст документа.", "записка.txt", "txt")
    assert (r.status, r.model) == ("ok", CLOUD_MODEL)
    assert [p[3]["model"] for p in cloud.posts] == [CLOUD_MODEL]


# ── ключ локальной модели ───────────────────────────────────────
NEW_KEY_VARIABLE = "FLYARCHIVE_LLM_KEY"
KEY_CASES = [  # (своя переменная, чужая переменная, содержимое файла, файл задан настройкой, ключ, который должен получиться)
    (None, None, None, False, ""),
    (None, None, "ключ-файла", False, ""),                       # файл лежит, но настройкой не задан: не читается
    (None, None, "ключ-файла", True, "ключ-файла"),
    (None, "ключ-прежней", "ключ-файла", True, "ключ-файла"),    # чужая переменная не перебивает файл
    (None, "ключ-прежней", None, False, ""),                     # чужая переменная одна: ключ пустой
    (None, "ключ-прежней", "ключ-файла", False, ""),             # файл не задан настройкой, чужая переменная не в счёт
    ("ключ-новой", "ключ-прежней", "ключ-файла", True, "ключ-новой"),
    ("ключ-новой", None, "ключ-файла", True, "ключ-новой"),
    ("ключ-новой", None, None, False, "ключ-новой"),
    ("", "ключ-прежней", "ключ-файла", True, "ключ-файла"),      # пустая переменная — не задана
    ("", "ключ-прежней", None, False, ""),
    ("", "", "ключ-файла", True, "ключ-файла"),
    ("ключ-новой", "", "ключ-файла", True, "ключ-новой"),
    (None, None, None, True, ""),                                # файл задан, но его нет
    (None, None, "  ключ-файла \n", True, "ключ-файла"),         # пробелы и перевод строки по краям снимаются
]


@pytest.mark.foreign_name
@pytest.mark.parametrize("named", [True, False])
@pytest.mark.parametrize("new, old, content, file_set, want", KEY_CASES)
def test_ключ_локальной_модели_из_своей_переменной_или_файла_по_настройке_а_чужая_переменная_не_читается(tmp_path, named, new, old, content, file_set, want):
    key_file = tmp_path / "ключи" / "локальная"
    if content is not None:
        key_file.parent.mkdir()
        key_file.write_text(content, encoding="utf-8")
    variables = {**({NEW_KEY_VARIABLE: new} if new is not None else {}), **({FOREIGN_KEY_VARIABLE: old} if old is not None else {}),
                 **({"FLYARCHIVE_LLM_KEY_FILE": str(key_file)} if file_set else {}), **({"FLYARCHIVE_LLM_LOCAL_MODEL": LOCAL_MODEL} if named else {})}
    c = L.from_env(env_of(tmp_path, **variables), cloud=False)
    assert c.local.key == want and c.local.name == (LOCAL_MODEL if named else "")


@pytest.mark.foreign_name
def test_путь_к_файлу_ключа_из_файла_настроек_а_чужая_переменная_пути_не_действует(tmp_path):
    key_file = tmp_path / "ключ-образец"
    key_file.write_text("ключ-из-файла\n", encoding="utf-8")
    arch = archive(tmp_path, llm_key_file=str(key_file))
    assert L.from_env({"FLYARCHIVE_HOME": str(arch), "HOME": str(tmp_path)}, cloud=False).local.key == "ключ-из-файла"
    other = tmp_path / "другой-ключ"
    other.write_text("ключ-другого-файла", encoding="utf-8")
    env = {"FLYARCHIVE_HOME": str(arch), "HOME": str(tmp_path), FOREIGN_PREFIX + "LLM_KEY_FILE": str(other)}
    assert L.from_env(env, cloud=False).local.key == "ключ-из-файла", "чужая переменная пути к файлу перебила файл настроек"
    assert L.from_env({**env, "FLYARCHIVE_LLM_KEY_FILE": str(other)}, cloud=False).local.key == "ключ-другого-файла", "новая переменная сильнее файла"


def test_без_заданного_файла_ключ_из_домашнего_каталога_не_читается_и_не_ищется(lab, tmp_path):
    key = lab.home / "llm" / "api-key"
    key.parent.mkdir()
    key.write_text("ключ-из-домашнего-каталога", encoding="utf-8")
    path = tmp_path / "snippet.py"
    path.write_text(KEY_BY_DEFAULT, encoding="utf-8")
    r = lab("snippet", str(path), env={**put(tmp_path, "env"), "FLYARCHIVE_LLM_LOCAL_MODEL": LOCAL_MODEL})
    assert r.code == 0, r.err[-800:]
    assert (r.data["key"], r.data["name"]) == ("", LOCAL_MODEL)
    assert r.touched == [], f"к файлу ключа по умолчанию обращений нет: {r.touched}"
    assert r.data["blocked"] == []


@pytest.mark.foreign_name
def test_переменная_ключа_из_окружения_процесса_читается_когда_окружение_не_передано(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYARCHIVE_LLM_LOCAL_MODEL", LOCAL_MODEL)
    monkeypatch.setenv(FOREIGN_KEY_VARIABLE, KEY + "-прежняя")
    monkeypatch.setenv(NEW_KEY_VARIABLE, KEY)
    c = L.from_env(cloud=False)
    assert (c.local.name, c.local.key) == (LOCAL_MODEL, KEY)
    monkeypatch.delenv(NEW_KEY_VARIABLE)
    assert L.from_env(cloud=False).local.key == "", "чужая переменная ключа читается, когда своей нет"


# ── ключ не попадает в сообщения и выводы ───────────────────────
@pytest.mark.foreign_name
def test_ключ_из_переменной_и_из_файла_не_попадает_в_вывод_настроек(tmp_path, capsys):
    key_file = tmp_path / "ключ-образец"
    key_file.write_text(KEY + "-из-файла\n", encoding="utf-8")
    env = {"FLYARCHIVE_HOME": str(archive(tmp_path)), "HOME": str(tmp_path), "FLYARCHIVE_LLM_KEY_FILE": str(key_file), NEW_KEY_VARIABLE: KEY + "-новая",
           FOREIGN_KEY_VARIABLE: KEY + "-прежняя"}
    for args in ([], ["--json"]):
        assert S.main(args, env=env) == 0
        out = capsys.readouterr().out
        assert KEY not in out and str(key_file) in out, "путь к файлу виден, содержимое и значения переменных — нет"


def test_негодная_настройка_модели_называет_ключ_настройки_а_не_секрет(tmp_path):
    env = env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL="ftp://" + LEAK, FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL, **{NEW_KEY_VARIABLE: KEY})
    with pytest.raises(S.SettingsError) as e:
        L.from_env(env)
    text = repr(e.value) + str(e.value) + json.dumps(M.of(e.value).to_json(), ensure_ascii=False)
    assert LEAK not in text and KEY not in text and e.value.message.args["key"] == "llm_local_url"


@pytest.mark.parametrize("how", ["status", "echo", "refused"])
def test_ключ_не_попадает_в_причину_отказа_и_замечания_при_сбое_модели(tmp_path, model_stub, how):
    stub, cloud = model_stub(running=[LOCAL_MODEL]), model_stub()
    stub.status = cloud.status = 500 if how != "echo" else 200
    if how == "echo":
        stub.content = cloud.content = "ответ с ключом " + KEY + " вместо JSON"
    url = stub.url if how != "refused" else f"http://127.0.0.1:{free_port()}"
    c = L.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=url, FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL, FLYARCHIVE_LLM_CLOUD_URL=cloud.url,
                          FLYARCHIVE_LLM_CLOUD_MODEL=CLOUD_MODEL, **{NEW_KEY_VARIABLE: KEY}), cloud=True)
    c.running = lambda: [LOCAL_MODEL]
    r = c.check_text("Текст документа.", "записка.txt", "txt")
    shown = json.dumps([[f.where, f.quote, f.msg.to_json(), f.where_msg.to_json()] for f in r.findings], ensure_ascii=False) + repr(r)
    assert r.status == "unchecked" and r.findings and KEY not in shown


def test_ключ_не_попадает_в_причину_остановки_описания_изображений(tmp_path):
    m = V.from_env(env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL, **{NEW_KEY_VARIABLE: KEY}))
    assert m.key == KEY

    for words in ("сбой, сервер повторил заголовок Authorization: Bearer " + KEY, "сбой, сервер вернул в теле ключ " + KEY + " как есть"):
        def broken(words=words):
            raise L.ModelError(words)

        m.running = broken
        stop = m.ready()
        assert stop.code == "vision.model_down" and KEY not in str(stop) + json.dumps(stop.to_json(), ensure_ascii=False), words


# ── живой архив получит свой файл настроек: поведение то же ────
def test_файл_настроек_живого_архива_даёт_тех_же_проверяющих_что_давал_прежний_код(tmp_path):
    key_file = tmp_path / "ключ-образец"
    key_file.write_text("ключ-образец-значение\n", encoding="utf-8")
    cloud_url = "http://127.0.0.1:19911"
    arch = archive(tmp_path, llm_local_model=LOCAL_MODEL, llm_key_file=str(key_file), llm_cloud_url=cloud_url, llm_cloud_model=CLOUD_MODEL)
    env = {"FLYARCHIVE_HOME": str(arch), "HOME": str(tmp_path / "дом")}
    # прежний код: адрес локальной модели по умолчанию, ключ из файла, мысли выключены; запасная: свой адрес и имя, ключ-заглушка, малое размышление
    want_local = L.Endpoint(LOCAL_MODEL, D["llm_local_url"] + "/v1", "ключ-образец-значение", True, True, {"chat_template_kwargs": {"enable_thinking": False}})
    want_cloud = L.Endpoint(CLOUD_MODEL, cloud_url + "/v1", "ollama", False, False, {"reasoning_effort": "low"})
    c = L.from_env(env)
    assert c.local == want_local and c.fallback == want_cloud and c.timeout == 180
    assert L.from_env(env, cloud=False).fallback is None and L.from_env(env, cloud=False).local == want_local


# ── команда проверки с ключом --llm ─────────────────────────────
def run_check(tmp_path, env):
    src = tmp_path / "входящие"
    src.mkdir()
    (src / "записка.txt").write_text("Согласовать перенос работ на четверг.", encoding="utf-8")
    environment = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "дом"), "PYTHONIOENCODING": "utf-8", **env}
    r = subprocess.run([sys.executable, CLI, "check", str(src), "--into", str(tmp_path / "разбор"), "--without-archive", "--llm"], env=environment,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    report = (tmp_path / "разбор" / "отчёт.jsonl").read_text(encoding="utf-8") if (tmp_path / "разбор" / "отчёт.jsonl").exists() else ""
    return r, report


def test_команда_без_имени_локальной_модели_говорит_почему_проверки_нет_и_модель_не_зовёт(tmp_path, model_stub):
    stub = model_stub(running=[LOCAL_MODEL])
    r, report = run_check(tmp_path, env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=stub.url, **{NEW_KEY_VARIABLE: KEY}))
    assert r.returncode == 0, r.stderr[-500:]
    assert str(M.make("llm.not_configured")) in r.stdout and "Traceback" not in r.stderr
    assert stub.requests == []
    (verdict,) = [json.loads(line) for line in report.splitlines()]
    assert verdict["decision"] == "review" and verdict["checked_by"] is None and verdict["findings"][-1]["rule"] == "llm_unchecked"
    assert KEY not in r.stdout + r.stderr + report


def test_команда_с_ключом_модели_и_сбоем_модели_ключа_нигде_не_печатает(tmp_path, model_stub):
    stub = model_stub(running=[LOCAL_MODEL])
    stub.status = 500
    r, report = run_check(tmp_path, env_of(tmp_path, FLYARCHIVE_LLM_LOCAL_URL=stub.url, FLYARCHIVE_LLM_LOCAL_MODEL=LOCAL_MODEL, **{NEW_KEY_VARIABLE: KEY}))
    assert r.returncode == 0, r.stderr[-500:]
    assert stub.posts and KEY not in r.stdout + r.stderr + report and "llm_unchecked" in report


# ══ новые коды сообщений ═════════════════════════════════════════
@pytest.mark.parametrize("code", ["llm.not_configured", "vision.model_not_configured"])
def test_новый_код_в_каталоге_называет_настройку_а_английский_шаблон_есть_в_словаре_плагина(code):
    template, params = M.CATALOG[code]
    assert params == () and re.search("[а-яё]", template, re.I) and "llm_local_model" in template
    made = M.make(code)
    assert (made.code, made.args, str(made)) == (code, {}, template)
    with open(os.path.join(ROOT, "dsh-plugin", "lib", "client.messages.js"), encoding="utf-8") as f:
        line = re.search(r'^\s*"%s":\s*"(.+)",?\s*$' % re.escape(code), f.read(), re.M)
    assert line, f"{code}: нет английского шаблона в client.messages.js"
    assert not re.search("[а-яё]", line.group(1), re.I) and "llm_local_model" in line.group(1)

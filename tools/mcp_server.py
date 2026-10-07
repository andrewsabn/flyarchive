r"""MCP-сервер над архивом: поиск, документы, схемы — для любой оболочки модели, которая берёт инструменты по MCP.

Тонкий переходник: отдаёт умения служб поиска и документов в виде MCP и
переспрашивает у этих служб (порты search_port и office_port из настроек).

Протокол — JSON-RPC 2.0 по HTTP (streamable-http). Нужны три метода:
initialize, tools/list, tools/call. Уведомления вроде notifications/initialized
подтверждать ответом нельзя — у них нет id.

    python3 tools/mcp_server.py
    http://127.0.0.1:8767/mcp
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth
import perms
import settings
import sources
import tokens
import version

_SETTINGS = settings.startup()
SOURCES = sources.startup()             # таблица источников: из неё описание поиска берёт перечень баз
HOST = "127.0.0.1"                      # адрес прослушивания не настраивается: наружу смотрит один шлюз
PORT = _SETTINGS["mcp_port"]
SEARCH = f"http://127.0.0.1:{_SETTINGS['search_port']}"          # соседи идут по петле на порты из настроек
OFFICE = f"http://127.0.0.1:{_SETTINGS['office_port']}"
TIMEOUT = _SETTINGS["mcp_timeout_s"]    # сколько секунд ждём ответа служб за переходником

# Пометки инструментов по спецификации MCP: клиент, который спрашивает у человека подтверждение на инструменты
# без пометок, читающие пропускает сам. Архив — замкнутый мир, создающие ничего не удаляют и не перезаписывают.
# Правило «уровень read — readOnlyHint true, уровень full — false» держит tests/test_mcp_annotations.py.
READ_HINTS = {"readOnlyHint": True, "openWorldHint": False}
MAKE_HINTS = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}

TOOLS = [
    {
        "name": "search_archive",
        "description": f"Найти документы в архиве {sources.PRODUCT}: {sources.scope_text(SOURCES)}. "
                       "Возвращает фрагмент, путь и ссылку на оригинал.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Запрос обычными словами"},
                "k": {"type": "integer", "description": f"Сколько вернуть, 1-{_SETTINGS['api_max_k']}"},
                "source": {"type": "string", "description": f"Ограничить базой: {sources.bases_text(SOURCES)}"},
                "space": {"type": "string", "description": "Раздел базы: каталог первого уровня, ящик или номер пачки"},
                "since": {"type": "string", "description": "Не старее даты: ГГГГ, ГГГГ-ММ или ГГГГ-ММ-ДД"},
            },
            "required": ["q"],
        },
        "route": ("GET", SEARCH + "/api/search"),
        "need": "read",
        "annotations": READ_HINTS,
    },
    {
        "name": "read_document",
        "description": "Прочитать документ архива целиком по полю path из выдачи "
                       "поиска. Читает pdf, docx, xlsx, pptx, msg, eml, vsdx, txt, md, csv, json, xml.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь из выдачи поиска"},
                "pages": {"type": "string",
                          "description": "Для pdf страницы вида 1-5 или 3,7; "
                                         "для xlsx имена листов через запятую"},
            },
            "required": ["path"],
        },
        "route": ("GET", OFFICE + "/read"),
        "need": "read",
        "annotations": READ_HINTS,
    },
    {
        "name": "read_document_rich",
        "description": "Разобрать документ с сохранением таблиц. Медленнее "
                       "обычного чтения в разы, зато таблицы остаются таблицами, "
                       "а не потоком слов. Бери, когда важна таблица: реестр, "
                       "смета, расписание, отчёт. Читает и старые форматы: "
                       "doc, xls, ppt, odt, ods, odp, epub.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь из выдачи поиска"},
                "pages": {"type": "string", "description": "Страницы вида 1-5"},
            },
            "required": ["path"],
        },
        "route": ("GET", OFFICE + "/rich"),
        "need": "read",
        "annotations": READ_HINTS,
    },
    {
        "name": "make_landscape",
        "description": "Схема ландшафта по краткому описанию: группы с узлами и "
                       "связи между ними. Оформление делает сервер. Бери всюду, где "
                       "схема это блоки по слоям и стрелки: устройство приложения, "
                       "этапы работ, карта связей. Замена Visio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "direction": {"type": "string", "description": "TB или LR"},
                "groups": {"type": "array", "items": {"type": "object"},
                           "description": "[{name: Экраны, nodes: [Маршрут, Журнал прогулок]}, {name: Данные, nodes: [Хранилище]}]"},
                "edges": {"type": "array", "items": {},
                          "description": "[[Маршрут, Хранилище], [Журнал прогулок, Хранилище]]"},
            },
            "required": ["groups"],
        },
        "route": ("POST", OFFICE + "/landscape"),
        "need": "full",
        "annotations": MAKE_HINTS,
    },
    {
        "name": "make_diagram",
        "description": "Схема исходником graphviz. Бери только для того, чего не "
                       "выразить группами и связями; держи короче трёх тысяч символов.",
        "inputSchema": {
            "type": "object",
            "properties": {"dot": {"type": "string"}, "fmt": {"type": "string"}},
            "required": ["dot"],
        },
        "route": ("POST", OFFICE + "/diagram"),
        "need": "full",
        "annotations": MAKE_HINTS,
    },
    {
        "name": "make_chart",
        "description": "График по числам: bar, barh, line или pie. Возвращает ссылку "
                       "на картинку — вставляй её в ответ как ![подпись](url).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"}, "title": {"type": "string"},
                "ylabel": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "series": {"type": "object", "description": "имя ряда -> массив чисел"},
            },
            "required": ["labels", "series"],
        },
        "route": ("POST", OFFICE + "/chart"),
        "need": "full",
        "annotations": MAKE_HINTS,
    },
    {
        "name": "make_document",
        "description": "Собрать файл Word, Excel, PowerPoint или PDF и вернуть ссылкой.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "docx, xlsx, pptx или pdf"},
                "title": {"type": "string"},
                "blocks": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["kind"],
        },
        "route": ("POST", OFFICE + "/document"),
        "need": "full",
        "annotations": MAKE_HINTS,
    },
    {
        "name": "submit_document",
        "description": "Передать документ в архив. Документ ложится во входящую папку и проходит приёмку: тип по "
                       "содержимому, правила безопасности, сверка с тем, что уже есть, проверка моделью. В поиске он "
                       "появится после ближайшего разбора; документ с находками попадёт на утверждение владельцу, "
                       f"программа — в карантин. Содержимое передаётся в base64, до {_SETTINGS['submit_max_mb']} МБ на документ.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Имя файла с расширением, без каталогов"},
                "content_base64": {"type": "string", "description": "Содержимое файла в base64"},
            },
            "required": ["name", "content_base64"],
        },
        "route": ("POST", OFFICE + "/submit"),
        "need": "full",
        "annotations": MAKE_HINTS,
    },
]

BY_NAME = {t["name"]: t for t in TOOLS}
HIDDEN = ("route", "need")          # служебные поля наружу не отдаются


def public_tools(level):
    """Инструменты, доступные уровню токена. Недоступные клиент не видит вовсе."""
    rank = tokens.LEVELS.index
    return [{k: v for k, v in t.items() if k not in HIDDEN}
            for t in TOOLS if rank(level) >= rank(t["need"])]


def call_upstream(tool, args, token=None, via="mcp", peer=None):
    """Переспрашивает у сервера, который уже умеет это делать.

    Дальше уходит токен самого клиента: сервер проверит его ещё раз и запишет
    обращение в журнал под именем клиента, а не переходника."""
    method, url = tool["route"]
    if method == "GET":
        clean = {k: v for k, v in (args or {}).items() if v not in (None, "")}
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(clean))
    else:
        req = urllib.request.Request(
            url, json.dumps(args or {}, ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.add_header("X-Flyarchive-Via", via)
    if peer:
        req.add_header("X-Forwarded-For", peer)           # узел, с которого пришёл клиент: для журнала
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8")


def _refusal(e):
    """Что сказать модели, когда служба ответила отказом: при 4xx поправить надо запрос, при 5xx виноват архив."""
    try:
        reason = json.loads(e.read().decode("utf-8")).get("error")
    except Exception:
        reason = None
    if not isinstance(reason, str) or not reason:
        return f"служба ответила кодом {e.code} без объяснения"
    return f"запрос отклонён (код {e.code}): {reason}" if e.code < 500 else f"сбой архива (код {e.code}): {reason}"


WITH_PARAMS = ("initialize", "tools/list", "tools/call")          # методы, у которых params разбираются; у остальных их не читают


def _wrong_params(method, params):
    """Что не так с params метода (текст для журнала) или None, если они годные: объект; у tools/call — ещё arguments (объект или null) и name (строка)."""
    if not isinstance(params, dict):
        return "params — не объект"
    if method == "tools/call":
        if params.get("arguments") is not None and not isinstance(params["arguments"], dict):
            return "arguments — не объект"
        if "name" in params and not isinstance(params["name"], str):
            return "name — не строка"
    return None


def handle(msg, client, token=None, audit=None, public=None, peer=None):
    """Возвращает ответ JSON-RPC либо None для уведомления.

    client — кто вызывает (имя и уровень токена), token — его токен для
    пересылки дальше, audit(tool, params, status, outcome) — запись в журнал,
    public — внешний адрес архива, если клиент пришёл с другой машины, peer — его узел.

    Тело, которое не объект JSON-RPC (число, строка, null, массив), — ответ «Invalid Request» (−32600, id null) и запись отказа в журнал, а не сбой.
    У initialize, tools/list и tools/call params, которые не объект, — ответ «Invalid params» (−32602, тот же id) и запись отказа в журнал; у tools/call
    то же для arguments, которые не объект, и name, которое не строка. Нет params (или null) — пустой объект, как и было."""
    if not isinstance(msg, dict):
        if audit:
            audit("mcp:invalid_request", {}, 400, "отказ: тело запроса — не объект JSON-RPC")
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None                       # уведомление: отвечать нечем и незачем

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def fail(code, text):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    p = msg.get("params")
    p = {} if p is None else p
    if method in WITH_PARAMS:
        wrong = _wrong_params(method, p)
        if wrong:
            if audit:
                audit("mcp:invalid_params", {"method": method}, 400, "отказ: " + wrong)
            return fail(-32602, "Invalid params")

    if method == "initialize":
        if audit:
            info = p.get("clientInfo") if isinstance(p.get("clientInfo"), dict) else {}
            name = " ".join(str(info[k]) for k in ("name", "version") if info.get(k))
            audit("mcp:initialize", {"client": name} if name else {}, 200, "ok")
        return ok({
            "protocolVersion": p.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "flyarchive", "version": version.VERSION},
        })
    if method == "tools/list":
        return ok({"tools": public_tools(client.level)})
    if method == "tools/call":
        tool = BY_NAME.get(p.get("name"))
        if not tool:
            return fail(-32602, f"нет инструмента {p.get('name')}")
        if not client.allows(tool["need"]):
            text = f"инструмент {tool['name']} требует уровня «{tool['need']}», у токена уровень «{client.level}»"
            if audit:
                audit("mcp:" + tool["name"], {}, 403, "отказ: " + text)
            return fail(-32602, text)
        try:
            text = call_upstream(tool, p.get("arguments") or {}, token, "tailnet" if public else "mcp", peer)
            if public:
                # ссылки на петлю с другой машины не открыть: клиент получает адрес шлюза
                text = text.replace(SEARCH + "/doc?", public + "/doc?").replace(OFFICE + "/file?", public + "/file?")
        except urllib.error.HTTPError as e:
            # служба ответила отказом: причина нужна модели, чтобы поправить запрос, а не повторять его
            return ok({"content": [{"type": "text", "text": _refusal(e)}], "isError": True})
        except urllib.error.URLError as e:
            # сервер за нами лежит — говорим об этом прямо, а не пустым ответом
            return ok({"content": [{"type": "text",
                                    "text": f"инструмент недоступен: {e}"}],
                       "isError": True})
        except Exception as e:
            return ok({"content": [{"type": "text",
                                    "text": f"{type(e).__name__}: {e}"}],
                       "isError": True})
        return ok({"content": [{"type": "text", "text": text}]})
    if method == "ping":
        return ok({})
    return fail(-32601, f"метод {method} не поддерживается")


class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


class Handler(auth.Guarded, BaseHTTPRequestHandler):
    server_version = "flyarchive-mcp"

    def log_message(self, fmt, *a):
        print(f"[{time.strftime('%H:%M:%S')}] {fmt % a if a else fmt}", flush=True)

    def reply(self, obj, code=200):
        data = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(202 if obj is None else code)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def audit(self, tool, params, status, outcome):
        self.guard.record(client=self.client.name, level=self.client.level, tool=tool, params=params,
                          status=status, outcome=outcome, ip=self._ba.get("ip") or self.client_address[0],
                          via="tailnet" if self.remote else None)

    def enter(self):
        """Пропуск запроса. Удачные запросы в журнал не идут: вызов инструмента
        запишет сервер, который его выполнит, а список и проверка связи — шум."""
        return self.admit("read", "mcp", quiet=True)

    def answer(self, msg):
        return handle(msg, self.client, self.token, self.audit, self.remote, self._ba.get("ip"))

    def do_POST(self):
        if not self.enter():
            return
        if urllib.parse.urlparse(self.path).path != "/mcp":
            return self.reply({"error": "не найдено"}, 404)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            msg = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return self.reply({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": "битый JSON"}}, 400)
        if isinstance(msg, list) and msg and all(isinstance(m, dict) for m in msg):      # пакет объектов; пустой и с не-объектом — отказ целиком
            out = [r for r in (self.answer(m) for m in msg) if r is not None]
            return self.reply(out or None)
        self.reply(self.answer(msg), 200 if isinstance(msg, dict) else 400)           # не объект: Invalid Request с тем же кодом, что у негодного JSON

    def do_GET(self):
        if not self.enter():
            return
        # на GET клиент ждёт поток событий сервера. Мы его не ведём: все
        # ответы идут на POST. По спецификации в таком случае положено 405 —
        # иначе клиент считает поток открытым и опрашивает нас без конца
        if urllib.parse.urlparse(self.path).path == "/mcp":
            self.hush()            # клиент пробует так при каждом подключении — в журнале это шум
            return self.reply({"error": "поток событий не поддерживается"}, 405)
        self.reply({"error": "не найдено"}, 404)

    def do_DELETE(self):
        if not self.enter():
            return
        self.reply(None)


def selftest():
    me = tokens.Client("selftest", "local")
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, me) is None
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, me)
    assert r["result"]["serverInfo"]["name"] == "flyarchive", r
    r = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, me)
    names = [t["name"] for t in r["result"]["tools"]]
    assert names == [t["name"] for t in TOOLS], names
    assert all("route" not in t and "need" not in t for t in r["result"]["tools"])   # внутреннее не наружу
    reader = tokens.Client("selftest", "read")
    r = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, reader)
    assert len(r["result"]["tools"]) == 3, r                     # уровень «чтение» создавать не может
    r = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "нет такого", "arguments": {}}}, me)
    assert r["error"]["code"] == -32602, r
    r = handle({"jsonrpc": "2.0", "id": 4, "method": "чепуха"}, me)
    assert r["error"]["code"] == -32601, r
    print("selftest ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--selftest" in sys.argv:
        selftest()
    else:
        perms.close_umask()                                # всё, что служба создаёт, — только владельцу, при любой маске запустившего
        try:
            Handler.guard = auth.Guard("mcp")
            Handler.guard.remote_full = True              # клиенты с других машин работают через MCP по токену
            Handler.guard.startup_check()
        except auth.AuthError as e:
            raise SystemExit(f"переходник MCP не запущен: {e}")
        s = Server((HOST, PORT), Handler)
        print(f"слушаю http://{HOST}:{PORT}/mcp, инструментов {len(TOOLS)}", flush=True)
        s.serve_forever()

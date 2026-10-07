r"""Локальный веб-поиск по индексу архива. Только стандартная библиотека.

    python webui.py            # http://127.0.0.1:8765 (порт — настройка search_port)

Результаты кликабельны: документ открывается в браузере тем же сервером.
Слушает только петлю — наружу ничего не отдаётся.
"""
import html, io, json, mimetypes, os, re, sys, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import search as S
import auth
import corpus_path
import perms
import settings
import sources
import version

_SETTINGS = settings.startup()
SOURCES = sources.startup()
CORPUS = os.path.join(_SETTINGS["home"], "corpus")
HOST = "127.0.0.1"                      # адрес прослушивания не настраивается: наружу смотрит один шлюз, а не поиск и документы напрямую
PORT = _SETTINGS["search_port"]
API_SNIPPET = _SETTINGS["api_snippet_chars"]    # символов текста на находку в ответе для модели
API_MAX_K = _SETTINGS["api_max_k"]              # находок за вызов (умолчание 20: больше переполняют контекст)
API_BUDGET = _SETTINGS["api_budget_chars"]      # общий потолок текста в одном ответе, символов
# это браузер только показывает; всё прочее (svg, xhtml, mht) он может открыть как страницу со сценариями
INERT = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/bmp", "image/tiff", "image/webp"}
TEXTY = {".md", ".txt", ".csv", ".tsv", ".sql", ".json", ".yaml", ".yml", ".log",
         ".xml", ".bpmn", ".eml", ".html", ".htm", ".properties", ".ini", ".conf"}

CSS = """
:root{--bg:#f7f9f9;--card:#fff;--ink:#10191a;--dim:#63787a;--line:#d3dedd;--acc:#0e4a50;--hi:#fbe6a2}
@media(prefers-color-scheme:dark){:root{--bg:#0b1213;--card:#131d1e;--ink:#e2ebea;--dim:#7e9493;--line:#26383a;--acc:#63b8be;--hi:#4a3d15}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 "IBM Plex Sans","Segoe UI",system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 0;z-index:5}
.wrap{max-width:980px;margin:0 auto;padding:0 20px}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input[type=text]{flex:1;min-width:260px;padding:11px 14px;font-size:16px;border:1px solid var(--line);border-radius:3px;background:var(--card);color:var(--ink)}
select,input[type=number]{padding:10px;border:1px solid var(--line);border-radius:3px;background:var(--card);color:var(--ink);font-size:14px}
button{padding:11px 20px;border:0;border-radius:3px;background:var(--acc);color:var(--bg);font-size:15px;font-weight:600;cursor:pointer}
.meta{color:var(--dim);font-size:13px;margin:16px 0 4px}
.hit{background:var(--card);border:1px solid var(--line);border-radius:3px;padding:14px 16px;margin-bottom:12px}
.hit a.t{font-size:17px;color:var(--acc);text-decoration:none;font-weight:600}
.hit a.t:hover{text-decoration:underline}
.sub{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;color:var(--dim);margin:4px 0 8px;word-break:break-all}
.snip{font-size:14.5px;color:var(--ink)}
mark{background:var(--hi);color:inherit;padding:0 2px}
.badge{display:inline-block;font-family:ui-monospace,monospace;font-size:11px;padding:2px 6px;border:1px solid var(--line);border-radius:2px;color:var(--dim);margin-right:6px}
pre.doc{white-space:pre-wrap;word-wrap:break-word;background:var(--card);border:1px solid var(--line);padding:18px;border-radius:3px;font:13px/1.6 "IBM Plex Mono",ui-monospace,monospace}
.empty{color:var(--dim);padding:40px 0}
"""


def resolve(rel):
    """Относительный путь из индекса -> реальный файл, с защитой от выхода за корпус."""
    return corpus_path.resolve(rel, CORPUS)


def highlight(text, query):
    words = [w for w in re.findall(r"\w{3,}", query, re.U)]
    out = html.escape(re.sub(r"\s+", " ", text).strip())
    for w in sorted(set(words), key=len, reverse=True)[:8]:
        out = re.sub(f"({re.escape(html.escape(w))})", r"<mark>\1</mark>", out, flags=re.I)
    return out


def page(body, title="Поиск по архиву"):
    return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style>{body}"""


BASES_TTL = 30          # секунд: базы индекса читаются по всей таблице, а форма строится на каждую страницу
_clock = time.monotonic
_seen = {}              # каталог индекса -> (когда прочитано, имена баз)


def index_bases():
    """Базы, которые есть в индексе (search.bases), по алфавиту. Читается раз в BASES_TTL секунд; нет библиотеки, таблицы или чтение не удалось —
    пусто и не запоминается: страница строится по таблице источников, а не падает из-за списка."""
    now, held = _clock(), _seen.get(S.DB)
    if held and now - held[0] < BASES_TTL:
        return list(held[1])
    try:
        names = sorted(name for name in S.bases() if name)
    except Exception:
        return []
    _seen[S.DB] = (now, names)
    return list(names)


def base_choices(table, indexed):
    """Список баз для страницы поиска: [(база, подпись)]. Первым «везде»; дальше базы таблицы источников в порядке таблицы, базы, которые есть
    только в индексе, по алфавиту, и последней база входящих. Подпись — из таблицы, у базы без подписи — её имя."""
    labels = table.labels
    names = [b for b in table.bases() if b != sources.INTAKE]
    names += sorted(b for b in set(indexed) if b not in names and b != sources.INTAKE)
    names.append(sources.INTAKE)
    return [("", "везде")] + [(b, labels.get(b) or (sources.INTAKE_LABEL if b == sources.INTAKE else b)) for b in names]


def form(q="", k=20, since="", source="", space="", bases=None):
    choices = base_choices(SOURCES, index_bases()) if bases is None else bases
    opts = "".join(f'<option{" selected" if source == v else ""} value="{html.escape(v)}">{html.escape(n)}</option>' for v, n in choices)
    return f"""<header><div class="wrap"><form method="get" action="/">
<input type="text" name="q" value="{html.escape(q)}" placeholder="что ищем" autofocus>
<select name="source">{opts}</select>
<input type="text" name="space" value="{html.escape(space)}" placeholder="проект" size="8">
<input type="text" name="since" value="{html.escape(since)}" placeholder="с даты" size="8">
<input type="number" name="k" value="{k}" min="1" max="100" style="width:70px">
<button>Найти</button></form></div></header>"""



class Server(ThreadingHTTPServer):
    """Очередь подключений по умолчанию равна пяти.

    Модель вызывает инструмент несколько раз подряд, и на десяти параллельных
    запросах лишние получали сброс соединения. Потоки делаем фоновыми, чтобы
    остановка сервера не ждала висящие запросы."""
    request_queue_size = 128
    daemon_threads = True

class Handler(auth.Guarded, BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        """Короткая строка на обращение: без неё не видно, звала ли модель поиск."""
        import time as _t
        line = fmt % a if a else fmt
        print(f"[{_t.strftime('%H:%M:%S')}] {self.address_string()} {line}", flush=True)

    def send(self, body, ctype="text/html; charset=utf-8", code=200, extra=()):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")       # тип назван сервером: браузер его не угадывает
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        g = lambda n, d="": (qs.get(n, [d])[0] or "").strip()
        if u.path == "/login":
            return self.login(g("c"))
        if u.path == "/doc":
            # документ открывается токеном, подписанной ссылкой из выдачи или сеансом человека
            if not self.admit("read", "/doc", {"p": g("p")}, link=("doc", g("p"), g("e"), g("s")), session=True):
                return
            return self.serve_doc(g("p"))
        if u.path == "/openapi.json":
            if not self.admit("read", "/openapi.json"):
                return
            return self.send(json.dumps(openapi(), ensure_ascii=False),
                             "application/json; charset=utf-8")
        if u.path == "/api/search":
            asked = {n: g(n) for n in ("q", "k", "since", "source", "space") if g(n)}
            if not self.admit("read", "/api/search", asked):
                return
            return self.serve_api(g("q"), g("k", "10"), g("since"), g("source"), g("space"))
        # страница поиска для человека: вход по одноразовой ссылке, дальше cookie
        if not self.admit("read", u.path[:80], {"q": g("q")} if g("q") else {}, session=True):
            return
        if u.path not in ("/", "/search"):
            return self.send("не найдено", "text/plain; charset=utf-8", 404)
        q, space, since, source = g("q"), g("space"), g("since"), g("source")
        try:
            k = max(1, min(100, int(g("k", "20"))))
        except ValueError:
            k = 20
        head = form(q, k, since, source, space)
        if not q:
            return self.send(page(head + '<div class="wrap"><p class="empty">Введите запрос обычными словами. '
                                         'Работает смысловой поиск вместе с точным совпадением, свежие документы поднимаются выше.</p></div>'))
        try:
            hits = S.search(q, k, since or None, source or None, space or None)
        except S.SearchError as e:                # нет библиотеки, таблицы или службы векторов: сообщение с подсказкой и код по смыслу (FR-107)
            return self.send(page(head + f'<div class="wrap"><p class="empty">Ошибка поиска: {html.escape(str(e))}</p></div>'), code=e.status)
        except Exception as e:
            return self.send(page(head + f'<div class="wrap"><p class="empty">Ошибка поиска: {html.escape(str(e))}</p></div>'))
        rows = [f'<div class="wrap"><p class="meta">Найдено: {len(hits)}</p>']
        for sc, r in hits:
            link = "/doc?p=" + urllib.parse.quote(r["path"])
            ext = os.path.splitext(r["path"])[1].lower() or "—"
            rows.append(
                f'<div class="hit"><a class="t" href="{link}">{html.escape(r["title"] or r["path"])}</a>'
                f'<div class="sub"><span class="badge">{html.escape(r["updated"] or "без даты")}</span>'
                f'<span class="badge">{html.escape(r["space"] or r["source"])}</span>'
                f'<span class="badge">{html.escape(ext)}</span>'
                f'<span class="badge">{sc:.4f}</span><br>{html.escape(r["path"])}</div>'
                f'<div class="snip">{highlight(r["text"][:600], q)}</div></div>')
        rows.append("</div>")
        self.send(page(head + "".join(rows), q))

    def do_POST(self):
        """Маршрутов у метода нет: любой путь отвечает так же, как неизвестный путь у GET."""
        u = urllib.parse.urlparse(self.path)
        if not self.admit("read", u.path[:80], {"path": u.path}, session=True):
            return
        self.send("не найдено", "text/plain; charset=utf-8", 404)

    def serve_api(self, q, k, since, source, space):
        """JSON для внешних интерфейсов: оболочка с OpenAPI, MCP, что угодно ещё."""
        if not q:
            return self.send(json.dumps({"error": "нужен параметр q"}, ensure_ascii=False),
                             "application/json; charset=utf-8", 400)
        try:
            n = max(1, min(API_MAX_K, int(k)))
        except ValueError:
            n = min(API_MAX_K, 10)               # негодное число — десять, но и тогда не больше предела из настройки
        try:
            hits = S.search(q, n, since or None, source or None, space or None)
        except S.BadQuery as e:                   # ошибка в запросе, а не сбой архива: модель должна узнать, что поправить
            return self.send(json.dumps({"error": str(e)}, ensure_ascii=False), "application/json; charset=utf-8", 400)
        except S.SearchError as e:                # нет библиотеки или таблицы — 503, не отвечает служба векторов — 502; error — строка, как у всех служб
            return self.send(json.dumps({"error": str(e), "code": e.message.code}, ensure_ascii=False), "application/json; charset=utf-8", e.status)
        except Exception as e:
            return self.send(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False),
                             "application/json; charset=utf-8", 500)
        out, spent = [], 0
        for sc, r in hits:
            body = re.sub(r"\s+", " ", r["text"])[:API_SNIPPET]
            # исчерпав бюджет, продолжаем перечислять находки, но без текста:
            # модель видит, что нашлось больше, и может уточнить запрос
            if spent + len(body) > API_BUDGET:
                body = ""
            spent += len(body)
            out.append({
                "score": round(sc, 5),
                "title": r["title"],
                "date": r["updated"],
                "base": r["source"],
                "space": r["space"],
                "path": r["path"],
                # ссылка подписана и живёт сутки: браузер токен не шлёт
                "url": f"http://127.0.0.1:{PORT}/doc?p=" + urllib.parse.quote(r["path"])
                       + "&" + self.guard.link_query("doc", r["path"]),
                "text": body,
            })
        self.send(json.dumps({"query": q, "count": len(out), "results": out}, ensure_ascii=False),
                  "application/json; charset=utf-8")

    def serve_doc(self, rel):
        p = resolve(rel)
        if not p:
            return self.send("файл не найден: " + html.escape(rel), "text/plain; charset=utf-8", 404)
        ext = os.path.splitext(p)[1].lower()
        if ext in TEXTY:
            with io.open(p, encoding="utf-8", errors="replace") as f:
                body = f.read(1_500_000)
            back = '<div class="wrap"><p class="meta"><a href="javascript:history.back()">← назад</a></p>'
            return self.send(page(back + f"<h2>{html.escape(os.path.basename(p))}</h2>"
                                         f'<pre class="doc">{html.escape(body)}</pre></div>',
                                  os.path.basename(p)))
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        with open(p, "rb") as f:
            data = f.read()
        # песочница: сценарии в документе не выполняются, а сам он считается чужим адресом — сеанс владельца ему не виден
        self.send(data, ctype, extra=() if ctype in INERT else (("Content-Security-Policy", "sandbox"),))


def openapi():
    """Спецификация OpenAPI: оболочка импортирует её и зовёт поиск как инструмент."""
    p = {"name": None, "in": "query", "schema": {"type": "string"}}
    def par(name, desc, typ="string", req=False):
        return {"name": name, "in": "query", "required": req, "description": desc,
                "schema": {"type": typ}}
    return {
        "openapi": "3.1.0",
        "info": {"title": f"Поиск по архиву {sources.PRODUCT}", "version": version.VERSION,
                 "description": f"Гибридный поиск по архиву {sources.PRODUCT}: {sources.scope_text(SOURCES)}. "
                                "Свежие документы ранжируются выше."},
        "servers": [{"url": f"http://127.0.0.1:{PORT}"}],
        "paths": {"/api/search": {"get": {
            "operationId": "search_archive",
            "summary": "Найти документы в личном архиве по запросу на естественном языке",
            "parameters": [
                par("q", "Поисковый запрос обычными словами", req=True),
                par("k", f"Сколько результатов вернуть, 1-{API_MAX_K}", "integer"),
                par("source", f"Ограничить базой: {sources.bases_text(SOURCES)}; можно несколько через запятую"),
                par("space", "Раздел базы: каталог первого уровня, ящик или номер пачки"),
                par("since", "Отсечь документы старее даты, формат ГГГГ, ГГГГ-ММ или ГГГГ-ММ-ДД"),
            ],
            "responses": {"200": {"description": "Найденные фрагменты документов",
                                  "content": {"application/json": {"schema": {"type": "object"}}}}},
        }}},
    }


def selftest():
    ROOT = os.sep + "tmp"
    spec = openapi()
    assert spec["paths"]["/api/search"]["get"]["operationId"] == "search_archive"
    assert [p["name"] for p in spec["paths"]["/api/search"]["get"]["parameters"]][0] == "q"
    assert spec["servers"][0]["url"].endswith(str(PORT))
    assert resolve("../../windows/system32/drivers/etc/hosts") is None
    assert resolve(os.path.join(ROOT, "Windows", "win.ini")) is None
    assert "<mark>лимит</mark>" in highlight("одобренный лимит клиента", "лимит ставка")
    assert highlight("текст", "") == "текст"
    assert corpus_path.resolve("../../x", ROOT) is None
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        perms.close_umask()                                # всё, что служба создаёт, — только владельцу, при любой маске запустившего
        try:
            Handler.guard = auth.Guard("search")
            Handler.guard.startup_check()
        except auth.AuthError as e:
            raise SystemExit(f"служба поиска не запущена: {e}")
        # слушаем только петлю, второго адреса нет: наружу выходит один шлюз, он и ставит заголовок «запрос с другой машины»
        try:
            server = Server((HOST, PORT), Handler)
        except OSError as e:
            raise SystemExit(f"не удалось занять {HOST}:{PORT} ({e})")
        print(f"слушаю http://{HOST}:{PORT}", flush=True)
        server.serve_forever()

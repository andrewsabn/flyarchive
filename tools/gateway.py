"""Шлюз в tailnet: единственная дверь к архиву с других машин.

Слушает адрес этой машины в tailnet и пропускает три вещи:
    /mcp   — инструменты архива, по токену;
    /doc   — документ по подписанной ссылке из ответа поиска;
    /file  — созданный файл по подписанной ссылке.
Всё остальное снаружи не существует: поиск, чтение и создание файлов напрямую закрыты,
службы за шлюзом слушают только петлю. Трафик внутри tailnet уже зашифрован, поэтому шлюз
говорит по HTTP.

Службам шлюз сообщает две вещи, которые клиент подменить не может: что запрос пришёл снаружи
(заголовок X-Flyarchive-Remote с внешним адресом архива) и с какого узла (X-Forwarded-For).

Настройки (файл settings.json архива или переменные окружения FLYARCHIVE_<КЛЮЧ>):
    gateway_bind=203.0.113.10                      адрес этой машины в tailnet; порт — gateway_port (или «адрес:порт»); пусто — шлюз не стартует
    public_url=http://archive.example.com:8780     внешний адрес архива: его имя узла шлюз принимает в заголовке Host
    public_hosts=["203.0.113.10"]                  необязательно: другие имена этого же узла
Порты служб за шлюзом — search_port, office_port, mcp_port: шлюз ходит к ним по петле.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth
import journal as journal_mod
import settings

_SETTINGS = settings.startup()
BACKENDS = {"/mcp": f"http://127.0.0.1:{_SETTINGS['mcp_port']}", "/doc": f"http://127.0.0.1:{_SETTINGS['search_port']}",
            "/file": f"http://127.0.0.1:{_SETTINGS['office_port']}"}
METHODS = {"/mcp": ("GET", "POST", "DELETE"), "/doc": ("GET",), "/file": ("GET",)}
MAX_BODY = 24 * 1024 * 1024         # самый большой запрос — передача документа: 16 МБ в base64
TIMEOUT = 330                       # чуть больше, чем переходник MCP ждёт службу за собой
PASS_IN = ("Authorization", "Content-Type", "Accept", "Mcp-Session-Id", "Mcp-Protocol-Version")
PASS_OUT = ("Content-Type", "Content-Disposition", "WWW-Authenticate")
CHUNK = 256 * 1024
DRAIN = 32 * 1024 * 1024         # столько лишнего шлюз готов дочитать, чтобы ответить отказом


class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def make_handler(backends, public_url, hosts, journal):
    """Обработчик шлюза. hosts — имена и адреса узла помимо того, что стоит в public_url."""
    allowed = {auth.host_name(urllib.parse.urlparse(public_url).netloc)} | {h.strip().lower() for h in hosts if h.strip()}

    class Handler(BaseHTTPRequestHandler):
        server_version = "flyarchive-gateway"

        def log_message(self, fmt, *a):
            print(f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} {fmt % a if a else fmt}", flush=True)

        def deny(self, code, text, started):
            journal.write(server="gateway", client="-", tool=urllib.parse.urlparse(self.path).path[:80], params={},
                          status=code, outcome="отказ: " + text, ms=int((time.time() - started) * 1000), ip=self.client_address[0])
            data = json.dumps({"error": text}, ensure_ascii=False).encode("utf-8")
            self.close_connection = True
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def relay(self):
            started, method = time.time(), self.command
            path = urllib.parse.urlparse(self.path).path
            if auth.host_name(self.headers.get("Host")) not in allowed:
                return self.deny(403, "чужой Host: шлюз отвечает только на адрес архива в tailnet", started)
            if path not in backends:
                return self.deny(404, "снаружи доступны только /mcp и подписанные ссылки на документы", started)
            if method not in METHODS[path]:
                return self.deny(405, f"метод {method} для {path} не поддерживается", started)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                left = min(max(length, 0), DRAIN)          # дочитать и выбросить: иначе клиент не увидит отказа
                while left > 0:
                    chunk = self.rfile.read(min(CHUNK, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                return self.deny(413, "слишком большой запрос", started)
            body = self.rfile.read(length) if length else None
            headers = {k: self.headers[k] for k in PASS_IN if self.headers.get(k)}
            headers["X-Flyarchive-Remote"] = public_url              # клиентские значения этих заголовков отбрасываются
            headers["X-Forwarded-For"] = self.client_address[0]
            req = urllib.request.Request(backends[path] + self.path, data=body if method == "POST" else None,
                                         headers=headers, method=method)
            try:
                resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            except urllib.error.HTTPError as e:
                resp = e                                            # отказ службы передаётся клиенту как есть
            except Exception as e:
                return self.deny(502, f"служба за шлюзом не отвечает: {type(e).__name__}", started)
            with resp:
                size = resp.headers.get("Content-Length")
                data = None if size else resp.read()
                self.send_response(resp.status if hasattr(resp, "status") else resp.code)
                for name in PASS_OUT:
                    if resp.headers.get(name):
                        self.send_header(name, resp.headers[name])
                self.send_header("Content-Length", size or str(len(data)))
                self.end_headers()
                if data is not None:
                    self.wfile.write(data)
                else:
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

        do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_OPTIONS = do_HEAD = relay

    return Handler


def listen_on(bind, port):
    """(узел, порт) из настройки gateway_bind: «адрес» (порт — gateway_port) или «адрес:порт»."""
    host, sep, tail = bind.rpartition(":")
    if sep and ":" not in host and tail.isdigit():
        return host, int(tail)
    return bind, port


def main():
    s = settings.startup()
    if not s["gateway_bind"] or not s["public_url"]:
        raise SystemExit("шлюз не настроен: задай gateway_bind (адрес этой машины в tailnet) и public_url (внешний адрес шлюза) в настройках")
    host, port = listen_on(s["gateway_bind"], s["gateway_port"])
    bind, public = f"{host}:{port}", s["public_url"]
    if host in ("", "0.0.0.0", "::", "[::]"):
        raise SystemExit("шлюз слушает только адрес tailnet: 0.0.0.0 открыл бы архив локальной сети")
    handler = make_handler(BACKENDS, public.rstrip("/"), [host, *s["public_hosts"]], journal_mod.Journal())
    for attempt in range(120):                    # адрес tailnet появляется не сразу после загрузки
        try:
            server = Server((host, port), handler)
            break
        except OSError as e:
            if attempt == 0:
                print(f"адрес {bind} пока недоступен ({e}), жду tailscale", flush=True)
            time.sleep(5)
    else:
        raise SystemExit(f"не удалось занять {bind}: tailscale не поднялся")
    print(f"шлюз слушает http://{bind}, внешний адрес {public}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

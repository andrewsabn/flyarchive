"""Вход в службы архива: Host, токен, подписанная ссылка, вход человека.

Три службы (поиск, документы, переходник MCP) делят одно хозяйство:
    <архив>/secrets/tokens.json   хеши токенов клиентов
    <архив>/secrets/link.key      ключ подписи ссылок на документы
    <архив>/secrets/login.json    одноразовые коды входа на страницу поиска
    <архив>/logs/access.jsonl     журнал обращений
где <архив> — каталог архива из настроек (settings.py).

Порядок проверки запроса: Host, затем токен из заголовка Authorization, затем
подписанная ссылка, затем сеанс человека. Отказ пишется в журнал.
"""
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from contextlib import contextmanager

import journal as journal_mod
import messages
import settings
import tokens

try:
    import fcntl
except ImportError:
    fcntl = None

_SETTINGS = settings.startup()
HOME = tokens.HOME
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
LINK_TTL = _SETTINGS["link_ttl_s"]          # срок ссылки на документ, секунд (по умолчанию сутки)
LOGIN_TTL = _SETTINGS["login_ttl_s"]        # срок одноразового кода входа (по умолчанию пять минут)
SESSION_TTL = _SETTINGS["session_ttl_s"]    # срок сеанса человека на странице поиска (по умолчанию двенадцать часов)
COOKIE = "ba_session"
REASONS = {"revoked": "отозван", "expired": "просрочен"}      # причины отказа токену, как их читает владелец в журнале


class AuthError(messages.CodedError):
    """Хозяйство доступа непригодно: чужие права на файлы, испорченный ключ."""


def host_name(header):
    """Имя узла из заголовка Host без порта. None, если это не «имя» или «имя:порт»."""
    if not header or header != header.strip():
        return None
    h = header.lower()
    if h.startswith("["):
        end = h.find("]")
        if end < 0:
            return None
        name, rest = h[:end + 1], h[end + 1:]
    else:
        name, sep, port = h.partition(":")
        rest = sep + port
    if rest and not (rest.startswith(":") and rest[1:].isdigit()):
        return None
    return name


def bearer(header):
    """Токен из «Authorization: Bearer <токен>» либо None."""
    parts = (header or "").split(" ")
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
        return parts[1]
    return None


class Guard:
    def __init__(self, server, home=HOME, hosts=None, clock=time.time):
        self.server, self.home, self.clock = server, home, clock
        self.secrets = os.path.join(home, "secrets")
        self.store = tokens.Store(os.path.join(self.secrets, "tokens.json"), clock)
        self.journal = journal_mod.Journal(os.path.join(home, "logs", "access.jsonl"), clock)
        extra = hosts if hosts is not None else settings.load(home=home)["hosts"]      # свои имена узла: настройка hosts этого архива
        self.hosts = set(LOOPBACK) | {h.strip().lower() for h in extra if h.strip()}
        self.sessions = {}
        self._key = None
        # запросы с других машин (через шлюз tailnet): True — по токену, как у переходника MCP;
        # False — только подписанные ссылки, как у поиска и документов
        self.remote_full = False

    # ── Host ────────────────────────────────────────────────────
    def host_ok(self, header):
        """Защита от подмены адреса через DNS: чужое имя в Host не обслуживается."""
        return host_name(header) in self.hosts

    # ── файлы с секретами ───────────────────────────────────────
    def _secret_dir(self):
        os.makedirs(self.secrets, mode=0o700, exist_ok=True)
        if stat.S_IMODE(os.stat(self.secrets).st_mode) & 0o077:
            raise AuthError(messages.make("auth.secrets_dir_open", path=self.secrets))

    def link_key(self):
        if self._key is None:
            self._secret_dir()
            path = os.path.join(self.secrets, "link.key")
            if not os.path.exists(path):
                # файл появляется сразу с содержимым: две службы стартуют одновременно,
                # и вторая не должна прочесть пустой ключ
                tmp = f"{path}.{os.getpid()}.tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(secrets.token_bytes(32))
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    pass
                finally:
                    os.unlink(tmp)
            if stat.S_IMODE(os.stat(path).st_mode) & 0o077:
                raise AuthError(messages.make("auth.link_key_open", path=path))
            with open(path, "rb") as f:
                key = f.read()
            if len(key) < 32:
                raise AuthError(messages.make("auth.link_key_broken", path=path))
            self._key = key
        return self._key

    def startup_check(self):
        """Вызывается при старте службы: с негодным хозяйством лучше не подниматься."""
        self._secret_dir()
        try:
            self.store.list()
        except tokens.TokenError as e:
            raise AuthError(messages.of(e))
        self.link_key()

    # ── подписанные ссылки ──────────────────────────────────────
    def _mac(self, kind, value, exp):
        msg = json.dumps([kind, value, exp], ensure_ascii=False).encode("utf-8")
        return hmac.new(self.link_key(), msg, hashlib.sha256).hexdigest()[:32]

    def sign(self, kind, value):
        """Параметры e и s для ссылки: браузер токен не шлёт, а ссылка из чата должна открываться."""
        exp = str(int(self.clock()) + LINK_TTL)
        return {"e": exp, "s": self._mac(kind, value, exp)}

    def link_query(self, kind, value):
        q = self.sign(kind, value)
        return f"e={q['e']}&s={q['s']}"

    def signed_ok(self, kind, value, e, s):
        if not isinstance(e, str) or not isinstance(s, str) or not e.isdigit() or not s:
            return False
        if int(e) <= self.clock():
            return False
        return hmac.compare_digest(self._mac(kind, value, e).encode(), s.encode("utf-8"))

    # ── вход человека ───────────────────────────────────────────
    @contextmanager
    def _login_codes(self):
        self._secret_dir()
        path = os.path.join(self.secrets, "login.json")
        lock = os.open(path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with open(path, encoding="utf-8") as f:
                    codes = json.load(f)["codes"]
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                codes = []
            box = {"codes": [c for c in codes if c["expires_at"] > self.clock()]}
            yield box
            fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(box, f)
            os.replace(path + ".tmp", path)
        finally:
            os.close(lock)

    def new_login_code(self):
        """Одноразовый код для ссылки входа. В файле остаётся только его хеш."""
        code = secrets.token_urlsafe(24)
        with self._login_codes() as box:
            box["codes"].append({"hash": tokens.digest(code), "expires_at": self.clock() + LOGIN_TTL})
        return code

    def redeem(self, code):
        """Меняет код на сеанс. Код после этого не действует."""
        if not isinstance(code, str) or not code:
            return None
        want = tokens.digest(code)
        with self._login_codes() as box:
            keep = [c for c in box["codes"] if not hmac.compare_digest(c["hash"], want)]
            found = len(keep) != len(box["codes"])
            box["codes"] = keep
        if not found:
            return None
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = self.clock() + SESSION_TTL
        return sid

    def session_ok(self, cookie_header):
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and value:
                expires = self.sessions.get(value)
                if expires and expires > self.clock():
                    return True
        return False

    # ── журнал ──────────────────────────────────────────────────
    def record(self, client, tool, params, status, outcome, level=None, ms=0, ip=None, via=None):
        self.journal.write(server=self.server, client=client, level=level, tool=tool, params=params,
                           status=status, outcome=outcome, ms=ms, ip=ip, via=via)


class Guarded:
    """Примесь к обработчику HTTP: пропуск запроса и запись в журнал.

    Обработчик зовёт admit() первой строкой. Ответ — клиент либо None: в этом
    случае отказ уже отправлен. Запись в журнал происходит сама, когда
    обработчик отправляет код ответа.
    """
    guard = None

    def admit(self, need, tool, params=None, link=None, session=False, quiet=False):
        g = self.guard
        # эти два заголовка ставит шлюз tailnet; клиентские значения он отбрасывает
        self.remote = self.headers.get("X-Flyarchive-Remote") or None
        peer = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()[:45] or None
        self._ba = ctx = {"t0": g.clock(), "tool": tool, "params": dict(params or {}), "client": "-",
                          "level": None, "outcome": "ok", "quiet": quiet, "done": False, "ip": peer,
                          "via": (self.headers.get("X-Flyarchive-Via") or "")[:20] or ("tailnet" if self.remote else None)}
        self.client = self.token = None
        if not g.host_ok(self.headers.get("Host")):
            return self._deny(403, "чужой Host: служба отвечает только на адреса этой машины")
        try:
            token = bearer(self.headers.get("Authorization"))
            if self.remote and not g.remote_full:
                # с других машин токен действует только в MCP; документ открывается по подписанной ссылке
                if not link or not g.signed_ok(*link):
                    return self._deny(403, "с других машин открываются только подписанные ссылки из ответов архива")
                token, client = None, tokens.Client("ссылка", "read")
            elif token is not None:
                client = g.store.verify(token)
                if client is None:
                    # клиенту — одно и то же для любого негодного токена; журналу — чей это был токен и почему отказ
                    why, logged = g.store.refusal(token), None
                    if why:
                        ctx.update(client=why.name, level=why.level)
                        logged = f"токен {REASONS[why.reason]} {why.when}"
                    return self._deny(401, "токен неверен, отозван или просрочен", logged)
            elif link and (link[2] or link[3]):
                if not g.signed_ok(*link):
                    return self._deny(403, "ссылка просрочена или изменена — запроси документ заново")
                client = tokens.Client("ссылка", "read")
            elif session and g.session_ok(self.headers.get("Cookie")):
                client = tokens.Client("владелец", "local")
            else:
                return self._deny(401, "нужен токен: заголовок Authorization: Bearer <токен>")
        except (tokens.TokenError, AuthError) as e:
            return self._deny(503, f"вход закрыт: {e}")
        ctx.update(client=client.name, level=client.level)
        if not client.allows(need):
            return self._deny(403, f"нужен уровень «{need}», у токена уровень «{client.level}»")
        self.client, self.token = client, token
        return client

    def note(self, **params):
        """Дополняет запись журнала параметрами, которые стали известны после пропуска."""
        self._ba["params"].update(params)

    def hush(self):
        """Ответ на этот запрос в журнал не пойдёт: штатная проба протокола, не обращение к архиву."""
        self._ba["done"] = True

    def _deny(self, code, text, logged=None):
        """Отказ: клиенту уходит text, в журнал — logged, если он задан (клиенту этого знать не нужно)."""
        self._ba["outcome"] = "отказ: " + (logged or text)
        self.close_connection = True          # тело запроса не прочитано — соединение не переиспользуем
        data = json.dumps({"error": text}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if code == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        return None

    def login(self, code, to="/"):
        """Вход человека: одноразовый код из ссылки меняется на сеанс в cookie."""
        g = self.guard
        self._ba = {"t0": g.clock(), "tool": "/login", "params": {}, "client": "-", "level": None,
                    "outcome": "ok", "quiet": False, "done": False, "via": None, "ip": None}
        if self.headers.get("X-Flyarchive-Remote"):
            return self._deny(403, "вход на страницу поиска — только с этой машины")
        if not g.host_ok(self.headers.get("Host")):
            return self._deny(403, "чужой Host: служба отвечает только на адреса этой машины")
        try:
            sid = g.redeem(code)
        except AuthError as e:
            return self._deny(503, f"вход закрыт: {e}")
        if not sid:
            return self._deny(401, "ссылка входа недействительна или уже использована — выполни: flyarchive open")
        self._ba.update(client="владелец", level="local")
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Set-Cookie", f"{COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_request(self, code="-", size="-"):
        super().log_request(code, size)
        ctx = getattr(self, "_ba", None)
        if ctx is None or ctx["done"]:
            return
        ctx["done"] = True
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 0
        if ctx["quiet"] and status < 400:
            return
        outcome = ctx["outcome"] if ctx["outcome"] != "ok" else ("ok" if status < 400 else f"ошибка {status}")
        g = self.guard
        g.record(client=ctx["client"], level=ctx["level"], tool=ctx["tool"], params=ctx["params"],
                 status=status, outcome=outcome, ms=int((g.clock() - ctx["t0"]) * 1000),
                 ip=ctx.get("ip") or self.client_address[0], via=ctx["via"])

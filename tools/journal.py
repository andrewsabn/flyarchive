"""Журнал обращений к архиву: кто, что, когда, чем кончилось.

Строка JSON на запрос. Значения токенов в журнал не попадают: служебные
параметры отбрасываются, а всё похожее на токен затирается в любом поле.
"""
import json
import os
import re
import sys
import time

import settings

HOME = settings.startup()["home"]
PATH = os.path.join(HOME, "logs", "access.jsonl")
MAX_VALUE = 200
DROP = {"authorization", "token", "cookie", "s", "c"}     # заголовки, подписи ссылок, коды входа
TOKEN_LIKE = re.compile("ba_[A-Za-z0-9_-]{8,}")


def scrub(value):
    return TOKEN_LIKE.sub("ba_***", value) if isinstance(value, str) else value


def short(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = scrub(text)
    return text[:MAX_VALUE] + "…" if len(text) > MAX_VALUE else text


class Journal:
    def __init__(self, path=PATH, clock=time.time):
        self.path, self.clock = path, clock

    def write(self, server, client, tool, params, status, outcome, ms, level=None, ip=None, via=None):
        """Дописывает строку. Сбой журнала службу не останавливает, но виден в её логе."""
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock())),
               "server": server, "client": scrub(client), "level": level, "tool": scrub(tool),
               "params": {k: short(v) for k, v in (params or {}).items()
                          if k.lower() not in DROP and v is not None},
               "status": status, "outcome": scrub(outcome), "ms": ms, "ip": scrub(ip), "via": scrub(via)}
        data = (json.dumps(rec, ensure_ascii=False) + chr(10)).encode("utf-8")
        try:
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, data)          # одна запись в режиме дозаписи строку не рвёт
            finally:
                os.close(fd)
            return True
        except OSError as e:
            print(f"журнал обращений не записан: {e}", file=sys.stderr, flush=True)
            return False

    def tail(self, n=100, client=None):
        """Последние n записей, по желанию одного клиента."""
        try:
            with open(self.path, encoding="utf-8") as f:
                recs = [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []
        if client is not None:
            recs = [r for r in recs if r.get("client") == client]
        return recs[-n:]

"""Токены доступа к архиву: выпуск, проверка, отзыв.

Хранится только хеш токена. Значение показывается один раз — при выпуске.
Файл читают три службы и команда flyarchive, поэтому каждое обращение сверяет
время изменения файла: отзыв действует со следующего запроса, без перезапуска.
"""
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from collections import namedtuple
from contextlib import contextmanager

import messages
import settings

try:
    import fcntl
except ImportError:          # Windows: блокировки нет, но и служб там нет
    fcntl = None

HOME = settings.startup()["home"]
PATH = os.path.join(HOME, "secrets", "tokens.json")
LEVELS = ("read", "full", "local")   # по возрастанию прав
PREFIX = "ba_"
TOUCH_EVERY = 60                     # секунд: чаще время последнего вызова не пишем


class TokenError(messages.CodedError):
    """Отказ хранилища: плохое имя, занятое имя, чужие права на файл, битый файл."""


class Client(namedtuple("Client", "name level")):
    def allows(self, need):
        return LEVELS.index(self.level) >= LEVELS.index(need)


class Refusal(namedtuple("Refusal", "name level reason when")):
    """Почему не пущен токен, который в хранилище есть: reason — revoked или expired, when — время из хранилища."""


def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def stamp(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


class Store:
    def __init__(self, path=PATH, clock=time.time):
        self.path, self.clock = path, clock
        self._seen, self._rows = None, []

    # ── файл ────────────────────────────────────────────────────
    def _read(self):
        """Строки хранилища. Перечитывает файл, только если он изменился."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._seen, self._rows = None, []
            return self._rows
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise TokenError(messages.make("token.store_open", path=self.path))
        seen = (st.st_mtime_ns, st.st_size, st.st_ino)
        if seen != self._seen:
            try:
                with open(self.path, encoding="utf-8") as f:
                    rows = json.load(f)["tokens"]
            except (ValueError, KeyError, TypeError) as e:
                raise TokenError(messages.make("token.store_broken", path=self.path, error=str(e)))
            self._seen, self._rows = seen, rows
        return self._rows

    def _write(self, rows):
        folder = os.path.dirname(self.path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "tokens": rows}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        self._seen = None

    @contextmanager
    def _locked(self):
        """Чтение-правка-запись под замком: служба и команда не затрут друг друга."""
        folder = os.path.dirname(self.path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        fd = os.open(self.path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            self._seen = None
            yield [dict(r) for r in self._read()]
        finally:
            os.close(fd)

    # ── действия ────────────────────────────────────────────────
    def issue(self, name, level="read", days=None):
        """Выпускает токен и возвращает его значение — единственный раз."""
        if not isinstance(name, str) or not 1 <= len(name) <= 64 \
                or not all(ch.isalnum() or ch in "._-" for ch in name):
            raise TokenError(messages.make("token.bad_name"))
        if level not in LEVELS:
            raise TokenError(messages.make("token.bad_level", levels=", ".join(LEVELS)))
        with self._locked() as rows:
            if any(r["name"] == name and not r["revoked"] for r in rows):
                raise TokenError(messages.make("token.name_taken", name=name))
            token = PREFIX + secrets.token_urlsafe(32)
            now = self.clock()
            rows.append({"name": name, "level": level, "hash": digest(token), "created": stamp(now),
                         "expires": stamp(now + days * 86400) if days else None,
                         "expires_at": now + days * 86400 if days else None,
                         "last_used": None, "last_used_at": None, "revoked": None})
            self._write(rows)
        return token

    def verify(self, token, touch=True):
        """Клиент по токену или None. Сравнение идёт по всем строкам за одинаковое время. touch=False — проверка не обращение клиента:
        «последний вызов» не отмечается и файл не пишется."""
        rows = self._read()
        if not isinstance(token, str) or not token.startswith(PREFIX):
            return None
        want, now, hit = digest(token), self.clock(), None
        for r in rows:
            same = hmac.compare_digest(r["hash"], want)
            if same and not r["revoked"] and (r["expires_at"] is None or now < r["expires_at"]):
                hit = r
        if hit is None:
            return None
        if touch and (hit["last_used_at"] is None or now - hit["last_used_at"] >= TOUCH_EVERY):
            self._touch(hit["hash"], now)
        return Client(hit["name"], hit["level"])

    def refusal(self, token):
        """Refusal для известного, но негодного токена; None, если токен годен или такой строки нет.

        Нужен только для журнала: клиенту состояние токена не раскрывается. Сравнение идёт по всем
        строкам, как в verify; «последнее обращение» не трогается и файл не пишется."""
        rows = self._read()
        if not isinstance(token, str) or not token.startswith(PREFIX):
            return None
        want, now, bad, live = digest(token), self.clock(), None, False
        for r in rows:
            if not hmac.compare_digest(r["hash"], want):
                continue
            if not r["revoked"] and (r["expires_at"] is None or now < r["expires_at"]):
                live = True
            else:
                bad = r
        if bad is None or live:
            return None
        if bad["revoked"]:                       # отозван и просрочен — говорим «отозван»
            return Refusal(bad["name"], bad["level"], "revoked", bad["revoked"])
        return Refusal(bad["name"], bad["level"], "expired", bad["expires"])

    def _touch(self, token_hash, now):
        with self._locked() as rows:
            for r in rows:
                if r["hash"] == token_hash and not r["revoked"]:
                    r["last_used"], r["last_used_at"] = stamp(now), now
                    self._write(rows)

    def revoke(self, name):
        with self._locked() as rows:
            live = [r for r in rows if r["name"] == name and not r["revoked"]]
            if not live:
                raise TokenError(messages.make("token.no_active", name=name))
            live[0]["revoked"] = stamp(self.clock())
            self._write(rows)

    def list(self):
        """Сведения о токенах без значений и без хешей."""
        keys = ("name", "level", "created", "expires", "last_used", "revoked")
        return [{k: r[k] for k in keys} for r in self._read()]

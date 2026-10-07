"""База известного: что уже лежит в архиве. По ней приёмка узнаёт дубликаты.

Файл опознаётся по sha256 содержимого. Письмо — по Message-ID: одно письмо из разных
выгрузок отличается служебными заголовками и переводами строк, а Message-ID у него один.
Письмо без Message-ID опознаётся по отпечатку: отправитель, время до минуты, тема, начало текста.

    build(корпус, база)            собрать или обновить: читается только новое и изменённое
    Known(база).find(sha256, mid, fp) -> путь в корпусе либо None
"""
import email
import hashlib
import os
import re
import sqlite3
import stat
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import timezone
from email import policy
from email.utils import parseaddr, parsedate_to_datetime

import filetype_sniff
import messages
import settings

HOME = settings.startup()["home"]
DB = os.path.join(HOME, "index", "known.sqlite")
CORPUS = os.path.join(HOME, "corpus")
SKIP_DIR = "_карантин"
FP_TEXT = 200          # столько букв и цифр из начала письма идёт в отпечаток
# номер правил опознания и ключей: после их смены база перечитывается целиком
VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, sha TEXT, mid TEXT, fp TEXT, date TEXT);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha);
CREATE INDEX IF NOT EXISTS files_mid ON files(mid);
CREATE INDEX IF NOT EXISTS files_fp ON files(fp);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


# ── ключи ───────────────────────────────────────────────────────
def sha256_of_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_mid(value):
    """Message-ID в одном виде: без пробелов, угловых скобок и различий регистра."""
    mid = re.sub(r"\s+", "", str(value or "")).strip("<>").lower()
    return mid or None


def fingerprint(sender, date, subject, body):
    """Отпечаток письма без Message-ID. Не зависит от служебных заголовков, пояса и переводов строк."""
    if not (sender or date or subject):
        return None
    addr = parseaddr(str(sender or ""))[1].lower()
    try:
        dt = date if hasattr(date, "astimezone") else parsedate_to_datetime(str(date))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        minute = dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    except (TypeError, ValueError, AttributeError):
        minute = ""
    subj = " ".join(str(subject or "").split()).lower()
    text = re.sub(r"[\W_]+", "", str(body or "").lower())[:FP_TEXT]
    return hashlib.sha256("|".join((addr, minute, subj, text)).encode("utf-8")).hexdigest()[:32]


def body_of(msg):
    """Текст письма для отпечатка: текстовая часть, а если её нет — разметка без тегов."""
    try:
        part = msg.get_body(preferencelist=("plain",))
        if part is not None:
            return part.get_content()
        part = msg.get_body(preferencelist=("html",))
        if part is not None:
            return re.sub(r"<[^>]+>", " ", part.get_content())
    except Exception:
        pass
    return ""


def keys_of_message(msg):
    """(Message-ID, отпечаток) разобранного письма."""
    def header(name):
        try:
            return msg.get(name)
        except Exception:
            return None

    sender, date, subject = header("From"), header("Date"), header("Subject")
    return norm_mid(header("Message-ID")), fingerprint(sender, date, subject, body_of(msg))


def _day(value):
    """Дата из строки заголовка или из готового времени: день по часам отправителя. Негодная — None."""
    try:
        dt = value if hasattr(value, "year") else parsedate_to_datetime(str(value))
        if 1990 <= dt.year <= time.localtime().tm_year + 1:
            return "%04d-%02d-%02d" % (dt.year, dt.month, dt.day)
    except (TypeError, ValueError, AttributeError, OverflowError):
        pass
    return None


def date_of_message(msg):
    """Дата письма: заголовок Date, а если его нет — самый ранний приём сервером (последний Received)."""
    try:
        candidates = [msg.get("Date")] + [str(r).rsplit(";", 1)[-1].strip() for r in reversed(msg.get_all("Received") or [])]
    except Exception:
        return None
    for value in candidates:
        if value:
            day = _day(value)
            if day:
                return day
    return None


def keys_of_outlook(m):
    """То же для письма Outlook (.msg), разобранного extract_msg."""
    return norm_mid(getattr(m, "messageId", None)), fingerprint(m.sender, m.date, m.subject, m.body)


def letter_info(path, type_):
    """(Message-ID, отпечаток, дата) письма в файле. Не письмо или не читается — (None, None, None)."""
    try:
        if type_ == "msg":
            import extract_msg
            m = extract_msg.Message(path)
            try:
                return keys_of_outlook(m) + (_day(m.date),)
            finally:
                m.close()
        with open(path, "rb") as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
        return keys_of_message(msg) + (date_of_message(msg),)
    except Exception:
        return None, None, None


def missing_library(type_):
    """Имя пакета, без которого письма этого вида не читаются, а его нет; иначе None. Без неё ключи сверки писем остаются пустыми,
    и это должно быть видно (FR-107): сборка базы называет библиотеку в замечании."""
    if type_ == "msg":
        try:
            import extract_msg  # noqa: F401 — только проверка, что библиотека есть
        except ImportError:
            return "extract-msg"
    return None


def missing_note(type_):
    """Замечание lib.missing (то же, что называет приёмка), если письма этого вида не читаются без отсутствующей библиотеки; иначе None. Без неё
    принятие письма и определение его даты молча получали пустые ключи и дату из запасного источника: теперь причина видна (FR-107, FR-45)."""
    package = missing_library(type_)
    if package is None:
        return None
    return messages.make("lib.missing", package=package, use="msg", file="requirements-optional.txt")


def mail_keys(path, type_):
    """(Message-ID, отпечаток) письма в файле. Не письмо или не читается — (None, None)."""
    return letter_info(path, type_)[:2]


# ── база ────────────────────────────────────────────────────────
def _open(db, create=False):
    if not os.path.exists(db):
        if not create:
            return None
        os.makedirs(os.path.dirname(db), mode=0o700, exist_ok=True)
        os.close(os.open(db, os.O_WRONLY | os.O_CREAT, 0o600))
    con = sqlite3.connect(db)
    if create:
        con.executescript(SCHEMA)
    return con


class Known:
    def __init__(self, db=DB):
        self.db = db
        self.con = _open(db)

    def find(self, sha256=None, mid=None, fp=None):
        """Путь в корпусе, где это уже лежит, либо None.

        Письмо с Message-ID сверяется по нему. Отпечаток идёт в ход, только когда Message-ID
        нет у входящего письма или у письма в архиве: два письма с разными Message-ID — разные."""
        if self.con is None:
            return None
        checks = [("mid = ?", mid), ("sha = ?", sha256), ("fp = ? AND mid IS NULL" if mid else "fp = ?", fp)]
        for where, value in checks:
            if value:
                row = self.con.execute(f"SELECT path FROM files WHERE {where} LIMIT 1", (value,)).fetchone()
                if row:
                    return row[0]
        return None

    def date_of(self, path):
        """Дата письма по пути в корпусе. Не письмо, нет даты или нет такого пути — None."""
        if self.con is None:
            return None
        row = self.con.execute("SELECT date FROM files WHERE path = ?", (path,)).fetchone()
        return row[0] if row else None

    def info(self):
        if self.con is None:
            return None
        meta = dict(self.con.execute("SELECT key, value FROM meta"))
        files = self.con.execute("SELECT count(*) FROM files").fetchone()[0]
        mail = self.con.execute("SELECT count(*) FROM files WHERE mid IS NOT NULL OR fp IS NOT NULL").fetchone()[0]
        return {"files": files, "mail": mail, "built": meta.get("built"), "corpus": meta.get("corpus")}


class KnownError(messages.CodedError):
    pass


def add(db, rows):
    """Дописывает принятые файлы: (путь в корпусе, размер, время изменения, sha256, Message-ID, отпечаток, дата).

    Нужна после каждой принятой пачки: следующая пачка должна узнавать только что принятое,
    а полный обход корпуса занимает десятки секунд."""
    if not os.path.exists(db):
        raise KnownError(messages.make("known.db_missing", path=db))
    con = sqlite3.connect(db)
    try:
        with con:
            con.executemany("INSERT OR REPLACE INTO files(path, size, mtime, sha, mid, fp, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [tuple(r) for r in rows])
    finally:
        con.close()
    os.chmod(db, 0o600)


def remove(db, paths):
    """Убирает записи о файлах, которых в корпусе больше нет: удалённый документ можно принять заново."""
    if not os.path.exists(db):
        raise KnownError(messages.make("known.db_missing", path=db))
    con = sqlite3.connect(db)
    try:
        with con:
            con.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in paths])
    finally:
        con.close()


def _describe(job):
    """Ключи одного файла корпуса. Работает в отдельном процессе."""
    path, rel = job
    try:
        st = os.stat(path)
        sha = sha256_of(path)
        kind = filetype_sniff.detect(path)
        lost = missing_library(kind.type) if kind.family == "mail" else None
        mid, fp, date = letter_info(path, kind.type) if kind.family == "mail" and not lost else (None, None, None)
        # ключи письма не прочитаны из-за отсутствующей библиотеки: время изменения не записывается, и следующая сборка перечитает файл
        return (rel, st.st_size, 0 if lost else st.st_mtime_ns, sha, mid, fp, date), lost
    except OSError:
        return None


def build(corpus=CORPUS, db=DB, jobs=1, progress=None):
    """Собирает или обновляет базу по корпусу. Возвращает счётчики."""
    started = time.time()
    corpus = os.path.abspath(corpus)
    con = _open(db, create=True)
    version = con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
    if version is None or version[0] != str(VERSION):
        # ключи посчитаны по старым правилам или столбцы другие — таблица создаётся заново
        con.executescript("DROP TABLE IF EXISTS files;" + SCHEMA)
        con.commit()
    have = {path: (size, mtime) for path, size, mtime in con.execute("SELECT path, size, mtime FROM files")}
    todo, seen = [], set()
    for root, dirs, files in os.walk(corpus):
        dirs[:] = sorted(d for d in dirs if d != SKIP_DIR)     # карантин в архив не входит
        for f in files:
            if f.startswith("._"):
                continue                                         # спутники macOS
            path = os.path.join(root, f)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_size == 0:
                continue
            rel = os.path.relpath(path, corpus).replace(os.sep, "/")
            seen.add(rel)
            if have.get(rel) != (st.st_size, st.st_mtime_ns):
                todo.append((path, rel))
    removed = [rel for rel in have if rel not in seen]
    con.executemany("DELETE FROM files WHERE path = ?", ((rel,) for rel in removed))
    con.commit()

    pool = ProcessPoolExecutor(max_workers=jobs) if jobs > 1 and len(todo) > 1 else None
    added, batch, lost = 0, [], {}
    try:
        results = pool.map(_describe, todo, chunksize=64) if pool else map(_describe, todo)
        for done in results:
            if done is None:
                continue
            row, package = done
            batch.append(row)
            added += 1
            if package:
                lost[package] = lost.get(package, 0) + 1
            if len(batch) >= 2000:
                con.executemany("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                con.commit()
                batch = []
                if progress:
                    progress(added, len(todo))
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    con.executemany("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
    con.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                    [("built", time.strftime("%Y-%m-%d %H:%M", time.localtime())), ("corpus", corpus),
                     ("version", str(VERSION))])
    con.commit()
    files = con.execute("SELECT count(*) FROM files").fetchone()[0]
    mail = con.execute("SELECT count(*) FROM files WHERE mid IS NOT NULL OR fp IS NOT NULL").fetchone()[0]
    con.close()
    return {"files": files, "mail": mail, "added": added, "removed": len(removed), "seconds": time.time() - started, "missing": lost}

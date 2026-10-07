#!/usr/bin/env python3
"""Ворота публикации (FR-90): ищут в файлах то, что нельзя выносить в открытый репозиторий.

    python3 tests/publication_gate.py [--root КАТАЛОГ] [--words ФАЙЛ] [--names ФАЙЛ] [--json]

Без --root проверяются файлы, отслеживаемые git в репозитории, где лежит сценарий; с --root — все файлы каталога
(так проверяется чистый снимок, который не репозиторий; каталог .git в снимке сам по себе находка).
Код возврата: 0 — чисто, 1 — есть находки, 2 — ворота не могли работать (нет списка слов, нет git, не открылся каталог).

Список запретных слов лежит вне репозитория: путь — ключ --words или переменная окружения FLYARCHIVE_GATE_WORDS.
Строка списка — слово или словосочетание, `#` в начале строки или после пробела — комментарий. Сравнение без учёта регистра,
по границам слова: буква рядом с совпадением — не граница, цифра, знак и подчёркивание — граница. Звёздочка в конце
(`основа*`) снимает правую границу: так ловятся склонения. Без списка ворота не считаются пройденными (код 2).
Содержимое списка не печатается нигде; печатается только найденное место.
Рабочие имена проекта — необязательный второй список (ключ --names или переменная FLYARCHIVE_GATE_NAMES): по одному имени в строке, `#` —
комментарий; имя ищется в любом регистре, без границ слова, в содержимом и в имени файла. Без списка имён вид working_name не срабатывает:
самих имён в этом файле нет.

Виды находок (поле kind):
  home_path        путь к домашнему каталогу с настоящим именем (заглушки из PLACEHOLDER_NAMES не в счёт)
  word             слово из списка запретных слов
  email            почтовый адрес не из доменов-примеров
  ip               адрес IPv4, кроме петли, нулевого и адресов документации; номера версий не в счёт
  host             имя узла частной сети (ts.net, local, lan, internal, corp) и ссылка на узел не из tests/publication_hosts.txt
  secret           значение, похожее на токен или ключ (печатается замаскированным: начало, конец, длина)
  working_name     рабочее имя проекта из списка имён в любом регистре, в том числе в имени файла
  tracked_private  файл в каталоге секретов, журналов, отчётов, индекса, корпуса, кэша; нет каталога в .gitignore; каталог .git в снимке
  too_large        файл больше MAX_READ_BYTES: прочитано только начало, остальное проверить руками
  allowlist        строка файла исключений или списка узлов не принята (нет причины, неверный вид)

Файлы исключений и разрешённых узлов берутся из проверяемого корня (у снимка — свои, они должны войти в снимок).
Исключения — tests/publication_allow.txt: «образец пути | вид | текст | причина». Текст — ровно то, что ворота печатают
в находке; `*` вместо текста снимает вид целиком для файла, но не для home_path. Для word `*` допустима только с причиной: так исключается файл,
чей текст менять нельзя (дословный текст лицензии), и слово списка в самой записи не называется. Без причины строка не принимается и
сама становится находкой. Двоичный файл проверяется по имени и по печатным строкам в нём (от MIN_STRING знаков).
"""
import argparse
import fnmatch
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORDS_ENV = "FLYARCHIVE_GATE_WORDS"
NAMES_ENV = "FLYARCHIVE_GATE_NAMES"
ALLOW_FILE = "tests/publication_allow.txt"
HOSTS_FILE = "tests/publication_hosts.txt"

MAX_READ_BYTES = 2 * 1024 * 1024       # сверх этого читается только начало, и файл становится находкой too_large
MIN_STRING = 6                         # печатные строки короче в двоичных файлах не читаются
TEXT_MAX = 80                          # длиннее найденный текст в находке обрезается
BINARY_PROBE = 8192                    # как git: нулевой байт в первых 8 КиБ — файл двоичный

KINDS = ("home_path", "word", "email", "ip", "host", "secret", "working_name", "tracked_private", "too_large", "allowlist")
NOT_WHOLE_FILE = ("home_path",)                        # целиком для файла этот вид не исключается, только конкретный текст

# Заглушки в домашних путях: то, что в документации стоит вместо настоящего имени. Список короткий и явный.
PLACEHOLDER_NAMES = frozenset({"user", "username", "you", "yourname", "your-name", "your_name", "example", "name", "me",
                               "someone", "foo", "alice", "bob", "public", "default"})     # плюс любое имя из одного знака: x, u, a
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net", "example.test")       # только зарезервированные для документации
RESERVED_TLDS = ("example", "invalid")                  # RFC 2606: для ссылок-образцов; в почте их не принимаем
NOT_HOST_HOLDERS = frozenset({"self", "this", "cls"})   # self.local — атрибут объекта, а не имя узла
DOC_NETS = tuple(ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
FILE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "svg", "webp", "ico"})        # icon@2x.png — не почта
PRIVATE_DIRS = ("secrets", "logs", "reports", "index", "corpus", "cache", "__pycache__")  # и они же обязаны стоять в .gitignore
PRIVATE_FILES = (".env", ".env.*", "*.pem", "*.key", "api-key", "id_rsa", "id_ed25519")
PRIVATE_FILES_OK = (".env.example",)
# Переменные, которыми git сам выбирает репозиторий и рабочее дерево. Вызов git из тестов и из ворот идёт без них (clean_git_env): иначе `git init`
# во временном каталоге у человека с несколькими рабочими копиями переписал бы конфиг его настоящего репозитория.
GIT_LOCAL_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE", "GIT_PREFIX")

# ── образцы ─────────────────────────────────────────────────────
_NAME = r"[^\W_][\w.-]*"
HOME_RE = re.compile(r"(?:/home/|/Users/|[A-Za-z]:[\\/]{1,4}(?i:users)[\\/]{1,4}|/mnt/[A-Za-z]/(?i:users)/)(?P<name>" + _NAME + ")")
EMAIL_RE = re.compile(r"(?<![\w.%+-])[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")
IP_RE = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?!\w|\.\d)")
# слова перед четырьмя числами, по которым понятно, что это номер версии
VERSION_BEFORE = re.compile(r"(?i)(?:\b(?:version|ver|rev|release|build|v|версия|версии|версию|версией|выпуск)\b\.?\s*[:=]?\s*"
                            r"|(?:===|==|>=|<=|~=|!=|\^|~)\s*)$")
URL_RE = re.compile(r"\b(?:https?|wss?|ftp|ssh|git)://(?:[^\s/'\"`<>@]*@)?(?P<host>[A-Za-z0-9][A-Za-z0-9.-]*)[^\s'\"`<>)\]\\]*", re.I)
HOST_RE = re.compile(r"(?<![\w@.-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+(?:ts\.net|local|lan|internal|corp)(?![\w-]|\.\w|\()", re.I)
KEY_HEADER_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY")
CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{32,}={0,2}(?![A-Za-z0-9+/_=-])")      # знак равенства — только добивка base64
HEX_RE = re.compile(r"[0-9a-fA-F]+")
# (образец, проверка совпадения): виды токенов и ключей по их начертанию. Токен проекта — префикс и 43 знака urlsafe (tools/tokens.py).
PREFIXED_SECRETS = (
    (re.compile(r"(?<![A-Za-z0-9])sk-([A-Za-z0-9_-]{20,})"), lambda tail: any(c.isdigit() for c in tail) and looks_real(tail)),
    (re.compile(r"(?<![A-Za-z0-9])gh[pousr]_([A-Za-z0-9]{30,})"), lambda tail: looks_real(tail)),
    (re.compile(r"(?<![A-Za-z0-9])github_pat_([A-Za-z0-9_]{30,})"), lambda tail: looks_real(tail)),
    (re.compile(r"(?<![A-Za-z0-9])xox[abprs]-([A-Za-z0-9-]{10,})"), lambda tail: looks_real(tail)),
    (re.compile(r"(?<![A-Za-z0-9])(AKIA[0-9A-Z]{16})(?![A-Za-z0-9])"), lambda tail: not tail.endswith("EXAMPLE")),   # пример из документации AWS
    (re.compile(r"(?<![A-Za-z0-9])(AIza[0-9A-Za-z_-]{35})"), lambda tail: looks_real(tail)),
    (re.compile(r"(?<![A-Za-z0-9])(?:ba|fa)_([A-Za-z0-9_-]{32,})"), lambda tail: three_classes(tail) and looks_real(tail)),
)
BINARY_STRING_RE = re.compile(rb"(?:[\x20-\x7e\t]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}){%d,}" % MIN_STRING)
LETTER_BEFORE = r"(?<![^\W\d_])"
LETTER_AFTER = r"(?![^\W\d_])"


class GateError(Exception):
    """Ворота не могли работать: нет списка слов, нет git, не открылся каталог."""


class Report:
    def __init__(self):
        self.findings, self.suppressed, self.files_checked, self.words_loaded = [], 0, 0, 0

    def summary(self):
        by_kind = Counter(f["kind"] for f in self.findings)
        by_file = Counter(f["file"] for f in self.findings)
        by_dir = Counter(f["file"].split("/")[0] if "/" in f["file"] else "." for f in self.findings)
        by_entry = Counter(f["entry"] for f in self.findings if "entry" in f)       # номер строки списка слов → находок
        order = lambda c: dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))  # noqa: E731
        return {"total": len(self.findings), "files_checked": self.files_checked, "suppressed": self.suppressed,
                "words_loaded": self.words_loaded, "by_kind": order(by_kind), "by_file": order(by_file), "by_dir": order(by_dir),
                "by_entry": order(by_entry)}


# ── разбор подозрительного ──────────────────────────────────────
def shorten(text):
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX - 1] + "…"


def entropy(s):
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def obvious_sample(s):
    """Явный образец: повтор короткого куска или почти сплошной возрастающий ряд знаков."""
    n = len(s)
    for p in range(1, 17):
        if n > p and s == (s[:p] * (n // p + 1))[:n]:
            return True
    low = s.lower()
    return any(sum(1 for a, b in zip(low, low[step:]) if ord(b) - ord(a) == 1) >= 0.8 * (n - step) for step in (1, 2))


def looks_real(s):
    return len(set(s)) >= 8 and not obvious_sample(s)


def three_classes(s):
    return any(c.islower() for c in s) and any(c.isupper() for c in s) and any(c.isdigit() for c in s)


def random_like(token):
    """Длинная строка из случайных знаков: шестнадцатеричная с достаточным разнообразием или смешанная из трёх классов знаков."""
    core = token.strip("=")
    if len(core) < 32 or obvious_sample(core):
        return False
    if "/" in core and "+" not in core and not token.endswith("="):
        return False                                                  # путь, а не base64
    if HEX_RE.fullmatch(core):
        return len(set(core)) >= 8 and entropy(core) >= 3.0
    return three_classes(core) and len(set(core)) >= 12 and entropy(core) >= 4.0


def mask(token):
    return token[:4] + "…" + token[-2:] + "[%d]" % len(token)


def secret_hits(line):
    """Значения, похожие на токены и ключи, в виде замаскированного текста."""
    hits, spans = [], []
    for m in KEY_HEADER_RE.finditer(line):
        hits.append(m.group(0))
        spans.append(m.span())
    for rx, check in PREFIXED_SECRETS:
        for m in rx.finditer(line):
            if check(m.group(1)):
                hits.append(mask(m.group(0)))
                spans.append(m.span())
    for m in CANDIDATE_RE.finditer(line):
        if any(m.start() < e and s < m.end() for s, e in spans):
            continue
        if random_like(m.group(0)):
            hits.append(mask(m.group(0)))
    return hits


def is_example_domain(host):
    return any(host == d or host.endswith("." + d) for d in EXAMPLE_DOMAINS)


def allowed_ip(ip):
    return ip.is_loopback or ip == ipaddress.ip_address("0.0.0.0") or any(ip in net for net in DOC_NETS)


class Rules:
    """Всё, что нужно для проверки строки: слова, разрешённые узлы, рабочие имена (необязательно: образец из load_names)."""

    def __init__(self, words, hosts, names=None):
        self.words, self.hosts, self.names = words, hosts, names

    def host_allowed(self, host):
        if host == "localhost" or host.endswith(".localhost") or is_example_domain(host) or host.rsplit(".", 1)[-1] in RESERVED_TLDS:
            return True
        return any(host.endswith(p[1:]) if p.startswith("*.") else host == p for p in self.hosts)

    def scan_line(self, line, name_only=False):
        """Находки одной строки: список (вид, текст, номер строки списка для word) без повторов.
        name_only — это имя файла: без ссылок и секретов."""
        found = []
        for m in HOME_RE.finditer(line):
            name = m.group("name").rstrip(".-").lower()
            if len(name) > 1 and name not in PLACEHOLDER_NAMES:
                found.append(("home_path", m.group(0).rstrip(".-"), None))
        nline = unicodedata.normalize("NFC", line)
        if self.words[0].search(nline):                 # быстрый проход без групп; номера строк списка — только там, где что-то нашлось
            for m in self.words[1].finditer(nline):
                found.append(("word", m.group(0), int(m.lastgroup[1:])))
        for m in EMAIL_RE.finditer(line):
            domain = m.group(0).rsplit("@", 1)[1].lower()
            tld = domain.rsplit(".", 1)[1]
            if tld.isalpha() and len(tld) >= 2 and tld not in FILE_EXTENSIONS and not is_example_domain(domain):
                found.append(("email", m.group(0), None))
        for m in IP_RE.finditer(line):
            try:
                ip = ipaddress.IPv4Address(m.group(0))
            except ValueError:
                continue
            if not allowed_ip(ip) and not VERSION_BEFORE.search(line[max(0, m.start() - 24):m.start()]):
                found.append(("ip", m.group(0), None))
        rest = line
        if not name_only:
            for m in URL_RE.finditer(line):
                host = m.group("host").rstrip(".-").lower()
                if not host.replace(".", "").isdigit() and not self.host_allowed(host):
                    found.append(("host", m.group(0).rstrip(".,;:!?"), None))
            rest = URL_RE.sub(lambda m: " " * len(m.group(0)), line)
        for m in HOST_RE.finditer(rest):
            if m.group(0).split(".", 1)[0].lower() not in NOT_HOST_HOLDERS:
                found.append(("host", m.group(0), None))
        if not name_only:
            for text in secret_hits(line):
                found.append(("secret", text, None))
        for m in (self.names.finditer(line) if self.names else ()):
            found.append(("working_name", m.group(0), None))
        seen, unique = set(), []
        for kind, text, entry in found:
            if (kind, text) not in seen:
                seen.add((kind, text))
                unique.append((kind, text, entry))
        return unique


# ── списки: слова, исключения, узлы ─────────────────────────────
def resolve_words_path(arg):
    path = arg or os.environ.get(WORDS_ENV)
    if not path:
        raise GateError("не задан список запретных слов: укажите --words ФАЙЛ или переменную окружения %s; "
                        "без списка ворота не считаются пройденными" % WORDS_ENV)
    return os.path.expanduser(path)


def load_words(path):
    """Пара регулярных выражений по списку слов (быстрое и с номерами строк списка) и число записей.
    Содержимое списка дальше не выходит."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise GateError("список запретных слов не открывается (%s): %s" % (path, e.strerror or e)) from e
    exact, stems = [], []                               # (номер строки списка, образец)
    for n, line in enumerate(raw.decode("utf-8-sig", errors="replace").split("\n"), 1):
        line = re.split(r"(?:^|\s)#", line.rstrip("\r"), maxsplit=1)[0].strip()
        if not line:
            continue
        stem = line.endswith("*")
        parts = unicodedata.normalize("NFC", line.rstrip("*")).split()
        if parts:
            (stems if stem else exact).append((n, r"\s+".join(re.escape(p) for p in parts)))
    if not exact and not stems:
        raise GateError("в списке запретных слов (%s) нет ни одной записи: такие ворота ничего не проверяют" % path)

    def build(named):                                   # имя группы e17 / s17 несёт номер строки списка
        def group(prefix, items):
            items = sorted(items, key=lambda it: len(it[1]), reverse=True)
            return "|".join(("(?P<%s%d>%s)" % (prefix, n, p)) if named else p for n, p in items)

        alts = []
        if exact:
            alts.append("(?:" + group("e", exact) + ")" + LETTER_AFTER)
        if stems:
            alts.append("(?:" + group("s", stems) + ")")
        return re.compile(LETTER_BEFORE + "(?:" + "|".join(alts) + ")", re.IGNORECASE)

    return (build(False), build(True)), len(exact) + len(stems)


def resolve_names_path(arg):
    """Путь к списку рабочих имён: ключ или переменная окружения; список необязателен, нет пути — None."""
    path = arg or os.environ.get(NAMES_ENV)
    return os.path.expanduser(path) if path else None


def load_names(path):
    """Образец по списку рабочих имён (регистр не важен, границ слова нет) или None, если списка нет. Имена дальше не выходят."""
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise GateError("список рабочих имён не открывается (%s): %s" % (path, e.strerror or e)) from e
    names = []
    for line in raw.decode("utf-8-sig", errors="replace").split("\n"):
        line = re.split(r"(?:^|\s)#", line.rstrip("\r"), maxsplit=1)[0].strip()
        if line:
            names.append(line)
    if not names:
        raise GateError("в списке рабочих имён (%s) нет ни одной записи" % path)
    return re.compile("|".join(re.escape(unicodedata.normalize("NFC", n)) for n in sorted(names, key=len, reverse=True)), re.IGNORECASE)


def read_lines(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8-sig", errors="replace").splitlines()
    except OSError:
        return None


class Allow:
    """Файл исключений и список разрешённых узлов; строки без причины отвергаются и сами становятся находками."""

    def __init__(self, root):
        self.entries, self.problems, self.hosts = [], [], []
        for n, line in enumerate(read_lines(os.path.join(root, ALLOW_FILE)) or [], 1):
            line = line.strip()
            if line and not line.startswith("#"):
                self._entry(n, line)
        for n, line in enumerate(read_lines(os.path.join(root, HOSTS_FILE)) or [], 1):
            line = line.strip()
            if line and not line.startswith("#"):
                self._host(n, line)

    def _entry(self, n, line):
        parts = [p.strip() for p in line.split("|", 3)]
        why = None
        if len(parts) != 4:
            why = "нужно четыре части: образец | вид | текст | причина"
        else:
            pattern, kind, text, reason = parts
            if not reason:
                why = "исключение без причины не принимается"
            elif kind not in KINDS or kind == "allowlist":
                why = "неизвестный вид исключения"
            elif not pattern or not text:
                why = "нужны образец пути и текст"
            elif text == "*" and kind in NOT_WHOLE_FILE:
                why = "вид %s целиком для файла не исключается, только конкретный текст" % kind
        if why:
            self.problems.append({"file": ALLOW_FILE, "line": n, "kind": "allowlist", "text": shorten(why)})
        else:
            self.entries.append((n, pattern, kind, text))

    def _host(self, n, line):
        parts = [p.strip() for p in line.split("|", 1)]
        if len(parts) != 2 or not parts[1] or not re.fullmatch(r"(?:\*\.)?[A-Za-z0-9][A-Za-z0-9.-]*", parts[0]):
            self.problems.append({"file": HOSTS_FILE, "line": n, "kind": "allowlist",
                                  "text": "нужны узел и причина: узел | причина"})
        else:
            self.hosts.append(parts[0].lower())

    def covers(self, f):
        """Исключение снимает находку. Находка в самом файле исключений покрывается своей же строкой."""
        for n, pattern, kind, text in self.entries:
            if kind != f["kind"] or not (text == "*" or text == f["text"]):
                continue
            if fnmatch.fnmatchcase(f["file"], pattern):
                return "rule"
            if f["file"] == ALLOW_FILE and f["line"] == n:
                return "self"
        return None


# ── проверка файла ──────────────────────────────────────────────
def private_path_findings(rel):
    parts = rel.split("/")
    out = [d for d in parts[:-1] if d in PRIVATE_DIRS]
    base = parts[-1].lower()
    if any(fnmatch.fnmatchcase(base, g) for g in PRIVATE_FILES) and base not in PRIVATE_FILES_OK:
        out.append(parts[-1])
    return out[:1]


def check_file(rel, path, rules):
    finds = []

    def add(line, kind, text, **extra):
        finds.append({"file": rel, "line": line, "kind": kind, "text": shorten(text), **extra})

    for kind, text, entry in rules.scan_line(rel, name_only=True):
        add(0, kind, text, **({"entry": entry} if entry else {}))
    for text in private_path_findings(rel):
        add(0, "tracked_private", text)
    if os.path.islink(path):
        return finds                                    # на что смотрит ссылка, ворота не идут
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read(MAX_READ_BYTES + 1)
    except OSError:
        return finds                                    # отслеживается, но с диска исчез
    if len(data) > MAX_READ_BYTES:
        data = data[:MAX_READ_BYTES]
        add(0, "too_large", "%d байт, предел %d: проверь руками" % (size, MAX_READ_BYTES))
    if b"\0" in data[:BINARY_PROBE]:
        seen = set()
        for m in BINARY_STRING_RE.finditer(data):
            for kind, text, entry in rules.scan_line(m.group(0).decode("utf-8", errors="replace")):
                if (kind, text) not in seen:
                    seen.add((kind, text))
                    add(0, kind, text, offset=m.start(), **({"entry": entry} if entry else {}))
        return finds
    text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    for n, line in enumerate(text.split("\n"), 1):
        for kind, found, entry in rules.scan_line(line.rstrip("\r")):
            add(n, kind, found, **({"entry": entry} if entry else {}))
    return finds


def gitignore_findings(root):
    lines = read_lines(os.path.join(root, ".gitignore"))
    if lines is None:
        return [{"file": ".gitignore", "line": 0, "kind": "tracked_private", "text": "нет файла .gitignore"}]
    have = set()
    for line in lines:
        s = line.strip()
        if s and not s.startswith(("#", "!")):
            s = s.lstrip("/")
            have.add(s.removeprefix("**/").removesuffix("/**").rstrip("/"))
    return [{"file": ".gitignore", "line": 0, "kind": "tracked_private", "text": d + "/"} for d in PRIVATE_DIRS if d not in have]


# ── список файлов ───────────────────────────────────────────────
def clean_git_env(env=None):
    """Копия окружения (по умолчанию — процесса) без переменных, которыми git выбирает репозиторий: GIT_LOCAL_VARIABLES."""
    return {name: value for name, value in (os.environ if env is None else env).items() if name not in GIT_LOCAL_VARIABLES}


def git_files(repo):
    def git(*args):
        try:
            return subprocess.run(["git", "-C", repo, *args], capture_output=True, timeout=120, env=clean_git_env())
        except FileNotFoundError as e:
            raise GateError("не найдена программа git: без неё не узнать, какие файлы отслеживаются (проверить каталог целиком — ключ --root)") from e

    top = git("rev-parse", "--show-toplevel")
    top_dir = top.stdout.decode("utf-8", "replace").strip()
    if top.returncode != 0:
        why = (top.stderr.decode("utf-8", "replace").strip().splitlines() or ["нет подробностей"])[-1]
        raise GateError("%s не репозиторий git (%s); для каталога-снимка нужен ключ --root" % (repo, why))
    if os.path.normcase(os.path.realpath(top_dir)) != os.path.normcase(os.path.realpath(repo)):
        raise GateError("%s не корень репозитория git: корень — %s" % (repo, top_dir))
    ls = git("ls-files", "-z")
    if ls.returncode != 0:
        raise GateError("git ls-files не сработал: " + ls.stderr.decode("utf-8", "replace").strip())
    return [os.fsdecode(n) for n in ls.stdout.split(b"\0") if n], []


def walk_files(root):
    """Все файлы каталога и пути найденных в нём .git (в снимке без истории их быть не должно); .git не читается."""
    names, git_dirs = [], []
    for base, dirs, files in os.walk(root):
        rel_base = os.path.relpath(base, root).replace(os.sep, "/")
        for entry in (".git",):
            if entry in dirs or entry in files:
                git_dirs.append(entry if rel_base == "." else rel_base + "/" + entry)
                if entry in dirs:
                    dirs.remove(entry)
                if entry in files:
                    files.remove(entry)
        dirs.sort()
        for name in sorted(files):
            names.append(name if rel_base == "." else rel_base + "/" + name)
    return names, git_dirs


# ── прогон ──────────────────────────────────────────────────────
def scan(root=None, words_path=None, repo=None, names_path=None):
    """Проверить файлы. root=None — отслеживаемые git файлы репозитория repo (по умолчанию — где лежит сценарий).
    names_path — список рабочих имён (по умолчанию — из переменной окружения; нет списка — вид working_name не ищется)."""
    words, count = load_words(resolve_words_path(words_path))
    working = load_names(resolve_names_path(names_path))
    if root is None:
        base = os.path.abspath(repo or REPO_ROOT)
        names, git_dirs = git_files(base)
    else:
        base = os.path.abspath(root)
        if not os.path.isdir(base):
            raise GateError("%s не каталог" % base)
        names, git_dirs = walk_files(base)
    allow = Allow(base)
    rules = Rules(words, allow.hosts, working)
    rep = Report()
    rep.words_loaded = count
    finds = []
    for rel in names:
        path = os.path.join(base, rel)
        if os.path.isdir(path) and not os.path.islink(path):
            continue                                    # вложенный репозиторий в списке git
        finds.extend(check_file(rel, path, rules))
        rep.files_checked += 1
    for rel in git_dirs:
        finds.append({"file": rel, "line": 0, "kind": "tracked_private", "text": ".git"})
    finds.extend(gitignore_findings(base))
    kept = []
    for f in finds:
        how = allow.covers(f)
        if how == "rule":
            rep.suppressed += 1
        elif how is None:
            kept.append(f)
    kept.extend(allow.problems)
    rep.findings = sorted(kept, key=lambda f: (f["file"], f["line"], f["kind"], f["text"]))
    return rep


def render(rep, out):
    for f in rep.findings:
        tail = " (двоичный файл, смещение %d)" % f["offset"] if "offset" in f else ""
        print("%s:%d: %s: %s%s" % (f["file"], f["line"], f["kind"], f["text"], tail), file=out)
    s = rep.summary()
    if not rep.findings:
        print("Ворота пройдены: находок нет; проверено файлов: %d; снято исключениями: %d; слов в списке: %d"
              % (s["files_checked"], s["suppressed"], s["words_loaded"]), file=out)
        return
    print("Итого находок: %d; файлов с находками: %d; проверено файлов: %d; снято исключениями: %d"
          % (s["total"], len(s["by_file"]), s["files_checked"], s["suppressed"]), file=out)
    for kind, n in s["by_kind"].items():
        print("  %s: %d" % (kind, n), file=out)
    if s["by_entry"]:                                   # какие строки списка дают шум; сами слова не печатаем
        top = list(s["by_entry"].items())[:8]
        print("Чаще всего срабатывают записи списка (номер строки: находок): " + ", ".join("%d: %d" % kv for kv in top), file=out)


def main(argv=None, stdout=None, stderr=None):
    out, err = stdout or sys.stdout, stderr or sys.stderr
    ap = argparse.ArgumentParser(prog="publication_gate.py", description="Ворота публикации: личное и секретное в файлах репозитория.")
    ap.add_argument("--root", help="проверить все файлы каталога (чистый снимок) вместо отслеживаемых git")
    ap.add_argument("--words", help="файл запретных слов; по умолчанию — переменная окружения " + WORDS_ENV)
    ap.add_argument("--names", help="файл рабочих имён проекта (необязательно); по умолчанию — переменная окружения " + NAMES_ENV)
    ap.add_argument("--json", action="store_true", help="вывести находки и сводку в виде JSON")
    args = ap.parse_args(argv)
    try:
        rep = scan(root=args.root, words_path=args.words, names_path=args.names)
    except GateError as e:
        print("Ворота не могли работать: %s" % e, file=err)
        if args.json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False), file=out)
        return 2
    except Exception as e:                              # ворота, которые упали, не должны выглядеть как «есть находки»
        print("Ворота не могли работать: внутренняя ошибка %s: %s" % (type(e).__name__, e), file=err)
        return 2
    if args.json:
        print(json.dumps({"findings": rep.findings, "summary": rep.summary()}, ensure_ascii=False, indent=1), file=out)
    else:
        render(rep, out)
    return 1 if rep.findings else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

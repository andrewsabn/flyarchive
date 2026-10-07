"""Таблица источников (FR-99): в какой базе лежит документ, какие повторы убираются из индекса и под какими именами корни лежали раньше.

Всё, что относится к конкретному владельцу (названия организаций, почтовые домены, имена каталогов выгрузок, подписи баз), лежит не в коде,
а в файле sources.json каталога архива. Файл необязателен: без него архив работает с одной базой — принятым через входящую папку.

    import sources
    table = sources.startup()          # один раз на службу или команду и при загрузке модуля: отказ — одна строка в stderr и выход с кодом 2
    table = sources.load()             # то же, но отказ — SourcesError с кодом sources.*
    table.meta_for(source, root, path, text) -> (база, раздел, название, дата)
    table.mail_roots(), .pages_roots(), .ranks(), .aliases, .labels, .walk(корпус)

Формат файла (ключи фиксированы, лишний ключ — отказ; все три раздела необязательны):

    {"roots": {"<имя каталога корня в корпусе>": {
                  "kind": "mail" | "pages" | "files",          обязателен
                  "source": "<база по умолчанию>",             обязателен
                  "attachments": "<база вложений>",            только pages, обязателен
                  "rules": [{"contains": ["<подстрока пути строчными>", ...], "source": "<база>"},     только mail: первое сработавшее по порядку
                            {"first_part_prefix": "<приставка первого каталога>", "source": "<база>"}], только files
                  "title": "folder",                           только files: название «раздел · имя»
                  "rank": 0}},                                 только mail: место при чистке повторов, меньше — предпочтительнее
     "labels": {"<база>": "<подпись>"},
     "aliases": {"<старое имя корня>": "<нынешнее имя корня>"}}

Смысл видов (правила переносятся из кода как есть, без единого отличия):
    mail   раздел — первый каталог пути; база — по первому сработавшему правилу (подстрока в относительном пути, приведённом к нижнему регистру),
           иначе source; название — имя файла без расширения и без даты в начале, не длиннее 200 знаков, а если в тексте есть строка «Тема: …» — она;
    pages  документ, у которого в каталоге пути есть _attachments, идёт в базу attachments, иначе в source; название вложения — «ключ · имя»;
    files  раздел — первый каталог; правило first_part_prefix выбирает базу по приставке первого каталога (startswith, с учётом регистра);
           title folder даёт название «раздел · имя»; без него название, как у страниц.
Корень принятого через входящую папку встроен в код (INTAKE): он ведёт себя как корень files с базой «входящие», и в файле его не задать.
Корень, которого нет в таблице, берёт базу, названную обходом, и название, как у страниц.

Ранг корня при чистке повторов: меньше — предпочтительнее, не задан — последний; порядок почтовых корней — порядок в файле.
Порядок обхода (walk): сначала вложения страниц в порядке файла, затем остальные корни и сами страницы в порядке файла, входящие последними.

Загрузка — как у настроек: у открытого файла проверяются владелец и права (чужой владелец или запись для группы и остальных — отказ),
файл читается один раз, `load` ничего не создаёт и не пишет. Отказ — SourcesError с кодом sources.* из каталога сообщений. Значения из файла
в отказ не попадают никогда: только место — корень, номер правила (с единицы), ключ — и что ожидалось. Псевдоним может вести только на корень
таблицы (или встроенный корень входящих): имя корня — одно имя каталога без разделителей, поэтому путь из индекса не выйдет за корпус.
"""
import json
import os
import re
import stat
import sys
import time
import unicodedata
from collections import namedtuple

import messages
import settings

FILE_NAME = "sources.json"
PRODUCT = "FlyArchive"                 # название продукта: единственное место, откуда его берут описания для программ
INTAKE = "входящие"                    # корень и база принятого через входящую папку: встроены в код, в таблице их не задать
INTAKE_LABEL = "принятое через входящую папку"      # подпись этой базы, пока владелец не назвал свою в labels: описания инструментов и форма поиска
POSIX = os.name == "posix"             # права и владелец файла проверяются там, где они есть
MAX_FILE_BYTES = 1 << 20               # таблица в мегабайт — уже ошибка: память на неё не тратим
MAX_TEXT = 200                         # база, подстрока, приставка
MAX_LABEL = 500                        # подпись базы
MAX_NAME = 255                         # имя каталога
MAX_RANK = 1000
KINDS = ("mail", "pages", "files")
ATTACHMENTS = "_attachments"           # каталог вложений в корпусе: так его называют выгрузки страниц и писем
TOP_KEYS = ("roots", "labels", "aliases")
ROOT_KEYS = ("kind", "source", "attachments", "rules", "title", "rank")
RULE_KEYS = ("contains", "first_part_prefix", "source")
DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")
DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_")
SUBJECT = re.compile(r"^Тема: (.+)$", re.M)
ATTACHMENT_KEY = re.compile(r"_attachments[\\/]([^\\/]+)")

Root = namedtuple("Root", "name kind source attachments rules title rank")
Rule = namedtuple("Rule", "contains prefix source")          # у правила заполнено одно из двух: contains (кортеж строк) или prefix
_INTAKE_ROOT = Root(INTAKE, "files", INTAKE, None, (), None, None)


class SourcesError(messages.CodedError):
    """Отказ таблицы источников: неизвестный ключ, негодное значение, чужие права на файл, битый файл."""


class _Duplicate(Exception):
    def __init__(self, key):
        self.key = key


def _control(text):
    """Управляющие знаки и разделители строк (в том числе нулевой байт и перевод строки)."""
    return any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in text)


def _shown(key):
    """Имя из файла для сообщения: не длиннее 64 знаков, управляющие знаки заменены."""
    text = key if isinstance(key, str) else repr(key)
    return "".join("?" if unicodedata.category(ch) in ("Cc", "Zl", "Zp") else ch for ch in text[:64]) + ("…" if len(text) > 64 else "")


# ── проверка значений: годное значение или отказ с местом ───────
def _object(value, where):
    if isinstance(value, dict):
        return value
    raise SourcesError(messages.make("sources.not_object", where=where))


def _plain(value, where, high):
    """Непустая строка без управляющих знаков не длиннее high."""
    if isinstance(value, str) and 0 < len(value) <= high and not _control(value):
        return value
    raise SourcesError(messages.make("sources.not_text", where=where, high=high))


def _base(value, where):
    """База: обычная строка, но не встроенная база входящих."""
    _plain(value, where, MAX_TEXT)
    if value == INTAKE:
        raise SourcesError(messages.make("sources.reserved_base", where=where))
    return value


def _good_name(value):
    """Одно имя каталога: без разделителей, не «.» и не «..», без управляющих знаков."""
    return (isinstance(value, str) and 0 < len(value) <= MAX_NAME and value not in (".", "..") and "/" not in value and "\\" not in value
            and not _control(value))


def _contains(value, where):
    if not isinstance(value, list) or not value:
        raise SourcesError(messages.make("sources.not_list", where=where))
    for number, item in enumerate(value, 1):
        _plain(item, f"{where}.{number}", MAX_TEXT)
        if item != item.lower():                  # путь сравнивается в нижнем регистре: заглавная подстрока не сработала бы никогда
            raise SourcesError(messages.make("sources.not_lowercase", where=f"{where}.{number}"))
    return tuple(value)


def _prefix(value, where):
    if isinstance(value, str) and 0 < len(value) <= MAX_TEXT and "/" not in value and "\\" not in value and not _control(value):
        return value
    raise SourcesError(messages.make("sources.bad_prefix", where=where))


def _rules(kind, raw, where):
    if not isinstance(raw, list):
        raise SourcesError(messages.make("sources.not_list", where=where))
    out = []
    for number, item in enumerate(raw, 1):
        here = f"{where}.{number}"
        _object(item, here)
        for key in item:
            if key not in RULE_KEYS:
                raise SourcesError(messages.make("sources.unknown_key", where=f"{here}.{_shown(key)}"))
        features = [key for key in ("contains", "first_part_prefix") if key in item]
        if len(features) != 1:
            raise SourcesError(messages.make("sources.bad_rule", where=here))
        if "source" not in item:
            raise SourcesError(messages.make("sources.rule_no_source", where=here))
        feature = features[0]
        if (kind == "mail") != (feature == "contains"):          # подстроки — у почты, приставка — у файлов
            raise SourcesError(messages.make("sources.key_not_for_kind", where=f"{here}.{feature}", kind=kind))
        source = _base(item["source"], f"{here}.source")
        if feature == "contains":
            out.append(Rule(_contains(item["contains"], f"{here}.contains"), None, source))
        else:
            out.append(Rule(None, _prefix(item["first_part_prefix"], f"{here}.first_part_prefix"), source))
    return tuple(out)


def _root(name, entry):
    where = f"roots.{_shown(name)}"
    if name == INTAKE:
        raise SourcesError(messages.make("sources.reserved_root", where=where))
    if not _good_name(name):
        raise SourcesError(messages.make("sources.bad_name", where=where))
    _object(entry, where)
    for key in entry:
        if key not in ROOT_KEYS:
            raise SourcesError(messages.make("sources.unknown_key", where=f"{where}.{_shown(key)}"))
    if "kind" not in entry:
        raise SourcesError(messages.make("sources.missing_key", where=f"{where}.kind"))
    kind = entry["kind"]
    if not isinstance(kind, str) or kind not in KINDS:
        raise SourcesError(messages.make("sources.bad_kind", where=f"{where}.kind"))
    if "source" not in entry:
        raise SourcesError(messages.make("sources.missing_key", where=f"{where}.source"))
    source = _base(entry["source"], f"{where}.source")
    attachments = None
    if "attachments" in entry:
        if kind != "pages":
            raise SourcesError(messages.make("sources.key_not_for_kind", where=f"{where}.attachments", kind=kind))
        attachments = _base(entry["attachments"], f"{where}.attachments")
    elif kind == "pages":
        raise SourcesError(messages.make("sources.missing_key", where=f"{where}.attachments"))
    rules = ()
    if "rules" in entry:
        if kind == "pages":
            raise SourcesError(messages.make("sources.key_not_for_kind", where=f"{where}.rules", kind=kind))
        rules = _rules(kind, entry["rules"], f"{where}.rules")
    title = None
    if "title" in entry:
        if kind != "files":
            raise SourcesError(messages.make("sources.key_not_for_kind", where=f"{where}.title", kind=kind))
        if not isinstance(entry["title"], str) or entry["title"] != "folder":
            raise SourcesError(messages.make("sources.bad_title", where=f"{where}.title"))
        title = "folder"
    rank = None
    if "rank" in entry:
        if kind != "mail":
            raise SourcesError(messages.make("sources.key_not_for_kind", where=f"{where}.rank", kind=kind))
        rank = entry["rank"]
        if isinstance(rank, bool) or not isinstance(rank, int) or not 0 <= rank <= MAX_RANK:
            raise SourcesError(messages.make("sources.bad_rank", where=f"{where}.rank", high=MAX_RANK))
    return Root(name, kind, source, attachments, rules, title, rank)


def _labels(raw):
    out = {}
    for base, label in raw.items():
        where = f"labels.{_shown(base)}"
        _plain(base, where, MAX_TEXT)
        out[base] = _plain(label, where, MAX_LABEL)
    return out


def _aliases(raw, roots):
    out = {}
    for old, new in raw.items():
        where = f"aliases.{_shown(old)}"
        if not _good_name(old):
            raise SourcesError(messages.make("sources.bad_name", where=where))
        if old == INTAKE or old in roots:                 # псевдоним закрыл бы настоящий корень
            raise SourcesError(messages.make("sources.alias_clash", where=where))
        if not isinstance(new, str):
            raise SourcesError(messages.make("sources.not_text", where=where, high=MAX_NAME))
        if not _good_name(new):                           # «..» и абсолютный путь выводили бы за корпус
            raise SourcesError(messages.make("sources.bad_name", where=where))
        if new not in roots and new != INTAKE:
            raise SourcesError(messages.make("sources.alias_target", where=where))
        out[old] = new
    return out


# ── сама таблица ────────────────────────────────────────────────
class Table:
    """Разобранная таблица: корни по порядку файла, подписи баз, псевдонимы. Не меняется; выдаваемые словари — копии."""
    __slots__ = ("_roots", "_labels", "_aliases")

    def __init__(self, roots, labels, aliases):
        object.__setattr__(self, "_roots", dict(roots))
        object.__setattr__(self, "_labels", dict(labels))
        object.__setattr__(self, "_aliases", dict(aliases))

    def __setattr__(self, name, value):
        raise AttributeError("таблица источников неизменяема: загрузи заново")

    def __delattr__(self, name):
        raise AttributeError("таблица источников неизменяема: загрузи заново")

    @property
    def roots(self):
        return dict(self._roots)

    @property
    def labels(self):
        return dict(self._labels)

    @property
    def aliases(self):
        return dict(self._aliases)

    def root(self, name):
        """Корень по имени каталога: из таблицы или встроенный корень входящих; None — корня не знаем."""
        found = self._roots.get(name)
        return found if found is not None else (_INTAKE_ROOT if name == INTAKE else None)

    def mail_roots(self):
        return tuple(name for name, r in self._roots.items() if r.kind == "mail")

    def pages_roots(self):
        return tuple(name for name, r in self._roots.items() if r.kind == "pages")

    def ranks(self):
        return {name: r.rank for name, r in self._roots.items() if r.rank is not None}

    def walk(self, corpus):
        """Корни обхода корпуса: [(база или «mail», каталог, признак)]; признак «_attachments» — только там, где он есть,
        «!_attachments» — всё остальное."""
        out = [(r.attachments, os.path.join(corpus, name), ATTACHMENTS) for name, r in self._roots.items() if r.kind == "pages"]
        for name, r in self._roots.items():
            folder = os.path.join(corpus, name)
            if r.kind == "pages":
                out.append((r.source, folder, "!" + ATTACHMENTS))
            else:
                out.append(("mail" if r.kind == "mail" else r.source, folder, None))
        out.append((INTAKE, os.path.join(corpus, INTAKE), None))
        return out

    def meta_for(self, source, root, path, text):
        """(база, раздел, название, дата) документа path корня root. Правила — по таблице; source — база, названная обходом, нужна только
        корню, которого в таблице нет. Дата — из имени файла, иначе из времени его изменения."""
        rel = os.path.relpath(path, root)
        name = os.path.basename(path)
        found = DATE_IN_NAME.search(name)
        updated = found.group(1) if found else ""
        if not updated:
            try:
                updated = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
            except OSError:
                updated = ""
        space = rel.split(os.sep)[0]
        entry = self.root(os.path.basename(root))
        if entry is not None and entry.kind == "mail":
            low = rel.lower()
            base = entry.source
            for rule in entry.rules:
                if any(part in low for part in rule.contains):
                    base = rule.source
                    break
            title = DATE_PREFIX.sub("", os.path.splitext(name)[0])[:200]
            subject = SUBJECT.search(text)
            if subject:
                title = subject.group(1)[:200]
            return base, space, title[:200], updated
        base = source if entry is None else entry.source
        if entry is not None and entry.kind == "pages" and ATTACHMENTS in os.path.dirname(path):
            base = entry.attachments
        if entry is not None and entry.kind == "files":
            for rule in entry.rules:
                if space.startswith(rule.prefix):
                    base = rule.source
                    break
        if entry is not None and entry.title == "folder":
            title = space + " · " + name
        else:
            key = ATTACHMENT_KEY.search(path)
            title = (key.group(1) + " · " if key else "") + name
        return base, space, title[:200], updated

    def old_meta(self, corpus, rel, full):
        """Что записал бы основной индексатор о старом документе корпуса; None — корень корпуса таблице неизвестен.
        rel — путь от корпуса через «/»."""
        head = rel.split("/")[0]
        entry = self.root(head)
        if entry is None:
            return None
        return self.meta_for(entry.source, os.path.join(corpus, head), full, "")

    def bases(self):
        """Базы таблицы для описаний: сначала названные подписями, затем базы корней и правил по порядку файла, входящие последними."""
        out = list(self._labels)
        for r in self._roots.values():
            for base in (r.source, r.attachments, *(rule.source for rule in r.rules)):
                if base is not None and base not in out:
                    out.append(base)
        if INTAKE not in out:
            out.append(INTAKE)
        return out


def normalize(path, aliases):
    """Путь из индекса в том виде, как он записан в базе известного: прямые слэши, без ведущих, нынешнее имя корня (по псевдонимам)."""
    p = path.replace("\\", "/").lstrip("/")
    first, sep, rest = p.partition("/")
    return aliases.get(first, first) + sep + rest


def scope_text(table):
    """Что лежит в архиве, одной фразой для описаний инструментов: базы с подписями, названными владельцем; без таблицы — только входящие."""
    if not table.roots and not table.labels:
        return "документы, принятые через входящую папку"
    labels = table.labels
    parts = []
    for base in table.bases():
        label = labels.get(base) or (INTAKE_LABEL if base == INTAKE else "")
        parts.append(f"{base} — {label}" if label else base)
    return "базы " + "; ".join(parts)


def bases_text(table):
    """Имена баз через запятую: подсказка для параметра «source»."""
    return ", ".join(table.bases())


# ── файл sources.json ───────────────────────────────────────────
def _owner_uid():
    return os.geteuid() if hasattr(os, "geteuid") else None


def _open_checked(path):
    """Файл таблицы, открытый на чтение, или None, если его нет. Права смотрятся у самого открытого файла (по ссылке — у того, на что она ведёт),
    до первого чтения: файл чужого владельца или с записью для группы и остальных не читается."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))     # без O_NONBLOCK канал повисит
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        raise SourcesError(messages.make("sources.file_unreadable", path=path, error_type=type(e).__name__)) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SourcesError(messages.make("sources.file_unreadable", path=path, error_type="NotAFile"))
        if POSIX and (st.st_uid != _owner_uid() or st.st_mode & 0o022):
            raise SourcesError(messages.make("sources.file_open", path=path))
        if st.st_size > MAX_FILE_BYTES:
            raise SourcesError(messages.make("sources.file_unreadable", path=path, error_type="TooLarge"))
        handle, fd = os.fdopen(fd, "rb"), None           # дальше дескриптор принадлежит рукоятке
        return handle
    except OSError as e:
        raise SourcesError(messages.make("sources.file_unreadable", path=path, error_type=type(e).__name__)) from None
    finally:
        if fd is not None:
            os.close(fd)


def _pairs(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:                                  # последний молча закрыл бы первый
            raise _Duplicate(key)
        seen.add(key)
    return dict(pairs)


def _read(folder):
    """Таблица из файла каталога архива; файла нет — пустая. Читается один раз; значения в отказы не попадают."""
    path = os.path.join(folder, FILE_NAME)
    handle = _open_checked(path)
    if handle is None:
        return Table({}, {}, {})
    try:
        with handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        raise SourcesError(messages.make("sources.file_unreadable", path=path, error_type=type(e).__name__)) from None
    if len(raw) > MAX_FILE_BYTES:
        raise SourcesError(messages.make("sources.file_unreadable", path=path, error_type="TooLarge"))
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs)
    except UnicodeDecodeError:
        raise SourcesError(messages.make("sources.file_unreadable", path=path, error_type="UnicodeDecodeError")) from None
    except _Duplicate as e:
        raise SourcesError(messages.make("sources.duplicate_key", where=_shown(e.key))) from None
    except json.JSONDecodeError as e:
        raise SourcesError(messages.make("sources.bad_json", path=path, line=e.lineno, column=e.colno)) from None
    except (ValueError, RecursionError):                 # число в тысячи знаков, вложенность без конца
        raise SourcesError(messages.make("sources.bad_json", path=path, line=0, column=0)) from None
    return parse(data)


def parse(data):
    """Таблица из разобранного JSON (без файла). Отказ — SourcesError."""
    if not isinstance(data, dict):
        raise SourcesError(messages.make("sources.not_object", where=FILE_NAME))
    for key in data:
        if key not in TOP_KEYS:
            raise SourcesError(messages.make("sources.unknown_key", where=_shown(key)))
    roots = {name: _root(name, entry) for name, entry in _object(data.get("roots", {}), "roots").items()}
    labels = _labels(_object(data.get("labels", {}), "labels"))
    aliases = _aliases(_object(data.get("aliases", {}), "aliases"), roots)
    return Table(roots, labels, aliases)


def load(env=None, home=None):
    """Таблица из sources.json каталога архива (его берут из настроек: env — окружение, home — каталог, если его задаёт вызывающий).

    Файла нет — пустая таблица. Ничего не создаёт и не пишет. Отказ — SourcesError; негодные настройки — SettingsError."""
    return _read(settings.load(env=env, home=home)["home"])


def startup(env=None, home=None):
    """load для точек входа и загрузки модулей: таблица или, при отказе, одна строка «таблица источников: <текст отказа>» в stderr и выход с кодом 2.

    Без трассировки. Значений из файла в строке нет — как в самом отказе. Негодные настройки останавливает settings.startup."""
    folder = settings.startup(env=env, home=home)["home"]
    try:
        return _read(folder)
    except SourcesError as e:
        if (getattr(sys.stderr, "encoding", None) or "").lower().replace("-", "") != "utf8":
            try:
                sys.stderr.reconfigure(encoding="utf-8")        # владелец читает русский текст и при ascii-локали службы
            except (AttributeError, ValueError, OSError):
                pass
        print(f"таблица источников: {messages.of(e)}", file=sys.stderr)
        raise SystemExit(2) from None

"""Единый модуль настроек (FR-95): откуда код берёт пути, адреса, модели и пределы.

Три слоя по возрастанию силы: значение по умолчанию в коде (не привязано к машине), файл settings.json в каталоге архива,
переменная окружения FLYARCHIVE_<КЛЮЧ ЗАГЛАВНЫМИ>. Каталог архива (home) в файле быть не может: файл лежит в нём самом. Его берут
из FLYARCHIVE_HOME, иначе ~/flyarchive. У каждой настройки одна переменная окружения. Значение ключа локальной модели в настройки не
попадает: его берёт llm_check из переменной FLYARCHIVE_LLM_KEY, а в настройках лишь путь к файлу ключа (llm_key_file).

    import settings
    s = settings.load()                # один раз на службу или команду: глобального кэша нет, когда читать заново — решает вызывающий
    s = settings.startup()             # то же для точки входа и загрузки модуля: отказ — одна строка в stderr и выход с кодом 2
    s["search_port"], s.search_port    # значение; список строк — неизменяемый список
    s.source("search_port")            # default, file или env (у home ещё arg: каталог передан аргументом)
    s.as_dict()                        # обычный словарь-копия

    python3 tools/settings.py              действующие значения и источник каждого
    python3 tools/settings.py --json       то же объектом {ключ: {"value", "source"}}
    python3 tools/settings.py --reference  справочник всех настроек таблицей Markdown
    python3 tools/settings.py --get КЛЮЧ    действующее значение одной настройки (его читают запускалки на shell); список — по строке на элемент

У каждой настройки в SCHEMA есть тип (str, int, float, bool, path, url, list[str], list[path], env_name), умолчание, границы (у чисел), описание.
Путь после раскрытия ~ и {home} (в начале) обязан быть абсолютным; адрес — http или https с именем узла и без имени пользователя;
env_name — имя переменной окружения: заглавные латинские буквы, цифры и подчёркивание, первой не цифра.
Секретов в настройках нет: настройка с key, token, secret или password в имени — путь к файлу и называется ...file; содержимое
таких файлов модуль не читает. Файл настроек чужого владельца или с записью для группы и остальных не читается (как хранилище токенов).

Настройки приёмки (входящая папка, период разбора, проверка моделью, запасная облачная модель, порог, пределы распаковки, описание изображений)
в общую схему не входят: их меняет команда `inbox set` и раздел Intake в DSH, а лежат они в inbox.json каталога архива. Одна настройка —
одно место: значение, сохранённое из DSH, не может быть молча перебито переменной окружения или файлом settings.json.

Отказ — SettingsError с кодом settings.* из каталога сообщений. Значение настройки в сообщение не попадает никогда: строка из файла
или переменной может оказаться секретом по ошибке владельца, поэтому в отказе только ключ, источник («settings.json» или имя
переменной) и что ожидалось. load ничего не создаёт и не пишет, окружение не меняет.
"""
import json
import math
import os
import re
import stat
import sys
import unicodedata
import urllib.parse
from collections import namedtuple

import messages

ENV_PREFIX = "FLYARCHIVE_"
FILE_NAME = "settings.json"
EMPTY_PATH_OK = ("bin_dir",)           # путь с умолчанием, который выключается пустым значением: пусто — «не делать этого», а не ошибка
DEFAULT_HOME = "~/flyarchive"
LLM_KEY_ENV = ENV_PREFIX + "LLM_KEY"   # переменная со значением ключа локальной модели: в схеме лишь путь к файлу ключа (llm_key_file)
LLM_CLOUD_KEY_ENV = ENV_PREFIX + "LLM_CLOUD_KEY"   # то же для запасной облачной модели (путь к файлу — llm_cloud_key_file)
MAX_FILE_BYTES = 1 << 20               # файл настроек в мегабайт — уже ошибка: память на него не тратим
POSIX = os.name == "posix"             # права и владелец файла проверяются там, где они есть
# переменные FLYARCHIVE_*, которые читают другие программы и которых в схеме быть не должно: не «неиспользуемые»
ELSEWHERE = (
    "FLYARCHIVE_LLM_KEY",              # значение ключа локальной модели: только окружение, в схеме лишь путь к файлу
    "FLYARCHIVE_LLM_CLOUD_KEY",        # то же для запасной облачной модели
    "FLYARCHIVE_TOKEN",               # токен клиента команды архива
    "FLYARCHIVE_LOCAL_TOKEN",          # служебный токен
    "FLYARCHIVE_CLI",                  # путь к команде для плагина DSH
    "FLYARCHIVE_GATE_WORDS",           # список запретных слов ворот публикации
)

Spec = namedtuple("Spec", "key type default low high choices note")
# строка под таблицей справочника: чего в таблице нет и почему
INTAKE_NOTE = ("Настройки приёмки (входящая папка, период разбора, проверка моделью, запасная облачная модель, порог, пределы "
               "распаковки, описание изображений) в таблице не перечислены: они лежат в `inbox.json` каталога архива, их меняет "
               "команда `inbox set` и раздел Intake в DSH. Переменные окружения и `settings.json` их не задают.")


class SettingsError(messages.CodedError):
    """Отказ настроек: неизвестный ключ, негодное значение, чужие права на файл, битый файл."""


class FrozenList(list):
    """Список, который не меняется: настройки неизменяемы, а равенство с обычным списком сохраняется."""

    def _refuse(self, *args, **kwargs):
        raise TypeError("список настроек не меняется")

    __setitem__ = __delitem__ = __iadd__ = __imul__ = append = extend = insert = remove = pop = clear = sort = reverse = _refuse

    def __hash__(self):
        return hash(tuple(self))


def _s(key, type_, default, note, low=None, high=None, choices=None):
    return Spec(key, type_, FrozenList(default) if type_.startswith("list[") else default, low, high, choices, note)


PORT = (1, 65535)
SCHEMA = {spec.key: spec for spec in (
    # каталоги и файлы
    _s("home", "path", DEFAULT_HOME, "Каталог архива: берётся только из переменной окружения (в settings.json его быть не может)"),
    _s("llm_key_file", "path", "", "Файл с ключом локальной модели (сам ключ — только в переменной FLYARCHIVE_LLM_KEY); пусто — не задан"),
    _s("llm_cloud_key_file", "path", "", "Файл с ключом запасной облачной модели (сам ключ — только в переменной FLYARCHIVE_LLM_CLOUD_KEY); "
                                        "пусто — не задан, серверу уходит заглушка"),
    _s("pdf_font", "path", "", "Файл шрифта .ttf с кириллицей для pdf, которые собирает сервер документов; пусто — шрифт ищется среди обычных "
                               "(DejaVu Sans, Liberation Sans, Noto Sans, FreeSans в каталогах шрифтов системы)"),
    _s("env_file", "path", "", "Необязательный файл с переменными окружения (например, ключ облачной модели) вне репозитория; пусто — не задан"),
    _s("dsh_profile", "path", "~/.dsh/profiles/web", "Профиль оболочки DSH, в который ставится плагин"),
    _s("units_dir", "path", "~/.config/systemd/user", "Каталог пользовательских служб systemd, куда установщик кладёт службы"),
    _s("sandbox_dir", "path", "~/.local/share/flyarchive-sandbox", "Каталог песочницы для запуска внешней оболочки"),
    _s("bin_dir", "path", "~/.local/bin", "Каталог команд пользователя: установка кладёт в него ссылку на команду flyarchive; пусто — не класть"),
    # адреса и порты служб
    _s("search_port", "int", 8765, "Порт сервера поиска и страницы поиска", *PORT),
    _s("office_port", "int", 8766, "Порт сервера документов", *PORT),
    _s("mcp_port", "int", 8767, "Порт переходника MCP", *PORT),
    _s("mcp_url", "url", "http://127.0.0.1:8767/mcp", "Адрес переходника MCP, как его видят клиенты"),
    _s("gateway_port", "int", 8780, "Порт шлюза в частную сеть", *PORT),
    _s("gateway_bind", "str", "", "Адрес, на котором слушает шлюз; пусто — шлюз выключен"),
    _s("public_url", "url", "", "Внешний адрес шлюза для ссылок на документы; пусто — не задан"),
    _s("public_hosts", "list[str]", [], "Имена узла, под которыми шлюз принимает запросы снаружи (список; в переменной через запятую)"),
    _s("hosts", "list[str]", [], "Свои имена узла, кроме петли, которые принимает проверка заголовка Host (список; в переменной через запятую)"),
    _s("dsh_port", "int", 3080, "Порт веб-оболочки DSH", *PORT),
    _s("embed_url", "url", "http://127.0.0.1:11434/api/embed", "Адрес службы векторов"),
    _s("llm_local_url", "url", "http://127.0.0.1:8080",
       "Адрес локальной модели — любого сервера с интерфейсом OpenAI — без /v1 на конце: /v1 дописывается само. "
       "Перечень загруженных моделей (GET /running) — необязательная возможность сервера: без него модель считается готовой"),
    _s("llm_cloud_url", "url", "", "Адрес запасной облачной модели без /v1 на конце (/v1 дописывается само); пусто — не задан. "
                                   "Включается отдельно, в настройках приёмки (по умолчанию выключена)"),
    # оболочка DSH: модели и их поставщики — дело владельца, в открытой части их нет
    _s("dsh_patches", "list[path]", [], "Дополнительные файлы настройки оболочки DSH с моделями и поставщиками (список путей, каждый "
                                        "передаётся оболочке ещё одним --patch после основного; в переменной через запятую)"),
    _s("env_names", "list[str]", [], "Имена переменных, которые запускалка оболочки берёт из файла окружения env_file (список; "
                                     "прочее из файла в окружение не попадает; в переменной через запятую)"),
    _s("dsh_llm_key_env", "env_name", "", "Имя переменной окружения, под которым оболочка ждёт ключ локальной модели (ключ читается "
                                          "из файла llm_key_file); пусто — ключ оболочке не передаётся"),
    # модели
    _s("embed_model", "str", "bge-m3", "Имя модели векторов поиска; смена требует пересборки индекса"),
    _s("embed_dim", "int", 1024, "Размерность векторов: зашита в таблицу индекса и меняется только вместе с пересборкой индекса", 1, 8192),
    _s("embed_gpu", "bool", False, "Считать векторы на видеокарте (иначе на процессоре)"),
    _s("llm_local_model", "str", "", "Имя локальной модели для проверки и описания; пусто — не задано"),
    _s("llm_cloud_model", "str", "", "Имя запасной облачной модели; пусто — не задано. "
                                    "Включается отдельно, в настройках приёмки (по умолчанию выключена)"),
    # поиск и ответы серверов
    _s("search_half_life_days", "int", 540, "Полураспад веса свежести в поиске, дней", 1, 36500),
    _s("search_nprobes", "int", 400, "Сколько разделов индекса IVF просматривает поиск (больше — точнее и медленнее)", 1, 10_000),
    _s("search_refine", "int", 30, "Во сколько раз больше кандидатов поиск уточняет по исходным векторам", 1, 1000),
    _s("api_snippet_chars", "int", 420, "Знаков текста на одну находку в ответе для модели", 40, 5000),
    _s("api_max_k", "int", 20, "Сколько находок максимум за один вызов поиска", 1, 100),
    _s("api_budget_chars", "int", 6000, "Общий потолок текста в одном ответе поиска, знаков", 500, 100_000),
    _s("mcp_timeout_s", "int", 300, "Сколько секунд переходник MCP ждёт ответа служб", 1, 3600),
    # сроки входа
    _s("link_ttl_s", "int", 86400, "Срок жизни ссылки на документ, секунд", 60, 2_592_000),
    _s("login_ttl_s", "int", 300, "Срок жизни одноразового кода входа, секунд", 30, 3600),
    _s("session_ttl_s", "int", 43200, "Срок сеанса на странице поиска, секунд", 300, 2_592_000),
    # проверка моделью
    _s("llm_timeout_s", "int", 180, "Сколько секунд ждут ответа модели при проверке", 5, 3600),
    _s("llm_max_chars", "int", 30000, "Сколько знаков документа видит модель при проверке (начало и конец)", 1000, 1_000_000),
    _s("llm_max_pages", "int", 20, "Сколько страниц скана проверяется моделью на один документ", 1, 200),
    # сервер документов
    # верхняя граница размера документа: в base64 (4/3) с запасом на оболочку запроса он обязан пройти через самое узкое тело запроса —
    # шлюз берёт 24 МиБ, и 17 МиБ документа (около 22,7 МиБ в base64) в них умещаются, 18 МиБ уже нет;
    # тело запроса — защитный предел кода, не настройка
    _s("submit_max_mb", "int", 16, "Предел размера одного документа, переданного по MCP, МБ (сверху ограничен телом запроса шлюза)", 1, 17),
    _s("out_keep_hours", "int", 48, "Сколько часов хранятся файлы, созданные сервером документов", 1, 8760),
)}

_INT = re.compile(r"[+-]?[0-9]{1,18}")
_FLOAT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?")
_SWITCH = {"1": True, "true": True, "on": True, "yes": True, "0": False, "false": False, "off": False, "no": False}
_UNPARSED = object()                   # текст из окружения, который в нужный тип не разобрался: проверка такое значение не пропустит


def env_name(key):
    """Имя переменной окружения для настройки: FLYARCHIVE_ и ключ заглавными."""
    return ENV_PREFIX + key.upper()


def unused_env(env=None):
    """Переменные FLYARCHIVE_*, у которых нет настройки (опечатка в имени?), по алфавиту. Это не отказ: окружение общее."""
    env = os.environ if env is None else env
    known = {env_name(key) for key in SCHEMA} | set(ELSEWHERE)
    return sorted(name for name in env if isinstance(name, str) and name.startswith(ENV_PREFIX) and name not in known)


def _owner_uid():
    return os.geteuid() if hasattr(os, "geteuid") else None


def _control(text):
    """Управляющие знаки и разделители строк (в том числе нулевой байт и перевод строки)."""
    return any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in text)


class _Ctx:
    """Что нужно, чтобы раскрыть путь: домашний каталог пользователя (из переданного окружения) и каталог архива."""
    __slots__ = ("user", "home")

    def __init__(self, user, home):
        self.user, self.home = user.rstrip("/") if isinstance(user, str) else user, home

    def expand(self, path):
        """~ и ~имя раскрываются; {home} — только в начале пути; лишняя косая в конце снимается."""
        if path == "~":
            path = self.user or "/"
        elif path.startswith("~/"):
            path = self.user + path[1:]
        elif path.startswith("~"):
            path = os.path.expanduser(path)             # ~имя: чужой домашний каталог; нет такого пользователя — путь не изменится
        if self.home is not None and (path == "{home}" or path.startswith("{home}/")):
            path = self.home.rstrip("/") + path[len("{home}"):]
        return path.rstrip("/") or ("/" if path.startswith("/") else path)


# ── проверка значения по типу: годное значение (уже раскрытое) или отказ ─
def _within(spec, value):
    return spec.low <= value <= spec.high


def _int(spec, value, source, ctx):
    if isinstance(value, int) and not isinstance(value, bool) and _within(spec, value) and (not spec.choices or value in spec.choices):
        return value
    if spec.choices:
        raise SettingsError(messages.make("settings.bad_choice", key=spec.key, source=source, allowed=", ".join(map(str, spec.choices))))
    raise SettingsError(messages.make("settings.bad_int", key=spec.key, source=source, low=spec.low, high=spec.high))


def _float(spec, value, source, ctx):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
        except OverflowError:
            number = math.inf
        if math.isfinite(number) and _within(spec, number):
            return number
    raise SettingsError(messages.make("settings.bad_number", key=spec.key, source=source, low=spec.low, high=spec.high))


def _bool(spec, value, source, ctx):
    if isinstance(value, bool):
        return value
    raise SettingsError(messages.make("settings.bad_switch", key=spec.key, source=source))


def _str(spec, value, source, ctx):
    if isinstance(value, str) and not _control(value):
        return value
    raise SettingsError(messages.make("settings.bad_text", key=spec.key, source=source))


def _list(spec, value, source, ctx):
    if isinstance(value, list) and all(isinstance(item, str) and not _control(item) for item in value):
        return FrozenList(value)
    raise SettingsError(messages.make("settings.bad_list", key=spec.key, source=source))


def _list_path(spec, value, source, ctx):
    """Список путей: каждый после раскрытия ~ и {home} обязан быть абсолютным. Не список строк — отказ как у списка, плохой путь — как у пути."""
    if not (isinstance(value, list) and all(isinstance(item, str) and not _control(item) for item in value)):
        raise SettingsError(messages.make("settings.bad_list", key=spec.key, source=source))
    full = [ctx.expand(item) for item in value]
    if not all(path and os.path.isabs(path) and not _control(path) for path in full):
        raise SettingsError(messages.make("settings.bad_path", key=spec.key, source=source))
    return FrozenList(full)


_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")


def _env_name(spec, value, source, ctx):
    if isinstance(value, str) and ((value == "" and spec.default == "") or _ENV_NAME.fullmatch(value)):
        return value
    raise SettingsError(messages.make("settings.bad_env_name", key=spec.key, source=source))


def _path(spec, value, source, ctx):
    if isinstance(value, str) and not _control(value):
        if value == "" and (spec.default == "" or spec.key in EMPTY_PATH_OK):
            return value                                # необязательный путь: пусто — «не задан» (у bin_dir — «не класть»)
        full = ctx.expand(value)
        if full and os.path.isabs(full) and not _control(full):
            return full
    raise SettingsError(messages.make("settings.bad_path", key=spec.key, source=source))


def _url_ok(text):
    if not text or _control(text) or any(ch.isspace() for ch in text):
        return False
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and "@" not in parts.netloc and port != 0


def _url(spec, value, source, ctx):
    if isinstance(value, str) and ((value == "" and spec.default == "") or _url_ok(value)):
        return value
    raise SettingsError(messages.make("settings.bad_url", key=spec.key, source=source))


_CHECKS = {"int": _int, "float": _float, "bool": _bool, "str": _str, "list[str]": _list, "list[path]": _list_path, "path": _path, "url": _url,
           "env_name": _env_name}


def _from_env(spec, text):
    """Текст переменной окружения в значение нужного типа; не разобралось — _UNPARSED (проверка откажет)."""
    if not isinstance(text, str):
        return _UNPARSED
    kind = spec.type
    if kind == "int":
        text = text.strip()
        return int(text) if _INT.fullmatch(text) else _UNPARSED
    if kind == "float":
        text = text.strip()
        return float(text) if _FLOAT.fullmatch(text) else _UNPARSED
    if kind == "bool":
        return _SWITCH.get(text.strip().lower(), _UNPARSED)
    if kind.startswith("list["):
        return [item.strip() for item in text.split(",") if item.strip()]
    return text


# ── файл settings.json ──────────────────────────────────────────
def _shown(key):
    """Ключ из файла для сообщения: не длиннее 64 знаков, управляющие знаки заменены."""
    text = key if isinstance(key, str) else repr(key)
    return "".join("?" if unicodedata.category(ch) in ("Cc", "Zl", "Zp") else ch for ch in text[:64]) + ("…" if len(text) > 64 else "")


def _open_checked(path):
    """Файл настроек, открытый на чтение, или None, если его нет. Права смотрятся у самого открытого файла (по ссылке — у того,
    на что она ведёт), до первого чтения: файл чужого владельца или с записью для группы и остальных не читается."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))     # без O_NONBLOCK канал повисит
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        raise SettingsError(messages.make("settings.file_unreadable", path=path, error_type=type(e).__name__)) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SettingsError(messages.make("settings.file_unreadable", path=path, error_type="NotAFile"))
        if POSIX and (st.st_uid != _owner_uid() or st.st_mode & 0o022):
            raise SettingsError(messages.make("settings.file_open", path=path))
        if st.st_size > MAX_FILE_BYTES:
            raise SettingsError(messages.make("settings.file_unreadable", path=path, error_type="TooLarge"))
        handle, fd = os.fdopen(fd, "rb"), None           # дальше дескриптор принадлежит рукоятке
        return handle
    except OSError as e:
        raise SettingsError(messages.make("settings.file_unreadable", path=path, error_type=type(e).__name__)) from None
    finally:
        if fd is not None:
            os.close(fd)


def _read_file(path):
    """Объект JSON из файла настроек или None, если файла нет. Читается один раз; значения в отказы не попадают."""
    handle = _open_checked(path)
    if handle is None:
        return None
    try:
        with handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        raise SettingsError(messages.make("settings.file_unreadable", path=path, error_type=type(e).__name__)) from None
    if len(raw) > MAX_FILE_BYTES:
        raise SettingsError(messages.make("settings.file_unreadable", path=path, error_type="TooLarge"))
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise SettingsError(messages.make("settings.file_unreadable", path=path, error_type="UnicodeDecodeError")) from None
    except json.JSONDecodeError as e:
        raise SettingsError(messages.make("settings.bad_json", path=path, line=e.lineno, column=e.colno)) from None
    except (ValueError, RecursionError):                 # число в тысячи знаков, вложенность без конца
        raise SettingsError(messages.make("settings.bad_json", path=path, line=0, column=0)) from None
    if not isinstance(data, dict):
        raise SettingsError(messages.make("settings.not_object", path=path))
    return data


# ── объект настроек и load ──────────────────────────────────────
class Settings:
    """Загруженные настройки: значение по ключу и атрибуту, источник, словарь. Не меняются; списки строк тоже."""
    __slots__ = ("_values", "_sources")

    def __init__(self, values, sources):
        object.__setattr__(self, "_values", dict(values))
        object.__setattr__(self, "_sources", dict(sources))

    def __getitem__(self, key):
        return self._values[key]

    def __getattr__(self, name):
        if not name.startswith("_") and name in self._values:
            return self._values[name]
        raise AttributeError(f"нет настройки {name!r}")

    def __setattr__(self, name, value):
        raise AttributeError("настройки неизменяемы: загрузи заново")

    def __delattr__(self, name):
        raise AttributeError("настройки неизменяемы: загрузи заново")

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __contains__(self, key):
        return key in self._values

    def __repr__(self):
        return f"Settings(home={self._values['home']!r}, настроек: {len(self._values)})"

    def __copy__(self):                                  # неизменяемому копия не нужна: copy и deepcopy отдают его же
        return self

    def __deepcopy__(self, memo):
        return self

    def source(self, key):
        """Откуда значение: default, file, env (у home ещё arg — каталог передан аргументом load)."""
        return self._sources[key]

    def as_dict(self):
        return {key: list(value) if isinstance(value, list) else value for key, value in self._values.items()}


def _home(env, given, user):
    """Каталог архива и откуда он: аргумент, переменная окружения FLYARCHIVE_HOME, умолчание. Пустая переменная — не задана."""
    var = env_name("home")
    if given is not None:
        raw, source, label = (os.fspath(given) if isinstance(given, os.PathLike) else given), "arg", "argument"
    elif env.get(var):
        raw, source, label = env[var], "env", var
    else:
        raw, source, label = DEFAULT_HOME, "default", "default"
    return _path(SCHEMA["home"], raw, label, _Ctx(user, None)), source


def load(env=None, home=None):
    """Настройки из трёх слоёв. env — словарь окружения (по умолчанию os.environ), home — каталог архива, если его задаёт вызывающий.

    Файл читается один раз на вызов. Каталогов и файлов не создаётся, окружение не меняется. Отказ — SettingsError."""
    env = os.environ if env is None else env
    user = next((env[name] for name in ("HOME", "USERPROFILE") if env.get(name)), None)
    if not isinstance(user, str):
        user = os.path.expanduser("~")                   # в переданном окружении домашнего каталога нет: берём у процесса
    base, base_source = _home(env, home, user)
    ctx = _Ctx(user, base)
    path = os.path.join(base, FILE_NAME)
    saved = _read_file(path) or {}
    for key in saved:                                    # опечатка в ключе не проходит молча: сначала ключи, потом значения
        if key == "home":
            raise SettingsError(messages.make("settings.home_in_file", path=path))
        if key not in SCHEMA:
            raise SettingsError(messages.make("settings.unknown_key", key=_shown(key)))
    from_file = {key: _CHECKS[SCHEMA[key].type](SCHEMA[key], value, FILE_NAME, ctx) for key, value in saved.items()}
    values, sources = {"home": base}, {"home": base_source}
    for key, spec in SCHEMA.items():
        if key == "home":
            continue
        var = env_name(key)
        if var in env:
            values[key], sources[key] = _CHECKS[spec.type](spec, _from_env(spec, env[var]), var, ctx), "env"
        elif key in from_file:
            values[key], sources[key] = from_file[key], "file"
        else:
            values[key], sources[key] = _CHECKS[spec.type](spec, spec.default, "default", ctx), "default"
    return Settings(values, sources)


def startup(env=None, home=None):
    """load для точек входа и загрузки модулей: настройки или, при отказе, одна строка «настройки: <текст отказа>» в stderr и выход с кодом 2.

    Без трассировки. Значения в строке нет — как в самом отказе. Ошибки, которые не отказ настроек, не глотаются."""
    try:
        return load(env=env, home=home)
    except SettingsError as e:
        if (getattr(sys.stderr, "encoding", None) or "").lower().replace("-", "") != "utf8":
            try:
                sys.stderr.reconfigure(encoding="utf-8")        # владелец читает русский текст и при ascii-локали службы
            except (AttributeError, ValueError, OSError):
                pass
        print(f"настройки: {messages.of(e)}", file=sys.stderr)
        raise SystemExit(2) from None


# ── запуск как программы ────────────────────────────────────────
def _text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return json.dumps(list(value), ensure_ascii=False)
    return '""' if value == "" else str(value)


def report(settings, env=None):
    """Действующие значения и источник каждого: строка на настройку, ниже — переменные без настройки."""
    path = os.path.join(settings["home"], FILE_NAME)
    width = max(len(key) for key in SCHEMA)
    lines = ["Настройки", f"Каталог архива: {settings['home']}", f"Файл настроек: {path} ({'есть' if os.path.exists(path) else 'нет'})", "",
             f"{'ключ':<{width}}  {'источник':<8}  значение"]
    lines += [f"{key:<{width}}  {settings.source(key):<8}  {_text(settings[key])}" for key in SCHEMA]
    unused = unused_env(env)
    if unused:
        lines += ["", f"Переменные {ENV_PREFIX}* без настройки (не используются): " + ", ".join(unused)]
    return "\n".join(lines)


def _bounds(spec):
    if spec.choices:
        return "одно из: " + ", ".join(map(str, spec.choices))
    return f"от {spec.low} до {spec.high}" if spec.low is not None else "—"


def _default(spec):
    shown = _text(spec.default)
    return "(пусто)" if shown == '""' else f"`{shown}`"


def reference():
    """Справочник всех настроек таблицей Markdown: ключ, тип, умолчание, границы, описание, переменная окружения.

    Под таблицей — пояснение, где настройки приёмки."""
    rows = ["| Ключ | Тип | Умолчание | Границы | Описание | Переменная окружения |", "|---|---|---|---|---|---|"]
    for spec in SCHEMA.values():
        cells = (f"`{spec.key}`", spec.type, _default(spec), _bounds(spec), spec.note, f"`{env_name(spec.key)}`")
        rows.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(rows) + "\n\n" + INTAKE_NOTE


def main(argv=None, env=None):
    import argparse
    p = argparse.ArgumentParser(prog="settings", description="Настройки: действующие значения с источником и справочник.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help='то же объектом {ключ: {"value", "source"}}')
    mode.add_argument("--reference", action="store_true", help="справочник всех настроек таблицей Markdown (от окружения и файла не зависит)")
    mode.add_argument("--get", metavar="КЛЮЧ", help="действующее значение одной настройки; список — по строке на элемент (для запускалок на shell)")
    a = p.parse_args(argv)
    if a.reference:
        print(reference())
        return 0
    if a.get is not None and a.get not in SCHEMA:
        print(f"неизвестная настройка: {_shown(a.get)}", file=sys.stderr)
        return 2
    try:
        settings = load(env=env)
    except SettingsError as e:
        message = messages.of(e)
        if a.json:                                       # как у команды архива: последняя строка stderr — {"error": {"code", "args", "text"}}
            print(json.dumps({"error": message.to_json()}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"ошибка: {message}", file=sys.stderr)
        return 1
    if a.get is not None:
        value = settings[a.get]
        if isinstance(value, list):
            for item in value:
                print(item)
        else:
            print(("true" if value else "false") if isinstance(value, bool) else value)
        return 0
    if a.json:
        values = settings.as_dict()
        print(json.dumps({key: {"value": values[key], "source": settings.source(key)} for key in SCHEMA}, ensure_ascii=False, indent=1))
    else:
        print(report(settings, env))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    sys.exit(main())

"""Установка одной командой из шаблонов (FR-104): `flyarchive install [--dry-run] [--no-start] [--no-plugin] [--no-dsh] [--gateway auto|off] [--json]`.

Что делает установка, по шагам:
    1. Заводит архив тем же кодом, что `flyarchive init` (его даёт сама команда: каталоги, служебный токен, база сверки, таблица индекса).
    2. Пишет службы поиска, документов, переходника MCP, оболочки (и шлюза, если он настроен) и службу с таймером разбора входящих из шаблонов
       `tools/templates` по настройкам. Службы запускают код из каталога, где лежит этот файл (репозиторий); данные, журналы и секреты — в каталоге
       архива из настроек, который службе передаёт одна строка Environment в шаблоне. Адресов и имён узла в службах нет: шлюз читает настройки.
       Нет настройки приёмки (`inbox.json`) — она заводится с умолчаниями вместе с входящей папкой, как у `inbox set`; готовая не переписывается.
    3. Кладёт плагин в профиль оболочки: `<dsh_profile>/node_modules/flyarchive-dsh-plugin`, прежняя папка плагина заменяется целиком.
    4. Собирает файл настройки оболочки `<архив>/dsh.patch.yml`: подключение сервера MCP архива и плагина. Моделей и поставщиков в нём нет.
    5. Пишет запускалку команды `<архив>/bin/flyarchive` (маленький файл sh: `exec '<интерпретатор установки>' '<код>/flyarchive' "$@"`, права 0700) и кладёт
       ссылку на неё в каталог команд пользователя (настройка bin_dir, по умолчанию ~/.local/bin; пусто — не класть, FR-107): каталога нет —
       создаёт; ссылка уже ведёт на запускалку — «уже есть»; там файл или ссылка на другое — не трогает и говорит; каталога нет в PATH — называет строку. Запускалка нужна, потому что у файла команды первая
       строка `#!/usr/bin/env python3`, а библиотеки стоят в том Python (чаще — в окружении), которым шла установка; запускалкой же зовёт команду плагин оболочки
       (строка command файла настройки) и запускалка оболочки.
    6. Перечитывает службы и включает их по порядку зависимостей; таймер разбора — как `inbox set`.

`--dry-run` ничего не трогает: печатает каждый файл (с содержимым или отпечатком), ссылку на команду и каждую команду systemctl. `--no-start` пишет
файлы и не зовёт systemctl вовсе, `--no-plugin` не трогает профиль оболочки. Нет systemctl или каталога профиля — не сбой: шаг пропускается с понятной строкой.

Оболочка DSH необязательна (FR-107): когда команды dsh в PATH нет и по ключу `--no-dsh` служба оболочки и плагин не ставятся, остальные службы ставятся;
установка говорит, что такое оболочка, как её поставить и что повторить. Прежде поставленные файл службы оболочки и плагин не удаляются.
В конце установка одной строкой называет, чего обязательного не хватает (doctor.py).

Шлюз ставится, только когда заданы gateway_bind и public_url. `--gateway auto` спрашивает адрес и имя узла у частной сети (tailscale), проверяет, что адрес
из её диапазона, и дописывает в settings.json только недостающие ключи; `--gateway off` шлюз не ставит и не спрашивает.

Службу и таймер разбора пишет `inbox_units`: тот же код и те же шаблоны использует команда `inbox set` (tools/inbox.py).
"""
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
from collections import namedtuple

import messages
import settings

CODE = os.path.dirname(os.path.realpath(__file__))           # каталог tools репозитория: код служб идёт отсюда, а не из каталога архива
TEMPLATES = os.path.join(CODE, "templates")
PLUGIN_SOURCE = os.path.join(os.path.dirname(CODE), "dsh-plugin")
PLUGIN_NAME = "flyarchive-dsh-plugin"
PREFIX = "flyarchive-"                                        # с этой приставки начинаются все файлы служб, которые пишет установка; чужих она не трогает
INBOX_SERVICE, INBOX_TIMER = PREFIX + "inbox.service", PREFIX + "inbox.timer"
DSH_NAME = PREFIX + "dsh"
RELOAD = ("daemon-reload",)
TIMER_START = (("enable", "--now", INBOX_TIMER), ("restart", INBOX_TIMER))      # включить таймер и перезапустить: смена периода вступает сразу
SYSTEM_PATH = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
TAILNET = ipaddress.IPv4Network((100 << 24 | 64 << 16, 10))    # диапазон адресов частной сети (100.64/10, RFC 6598); на любой другой шлюз не ставится
NODE_NAME = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")
UNSAFE = re.compile(r"[\s%\"'\\$]")                           # знаки, которые файл службы systemd разбирает по-своему
SLOT = re.compile(r"\{([a-z_]+)\}")

# служба на Python: имя (файл службы и журнала), сценарий, описание с портом из настроек
PYTHON_SERVICES = (
    ("search", "webui.py", "Поиск по архиву (порт {search_port})"),
    ("office", "office_server.py", "Документы и графика (порт {office_port})"),
    ("mcp", "mcp_server.py", "Переходник MCP (порт {mcp_port})"),
)
GATEWAY_SERVICE = ("gateway", "gateway.py", "Шлюз архива в tailnet (порт {gateway_port}): снаружи только MCP и подписанные ссылки")
DSH_DESCRIPTION = "DeepSeek Harness с инструментами архива (порт {dsh_port})"
COMMAND = "flyarchive"                                         # имя ссылки на команду в каталоге команд пользователя и имя запускалки
LAUNCHER_DIR = "bin"                                           # каталог запускалки внутри каталога архива
DSH_ABOUT = ("DSH (DeepSeek Harness) — веб-оболочка для разговора с моделью, в ней появляется раздел «Архив». Архив работает и без неё: команда "
             "flyarchive, страница поиска, подключение сторонних оболочек (flyarchive connect). ")
DSH_HOW = ("Поставить: npm install -g @deepseek-ai/dsh (пакет под лицензией MIT, исходники: github.com/deepseek-ai/deepseek-harness); "
           "потом повторить: flyarchive install")
GATEWAY_HINT = ("Шлюз не ставится: не заданы настройки gateway_bind (адрес этой машины в частной сети) и public_url (внешний адрес шлюза). "
                "Задай их в settings.json или поставь шлюз так: flyarchive install --gateway auto (нужен tailscale).")

File = namedtuple("File", "path mode data show")               # data — байты; show — печатать ли содержимое (тексты) или только отпечаток (плагин)
Config = namedtuple("Config", "home units_dir dsh_profile dsh_port search_port office_port mcp_port mcp_url gateway_port gateway_bind public_url bin_dir")


class InstallError(messages.CodedError):
    """Установка остановлена до записи: путь, который нельзя записать в службу, шаблон с неизвестной подстановкой."""


def fail(text):
    return InstallError(messages.make("generic.text", text=text))


# ── настройки ───────────────────────────────────────────────────
def read_settings(env=None):
    """Настройки, которые нужны установке, одним набором: больше установка настроек не читает."""
    s = settings.load(env=env)
    # адрес переходника для клиентов: настройка mcp_url; пока её никто не задал, он выводится из порта переходника (как в connect.py)
    local = s["mcp_url"] if s.source("mcp_url") != "default" else f"http://127.0.0.1:{s['mcp_port']}/mcp"
    return Config(home=s["home"], units_dir=s["units_dir"], dsh_profile=s["dsh_profile"], dsh_port=s["dsh_port"], search_port=s["search_port"],
                  office_port=s["office_port"], mcp_port=s["mcp_port"], mcp_url=local, gateway_port=s["gateway_port"],
                  gateway_bind=s["gateway_bind"], public_url=s["public_url"], bin_dir=s["bin_dir"])


# ── шаблоны ─────────────────────────────────────────────────────
def render(name, values):
    """Шаблон из tools/templates с подставленными значениями. Подстановка — {имя} строчными буквами; неизвестная — ошибка, а не пустое место."""
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        text = f.read()

    def put(found):
        if found.group(1) not in values:
            raise fail(f"в шаблоне {name} неизвестная подстановка {{{found.group(1)}}}")
        return str(values[found.group(1)])

    return SLOT.sub(put, text)


def check_paths(*paths):
    """В файл службы идут только пути без пробела и знаков, которые systemd разбирает по-своему: иначе служба не стартует."""
    for path in paths:
        if UNSAFE.search(path):
            raise fail(f"путь «{path}» нельзя записать в файл службы systemd: в нём пробел или знак % \" ' \\ $. Выбери каталог без них")


def service_path():
    """(PATH службы оболочки, замечание или None). В PATH — каталог, где при установке нашёлся dsh, привычный каталог npm пользователя
    (%h — домашний каталог, его раскрывает сам systemd) и системные каталоги. Служба не читает .bashrc, и без этого команда dsh не находится.
    Каталог с пробелом или знаком, который systemd разбирает по-своему, в PATH не идёт: строка окружения оборвалась бы на нём, и служба
    осталась бы без системных каталогов. Под WSL так выглядят каталоги Windows, попавшие в PATH."""
    found = shutil.which("dsh")
    folder = os.path.dirname(os.path.abspath(found)) if found else ""
    note = None
    if folder and UNSAFE.search(folder):
        note = (f"Команда dsh найдена в каталоге «{folder}», а его нельзя записать в файл службы systemd (пробел или знак % \" ' \\ $): "
                "служба оболочки будет искать dsh в ~/.npm-global/bin и системных каталогах. Поставь dsh туда или дай оттуда ссылку на него.")
        folder = ""
    parts = ([folder] if folder else []) + ["%h/.npm-global/bin", *SYSTEM_PATH]
    return ":".join(dict.fromkeys(parts)), note


def inbox_units(home, period, code=None, python=None):
    """Тексты службы и таймера systemd для разбора по расписанию: {имя файла: текст}. Ту же пару пишет `inbox set`."""
    values = {"code": code or CODE, "home": home, "python": python or sys.executable, "period": period}
    check_paths(values["code"], home, values["python"])
    return {INBOX_SERVICE: render("inbox.service", values), INBOX_TIMER: render("inbox.timer", values)}


def service_units(cfg, python, gateway, period, path, dsh=True):
    """Все файлы служб и таймера: [(имя файла, текст)] в порядке запуска; шлюз — только если gateway, службу оболочки — только если dsh.
    path — PATH службы оболочки."""
    values = {"code": CODE, "home": cfg.home, "python": python, "path": path}
    check_paths(CODE, cfg.home, python)
    ports = cfg._asdict()
    out = []
    for name, script, description in PYTHON_SERVICES + ((GATEWAY_SERVICE,) if gateway else ()):
        out.append((f"{PREFIX}{name}.service", render("python.service", {**values, "name": name, "script": script,
                                                                         "description": description.format_map(ports)})))
    if dsh:
        out.append((DSH_NAME + ".service", render("dsh.service", {**values, "description": DSH_DESCRIPTION.format_map(ports)})))
    out += list(inbox_units(cfg.home, period, python=python).items())
    return out


def launcher_path(home):
    """Где лежит запускалка команды: в каталоге архива, а не в каталоге кода (это не код, а порождённая установкой настройка)."""
    return os.path.join(home, LAUNCHER_DIR, COMMAND)


def launcher_text(python, code):
    """Запускалка команды: sh, который запускает файл команды интерпретатором установки. Оба пути идут в одинарные кавычки sh, поэтому пробел,
    кавычка, доллар и обратная черта в них недопустимы (check_paths) — иначе путь разобрался бы оболочкой по-своему."""
    check_paths(python, code)
    return ("#!/bin/sh\n"
            "# Запускалка команды flyarchive: её пишет `flyarchive install`, руками не правится (повторная установка перепишет).\n"
            "# Команда идёт тем Python, которым её установили: библиотеки стоят в нём, а в пути поиска оболочки или службы их может не быть.\n"
            f"exec '{python}' '{os.path.join(code, COMMAND)}' \"$@\"\n")


def patch_text(cfg):
    """Файл настройки оболочки: подключение сервера MCP архива и плагина. Адрес — из настроек, в одинарных кавычках YAML; плагин зовёт запускалку."""
    return render("dsh.patch.yml", {"launcher": launcher_path(cfg.home).replace("'", "''"), "mcp_url": cfg.mcp_url.replace("'", "''")})


# ── запись ──────────────────────────────────────────────────────
def write_file(path, data, mode):
    """Файл целиком: во временный файл рядом и переименование; права — ровно mode (umask их не урезает)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_units(folder, texts):
    """Пишет файлы служб в каталог служб: {имя: текст}. Единственное место, где записываются службы и таймер (установка и `inbox set`)."""
    os.makedirs(folder, exist_ok=True)
    for name, text in texts.items():
        write_file(os.path.join(folder, name), text.encode("utf-8"), 0o644)


# ── шлюз ────────────────────────────────────────────────────────
def ask_tailnet(run=subprocess.run):
    """(адрес, имя узла) у частной сети машины или None: `tailscale ip -4` и `tailscale status --json`. Адрес обязан быть из диапазона частной сети,
    имя — похоже на имя узла: иначе шлюз остался бы открытым всем соседям или получил бы чужое имя."""
    try:
        got = run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=15)
        lines = (got.stdout or "").strip().splitlines()
        address = ipaddress.ip_address(lines[0].strip()) if got.returncode == 0 and lines else None
        if not isinstance(address, ipaddress.IPv4Address) or address not in TAILNET:
            return None
        status = run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=15)
        name = json.loads(status.stdout)["Self"]["DNSName"].rstrip(".") if status.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return (str(address), name) if isinstance(name, str) and NODE_NAME.fullmatch(name) else None


def decide_gateway(cfg, mode, ask=ask_tailnet):
    """(ставить ли шлюз, ключи для дописывания в settings.json, строки для человека). Заданные настройки не перезаписываются."""
    if mode == "off":
        return False, {}, ["Шлюз не ставится (--gateway off)."]
    add, notes = {}, []
    if mode == "auto" and not (cfg.gateway_bind and cfg.public_url):
        found = ask()
        if found is None:
            notes.append("Шлюз не ставится: у частной сети нет подходящего адреса и имени узла (tailscale ip -4, tailscale status). "
                         "Задай gateway_bind и public_url в settings.json сам.")
        else:
            address, name = found
            if not cfg.gateway_bind:
                add["gateway_bind"] = address
            if not cfg.public_url:
                add["public_url"] = f"http://{name}:{cfg.gateway_port}"
            notes.append("Шлюз: адрес и имя узла взяты у частной сети; в settings.json допишутся только недостающие ключи: " + ", ".join(add) + ".")
    ready = bool((cfg.gateway_bind or "gateway_bind" in add) and (cfg.public_url or "public_url" in add))
    if not ready and not notes:
        notes.append(GATEWAY_HINT)
    return ready, add, notes


def settings_with(home, add):
    """Текст settings.json с дописанными ключами: прочие ключи и их порядок сохраняются, существующие значения не трогаются (пустое — заполняется)."""
    path = os.path.join(home, settings.FILE_NAME)
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    for key, value in add.items():
        if not data.get(key):
            data[key] = value
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# ── плагин ──────────────────────────────────────────────────────
def plugin_files():
    """[(путь внутри плагина, байты)] — package.json и всё из lib; тестов и стенда в профиль не везут. None, если исходника плагина нет."""
    package = os.path.join(PLUGIN_SOURCE, "package.json")
    if not os.path.isfile(package):
        return None
    found = [("package.json", package)]
    for folder, dirs, names in os.walk(os.path.join(PLUGIN_SOURCE, "lib")):
        dirs.sort()
        found += [(os.path.relpath(os.path.join(folder, n), PLUGIN_SOURCE).replace(os.sep, "/"), os.path.join(folder, n)) for n in sorted(names)]
    out = []
    for rel, path in found:
        with open(path, "rb") as f:
            out.append((rel, f.read()))
    return out


def replace_plugin(target, files):
    """Прежняя папка плагина (или ссылка на неё) убирается целиком, новая пишется заново; соседние каталоги профиля остаются."""
    if os.path.islink(target) or os.path.isfile(target):
        os.unlink(target)
    elif os.path.isdir(target):
        shutil.rmtree(target)
    for rel, data in files:
        write_file(os.path.join(target, *rel.split("/")), data, 0o644)


# ── ссылка на команду ───────────────────────────────────────────
def command_state(folder, target, legacy=None):
    """Состояние ссылки на команду: disabled (каталог не задан), free (на месте ничего нет), exists (ссылка уже ведёт на запускалку target),
    legacy (ссылка ведёт на файл команды legacy из каталога кода: остаток сборки до версии 0.1, она заменяется),
    foreign (там файл, каталог или ссылка на другое, в том числе висячая)."""
    if not folder:
        return "disabled"
    link = os.path.join(folder, COMMAND)
    if not os.path.lexists(link):
        return "free"
    if os.path.islink(link):
        real = os.path.realpath(link)
        if real == os.path.realpath(target):
            return "exists"
        if legacy and real == os.path.realpath(legacy):
            return "legacy"
    return "foreign"


def on_search_path(folder, path_env):
    """Есть ли каталог в строке PATH (пустые и относительные записи не в счёт; сравниваются настоящие пути)."""
    want = os.path.realpath(folder)
    return any(entry and os.path.isabs(entry) and os.path.realpath(entry) == want for entry in path_env.split(os.pathsep))


# ── команда ─────────────────────────────────────────────────────
def entry(file, content=False):
    """Запись о файле для `--json`: путь, права, отпечаток; в пробном прогоне у текстов — ещё и содержимое."""
    out = {"path": file.path, "mode": format(file.mode, "04o"), "sha256": hashlib.sha256(file.data).hexdigest()}
    if content and file.show:
        out["content"] = file.data.decode("utf-8")
    return out


class Install:
    """Один запуск установки. Всё, что установка сделала бы или сделала, копится в self.report (его отдаёт `--json`), а в текстовом виде
    печатается по ходу дела. В пробном прогоне (dry) на диск и в службы не идёт ничего."""

    def __init__(self, a, init, env, say):
        self.a, self.init, self.say, self.env = a, init, say, env
        self.cfg = read_settings(env)
        self.python = sys.executable
        check_paths(CODE, self.cfg.home, self.python)               # до любой записи: путь, который службе не передать, останавливает установку
        self.dry, self.loud = a.dry_run, not a.json
        self.todo, self.units, self.gateway = [], [], False
        # оболочка ставится, только когда она есть в PATH и её не отключили ключом: иначе служба оболочки падала бы с кодом 127 каждые десять секунд
        self.dsh = shutil.which("dsh") is not None and not getattr(a, "no_dsh", False)
        self.report = {"dry_run": self.dry, "home": self.cfg.home, "code": CODE, "python": self.python, "init": None, "files": [], "removes": [],
                       "commands": [], "skipped": [], "notes": [], "failed": None, "todo": [], "launcher": None, "command": None}

    # вывод
    def out(self, text=""):
        if self.loud:
            self.say(text)

    def skip(self, step, text):
        self.report["skipped"].append({"step": step, "text": text})
        self.out(f"  {text}")

    def put(self, files):
        """Файлы шага: в пробном прогоне только показываются (тексты — вместе с содержимым), иначе пишутся."""
        for file in files:
            self.report["files"].append(entry(file, self.dry))
            if self.dry:
                self.out(f"  записал бы {file.path} (права {format(file.mode, '04o')}, sha256 {hashlib.sha256(file.data).hexdigest()[:12]}…)")
                for line in file.data.decode("utf-8").splitlines() if file.show else ():
                    self.out(f"      | {line}")
            else:
                write_file(file.path, file.data, file.mode)
                self.out(f"  записан {file.path}")

    # шаги
    def archive(self):
        self.out("1. Архив")
        if self.dry:
            self.skip("init", "архив не заводится (пробный прогон): init создал бы каталоги, служебный токен, базу сверки и таблицу индекса")
            return
        steps, self.todo = self.init((lambda message: self.out(f"  {message}")) if self.loud else None)
        self.report["init"] = {"home": self.cfg.home, "steps": [{**s, "message": s["message"].to_json()} for s in steps],
                               "todo": [{"id": i, "message": m.to_json()} for i, m in self.todo]}

    def services(self):
        import inbox
        cfg = self.cfg
        self.out(f"2. Службы и таймер -> {cfg.units_dir}")
        self.gateway, add, notes = decide_gateway(cfg, self.a.gateway)
        self.report["notes"] += notes
        for note in notes:
            self.out(f"  {note}")
        if add:
            self.put([File(os.path.join(cfg.home, settings.FILE_NAME), 0o600, settings_with(cfg.home, add).encode("utf-8"), True)])
        config = inbox.load_config(cfg.home)                           # период таймера — из готовой настройки приёмки, иначе по умолчанию
        if not os.path.exists(os.path.join(cfg.home, "inbox.json")):
            if self.dry:
                self.put([File(os.path.join(cfg.home, "inbox.json"), 0o600, json.dumps(config, ensure_ascii=False, indent=1).encode("utf-8"), True)])
            else:
                inbox.save_config(cfg.home, config)                    # входящая папка заводится там же, где у `inbox set`
                self.put_done(os.path.join(cfg.home, "inbox.json"), 0o600)
        path, note = service_path()
        if note:
            self.report["notes"].append(note)
            self.out(f"  {note}")
        self.units = service_units(cfg, self.python, self.gateway, config["period"], path, dsh=self.dsh)
        self.put([File(os.path.join(cfg.units_dir, name), 0o644, text.encode("utf-8"), True) for name, text in self.units])
        if not self.dsh:
            if getattr(self.a, "no_dsh", False):
                self.skip("dsh", "служба оболочки не ставится (--no-dsh): остальные службы ставятся. " + DSH_HOW.replace("Поставить", "Когда понадобится, поставить"))
            else:
                self.skip("dsh", "команды dsh в PATH нет, служба оболочки не ставится: остальные службы ставятся. " + DSH_ABOUT + DSH_HOW)
        self.out(f"  Входящая папка: {config['inbox']}")

    def put_done(self, path, mode):
        """Файл, который записал не установка, а код, которым она пользуется (настройка приёмки): в отчёт и на экран."""
        with open(path, "rb") as f:
            self.report["files"].append(entry(File(path, mode, f.read(), True)))
        self.out(f"  записан {path}")

    def plugin(self):
        cfg = self.cfg
        self.out("3. Плагин оболочки")
        target = os.path.join(cfg.dsh_profile, "node_modules", PLUGIN_NAME)
        sources = plugin_files()
        if self.a.no_plugin:
            self.skip("plugin", "плагин не ставится (--no-plugin): профиль оболочки не тронут")
        elif not self.dsh:
            self.skip("plugin", "плагин не ставится: оболочки DSH нет или она отключена; прежде поставленный плагин и профиль оболочки не тронуты")
        elif sources is None:
            self.skip("plugin", f"плагин не поставлен: нет исходника {PLUGIN_SOURCE}")
        elif not os.path.isdir(cfg.dsh_profile):
            self.skip("plugin", f"плагин не поставлен: нет каталога профиля оболочки {cfg.dsh_profile} (профиль появляется после первого запуска "
                                "оболочки; потом повтори установку: flyarchive install)")
        else:
            if os.path.lexists(target):
                self.report["removes"].append(target)
                self.out(f"  прежняя папка плагина {target} заменяется целиком")
            files = [File(os.path.join(target, *rel.split("/")), 0o644, data, False) for rel, data in sources]
            self.report["files"] += [entry(f) for f in files]
            if self.dry:
                self.out(f"  записал бы плагин в {target}: {len(files)} файлов (package.json и lib)")
            else:
                replace_plugin(target, sources)
                self.out(f"  плагин «Архив» поставлен в {target}; чтобы оболочка его увидела, нужен перезапуск: systemctl --user restart {DSH_NAME}")

    def patch(self):
        self.out("4. Файл настройки оболочки")
        self.put([File(os.path.join(self.cfg.home, "dsh.patch.yml"), 0o600, patch_text(self.cfg).encode("utf-8"), True)])

    def launcher(self):
        """Запускалка команды в каталоге архива: пишется всегда, потому что на неё ведёт и ссылка, и строка command файла настройки оболочки."""
        path = launcher_path(self.cfg.home)
        if not self.dry:
            folder = os.path.dirname(path)
            os.makedirs(folder, mode=0o700, exist_ok=True)
            os.chmod(folder, 0o700)
        self.put([File(path, 0o700, launcher_text(self.python, CODE).encode("utf-8"), True)])
        self.report["launcher"] = path
        return path

    def command(self):
        """Запускалка команды и ссылка на неё в каталоге команд пользователя: оболочка найдёт команду по имени. Чужой файл с тем же именем не трогается."""
        self.out("5. Команда flyarchive в пути поиска")
        folder, target = self.cfg.bin_dir, self.launcher()
        link = os.path.join(folder, COMMAND) if folder else None
        legacy = os.path.join(CODE, COMMAND)
        state = command_state(folder, target, legacy)
        found = {"folder": folder or None, "link": link, "target": target, "state": state, "on_path": None}
        if state == "disabled":
            self.out("  ссылка на команду не кладётся: настройка bin_dir пуста")
        elif state == "exists":
            self.out(f"  ссылка на команду уже есть: {link}")
        elif state == "foreign":
            self.out(f"  по пути {link} уже лежит другой файл или ссылка на другое: не трогаю. Освободи имя или положи ссылку сам: ln -s {target} {link}")
        elif self.dry:
            if state == "legacy":
                self.out(f"  заменил бы ссылку {link}: она ведёт на файл команды {os.readlink(link)}, а должна вести на запускалку {target}")
            else:
                self.out(f"  создал бы {'каталог ' + folder + ' и ' if not os.path.isdir(folder) else ''}ссылку {link} -> {target}")
        else:
            try:
                os.makedirs(folder, exist_ok=True)
                if state == "legacy":
                    before, spare = os.readlink(link), link + ".tmp"
                    if os.path.lexists(spare):
                        os.unlink(spare)
                    os.symlink(target, spare)
                    os.replace(spare, link)                            # ссылка не пропадает ни на миг: команду в этот момент могут звать
                    found["state"] = "replaced"
                    self.out(f"  заменена ссылка {link}: вела на файл команды {before}, теперь -> запускалка {target}")
                else:
                    os.symlink(target, link)
                    found["state"] = "created"
                    self.out(f"  создана ссылка {link} -> {target}")
            except OSError as e:
                found["state"] = "failed"
                note = f"ссылка на команду не положена ({type(e).__name__}): {link}. Положи её сам: ln -s {target} {link}"
                self.report["notes"].append(note)
                self.out(f"  {note}")
        if state in ("free", "exists", "legacy"):
            found["on_path"] = on_search_path(folder, (self.env if self.env is not None else os.environ).get("PATH", ""))
            if not found["on_path"]:
                note = (f"каталог {folder} не в пути поиска команд (PATH): добавь в профиль оболочки (~/.profile или ~/.bashrc) строку "
                        f'export PATH="{folder}:$PATH"')
                self.report["notes"].append(note)
                self.out(f"  {note}")
        self.report["command"] = found

    def start(self):
        self.out("6. Запуск служб")
        names = [name for name, _ in self.units if name not in (INBOX_SERVICE, INBOX_TIMER)]
        commands = [["systemctl", "--user", *RELOAD]] + [["systemctl", "--user", "enable", "--now", name] for name in names] + \
            [["systemctl", "--user", *args] for args in TIMER_START]
        if self.a.no_start:
            self.skip("start", "службы не запущены (--no-start). Чтобы запустить, выполни по порядку: " + "; ".join(" ".join(c) for c in commands))
            return
        if shutil.which("systemctl") is None:
            self.skip("start", "systemctl не найден: службы записаны, но не запущены. Когда systemd появится, повтори установку: flyarchive install")
            return
        for command in commands:
            self.report["commands"].append(command)
            self.out(f"  {' '.join(command)}")
            if self.dry:
                continue
            why = ""
            try:
                got = subprocess.run(command, capture_output=True, text=True, timeout=60)
                if got.returncode != 0:
                    why = (got.stderr or "").strip()[:200] or f"код возврата {got.returncode}"
            except (OSError, subprocess.SubprocessError) as e:
                why = type(e).__name__
            if why:
                self.report["failed"] = {"command": " ".join(command[2:]), "why": why}
                print(f"ошибка: systemctl {' '.join(command[2:])}: {why}. Файлы записаны, службы запущены не полностью: повтори установку "
                      "после исправления", file=sys.stderr)
                return

    def finish(self):
        if not self.dry:
            inbox_ready = os.path.exists(os.path.join(self.cfg.home, "inbox.json"))      # входящую папку заводит сама установка: напоминать о ней нечего
            left = [(i, m) for i, m in self.todo if not (i == "inbox" and inbox_ready)]
            self.report["todo"] = [{"id": i, "message": m.to_json()} for i, m in left]
            if left:
                self.out("Осталось сделать самому:")
                for _, m in left:
                    self.out(f"  {m}")
            if self.loud:                           # чего обязательного не хватает: из той же проверки, что `flyarchive doctor`
                import doctor
                self.out(str(doctor.tail(doctor.collect(settings.load(env=self.env), notify=doctor.say_waiting))))
        if self.a.json:
            self.say(json.dumps(self.report, ensure_ascii=False))

    def go(self):
        self.out("Установка FlyArchive" + (" (пробный прогон: ничего не записывается и не запускается)" if self.dry else ""))
        self.out(f"Каталог архива: {self.cfg.home}")
        self.out(f"Код служб: {CODE}; интерпретатор: {self.python}")
        self.archive()
        self.services()
        self.plugin()
        self.patch()
        self.command()
        self.start()
        self.finish()
        return 1 if self.report["failed"] else 0


def run(a, init=None, env=None, say=print):
    """Установка по ключам a (dry_run, no_start, no_plugin, gateway, json). init(report) — заведение архива (его даёт flyarchive): возвращает
    (шаги, что осталось владельцу), report(сообщение) зовётся на каждом шаге. Возвращает код возврата: 0, а при отказе systemctl — 1."""
    return Install(a, init, env, say).go()

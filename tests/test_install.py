"""Установка одной командой из шаблонов: `flyarchive install` (FR-104).

Команда настоящая (процесс, файл tools/flyarchive), машина — выдуманная и пустая: свой домашний каталог, каталог архива вне него, подставные
`systemctl`, `tailscale` и `dsh` в PATH (настоящие стоят позже в PATH или не видны вовсе). Живой архив, настоящие службы, `~/.config/systemd/user`
и `~/.dsh` тесты не трогают. Адреса частной сети и имена узлов — выдуманные образцы, собранные из кусков.

Что держат тесты. Установка заводит архив тем же `init`, пишет службы и таймер из шаблонов по настройкам (код — из репозитория, данные и журналы —
в каталоге архива из настроек), кладёт плагин в профиль оболочки, собирает файл настройки оболочки и запускает службы по порядку зависимостей.
`--dry-run` ничего не трогает и показывает каждый файл и каждую команду systemctl; `--no-start` не зовёт systemctl вовсе; `--no-plugin` не трогает
профиль. Шлюз ставится, только когда заданы gateway_bind и public_url, `--gateway auto` берёт их у частной сети и дописывает недостающие ключи.
Служебный токен и ключ модели нигде не пишутся и не печатаются. Повторная установка даёт те же файлы байт в байт, чужие файлы служб и каталоги
профиля не трогаются. Службу и таймер разбора пишет тот же код и те же шаблоны, что `inbox set`.
"""
import hashlib
import json
import os
import re
import site
import stat
import subprocess
import sys

import pytest

import inbox
import install
import publication_gate as G
import settings as S
from archivekit import DEAD_VECTORS

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")
FLYARCHIVE = os.path.join(TOOLS, "flyarchive")
TEMPLATES = os.path.join(TOOLS, "templates")
PLUGIN = os.path.join(ROOT, "dsh-plugin")

TAILNET_IP = "100.64." + "0.5"                       # выдуманный адрес частной сети; в одну строку его не пишем: ворота публикации читают и этот файл
NODE = "node.tail." + "ts.net"
OTHER_IP = "100.64." + "0.9"
BASE_UNITS = ["flyarchive-dsh.service", "flyarchive-inbox.service", "flyarchive-inbox.timer", "flyarchive-mcp.service",
              "flyarchive-office.service", "flyarchive-search.service"]
START_ORDER = ["flyarchive-search.service", "flyarchive-office.service", "flyarchive-mcp.service", "flyarchive-dsh.service"]
TIMER_COMMANDS = ["enable --now flyarchive-inbox.timer", "restart flyarchive-inbox.timer"]

try:
    import lancedb
    HAVE_INDEX = hasattr(lancedb, "__version__")
except ImportError:
    HAVE_INDEX = False
needs_index = pytest.mark.skipif(not HAVE_INDEX, reason="нет lancedb: init заводит настоящую таблицу индекса")


# ── машина ──────────────────────────────────────────────────────
class Result:
    def __init__(self, process):
        self.code, self.out, self.err = process.returncode, process.stdout, process.stderr

    def json(self):
        return json.loads(self.out)


class Machine:
    """Пустая машина: домашний каталог пользователя, каталог архива вне его, профиль оболочки и подставные команды в PATH."""

    def __init__(self, base, tailscale=None, systemctl="exit 0", dsh=True, profile=True, plain_path=False, settings_file=None):
        self.base = base
        self.user = base / "user"
        self.home = base / "архив"                    # вне домашнего каталога: подстановку каталога архива не спутать с домашним путём
        self.bin = base / "bin"
        self.units = self.user / ".config" / "systemd" / "user"
        self.profile = self.user / ".dsh" / "profiles" / "web"
        self.user.mkdir(parents=True)
        self.bin.mkdir()
        if systemctl is not None:
            self.stub("systemctl", 'echo "$@" >> "$HOME/systemctl.log"\n' + systemctl)
        if tailscale is not None:
            self.stub("tailscale", 'echo "$@" >> "$HOME/tailscale.log"\n' + tailscale)
        if dsh:
            self.stub("dsh", "exit 0")
        if profile:
            self.profile.mkdir(parents=True)
        self.plain_path = plain_path
        if settings_file is not None:
            self.put_settings(settings_file)

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
        path.chmod(0o755)

    def put_settings(self, data):
        self.home.mkdir(mode=0o700, exist_ok=True)
        path = self.home / "settings.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def env(self, **extra):
        path = str(self.bin) if self.plain_path else f"{self.bin}:/usr/bin:/bin"
        env = {"HOME": str(self.user), "FLYARCHIVE_HOME": str(self.home), "PATH": path, "PYTHONIOENCODING": "utf-8", "LANG": "C.UTF-8",
               # домашний каталог в тесте подменён, а библиотеки стоят в домашнем каталоге настоящего пользователя
               "PYTHONPATH": os.pathsep.join(p for p in (site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p),
               # установка в конце спрашивает службу векторов: адрес — несуществующий, до живой службы владельца она не дойдёт
               "FLYARCHIVE_EMBED_URL": DEAD_VECTORS}
        env.update(extra)
        return env

    def run(self, *args, **extra):
        return Result(subprocess.run([sys.executable, FLYARCHIVE, "install", *args], env=self.env(**extra), capture_output=True, text=True,
                                     encoding="utf-8", cwd=str(self.base), timeout=300))

    def log(self, name="systemctl.log"):
        path = self.user / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def unit(self, name):
        return (self.units / name).read_text(encoding="utf-8")


def unit_lines(text):
    """Строки файла службы по разделам: {раздел: [строки]}."""
    sections, current = {}, None
    for line in text.splitlines():
        if re.fullmatch(r"\[\w+\]", line):
            current = sections.setdefault(line, [])
        elif line.strip() and current is not None:
            current.append(line)
    return sections


def tree(root):
    """Снимок каталога: путь → (размер, время изменения, отпечаток содержимого) у файла, None у каталога. Любая запись видна."""
    out = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs:
            out[os.path.relpath(os.path.join(folder, name), root)] = None
        for name in files:
            path = os.path.join(folder, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = (os.stat(path).st_size, os.stat(path).st_mtime_ns, hashlib.sha256(f.read()).hexdigest())
    return out


def hashes(root):
    return {k: v and v[2] for k, v in tree(root).items()}


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sources_of(rel_root):
    """Файлы плагина из репозитория, которые должны попасть в профиль: package.json и lib/. {путь внутри плагина: байты}."""
    found = {"package.json": open(os.path.join(PLUGIN, "package.json"), "rb").read()}
    for folder, _, files in os.walk(os.path.join(PLUGIN, "lib")):
        for name in files:
            path = os.path.join(folder, name)
            found[os.path.relpath(path, PLUGIN).replace(os.sep, "/")] = open(path, "rb").read()
    return found


def says(ip, status):
    """Тело подставного tailscale: на `ip -4` печатает ip, на `status --json` — status."""
    return f'case "$1" in ip) echo "{ip}" ;; status) echo \'{status}\' ;; esac'


def selfname(name):
    return json.dumps({"Self": {"DNSName": name}}, ensure_ascii=False)


GOOD_TAILSCALE = says(TAILNET_IP, selfname(NODE + "."))


@pytest.fixture
def machine(tmp_path):
    return Machine(tmp_path, tailscale=GOOD_TAILSCALE)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """Одна настоящая установка на выдуманную машину (профиль оболочки есть, шлюз не задан): её читают тесты, которым нужен готовый результат."""
    m = Machine(tmp_path_factory.mktemp("machine"), dsh=True)
    r = m.run()
    assert r.code == 0, r.err + r.out
    m.result, m.install_log = r, m.log()
    return m


# ══ обычная установка ═════════════════════════════════════════════
@needs_index
def test_установка_в_пустой_домашний_каталог_пишет_службы_и_таймер_с_именами_flyarchive(installed):
    assert sorted(os.listdir(installed.units)) == BASE_UNITS
    assert installed.result.err == ""


@needs_index
def test_установка_заводит_архив_тем_же_init_каталоги_токен_база_сверки_таблица_индекса(installed):
    home = installed.home
    assert sorted(n for n in os.listdir(home) if n in ("secrets", "logs", "index", "corpus")) == ["corpus", "index", "logs", "secrets"]
    assert mode(home / "secrets" / "local.token") == 0o600 and mode(home / "secrets") == 0o700 and mode(home) & 0o077 == 0
    assert (home / "index" / "known.sqlite").is_file() and (home / "index" / "lance").is_dir()
    assert "служебный токен" in installed.result.out and "таблица индекса" in installed.result.out


@needs_index
def test_службы_запускают_код_из_репозитория_а_не_из_каталога_архива(installed):
    for name in ("search", "office", "mcp"):
        text = installed.unit(f"flyarchive-{name}.service")
        assert f"WorkingDirectory={TOOLS}" in text
        assert str(installed.home / "tools") not in text, f"{name}: код ищется в каталоге архива"
    scripts = {"search": "webui.py", "office": "office_server.py", "mcp": "mcp_server.py"}
    for name, script in scripts.items():
        assert f"ExecStart={sys.executable} {TOOLS}/{script}" in installed.unit(f"flyarchive-{name}.service")
    assert f"ExecStart={TOOLS}/dsh-web-start" in installed.unit("flyarchive-dsh.service")
    assert f"ExecStart={sys.executable} {TOOLS}/flyarchive inbox run" in installed.unit("flyarchive-inbox.service")


@needs_index
def test_каталог_архива_передан_службам_одной_строкой_и_журналы_в_нём(installed):
    home = str(installed.home)
    logs = {"search": "search.log", "office": "office.log", "mcp": "mcp.log", "dsh": "dsh-web.log", "inbox": "inbox.log"}
    for name, log in logs.items():
        text = installed.unit(f"flyarchive-{name}.service")
        assert text.count("Environment=FLYARCHIVE_HOME=" + home + "\n") == 1, name
        assert f"StandardOutput=append:{home}/logs/{log}" in text and f"StandardError=append:{home}/logs/{log}" in text, name


@needs_index
def test_в_службах_нет_домашнего_пути_процесса_и_адресов_только_подстановки(installed):
    user, home = str(installed.user), str(installed.home)
    for name in os.listdir(installed.units):
        text = installed.unit(name)
        assert user not in text, f"{name}: домашний путь процесса вместо подстановки"
        assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text), f"{name}: адрес в тексте службы"
        # клон и окружение новичка лежат в его домашнем каталоге (/home/…): путь кода и интерпретатора там законны, это подстановки {code} и {python}
        assert not re.search(r"(?i)\.ts\.net|/home/|/Users/", text.replace(TOOLS, "{code}").replace(sys.executable, "{python}")), name
    env_lines = [line for n in os.listdir(installed.units) for line in installed.unit(n).splitlines() if line.startswith("Environment=")]
    assert all(line.startswith(("Environment=FLYARCHIVE_HOME=", "Environment=PATH=")) for line in env_lines), env_lines
    assert home in installed.unit("flyarchive-search.service")


@needs_index
def test_службы_закрыты_umask_0077_перезапускаются_и_не_ждут_default_target(installed):
    for name in BASE_UNITS:
        text = installed.unit(name)
        assert "After=default.target" not in text, name
        if name.endswith(".service"):
            assert "UMask=0077" in text, name
    for name in ("search", "office", "mcp"):
        service = unit_lines(installed.unit(f"flyarchive-{name}.service"))
        assert "Restart=on-failure" in service["[Service]"] and "RestartSec=5" in service["[Service]"], name
        assert service["[Install]"] == ["WantedBy=default.target"] and "Type=simple" in service["[Service]"]
    dsh = unit_lines(installed.unit("flyarchive-dsh.service"))
    assert "Restart=on-failure" in dsh["[Service]"] and "RestartSec=10" in dsh["[Service]"]
    assert "After=flyarchive-mcp.service" in dsh["[Unit]"]
    assert "Wants=flyarchive-search.service flyarchive-office.service flyarchive-mcp.service" in dsh["[Unit]"]


@needs_index
def test_служба_оболочки_видит_dsh_через_PATH_с_каталогом_найденной_команды(installed):
    line = next(l for l in installed.unit("flyarchive-dsh.service").splitlines() if l.startswith("Environment=PATH="))
    entries = line[len("Environment=PATH="):].split(":")
    assert entries[0] == str(installed.bin), "каталог, где при установке нашёлся dsh, первый в PATH службы"
    assert "%h/.npm-global/bin" in entries and "/usr/bin" in entries, "и привычный каталог npm пользователя, и системные"


@needs_index
def test_без_dsh_в_PATH_службы_оболочки_нет_вовсе_а_значит_нет_и_её_PATH(tmp_path):
    # служба оболочки без dsh не ставится (подробности — tests/test_install_shell.py); каталог npm в PATH службы нужен только когда она есть
    m = Machine(tmp_path, dsh=False)
    assert m.run().code == 0
    assert not (m.units / "flyarchive-dsh.service").exists()


@needs_index
def test_описание_служб_называет_порты_из_настроек(tmp_path):
    m = Machine(tmp_path)
    r = m.run(FLYARCHIVE_SEARCH_PORT="9101", FLYARCHIVE_OFFICE_PORT="9102", FLYARCHIVE_MCP_PORT="9103", FLYARCHIVE_DSH_PORT="9104")
    assert r.code == 0, r.err
    for name, port in (("search", 9101), ("office", 9102), ("mcp", 9103), ("dsh", 9104)):
        description = next(l for l in m.unit(f"flyarchive-{name}.service").splitlines() if l.startswith("Description="))
        assert f"порт {port}" in description, (name, description)


@needs_index
def test_таймер_и_служба_разбора_как_раньше_каждые_тридцать_минут_oneshot(installed):
    timer, service = installed.unit("flyarchive-inbox.timer"), unit_lines(installed.unit("flyarchive-inbox.service"))
    assert "OnUnitActiveSec=30min" in timer and "OnBootSec=3min" in timer and "AccuracySec=10s" in timer and "WantedBy=timers.target" in timer
    assert "Type=oneshot" in service["[Service]"] and "Nice=10" in service["[Service]"] and "UMask=0077" in service["[Service]"]


@needs_index
def test_файл_настройки_оболочки_собран_в_каталоге_архива_с_закрытыми_правами(installed):
    path = installed.home / "dsh.patch.yml"
    text = path.read_text(encoding="utf-8")
    assert mode(path) == 0o600
    assert "id: mcp-flyarchive" in text and "name: '@deepseek-ai/dsh-mcp-client'" in text and "serverName: flyarchive" in text
    assert "url: 'http://127.0.0.1:8767/mcp'" in text, "адрес переходника — из настройки mcp_url (или порта по умолчанию)"
    assert "Authorization: !!js '`Bearer ${process.env.FLYARCHIVE_LOCAL_TOKEN}`'" in text
    assert "id: flyarchive-admin" in text and "name: 'flyarchive-dsh-plugin'" in text and f"command: '{installed.home}/bin/flyarchive'" in text


@needs_index
def test_в_файле_настройки_оболочки_нет_ни_моделей_ни_поставщиков_ни_ключей(installed):
    text = (installed.home / "dsh.patch.yml").read_text(encoding="utf-8")
    for word in ("llm-pi-ai", "agent-default-model", "providers", "apiKeyEnv", "baseURL", "contextWindow", "reasoning", "model:"):
        assert word not in text, word
    assert not re.search(MODEL_WORDS, text)


@needs_index
def test_адрес_переходника_в_файле_настройки_оболочки_из_настройки_mcp_url_или_порта(tmp_path):
    m = Machine(tmp_path / "а")
    assert m.run(FLYARCHIVE_MCP_URL="http://127.0.0.1:9777/mcp").code == 0
    assert "url: 'http://127.0.0.1:9777/mcp'" in (m.home / "dsh.patch.yml").read_text(encoding="utf-8")
    other = Machine(tmp_path / "б")
    assert other.run(FLYARCHIVE_MCP_PORT="9888").code == 0
    assert "url: 'http://127.0.0.1:9888/mcp'" in (other.home / "dsh.patch.yml").read_text(encoding="utf-8")


@needs_index
def test_плагин_лежит_в_профиле_оболочки_без_тестов_и_стенда(installed):
    target = installed.profile / "node_modules" / "flyarchive-dsh-plugin"
    want = sources_of(PLUGIN)
    got = {os.path.relpath(os.path.join(f, n), target).replace(os.sep, "/"): open(os.path.join(f, n), "rb").read()
           for f, _, names in os.walk(target) for n in names}
    assert got == want and "package.json" in got and "lib/index.js" in got and "lib/client.js" in got
    assert not any(part in ("test", "e2e") for path in got for part in path.split("/"))
    assert "перезапус" in installed.result.out.lower() and "flyarchive-dsh" in installed.result.out


@needs_index
def test_прежняя_папка_плагина_заменяется_целиком_а_чужие_каталоги_профиля_стоят(tmp_path):
    m = Machine(tmp_path)
    target = m.profile / "node_modules" / "flyarchive-dsh-plugin"
    (target / "lib").mkdir(parents=True)
    (target / "lib" / "old.js").write_text("// прежняя версия", encoding="utf-8")
    (target / "stale.txt").write_text("лишний", encoding="utf-8")
    foreign = {"other-plugin/index.js": "// чужой", "flyarchive-dsh-plugin-extra/lib/x.js": "// сосед с похожим именем", ".package-lock.json": "{}"}
    for rel, text in foreign.items():
        path = m.profile / "node_modules" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    before = tree(m.profile / "node_modules")
    assert m.run().code == 0
    assert not (target / "lib" / "old.js").exists() and not (target / "stale.txt").exists()
    after = tree(m.profile / "node_modules")
    for rel in foreign:
        assert after[rel] == before[rel], f"{rel}: чужой файл тронут"
    assert (m.profile / "node_modules" / "other-plugin").is_dir() and (target / "package.json").is_file()


@needs_index
def test_плагин_если_прежняя_папка_ссылка_ссылка_заменяется_а_то_на_что_она_вела_остаётся(tmp_path):
    m = Machine(tmp_path)
    elsewhere = tmp_path / "чужая-папка"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("не трогать", encoding="utf-8")
    link = m.profile / "node_modules" / "flyarchive-dsh-plugin"
    link.parent.mkdir(parents=True)
    os.symlink(elsewhere, link)
    assert m.run().code == 0
    assert not os.path.islink(link) and (link / "package.json").is_file()
    assert (elsewhere / "keep.txt").read_text(encoding="utf-8") == "не трогать"


@needs_index
def test_без_ключа_no_plugin_профиль_оболочки_не_трогается(tmp_path):
    m = Machine(tmp_path)
    (m.profile / "node_modules" / "other").mkdir(parents=True)
    (m.profile / "node_modules" / "other" / "x.js").write_text("//", encoding="utf-8")
    before = tree(m.profile)
    r = m.run("--no-plugin")
    assert r.code == 0 and tree(m.profile) == before
    assert not (m.profile / "node_modules" / "flyarchive-dsh-plugin").exists()
    assert sorted(os.listdir(m.units)) == BASE_UNITS and "плагин" in r.out.lower()


@needs_index
def test_нет_каталога_профиля_оболочки_не_сбой_шаг_пропущен_с_понятной_строкой_остальное_ставится(tmp_path):
    m = Machine(tmp_path, profile=False)
    r = m.run()
    assert r.code == 0, r.err
    assert not m.profile.exists() and not (m.user / ".dsh").exists(), "профиль установка не создаёт"
    assert str(m.profile) in r.out and "профиль" in r.out and "первого запуска" in r.out
    assert sorted(os.listdir(m.units)) == BASE_UNITS and (m.home / "dsh.patch.yml").is_file()


@needs_index
def test_службы_запускаются_по_порядку_зависимостей_таймер_как_у_inbox_set(installed):
    assert installed.install_log == ["--user daemon-reload"] + [f"--user enable --now {unit}" for unit in START_ORDER] + \
        [f"--user {c}" for c in TIMER_COMMANDS]


@needs_index
def test_без_ключа_no_start_файлы_пишутся_а_systemctl_не_зовётся_вовсе(tmp_path):
    m = Machine(tmp_path)
    r = m.run("--no-start")
    assert r.code == 0, r.err
    assert sorted(os.listdir(m.units)) == BASE_UNITS and (m.home / "dsh.patch.yml").is_file()
    assert not (m.user / "systemctl.log").exists(), "systemctl вызван"
    assert "systemctl" in r.out, "сказано, чем запустить службы позже"


@needs_index
def test_нет_systemctl_не_сбой_службы_записаны_но_не_запущены_строка_объясняет(tmp_path):
    m = Machine(tmp_path, systemctl=None, plain_path=True)
    r = m.run()
    assert r.code == 0, r.err
    assert sorted(os.listdir(m.units)) == BASE_UNITS and (m.home / "dsh.patch.yml").is_file()
    assert "systemctl" in r.out and "не найден" in r.out


@needs_index
def test_systemctl_отказал_код_1_команда_и_причина_названы_файлы_уже_записаны(tmp_path):
    m = Machine(tmp_path, systemctl='if [ "$2" = enable ]; then echo "Failed to connect to bus" >&2; exit 1; fi')
    r = m.run()
    assert r.code == 1
    assert "enable --now flyarchive-search.service" in r.err and "Failed to connect to bus" in r.err
    assert sorted(os.listdir(m.units)) == BASE_UNITS
    assert m.log() == ["--user daemon-reload", "--user enable --now flyarchive-search.service"], "после отказа остальное не запускается"


@needs_index
def test_без_inbox_json_установка_заводит_настройку_приёмки_и_входящую_папку_с_периодом_по_умолчанию(installed):
    assert json.loads((installed.home / "inbox.json").read_text(encoding="utf-8"))["period"] == 30
    assert (installed.home / "входящие").is_dir()


@needs_index
def test_период_таймера_берётся_из_готовой_настройки_приёмки_и_она_не_переписывается(tmp_path):
    m = Machine(tmp_path)
    m.home.mkdir(mode=0o700)
    box = tmp_path / "моя-входящая"
    config = m.home / "inbox.json"
    config.write_text(json.dumps({"inbox": str(box), "period": 5, "llm": False, "cloud": False}), encoding="utf-8")
    config.chmod(0o600)
    before = tree(m.home)["inbox.json"]
    assert m.run().code == 0
    assert "OnUnitActiveSec=5min" in m.unit("flyarchive-inbox.timer")
    assert tree(m.home)["inbox.json"] == before, "готовая настройка приёмки переписана"


@needs_index
def test_итог_печатается_и_называет_что_осталось_владельцу(installed):
    assert "Осталось сделать самому" in installed.result.out
    assert "inbox set --path" not in installed.result.out, "входящая папка уже заведена установкой"    # «inbox set --llm off» — подсказка про модель (FR-107)


# ══ --dry-run ═════════════════════════════════════════════════════
def test_dry_run_ничего_не_создаёт_ни_каталога_архива_ни_служб_и_не_зовёт_systemctl(tmp_path):
    m = Machine(tmp_path, dsh=True)
    before = tree(tmp_path)
    r = m.run("--dry-run")
    assert r.code == 0, r.err
    assert tree(tmp_path) == before, "пробный прогон что-то записал"
    assert not m.home.exists() and not m.units.exists()
    assert m.log() == [] and m.log("tailscale.log") == []


def test_dry_run_с_ключами_ничего_не_пишет_и_с_no_plugin_и_с_gateway_auto(tmp_path):
    m = Machine(tmp_path, tailscale=GOOD_TAILSCALE)
    before = tree(tmp_path)
    for args in (("--dry-run", "--no-plugin"), ("--dry-run", "--no-start"), ("--dry-run", "--gateway", "auto"), ("--dry-run", "--json")):
        r = m.run(*args)
        assert r.code == 0, (args, r.err)
        assert not m.home.exists() and not m.units.exists() and m.log() == [], args
    after = tree(tmp_path)
    assert set(after) - set(before) <= {"user/tailscale.log"}, "подставной tailscale пишет вопросы в свой журнал; больше ничего не появилось"
    assert {k: v for k, v in after.items() if k in before} == before


def test_dry_run_печатает_каждый_файл_с_путём_и_содержимым(tmp_path):
    m = Machine(tmp_path)
    r = m.run("--dry-run")
    for name in BASE_UNITS:
        assert str(m.units / name) in r.out, name
    assert str(m.home / "dsh.patch.yml") in r.out
    assert f"ExecStart={sys.executable} {TOOLS}/webui.py" in r.out and "OnUnitActiveSec=30min" in r.out and "id: mcp-flyarchive" in r.out
    assert str(m.profile / "node_modules" / "flyarchive-dsh-plugin") in r.out, "плагин назван"


def test_dry_run_печатает_каждую_команду_systemctl_которая_была_бы_выполнена(tmp_path):
    m = Machine(tmp_path)
    r = m.run("--dry-run")
    lines = [line.strip() for line in r.out.splitlines() if "systemctl --user" in line]
    want = ["systemctl --user daemon-reload"] + [f"systemctl --user enable --now {u}" for u in START_ORDER] + [f"systemctl --user {c}" for c in TIMER_COMMANDS]
    assert lines == want, lines


def test_dry_run_json_файлы_с_содержимым_или_отпечатком_и_команды_списком(tmp_path):
    m = Machine(tmp_path)
    data = m.run("--dry-run", "--json").json()
    assert data["dry_run"] is True and data["home"] == str(m.home) and data["code"] == TOOLS and data["python"] == sys.executable
    files = {f["path"]: f for f in data["files"]}
    for name in BASE_UNITS:
        entry = files[str(m.units / name)]
        assert entry["content"].startswith("[") and entry["sha256"] == sha(entry["content"].encode("utf-8")) and entry["mode"] == "0644", name
    patch = files[str(m.home / "dsh.patch.yml")]
    assert "id: mcp-flyarchive" in patch["content"] and patch["mode"] == "0600"
    plugin = {path: f for path, f in files.items() if "flyarchive-dsh-plugin" in path}
    want = sources_of(PLUGIN)
    assert {os.path.relpath(p, m.profile / "node_modules" / "flyarchive-dsh-plugin").replace(os.sep, "/") for p in plugin} == set(want)
    for path, entry in plugin.items():
        rel = os.path.relpath(path, m.profile / "node_modules" / "flyarchive-dsh-plugin").replace(os.sep, "/")
        assert entry["sha256"] == sha(want[rel]), rel
    assert data["commands"] == [["systemctl", "--user", "daemon-reload"]] + [["systemctl", "--user", "enable", "--now", u] for u in START_ORDER] + \
        [["systemctl", "--user", *c.split()] for c in TIMER_COMMANDS]
    assert data["init"] is None and data["failed"] is None


def test_dry_run_с_no_start_команд_нет_а_с_no_plugin_плагина_в_списке_нет(tmp_path):
    m = Machine(tmp_path)
    data = m.run("--dry-run", "--no-start", "--json").json()
    assert data["commands"] == [] and any(s["step"] == "start" for s in data["skipped"])
    data = m.run("--dry-run", "--no-plugin", "--json").json()
    assert not [f for f in data["files"] if "flyarchive-dsh-plugin" in f["path"]] and any(s["step"] == "plugin" for s in data["skipped"])


def test_dry_run_в_json_у_машины_без_systemctl_команд_нет_а_пропуск_назван(tmp_path):
    m = Machine(tmp_path, systemctl=None, plain_path=True)
    data = m.run("--dry-run", "--json").json()
    assert data["commands"] == [] and [s for s in data["skipped"] if s["step"] == "start" and "systemctl" in s["text"]]


@needs_index
def test_dry_run_показал_ровно_то_что_потом_записала_настоящая_установка(tmp_path):
    m = Machine(tmp_path, dsh=True)
    plan = m.run("--dry-run", "--json").json()
    assert not m.home.exists()
    assert m.run().code == 0
    for entry in plan["files"]:
        data = open(entry["path"], "rb").read()
        assert sha(data) == entry["sha256"], entry["path"]
        if "content" in entry:
            assert data.decode("utf-8") == entry["content"], entry["path"]
    real = [" ".join(c[1:]) for c in plan["commands"]]
    assert m.log() == real


@needs_index
def test_настоящая_установка_с_json_сообщает_то_же_самое_без_содержимого_и_с_ходом_init(tmp_path):
    m = Machine(tmp_path)
    data = m.run("--json").json()
    assert data["dry_run"] is False and data["failed"] is None and data["init"]["home"] == str(m.home)
    assert [s["id"] for s in data["init"]["steps"]] == ["home", "secrets", "token", "logs", "index", "corpus", "known", "table"]
    assert all("content" not in f for f in data["files"]) and {f["path"] for f in data["files"]} >= {str(m.units / n) for n in BASE_UNITS}
    assert data["commands"][0] == ["systemctl", "--user", "daemon-reload"]


# ══ шлюз ══════════════════════════════════════════════════════════
@needs_index
def test_шлюз_без_настроек_не_ставится_и_строка_называет_две_настройки(installed):
    assert "flyarchive-gateway.service" not in os.listdir(installed.units) and not any("gateway" in c for c in installed.install_log)
    out = installed.result.out
    assert "gateway_bind" in out and "public_url" in out and "--gateway auto" in out


@needs_index
def test_шлюз_с_заданными_настройками_ставится_а_в_службе_нет_адреса_и_имени(tmp_path):
    m = Machine(tmp_path, settings_file={"gateway_bind": TAILNET_IP, "public_url": f"http://{NODE}:8780"})
    r = m.run()
    assert r.code == 0, r.err
    text = m.unit("flyarchive-gateway.service")
    assert TAILNET_IP not in text and NODE not in text and "ts.net" not in text and "8780" in text.splitlines()[1], "порт — в описании, адреса нет"
    assert [l for l in text.splitlines() if l.startswith("Environment=")] == [f"Environment=FLYARCHIVE_HOME={m.home}"]
    assert f"ExecStart={sys.executable} {TOOLS}/gateway.py" in text and f"WorkingDirectory={TOOLS}" in text
    assert f"StandardOutput=append:{m.home}/logs/gateway.log" in text and "UMask=0077" in text
    started = [l.split()[-1] for l in m.log() if " enable " in l]
    assert started[:5] == ["flyarchive-search.service", "flyarchive-office.service", "flyarchive-mcp.service", "flyarchive-gateway.service",
                           "flyarchive-dsh.service"]


@needs_index
@pytest.mark.parametrize("given", [{"gateway_bind": TAILNET_IP}, {"public_url": f"http://{NODE}:8780"}, {}], ids=["только адрес", "только внешний адрес", "ничего"])
def test_шлюз_без_одной_из_двух_настроек_не_ставится(tmp_path, given):
    m = Machine(tmp_path, settings_file=given)
    r = m.run()
    assert r.code == 0 and "flyarchive-gateway.service" not in os.listdir(m.units)
    assert "gateway_bind" in r.out and "public_url" in r.out and not any("gateway" in c for c in m.log())


@needs_index
def test_gateway_off_шлюз_не_ставится_и_частную_сеть_не_спрашивают(tmp_path):
    m = Machine(tmp_path, tailscale="exit 1", settings_file={"gateway_bind": TAILNET_IP, "public_url": f"http://{NODE}:8780"})
    r = m.run("--gateway", "off")
    assert r.code == 0 and "flyarchive-gateway.service" not in os.listdir(m.units) and m.log("tailscale.log") == []
    assert "--gateway off" in r.out


@needs_index
def test_gateway_auto_спрашивает_частную_сеть_и_дописывает_недостающие_ключи_с_правами_0600(machine):
    machine.put_settings({"search_port": 9001, "embed_model": "модель-№1"})
    r = machine.run("--gateway", "auto")
    assert r.code == 0, r.err
    path = machine.home / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data) == ["search_port", "embed_model", "gateway_bind", "public_url"], "прочие ключи и их порядок сохранены"
    assert data == {"search_port": 9001, "embed_model": "модель-№1", "gateway_bind": TAILNET_IP, "public_url": f"http://{NODE}:8780"}
    assert mode(path) == 0o600
    assert machine.log("tailscale.log") == ["ip -4", "status --json"]
    assert "flyarchive-gateway.service" in os.listdir(machine.units)
    assert not any(n.endswith(".tmp") for n in os.listdir(machine.home)), "временный файл не остался"


@needs_index
def test_gateway_auto_без_файла_настроек_создаёт_его_закрытым(machine):
    assert machine.run("--gateway", "auto").code == 0
    path = machine.home / "settings.json"
    assert mode(path) == 0o600 and json.loads(path.read_text(encoding="utf-8")) == {"gateway_bind": TAILNET_IP, "public_url": f"http://{NODE}:8780"}


@needs_index
def test_gateway_auto_порт_внешнего_адреса_из_настройки_gateway_port(machine):
    machine.put_settings({"gateway_port": 9100})
    assert machine.run("--gateway", "auto").code == 0
    assert json.loads((machine.home / "settings.json").read_text(encoding="utf-8"))["public_url"] == f"http://{NODE}:9100"


@needs_index
def test_gateway_auto_заданные_значения_не_перезаписываются_пишется_только_недостающее(machine):
    machine.put_settings({"gateway_bind": OTHER_IP, "embed_model": "x"})
    assert machine.run("--gateway", "auto").code == 0
    data = json.loads((machine.home / "settings.json").read_text(encoding="utf-8"))
    assert data == {"gateway_bind": OTHER_IP, "embed_model": "x", "public_url": f"http://{NODE}:8780"}
    machine.put_settings({"public_url": "http://archive.example.com:8780"})
    assert machine.run("--gateway", "auto").code == 0
    data = json.loads((machine.home / "settings.json").read_text(encoding="utf-8"))
    assert data == {"public_url": "http://archive.example.com:8780", "gateway_bind": TAILNET_IP}


@needs_index
def test_gateway_auto_значения_из_окружения_тоже_не_перезаписываются_и_файл_не_пишется(machine):
    machine.put_settings({"embed_model": "x"})
    before = tree(machine.home)["settings.json"]
    r = machine.run("--gateway", "auto", FLYARCHIVE_GATEWAY_BIND=OTHER_IP, FLYARCHIVE_PUBLIC_URL="http://archive.example.com:8780")
    assert r.code == 0 and tree(machine.home)["settings.json"] == before
    assert machine.log("tailscale.log") == [], "оба значения заданы: спрашивать нечего"
    assert "flyarchive-gateway.service" in os.listdir(machine.units)


BAD_TAILSCALE = {
    "не запущена": "exit 1",
    "нет адреса": says("", selfname(NODE + ".")),
    "нет имени": says(TAILNET_IP, selfname("")),
    "все адреса": says("0.0.0.0", selfname(NODE + ".")),                              # открыл бы архив всей сети
    "адрес локальной сети": says("192.0.2.20", selfname(NODE + ".")),                  # не из диапазона частной сети
    "за диапазоном": says("100." + "128.0.1", selfname(NODE + ".")),                  # сразу за диапазоном
    "перед диапазоном": says("100." + "63.255.1", selfname(NODE + ".")),              # сразу перед диапазоном
    "десятая сеть": says("10." + "0.0.5", selfname(NODE + ".")),
    "не адрес": says("100.64." + "999.1", selfname(NODE + ".")),
    "негодное имя": says(TAILNET_IP, selfname("node." + "ts.net; rm -rf ~")),          # имя не похоже на имя узла
    "не json": says(TAILNET_IP, "не json"),
}


@needs_index
@pytest.mark.parametrize("tailscale", list(BAD_TAILSCALE.values()), ids=list(BAD_TAILSCALE))
def test_gateway_auto_без_годного_адреса_ничего_не_записывает_шлюз_не_ставит_строка_называет_настройки(tmp_path, tailscale):
    m = Machine(tmp_path, tailscale=tailscale, settings_file={"embed_model": "x"})
    before = tree(m.home)["settings.json"]
    r = m.run("--gateway", "auto")
    assert r.code == 0, r.err
    assert tree(m.home)["settings.json"] == before, "в файл настроек записан адрес вне диапазона частной сети"
    assert "flyarchive-gateway.service" not in os.listdir(m.units) and not any("gateway" in c for c in m.log())
    assert "gateway_bind" in r.out and "public_url" in r.out


@needs_index
def test_gateway_auto_нет_команды_tailscale_не_сбой(tmp_path):
    m = Machine(tmp_path, tailscale=None)
    r = m.run("--gateway", "auto", PATH=f"{m.bin}")
    assert r.code == 0 and "flyarchive-gateway.service" not in os.listdir(m.units) and not (m.home / "settings.json").exists()


def test_gateway_auto_пробный_прогон_спрашивает_но_файл_настроек_не_пишет_а_называет(machine):
    r = machine.run("--dry-run", "--gateway", "auto", "--json")
    data = r.json()
    assert not machine.home.exists()
    entry = next(f for f in data["files"] if f["path"] == str(machine.home / "settings.json"))
    assert json.loads(entry["content"]) == {"gateway_bind": TAILNET_IP, "public_url": f"http://{NODE}:8780"} and entry["mode"] == "0600"
    assert any(f["path"] == str(machine.units / "flyarchive-gateway.service") for f in data["files"])
    assert ["systemctl", "--user", "enable", "--now", "flyarchive-gateway.service"] in data["commands"]


def test_gateway_ключ_только_auto_или_off(machine):
    r = machine.run("--dry-run", "--gateway", "всегда")
    assert r.code == 2 and "--gateway" in r.err


# ══ безопасность ══════════════════════════════════════════════════
@needs_index
def test_служебный_токен_и_ключ_модели_не_попадают_ни_в_файл_ни_в_вывод(tmp_path):
    key = "LLMKEY-" + "q7Zk9x2Lm4Pd8Rt5"
    m = Machine(tmp_path)
    key_file = tmp_path / "ключ-модели"
    key_file.write_text(key + "\n", encoding="utf-8")
    key_file.chmod(0o600)
    m.put_settings({"llm_key_file": str(key_file), "dsh_llm_key_env": "LOCAL_LLM_KEY"})
    outputs = [m.run("--dry-run"), m.run("--dry-run", "--json"), m.run(), m.run("--json")]
    token = (m.home / "secrets" / "local.token").read_text(encoding="utf-8").strip()
    assert len(token) > 20
    written = [m.units, m.profile, m.home / "dsh.patch.yml", m.home / "settings.json"]
    for place in written:
        files = [place] if place.is_file() else [os.path.join(f, n) for f, _, names in os.walk(place) for n in names]
        for path in files:
            data = open(path, "rb").read().decode("utf-8", "replace")
            assert token not in data and key not in data, f"секрет записан в {path}"
    for r in outputs:
        assert token not in r.out + r.err and key not in r.out + r.err and r.code == 0


@needs_index
def test_чужие_файлы_служб_и_похожие_имена_установка_не_трогает(tmp_path):
    m = Machine(tmp_path)
    m.units.mkdir(parents=True)
    foreign = {"my-app.service": "[Service]\nExecStart=/bin/true\n", "flyarchive.service": "[Service]\n# без дефиса\n",
               "flyarchive-custom.service": "[Service]\n# своя служба владельца с нашей приставкой\n", "other.timer": "[Timer]\n",
               "notes.txt": "заметки"}
    for name, text in foreign.items():
        (m.units / name).write_text(text, encoding="utf-8")
    before = {name: (m.units / name).stat().st_mtime_ns for name in foreign}
    assert m.run().code == 0
    for name, text in foreign.items():
        assert (m.units / name).read_text(encoding="utf-8") == text and (m.units / name).stat().st_mtime_ns == before[name], name
    assert sorted(set(os.listdir(m.units)) - set(foreign)) == BASE_UNITS
    assert not [c for c in m.log() if "my-app" in c or "other" in c or "flyarchive-custom" in c or "flyarchive.service" in c]


@needs_index
def test_повторная_установка_даёт_те_же_файлы_байт_в_байт_и_не_меняет_данные_архива(tmp_path):
    m = Machine(tmp_path, dsh=True, settings_file={"gateway_bind": TAILNET_IP, "public_url": f"http://{NODE}:8780"})
    assert m.run().code == 0
    units_first, profile_first, archive_first = hashes(m.units), hashes(m.profile), hashes(m.home)
    log_first = m.log()
    assert m.run().code == 0
    assert hashes(m.units) == units_first and hashes(m.profile) == profile_first
    assert hashes(m.home) == archive_first, "данные архива изменились при повторной установке"
    assert m.log() == log_first + log_first, "повторная установка запускает то же самое"
    assert set(units_first) >= set(BASE_UNITS) | {"flyarchive-gateway.service"}


@needs_index
def test_повторная_установка_с_gateway_auto_ничего_не_меняет_во_второй_раз(machine):
    assert machine.run("--gateway", "auto").code == 0
    first = (tree(machine.home)["settings.json"], hashes(machine.units))
    assert machine.run("--gateway", "auto").code == 0
    assert (tree(machine.home)["settings.json"], hashes(machine.units)) == first
    assert machine.log("tailscale.log") == ["ip -4", "status --json"], "заданное во второй раз у частной сети не спрашивается"


@needs_index
def test_каталоги_служб_и_профиля_берутся_из_настроек(tmp_path):
    m = Machine(tmp_path)
    own_units, own_profile = tmp_path / "свои-службы", tmp_path / "свой-профиль"
    own_profile.mkdir()
    m.put_settings({"units_dir": str(own_units), "dsh_profile": str(own_profile)})
    assert m.run().code == 0
    assert sorted(os.listdir(own_units)) == BASE_UNITS and (own_profile / "node_modules" / "flyarchive-dsh-plugin" / "package.json").is_file()
    assert not m.units.exists() and not (m.profile / "node_modules").exists()


@needs_index
def test_каталог_архива_с_пробелом_отказ_с_понятной_строкой_а_не_битая_служба(tmp_path):
    m = Machine(tmp_path)
    m.home = tmp_path / "архив с пробелом"
    r = m.run()
    assert r.code == 1 and "пробел" in r.err and not m.units.exists()


# ══ одна реализация служб и таймера ═══════════════════════════════
def test_службу_и_таймер_разбора_пишет_один_код_inbox_units_это_install_inbox_units(tmp_path):
    home = str(tmp_path / "архив")
    for period in (1, 5, 30, 60):
        assert inbox.units(home, period) == install.inbox_units(home, period)
    assert sorted(inbox.units(home, 5)) == [inbox.SERVICE, inbox.TIMER] == [install.INBOX_SERVICE, install.INBOX_TIMER]


def test_текста_служб_в_inbox_py_нет():
    with open(os.path.join(TOOLS, "inbox.py"), encoding="utf-8") as f:
        source = f.read()
    for piece in ("[Service]", "[Timer]", "[Unit]", "OnUnitActiveSec", "WantedBy", "ExecStart"):
        assert piece not in source, f"inbox.py пишет текст службы сам: {piece}"


@needs_index
def test_inbox_set_пишет_те_же_байты_что_установка_и_меняет_только_период(installed):
    first = {n: installed.unit(n) for n in ("flyarchive-inbox.service", "flyarchive-inbox.timer")}
    before = len(installed.log())
    r = subprocess.run([sys.executable, FLYARCHIVE, "inbox", "set"], env=installed.env(), capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert {n: installed.unit(n) for n in first} == first, "inbox set без ключей переписал службу иначе, чем установка"
    r = subprocess.run([sys.executable, FLYARCHIVE, "inbox", "set", "--period", "5"], env=installed.env(), capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert installed.unit("flyarchive-inbox.service") == first["flyarchive-inbox.service"]
    assert installed.unit("flyarchive-inbox.timer") == first["flyarchive-inbox.timer"].replace("30min", "5min").replace("30 мин", "5 мин")
    assert installed.log()[before:before + 3] == ["--user daemon-reload", "--user enable --now flyarchive-inbox.timer",
                                                 "--user restart flyarchive-inbox.timer"]
    back = subprocess.run([sys.executable, FLYARCHIVE, "inbox", "set", "--period", "30"], env=installed.env(), capture_output=True)
    assert back.returncode == 0 and installed.unit("flyarchive-inbox.timer") == first["flyarchive-inbox.timer"], "прежний период возвращён"


# ══ шаблоны и старые файлы ════════════════════════════════════════
TEMPLATE_NAMES = ["dsh.patch.yml", "dsh.service", "inbox.service", "inbox.timer", "python.service"]
PLACEHOLDERS = {"code", "home", "python", "path", "name", "script", "description", "period", "mcp_url", "launcher"}
# Модель, поставщик и ключ в шаблоне и запускалке не стоят вовсе: их задаёт настройка, а в файле они были бы чьей-то одной машиной. Имён
# конкретных моделей тест не перечисляет — он ищет сами слова «модель», «поставщик», «ключ API» и признаки тега модели (`:latest`, `:27b`).
MODEL_WORDS = r"(?i)\bmodels?\b|\bproviders?\b|\bllm\b|api[-_ ]?key|baseurl|:latest\b|:\d+(?:\.\d+)?[bB]\b"


def template(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        return f.read()


def test_шаблоны_лежат_в_репозитории_пять_файлов_и_только_они():
    assert sorted(os.listdir(TEMPLATES)) == TEMPLATE_NAMES


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_в_шаблоне_только_известные_подстановки_и_ни_одного_абсолютного_пути_адреса_имени_узла_модели(name):
    text = template(name)
    used = set(re.findall(r"\{([a-z_]+)\}", text))
    assert used and used <= PLACEHOLDERS, used - PLACEHOLDERS
    assert not re.search(r"(?:^|[\s=:'\"(])/[A-Za-z~.]", text, re.M), "абсолютный путь в шаблоне"
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text), "адрес в шаблоне"
    assert not re.search(r"(?i)/home/|/Users/|/mnt/|[a-z]:[\\/]|\.ts\.net|\.local\b|\.lan\b|\.internal\b", text)
    assert not re.search(r"(?i)\b(?:8765|8766|8767|8780|3080|11434|8080)\b", text), "порт числом"
    assert not re.search(MODEL_WORDS, text), "модель, поставщик или ключ в шаблоне"
    rules = G.Rules((re.compile(r"(?!x)x"), re.compile(r"(?!x)x")), [])
    for number, line in enumerate(text.splitlines(), 1):
        assert rules.scan_line(line) == [], (name, number, line)


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_шаблон_подставляется_до_конца_и_ничего_не_оставляет(name):
    values = {key: "ЗНАЧЕНИЕ-" + key for key in PLACEHOLDERS}
    out = install.render(name, values)
    assert not re.search(r"\{[a-z_]+\}", out) and all("ЗНАЧЕНИЕ-" + key in out for key in set(re.findall(r"\{([a-z_]+)\}", template(name))))


def test_неизвестная_подстановка_в_шаблоне_ошибка_а_не_молчаливая_пустота(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "TEMPLATES", str(tmp_path))
    (tmp_path / "x.service").write_text("A={code}\nB={нет_такой}\nC={mystery}\n", encoding="utf-8")
    with pytest.raises(Exception) as caught:
        install.render("x.service", {"code": "/c"})
    assert "mystery" in str(caught.value)


def test_четыре_службы_на_python_пишет_один_шаблон_и_различаются_они_только_подстановками():
    values = {"code": "/c", "home": "/h", "python": "/p"}
    one = install.render("python.service", {**values, "name": "search", "script": "webui.py", "description": "Поиск"})
    two = install.render("python.service", {**values, "name": "mcp", "script": "mcp_server.py", "description": "Переходник"})
    assert one.replace("search", "mcp").replace("webui.py", "mcp_server.py").replace("Поиск", "Переходник") == two
    assert "ExecStart=/p /c/webui.py" in one and "append:/h/logs/search.log" in one


def test_каталога_systemd_с_готовыми_файлами_в_репозитории_нет_и_готовых_служб_нет():
    assert not os.path.exists(os.path.join(ROOT, "systemd"))
    ready = []
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]    # скрытые каталоги — не содержимое репозитория
        ready += [os.path.relpath(os.path.join(folder, n), ROOT).replace(os.sep, "/") for n in files if n.endswith((".service", ".timer"))]
    assert sorted(ready) == ["tools/templates/dsh.service", "tools/templates/inbox.service", "tools/templates/inbox.timer",
                             "tools/templates/python.service"]


def test_в_запускалках_и_шаблонах_нет_домашних_путей_адресов_имён_узлов_и_имён_моделей():
    rules = G.Rules((re.compile(r"(?!x)x"), re.compile(r"(?!x)x")), [])
    files = [os.path.join(TOOLS, n) for n in ("dsh-web-start", "dsh-url")] + \
        [os.path.join(TEMPLATES, n) for n in TEMPLATE_NAMES]
    for path in files:
        with open(path, encoding="utf-8") as f:
            for number, line in enumerate(f.read().splitlines(), 1):
                assert rules.scan_line(line) == [], (os.path.basename(path), number)
                assert not re.search(MODEL_WORDS + r"|(?<![\w])\.env\b", line), (os.path.basename(path), number, line)


def open_part_files():
    return [os.path.join(TOOLS, n) for n in ("dsh-web-start", "dsh-url", "install.py")] + \
        [os.path.join(TEMPLATES, n) for n in TEMPLATE_NAMES]


def words_in(rules, paths):
    found = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            found += [(os.path.basename(path), number) for number, line in enumerate(f.read().splitlines(), 1)
                      if any(kind == "word" for kind, _, _ in rules.scan_line(line))]
    return found


def test_поиск_слов_из_закрытого_списка_находит_слово_в_строке_и_не_находит_в_открытой_части(tmp_path):
    sample = tmp_path / "слова.txt"
    sample.write_text("образцовое-слово\nдругое слово*\n", encoding="utf-8")
    rules = G.Rules(G.load_words(str(sample))[0], [])
    assert [k for k, _, _ in rules.scan_line("Description=образцовое-слово (порт 1)")] == ["word"]
    assert words_in(rules, open_part_files()) == []


def test_в_шаблонах_запускалках_и_установке_нет_слов_из_закрытого_списка_владельца():
    path = os.environ.get(G.WORDS_ENV)
    if not path:
        pytest.skip(f"список запретных слов лежит вне репозитория и не задан ({G.WORDS_ENV}): без него проверка пуста")
    rules = G.Rules(G.load_words(os.path.expanduser(path))[0], [])
    assert words_in(rules, open_part_files()) == []


def test_обёртки_старого_установщика_в_открытой_части_нет_службы_ставит_только_flyarchive_install():
    """Обёртка «для тех, кто ставил службы этим файлом» нужна тому, у кого такое прошлое, а у постороннего его нет: файла нет, и на него никто не ссылается."""
    from openpart import open_files
    name = "install_" + "services"
    assert not os.path.exists(os.path.join(TOOLS, name + ".sh"))
    named = []
    for rel in open_files():
        with open(os.path.join(ROOT, rel), "rb") as f:
            if name.encode("ascii") in f.read():
                named.append(rel)
    assert named == [], named


def test_справка_команды_называет_все_ключи():
    r = subprocess.run([sys.executable, FLYARCHIVE, "install", "--help"], capture_output=True, text=True, encoding="utf-8",
                       env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "PYTHONIOENCODING": "utf-8", "FLYARCHIVE_HOME": "/nonexistent/архив"})
    assert r.returncode == 0 and r.stdout.startswith("usage: flyarchive install")
    for key in ("--dry-run", "--no-start", "--no-plugin", "--gateway", "--json"):
        assert key in r.stdout


def test_каталог_кода_установки_это_tools_репозитория_где_лежит_install_py():
    assert install.CODE == TOOLS == os.path.dirname(os.path.realpath(install.__file__)) and install.PLUGIN_SOURCE == PLUGIN
    assert S.SCHEMA["units_dir"].type == "path"

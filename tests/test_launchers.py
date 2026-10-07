"""Запускалки DSH и настройка: служебный токен доходит до переходника MCP (FR-02), всё остальное — из настроек (FR-104).

Запускалка `dsh-web-start` берёт каталог архива, порты, дополнительные файлы настройки оболочки, ключ локальной модели и переменные из файла
окружения командой `python3 tools/settings.py --get КЛЮЧ`; имён моделей, поставщиков, путей и слова о конкретном ключе в ней нет. Запускалка
`dsh-url` печатает ссылку из журнала оболочки в каталоге архива. Тесты запускают настоящие файлы со своим `HOME`, своим каталогом архива, подставными
`sleep` и `dsh` в PATH и своим сокетом на петле вместо переходника MCP (его порт тест называет сам: живой переходник владельца на порту по умолчанию
не затрагивается): настоящая оболочка и службы не трогаются.
"""
import json
import os
import re
import shutil
import site
import socket
import subprocess

import pytest

import foreign
import publication_gate as G
from test_install import MODEL_WORDS

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
LAUNCHERS = ["dsh-web-start"]
START = os.path.join(TOOLS, "dsh-web-start")
URL = os.path.join(TOOLS, "dsh-url")
PATCH_TEMPLATE = "templates/dsh.patch.yml"
pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="нет bash")


def text(name):
    with open(os.path.join(TOOLS, name), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("name", LAUNCHERS + ["dsh-url"])
def test_запускалка_без_ошибок_разбора(name):
    r = subprocess.run(["bash", "-n", os.path.join(TOOLS, name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("name", LAUNCHERS)
def test_запускалка_готовит_и_передаёт_служебный_токен(name):
    s = text(name)
    init, export, start = s.index("token init-local"), s.index("export FLYARCHIVE_LOCAL_TOKEN"), s.rindex("exec dsh")
    assert init < export < start                         # сначала токен, потом DSH
    assert "exit 1" in s[init:export]                    # негодный токен — DSH не стартует
    assert 'secrets/local.token' in s[init:export]


@pytest.mark.parametrize("name", LAUNCHERS)
def test_токен_не_печатается_и_не_записан_в_запускалку(name):
    s = text(name)
    assert "ba_" not in s
    assert "echo $FLYARCHIVE_LOCAL_TOKEN" not in s and 'echo "$FLYARCHIVE_LOCAL_TOKEN"' not in s


def test_настройка_dsh_шлёт_токен_переходнику_и_не_хранит_его():
    s = text(PATCH_TEMPLATE)
    mcp = s[s.index("id: mcp-flyarchive"):s.index("id: flyarchive-admin")]
    assert "Authorization: !!js '`Bearer ${process.env.FLYARCHIVE_LOCAL_TOKEN}`'" in mcp
    assert "ba_" not in s


def test_в_настройке_оболочки_из_репозитория_нет_ключей_и_имён_переменных_с_ключами():
    s = text(PATCH_TEMPLATE)
    assert "apiKeyEnv" not in s and "headers:" in s and s.count("Authorization") == 1
    assert not [line for line in s.splitlines() if "KEY" in line or "_API" in line], "имя переменной ключа в файле настройки из репозитория"


def test_в_запускалках_нет_моделей_поставщиков_домашних_путей_и_слова_о_конкретном_ключе():
    rules = G.Rules((re.compile(r"(?!x)x"), re.compile(r"(?!x)x")), [])
    for name in LAUNCHERS + ["dsh-url"]:
        for number, line in enumerate(text(name).splitlines(), 1):
            assert not re.search(MODEL_WORDS + r"|/mnt/|\.env\b|\$HOME/", line), (name, number, line)
            assert rules.scan_line(line) == [], (name, number)


# ── кит запуска ────────────────────────────────────────────────
LISTEN = object()                  # в настройках теста: порт сокета, который изображает переходник
LISTENERS = []


@pytest.fixture(autouse=True)
def _close_listeners():
    yield
    while LISTENERS:
        LISTENERS.pop().close()


def free_port():
    """Порт на петле, на котором никто не слушает."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Run:
    """Запуск запускалки на пустой машине: свой HOME, каталог архива вне его, подставные sleep и dsh, сокет теста вместо переходника MCP.
    listening — слушает ли сокет (иначе порт занят, но соединение отвергается). Порт переходника — переменная окружения, если его не задаёт
    сам тест в settings (LISTEN в mcp_port — порт сокета)."""

    def __init__(self, tmp_path, listening=True, settings=None, patch=True):
        self.user, self.home, self.bin = tmp_path / "user", tmp_path / "архив", tmp_path / "bin"
        for d in (self.user, self.bin):
            d.mkdir()
        self.home.mkdir(mode=0o700)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        if listening:
            self.sock.listen(8)
        LISTENERS.append(self.sock)
        self.port = self.sock.getsockname()[1]
        self.own_port = settings is None or "mcp_port" not in settings
        if settings is not None:
            path = self.home / "settings.json"
            data = {key: self.port if value is LISTEN else value for key, value in settings.items()}
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            path.chmod(0o600)
        if patch:
            (self.home / "dsh.patch.yml").write_text("# настройка оболочки образца\n", encoding="utf-8")
        for name, body in {"sleep": "", "dsh": 'printf "%s\\n" "$@" > "$HOME/dsh.args"\nenv > "$HOME/dsh.env"'}.items():
            stub = self.bin / name
            stub.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
            stub.chmod(0o755)

    def env(self, **extra):
        env = {"HOME": str(self.user), "FLYARCHIVE_HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "PYTHONIOENCODING": "utf-8",
               "LANG": "C.UTF-8",
               "PYTHONPATH": os.pathsep.join(p for p in (site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p)}
        if self.own_port:
            env["FLYARCHIVE_MCP_PORT"] = str(self.port)
        env.update(extra)
        return env

    def start(self, **extra):
        return subprocess.run(["bash", START], env=self.env(**extra), capture_output=True, text=True, encoding="utf-8", timeout=120)

    def args(self):
        path = self.user / "dsh.args"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else None

    def dsh_env(self):
        path = self.user / "dsh.env"
        return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line) if path.exists() else None


def secret(path, value, mode=0o600):
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)
    return str(path)


def test_запускалка_берёт_каталог_архива_порты_и_файл_настройки_из_настроек(tmp_path):
    run = Run(tmp_path, settings={"mcp_port": LISTEN, "dsh_port": 3199})
    r = run.start()
    assert r.returncode == 0, r.stderr
    assert run.args() == ["--profile", "web", "--patch", str(run.home / "dsh.patch.yml"), "--no-open", "--host", "127.0.0.1", "--port", "3199"]


def test_запускалка_по_умолчанию_берёт_порты_схемы_и_каталог_архива_из_переменной(tmp_path):
    run = Run(tmp_path)
    r = run.start()
    assert r.returncode == 0, r.stderr
    assert run.args()[-2:] == ["--port", "3080"] and run.args()[3] == str(run.home / "dsh.patch.yml")


def test_запускалка_без_переменной_архива_берёт_каталог_по_умолчанию_в_домашнем(tmp_path):
    run = Run(tmp_path)
    default = run.user / "flyarchive"
    default.mkdir(mode=0o700)
    (default / "dsh.patch.yml").write_text("# образец\n", encoding="utf-8")
    env = run.env()
    del env["FLYARCHIVE_HOME"]
    r = subprocess.run(["bash", START], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    assert run.args()[3] == str(default / "dsh.patch.yml") and (default / "secrets" / "local.token").is_file()


def test_запускалка_ждёт_переходник_на_порту_из_настроек_а_не_на_порту_по_умолчанию(tmp_path):
    elsewhere = free_port()
    run = Run(tmp_path, settings={"mcp_port": elsewhere})
    r = run.start()
    assert r.returncode == 1 and str(elsewhere) in r.stderr and run.args() is None, "сокет слушает, но переходник по настройке должен быть на другом порту"


def stub(run, name, body, folder=None):
    """Подставная программа в PATH теста (или в другом каталоге): sh-файл с телом body."""
    path = (folder or run.bin) / name
    path.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_запускалка_ждёт_переходник_без_ss_даже_если_она_в_пути_и_отвечает_что_никто_не_слушает(tmp_path):
    run = Run(tmp_path)
    called = run.user / "ss.вызвана"
    stub(run, "ss", f'echo вызвана >> "{called}"\nexit 1')
    r = run.start()
    assert r.returncode == 0, r.stderr
    assert run.args() is not None and not called.exists(), "порт проверяется без программы ss (пакет iproute2 есть не на каждой машине)"


def test_в_запускалке_оболочки_нет_вызова_ss():
    code = [line for line in text("dsh-web-start").splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if re.search(r"\bss\b", line)]


def test_переходник_не_поднялся_отказ_называет_порт_и_оболочка_не_запускается(tmp_path):
    run = Run(tmp_path, listening=False)                    # порт занят, но соединение отвергается: как будто переходник не поднялся
    r = run.start()
    assert r.returncode == 1 and str(run.port) in r.stderr and "не поднялся" in r.stderr and run.args() is None


def test_профиль_оболочки_берётся_из_настройки_dsh_profile(tmp_path):
    run = Run(tmp_path, settings={"dsh_profile": "~/профили/второй"})
    assert run.start().returncode == 0
    assert run.args()[:4] == ["--profile", "второй", "--patch", str(run.home / "dsh.patch.yml")]


def test_профиль_оболочки_без_настройки_web_и_косая_черта_на_конце_пути_имени_не_меняет(tmp_path):
    (tmp_path / "а").mkdir()
    (tmp_path / "б").mkdir()
    plain = Run(tmp_path / "а")
    assert plain.start().returncode == 0 and plain.args()[:2] == ["--profile", "web"]
    slashed = Run(tmp_path / "б", settings={"dsh_profile": "~/профили/третий/"})
    assert slashed.start().returncode == 0 and slashed.args()[:2] == ["--profile", "третий"]


def test_команду_архива_запускалка_оболочки_зовёт_через_запускалку_из_каталога_архива(tmp_path):
    run = Run(tmp_path)
    (run.home / "bin").mkdir(mode=0o700)
    stub(run, "flyarchive", f'echo "$@" >> "$HOME/запускалка.args"\nexec python3 \'{TOOLS}/flyarchive\' "$@"', folder=run.home / "bin")
    r = run.start()
    assert r.returncode == 0, r.stderr
    assert (run.user / "запускалка.args").read_text(encoding="utf-8").split() == ["token", "init-local"]
    assert (run.home / "secrets" / "local.token").is_file() and run.args() is not None


def test_запускалка_команды_отказала_оболочка_не_стартует_и_причина_названа(tmp_path):
    run = Run(tmp_path)
    (run.home / "bin").mkdir(mode=0o700)
    stub(run, "flyarchive", "exit 3", folder=run.home / "bin")
    r = run.start()
    assert r.returncode == 1 and "служебный токен" in r.stderr and run.args() is None


def test_запускалка_готовит_служебный_токен_в_каталоге_архива_и_передаёт_его_оболочке_не_печатая(tmp_path):
    run = Run(tmp_path)
    r = run.start()
    assert r.returncode == 0, r.stderr
    token = (run.home / "secrets" / "local.token").read_text(encoding="utf-8").strip()
    assert len(token) > 20 and run.dsh_env()["FLYARCHIVE_LOCAL_TOKEN"] == token
    assert token not in r.stdout + r.stderr and token not in "\n".join(run.args())


def test_нет_файла_настройки_оболочки_запускалка_отказывает_и_называет_команду_установки(tmp_path):
    run = Run(tmp_path, patch=False)
    r = run.start()
    assert r.returncode == 1 and "install" in r.stderr and run.args() is None


def test_дополнительные_файлы_настройки_передаются_оболочке_после_основного_по_порядку(tmp_path):
    first, second = tmp_path / "модели-один.yml", tmp_path / "модели-два.yml"
    run = Run(tmp_path, settings={"dsh_patches": [str(first), "{home}/модели-три.yml", str(second)]})
    assert run.start().returncode == 0
    args = run.args()
    patches = [args[i + 1] for i, a in enumerate(args) if a == "--patch"]
    assert patches == [str(run.home / "dsh.patch.yml"), str(first), str(run.home / "модели-три.yml"), str(second)]
    assert args[-5:] == ["--no-open", "--host", "127.0.0.1", "--port", "3080"]


# ── ключ локальной модели ──────────────────────────────────────
KEY = "local-key-test-value-0123456789"


def test_ключ_локальной_модели_читается_из_файла_и_экспортируется_под_именем_из_настройки(tmp_path):
    run = Run(tmp_path, settings={})
    key_file = secret(tmp_path / "ключ", KEY + "\r\n")
    (run.home / "settings.json").write_text(json.dumps({"llm_key_file": key_file, "dsh_llm_key_env": "LOCAL_LLM_KEY"}), encoding="utf-8")
    r = run.start()
    assert r.returncode == 0, r.stderr
    assert run.dsh_env()["LOCAL_LLM_KEY"] == KEY, "значение без перевода строки"
    assert KEY not in r.stdout + r.stderr


@pytest.mark.foreign_name
@pytest.mark.parametrize("settings", [{"dsh_llm_key_env": "LOCAL_LLM_KEY"}, {"llm_key_file": "{key}"}, {}], ids=["нет файла", "нет имени", "ничего"])
def test_ключ_модели_без_одной_из_двух_настроек_ничего_не_экспортируется(tmp_path, settings):
    run = Run(tmp_path)
    key_file = secret(tmp_path / "ключ", KEY)
    data = {k: v.replace("{key}", key_file) for k, v in settings.items()}
    (run.home / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    (run.home / "settings.json").chmod(0o600)
    assert run.start().returncode == 0
    env = run.dsh_env()
    assert KEY not in "".join(env.values()) and "LOCAL_LLM_KEY" not in env and foreign.KEY not in env


def test_ключ_модели_готовая_переменная_окружения_файл_не_перебивает(tmp_path):
    run = Run(tmp_path)
    key_file = secret(tmp_path / "ключ", KEY)
    (run.home / "settings.json").write_text(json.dumps({"llm_key_file": key_file, "dsh_llm_key_env": "LOCAL_LLM_KEY"}), encoding="utf-8")
    (run.home / "settings.json").chmod(0o600)
    assert run.start(LOCAL_LLM_KEY="from-env").returncode == 0
    assert run.dsh_env()["LOCAL_LLM_KEY"] == "from-env"


def test_нечитаемый_файл_ключа_не_сбой_ключ_просто_не_передаётся(tmp_path):
    run = Run(tmp_path, settings={"llm_key_file": str(tmp_path / "нет-такого"), "dsh_llm_key_env": "LOCAL_LLM_KEY"})
    r = run.start()
    assert r.returncode == 0 and "LOCAL_LLM_KEY" not in run.dsh_env()


# ── файл окружения владельца: только названные переменные ──────
SECOND = "second-key-0123456789"
ENV_FILES = [
    ("одна строка без перевода строки", f"FIRST_KEY={KEY}", KEY),
    ("после неё другая переменная", f"FIRST_KEY={KEY}\nSECOND_KEY={SECOND}\n", KEY),
    ("между другими, в кавычках, переводы строк Windows", f'AAA=aaa\r\nFIRST_KEY="{KEY}"\r\nZZZ=zzz\r\n', KEY),
    ("знак равенства в значении", "FIRST_KEY=abc=def==\nZZZ=zzz\n", "abc=def=="),
    ("с export и пробелами", f"  export FIRST_KEY = '{KEY}'\n", KEY),
    ("похожее имя не подходит", f"FIRST_KEY_OLD=wrong\nFIRST_KEY={KEY}\nNOT_FIRST_KEY=wrong\nMY FIRST_KEY=wrong\n", KEY),
    ("переменной в файле нет", f"SECOND_KEY={SECOND}\n", None),
    ("пустой файл", "", None),
    ("последняя из повторённых", "FIRST_KEY=old\nFIRST_KEY=new\n", "new"),
]


@pytest.mark.parametrize("title, env_text, want", ENV_FILES, ids=[c[0] for c in ENV_FILES])
def test_из_файла_окружения_берётся_только_своя_строка_названной_переменной(title, env_text, want, tmp_path):
    run = Run(tmp_path)
    env_file = tmp_path / "окружение.env"
    env_file.write_bytes(env_text.encode("utf-8"))
    (run.home / "settings.json").write_text(json.dumps({"env_file": str(env_file), "env_names": ["FIRST_KEY"]}), encoding="utf-8")
    (run.home / "settings.json").chmod(0o600)
    r = run.start()
    assert r.returncode == 0, r.stderr
    env = run.dsh_env()
    assert env.get("FIRST_KEY") == want
    assert "SECOND_KEY" not in env, "соседняя переменная из того же файла попала в окружение оболочки"
    assert "AAA" not in env and "ZZZ" not in env


def test_переменная_не_названная_в_env_names_в_окружение_не_попадает_а_названные_обе_попадают(tmp_path):
    run = Run(tmp_path)
    env_file = tmp_path / "окружение.env"
    env_file.write_text(f"FIRST_KEY={KEY}\nSECOND_KEY={SECOND}\nTHIRD_KEY=third\n", encoding="utf-8")
    (run.home / "settings.json").write_text(json.dumps({"env_file": str(env_file), "env_names": ["FIRST_KEY", "THIRD_KEY"]}), encoding="utf-8")
    (run.home / "settings.json").chmod(0o600)
    assert run.start().returncode == 0
    env = run.dsh_env()
    assert (env["FIRST_KEY"], env["THIRD_KEY"]) == (KEY, "third") and "SECOND_KEY" not in env


def test_без_env_names_файл_окружения_не_читается_вовсе(tmp_path):
    run = Run(tmp_path)
    env_file = tmp_path / "окружение.env"
    env_file.write_text(f"FIRST_KEY={KEY}\n", encoding="utf-8")
    (run.home / "settings.json").write_text(json.dumps({"env_file": str(env_file)}), encoding="utf-8")
    (run.home / "settings.json").chmod(0o600)
    assert run.start().returncode == 0 and "FIRST_KEY" not in run.dsh_env()


def test_без_env_file_названные_переменные_ничего_не_дают(tmp_path):
    run = Run(tmp_path, settings={"env_names": ["FIRST_KEY"]})
    assert run.start().returncode == 0 and "FIRST_KEY" not in run.dsh_env()


def test_готовая_переменная_окружения_файл_окружения_не_перебивает(tmp_path):
    run = Run(tmp_path)
    env_file = tmp_path / "окружение.env"
    env_file.write_text("FIRST_KEY=from-file-000000000000\n", encoding="utf-8")
    (run.home / "settings.json").write_text(json.dumps({"env_file": str(env_file), "env_names": ["FIRST_KEY"]}), encoding="utf-8")
    (run.home / "settings.json").chmod(0o600)
    assert run.start(FIRST_KEY="from-env").returncode == 0 and run.dsh_env()["FIRST_KEY"] == "from-env"


@pytest.mark.parametrize("bad", ["X;touch {marker}", "X$(touch {marker})", "X`touch {marker}`", "ТОКЕН", "1ABC", "A B", "A.*", "A/B"])
def test_негодное_имя_в_env_names_пропускается_и_ничего_не_исполняется(tmp_path, bad):
    run = Run(tmp_path)
    marker = tmp_path / "исполнено"
    env_file = tmp_path / "окружение.env"
    env_file.write_text("FIRST_KEY=1\nX=2\n", encoding="utf-8")
    name = bad.replace("{marker}", str(marker))
    (run.home / "settings.json").write_text(json.dumps({"env_file": str(env_file), "env_names": [name, "FIRST_KEY"]}), encoding="utf-8")
    (run.home / "settings.json").chmod(0o600)
    r = run.start()
    assert r.returncode == 0 and not marker.exists() and run.dsh_env()["FIRST_KEY"] == "1"
    assert "env_names" in r.stderr, "пропуск назван"


# ── dsh-url ────────────────────────────────────────────────────
def url_run(tmp_path, log=None, settings=None):
    run = Run(tmp_path, settings=settings)
    (run.home / "logs").mkdir()
    if log is not None:
        (run.home / "logs" / "dsh-web.log").write_bytes(log if isinstance(log, bytes) else log.encode("utf-8"))
    return run, subprocess.run(["bash", URL], env=run.env(), capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_dsh_url_печатает_последнюю_ссылку_с_портом_из_настроек_из_журнала_в_каталоге_архива(tmp_path):
    log = ("dsh web: http://127.0.0.1:3080/?token=old-token\n" "dsh web: http://127.0.0.1:3199/?token=first_1\n"
           "dsh web: http://127.0.0.1:3199/?token=Second-2\n")
    _, r = url_run(tmp_path, log, settings={"dsh_port": 3199})
    assert (r.returncode, r.stdout, r.stderr) == (0, "http://127.0.0.1:3199/?token=Second-2\n", "")


def test_dsh_url_читает_журнал_с_управляющими_байтами(tmp_path):
    _, r = url_run(tmp_path, b"\x1b[32m\x00http://127.0.0.1:3080/?token=abc_DEF-1\x1b[0m\n\xff\xfe")
    assert r.returncode == 0 and r.stdout == "http://127.0.0.1:3080/?token=abc_DEF-1\n"


def test_dsh_url_нет_журнала_код_1_и_путь_в_сообщении(tmp_path):
    run, r = url_run(tmp_path)
    assert r.returncode == 1 and r.stdout == "" and str(run.home / "logs" / "dsh-web.log") in r.stderr


def test_dsh_url_нет_ссылки_код_1_и_подсказка_называет_службу_оболочки(tmp_path):
    _, r = url_run(tmp_path, "dsh web: запускается\n")
    assert r.returncode == 1 and r.stdout == "" and "flyarchive-dsh" in r.stderr

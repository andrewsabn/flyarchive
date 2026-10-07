"""Единый модуль настроек (FR-95): слои, схема, проверки типов и границ, права на файл, отказы с кодами, пример и запуск как программы.

Все образцы выдуманные. Главное, что держат тесты: значение настройки не попадает в текст отказа (строка может оказаться секретом
по ошибке владельца), файл с чужой записью не читается, `load` ничего не пишет на диск, пример из репозитория проходит ворота публикации.
Окружение в тестах — всегда явный словарь `env=`: переменные той машины, где идут тесты, на результат не влияют.
"""
import ast
import builtins
import copy
import json
import os
import re
import signal
import subprocess
import sys

import pytest

import foreign
import messages as M
import publication_gate as G
import settings as S

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROGRAM = os.path.join(ROOT, "tools", "settings.py")
MODULE = PROGRAM
EXAMPLE = os.path.join(ROOT, "settings.example.json")

LEAK = "LEAKq7Zk9"                       # метка «значения»: нигде в тексте отказа и в выводе программы её быть не должно
FOREIGN_HOME = foreign.HOME_VARIABLE      # переменная каталога архива другой программы: проект её не читает
FOREIGN_NAME = foreign.NAME                  # имя другой программы: из него приставка переменных и каталог в домашнем
HOME_DIR = "/home" + "/"                 # образцы «настоящих» путей и адресов собираются из кусков: ворота читают и этот файл
PRIVATE_IP = "192.168." + "1.5"
GITIGNORE = "secrets/\nlogs/\nreports/\nindex/\ncorpus/\ncache/\n__pycache__/\n"
DUMMY_WORDS = ("слово-которого-нет-ни-в-одном-файле",)

# Что должно быть в схеме: ключ -> (тип, умолчание как в коде, нижняя граница, верхняя граница). Таблица — из описи 20а (разделы 1, 2, 3, 6)
# без настроек приёмки и распознавания (FR-98): первые лежат в inbox.json и меняются командой `inbox set`, вторые — сценарии владельца
INVENTORY = {
    # каталоги и файлы (A1, A3, A5, A8, A9, A10)
    "home": ("path", "~/flyarchive", None, None),
    "llm_key_file": ("path", "", None, None),
    "llm_cloud_key_file": ("path", "", None, None),
    "pdf_font": ("path", "", None, None),
    "env_file": ("path", "", None, None),
    "dsh_profile": ("path", "~/.dsh/profiles/web", None, None),
    "units_dir": ("path", "~/.config/systemd/user", None, None),
    "sandbox_dir": ("path", "~/.local/share/flyarchive-sandbox", None, None),
    "bin_dir": ("path", "~/.local/bin", None, None),
    # адреса и порты служб (B1-B8, B10)
    "search_port": ("int", 8765, 1, 65535),
    "office_port": ("int", 8766, 1, 65535),
    "mcp_port": ("int", 8767, 1, 65535),
    "mcp_url": ("url", "http://127.0.0.1:8767/mcp", None, None),
    "gateway_port": ("int", 8780, 1, 65535),
    "gateway_bind": ("str", "", None, None),
    "public_url": ("url", "", None, None),
    "public_hosts": ("list[str]", [], None, None),
    "hosts": ("list[str]", [], None, None),
    "dsh_port": ("int", 3080, 1, 65535),
    "embed_url": ("url", "http://127.0.0.1:11434/api/embed", None, None),
    "llm_local_url": ("url", "http://127.0.0.1:8080", None, None),
    "llm_cloud_url": ("url", "", None, None),
    "dsh_patches": ("list[path]", [], None, None),
    "env_names": ("list[str]", [], None, None),
    "dsh_llm_key_env": ("env_name", "", None, None),
    # модели (C1-C4)
    "embed_model": ("str", "bge-m3", None, None),
    "embed_dim": ("int", 1024, 1, 8192),
    "embed_gpu": ("bool", False, None, None),
    "llm_local_model": ("str", "", None, None),
    "llm_cloud_model": ("str", "", None, None),
    # поиск и ответы серверов (F1, F2)
    "search_half_life_days": ("int", 540, 1, 36500),
    "search_nprobes": ("int", 400, 1, 10000),
    "search_refine": ("int", 30, 1, 1000),
    "api_snippet_chars": ("int", 420, 40, 5000),
    "api_max_k": ("int", 20, 1, 100),
    "api_budget_chars": ("int", 6000, 500, 100000),
    "mcp_timeout_s": ("int", 300, 1, 3600),
    # сроки входа (F3)
    "link_ttl_s": ("int", 86400, 60, 2592000),
    "login_ttl_s": ("int", 300, 30, 3600),
    "session_ttl_s": ("int", 43200, 300, 2592000),
    # проверка моделью (F4)
    "llm_timeout_s": ("int", 180, 5, 3600),
    "llm_max_chars": ("int", 30000, 1000, 1_000_000),
    "llm_max_pages": ("int", 20, 1, 200),
    # сервер документов (F7): верхняя граница размера документа — то, что пройдёт в base64 через самое узкое тело запроса (шлюз, 24 МиБ)
    "submit_max_mb": ("int", 16, 1, 17),
    "out_keep_hours": ("int", 48, 1, 8760),
}
CHOICES = {}          # после FR-98 в схеме нет настройки «одно из списка»: код выбора проверяют пробные настройки (см. probes)
# Что убрано из схемы (FR-98): настройки приёмки, которые хранит inbox.json, и распознавание текста на картинках
REMOVED = ("inbox_dir", "period", "llm", "cloud", "stable_seconds", "threshold", "max_gb", "max_files", "max_ratio", "depth", "vision",
           "vision_pages", "vision_minutes", "ocr_languages", "ocr_gpu", "ocr_min_conf")
# Типы, у которых после FR-98 не осталось настоящей настройки: дробное число, выбор из списка, булево с умолчанием «истина», путь с умолчанием
# от {home}, список с умолчанием. Код этих веток остаётся в модуле, и тесты гоняют его на пробных настройках, которых в схеме нет.
PROBES = {
    "probe_float": ("float", 0.45, 0.0, 1.0, None),
    "probe_choice": ("int", 30, 1, 60, (1, 5, 10, 30, 60)),
    "probe_on": ("bool", True, None, None, None),
    "probe_dir": ("path", "{home}/probe", None, None, None),
    "probe_list": ("list[str]", ["en"], None, None, None),
}
BAD_CODE = {"int": "settings.bad_int", "float": "settings.bad_number", "bool": "settings.bad_switch", "str": "settings.bad_text",
            "path": "settings.bad_path", "url": "settings.bad_url", "list[str]": "settings.bad_list", "list[path]": "settings.bad_list",
            "env_name": "settings.bad_env_name"}
SETTINGS_CODES = {code for code in M.CATALOG if code.startswith("settings.")}


# ── обвязка ─────────────────────────────────────────────────────
@pytest.fixture
def home(tmp_path):
    d = tmp_path / "arch"
    d.mkdir()
    return d


@pytest.fixture
def env(tmp_path, home):
    """Явное окружение: свой HOME (его нет на диске) и свой каталог архива; больше ничего."""
    return {"HOME": str(tmp_path / "user"), "FLYARCHIVE_HOME": str(home)}


@pytest.fixture
def probes(monkeypatch):
    """В схему на время теста добавлены пробные настройки (PROBES): их ключи читают load, окружение и файл как настоящие."""
    for key, (kind, default, low, high, choices) in PROBES.items():
        monkeypatch.setitem(S.SCHEMA, key, S.Spec(key, kind, S.FrozenList(default) if kind == "list[str]" else default, low, high, choices,
                                                  "Пробная настройка теста"))
    return tuple(PROBES)


def put(folder, data=None, mode=0o600, raw=None):
    """Файл settings.json в каталоге архива: JSON из data или сырое содержимое raw (строка или байты)."""
    p = folder / "settings.json"
    if raw is None:
        raw = json.dumps(data)
    if isinstance(raw, bytes):
        p.write_bytes(raw)
    else:
        p.write_text(raw, encoding="utf-8")
    p.chmod(mode)
    return p


def refusal(**kw):
    """Отказ load с этими аргументами; если отказа нет, тест красный."""
    with pytest.raises(S.SettingsError) as caught:
        S.load(**kw)
    return caught.value


def code_of(error):
    return error.message.code


def want(default, home, env):
    """Умолчание так, как его должен вернуть load: путь раскрыт."""
    if isinstance(default, str) and default.startswith("{home}"):
        default = str(home) + default[len("{home}"):]
    if isinstance(default, str) and default.startswith("~/"):
        default = env["HOME"] + default[1:]
    return default


def tree(path):
    """Снимок каталога: все пути и время изменения; по нему видно, что load ничего не создал и не тронул."""
    out = {}
    for base, dirs, files in os.walk(path):
        for name in dirs + files:
            full = os.path.join(base, name)
            out[os.path.relpath(full, path)] = os.stat(full, follow_symlinks=False).st_mtime_ns
    return out


@pytest.fixture
def reads(monkeypatch):
    """Чтения файла настроек: модуль читает его через os.fdopen, каждое обращение — одно чтение."""
    calls = []
    real = os.fdopen

    def spy(fd, *args, **kwargs):
        calls.append(fd)
        return real(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", spy)
    return calls


def run_program(*args, extra=None, cwd=None):
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", **(extra or {})}
    return subprocess.run([sys.executable, PROGRAM, *args], capture_output=True, text=True, encoding="utf-8", env=environment, cwd=cwd)


# ── схема: у каждой настройки тип, умолчание, границы и описание ─
def test_схема_состоит_ровно_из_настроек_описи():
    assert set(S.SCHEMA) == set(INVENTORY)


def test_у_каждой_настройки_есть_тип_умолчание_границы_и_описание_по_русски():
    assert len(S.SCHEMA) == len(INVENTORY) == 45
    for key, spec in S.SCHEMA.items():
        typ, default, low, high = INVENTORY[key]
        assert (spec.key, spec.type, spec.default, spec.low, spec.high) == (key, typ, default, low, high), key
        assert spec.type in ("str", "int", "float", "bool", "path", "url", "list[str]", "list[path]", "env_name"), key
        assert isinstance(spec.note, str) and spec.note.strip() and "\n" not in spec.note, key
        assert re.search("[а-яё]", spec.note, re.I), f"{key}: описание не по-русски"
        assert spec.choices == CHOICES.get(key), key
        if spec.type in ("int", "float"):
            assert spec.low is not None and spec.high is not None and spec.low <= spec.default <= spec.high, key
        else:
            assert spec.low is None and spec.high is None, key
        if spec.choices:
            assert spec.default in spec.choices and spec.low <= min(spec.choices) and max(spec.choices) <= spec.high, key


def test_ключи_схемы_строчные_латиницей_и_не_совпадают_с_именами_методов_объекта():
    taken = {"source", "as_dict", "keys", "items", "values", "get"}
    assert len(S.SCHEMA) >= 35
    for key in S.SCHEMA:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key
        assert key not in taken, key


def test_умолчания_проходят_собственную_проверку_и_раскрываются(env, home):
    s = S.load(env=env)
    for key, (typ, default, _, _) in INVENTORY.items():
        if key == "home":
            continue
        assert s.source(key) == "default", key
        assert s[key] == want(default, home, env), key
        assert isinstance(s[key], list) if typ.startswith("list[") else type(s[key]) is type(default), key


def test_все_умолчания_ложатся_в_файл_и_читаются_обратно_теми_же(env, home):
    put(home, {key: spec.default for key, spec in S.SCHEMA.items() if key != "home"})
    s = S.load(env=env)
    for key in S.SCHEMA:
        if key != "home":
            assert s.source(key) == "file" and s[key] == want(INVENTORY[key][1], home, env), key


def test_запасная_облачная_модель_по_умолчанию_не_задана_а_включается_не_здесь_а_в_настройках_приёмки(env):
    s = S.load(env=env)
    assert s["llm_cloud_model"] == "" and s["llm_cloud_url"] == ""
    assert "cloud" not in S.SCHEMA, "флажок запасной модели — настройка приёмки (inbox.json, команда inbox set --cloud), а не общей схемы"


def test_имя_локальной_модели_по_умолчанию_не_задано_а_не_имя_под_видеокарту(env):
    assert S.load(env=env)["llm_local_model"] == ""


def test_умолчания_не_содержат_домашних_путей_и_адресов_кроме_петли():
    homes = re.compile(r"(?i)(/home/|/Users/|/mnt/|/root\b|\b[a-z]:[\\/]|\\\\)")
    ips = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    named_hosts = re.compile(r"(?i)[a-z0-9-]\.(?:ts\.net|local|lan|internal|corp)\b")      # ~/.local/share — каталог, а не узел
    seen = 0
    for key, spec in S.SCHEMA.items():
        for value in (spec.default if isinstance(spec.default, list) else [spec.default]):
            if not isinstance(value, str):
                continue
            seen += 1
            assert not homes.search(value), f"{key}: домашний путь или диск в умолчании"
            assert not named_hosts.search(value), f"{key}: имя узла частной сети в умолчании"
            assert all(ip == "127.0.0.1" for ip in ips.findall(value)), f"{key}: чужой адрес в умолчании"
            if spec.type == "path" and value:
                assert value.startswith(("~/", "{home}/")), f"{key}: путь не от ~ и не от каталога архива"
    assert seen >= 15


def test_настройки_с_секретным_словом_в_имени_только_пути_к_файлу_и_оканчиваются_на_file():
    # правило из карточки FR-95; сама схема не знает значений ключей и токенов, только пути к файлам с ними
    secretive = [k for k in S.SCHEMA if re.search("key|token|secret|password", k)]
    assert "llm_key_file" in secretive, "правило ничего не проверяет: в схеме нет настройки с ключом"
    for key in secretive:
        if S.SCHEMA[key].type == "env_name":            # имя переменной окружения, в которой оболочка ждёт ключ, — не ключ и не путь к нему
            assert key.endswith("_env"), key
            continue
        assert S.SCHEMA[key].type == "path" and key.endswith("_file"), key


def test_имя_переменной_окружения_строится_по_ключу():
    assert S.env_name("search_port") == "FLYARCHIVE_SEARCH_PORT"
    assert S.env_name("home") == "FLYARCHIVE_HOME"
    assert S.env_name("llm_key_file") == "FLYARCHIVE_LLM_KEY_FILE"


# ── слои: умолчание < файл < окружение ──────────────────────────
@pytest.mark.parametrize("in_file, in_env, value, source", [
    (False, False, 8765, "default"), (True, False, 9001, "file"), (False, True, 9002, "env"), (True, True, 9002, "env")])
def test_слои_во_всех_сочетаниях_побеждает_самый_сильный(env, home, in_file, in_env, value, source):
    if in_file:
        put(home, {"search_port": 9001})
    if in_env:
        env["FLYARCHIVE_SEARCH_PORT"] = "9002"
    s = S.load(env=env)
    assert (s["search_port"], s.source("search_port")) == (value, source)
    assert s.search_port == value


def test_слои_по_ключам_независимы_в_одной_загрузке(env, home):
    put(home, {"search_port": 9001, "office_port": 9003, "embed_gpu": True})
    env.update({"FLYARCHIVE_OFFICE_PORT": "9004", "FLYARCHIVE_MCP_PORT": "9005"})
    s = S.load(env=env)
    got = {k: (s[k], s.source(k)) for k in ("search_port", "office_port", "mcp_port", "dsh_port", "embed_gpu")}
    assert got == {"search_port": (9001, "file"), "office_port": (9004, "env"), "mcp_port": (9005, "env"),
                   "dsh_port": (3080, "default"), "embed_gpu": (True, "file")}


def test_окружение_слабее_файла_не_бывает_файл_не_перебивает_переменную(env, home):
    put(home, {"embed_gpu": False, "mcp_timeout_s": 100, "embed_model": "из-файла"})
    env.update({"FLYARCHIVE_EMBED_GPU": "yes", "FLYARCHIVE_MCP_TIMEOUT_S": "200", "FLYARCHIVE_EMBED_MODEL": "из-окружения"})
    s = S.load(env=env)
    assert (s["embed_gpu"], s["mcp_timeout_s"], s["embed_model"]) == (True, 200, "из-окружения")
    assert {s.source(k) for k in ("embed_gpu", "mcp_timeout_s", "embed_model")} == {"env"}


def test_негодное_значение_в_файле_отказ_даже_если_ключ_перебит_переменной(env, home):
    put(home, {"search_port": "много"})
    env["FLYARCHIVE_SEARCH_PORT"] = "9002"
    e = refusal(env=env)
    assert code_of(e) == "settings.bad_int" and e.message.args["source"] == "settings.json"      # опечатка в файле не прячется за окружением


def test_пути_умолчания_строятся_от_каталога_архива_и_следуют_за_ним(env, home, tmp_path, probes):
    s = S.load(env=env)
    assert s["probe_dir"] == str(home) + "/probe"
    other = tmp_path / "other"
    s = S.load(env={**env, "FLYARCHIVE_HOME": str(other)})
    assert s["probe_dir"] == str(other) + "/probe"


def test_глобального_кэша_нет_свежая_загрузка_видит_новый_файл(env, home):
    s = S.load(env=env)
    put(home, {"search_port": 9001})
    s2 = S.load(env=env)
    assert (s["search_port"], s2["search_port"]) == (8765, 9001)           # глобального кэша нет: свежая загрузка видит новый файл


# ── каталог архива ──────────────────────────────────────────────
def test_каталог_архива_все_сочетания_переменных(tmp_path, probes):
    new, old, user = tmp_path / "new", tmp_path / "old", tmp_path / "user"
    base = {"HOME": str(user)}
    cases = [
        ({}, str(user / "flyarchive"), "default"),
        ({FOREIGN_HOME: str(old)}, str(user / "flyarchive"), "default"),                    # чужая переменная не действует: умолчание, а не окружение
        ({"FLYARCHIVE_HOME": str(new)}, str(new), "env"),
        ({"FLYARCHIVE_HOME": str(new), FOREIGN_HOME: str(old)}, str(new), "env"),           # чужая рядом со своей ничего не меняет
        ({"FLYARCHIVE_HOME": "", FOREIGN_HOME: str(old)}, str(user / "flyarchive"), "default"),       # пустая переменная — как не заданная
        ({"FLYARCHIVE_HOME": str(new), FOREIGN_HOME: ""}, str(new), "env"),
        ({"FLYARCHIVE_HOME": "", FOREIGN_HOME: ""}, str(user / "flyarchive"), "default"),
    ]
    for extra, home_dir, source in cases:
        s = S.load(env={**base, **extra})
        assert (s["home"], s.source("home")) == (home_dir, source), extra
        assert s.home == home_dir and s["probe_dir"] == home_dir + "/probe", extra


@pytest.mark.foreign_name
def test_в_модуле_нет_чужого_имени_и_ничего_из_уступок_ему():
    for name in ("LEGACY_NAME", "LEGACY_PREFIX", "LEGACY_DIR", "LEGACY_KEY_ENV", "LEGACY_HOME_ENV", "env_names", "llm_key_envs", "default_home",
                 "legacy_in_use"):
        assert not hasattr(S, name), f"{name}: в модуле настроек осталось от уступок другому имени"
    with open(MODULE, encoding="utf-8") as f:
        text = f.read()
    assert not re.search("(?i)" + re.escape(foreign.NAME), text) and foreign.KEY not in text, "модуль настроек называет чужое имя или чужую переменную ключа"
    assert S.ENV_PREFIX == "FLYARCHIVE_" and S.DEFAULT_HOME == "~/flyarchive" and S.LLM_KEY_ENV == "FLYARCHIVE_LLM_KEY"


def test_файл_настроек_берётся_из_каталога_который_выбрали_переменные(tmp_path):
    new, old = tmp_path / "new", tmp_path / "old"
    new.mkdir()
    old.mkdir()
    put(new, {"search_port": 9101})
    put(old, {"search_port": 9202})
    base = {"HOME": str(tmp_path / "user")}
    assert S.load(env={**base, "FLYARCHIVE_HOME": str(new)})["search_port"] == 9101
    assert S.load(env={**base, "FLYARCHIVE_HOME": str(new), FOREIGN_HOME: str(old)})["search_port"] == 9101
    s = S.load(env={**base, FOREIGN_HOME: str(old)})
    assert (s["search_port"], s["home"], s.source("home")) == (8765, str(tmp_path / "user" / "flyarchive"), "default"), "файл чужого каталога прочитан"


def test_аргумент_home_сильнее_переменной(env, tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    put(mine, {"search_port": 9303})
    s = S.load(env=env, home=str(mine))
    assert (s["home"], s.source("home"), s["search_port"]) == (str(mine), "arg", 9303)


def test_каталог_архива_без_файла_настроек_и_несуществующий_не_ошибка(env, tmp_path):
    s = S.load(env={**env, "FLYARCHIVE_HOME": str(tmp_path / "нет" / "такого")})
    assert s["search_port"] == 8765 and s.source("search_port") == "default"
    stub = tmp_path / "file"
    stub.write_text("это файл, а не каталог")
    assert S.load(env={**env, "FLYARCHIVE_HOME": str(stub)})["search_port"] == 8765


def test_тильда_в_каталоге_архива_раскрывается_а_относительный_и_с_нулём_отказ(env):
    assert S.load(env={**env, "FLYARCHIVE_HOME": "~/мой-архив"})["home"] == env["HOME"] + "/мой-архив"
    for bad in ("архив", "./архив", "../архив", "~нет_такого_пользователя_q7/x", "/a\x00b", "/a\nb"):
        e = refusal(env={**env, "FLYARCHIVE_HOME": bad})
        assert code_of(e) == "settings.bad_path" and e.message.args["key"] == "home" and e.message.args["source"] == "FLYARCHIVE_HOME", bad
    e = refusal(env=env, home="относительный")
    assert code_of(e) == "settings.bad_path" and e.message.args["key"] == "home"


def test_в_файле_ключа_home_быть_не_может_отказ(env, home):
    for value in ("/где-угодно", str(home), 5, None):
        put(home, {"search_port": 9001, "home": value})
        e = refusal(env=env)
        assert code_of(e) == "settings.home_in_file" and str(home / "settings.json") in str(e)


# ── окружение: разбор по типу ───────────────────────────────────
@pytest.mark.parametrize("text", ["1", "true", "on", "yes", "TRUE", "True", "ON", "On", "Yes", "YES", "yEs"])
def test_булево_из_окружения_истина(env, probes, text):
    for key, var in (("embed_gpu", "FLYARCHIVE_EMBED_GPU"), ("probe_on", "FLYARCHIVE_PROBE_ON")):       # у одного умолчание ложь, у другого истина
        assert S.load(env={**env, var: text})[key] is True, (key, text)


@pytest.mark.parametrize("text", ["0", "false", "off", "no", "FALSE", "False", "OFF", "Off", "No", "NO", "oFf"])
def test_булево_из_окружения_ложь(env, probes, text):
    for key, var in (("embed_gpu", "FLYARCHIVE_EMBED_GPU"), ("probe_on", "FLYARCHIVE_PROBE_ON")):
        assert S.load(env={**env, var: text})[key] is False, (key, text)


@pytest.mark.parametrize("text", ["2", "-1", "maybe", "", "truee", "y", "n", "да", "нет", "enabled", "1.0"])
def test_булево_из_окружения_не_из_списка_отказ(env, text):
    e = refusal(env={**env, "FLYARCHIVE_EMBED_GPU": text})
    assert code_of(e) == "settings.bad_switch" and e.message.args["key"] == "embed_gpu" and e.message.args["source"] == "FLYARCHIVE_EMBED_GPU"


@pytest.mark.parametrize("text, value", [("8800", 8800), (" 8800 ", 8800), ("+8800", 8800), ("08800", 8800), ("1", 1), ("65535", 65535)])
def test_целое_из_окружения(env, text, value):
    s = S.load(env={**env, "FLYARCHIVE_SEARCH_PORT": text})
    assert s["search_port"] == value and type(s["search_port"]) is int and s.source("search_port") == "env"


@pytest.mark.parametrize("text", ["0", "-1", "65536", "99999999999", "80.5", "8e3", "abc", "", " ", "1_000", "0x50", "١٢٣", "88 80", "9" * 40, "NaN"])
def test_целое_из_окружения_негодное_или_вне_границ_отказ(env, text):
    e = refusal(env={**env, "FLYARCHIVE_SEARCH_PORT": text})
    assert code_of(e) == "settings.bad_int" and e.message.args["key"] == "search_port"
    assert e.message.args["source"] == "FLYARCHIVE_SEARCH_PORT" and (e.message.args["low"], e.message.args["high"]) == (1, 65535)


@pytest.mark.parametrize("text, value", [("0.5", 0.5), (" 0.5 ", 0.5), (".5", 0.5), ("1", 1.0), ("0", 0.0), ("5e-1", 0.5), ("+0.25", 0.25)])
def test_число_из_окружения(env, probes, text, value):
    s = S.load(env={**env, "FLYARCHIVE_PROBE_FLOAT": text})
    assert s["probe_float"] == value and type(s["probe_float"]) is float and s.source("probe_float") == "env"


@pytest.mark.parametrize("text", ["1.01", "-0.01", "2", "nan", "NaN", "inf", "-inf", "abc", "", "0,5", "1e400", "0.5.5", "١"])
def test_число_из_окружения_негодное_или_вне_границ_отказ(env, probes, text):
    e = refusal(env={**env, "FLYARCHIVE_PROBE_FLOAT": text})
    assert code_of(e) == "settings.bad_number" and e.message.args["key"] == "probe_float"
    assert e.message.args["source"] == "FLYARCHIVE_PROBE_FLOAT"


@pytest.mark.parametrize("text, value", [
    ("a.example.com", ["a.example.com"]), ("a.example.com,b.example.com", ["a.example.com", "b.example.com"]),
    (" a , b ,, c ,", ["a", "b", "c"]), ("", []), (" , ,", []), ("один,два", ["один", "два"])])
def test_список_из_окружения_режется_по_запятой(env, text, value):
    s = S.load(env={**env, "FLYARCHIVE_PUBLIC_HOSTS": text})
    assert s["public_hosts"] == value and s.source("public_hosts") == "env"


def test_строка_из_окружения_берётся_как_есть_пустая_допустима(env):
    assert S.load(env={**env, "FLYARCHIVE_EMBED_MODEL": " модель:1b "})["embed_model"] == " модель:1b "
    s = S.load(env={**env, "FLYARCHIVE_EMBED_MODEL": ""})
    assert s["embed_model"] == "" and s.source("embed_model") == "env"


def test_управляющие_знаки_в_строке_и_списке_отказ(env):
    for bad in ("a\x01b", "a\x00b", "a\nb", "a\tb", "a\x7fb", "a\x85b"):
        assert code_of(refusal(env={**env, "FLYARCHIVE_EMBED_MODEL": bad})) == "settings.bad_text", repr(bad)
        assert code_of(refusal(env={**env, "FLYARCHIVE_PUBLIC_HOSTS": "ok," + bad})) == "settings.bad_list", repr(bad)


def test_не_строка_вместо_значения_переменной_отказ_а_не_падение(env):
    e = refusal(env={**env, "FLYARCHIVE_SEARCH_PORT": 8800})
    assert code_of(e) == "settings.bad_int"


# ── файл: типы и границы ────────────────────────────────────────
def test_границы_каждой_числовой_настройки_из_файла_и_из_окружения(env, home):
    numeric = {k: v for k, v in INVENTORY.items() if v[0] in ("int", "float")}
    assert len(numeric) >= 20
    for key, (typ, _, low, high) in numeric.items():
        bad_code = "settings.bad_choice" if key in CHOICES else BAD_CODE[typ]
        step = 1 if typ == "int" else 0.001
        var = "FLYARCHIVE_" + key.upper()
        for value in (low, high):                                                        # границы входят
            put(home, {key: value})
            assert S.load(env=env)[key] == value, (key, value, "файл")
            assert S.load(env={**env, var: str(value)})[key] == value, (key, value, "окружение")
        for value in (low - step, high + step):                                          # а соседние значения снаружи — нет
            put(home, {key: value})
            e = refusal(env=env)
            assert code_of(e) == bad_code and e.message.args["key"] == key and e.message.args["source"] == "settings.json", (key, value)
            put(home, {})                                                                # плохое значение в файле отказало бы раньше окружения
            e = refusal(env={**env, var: str(value)})
            assert code_of(e) == bad_code and e.message.args["key"] == key and e.message.args["source"] == var, (key, value)
        put(home, {})                                                                    # файл не мешает проверке окружения


def test_выбор_только_из_допустимых_значений_и_называет_их(env, home, probes):
    for value in PROBES["probe_choice"][4]:
        put(home, {"probe_choice": value})
        assert S.load(env=env)["probe_choice"] == value
    for value in (0, 2, 7, 15, 45, 61, -5):
        put(home, {"probe_choice": value})
        e = refusal(env=env)
        assert code_of(e) == "settings.bad_choice" and e.message.args["allowed"] == "1, 5, 10, 30, 60" and e.message.args["key"] == "probe_choice"
        assert code_of(refusal(env={**env, "FLYARCHIVE_PROBE_CHOICE": str(value)})) == "settings.bad_choice"
    put(home, {"probe_choice": "5"})
    assert code_of(refusal(env=env)) == "settings.bad_choice"


BAD_FILE_VALUES = {
    "int": ["8765", 8765.0, 87.65, True, False, None, [8765], {"a": 1}, "", float("nan")],
    "float": ["0.5", True, None, [0.5], {"a": 1}, "", float("nan"), float("inf")],
    "bool": [1, 0, "true", "yes", "on", None, [True], {"a": True}, ""],
    "str": [5, 1.5, True, None, ["a"], {"a": "b"}, "a\x01b", "a\nb"],
    "path": [5, True, None, ["/a"], {"a": "/b"}, "относительный/путь", "./x", "../x", "/a\x00b", "/a\nb", "/a\x1bb", "{home}x", "~нет_пользователя_q7/x"],
    "url": [5, True, None, ["http://example.com"], "example.com", "//example.com", "ftp://example.com", "file:///etc/hosts", "javascript:alert(1)",
            "http://", "http:///x", "http://:80/", "http://user:secret@example.com/", "http://example.com:99999", "http://example.com:0",
            "http://example.com:abc", "http://exa mple.com/", " http://example.com", "http://example.com\n", "http://[::1", "mailto:a@example.com"],
    "list[str]": ["a,b", "a", 5, True, None, {"a": "b"}, [1], ["a", 1], ["a", None], ["a", ["b"]], [["a"]], ["a\x01b"], ["a\nb"]],
    "list[path]": ["/a,/b", "/a", 5, True, None, {"a": "/b"}, [1], ["/a", 1], ["/a", None], ["/a", ["/b"]], [["/a"]], ["/a\x01b"], ["/a\nb"]],
    "env_name": [5, 1.5, True, None, ["A"], {"a": "b"}, "a", "Ab", "A B", "1A", "A-B", "A=B", "A\x01B", "A\nB", "ИМЯ", "A.B", "${A}", "A;B"],
}


def test_неверный_тип_в_файле_отказ_с_ключом_и_источником_для_каждой_настройки(env, home):
    checked = 0
    for key, (typ, _, _, _) in INVENTORY.items():
        if key == "home":
            continue
        for bad in BAD_FILE_VALUES[typ]:
            put(home, {key: bad})                                                        # NaN и Infinity json.dumps пишет, как их читает и json.loads
            e = refusal(env=env)
            code = "settings.bad_choice" if key in CHOICES else BAD_CODE[typ]
            assert code_of(e) == code, (key, bad)
            assert e.message.args["key"] == key and e.message.args["source"] == "settings.json", (key, bad)
            checked += 1
    assert checked >= 400


def test_пробные_настройки_всех_типов_отказывают_на_неверный_тип_в_файле(env, home, probes):
    checked = 0
    for key, (typ, _, _, _, choices) in PROBES.items():
        for bad in BAD_FILE_VALUES[typ]:
            put(home, {key: bad})
            e = refusal(env=env)
            assert code_of(e) == ("settings.bad_choice" if choices else BAD_CODE[typ]), (key, bad)
            assert e.message.args["key"] == key and e.message.args["source"] == "settings.json", (key, bad)
            checked += 1
    assert checked >= 40


def test_целое_в_числовой_настройке_с_плавающей_точкой_принимается_как_число(env, home, probes):
    put(home, {"probe_float": 1})
    s = S.load(env=env)
    assert s["probe_float"] == 1.0 and type(s["probe_float"]) is float


def test_файл_булево_только_настоящее_true_или_false(env, home, probes):
    put(home, {"embed_gpu": True, "probe_on": False})
    s = S.load(env=env)
    assert (s["embed_gpu"], s["probe_on"]) == (True, False)


def test_необязательные_путь_и_адрес_пусты_только_если_пусты_по_умолчанию(env, home):
    put(home, {"llm_key_file": "", "env_file": "", "public_url": "", "llm_cloud_url": ""})
    s = S.load(env=env)
    assert (s["llm_key_file"], s["public_url"]) == ("", "") and s.source("llm_key_file") == "file"
    for key in ("dsh_profile", "mcp_url", "embed_url"):                                  # у этих есть умолчание, «пусто» не бывает
        put(home, {key: ""})
        assert code_of(refusal(env=env)) == BAD_CODE[INVENTORY[key][0]], key
        assert code_of(refusal(env={**env, "FLYARCHIVE_" + key.upper(): ""})) == BAD_CODE[INVENTORY[key][0]], key


def test_адрес_только_http_и_https_с_узлом_порт_целый_1_65535(env, home):
    good = ["http://127.0.0.1:8080", "https://example.com/path?q=1#x", "http://[::1]:80/x", "HTTP://EXAMPLE.COM", "http://localhost",
            "http://203.0.113.7:65535/", "https://api.example.com:1/"]
    for url in good:
        put(home, {"llm_local_url": url})
        s = S.load(env=env)
        assert s["llm_local_url"] == url and s.source("llm_local_url") == "file", url
        assert S.load(env={**env, "FLYARCHIVE_LLM_LOCAL_URL": url})["llm_local_url"] == url
    for url in BAD_FILE_VALUES["url"]:
        if isinstance(url, str):
            assert code_of(refusal(env={**env, "FLYARCHIVE_LLM_LOCAL_URL": url})) == "settings.bad_url", url


def test_список_в_файле_только_список_строк_и_отдаётся_как_есть(env, home):
    put(home, {"env_names": ["RU", "EN"], "hosts": [], "public_hosts": ["a.example.com"]})
    s = S.load(env=env)
    assert (s["env_names"], s["hosts"], s["public_hosts"]) == (["RU", "EN"], [], ["a.example.com"])


# ── пути ────────────────────────────────────────────────────────
def test_тильда_и_подстановка_home_раскрываются_в_файле_и_в_окружении(env, home):
    put(home, {"dsh_profile": "~/свой-профиль", "sandbox_dir": "{home}/своя-песочница", "llm_key_file": "~/.keys.d/file"})
    env["FLYARCHIVE_ENV_FILE"] = "{home}/вход/ящик"
    env["FLYARCHIVE_UNITS_DIR"] = "~/units"
    s = S.load(env=env)
    assert s["dsh_profile"] == env["HOME"] + "/свой-профиль" and s["sandbox_dir"] == str(home) + "/своя-песочница"
    assert s["env_file"] == str(home) + "/вход/ящик" and s["units_dir"] == env["HOME"] + "/units" and s["llm_key_file"] == env["HOME"] + "/.keys.d/file"
    s = S.load(env={**env, "FLYARCHIVE_DSH_PROFILE": "~", "FLYARCHIVE_SANDBOX_DIR": "{home}"})
    assert (s["dsh_profile"], s["sandbox_dir"]) == (env["HOME"], str(home))


def test_тильда_берётся_из_HOME_переданного_окружения_а_не_из_процесса(env, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "чужой-процесс"))
    s = S.load(env=env)
    assert s["dsh_profile"] == env["HOME"] + "/.dsh/profiles/web" and s["units_dir"] == env["HOME"] + "/.config/systemd/user"


def test_подстановка_home_видит_только_начало_пути(env, home):
    env["FLYARCHIVE_SANDBOX_DIR"] = "/srv/{home}/x"
    assert S.load(env=env)["sandbox_dir"] == "/srv/{home}/x"                             # посередине это просто знаки пути


def test_путь_обязан_быть_абсолютным_после_раскрытия_отказ(env, home):
    for bad in BAD_FILE_VALUES["path"]:
        if isinstance(bad, str):
            put(home, {"sandbox_dir": bad})
            e = refusal(env=env)
            assert code_of(e) == "settings.bad_path" and e.message.args["key"] == "sandbox_dir", repr(bad)
            put(home, {})
            e = refusal(env={**env, "FLYARCHIVE_SANDBOX_DIR": bad})
            assert code_of(e) == "settings.bad_path" and e.message.args["source"] == "FLYARCHIVE_SANDBOX_DIR", repr(bad)


def test_умолчание_с_тильдой_при_относительном_HOME_отказ_с_источником_default(env):
    e = refusal(env={"HOME": "относительный", "FLYARCHIVE_HOME": env["FLYARCHIVE_HOME"]})
    assert code_of(e) == "settings.bad_path" and e.message.args["source"] == "default"


def test_путь_с_точками_после_раскрытия_остаётся_абсолютным_и_допустим(env, home):
    put(home, {"dsh_profile": "/srv/../данные/профиль", "sandbox_dir": "{home}/../соседний", "env_file": "~/a/../b"})
    s = S.load(env=env)
    assert s["dsh_profile"] == "/srv/../данные/профиль" and s["sandbox_dir"] == str(home) + "/../соседний" and s["env_file"] == env["HOME"] + "/a/../b"


def test_относительный_путь_и_путь_с_нулевым_байтом_нельзя_а_с_точками_можно(env):
    assert S.load(env={**env, "FLYARCHIVE_UNITS_DIR": "/a/../b"})["units_dir"] == "/a/../b"
    assert code_of(refusal(env={**env, "FLYARCHIVE_UNITS_DIR": "a/../b"})) == "settings.bad_path"
    assert code_of(refusal(env={**env, "FLYARCHIVE_UNITS_DIR": "/a/\x00/b"})) == "settings.bad_path"


# ── файл: ошибки чтения и разбора ───────────────────────────────
def test_файла_нет_не_ошибка_действуют_умолчания_и_окружение(env, home):
    assert not (home / "settings.json").exists()
    env["FLYARCHIVE_DSH_PORT"] = "3999"
    s = S.load(env=env)
    assert (s["dsh_port"], s.source("dsh_port"), s.source("search_port")) == (3999, "env", "default")


def test_битый_json_отказ_с_местом_ошибки(env, home):
    put(home, raw='{"search_port": 1,\n  "office_port": }')
    e = refusal(env=env)
    assert code_of(e) == "settings.bad_json" and e.message.args["line"] == 2 and e.message.args["column"] >= 1
    assert e.message.args["path"] == str(home / "settings.json")
    for broken in ("", "   ", "{", "[1,", "{'a': 1}", "{\"a\": 1,}", "{\"a\" 1}", "nul", "1" * 5000):
        put(home, raw=broken)
        assert code_of(refusal(env=env)) == "settings.bad_json", repr(broken)


def test_глубокая_вложенность_отказ_а_не_падение_интерпретатора(env, home):
    put(home, raw="[" * 100_000)
    assert code_of(refusal(env=env)) == "settings.bad_json"


@pytest.mark.parametrize("raw", ["[]", "[1, 2]", '"строка"', "5", "null", "true", "1.5"])
def test_файл_не_объект_отказ(env, home, raw):
    put(home, raw=raw)
    e = refusal(env=env)
    assert code_of(e) == "settings.not_object" and e.message.args["path"] == str(home / "settings.json")


def test_неизвестный_ключ_в_файле_отказ_с_названием_ключа_опечатка_не_проходит(env, home):
    for typo in ("serch_port", "Search_Port", "search-port", "searchport", "cloud ", "FLYARCHIVE_SEARCH_PORT", "inbox", "bind"):
        put(home, {"search_port": 9001, typo: 1})
        e = refusal(env=env)
        assert code_of(e) == "settings.unknown_key" and e.message.args["key"] == typo and typo in str(e), typo


def test_неизвестный_ключ_называется_безопасно_длинный_обрезается_знаки_управления_заменяются(env, home):
    put(home, {"x" * 500: 1})
    shown = refusal(env=env).message.args["key"]
    assert len(shown) <= 70 and shown.startswith("x" * 60)
    put(home, {"a\nb\x1b[31mc": 1})
    e = refusal(env=env)
    assert "\n" not in str(e) and "\x1b" not in str(e) and "\n" not in e.message.args["key"] and "\x1b" not in e.message.args["key"]


def test_файл_не_в_utf8_и_файл_слишком_большой_отказ(env, home):
    put(home, raw=b'{"embed_model": "\xff\xfe"}')
    e = refusal(env=env)
    assert code_of(e) == "settings.file_unreadable" and e.message.args["error_type"] == "UnicodeDecodeError"
    put(home, raw='{"embed_model": "' + "x" * (S.MAX_FILE_BYTES + 1) + '"}')
    e = refusal(env=env)
    assert code_of(e) == "settings.file_unreadable" and e.message.args["error_type"] == "TooLarge"


def test_файл_с_BOM_читается_как_обычный(env, home):
    put(home, raw=b"\xef\xbb\xbf" + json.dumps({"search_port": 9001}).encode("utf-8"))
    assert S.load(env=env)["search_port"] == 9001


def test_каталог_вместо_файла_и_именованный_канал_отказ_без_зависания(env, home):
    (home / "settings.json").mkdir()
    e = refusal(env=env)
    assert code_of(e) == "settings.file_unreadable" and e.message.args["error_type"] == "NotAFile"
    (home / "settings.json").rmdir()
    os.mkfifo(home / "settings.json", 0o600)

    def hung(*_):
        raise TimeoutError("load завис на именованном канале")

    old = signal.signal(signal.SIGALRM, hung)
    signal.alarm(10)
    try:
        e = refusal(env=env)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert code_of(e) == "settings.file_unreadable" and e.message.args["error_type"] == "NotAFile"


# ── права на файл настроек ──────────────────────────────────────
@pytest.mark.parametrize("mode", [0o620, 0o660, 0o602, 0o606, 0o622, 0o664, 0o666, 0o770, 0o777])
def test_файл_с_записью_для_группы_или_остальных_не_читается_отказ(env, home, reads, mode):
    put(home, {"search_port": 9001}, mode=mode)
    e = refusal(env=env)
    assert code_of(e) == "settings.file_open" and e.message.args["path"] == str(home / "settings.json")
    assert reads == [], "содержимое файла прочитано до проверки прав"


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644, 0o400, 0o440, 0o444, 0o604])
def test_файл_без_записи_для_других_читается(env, home, reads, mode):
    put(home, {"search_port": 9001}, mode=mode)
    assert S.load(env=env)["search_port"] == 9001 and len(reads) == 1


def test_файл_чужого_владельца_не_читается_отказ(env, home, reads, monkeypatch):
    put(home, {"search_port": 9001}, mode=0o600)
    monkeypatch.setattr(S, "_owner_uid", lambda: os.geteuid() + 1)
    e = refusal(env=env)
    assert code_of(e) == "settings.file_open" and reads == []


def test_ссылка_на_открытый_файл_не_обходит_проверку_проверяется_сам_файл(env, home, tmp_path, reads):
    elsewhere = tmp_path / "где-то-ещё"
    elsewhere.mkdir()
    target = elsewhere / "настройки.json"
    target.write_text(json.dumps({"search_port": 9001}))
    target.chmod(0o666)
    os.symlink(target, home / "settings.json")
    assert code_of(refusal(env=env)) == "settings.file_open" and reads == []
    target.chmod(0o600)                                                                    # собственные права ссылки (777) не в счёт
    assert S.load(env=env)["search_port"] == 9001


def test_ссылка_на_файл_чужого_владельца_не_обходит_проверку(env, home, tmp_path, monkeypatch):
    target = tmp_path / "чужой.json"
    target.write_text(json.dumps({"search_port": 9001}))
    target.chmod(0o600)
    os.symlink(target, home / "settings.json")
    assert S.load(env=env)["search_port"] == 9001
    monkeypatch.setattr(S, "_owner_uid", lambda: os.geteuid() + 1)
    assert code_of(refusal(env=env)) == "settings.file_open"


def test_проверка_прав_только_там_где_они_есть_не_на_posix_пропускается(env, home, monkeypatch):
    put(home, {"search_port": 9001}, mode=0o666)
    monkeypatch.setattr(S, "POSIX", False)
    assert S.load(env=env)["search_port"] == 9001


def test_отказ_по_правам_на_файл_виден_и_в_выводе_программы(env, home, capsys):
    put(home, {"search_port": 9001}, mode=0o666)
    assert S.main(["--json"], env=env) == 1
    out = capsys.readouterr()
    assert out.out == "" and json.loads(out.err.strip().splitlines()[-1])["error"]["code"] == "settings.file_open"


# ── отказы: значение в текст не попадает ────────────────────────
LEAKING = {
    "int": [LEAK, [LEAK], {"x": LEAK}, LEAK + "1"],
    "float": [LEAK, [LEAK], {"x": LEAK}],
    "bool": [LEAK, [LEAK], {"x": LEAK}],
    "str": [[LEAK], {"x": LEAK}, LEAK + "\x01"],
    "path": [LEAK + "/относительный", LEAK + "\x00", [LEAK], {"x": LEAK}, "{home}" + LEAK, "~" + LEAK + "/x"],
    "url": [LEAK + "://x", "ftp://" + LEAK, "http://" + LEAK + ":secret@example.com/", "http://example.com:" + LEAK, [LEAK], {"x": LEAK}],
    "list[str]": [[LEAK, 1], [[LEAK]], {"x": LEAK}, LEAK, [LEAK + "\x01"]],
    "list[path]": [[LEAK, 1], [[LEAK]], {"x": LEAK}, LEAK, [LEAK + "\x01"], [LEAK + "/относительный"], ["{home}" + LEAK]],
    "env_name": [LEAK + "-не-имя", LEAK.lower(), [LEAK], {"x": LEAK}, LEAK + "\x01", "A B " + LEAK],
}


def seen_everywhere(error):
    message = error.message
    return [str(error), repr(error), json.dumps(message.to_json(), ensure_ascii=False), repr(message.args), str(message), repr(error.args)]


def test_значение_из_файла_не_попадает_в_текст_отказа_для_всех_настроек_и_типов(env, home):
    checked = 0
    for key, (typ, _, _, _) in INVENTORY.items():
        if key == "home":
            continue
        for bad in LEAKING[typ]:
            put(home, {key: bad})
            e = refusal(env=env)
            assert all(LEAK not in text for text in seen_everywhere(e)), (key, bad)
            checked += 1
    assert checked >= 150


def test_значение_из_переменной_не_попадает_в_текст_отказа_для_всех_настроек_и_типов(env):
    env_bad = {"int": [LEAK, "1" + LEAK], "float": [LEAK, "0.5" + LEAK], "bool": [LEAK, "yes" + LEAK], "str": ["a\x01" + LEAK],
               "path": [LEAK + "/относительный", LEAK + "\x00"], "url": [LEAK + "://x", "ftp://" + LEAK, "http://" + LEAK + ":x@example.com/"],
               "list[str]": ["ok,a\x01" + LEAK], "list[path]": ["/ok,a\x01" + LEAK, LEAK + "/относительный"], "env_name": [LEAK + "-не-имя", "A B " + LEAK]}
    checked = 0
    for key, (typ, _, _, _) in INVENTORY.items():
        for bad in env_bad[typ]:
            e = refusal(env={**env, "FLYARCHIVE_" + key.upper(): bad})
            assert all(LEAK not in text for text in seen_everywhere(e)), (key, bad)
            checked += 1
    assert checked >= 60


def test_значение_не_попадает_в_отказ_при_битом_json_неизвестном_ключе_и_лишнем_home(env, home):
    put(home, raw='{"search_port": "' + LEAK + '", ')
    assert all(LEAK not in t for t in seen_everywhere(refusal(env=env)))
    put(home, {"nope": LEAK})
    assert all(LEAK not in t for t in seen_everywhere(refusal(env=env)))
    put(home, {"home": LEAK})
    assert all(LEAK not in t for t in seen_everywhere(refusal(env=env)))
    put(home, raw='["' + LEAK + '"]')
    assert all(LEAK not in t for t in seen_everywhere(refusal(env=env)))
    put(home, raw=b'{"embed_model": "' + LEAK.encode() + b'\xff"}')
    e = refusal(env=env)
    assert code_of(e) == "settings.file_unreadable" and all(LEAK not in t for t in seen_everywhere(e))


def test_значение_не_попадает_в_вывод_программы_при_отказе_ни_текстом_ни_json(env, home, capsys):
    for data in ({"search_port": LEAK}, {"dsh_profile": LEAK + "/x"}, {"public_url": "ftp://" + LEAK}, {"hosts": [LEAK, 1]}):
        put(home, data)
        for args in ([], ["--json"]):
            assert S.main(args, env=env) == 1
            out = capsys.readouterr()
            assert LEAK not in out.out and LEAK not in out.err, (data, args)


def test_отказ_для_пути_к_секрету_называет_ключ_и_источник_а_не_путь(env, home):
    put(home, {"llm_key_file": LEAK + "/относительный"})
    e = refusal(env=env)
    assert code_of(e) == "settings.bad_path" and e.message.args == {"key": "llm_key_file", "source": "settings.json"}
    assert LEAK not in str(e)


def test_путь_к_файлу_с_ключом_показывается_путём_а_файл_модуль_не_открывает(env, home, tmp_path, monkeypatch, capsys):
    key_file = tmp_path / "ключ.txt"
    key_file.write_text("СОДЕРЖИМОЕ-" + LEAK)
    key_file.chmod(0o600)
    put(home, {"llm_key_file": str(key_file), "env_file": str(tmp_path / "нет-такого.env")})
    opened = []
    real_open, real_os_open = open, os.open
    with monkeypatch.context() as m:
        m.setattr("builtins.open", lambda path, *a, **k: (opened.append(os.fspath(path)), real_open(path, *a, **k))[1])
        m.setattr(os, "open", lambda path, *a, **k: (opened.append(os.fspath(path)), real_os_open(path, *a, **k))[1])
        s = S.load(env=env)
        assert S.main([], env=env) == 0
    assert s["llm_key_file"] == str(key_file) and s["env_file"] == str(tmp_path / "нет-такого.env")
    assert set(opened) == {str(home / "settings.json")}, "модуль открыл что-то кроме файла настроек"
    shown = capsys.readouterr().out
    assert str(key_file) in shown and LEAK not in shown and "СОДЕРЖИМОЕ" not in shown


# ── объект настроек ─────────────────────────────────────────────
def test_значение_по_ключу_и_по_атрибуту_и_as_dict(env, home):
    put(home, {"search_port": 9001, "env_names": ["RU", "EN"]})
    s = S.load(env=env)
    assert s["search_port"] == s.search_port == 9001 and s.env_names == s["env_names"] == ["RU", "EN"]
    d = s.as_dict()
    assert set(d) == set(S.SCHEMA) and d["search_port"] == 9001 and d["home"] == str(home)
    assert all(d[k] == s[k] for k in S.SCHEMA)
    assert "search_port" in s and "нет_такого" not in s and len(s) == len(S.SCHEMA) and set(s) == set(S.SCHEMA)


def test_объект_неизменяем_и_as_dict_это_копия(env):
    s = S.load(env=env)
    with pytest.raises(AttributeError):
        s.search_port = 1
    with pytest.raises(AttributeError):
        s.новая = 1
    with pytest.raises(AttributeError):
        del s.search_port
    with pytest.raises(TypeError):
        s["search_port"] = 1
    with pytest.raises(TypeError):
        del s["search_port"]
    with pytest.raises(TypeError):
        s["hosts"].append("ru")
    with pytest.raises(TypeError):
        s["hosts"][0] = "ru"
    assert s["hosts"] == [] and s.search_port == 8765
    d = s.as_dict()
    d["search_port"] = 1
    d["hosts"].append("ru")
    assert s["search_port"] == 8765 and s["hosts"] == [] and s.as_dict()["hosts"] == []
    assert type(s.as_dict()["hosts"]) is list and json.dumps(s.as_dict())


def test_неизменяемый_объект_копированию_не_мешает(env):
    s = S.load(env=env)
    assert copy.copy(s) is s and copy.deepcopy(s) is s and copy.deepcopy({"s": s})["s"] is s
    assert "Settings" in repr(s) and s["home"] in repr(s)


def test_неизвестный_ключ_у_объекта_ошибка_обращения(env):
    s = S.load(env=env)
    with pytest.raises(KeyError):
        s["нет_такого"]
    with pytest.raises(AttributeError):
        s.нет_такого
    with pytest.raises(KeyError):
        s.source("нет_такого")


def test_источник_значения_одно_из_трёх_слов(env, home):
    put(home, {"search_port": 9001})
    env["FLYARCHIVE_OFFICE_PORT"] = "9002"
    s = S.load(env=env)
    assert {s.source(k) for k in S.SCHEMA} == {"default", "file", "env"}
    assert (s.source("search_port"), s.source("office_port"), s.source("mcp_port"), s.source("home")) == ("file", "env", "default", "env")


def test_load_без_аргументов_читает_окружение_процесса_а_load_с_env_его_не_читает(env, home, monkeypatch):
    monkeypatch.setenv("FLYARCHIVE_HOME", str(home))
    monkeypatch.setenv("FLYARCHIVE_SEARCH_PORT", "9100")
    assert S.load()["search_port"] == 9100
    assert S.load(env=env)["search_port"] == 8765


# ── load ничего не пишет ────────────────────────────────────────
def test_load_не_создаёт_каталогов_и_файлов_ни_без_каталога_архива_ни_с_ним(env, home, tmp_path):
    missing = tmp_path / "нет" / "такого" / "архива"
    before = tree(tmp_path)
    S.load(env={**env, "FLYARCHIVE_HOME": str(missing)})
    S.load(env={**env, "FLYARCHIVE_HOME": str(missing), "FLYARCHIVE_SANDBOX_DIR": "{home}/песочница", "FLYARCHIVE_UNITS_DIR": str(tmp_path / "ещё" / "нет")})
    assert tree(tmp_path) == before and not missing.exists() and not (tmp_path / "нет").exists()
    put(home, {"search_port": 9001, "units_dir": "{home}/новый/каталог"})
    before = tree(tmp_path)
    S.load(env=env)
    S.main([], env=env)
    S.main(["--json"], env=env)
    assert tree(tmp_path) == before and not (home / "новый").exists()
    for entry in os.listdir(home):
        assert entry == "settings.json", entry


def test_load_не_пишет_через_системные_вызовы_записи_и_создания(env, home, tmp_path, monkeypatch):
    put(home, {"search_port": 9001})
    real_open = os.open

    def checked_open(path, flags, *a, **k):
        assert flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) == 0, "load открыл файл на запись"
        return real_open(path, flags, *a, **k)

    with monkeypatch.context() as m:
        for name in ("mkdir", "makedirs", "rename", "replace", "remove", "unlink", "rmdir", "symlink", "chmod", "utime"):
            m.setattr(os, name, lambda *a, _n=name, **k: pytest.fail(f"load вызвал os.{_n}"))
        m.setattr(os, "open", checked_open)
        assert S.load(env=env)["search_port"] == 9001


def test_load_не_меняет_окружение_ни_переданное_ни_процесса(env, home, monkeypatch):
    put(home, {"search_port": 9001})
    env["FLYARCHIVE_NO_SUCH_THING"] = "1"
    given = copy.deepcopy(env)
    process = dict(os.environ)
    S.load(env=env)
    S.load(env=env, home=str(home))
    assert env == given and dict(os.environ) == process
    monkeypatch.setenv("FLYARCHIVE_HOME", str(home))
    process = dict(os.environ)
    S.load()
    assert dict(os.environ) == process


def test_файл_читается_один_раз_на_вызов_load_и_без_файла_не_читается_вовсе(env, home, reads):
    S.load(env=env)
    assert reads == []
    put(home, {"search_port": 9001})
    S.load(env=env)
    assert len(reads) == 1
    S.load(env=env)
    assert len(reads) == 2


# ── неизвестные переменные окружения ────────────────────────────
def test_неизвестная_переменная_FLYARCHIVE_не_отказ_но_названа_неиспользуемой(env):
    env.update({"FLYARCHIVE_NO_SUCH_THING": "1", "FLYARCHIVE_SERCH_PORT": "9", "OTHER_PROGRAM_SETTING": "x", "FLYARCHIVE_LLM_KEY": "значение"})
    S.load(env=env)
    assert S.unused_env(env) == ["FLYARCHIVE_NO_SUCH_THING", "FLYARCHIVE_SERCH_PORT"]       # чужие и читаемые другими программами — не в счёт
    assert S.unused_env({"HOME": "/x", "FLYARCHIVE_HOME": "/y", "FLYARCHIVE_SEARCH_PORT": "1"}) == []


def test_неиспользуемые_переменные_названы_в_выводе_программы_а_в_json_их_нет(env, capsys):
    env.update({"FLYARCHIVE_NO_SUCH_THING": "1", "FLYARCHIVE_LLM_KEY": "x"})
    assert S.main([], env=env) == 0
    out = capsys.readouterr().out
    line = next(line for line in out.splitlines() if "не используются" in line)
    assert "FLYARCHIVE_NO_SUCH_THING" in line and "FLYARCHIVE_LLM_KEY" not in out
    assert S.main(["--json"], env=env) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == set(S.SCHEMA)


# ── программа ───────────────────────────────────────────────────
def test_программа_печатает_действующие_значения_и_источник_каждого(env, home, capsys):
    put(home, {"search_port": 9001})
    env["FLYARCHIVE_OFFICE_PORT"] = "9002"
    assert S.main([], env=env) == 0
    out = capsys.readouterr()
    assert out.err == ""
    rows = {parts[0]: parts for parts in (line.split() for line in out.out.splitlines()) if parts and parts[0] in S.SCHEMA}
    assert set(rows) == set(S.SCHEMA)
    assert rows["search_port"][1:3] == ["file", "9001"] and rows["office_port"][1:3] == ["env", "9002"] and rows["mcp_port"][1:3] == ["default", "8767"]
    assert rows["home"][1:3] == ["env", str(home)] and rows["embed_gpu"][1:3] == ["default", "false"] and rows["api_max_k"][1:3] == ["default", "20"]
    assert rows["hosts"][1:] == ["default", "[]"]


def test_программа_с_json_печатает_объект_ключ_значение_источник(env, home, capsys):
    put(home, {"search_port": 9001, "env_names": ["RU"]})
    env["FLYARCHIVE_OFFICE_PORT"] = "9002"
    assert S.main(["--json"], env=env) == 0
    out = capsys.readouterr()
    assert out.err == "" and len(out.out.strip().splitlines()) >= 1
    data = json.loads(out.out)
    assert set(data) == set(S.SCHEMA), "в json ничего кроме ключей схемы"
    assert all(set(v) == {"value", "source"} and v["source"] in ("default", "file", "env") for v in data.values())
    assert data["search_port"] == {"value": 9001, "source": "file"} and data["office_port"] == {"value": 9002, "source": "env"}
    assert data["env_names"] == {"value": ["RU"], "source": "file"} and data["home"] == {"value": str(home), "source": "env"}
    s = S.load(env=env)
    assert {k: v["value"] for k, v in data.items()} == s.as_dict()


def test_программа_как_отдельный_процесс_и_кириллица_в_значениях(env, home):
    put(home, {"embed_model": "модель-№1", "env_names": ["RU", "EN"]})
    r = run_program("--json", extra=env, cwd=str(home))
    assert r.returncode == 0 and r.stderr == ""
    data = json.loads(r.stdout)
    assert data["embed_model"] == {"value": "модель-№1", "source": "file"} and set(data) == set(S.SCHEMA)
    r = run_program(extra=env)
    assert r.returncode == 0 and "модель-№1" in r.stdout and r.stderr == ""


def test_справочник_по_одной_строке_на_настройку_ключ_тип_умолчание_границы_описание_переменная(capsys):
    assert S.main(["--reference"], env={}) == 0
    out = capsys.readouterr()
    assert out.err == ""
    lines = [line for line in out.out.strip().splitlines() if line.startswith("|")]          # таблица; пояснение о настройках приёмки — вне неё
    assert lines[0].startswith("|") and lines[1].replace("|", "").replace("-", "").replace(" ", "") == ""
    rows = [line for line in lines[2:] if line.startswith("| `")]
    assert len(rows) == len(lines) - 2 == len(S.SCHEMA)
    keys = [re.match(r"\| `([a-z0-9_]+)`", line).group(1) for line in rows]
    assert sorted(keys) == sorted(S.SCHEMA) and len(set(keys)) == len(keys), "каждая настройка ровно один раз"
    for line in rows:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 6, line
        key, typ, default, bounds, note, var = cells
        key = key.strip("`")
        spec = S.SCHEMA[key]
        assert typ == spec.type and note == spec.note and var == "`" + S.env_name(key) + "`", key
    by_key = dict(zip(keys, rows))
    assert "8765" in by_key["search_port"] and "65535" in by_key["search_port"] and "FLYARCHIVE_SEARCH_PORT" in by_key["search_port"]
    assert "от 1 до 100" in by_key["api_max_k"] and "6000" in by_key["api_budget_chars"] and "false" in by_key["embed_gpu"]
    assert "~/.config/systemd/user" in by_key["units_dir"] and "FLYARCHIVE_HOME" in by_key["home"]


def test_справочник_не_зависит_от_окружения_и_файла_настроек(env, home, capsys):
    S.main(["--reference"], env={})
    plain = capsys.readouterr().out
    put(home, raw="{ это не json")
    env["FLYARCHIVE_SEARCH_PORT"] = "1234"
    assert S.main(["--reference"], env=env) == 0
    assert capsys.readouterr().out == plain and "1234" not in plain


def test_справочник_как_отдельный_процесс_код_0_и_без_лишнего_вывода():
    r = run_program("--reference")
    assert r.returncode == 0 and r.stderr == "" and r.stdout.startswith("| ")


@pytest.mark.foreign_name
def test_справочник_не_называет_чужого_имени():
    r = run_program("--reference")
    assert r.returncode == 0 and len(r.stdout.splitlines()) > 40
    assert not re.search("(?i)" + re.escape(foreign.NAME), r.stdout)


def test_ключи_json_и_справочник_вместе_не_принимаются(env, capsys):
    with pytest.raises(SystemExit) as e:
        S.main(["--json", "--reference"], env=env)
    assert e.value.code == 2


def test_отказ_программы_код_1_сообщение_в_stderr_ничего_в_stdout(env, home, capsys):
    put(home, {"serch_port": 1})
    assert S.main([], env=env) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err.startswith("ошибка: ") and "serch_port" in out.err


def test_отказ_программы_с_json_последняя_строка_stderr_объект_error_с_кодом_параметрами_и_текстом(env, home, capsys):
    put(home, {"search_port": "много"})
    assert S.main(["--json"], env=env) == 1
    out = capsys.readouterr()
    assert out.out == ""
    last = json.loads(out.err.strip().splitlines()[-1])
    assert set(last) == {"error"} and set(last["error"]) == {"code", "args", "text"}
    assert last["error"]["code"] == "settings.bad_int" and last["error"]["args"]["key"] == "search_port"
    assert last["error"]["text"] == str(M.make("settings.bad_int", **last["error"]["args"]))


def test_отказ_программы_как_отдельный_процесс(env, home):
    put(home, {"search_port": 0})
    r = run_program(extra=env)
    assert r.returncode == 1 and r.stdout == "" and "search_port" in r.stderr
    r = run_program("--json", extra=env)
    assert r.returncode == 1 and r.stdout == "" and json.loads(r.stderr.strip().splitlines()[-1])["error"]["code"] == "settings.bad_int"


# ── отказы: коды каталога ───────────────────────────────────────
def test_отказ_это_SettingsError_и_CodedError_с_сообщением_из_каталога(env, home):
    assert issubclass(S.SettingsError, M.CodedError)
    put(home, {"search_port": 0})
    e = refusal(env=env)
    assert isinstance(e, M.CodedError) and isinstance(e.message, M.Message) and str(e) == str(e.message)
    assert e.message.code in M.CATALOG and e.message.to_json()["args"] == e.message.args


def test_каждый_код_settings_достижим_отказом_load_и_других_отказов_нет(env, home, tmp_path, probes):
    def set_file(data=None, raw=None, mode=0o600):
        put(home, data, mode=mode, raw=raw)

    scenarios = {
        "settings.unknown_key": lambda: set_file({"нет_такой": 1}),
        "settings.home_in_file": lambda: set_file({"home": "/x"}),
        "settings.file_open": lambda: set_file({}, mode=0o666),
        "settings.file_unreadable": lambda: set_file(raw=b"\xff\xfe"),
        "settings.bad_json": lambda: set_file(raw="{"),
        "settings.not_object": lambda: set_file(raw="[]"),
        "settings.bad_int": lambda: set_file({"search_port": 0}),
        "settings.bad_number": lambda: set_file({"probe_float": 2}),
        "settings.bad_choice": lambda: set_file({"probe_choice": 7}),
        "settings.bad_switch": lambda: set_file({"embed_gpu": "да"}),
        "settings.bad_text": lambda: set_file({"embed_model": 5}),
        "settings.bad_list": lambda: set_file({"hosts": "a,b"}),
        "settings.bad_path": lambda: set_file({"sandbox_dir": "относительный"}),
        "settings.bad_url": lambda: set_file({"mcp_url": "ftp://example.com"}),
        "settings.bad_env_name": lambda: set_file({"dsh_llm_key_env": "не имя"}),
    }
    assert set(scenarios) == SETTINGS_CODES, "новый код каталога без сценария отказа (или сценарий без кода)"
    for code, make in scenarios.items():
        make()
        e = refusal(env=env)
        assert code_of(e) == code and set(e.message.args) == set(M.CATALOG[code][1]), code


def test_каждый_отказ_load_собран_из_каталога_а_не_из_голой_строки(env, home):
    for data in ({"нет_такой": 1}, {"search_port": 0}, {"embed_gpu": "да"}, {"sandbox_dir": "а"}):
        put(home, data)
        e = refusal(env=env)
        assert e.args and isinstance(e.args[0], M.Message) and e.message.code.startswith("settings.")


# ── пример settings.example.json ────────────────────────────────
def test_пример_лежит_в_корне_репозитория_и_это_чистый_json_без_комментариев():
    with open(EXAMPLE, encoding="utf-8") as f:
        text = f.read()
    seen = []

    def no_duplicates(pairs):
        names = [k for k, _ in pairs]
        assert len(names) == len(set(names)), "ключ повторён"
        seen.append(names)
        return dict(pairs)

    data = json.loads(text, object_pairs_hook=no_duplicates)
    assert isinstance(data, dict) and not text.startswith("\ufeff") and text.endswith("\n")
    assert not re.search(r"^\s*(//|#|/\*)", text, re.M), "в примере комментарии"


def test_пример_содержит_каждую_настройку_схемы_кроме_home_и_ничего_лишнего():
    with open(EXAMPLE, encoding="utf-8") as f:
        data = json.load(f)
    assert set(data) == set(S.SCHEMA) - {"home"}
    assert "home" not in data


def test_пример_загружается_как_файл_настроек_и_значения_проходят_проверку(env, home):
    with open(EXAMPLE, encoding="utf-8") as f:
        text = f.read()
    put(home, raw=text)
    s = S.load(env=env)
    data = json.loads(text)
    for key, value in data.items():
        assert s.source(key) == "file", key
        if S.SCHEMA[key].type not in ("path", "list[path]"):
            assert s[key] == value, key


def test_в_примере_нет_настроек_приёмки_и_распознавания_и_значения_выдуманные_или_умолчания():
    with open(EXAMPLE, encoding="utf-8") as f:
        data = json.load(f)
    assert not set(REMOVED) & set(data), "настройки приёмки лежат в inbox.json, распознавание — сценарии владельца: в примере им не место"
    allowed_hosts = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\]|203\.0\.113\.\d{1,3}|([a-z0-9-]+\.)*example\.com)$")
    for key, value in data.items():
        spec = S.SCHEMA[key]
        for item in (value if isinstance(value, list) else [value]):
            if not isinstance(item, str):
                continue
            if spec.type == "url" and item:
                host = re.match(r"https?://(\[[^\]]+\]|[^/:]+)", item).group(1)
                assert allowed_hosts.match(host), f"{key}: адрес не из диапазона для документации и не example.com"
            if spec.type in ("path", "list[path]") and item:
                assert item == spec.default or item.startswith(("~/flyarchive", "{home}")), f"{key}: путь не от ~/flyarchive"
            if key in ("hosts", "public_hosts", "gateway_bind"):
                assert item == "" or allowed_hosts.match(item), key
            assert not re.search(r"(?i)(" + re.escape(HOME_DIR) + r"|/Users/|C:\\)", item), key


def workdir(tmp_path, files, words=DUMMY_WORDS):
    root = tmp_path / "snap"
    root.mkdir(exist_ok=True)
    for name, text in {".gitignore": GITIGNORE, **files}.items():
        (root / name).write_text(text, encoding="utf-8")
    (tmp_path / "words.txt").write_text("\n".join(words) + "\n", encoding="utf-8")
    return G.scan(root=str(root), words_path=str(tmp_path / "words.txt"))


def test_пример_проходит_ворота_публикации_ноль_находок(tmp_path):
    with open(EXAMPLE, encoding="utf-8") as f:
        text = f.read()
    rep = workdir(tmp_path, {"settings.example.json": text})
    assert rep.findings == [] and rep.files_checked == 2


def test_ворота_на_каталоге_с_примером_живые_домашний_путь_и_чужой_адрес_в_нём_находятся(tmp_path):
    with open(EXAMPLE, encoding="utf-8") as f:
        data = json.load(f)
    data["sandbox_dir"] = HOME_DIR + "ivan/sandbox"
    data["llm_local_url"] = "http://" + PRIVATE_IP + ":8080"
    rep = workdir(tmp_path, {"settings.example.json": json.dumps(data, indent=1) + "\n"})
    assert {f["kind"] for f in rep.findings} >= {"home_path", "ip"} and {f["file"] for f in rep.findings} == {"settings.example.json"}


def test_исходник_модуля_проходит_ворота_без_единой_находки(tmp_path):
    with open(MODULE, encoding="utf-8") as f:
        text = f.read()
    rep = workdir(tmp_path, {"settings.py": text})
    assert rep.findings == [], rep.findings


# ── сам модуль ──────────────────────────────────────────────────
def test_модуль_использует_только_стандартную_библиотеку_и_каталог_сообщений():
    with open(MODULE, encoding="utf-8") as f:
        tree_ = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree_):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names and names <= set(sys.stdlib_module_names) | {"messages"}, names - set(sys.stdlib_module_names)
    assert "messages" in names


def test_модуль_не_держит_глобального_кэша_настроек():
    with open(MODULE, encoding="utf-8") as f:
        tree_ = ast.parse(f.read())
    for node in tree_.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assert target.id not in ("CACHE", "_CACHE", "_cache", "cache", "_loaded", "_settings", "SETTINGS", "_current"), target.id
    assert not any(isinstance(n, ast.Global) for n in ast.walk(tree_)), "global в модуле: значит, есть общее состояние"
    assert not any(hasattr(S.load, a) for a in ("cache_clear", "cache_info")), "load обёрнут в lru_cache"


def test_у_каждого_кода_settings_есть_английский_шаблон_в_словаре_плагина():
    # полную сверку параметров делает dsh-plugin/test/client-messages.test.mjs; здесь только то, что для каждого кода строка вообще есть
    with open(os.path.join(ROOT, "dsh-plugin", "lib", "client.messages.js"), encoding="utf-8") as f:
        text = f.read()
    assert SETTINGS_CODES
    for code in SETTINGS_CODES:
        line = re.search(r'^\s*"%s":\s*"(.+)",?\s*$' % re.escape(code), text, re.M)
        assert line, f"{code}: нет английского шаблона в client.messages.js"
        assert not re.search("[а-яё]", line.group(1), re.I) and all(f"{{{p}}}" in line.group(1) for p in M.CATALOG[code][1]), code


# ── чужое имя не действует (FR-105) ─────
# Приставка переменных окружения, каталог архива в домашнем каталоге и переменная значения ключа модели, взятые у другой программы, ничего не
# значат: проект их не читает, не создаёт и не называет. Каждый тест держит одну такую уступку, которой быть не должно.
FOREIGN_PREFIX = foreign.PREFIX            # приставка чужих переменных окружения
FOREIGN_KEY = foreign.KEY                  # чужая переменная значения ключа модели: приставки проекта у неё нет
# слова прежнего совета «значение взято из прежней переменной, переименуй её»; голое «прежн» не ищем: во временном каталоге теста стоит имя теста
ADVICE = r"(?i)значение взято|переименуй|прежней переменной|прежней приставк|уступк"
BAD_TEXT = {"int": "много", "float": "много", "bool": "может быть", "str": "a\x01b", "path": "относительный", "url": "ftp://x", "list[str]": "a\x01b",
            "list[path]": "a\x01b", "env_name": "не имя"}
KEYS = [key for key in INVENTORY if key != "home"]


def old_name(key):
    return FOREIGN_PREFIX + key.upper()


def new_name(key):
    return "FLYARCHIVE_" + key.upper()


def texts(spec):
    """Два разных годных текста переменной окружения: первый не равен умолчанию."""
    if spec.choices:
        other = [str(c) for c in spec.choices if c != spec.default]
        return other[0], other[1] if len(other) > 1 else str(spec.default)
    if spec.type in ("int", "float"):
        return str(spec.low if spec.low != spec.default else spec.high), str(spec.high if spec.low != spec.default else spec.low)
    return {"bool": ("false" if spec.default else "true", "true" if spec.default else "false"), "str": ("образец", "другое"),
            "path": ("/образец/путь", "/другой/путь"), "url": ("http://example.com/x", "http://example.com/y"),
            "list[str]": ("один,два", "три"), "list[path]": ("/образец/один.yml,/образец/два.yml", "/другой/три.yml"),
            "env_name": ("SAMPLE_KEY", "OTHER_KEY")}[spec.type]


def watched(monkeypatch, where):
    """Обращения процесса к путям внутри where: stat, open, listdir, scandir, mkdir. Возвращает список, он пополняется во время теста."""
    seen = []

    def spy(real):
        def wrapper(path, *args, **kwargs):
            try:
                text = os.fsdecode(path)
            except (TypeError, ValueError):
                text = ""
            if text == where or text.startswith(where + os.sep):
                seen.append((real.__name__, text))
            return real(path, *args, **kwargs)
        return wrapper

    for owner, name in ((os, "stat"), (os, "lstat"), (os, "open"), (os, "listdir"), (os, "scandir"), (os, "mkdir"), (builtins, "open")):
        monkeypatch.setattr(owner, name, spy(getattr(owner, name)))
    return seen


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.foreign_name
def test_чужая_приставка_не_действует_ни_для_одной_настройки(env, key):
    first, _ = texts(S.SCHEMA[key])
    default = S.load(env=env)
    assert S.load(env={**env, new_name(key): first})[key] != default[key], "образец ничем не отличается от умолчания: тест ничего не доказывает"
    s = S.load(env={**env, old_name(key): first})
    assert s[key] == default[key] and s.source(key) == "default", "значение взято из переменной с чужой приставкой"


@pytest.mark.parametrize("key", KEYS)
def test_если_заданы_обе_переменные_с_разными_значениями_берётся_новая(env, key):
    first, second = texts(S.SCHEMA[key])
    s = S.load(env={**env, new_name(key): first, old_name(key): second})
    assert s[key] == S.load(env={**env, new_name(key): first})[key] and s.source(key) == "env"
    s = S.load(env={**env, new_name(key): second, old_name(key): first})
    assert s[key] == S.load(env={**env, new_name(key): second})[key]


@pytest.mark.foreign_name
def test_чужая_переменная_не_перебивает_файл_настроек_значение_остаётся_из_файла(env, home):
    put(home, {"search_port": 9001, "embed_model": "из-файла", "public_hosts": ["archive.example"], "embed_gpu": True})
    s = S.load(env={**env, old_name("search_port"): "61234", old_name("embed_model"): "из-прежней", old_name("public_hosts"): "other.example",
                    old_name("embed_gpu"): "false", new_name("embed_model"): "из-новой"})
    assert [(s[key], s.source(key)) for key in ("search_port", "public_hosts", "embed_gpu")] == [(9001, "file"), (["archive.example"], "file"), (True, "file")]
    assert (s["embed_model"], s.source("embed_model")) == ("из-новой", "env"), "новая переменная, как и раньше, сильнее файла"


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.foreign_name
def test_негодное_значение_чужой_переменной_не_отказ_а_негодное_в_своей_называет_свою(env, key):
    spec = S.SCHEMA[key]
    bad, good = BAD_TEXT[spec.type], texts(spec)[0]
    s = S.load(env={**env, old_name(key): bad})
    assert s[key] == S.load(env=env)[key] and s.source(key) == "default", "чужую переменную читают: негодное значение в ней не прошло молча"
    e = refusal(env={**env, new_name(key): bad})
    assert code_of(e) in set(BAD_CODE.values()) | {"settings.bad_choice"} and e.message.args["key"] == key and e.message.args["source"] == new_name(key)
    e = refusal(env={**env, new_name(key): bad, old_name(key): good})
    assert e.message.args["source"] == new_name(key)


@pytest.mark.parametrize("value", ["относительный", "/a\x00b", "~нет_такого_пользователя_q7/x", "/занят", ""])
@pytest.mark.foreign_name
def test_чужая_переменная_каталога_архива_не_читается_и_её_значение_не_проверяется(tmp_path, value):
    user = tmp_path / "user"
    s = S.load(env={"HOME": str(user), FOREIGN_HOME: value})
    assert (s["home"], s.source("home")) == (str(user / "flyarchive"), "default")
    s = S.load(env={"HOME": str(user), "FLYARCHIVE_HOME": "", FOREIGN_HOME: value})
    assert (s["home"], s.source("home")) == (str(user / "flyarchive"), "default"), "пустая своя переменная и чужая: умолчание"


@pytest.mark.parametrize("new_exists, old_exists", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.foreign_name
def test_чужой_каталог_в_домашнем_не_подхватывается_не_читается_и_ничего_в_нём_не_создаётся(tmp_path, monkeypatch, new_exists, old_exists):
    user = tmp_path / "user"
    user.mkdir()
    for name, exists in (("flyarchive", new_exists), (FOREIGN_NAME, old_exists)):
        if exists:
            (user / name).mkdir()
            put(user / name, {"search_port": 9707 if name == FOREIGN_NAME else 9606})
    before = tree(user)
    old_seen = watched(monkeypatch, str(user / FOREIGN_NAME))
    s = S.load(env={"HOME": str(user)})
    assert (s["home"], s.source("home")) == (str(user / "flyarchive"), "default"), "каталог архива — не тот, что по умолчанию"
    assert s["search_port"] == (9606 if new_exists else 8765), "файл настроек прочитан не из каталога по умолчанию"
    assert old_seen == [], f"к чужому каталогу были обращения: {old_seen}"
    assert tree(user) == before, "загрузка настроек что-то создала или тронула"


def test_каталог_по_умолчанию_берётся_у_HOME_переданного_окружения_а_не_процесса(tmp_path, monkeypatch):
    user, other = tmp_path / "user", tmp_path / "other"
    (user / FOREIGN_NAME).mkdir(parents=True)
    other.mkdir()
    monkeypatch.setenv("HOME", str(other))
    assert S.load(env={"HOME": str(user)})["home"] == str(user / "flyarchive")
    assert S.load(env={"HOME": str(other)})["home"] == str(other / "flyarchive")


@pytest.mark.foreign_name
def test_справочник_и_пример_без_слов_об_уступках_и_чужом_имени(env, capsys):
    assert S.main(["--reference"], env={**env, old_name("search_port"): "1234", FOREIGN_HOME: "/x"}) == 0
    out = capsys.readouterr().out
    assert "1234" not in out and not re.search("(?i)" + FOREIGN_NAME, out)
    assert not re.search(r"(?i)уступк|переходный период|переходного период|прежн|переименов|legacy", out), "в справочнике остались слова об уступках"
    assert not re.search(r"(?i)уступк|переходный период|переходного период|прежн|переименов|legacy", S.__doc__), "в докстроке модуля остались слова об уступках"
    with open(EXAMPLE, encoding="utf-8") as f:
        assert not re.search("(?i)" + FOREIGN_NAME, f.read())


@pytest.mark.foreign_name
def test_отчёт_настроек_о_чужих_переменных_молчит_и_ничего_не_советует(env, capsys):
    secret = LEAK + "key"
    extra = {old_name("search_port"): "61234", old_name("embed_model"): "из-прежней", FOREIGN_HOME: str(env["HOME"]) + "/arch", FOREIGN_KEY: secret}
    assert S.main([], env={**env, **extra}) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("search_port"))
    assert row.split() == ["search_port", "default", "8765"], "значение взято из чужой переменной"
    assert not re.search("|".join(map(re.escape, [FOREIGN_PREFIX, FOREIGN_KEY, "61234", "из-прежней", secret])), out), out
    assert not re.search(ADVICE, out), out
    assert S.main(["--json"], env={**env, **extra}) == 0
    raw = capsys.readouterr().out
    data = json.loads(raw)
    assert data["search_port"] == {"value": 8765, "source": "default"} and data["embed_model"]["source"] == "default" and set(data) == set(S.SCHEMA)
    assert not re.search("|".join(map(re.escape, [FOREIGN_PREFIX, FOREIGN_KEY, "61234", "из-прежней", secret])), raw), raw


@pytest.mark.foreign_name
def test_отчёт_настроек_о_чужой_переменной_каталога_архива_молчит_и_называет_каталог_по_умолчанию(env, capsys):
    env.pop("FLYARCHIVE_HOME")
    assert S.main([], env={**env, FOREIGN_HOME: str(env["HOME"]) + "/arch"}) == 0
    out = capsys.readouterr().out
    assert "Каталог архива: " + env["HOME"] + "/flyarchive\n" in out and FOREIGN_HOME not in out and "/arch\n" not in out
    row = next(line for line in out.splitlines() if line.startswith("home"))
    assert row.split() == ["home", "default", env["HOME"] + "/flyarchive"]
    assert not re.search(ADVICE, out)


@pytest.mark.parametrize("extra", [{}, {"new": "9002"}, {"new": "9002", "old": "9003"}, {"old": "9003"}])
@pytest.mark.foreign_name
def test_вывод_программы_не_называет_чужую_приставку_при_любом_сочетании_переменных(env, capsys, extra):
    variables = {**({new_name("search_port"): extra["new"]} if "new" in extra else {}), **({old_name("search_port"): extra["old"]} if "old" in extra else {})}
    assert S.main([], env={**env, **variables}) == 0
    out = capsys.readouterr().out
    assert FOREIGN_PREFIX not in out and (not extra.get("old") or extra["old"] not in out)


# ── значение ключа локальной модели: одна переменная нового имени (FR-97, FR-105) ──


def test_значение_ключа_модели_в_схему_не_попадает_а_переменная_не_считается_лишней():
    assert "FLYARCHIVE_LLM_KEY" not in S.SCHEMA and S.env_name("llm_key_file") == "FLYARCHIVE_LLM_KEY_FILE", "значение ключа в схему не попадает"
    assert S.LLM_KEY_ENV == "FLYARCHIVE_LLM_KEY" and "FLYARCHIVE_LLM_KEY" in S.ELSEWHERE and S.unused_env({"FLYARCHIVE_LLM_KEY": "x"}) == []


def test_значение_ключа_запасной_облачной_модели_в_схему_не_попадает_а_переменная_не_считается_лишней(env, capsys):
    assert "FLYARCHIVE_LLM_CLOUD_KEY" not in S.SCHEMA and S.env_name("llm_cloud_key_file") == "FLYARCHIVE_LLM_CLOUD_KEY_FILE"
    assert S.LLM_CLOUD_KEY_ENV == "FLYARCHIVE_LLM_CLOUD_KEY" and "FLYARCHIVE_LLM_CLOUD_KEY" in S.ELSEWHERE
    assert S.unused_env({"FLYARCHIVE_LLM_CLOUD_KEY": "x"}) == []
    secret = LEAK + "cloud"
    for args in ([], ["--json"]):
        assert S.main(args, env={**env, "FLYARCHIVE_LLM_CLOUD_KEY": secret}) == 0
        assert secret not in capsys.readouterr().out, "значение переменной с ключом попало в вывод настроек"


@pytest.mark.foreign_name
def test_чужая_переменная_ключа_модели_не_названа_в_модуле_настроек_и_в_ELSEWHERE():
    assert FOREIGN_KEY not in S.ELSEWHERE and S.unused_env({FOREIGN_KEY: "x"}) == [], "чужая переменная: проект её не знает"
    with open(MODULE, encoding="utf-8") as f:
        assert FOREIGN_KEY not in f.read()


@pytest.mark.foreign_name
def test_вывод_программы_о_чужой_переменной_ключа_молчит_а_значения_не_печатает_и_при_своей(env, capsys):
    secret = LEAK + "key"
    for extra in ({FOREIGN_KEY: secret}, {FOREIGN_KEY: secret, "FLYARCHIVE_LLM_KEY": secret + "2"}, {FOREIGN_KEY: ""}, {"FLYARCHIVE_LLM_KEY": secret + "2"}, {}):
        for args in ([], ["--json"]):
            assert S.main(args, env={**env, **extra}) == 0
            out = capsys.readouterr().out
            assert FOREIGN_KEY not in out and secret not in out and secret + "2" not in out, (args, extra)
            assert not re.search(ADVICE, out), (args, extra)


# ── адрес прослушивания служб не настраивается (FR-97): поиск, документы и переходник MCP слушают только петлю, наружу — шлюз ──
def test_настройки_адреса_прослушивания_нет_ни_в_схеме_ни_в_примере_ни_в_справочнике():
    assert "bind_host" not in S.SCHEMA
    with open(EXAMPLE, encoding="utf-8") as f:
        text = f.read()
    assert "bind_host" not in json.loads(text) and "bind_host" not in text
    reference = S.reference()
    assert "bind_host" not in reference and "BIND_HOST" not in reference


def test_ключ_bind_host_в_файле_настроек_отказ_неизвестная_настройка_без_значения(env, home):
    put(home, {"bind_host": LEAK + "0.0.0.0"})
    e = refusal(env=env)
    assert code_of(e) == "settings.unknown_key" and e.message.args == {"key": "bind_host"} and LEAK not in str(e)


def test_переменная_bind_host_ничего_не_задаёт_а_вывод_называет_её_переменной_без_настройки(env, capsys):
    s = S.load(env={**env, "FLYARCHIVE_BIND_HOST": "0.0.0.0"})
    assert "bind_host" not in s and "0.0.0.0" not in json.dumps(s.as_dict())
    assert S.unused_env({**env, "FLYARCHIVE_BIND_HOST": "0.0.0.0"}) == ["FLYARCHIVE_BIND_HOST"]
    assert S.main([], env={**env, "FLYARCHIVE_BIND_HOST": "0.0.0.0"}) == 0
    out = capsys.readouterr().out
    assert "FLYARCHIVE_BIND_HOST" in out and "без настройки" in out


# ── startup: точка входа без трассировки (FR-96) ────────────────
def run_startup(extra):
    code = "import sys; sys.path.insert(0, %r); import settings; s = settings.startup(); print('ok', s['search_port'])" % os.path.dirname(MODULE)
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", **extra}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", env=environment)


def test_startup_возвращает_те_же_настройки_что_load(env, home):
    put(home, {"search_port": 9001})
    s = S.startup(env=env)
    assert isinstance(s, S.Settings) and s.as_dict() == S.load(env=env).as_dict() and s["search_port"] == 9001
    mine = home.parent / "mine"
    assert S.startup(env=env, home=str(mine))["home"] == str(mine)


def test_startup_без_аргументов_читает_окружение_процесса(env, home, monkeypatch):
    put(home, {"search_port": 9001})
    monkeypatch.setenv("FLYARCHIVE_HOME", str(home))
    assert S.startup()["search_port"] == 9001


def test_startup_при_отказе_одна_строка_в_stderr_и_выход_с_кодом_2(env, home, capsys):
    put(home, {"search_port": "много"})
    with pytest.raises(SystemExit) as caught:
        S.startup(env=env)
    assert caught.value.code == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == "настройки: " + str(refusal(env=env)) + "\n"
    assert out.err.count("\n") == 1 and "search_port" in out.err and "Traceback" not in out.err


def test_startup_отказ_всегда_одной_строкой_и_без_значения(env, home, capsys):
    cases = [
        lambda: put(home, {"search_port": LEAK}),
        lambda: put(home, raw='{"search_port": "' + LEAK + '", '),
        lambda: put(home, {"nope": LEAK}),
        lambda: put(home, {"home": LEAK}),
        lambda: put(home, {"search_port": 9001}, mode=0o666),
        lambda: put(home, raw=b'{"embed_model": "' + LEAK.encode() + b'\xff"}'),
        lambda: env.update({new_name("search_port"): LEAK}) or put(home, {}),
        lambda: env.update({new_name("hosts"): "a\x01" + LEAK}) or put(home, {}),
    ]
    for make in cases:
        env.pop(new_name("search_port"), None)
        env.pop(new_name("hosts"), None)
        make()
        with pytest.raises(SystemExit) as caught:
            S.startup(env=env)
        err = capsys.readouterr().err
        assert caught.value.code == 2 and err.startswith("настройки: ") and err.count("\n") == 1 and err.endswith("\n")
        assert LEAK not in err and "Traceback" not in err


def test_startup_как_отдельный_процесс_код_2_и_ни_трассировки_ни_значения(env, home):
    put(home, {"search_port": LEAK})
    r = run_startup(env)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.startswith("настройки: ") and len(r.stderr.splitlines()) == 1 and "Traceback" not in r.stderr and LEAK not in r.stderr
    put(home, {"search_port": 9001})
    r = run_startup(env)
    assert r.returncode == 0 and r.stdout.strip() == "ok 9001" and r.stderr == ""


def test_startup_пишет_по_русски_и_при_ascii_stderr(env, home):
    put(home, {"search_port": "много"})
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import settings; settings.startup()" % os.path.dirname(MODULE)],
                       capture_output=True, env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "ascii", **env})
    assert r.returncode == 2 and b"Traceback" not in r.stderr and r.stderr.decode("utf-8").startswith("настройки: ")


def test_startup_не_глотает_чужие_ошибки(env, home, monkeypatch):
    def boom(**kw):
        raise ZeroDivisionError("не отказ настроек")
    monkeypatch.setattr(S, "load", boom)
    with pytest.raises(ZeroDivisionError):
        S.startup(env=env)


# ── раскладка внутри каталога архива фиксирована: индекс и корпус отдельными настройками не выносятся (FR-96) ──
@pytest.mark.parametrize("key", ["index_dir", "corpus_dir"])
def test_ключи_index_dir_и_corpus_dir_в_файле_настроек_отказ_неизвестной_настройки(env, home, key):
    assert key not in S.SCHEMA
    for value in ("{home}/" + LEAK, "/где-то/" + LEAK, 5):
        put(home, {key: value})
        e = refusal(env=env)
        assert code_of(e) == "settings.unknown_key" and e.message.args == {"key": key}
        assert all(LEAK not in text for text in seen_everywhere(e))
    assert key not in S.load(env=env, home=str(home / "нет-файла"))


def test_переменные_индекса_и_корпуса_не_настройки_их_значения_не_читаются(env):
    env.update({"FLYARCHIVE_INDEX_DIR": "относительный", "FLYARCHIVE_CORPUS_DIR": "\x00"})
    s = S.load(env=env)                                    # негодные значения не отказ: настройки нет, и переменная названа неиспользуемой
    assert "index_dir" not in s and "corpus_dir" not in s
    assert S.unused_env(env) == ["FLYARCHIVE_CORPUS_DIR", "FLYARCHIVE_INDEX_DIR"]


# ── настройки приёмки остаются в inbox.json, в общей схеме их нет (FR-98) ──
# Одна настройка — одно место: значение, сохранённое из DSH (раздел Intake) или командой `inbox set`, лежит в inbox.json и не может быть молча
# перебито ни переменной окружения, ни файлом settings.json. Распознавание текста на картинках — сценарии владельца, в открытый снимок не идут.
INTAKE_WORDS = ("inbox set", "inbox.json", "Intake")


def test_в_схеме_нет_ключей_приёмки_и_распознавания_а_настроек_осталось_сорок_пять():
    assert not set(REMOVED) & set(S.SCHEMA) and len(S.SCHEMA) == 45
    assert set(INVENTORY) == set(S.SCHEMA) and not set(REMOVED) & set(INVENTORY)


@pytest.mark.parametrize("key", REMOVED)
def test_ключ_приёмки_или_распознавания_в_файле_настроек_отказ_неизвестной_настройки_без_значения(env, home, key):
    for value in (LEAK, 5, True, ["x"]):
        put(home, {key: value})
        e = refusal(env=env)
        assert code_of(e) == "settings.unknown_key" and e.message.args == {"key": key}, (key, value)
        assert all(LEAK not in text for text in seen_everywhere(e))


@pytest.mark.parametrize("key", REMOVED)
def test_переменная_приёмки_или_распознавания_ничего_не_задаёт_и_названа_неиспользуемой(env, key):
    s = S.load(env={**env, new_name(key): LEAK})             # негодное значение не отказ: настройки нет
    assert key not in s and LEAK not in json.dumps(s.as_dict())
    assert S.unused_env({**env, new_name(key): "1"}) == [new_name(key)]


def test_ключи_приёмки_в_примере_настроек_нет_и_пример_с_ними_загрузке_не_подлежит(env, home):
    with open(EXAMPLE, encoding="utf-8") as f:
        data = json.load(f)
    assert not set(REMOVED) & set(data)
    put(home, {**data, "period": 30})
    assert code_of(refusal(env=env)) == "settings.unknown_key"


@pytest.mark.foreign_name
def test_справочник_говорит_что_настройки_приёмки_лежат_в_inbox_json_и_меняются_командой_и_в_DSH(capsys):
    assert S.main(["--reference"], env={}) == 0
    out = capsys.readouterr().out
    table = [line for line in out.splitlines() if line.startswith("|")]
    note = "\n".join(line for line in out.splitlines() if not line.startswith("|"))
    assert all(word in note for word in INTAKE_WORDS), note
    assert not any("`" + key + "`" in line for key in REMOVED for line in table), "в таблице настроек приёмки быть не должно"
    assert not re.search("(?i)" + re.escape(foreign.NAME), note)


def test_справочник_отдельным_процессом_тоже_несёт_пояснение_о_настройках_приёмки():
    r = run_program("--reference")
    assert r.returncode == 0 and all(word in r.stdout for word in INTAKE_WORDS) and r.stdout.startswith("| ")


def test_докстрока_модуля_говорит_где_настройки_приёмки():
    doc = " ".join(S.__doc__.split())
    assert all(word in doc for word in INTAKE_WORDS), "докстрока не называет, где настройки приёмки"

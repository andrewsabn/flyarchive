"""Настройки установки и запускалок: режим `--get`, настройки оболочки DSH (FR-104).

Запускалки на shell читают настройки командой `python3 tools/settings.py --get КЛЮЧ`: действующее значение одной настройки, список — по строке
на элемент. Здесь же схема настроек оболочки: дополнительные файлы настройки (`dsh_patches`, список путей), имена переменных из файла окружения
(`env_names`) и имя переменной, под которым оболочка ждёт ключ локальной модели (`dsh_llm_key_env`). Все образцы выдуманные; окружение —
явный словарь `env=`, процессы идут с чистым окружением.
"""
import json
import os
import re
import subprocess
import sys

import pytest

import messages as M
import settings as S

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROGRAM = os.path.join(ROOT, "tools", "settings.py")
LEAK = "LEAKq7Zk9"


@pytest.fixture
def home(tmp_path):
    d = tmp_path / "arch"
    d.mkdir()
    return d


@pytest.fixture
def env(tmp_path, home):
    return {"HOME": str(tmp_path / "user"), "FLYARCHIVE_HOME": str(home)}


def put(folder, data):
    p = folder / "settings.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    p.chmod(0o600)


def program(*args, extra=None):
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", **(extra or {})}
    return subprocess.run([sys.executable, PROGRAM, *args], capture_output=True, text=True, encoding="utf-8", env=environment)


def refusal(**kw):
    with pytest.raises(S.SettingsError) as caught:
        S.load(**kw)
    return caught.value


# ── --get: значение одной настройки ─────────────────────────────
def test_get_печатает_значение_числа_и_строки_одной_строкой(env, home):
    put(home, {"dsh_port": 3181, "embed_model": "модель-№1"})
    r = program("--get", "dsh_port", extra=env)
    assert (r.returncode, r.stdout, r.stderr) == (0, "3181\n", "")
    r = program("--get", "embed_model", extra=env)
    assert (r.returncode, r.stdout, r.stderr) == (0, "модель-№1\n", "")


def test_get_без_файла_и_окружения_печатает_умолчание(env):
    assert program("--get", "dsh_port", extra=env).stdout == "3080\n"
    assert program("--get", "mcp_port", extra=env).stdout == "8767\n"


def test_get_каталог_архива_и_пути_уже_раскрыты(env, home):
    assert program("--get", "home", extra=env).stdout == str(home) + "\n"
    r = program("--get", "units_dir", extra=env)
    assert r.stdout == env["HOME"] + "/.config/systemd/user\n"
    put(home, {"env_file": "{home}/секреты/cloud.env"})
    assert program("--get", "env_file", extra=env).stdout == str(home) + "/секреты/cloud.env\n"


def test_get_пустая_настройка_печатает_пустую_строку_и_код_0(env):
    r = program("--get", "env_file", extra=env)
    assert (r.returncode, r.stdout, r.stderr) == (0, "\n", "")
    assert program("--get", "dsh_llm_key_env", extra=env).stdout == "\n"


def test_get_переключатель_печатает_true_или_false(env, home):
    assert program("--get", "embed_gpu", extra=env).stdout == "false\n"
    put(home, {"embed_gpu": True})
    assert program("--get", "embed_gpu", extra=env).stdout == "true\n"


def test_get_список_по_строке_на_элемент_а_пустой_список_ничего_не_печатает(env, home):
    r = program("--get", "env_names", extra=env)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    put(home, {"env_names": ["FIRST_KEY", "SECOND_KEY"], "dsh_patches": ["/опт/модели.yml", "{home}/второй.yml"]})
    assert program("--get", "env_names", extra=env).stdout == "FIRST_KEY\nSECOND_KEY\n"
    assert program("--get", "dsh_patches", extra=env).stdout == "/опт/модели.yml\n" + str(home) + "/второй.yml\n"


def test_get_видит_все_три_слоя_окружение_сильнее_файла(env, home):
    put(home, {"dsh_port": 3181})
    assert program("--get", "dsh_port", extra=env).stdout == "3181\n"
    assert program("--get", "dsh_port", extra={**env, "FLYARCHIVE_DSH_PORT": "3282"}).stdout == "3282\n"
    assert program("--get", "env_names", extra={**env, "FLYARCHIVE_ENV_NAMES": "ONE_KEY, TWO_KEY"}).stdout == "ONE_KEY\nTWO_KEY\n"


def test_get_каждая_настройка_схемы_читается_и_совпадает_с_load(env, home):
    s = S.load(env=env)
    for key in S.SCHEMA:
        r = program("--get", key, extra=env)
        value = s[key]
        want = "".join(f"{item}\n" for item in value) if isinstance(value, list) else \
            ("true\n" if value is True else "false\n" if value is False else f"{value}\n")
        assert (r.returncode, r.stdout, r.stderr) == (0, want, ""), key


def test_get_неизвестный_ключ_код_2_и_ничего_в_stdout(env):
    r = program("--get", "нет_такой_настройки", extra=env)
    assert r.returncode == 2 and r.stdout == "" and "нет_такой_настройки" in r.stderr
    assert program("--get", "llm_providers", extra=env).returncode == 2, "прежней настройки провайдеров в схеме больше нет"


def test_get_ключ_с_управляющими_знаками_не_попадает_в_stderr_как_есть(env):
    r = program("--get", "плохой\x1b[31mключ", extra=env)
    assert r.returncode == 2 and "\x1b" not in r.stderr


def test_get_не_вместе_с_json_и_справочником_и_нужен_ключ(env):
    for extra in (("--json",), ("--reference",)):
        assert program("--get", "dsh_port", *extra, extra=env).returncode == 2
    assert program("--get", extra=env).returncode == 2


def test_get_отказ_настроек_код_1_сообщение_в_stderr_и_пусто_в_stdout(env, home):
    put(home, {"dsh_port": 0})
    r = program("--get", "dsh_port", extra=env)
    assert r.returncode == 1 and r.stdout == "" and "dsh_port" in r.stderr


def test_get_в_процессе_через_main_то_же_самое(env, home, capsys):
    put(home, {"dsh_port": 3181})
    assert S.main(["--get", "dsh_port"], env=env) == 0
    assert capsys.readouterr().out == "3181\n"
    assert S.main(["--get", "нет_такой"], env=env) == 2
    out = capsys.readouterr()
    assert out.out == "" and "нет_такой" in out.err


def test_get_ничего_не_создаёт_на_диске(env, home):
    program("--get", "dsh_port", extra=env)
    assert sorted(os.listdir(home)) == []


# ── дополнительные файлы настройки оболочки: dsh_patches ────────
def test_dsh_patches_по_умолчанию_пуст_и_это_список_путей():
    spec = S.SCHEMA["dsh_patches"]
    assert spec.type == "list[path]" and list(spec.default) == [] and re.search("[а-яё]", spec.note, re.I)


def test_dsh_patches_тильда_и_подстановка_home_раскрываются_в_каждом_элементе(env, home):
    put(home, {"dsh_patches": ["~/модели.yml", "{home}/свой.yml", "/опт/третий.yml"]})
    s = S.load(env=env)
    assert list(s["dsh_patches"]) == [env["HOME"] + "/модели.yml", str(home) + "/свой.yml", "/опт/третий.yml"]
    assert s.source("dsh_patches") == "file"


def test_dsh_patches_из_окружения_через_запятую(env):
    s = S.load(env={**env, "FLYARCHIVE_DSH_PATCHES": "/опт/один.yml, ~/два.yml"})
    assert list(s["dsh_patches"]) == ["/опт/один.yml", env["HOME"] + "/два.yml"] and s.source("dsh_patches") == "env"


@pytest.mark.parametrize("bad", [["относительный.yml"], ["/опт/а.yml", "./б.yml"], [""], ["../в.yml"], ["{home}в.yml"]])
def test_dsh_patches_относительный_путь_отказ_с_ключом_и_источником_без_значения(env, home, bad):
    put(home, {"dsh_patches": bad})
    e = refusal(env=env)
    assert e.message.code == "settings.bad_path" and e.message.args == {"key": "dsh_patches", "source": "settings.json"}
    assert bad[-1] not in str(e) or bad[-1] == ""


@pytest.mark.parametrize("bad", ["/опт/один.yml", 5, [1], [["/опт/один.yml"]], {"a": "/b"}, ["/опт/а\x01.yml"]])
def test_dsh_patches_не_список_строк_отказ_как_у_списка(env, home, bad):
    put(home, {"dsh_patches": bad})
    assert refusal(env=env).message.code == "settings.bad_list"


def test_dsh_patches_список_неизменяем(env):
    s = S.load(env={**env, "FLYARCHIVE_DSH_PATCHES": "/опт/один.yml"})
    with pytest.raises(TypeError):
        s["dsh_patches"].append("/другой.yml")


# ── имена переменных из файла окружения: env_names ──────────────
def test_env_names_по_умолчанию_пуст_и_это_список_строк(env):
    spec = S.SCHEMA["env_names"]
    assert spec.type == "list[str]" and list(spec.default) == []
    assert list(S.load(env=env)["env_names"]) == []


def test_env_names_из_файла_как_есть(env, home):
    put(home, {"env_names": ["CLOUD_KEY_ONE", "CLOUD_KEY_TWO"]})
    assert list(S.load(env=env)["env_names"]) == ["CLOUD_KEY_ONE", "CLOUD_KEY_TWO"]


# ── имя переменной ключа локальной модели: dsh_llm_key_env ──────
def test_dsh_llm_key_env_по_умолчанию_пусто_и_тип_имя_переменной(env):
    spec = S.SCHEMA["dsh_llm_key_env"]
    assert spec.type == "env_name" and spec.default == "" and re.search("[а-яё]", spec.note, re.I)
    assert S.load(env=env)["dsh_llm_key_env"] == ""


@pytest.mark.parametrize("name", ["LOCAL_KEY", "A", "_X", "KEY_2", "MODEL_API_KEY_1"])
def test_dsh_llm_key_env_годное_имя_принимается_в_файле_и_в_окружении(env, home, name):
    put(home, {"dsh_llm_key_env": name})
    assert S.load(env=env)["dsh_llm_key_env"] == name
    assert S.load(env={**env, "FLYARCHIVE_DSH_LLM_KEY_ENV": name})["dsh_llm_key_env"] == name


@pytest.mark.parametrize("name", ["local_key", "Local_Key", "1KEY", "KEY NAME", "KEY-NAME", "KEY=1", "KEY;rm", "$(KEY)", "${KEY}", "ИМЯ", "KEY.A", "KEY\n"])
def test_dsh_llm_key_env_негодное_имя_отказ_из_файла_и_из_окружения(env, home, name):
    put(home, {"dsh_llm_key_env": name})
    e = refusal(env=env)
    assert e.message.code == "settings.bad_env_name" and e.message.args == {"key": "dsh_llm_key_env", "source": "settings.json"}
    put(home, {})
    e = refusal(env={**env, "FLYARCHIVE_DSH_LLM_KEY_ENV": name})
    assert e.message.code == "settings.bad_env_name" and e.message.args["source"] == "FLYARCHIVE_DSH_LLM_KEY_ENV"


def test_dsh_llm_key_env_значение_в_отказ_не_попадает(env, home):
    put(home, {"dsh_llm_key_env": LEAK + " не имя"})
    e = refusal(env=env)
    assert LEAK not in str(e) and LEAK not in json.dumps(e.message.to_json(), ensure_ascii=False)


def test_отказ_bad_env_name_в_каталоге_сообщений_с_двумя_параметрами():
    assert M.CATALOG["settings.bad_env_name"][1] == ("key", "source")


# ── прежняя настройка провайдеров убрана ────────────────────────
def test_llm_providers_нет_в_схеме_и_в_файле_это_неизвестный_ключ(env, home):
    assert "llm_providers" not in S.SCHEMA
    put(home, {"llm_providers": ["x"]})
    assert refusal(env=env).message.code == "settings.unknown_key"


def test_справочник_называет_новые_настройки_и_их_типы(capsys):
    assert S.main(["--reference"], env={}) == 0
    out = capsys.readouterr().out
    assert re.search(r"\| `dsh_patches` \| list\[path\] \|", out) and re.search(r"\| `env_names` \| list\[str\] \|", out)
    assert re.search(r"\| `dsh_llm_key_env` \| env_name \|", out) and "llm_providers" not in out

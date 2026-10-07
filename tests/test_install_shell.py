"""Оболочка по желанию: служба оболочки и плагин не ставятся без неё (FR-107).

Служба оболочки (flyarchive-dsh.service) и плагин не ставятся, когда команды dsh в PATH нет, и по ключу `--no-dsh`; установка говорит, что такое оболочка
(одной фразой), как её поставить (`npm install -g @deepseek-ai/dsh`, пакет под лицензией MIT, исходники github.com/deepseek-ai/deepseek-harness) и что
повторить (`flyarchive install`). Остальные службы ставятся. Раньше поставленный файл службы оболочки не удаляется. Команды запуска служб в `--dry-run`
и в `--json` — без службы оболочки. Когда dsh есть, всё как раньше, байт в байт: отпечатки файлов ниже сняты с прежнего кода (пути в тексте заменены
на подстановки, чтобы отпечаток не зависел от места репозитория и временного каталога). Машина выдуманная; dsh — заглушка в PATH.
"""
import hashlib
import os
import sys

import pytest

from test_install import BASE_UNITS, PLUGIN, START_ORDER, TIMER_COMMANDS, TOOLS, Machine, sources_of

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: init заводит настоящую таблицу индекса")

NO_DSH_UNITS = [name for name in BASE_UNITS if name != "flyarchive-dsh.service"]
WITHOUT_DSH = [name for name in START_ORDER if name != "flyarchive-dsh.service"]
# первые 20 знаков sha256 текстов, которые писал прежний код при dsh в PATH: {code}, {home}, {python}, {bin} — подстановки путей машины теста
# (двадцати знаков хватает, чтобы заметить правку в один байт; целиком 64 знака ворота публикации приняли бы за ключ)
GOLDEN = {
    "flyarchive-dsh.service": "c6a35da8ca0aa730aab1",
    "flyarchive-inbox.service": "8eca22f851d8f14905dc",
    "flyarchive-inbox.timer": "37a992c0aba04ab69ad4",
    "flyarchive-mcp.service": "cf58615e2b490250703e",
    "flyarchive-office.service": "0124fc96217402406057",
    "flyarchive-search.service": "618270d4f1c48d6c043b",
}
# файл настройки оболочки: прежний отпечаток 0baf49cd95061a315450; изменилась одна строка — command ведёт на запускалку {home}/bin/flyarchive
GOLDEN_PATCH = "f07b1c41df89676a0cd2"
EXPLAIN = ("DeepSeek Harness", "npm install -g @deepseek-ai/dsh", "MIT", "github.com/deepseek-ai/deepseek-harness", "flyarchive install")


def normal(m, text):
    return text.replace(str(TOOLS), "{code}").replace(str(m.home), "{home}").replace(sys.executable, "{python}").replace(str(m.bin), "{bin}")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


# ── dsh есть: всё как раньше, байт в байт ───────────────────────
def test_с_dsh_службы_и_таймер_те_же_что_писал_прежний_код_байт_в_байт(tmp_path):
    m = Machine(tmp_path, dsh=True)
    assert m.run("--no-start").code == 0
    assert sorted(os.listdir(m.units)) == BASE_UNITS
    assert {name: sha(normal(m, m.unit(name))) for name in BASE_UNITS} == GOLDEN
    assert sha((m.home / "dsh.patch.yml").read_text(encoding="utf-8").replace(str(TOOLS), "{code}").replace(str(m.home), "{home}")) == GOLDEN_PATCH


def test_с_dsh_плагин_лежит_в_профиле_и_службы_запускаются_в_прежнем_порядке(tmp_path):
    m = Machine(tmp_path, dsh=True)
    assert m.run().code == 0
    target = m.profile / "node_modules" / "flyarchive-dsh-plugin"
    for rel, data in sources_of(PLUGIN).items():
        assert (target / rel).read_bytes() == data, rel
    assert m.log() == ["--user daemon-reload"] + [f"--user enable --now {u}" for u in START_ORDER] + [f"--user {c}" for c in TIMER_COMMANDS]


def test_с_dsh_установка_не_говорит_про_установку_оболочки(tmp_path):
    r = Machine(tmp_path, dsh=True).run("--no-start")
    assert r.code == 0 and "npm install" not in r.out and "--no-dsh" not in r.out


# ── dsh нет ─────────────────────────────────────────────────────
def test_без_dsh_в_PATH_службы_оболочки_нет_а_остальные_службы_и_таймер_стоят(tmp_path):
    m = Machine(tmp_path, dsh=False)
    r = m.run()
    assert r.code == 0, r.err
    assert sorted(os.listdir(m.units)) == NO_DSH_UNITS
    assert {name: sha(normal(m, m.unit(name))) for name in NO_DSH_UNITS} == {name: GOLDEN[name] for name in NO_DSH_UNITS}, "остальные — те же байты"


def test_без_dsh_плагин_не_ставится_и_профиль_оболочки_не_тронут(tmp_path):
    m = Machine(tmp_path, dsh=False)
    before = sorted(os.listdir(m.profile))
    r = m.run()
    assert r.code == 0 and sorted(os.listdir(m.profile)) == before == []
    assert "плагин" in r.out and "оболочк" in r.out


def test_без_dsh_установка_говорит_что_такое_оболочка_как_её_поставить_и_что_повторить(tmp_path):
    r = Machine(tmp_path, dsh=False).run("--no-start")
    assert r.code == 0
    for part in EXPLAIN:
        assert part in r.out, part
    assert "flyarchive connect" in r.out or "сторонних оболочек" in r.out, "и что без оболочки архив работает"


def test_без_dsh_в_запуске_служб_нет_службы_оболочки_а_остальные_по_порядку(tmp_path):
    m = Machine(tmp_path, dsh=False)
    assert m.run().code == 0
    assert not any("dsh" in line for line in m.log()), m.log()
    assert m.log() == ["--user daemon-reload"] + [f"--user enable --now {u}" for u in WITHOUT_DSH] + [f"--user {c}" for c in TIMER_COMMANDS]


def test_без_dsh_в_пробном_прогоне_и_в_json_команд_запуска_службы_оболочки_нет(tmp_path):
    m = Machine(tmp_path, dsh=False)
    dry = m.run("--dry-run")
    assert dry.code == 0 and "flyarchive-dsh.service" not in dry.out, "ни файла, ни команды запуска службы оболочки"
    assert [line.strip() for line in dry.out.splitlines() if "systemctl --user enable" in line] ==         [f"systemctl --user enable --now {u}" for u in WITHOUT_DSH] + ["systemctl --user enable --now flyarchive-inbox.timer"]
    data = m.run("--dry-run", "--json").json()
    assert not any("dsh" in " ".join(c) for c in data["commands"]), data["commands"]
    assert not any(f["path"].endswith("flyarchive-dsh.service") for f in data["files"])
    assert not any("flyarchive-dsh-plugin" in f["path"] for f in data["files"])
    assert [c[-1] for c in data["commands"] if c[2] == "enable" and "timer" not in c[-1]] == WITHOUT_DSH
    done = m.run("--no-start", "--json").json()
    assert not any("dsh" in " ".join(c) for c in done["commands"]) and any(s["step"] == "dsh" for s in done["skipped"])


def test_раньше_поставленный_файл_службы_оболочки_не_удаляется(tmp_path):
    m = Machine(tmp_path, dsh=True)
    assert m.run("--no-start").code == 0
    before = (m.units / "flyarchive-dsh.service").read_bytes()
    os.remove(m.bin / "dsh")                                                   # оболочку убрали из PATH
    r = m.run("--no-start")
    assert r.code == 0 and (m.units / "flyarchive-dsh.service").read_bytes() == before


def test_раньше_поставленный_плагин_без_dsh_тоже_остаётся(tmp_path):
    m = Machine(tmp_path, dsh=True)
    assert m.run("--no-start").code == 0
    plugin = m.profile / "node_modules" / "flyarchive-dsh-plugin"
    before = {rel: (plugin / rel).read_bytes() for rel in sources_of(PLUGIN)}
    os.remove(m.bin / "dsh")
    assert m.run("--no-start").code == 0
    assert {rel: (plugin / rel).read_bytes() for rel in before} == before


def test_повторить_установку_после_появления_dsh_ставит_службу_оболочки_и_плагин(tmp_path):
    m = Machine(tmp_path, dsh=False)
    assert m.run().code == 0 and not (m.units / "flyarchive-dsh.service").exists()
    m.stub("dsh", "exit 0")
    assert m.run().code == 0
    assert sorted(os.listdir(m.units)) == BASE_UNITS and (m.profile / "node_modules" / "flyarchive-dsh-plugin" / "package.json").is_file()
    assert sha(normal(m, m.unit("flyarchive-dsh.service"))) == GOLDEN["flyarchive-dsh.service"]


# ── ключ --no-dsh ───────────────────────────────────────────────
def test_ключ_no_dsh_при_dsh_в_PATH_тоже_не_ставит_службу_оболочки_и_плагин(tmp_path):
    m = Machine(tmp_path, dsh=True)
    r = m.run("--no-dsh")
    assert r.code == 0, r.err
    assert sorted(os.listdir(m.units)) == NO_DSH_UNITS and sorted(os.listdir(m.profile)) == []
    assert "--no-dsh" in r.out and not any("dsh" in line for line in m.log())


def test_ключ_no_dsh_в_справке_команды(tmp_path):
    r = Machine(tmp_path, dsh=True).run("--help")
    assert r.code == 0 and "--no-dsh" in r.out

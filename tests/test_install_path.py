"""Установка кладёт команду туда, где её найдёт оболочка пользователя (FR-107).

`flyarchive install` кладёт ссылку на команду в каталог команд пользователя (настройка bin_dir, по умолчанию ~/.local/bin; пустое значение — не класть):
каталога нет — создаёт; ссылка уже ведёт на эту команду — «уже есть»; там файл или ссылка на другое — не трогает и говорит; каталога нет в PATH — говорит,
какую строку добавить; `--dry-run` показывает, что было бы сделано, и ничего не пишет; повторная установка ничего не меняет. Файлы служб остаются байт
в байт прежними. В конце установка одной строкой называет, чего обязательного не хватает (та же проверка, что у `flyarchive doctor`).
Машина выдуманная и пустая (свой домашний каталог, подставные systemctl и dsh в PATH); живые ~/.local/bin, службы и служба векторов не трогаются.
"""
import json
import os
import subprocess

import pytest

import messages as M
from archivekit import DEAD_VECTORS
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов
from test_install import Machine, hashes, tree

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: init заводит настоящую таблицу индекса")


def machine(tmp_path):
    return Machine(tmp_path, dsh=True)


def link_of(m):
    return m.user / ".local" / "bin" / "flyarchive"


def on_path(m):
    return f"{m.user / '.local' / 'bin'}:{m.bin}:/usr/bin:/bin"


def command_lines(out):
    """Строки шага «Команда» установки: от его заголовка до следующего."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("5. Команда"))
    return lines[start + 1:next((i for i in range(start + 1, len(lines)) if lines[i][:2] in ("1.", "2.", "3.", "4.", "6.")), len(lines))]


# ── ссылка кладётся ─────────────────────────────────────────────
def test_каталога_команд_нет_установка_создаёт_его_и_кладёт_ссылку_на_команду(tmp_path):
    m = machine(tmp_path)
    assert not (m.user / ".local").exists()
    r = m.run("--no-start")
    assert r.code == 0, r.err
    link = link_of(m)
    assert link.is_symlink() and os.readlink(link) == str(m.home / "bin" / "flyarchive"), "ссылка ведёт на запускалку в каталоге архива, абсолютным путём"
    assert os.access(link, os.X_OK)
    assert "создана ссылка" in r.out and str(link) in r.out


def test_ссылка_работает_команду_можно_позвать_по_имени_из_каталога_команд(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start").code == 0
    got = subprocess.run([str(link_of(m)), "--help"], env=m.env(), capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert got.returncode == 0 and "doctor" in got.stdout and "install" in got.stdout, got.stderr


def test_каталога_нет_в_PATH_установка_называет_строку_которую_надо_добавить(tmp_path):
    m = machine(tmp_path)
    r = m.run("--no-start")
    folder = m.user / ".local" / "bin"
    assert f'export PATH="{folder}:$PATH"' in r.out
    assert any("PATH" in line for line in command_lines(r.out))


def test_каталог_в_PATH_строки_про_PATH_нет(tmp_path):
    m = machine(tmp_path)
    r = m.run("--no-start", PATH=on_path(m))
    assert r.code == 0 and "export PATH" not in r.out and "создана ссылка" in r.out


def test_повторная_установка_говорит_уже_есть_и_ничего_не_меняет(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start", PATH=on_path(m)).code == 0
    link = link_of(m)
    before = (os.lstat(link).st_ino, os.lstat(link).st_mtime_ns, os.readlink(link), hashes(m.user / ".local"))     # по содержимому: запускалку, на которую
    r = m.run("--no-start", PATH=on_path(m))                                                                       # ведёт ссылка, установка пишет заново
    assert r.code == 0 and "уже есть" in r.out and "создана ссылка" not in r.out
    assert (os.lstat(link).st_ino, os.lstat(link).st_mtime_ns, os.readlink(link), hashes(m.user / ".local")) == before


def test_ссылка_на_запускалку_через_другой_путь_тоже_уже_есть(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start").code == 0
    link_of(m).unlink()
    other = tmp_path / "обход"
    other.symlink_to(m.home / "bin")                                  # другой путь к тому же каталогу запускалки
    link_of(m).symlink_to(other / "flyarchive")
    r = m.run("--no-start")
    assert r.code == 0 and "уже есть" in r.out and os.readlink(link_of(m)) == str(other / "flyarchive")


# ── чужое не трогается ──────────────────────────────────────────
def test_на_месте_ссылки_лежит_чужой_файл_он_не_тронут_и_установка_говорит_об_этом(tmp_path):
    m = machine(tmp_path)
    link_of(m).parent.mkdir(parents=True)
    link_of(m).write_text("#!/bin/sh\necho чужая команда\n", encoding="utf-8")
    link_of(m).chmod(0o755)
    before = tree(m.user / ".local")
    r = m.run("--no-start")
    assert r.code == 0, r.err
    assert not link_of(m).is_symlink() and link_of(m).read_text(encoding="utf-8") == "#!/bin/sh\necho чужая команда\n"
    assert tree(m.user / ".local") == before
    assert "не трогаю" in r.out and str(link_of(m)) in r.out and "создана ссылка" not in r.out


def test_на_месте_ссылки_ссылка_на_другое_она_не_тронута(tmp_path):
    m = machine(tmp_path)
    link_of(m).parent.mkdir(parents=True)
    other = tmp_path / "другая-команда"
    other.write_text("x", encoding="utf-8")
    link_of(m).symlink_to(other)
    r = m.run("--no-start")
    assert r.code == 0 and os.readlink(link_of(m)) == str(other) and "не трогаю" in r.out


def test_висячая_ссылка_тоже_чужая_и_не_тронута(tmp_path):
    m = machine(tmp_path)
    link_of(m).parent.mkdir(parents=True)
    link_of(m).symlink_to(tmp_path / "нет-такого")
    r = m.run("--no-start")
    assert r.code == 0 and os.readlink(link_of(m)) == str(tmp_path / "нет-такого") and "не трогаю" in r.out


def test_чужой_файл_с_тем_же_именем_не_мешает_остальной_установке(tmp_path):
    m = machine(tmp_path)
    link_of(m).parent.mkdir(parents=True)
    link_of(m).write_text("чужое", encoding="utf-8")
    assert m.run().code == 0
    assert sorted(os.listdir(m.units))[0].startswith("flyarchive-") and (m.home / "dsh.patch.yml").is_file()


# ── настройка bin_dir ───────────────────────────────────────────
def test_пустая_настройка_bin_dir_ссылку_не_кладёт_и_каталог_не_создаёт(tmp_path):
    m = machine(tmp_path)
    r = m.run("--no-start", FLYARCHIVE_BIN_DIR="")
    assert r.code == 0 and not (m.user / ".local").exists() and "bin_dir" in r.out and "создана ссылка" not in r.out


def test_настройка_bin_dir_задаёт_каталог_команд_из_файла_и_из_окружения(tmp_path):
    m = Machine(tmp_path, dsh=True, settings_file={"bin_dir": "~/мои-команды"})
    assert m.run("--no-start").code == 0
    assert (m.user / "мои-команды" / "flyarchive").is_symlink() and not (m.user / ".local").exists()
    n = Machine(tmp_path / "второй", dsh=True)
    assert n.run("--no-start", FLYARCHIVE_BIN_DIR=str(tmp_path / "вне-дома")).code == 0
    assert (tmp_path / "вне-дома" / "flyarchive").is_symlink()


def test_настройка_bin_dir_в_справочнике_с_умолчанием_и_пустым_значением():
    import settings as S
    spec = S.SCHEMA["bin_dir"]
    assert (spec.type, spec.default) == ("path", "~/.local/bin") and "пусто" in spec.note
    assert S.load(env={"HOME": "/h", "FLYARCHIVE_HOME": "/a"})["bin_dir"] == "/h/.local/bin"
    assert S.load(env={"HOME": "/h", "FLYARCHIVE_HOME": "/a", "FLYARCHIVE_BIN_DIR": ""})["bin_dir"] == ""
    assert S.load(env={"HOME": "/h", "FLYARCHIVE_HOME": "/a", "FLYARCHIVE_BIN_DIR": "~/bin"}).source("bin_dir") == "env"


def test_относительный_bin_dir_отказ_как_у_других_путей(tmp_path):
    m = machine(tmp_path)
    r = m.run("--no-start", FLYARCHIVE_BIN_DIR="относительный/путь")
    assert r.code == 2 and "bin_dir" in r.err and not (m.user / ".local").exists()


# ── пробный прогон ──────────────────────────────────────────────
def test_пробный_прогон_говорит_что_было_бы_сделано_и_ничего_не_пишет(tmp_path):
    m = machine(tmp_path)
    before = tree(m.user)
    r = m.run("--dry-run")
    assert r.code == 0, r.err
    assert tree(m.user) == before and not (m.user / ".local").exists()
    assert "создал бы" in "\n".join(command_lines(r.out)) and str(link_of(m)) in r.out
    assert f'export PATH="{m.user / ".local" / "bin"}:$PATH"' in r.out


def test_пробный_прогон_с_готовой_ссылкой_говорит_уже_есть(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start").code == 0
    before = tree(m.user / ".local")
    r = m.run("--dry-run")
    assert "уже есть" in r.out and tree(m.user / ".local") == before


def test_пробный_прогон_json_называет_ссылку_цель_и_состояние(tmp_path):
    m = machine(tmp_path)
    data = m.run("--dry-run", "--json").json()
    folder = str(m.user / ".local" / "bin")
    launcher = str(m.home / "bin" / "flyarchive")
    assert data["command"] == {"folder": folder, "link": folder + "/flyarchive", "target": launcher, "state": "free", "on_path": False}
    assert not (m.user / ".local").exists()
    done = m.run("--no-start", "--json", PATH=on_path(m)).json()
    assert done["command"]["state"] == "created" and done["command"]["on_path"] is True
    again = m.run("--no-start", "--json", PATH=on_path(m)).json()
    assert again["command"]["state"] == "exists"
    foreign = Machine(tmp_path / "ч", dsh=True)
    link_of(foreign).parent.mkdir(parents=True)
    link_of(foreign).write_text("чужое", encoding="utf-8")
    assert foreign.run("--no-start", "--json").json()["command"]["state"] == "foreign"
    quiet = Machine(tmp_path / "о", dsh=True)
    off = quiet.run("--no-start", "--json", FLYARCHIVE_BIN_DIR="").json()
    assert off["command"] == {"folder": None, "link": None, "target": str(quiet.home / "bin" / "flyarchive"), "state": "disabled", "on_path": None}


def test_ключ_no_start_и_no_plugin_ссылку_не_отменяют(tmp_path):
    m = machine(tmp_path)
    assert m.run("--no-start", "--no-plugin").code == 0 and link_of(m).is_symlink()


# ── файлы служб прежние ─────────────────────────────────────────
def test_файлы_служб_одни_и_те_же_с_ссылкой_и_без_неё_и_повтор_их_не_меняет(tmp_path):
    with_link, without = Machine(tmp_path / "а", dsh=True), Machine(tmp_path / "б", dsh=True)
    assert with_link.run("--no-start").code == 0 and without.run("--no-start", FLYARCHIVE_BIN_DIR="").code == 0

    def units(m):
        return {n: m.unit(n).replace(str(m.home), "{home}").replace(str(m.bin), "{bin}") for n in sorted(os.listdir(m.units))}

    assert units(with_link) == units(without)
    first = {n: (with_link.units / n).read_bytes() for n in os.listdir(with_link.units)}
    assert with_link.run("--no-start").code == 0
    assert {n: (with_link.units / n).read_bytes() for n in os.listdir(with_link.units)} == first


def test_повторная_установка_целиком_ничего_не_меняет_кроме_журнала_systemctl(tmp_path):
    m = machine(tmp_path)
    assert m.run(PATH=on_path(m)).code == 0

    def state():                                               # содержимое файлов: службы и плагин пишутся заново теми же байтами, время изменения у них новое
        return {k: v and v[2] for k, v in tree(m.user).items() if k != "systemctl.log"}

    first = state()
    assert m.run(PATH=on_path(m)).code == 0
    assert state() == first


# ── итог установки: чего обязательного не хватает ───────────────
def test_установка_в_конце_одной_строкой_говорит_что_обязательное_на_месте(tmp_path, vectors):
    m = machine(tmp_path)
    r = m.run("--no-start", FLYARCHIVE_EMBED_URL=vectors["url"])
    assert r.code == 0 and r.out.rstrip("\n").splitlines()[-1] == str(M.make("doctor.tail_ok"))


def test_установка_в_конце_называет_чего_обязательного_не_хватает_и_кода_возврата_не_меняет(tmp_path):
    m = machine(tmp_path)
    r = m.run("--no-start", FLYARCHIVE_EMBED_URL=DEAD_VECTORS)
    assert r.code == 0 and r.out.rstrip("\n").splitlines()[-1] == str(M.make("doctor.tail_missing", names="служба векторов"))


def test_в_json_и_в_пробном_прогоне_строки_проверки_нет(tmp_path):
    m = machine(tmp_path)
    done = m.run("--no-start", "--json")
    assert "doctor" not in done.out and json.loads(done.out)["dry_run"] is False
    dry = m.run("--dry-run")
    assert "Окружение:" not in dry.out

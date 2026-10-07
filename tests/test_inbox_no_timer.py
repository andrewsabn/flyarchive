"""`flyarchive inbox set --no-timer`: настройка входящей папки без таймера systemd (FR-107).

Без ключа команда пишет службу и таймер разбора и включает таймер. Человеку, который «только пробует» (сценарий «первые пять минут» в README),
в системе не должно остаться включённого таймера. С ключом --no-timer настройки сохраняются, файлы служб не пишутся, systemctl не зовётся ни разу,
а команда одной строкой говорит, что таймер не трогали и как разбирать вручную. Без ключа поведение прежнее (его зовёт плагин).
Вместо systemctl — заглушка в PATH, которая пишет вызовы в журнал; настоящие службы и живой архив не трогаются.
"""
import json
import os

import pytest

import messages as M
from test_inbox_cli import cli, vectors  # noqa: F401 — настройка входящих в подменённом домашнем каталоге, заглушка systemctl

SKIPPED = str(M.make("inbox.timer_skipped"))


def unit_files(cli):
    return sorted(os.listdir(cli.units)) if os.path.isdir(cli.units) else []


# ── с ключом ────────────────────────────────────────────────────
def test_с_ключом_настройки_сохраняются_а_файлов_служб_нет_и_systemctl_не_звался(cli):
    code, out, err = cli("set", "--path", cli.box, "--period", "10", "--llm", "off", "--cloud", "off", "--no-timer")
    assert code == 0 and err == "", err
    saved = cli.config()
    assert (saved["inbox"], saved["period"], saved["llm"], saved["cloud"]) == (cli.box, 10, False, False)
    assert unit_files(cli) == [] and not os.path.exists(cli.units), "файлы служб записаны"
    assert cli.systemctl() == "", "systemctl вызывался: " + cli.systemctl()
    assert not os.path.exists(os.path.join(cli.user, ".config", "systemd")), "каталог служб создан"


def test_с_ключом_команда_говорит_одной_строкой_что_таймер_не_трогали_и_как_разбирать_вручную(cli):
    code, out, err = cli("set", "--path", cli.box, "--llm", "off", "--no-timer")
    assert code == 0
    said = [line for line in out.splitlines() if "таймер" in line.lower() and "не трогали" in line]
    assert said == [SKIPPED], out
    assert "flyarchive inbox run" in SKIPPED and "flyarchive install" in SKIPPED and "flyarchive inbox set" in SKIPPED
    assert f"Входящая папка: {cli.box}" in out, "остальной вывод прежний"


def test_с_ключом_строка_о_периоде_говорит_правду_разбор_вручную_а_период_записан_на_будущее(cli):
    code, out, err = cli("set", "--path", cli.box, "--period", "10", "--llm", "off", "--cloud", "off", "--no-timer")
    assert code == 0 and err == "", err
    assert "Разбор каждые" not in out, "с ключом таймера нет: разбор по расписанию не идёт, а строка говорила обратное"
    assert ("Разбор — вручную; период 10 мин записан на будущее. Проверка моделью: нет; запасная облачная модель: нет."
            in out.splitlines()), out
    assert out.index("Разбор — вручную; период") < out.index(SKIPPED), "порядок прежний: строка о периоде выше строки о таймере"


def test_с_ключом_и_прежним_периодом_строка_тоже_называет_период_из_настройки(cli):
    code, out, _ = cli("set", "--path", cli.box, "--llm", "off", "--no-timer")        # период по умолчанию — 30 минут
    assert code == 0 and "Разбор — вручную; период 30 мин записан на будущее." in out and "Разбор каждые" not in out


def test_с_ключом_и_systemctl_который_отказал_таймер_не_поминается_ни_stderr_ни_сбоем(cli):
    stub = os.path.join(os.path.dirname(cli.user), "bin", "systemctl")
    with open(stub, "w", encoding="utf-8") as f:
        f.write('#!/bin/bash\necho "$@" >> "$HOME/systemctl.log"\necho "Failed to connect to bus" >&2\nexit 1\n')
    code, out, err = cli("set", "--path", cli.box, "--no-timer")
    assert code == 0 and err == "" and cli.systemctl() == ""


def test_с_ключом_и_json_ответ_называет_что_таймер_пропущен_и_ошибки_таймера_нет(cli):
    code, out, err = cli("set", "--path", cli.box, "--no-timer", "--json")
    assert code == 0 and err == ""
    data = json.loads(out)
    assert data["timer_skipped"] is True and data["timer_error"] is None and data["inbox"] == cli.box
    assert unit_files(cli) == [] and cli.systemctl() == ""


def test_с_ключом_готовый_таймер_не_переписывается_и_не_выключается(cli):
    assert cli("set", "--path", cli.box, "--period", "5", "--llm", "off")[0] == 0
    timer_before = (cli.units / "flyarchive-inbox.timer").read_bytes()
    log_before = cli.systemctl()
    assert "enable --now flyarchive-inbox.timer" in log_before
    code, out, err = cli("set", "--period", "60", "--no-timer")
    assert code == 0 and cli.config()["period"] == 60, "настройка сохранена"
    assert (cli.units / "flyarchive-inbox.timer").read_bytes() == timer_before, "файл таймера переписан"
    assert cli.systemctl() == log_before, "systemctl вызван ещё раз"
    assert SKIPPED in out


def test_после_настройки_с_ключом_состояние_говорит_что_таймер_не_поставлен(cli):
    assert cli("set", "--path", cli.box, "--llm", "off", "--no-timer")[0] == 0
    code, out, _ = cli("status", "--json")
    assert code == 0 and json.loads(out)["timer"] is False


# ── без ключа прежнее ───────────────────────────────────────────
def test_без_ключа_пишутся_служба_и_таймер_и_таймер_включается(cli):
    code, out, err = cli("set", "--path", cli.box, "--period", "5", "--llm", "off")
    assert code == 0 and err == ""
    assert unit_files(cli) == ["flyarchive-inbox.service", "flyarchive-inbox.timer"]
    log = cli.systemctl()
    assert "--user daemon-reload" in log and "--user enable --now flyarchive-inbox.timer" in log
    assert "не трогали" not in out and SKIPPED not in out


def test_без_ключа_строка_о_периоде_прежняя_байт_в_байт(cli):
    code, out, err = cli("set", "--path", cli.box, "--period", "5", "--llm", "off", "--cloud", "off")
    assert code == 0 and err == ""
    assert "Разбор каждые 5 мин. Проверка моделью: нет; запасная облачная модель: нет.\n" in out
    assert "вручную; период" not in out and "записан на будущее" not in out


def test_без_ключей_настройка_включает_таймер_заново(cli):
    code, out, err = cli("set")
    assert code == 0 and unit_files(cli) == ["flyarchive-inbox.service", "flyarchive-inbox.timer"]
    assert "--user enable --now flyarchive-inbox.timer" in cli.systemctl()


def test_без_ключа_ответ_json_прежний_без_лишнего_ключа(cli):
    code, out, err = cli("set", "--path", cli.box, "--json")
    assert code == 0
    data = json.loads(out)
    assert "timer_skipped" not in data and data["timer_error"] is None
    assert set(data) == {"inbox", "period", "llm", "cloud", "stable_seconds", "threshold", "max_gb", "max_files", "max_ratio", "depth",
                         "vision", "vision_pages", "vision_minutes", "service_names", "service_root_names", "service_fate", "timer_error"}


def test_без_ключа_сбой_systemctl_по_прежнему_называется_в_stderr(cli):
    stub = os.path.join(os.path.dirname(cli.user), "bin", "systemctl")
    with open(stub, "w", encoding="utf-8") as f:
        f.write('#!/bin/bash\necho "Failed to connect to bus" >&2\nexit 1\n')
    code, out, err = cli("set", "--path", cli.box)
    assert code == 0 and err == "Таймер не включён: systemctl daemon-reload: Failed to connect to bus\n"


def test_справка_называет_ключ():
    import subprocess
    import sys
    flyarchive = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")
    r = subprocess.run([sys.executable, flyarchive, "inbox", "set", "--help"], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0 and "--no-timer" in r.stdout

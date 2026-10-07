"""Смена входящей папки из плагина: flyarchive inbox set --path P --must-exist [--json] (FR-75).

Каталог должен существовать, не лежать внутри архива и не содержать его; корень и домашний каталог — отказ. Каталога нет — отказ,
ничего не создано, настройка и таймер не тронуты. Смена записывается в журнал обращений. Без --must-exist команда работает как раньше.
"""
import json
import os

import pytest

import inbox as B
import journal as J
from test_inbox_cli import cli, vectors  # noqa: F401  — общая обвязка команды
from test_review_cli import error_line

UNITS = ("flyarchive-inbox.service", "flyarchive-inbox.timer")


def journal(cli):
    return J.Journal(os.path.join(cli.home, "logs", "access.jsonl")).tail(50)


def snapshot(cli):
    """Всё, что должна не тронуть отказанная смена: настройка, файлы таймера, вызовы systemctl."""
    cfg = os.path.join(cli.home, "inbox.json")
    return {"config": open(cfg, encoding="utf-8").read() if os.path.exists(cfg) else None,
            "units": {n: (cli.units / n).read_text(encoding="utf-8") if (cli.units / n).exists() else None for n in UNITS},
            "systemctl": cli.systemctl()}


# ── папка есть ──────────────────────────────────────────────────
def test_папка_есть_настройка_записана_таймер_поставлен(cli, tmp_path):
    target = tmp_path / "новая-входящая"
    target.mkdir()
    code, out, err = cli("set", "--path", str(target), "--must-exist")
    assert code == 0, err
    assert cli.config()["inbox"] == str(target) and f"Входящая папка: {target}" in out
    assert "--user daemon-reload" in cli.systemctl() and "--user enable --now flyarchive-inbox.timer" in cli.systemctl()


def test_папка_есть_с_json_ответ_настройка_и_итог_таймера(cli, tmp_path):
    target = tmp_path / "новая-входящая"
    target.mkdir()
    code, out, err = cli("set", "--path", str(target), "--must-exist", "--period", "5", "--json")
    assert code == 0 and err == "", err
    data = json.loads(out)
    assert data["inbox"] == str(target) and data["period"] == 5 and data["llm"] is True and data["timer_error"] is None
    assert data == {**cli.config(), "vision": True, "timer_error": None}      # в ответе — действующее значение описания (не задано: как llm), не null


def test_папка_есть_systemctl_отказал_с_json_итог_таймера_в_ответе_а_не_ошибка(cli, tmp_path):
    stub = os.path.join(os.path.dirname(cli.user), "bin", "systemctl")
    with open(stub, "w", encoding="utf-8") as f:
        f.write('#!/bin/bash\necho "Failed to connect to bus" >&2\nexit 1\n')
    target = tmp_path / "новая-входящая"
    target.mkdir()
    code, out, err = cli("set", "--path", str(target), "--must-exist", "--json")
    assert code == 0 and err == "", err
    assert json.loads(out)["timer_error"] == {"code": "inbox.timer_failed", "args": {"command": "daemon-reload", "why": "Failed to connect to bus"},
                                              "text": "systemctl daemon-reload: Failed to connect to bus"}
    assert cli.config()["inbox"] == str(target)


# ── папки нет ───────────────────────────────────────────────────
def test_каталога_нет_отказ_с_кодом_каталог_не_создан_настройка_и_таймер_не_тронуты(cli, tmp_path):
    assert cli("set", "--path", cli.box, "--period", "10")[0] == 0           # папка уже настроена, таймер стоит
    before = snapshot(cli)
    missing = str(tmp_path / "нет-такой")
    code, out, err = cli("set", "--path", missing, "--must-exist", "--period", "5", "--json")
    assert code == 1 and out == "" and error_line(err) == {"code": "inbox.folder_missing", "args": {"path": missing},
                                                          "text": f"входящей папки нет: {missing}"}
    assert not os.path.exists(missing) and snapshot(cli) == before


def test_каталога_нет_и_настройки_раньше_не_было_ничего_не_создано(cli, tmp_path):
    missing = str(tmp_path / "нет-такой" / "и-этого")
    code, out, err = cli("set", "--path", missing, "--must-exist")
    assert code == 1 and out == "" and err == f"ошибка: входящей папки нет: {missing}\n"
    assert not os.path.exists(os.path.dirname(missing)) and not os.path.exists(os.path.join(cli.home, "inbox.json"))
    assert not cli.units.exists() and cli.systemctl() == ""


def test_вместо_каталога_файл_отказ_так_же(cli, tmp_path):
    file = tmp_path / "файл.txt"
    file.write_text("не каталог", encoding="utf-8")
    code, out, err = cli("set", "--path", str(file), "--must-exist", "--json")
    assert code == 1 and out == "" and error_line(err)["code"] == "inbox.folder_missing"
    assert file.read_text(encoding="utf-8") == "не каталог" and not os.path.exists(os.path.join(cli.home, "inbox.json"))


def test_без_must_exist_каталог_создаётся_как_раньше(cli, tmp_path):
    target = str(tmp_path / "создастся")
    code, out, err = cli("set", "--path", target)
    assert code == 0, err
    assert os.path.isdir(target) and cli.config()["inbox"] == target


# ── внутри архива, корень и домашний каталог ────────────────────
def test_папка_внутри_архива_отказ_и_ничего_не_тронуто(cli):
    inside = os.path.join(cli.home, "corpus", "новое")
    os.makedirs(inside)
    code, out, err = cli("set", "--path", inside, "--must-exist", "--json")
    assert code == 1 and out == "" and error_line(err)["code"] == "inbox.folder_inside_archive"
    assert error_line(err)["args"] == {"archive": os.path.realpath(cli.home)}
    assert not os.path.exists(os.path.join(cli.home, "inbox.json")) and cli.systemctl() == ""


def test_сам_архив_и_каталог_над_архивом_отказ(cli, tmp_path):
    for path in (cli.home, str(tmp_path)):                  # tmp_path содержит каталог user, а в нём архив
        code, out, err = cli("set", "--path", path, "--must-exist", "--json")
        assert code == 1 and out == "" and error_line(err)["code"] == "inbox.folder_inside_archive", path
    assert not os.path.exists(os.path.join(cli.home, "inbox.json"))


@pytest.mark.parametrize("where", ["корень", "домашний каталог"])
def test_корень_и_домашний_каталог_отказ_с_кодом(cli, where):
    path = os.sep if where == "корень" else cli.user          # HOME команды в тесте — каталог user
    code, out, err = cli("set", "--path", path, "--must-exist", "--json")
    assert code == 1 and out == "" and error_line(err) == {
        "code": "inbox.folder_too_wide", "args": {"path": path}, "text": f"входящей папкой не может быть корень или домашний каталог: {path}"}
    assert not os.path.exists(os.path.join(cli.home, "inbox.json")) and cli.systemctl() == ""


def test_домашний_каталог_отказ_и_когда_архив_лежит_не_в_нём(tmp_path, monkeypatch):
    """Домашний каталог не содержит архив, но всё равно не годится: в него складывают всё подряд."""
    user, home = tmp_path / "user", tmp_path / "srv" / "flyarchive"
    user.mkdir()
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    with pytest.raises(B.InboxError) as e:
        B.require_folder(str(user))
    assert (e.value.message.code, e.value.message.args) == ("inbox.folder_too_wide", {"path": str(user)})
    with pytest.raises(B.InboxError) as e:
        B.require_folder(os.sep)
    assert e.value.message.code == "inbox.folder_too_wide"
    B.require_folder(str(tmp_path))                             # не корень, не домашний, существует: годится
    link = tmp_path / "ссылка-на-домашний"
    link.symlink_to(user)
    with pytest.raises(B.InboxError) as e:
        B.require_folder(str(link))                             # ссылка на домашний каталог — тот же домашний каталог
    assert e.value.message.code == "inbox.folder_too_wide"
    assert home.is_dir()


# ── журнал обращений ────────────────────────────────────────────
def test_смена_папки_записана_в_журнал_служба_cli_клиент_владелец_старый_и_новый_адрес(cli, tmp_path):
    target = tmp_path / "новая-входящая"
    target.mkdir()
    assert cli("set", "--path", cli.box, "--must-exist")[0] == 0           # папка уже настроена: это и есть старый адрес
    code, out, err = cli("set", "--path", str(target), "--must-exist")
    assert code == 0, err
    *_, last = journal(cli)
    assert (last["server"], last["client"], last["tool"], last["status"], last["outcome"]) == ("cli", "владелец", "inbox set", 0, "ok")
    assert last["params"] == {"old": cli.box, "new": str(target)}


def test_первая_смена_старый_адрес_это_папка_по_умолчанию(cli, tmp_path):
    target = tmp_path / "новая-входящая"
    target.mkdir()
    cli("set", "--path", str(target), "--must-exist")
    (rec,) = [r for r in journal(cli) if r["tool"] == "inbox set"]
    assert rec["params"] == {"old": os.path.join(cli.home, "входящие"), "new": str(target)}


def test_отказ_смены_тоже_в_журнале_со_статусом_один(cli, tmp_path):
    missing = str(tmp_path / "нет-такой")
    cli("set", "--path", missing, "--must-exist")
    (rec,) = [r for r in journal(cli) if r["tool"] == "inbox set"]
    assert (rec["server"], rec["client"], rec["status"]) == ("cli", "владелец", 1)
    assert rec["outcome"] == f"отказ: входящей папки нет: {missing}" and rec["params"] == {"old": os.path.join(cli.home, "входящие"), "new": missing}


def test_та_же_папка_не_смена_и_в_журнал_не_пишется(cli, tmp_path):
    target = tmp_path / "новая-входящая"
    target.mkdir()
    cli("set", "--path", str(target), "--must-exist")
    cli("set", "--path", str(target), "--must-exist", "--period", "5")
    assert len([r for r in journal(cli) if r["tool"] == "inbox set"]) == 1 and cli.config()["period"] == 5


def test_без_must_exist_журнал_как_раньше_не_пишется(cli, tmp_path):
    cli("set", "--path", str(tmp_path / "создастся"))
    assert [r for r in journal(cli) if r["tool"] == "inbox set"] == []


def test_must_exist_без_path_ничего_не_проверяет_и_не_пишет(cli):
    code, out, err = cli("set", "--must-exist", "--period", "5")
    assert code == 0, err
    assert cli.config()["period"] == 5 and [r for r in journal(cli) if r["tool"] == "inbox set"] == []


def test_в_журнале_нет_ничего_кроме_адресов(cli, tmp_path):
    target = tmp_path / "новая-входящая"
    target.mkdir()
    cli("set", "--path", str(target), "--must-exist", "--llm", "off", "--threshold", "5")
    (rec,) = [r for r in journal(cli) if r["tool"] == "inbox set"]
    assert set(rec["params"]) == {"old", "new"}

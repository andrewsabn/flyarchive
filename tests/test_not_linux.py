"""На другой системе команда отвечает одной строкой, а не ошибкой импорта (FR-107).

FlyArchive работает на Linux (fcntl, resource, bwrap, systemctl --user). На другой системе любая подкоманда, кроме `--help`, отвечает одной строкой
«FlyArchive работает на Linux; под Windows — в WSL2» и кодом 2. Проверка — функция от имени системы (doctor.refusal), поэтому тест не подменяет sys.platform
всему процессу: функцию зовут с разными именами, а команду как процесс — с platform.system(), которое подменено только в дочернем процессе (Archive.hide).
"""
import os

import pytest

import doctor as D
import messages as M
from archivekit import Archive

LINE = "FlyArchive работает на Linux; под Windows — в WSL2"


@pytest.fixture
def archive(tmp_path):
    return Archive(tmp_path)


@pytest.mark.parametrize("name", ["Windows", "Darwin", "FreeBSD", "CYGWIN_NT-10.0", "Java", ""])
def test_функция_на_любой_системе_кроме_linux_возвращает_строку_отказа(name):
    message = D.refusal(name)
    assert message.code == "cli.not_linux" and str(message) == LINE == str(M.make("cli.not_linux"))


def test_функция_на_linux_ничего_не_возвращает():
    assert D.refusal("Linux") is None


def test_без_имени_система_берётся_у_процесса_и_тут_это_linux():
    assert D.refusal() is None


@pytest.mark.parametrize("args", [("inbox", "status"), ("token", "list"), ("init",), ("doctor",), ("known", "status"), ("install", "--dry-run"),
                                  ("check", "нет-такого", "--into", "куда"), ("journal",), ("open",), ("perms", "check")])
def test_любая_подкоманда_на_другой_системе_одна_строка_и_код_2_без_ошибки_импорта(archive, args):
    code, out, err = archive(*args, **archive.hide(system="Windows"))
    assert code == 2 and out == "" and err == LINE + "\n", (out, err)
    assert "fcntl" not in err and "Traceback" not in err
    assert not os.path.exists(archive.home), "на неподдерживаемой системе ничего не заводится"


def test_ключ_json_ответ_тот_же_одна_строка(archive):
    code, out, err = archive("inbox", "status", "--json", **archive.hide(system="Darwin"))
    assert (code, out, err) == (2, "", LINE + "\n")


def test_справка_работает_и_на_другой_системе(archive):
    for args in (("--help",), ("init", "--help"), ("install", "--help"), ("doctor", "--help")):
        code, out, err = archive(*args, **archive.hide(system="Windows"))
        assert code == 0 and "usage:" in out and err == "", args


def test_на_linux_подкоманды_работают_как_раньше(archive):
    code, out, err = archive("token", "list")
    assert code == 0 and out == "Токенов нет.\n" and err == ""
    code, out, err = archive("--help", **archive.hide(system="Linux"))
    assert code == 0 and "doctor" in out


def test_проверка_идёт_до_создания_чего_бы_то_ни_было_даже_служебный_объект_доступа_не_строится(archive):
    code, out, err = archive("token", "add", "ноутбук", **archive.hide(system="Windows"))
    assert code == 2 and err == LINE + "\n" and not os.path.exists(archive.home)

"""Права на файлы архива: NFR-01, часть без прав администратора.

Каталог архива и всё в нём закрыто от других пользователей машины. Код служб лежит в каталоге репозитория, а не в архиве: проверка прав каталога
кода внутри архива осталась от прежней раскладки и убрана.
"""
import os
import stat
import subprocess
import sys

import pytest

import perms as P

FLYARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")
needs_modes = pytest.mark.skipif(os.name == "nt", reason="права POSIX")


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


@pytest.fixture
def home(tmp_path):
    """Архив, каким он был до проверки прав: всё открыто на чтение всем."""
    h = tmp_path / "flyarchive"
    for d in ("corpus/mail/2020", "index/lance", "logs", "out", "staging", "secrets"):
        (h / d).mkdir(parents=True)
    files = {"corpus/mail/2020/письмо.eml": 0o644, "index/known.sqlite": 0o644, "index/lance/data.lance": 0o664,
             "logs/access.jsonl": 0o644, "logs/search.log": 0o644, "secrets/tokens.json": 0o600,
             "README.md": 0o644}
    for rel, m in files.items():
        (h / rel).write_text("x", encoding="utf-8")
        os.chmod(h / rel, m)
    for d in ("corpus", "corpus/mail", "corpus/mail/2020", "index", "index/lance", "logs", "out", "staging"):
        os.chmod(h / d, 0o755)
    os.chmod(h / "secrets", 0o700)
    os.chmod(h, 0o755)
    return str(h)


def paths(problems, home):
    return sorted(os.path.relpath(p.path, home) for p in problems)


# ── проверка ────────────────────────────────────────────────────
@needs_modes
def test_открытый_архив_проверка_называет_каталоги(home):
    found = P.audit(home)
    assert paths(found, home) == [".", "corpus", "index", "logs", "logs/access.jsonl", "logs/search.log", "out", "staging"]
    assert all("открыт" in p.text for p in found)


@needs_modes
def test_закрытый_архив_проверка_чиста(home):
    P.fix(home)
    assert P.audit(home) == []


@needs_modes
def test_исправление_закрывает_каталоги_и_не_трогает_лишнего(home):
    changed = P.fix(home)
    assert changed == 8
    for d in ("", "corpus", "index", "logs", "out", "staging", "secrets"):
        assert mode(os.path.join(home, d)) == 0o700, d
    assert mode(os.path.join(home, "logs", "access.jsonl")) == 0o600
    assert mode(os.path.join(home, "corpus", "mail", "2020", "письмо.eml")) == 0o644   # вглубь — только с ключом


@needs_modes
def test_каталог_с_именем_tools_внутри_архива_ничем_не_особый_закрывается_как_любой_другой(home):
    """Раньше проверка знала такой каталог: ему разрешалось быть открытым на чтение, а запись другим находилась отдельным сообщением про код служб."""
    for folder, m in (("tools", 0o755), ("tools/вложенный", 0o755)):
        os.makedirs(os.path.join(home, folder), exist_ok=True)
        os.chmod(os.path.join(home, folder), m)
    code = os.path.join(home, "tools", "gate.py")
    with open(code, "w") as f:
        f.write("x")
    os.chmod(code, 0o666)
    P.fix(home)
    os.chmod(os.path.join(home, "tools"), 0o777)
    found = P.audit(home)
    assert paths(found, home) == ["tools"] and found[0].text == "открыт другим пользователям: права 777" and found[0].want == 0o700
    assert all("изменить" not in p.text and "код служб" not in p.text for p in found)
    P.fix(home)
    assert mode(os.path.join(home, "tools")) == 0o700 and P.audit(home) == []
    assert mode(code) == 0o666, "вглубь без ключа не идёт"
    assert [p for p in paths(P.audit(home, deep=True), home) if p.startswith("tools")] == ["tools/gate.py", "tools/вложенный"]


def test_у_проверки_нет_ветки_про_каталог_кода():
    for name in ("CODE", "OTHERS_WRITE"):
        assert not hasattr(P, name), name
    with open(P.__file__, encoding="utf-8") as f:
        source = f.read()
    assert "изменить код" not in source and "tools" not in source.split('"""', 2)[2], "в коде проверки нет особого места для каталога кода"


@needs_modes
def test_проверка_вглубь_находит_каждый_открытый_файл(home):
    P.fix(home)
    found = P.audit(home, deep=True)
    assert paths(found, home) == ["corpus/mail", "corpus/mail/2020", "corpus/mail/2020/письмо.eml", "index/known.sqlite",
                                  "index/lance", "index/lance/data.lance"]


@needs_modes
def test_исправление_вглубь_закрывает_всё_и_сохраняет_права_владельца(home):
    script = os.path.join(home, "staging", "run.sh")
    with open(script, "w") as f:
        f.write("#!/bin/sh")
    os.chmod(script, 0o755)
    P.fix(home, deep=True)
    assert P.audit(home, deep=True) == []
    assert mode(os.path.join(home, "corpus", "mail", "2020")) == 0o700
    assert mode(os.path.join(home, "corpus", "mail", "2020", "письмо.eml")) == 0o600
    assert mode(os.path.join(home, "index", "lance", "data.lance")) == 0o600
    assert mode(script) == 0o700                                             # исполняемость для владельца осталась


@needs_modes
def test_ссылки_наружу_не_раскрываются(home, tmp_path):
    outside = tmp_path / "чужое"
    outside.mkdir()
    (outside / "файл").write_text("x", encoding="utf-8")
    os.chmod(outside, 0o755)
    os.chmod(outside / "файл", 0o644)
    os.symlink(outside, os.path.join(home, "corpus", "ссылка"))
    os.symlink(outside / "файл", os.path.join(home, "corpus", "mail", "ссылка-на-файл"))
    P.fix(home, deep=True)
    assert mode(outside) == 0o755 and mode(outside / "файл") == 0o644
    assert P.audit(home, deep=True) == []


@needs_modes
def test_чужой_владелец_находится_и_не_исправляется(home):
    P.fix(home, deep=True)
    other = os.getuid() + 1
    found = P.audit(home, uid=other)
    assert "." in paths(found, home) and all("владелец" in p.text and p.want is None for p in found)
    assert P.fix(home, uid=other) == 0 and P.audit(home, uid=other) == found


def test_нет_каталога_архива_ошибка(tmp_path):
    with pytest.raises(P.PermsError):
        P.audit(str(tmp_path / "нет"))
    with pytest.raises(P.PermsError):
        P.fix(str(tmp_path / "нет"))


# ── команда flyarchive perms ──────────────────────────────────────
def cli(home, *args):
    r = subprocess.run([sys.executable, FLYARCHIVE, "perms", *args], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "FLYARCHIVE_HOME": home, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, r.stdout, r.stderr


@needs_modes
def test_команда_проверки_код_1_пока_открыто_и_0_после_исправления(home):
    # ответ команды идёт в сообщение отказа: без него красный тест говорит только «код 1» (так было в полном прогоне 2026-10-06)
    code, out, err = cli(home, "check")
    assert code == 1 and "corpus" in out and "8" in out, (code, out, err)
    code, out, err = cli(home, "fix")
    assert code == 0 and "8" in out, (code, out, err)
    again = cli(home, "check")
    assert again[0] == 0, again
    code, out, err = cli(home, "check", "--deep")
    assert code == 1 and "письмо.eml" in out, (code, out, err)
    fixed, checked = cli(home, "fix", "--deep"), cli(home, "check", "--deep")
    assert fixed[0] == 0 and checked[0] == 0, (fixed, checked)


@needs_modes
def test_команда_не_заваливает_экран_списком(home):
    for i in range(60):
        with open(os.path.join(home, "corpus", "mail", f"{i:03d}.eml"), "w") as f:
            f.write("x")
    cli(home, "fix")
    code, out, _ = cli(home, "check", "--deep")
    assert code == 1 and len(out.splitlines()) < 30 and "66" in out            # 60 писем и 6 прежних


def test_команда_без_каталога_архива_ошибка(tmp_path):
    code, out, err = cli(str(tmp_path / "нет"), "check")
    assert code == 1 and "нет" in err


@needs_modes
def test_команда_исправления_код_1_когда_остались_чужие_файлы(home, monkeypatch, capsys):
    """Чужой файл владелец исправить не может: команда об этом говорит, а не молчит."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("flyarchive_cli", FLYARCHIVE)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("flyarchive_cli", loader))
    loader.exec_module(module)
    monkeypatch.setattr(module.tokens, "HOME", home)
    me = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: me + 1)                       # все файлы архива стали «чужими»
    assert module.main(["perms", "fix"]) == 1
    assert "Осталось нарушений" in capsys.readouterr().err
    assert module.main(["perms", "check"]) == 1


# ── текст успеха говорит только о проверенном ─────────────────────
CODE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")       # каталог, где лежит команда
OK_LINE = "Права в порядке: архив закрыт от других пользователей."
CODE_LINE = f"Каталог кода ({CODE_DIR}) эта проверка не смотрит: закрой его от записи другим сам — chmod -R go-w {CODE_DIR}."


def isolated(home, *args, command=FLYARCHIVE):
    """Команда perms с подменённым домашним каталогом: настоящий домашний каталог не виден."""
    user = os.path.join(os.path.dirname(home), "user")
    os.makedirs(user, exist_ok=True)
    env = {**os.environ, "HOME": user, "FLYARCHIVE_HOME": home, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, command, "perms", *args], capture_output=True, text=True, encoding="utf-8", env=env)
    return r.returncode, r.stdout, r.stderr


@needs_modes
def test_проверка_закрытого_архива_говорит_только_о_проверенном_и_называет_каталог_кода(home):
    P.fix(home)
    code, out, err = isolated(home, "check")
    assert (code, err) == (0, ""), (out, err)
    assert out.splitlines() == [OK_LINE, CODE_LINE], out
    assert "код служб" not in out.split("\n")[0] and "изменить" not in out, "успех не обещает неизменности кода"


@needs_modes
def test_проверка_вглубь_закрытого_архива_печатает_те_же_две_строки(home):
    P.fix(home, deep=True)
    code, out, err = isolated(home, "check", "--deep")
    assert (code, out.splitlines(), err) == (0, [OK_LINE, CODE_LINE], ""), (out, err)


@needs_modes
def test_после_исправления_каталог_кода_назван_один_раз_и_рядом_нет_обещания_про_код_служб(home):
    code, out, err = isolated(home, "fix")
    assert (code, err) == (0, ""), (out, err)
    assert out.splitlines() == ["Исправлено: 8.", CODE_LINE], out
    assert out.count("Каталог кода") == 1 and "код служб" not in out
    code, out, err = isolated(home, "fix")                      # повторное исправление: делать нечего, но напоминание то же и одно
    assert (code, out.splitlines()) == (0, ["Исправлено: 0.", CODE_LINE]), (out, err)


@needs_modes
def test_пока_архив_открыт_проверка_не_объявляет_права_в_порядке(home):
    code, out, err = isolated(home, "check")
    assert code == 1 and "Права в порядке" not in out and "Нарушений: 8" in out, (out, err)


@needs_modes
def test_каталог_кода_берётся_там_где_лежит_команда_и_через_ссылку_называется_настоящий(home, tmp_path):
    P.fix(home)
    link = tmp_path / "bin" / "flyarchive"
    link.parent.mkdir()
    os.symlink(FLYARCHIVE, link)
    code, out, err = isolated(home, "check", command=str(link))
    assert (code, out.splitlines()) == (0, [OK_LINE, CODE_LINE]), (out, err)
    assert str(link.parent) not in out, "называется каталог настоящего файла команды, а не ссылки"


@needs_modes
def test_путь_каталога_кода_с_пробелом_в_подсказке_взят_в_кавычки(home, tmp_path):
    """Подсказку `chmod -R go-w <путь>` копируют в оболочку как есть: путь с пробелом без кавычек стал бы двумя аргументами."""
    import shutil
    P.fix(home)
    code_dir = tmp_path / "мой код" / "tools"
    shutil.copytree(CODE_DIR, code_dir, ignore=shutil.ignore_patterns("__pycache__"))
    code, out, err = isolated(home, "check", command=str(code_dir / "flyarchive"))
    assert code == 0 and f"chmod -R go-w '{code_dir}'." in out, (out, err)
    assert f"Каталог кода ({code_dir})" in out

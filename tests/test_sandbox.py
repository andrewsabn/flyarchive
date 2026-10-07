"""Песочница для оболочек внешних моделей: FR-61.

Оболочка видит свой рабочий каталог, отдельный домашний каталог и сеть (MCP на петле).
Архива, домашнего каталога владельца, чужих процессов и сокетов служб для неё нет.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import sandbox as SB
from sandboxkit import needs_sandbox

FLYARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")
needs_bwrap = needs_sandbox              # пропуск, когда песочницу нельзя создать: нет bwrap, он не запускается или пространства имён запрещены
OWNER_ENV = {"USER": "владелец", "TERM": "xterm", "LANG": "C.UTF-8", "SECRET_KEY": "s3cr3t-value",
             "FLYARCHIVE_LOCAL_TOKEN": "ba_local-value", "MY_TOKEN": "ba_client-value"}


class Box:
    pass


@pytest.fixture
def box(tmp_path):
    """Подставные архив, домашний каталог владельца и рабочий каталог оболочки."""
    owner = tmp_path / "owner"
    archive = owner / "flyarchive"
    for d in ("corpus/mail", "index", "secrets", "logs"):
        (archive / d).mkdir(parents=True)
    (archive / "corpus" / "mail" / "письмо.eml").write_text("тайна переписки", encoding="utf-8")
    (archive / "secrets" / "local.token").write_text("ba_секрет", encoding="utf-8")
    (owner / ".ssh").mkdir()
    (owner / ".ssh" / "id_ed25519").write_text("ключ владельца", encoding="utf-8")
    work = owner / "projects" / "задача"
    work.mkdir(parents=True)
    (work / "заметка.txt").write_text("привет" + chr(10), encoding="utf-8")
    tools = owner / ".npm-global"
    (tools / "bin").mkdir(parents=True)
    b = Box()
    b.tmp, b.archive, b.owner, b.work, b.tools = tmp_path, str(archive), str(owner), str(work), str(tools)
    b.state = str(tmp_path / "state")

    def build(argv=("true",), **kw):
        kw.setdefault("workdir", b.work)
        kw.setdefault("archive", b.archive)
        kw.setdefault("owner_home", b.owner)
        kw.setdefault("state", b.state)
        kw.setdefault("environ", {**OWNER_ENV, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": b.owner})
        return SB.build(list(argv), **kw)

    def sh(script, **kw):
        cmd, env = build(["bash", "-c", script], **kw)
        return subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)

    b.build, b.sh = build, sh
    return b


def place(box, where):
    """Путь из таблицы случаев: archive/… — в архиве, owner/… — в домашнем каталоге владельца."""
    for word, base in (("archive", box.archive), ("owner", box.owner)):
        if where.startswith(word):
            return base + where[len(word):]
    return where if os.path.isabs(where) else os.path.join(box.work, where)


def sources(cmd):
    """Что с машины привязано внутрь песочницы: пары (ключ, путь на машине)."""
    return [(cmd[i], cmd[i + 1]) for i, a in enumerate(cmd) if a in ("--bind", "--ro-bind", "--dev-bind")]


# ── командная строка bwrap ──────────────────────────────────────
def test_архив_и_домашний_каталог_владельца_внутрь_не_привязаны(box):
    cmd, _ = box.build(ro=[box.tools])
    for _, src in sources(cmd):
        assert not SB.inside(src, box.archive) and not SB.inside(box.archive, src), src
        assert src != box.owner and not SB.inside(box.owner, src), src
    assert "/" not in [src for _, src in sources(cmd)] and "/home" not in [src for _, src in sources(cmd)]
    assert not any(src.startswith(("/mnt/c", "/run", "/var")) for _, src in sources(cmd))


def test_рабочий_каталог_на_запись_и_текущий(box):
    cmd, _ = box.build(["bash", "-c", "pwd"])
    assert ("--bind", box.work) in sources(cmd)
    assert cmd[cmd.index("--chdir") + 1] == box.work
    assert cmd[cmd.index("--") + 1:] == ["bash", "-c", "pwd"]


def test_вместо_домашнего_каталога_владельца_отдельный(box):
    cmd, env = box.build()
    i = cmd.index(box.state)
    assert cmd[i - 1] == "--bind" and cmd[i + 1] == box.owner          # свой каталог на месте домашнего
    assert env["HOME"] == box.owner and os.path.isdir(box.state)
    assert os.stat(box.state).st_mode & 0o077 == 0


def test_сеть_общая_остальное_отделено(box):
    cmd, _ = box.build()
    assert "--unshare-all" in cmd and "--share-net" in cmd and "--die-with-parent" in cmd
    assert cmd.index("--share-net") < cmd.index("--")


def test_система_только_для_чтения(box):
    cmd, _ = box.build()
    assert ("--ro-bind", "/usr") in sources(cmd) and ("--ro-bind", "/etc") in sources(cmd)
    assert ("--bind", "/usr") not in sources(cmd) and ("--bind", "/etc") not in sources(cmd)
    assert cmd[cmd.index("/tmp") - 1] == "--tmpfs" and cmd[cmd.index("/run") - 1] == "--tmpfs"


def test_каталог_с_программами_привязан_только_для_чтения_и_попал_в_путь(box):
    cmd, env = box.build(ro=[box.tools])
    assert ("--ro-bind", box.tools) in sources(cmd) and ("--bind", box.tools) not in sources(cmd)
    assert env["PATH"].split(":")[0] == os.path.join(box.tools, "bin")
    assert cmd.index(box.state) < cmd.index(box.tools) < cmd.index("--chdir")     # поверх домашнего, до рабочего


@pytest.mark.parametrize("blocked, present", [(True, False), (False, True)])
def test_отдельный_сеанс_терминала_если_ядро_не_запрещает_подстановку_ввода(box, blocked, present):
    cmd, _ = box.build(tiocsti_blocked=blocked)
    assert ("--new-session" in cmd) is present


def test_пустая_команда_отклоняется(box):
    with pytest.raises(SB.SandboxError):
        box.build([])


# ── что нельзя отдать в песочницу ───────────────────────────────
@pytest.mark.parametrize("where", ["archive", "archive/corpus", "archive/secrets", "owner", "owner/..", "/", "нет-такого",
                                   "ссылка-на-архив", "ссылка-на-корпус"])
def test_негодный_рабочий_каталог_отклоняется(box, where):
    os.symlink(box.archive, os.path.join(box.work, "ссылка-на-архив"))
    os.symlink(os.path.join(box.archive, "corpus"), os.path.join(box.work, "ссылка-на-корпус"))
    with pytest.raises(SB.SandboxError):
        box.build(workdir=place(box, where))


@pytest.mark.parametrize("where", ["archive", "archive/index", "owner", "owner/..", "/", "нет-такого"])
def test_негодный_каталог_для_чтения_отклоняется(box, where):
    with pytest.raises(SB.SandboxError):
        box.build(ro=[place(box, where)])


@pytest.mark.parametrize("state", ["archive/sandbox", "archive", "owner"])
def test_домашний_каталог_песочницы_не_в_архиве_и_не_домашний_владельца(box, state):
    with pytest.raises(SB.SandboxError):
        box.build(state=place(box, state))
    assert not os.path.exists(os.path.join(box.archive, "sandbox"))


def test_домашний_каталог_владельца_не_отдаётся_даже_когда_архив_в_другом_месте(box):
    """После переезда архива к служебному пользователю в домашнем каталоге остаются ключи владельца."""
    elsewhere = box.tmp / "srv" / "flyarchive"
    elsewhere.mkdir(parents=True)
    for bad in (box.owner, os.path.dirname(box.owner)):
        with pytest.raises(SB.SandboxError, match="домашний"):
            box.build(workdir=bad, archive=str(elsewhere))
        with pytest.raises(SB.SandboxError, match="домашний"):
            box.build(ro=[bad], archive=str(elsewhere))
        with pytest.raises(SB.SandboxError, match="домашний"):
            box.build(state=bad, archive=str(elsewhere))               # иначе оболочка получила бы его на запись
    cmd, _ = box.build(archive=str(elsewhere))                     # каталог задачи внутри домашнего — можно
    assert ("--bind", box.work) in sources(cmd)


def test_другие_защищённые_каталоги_тоже_не_отдаются(box):
    """Копия корпуса на другом диске: ни сама, ни каталог над ней."""
    copy = box.tmp / "диск" / "копия-корпуса"
    (copy / "mail").mkdir(parents=True)
    for bad in (str(copy), str(copy / "mail"), str(box.tmp / "диск")):
        with pytest.raises(SB.SandboxError):
            box.build(workdir=bad, protected=[str(copy)])
    neighbour = box.tmp / "диск" / "проект"
    neighbour.mkdir()
    cmd, _ = box.build(workdir=str(neighbour), protected=[str(copy)])
    assert ("--bind", str(neighbour)) in sources(cmd)


# ── окружение ───────────────────────────────────────────────────
def test_ключи_владельца_внутрь_не_попадают(box):
    cmd, env = box.build()
    assert "SECRET_KEY" not in env and "FLYARCHIVE_LOCAL_TOKEN" not in env and "MY_TOKEN" not in env
    assert not any("s3cr3t" in v or v.startswith("ba_") for v in env.values())
    assert env["USER"] == "владелец" and env["TERM"] == "xterm" and env["LANG"] == "C.UTF-8"


def test_названная_переменная_передаётся_и_значение_не_в_командной_строке(box):
    cmd, env = box.build(pass_env=["MY_TOKEN"])
    assert env["MY_TOKEN"] == "ba_client-value"
    assert not any("ba_client-value" in part for part in cmd)          # командную строку видят все процессы машины
    assert "--setenv" not in cmd


@pytest.mark.parametrize("name", ["FLYARCHIVE_LOCAL_TOKEN", "НЕТ_ТАКОЙ", "MY_TOKEN=ba_x", "", "A B"])
def test_негодная_переменная_отклоняется(box, name):
    with pytest.raises(SB.SandboxError):
        box.build(pass_env=[name])


def test_значение_вместо_имени_отклоняется_и_в_сообщение_не_попадает(box):
    """Ошибку видят на экране и в логах: значение токена в неё попадать не должно."""
    with pytest.raises(SB.SandboxError) as e:
        box.build(pass_env=["MY_TOKEN=ba_typed-by-mistake"])
    assert "ba_typed-by-mistake" not in str(e.value) and "MY_TOKEN" in str(e.value) and "имя" in str(e.value)


# ── настоящая песочница ─────────────────────────────────────────
@needs_bwrap
def test_файлы_архива_внутри_не_существуют(box):
    letter = os.path.join(box.archive, "corpus", "mail", "письмо.eml")
    r = box.sh(f"cat '{letter}'; cat '{box.archive}/secrets/local.token'; ls '{box.archive}'")
    assert r.returncode != 0 and "тайна" not in r.stdout and "ba_секрет" not in r.stdout
    assert r.stderr.count("No such file") == 3


@needs_bwrap
def test_домашний_каталог_владельца_внутри_пуст(box):
    r = box.sh("ls -A ~; cat ~/.ssh/id_ed25519")
    assert r.stdout.split() == ["projects"] and "ключ владельца" not in r.stdout and r.returncode != 0


@needs_bwrap
def test_рабочий_каталог_виден_и_доступен_на_запись(box):
    r = box.sh("cat заметка.txt && echo готово > итог.txt && pwd")
    assert r.returncode == 0 and r.stdout.split() == ["привет", box.work]
    with open(os.path.join(box.work, "итог.txt"), encoding="utf-8") as f:
        assert f.read().strip() == "готово"


@needs_bwrap
def test_свой_домашний_каталог_сохраняется_между_запусками(box):
    assert box.sh("mkdir -p ~/.config && echo настройка > ~/.config/оболочка").returncode == 0
    assert box.sh("cat ~/.config/оболочка").stdout.strip() == "настройка"
    assert os.path.exists(os.path.join(box.state, ".config", "оболочка"))


@needs_bwrap
def test_служба_на_петле_доступна(box):
    """MCP слушает петлю: сеть у песочницы общая с машиной."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        r = box.sh(f"python3 -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:{port}/').read().decode())\"")
        assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr
    finally:
        server.shutdown()


@needs_bwrap
def test_в_окружении_внутри_только_названное(box):
    r = box.sh("env", pass_env=["MY_TOKEN"])
    names = {line.split("=", 1)[0] for line in r.stdout.splitlines()}
    assert "MY_TOKEN=ba_client-value" in r.stdout.splitlines()
    assert "SECRET_KEY" not in names and "FLYARCHIVE_LOCAL_TOKEN" not in names and "s3cr3t" not in r.stdout


@needs_bwrap
def test_чужие_процессы_не_видны(box):
    """Иначе окружение служб с токенами читалось бы из /proc."""
    r = box.sh(f"ls /proc | grep -c '^[0-9]'; cat /proc/{os.getpid()}/environ")
    assert int(r.stdout.split()[0]) <= 4 and r.returncode != 0


@needs_bwrap
@pytest.mark.parametrize("path", ["/run/user", "/run/WSL", "/var/run/docker.sock", "/mnt/c", "archive", "/root"])
def test_сокеты_служб_и_диски_снаружи_не_видны(box, path):
    assert box.sh(f"test -e {place(box, path)}").returncode != 0


@needs_bwrap
def test_система_внутри_не_изменяется(box):
    r = box.sh("touch /usr/x; touch /etc/x; test -r /etc/resolv.conf && echo dns")
    assert r.stderr.count("Read-only file system") == 2 and r.stdout.strip() == "dns"


@needs_bwrap
@pytest.mark.skipif(not shutil.which("sudo"), reason="нет sudo")
def test_sudo_внутри_не_работает(box):
    assert box.sh("sudo -n true").returncode != 0


@needs_bwrap
def test_код_возврата_команды_возвращается(box):
    assert SB.run(["bash", "-c", "exit 7"], workdir=box.work, archive=box.archive, owner_home=box.owner, state=box.state,
                  environ={"PATH": os.environ["PATH"]}) == 7


# ── команда flyarchive run ────────────────────────────────────────
@pytest.fixture
def cli(box):
    def run(*args, env=None):
        full = {"PATH": os.environ["PATH"], "HOME": box.owner, "FLYARCHIVE_HOME": box.archive, "PYTHONIOENCODING": "utf-8",
                "USER": "владелец", "SECRET_KEY": "s3cr3t-value", "MY_TOKEN": "ba_client-value", **(env or {})}
        r = subprocess.run([sys.executable, FLYARCHIVE, "run", *args], env=full, capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        return r.returncode, r.stdout, r.stderr

    return run


def journal(box):
    path = os.path.join(box.archive, "logs", "access.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@needs_bwrap
def test_команда_запускает_в_песочнице_и_пишет_журнал(box, cli):
    code, out, err = cli("--dir", box.work, "--name", "оболочка-1", "--env", "MY_TOKEN", "--", "bash", "-c",
                         f"cat заметка.txt; echo $MY_TOKEN; ls '{box.archive}' 2>/dev/null; echo ${{SECRET_KEY:-пусто}}; exit 3")
    assert code == 3 and out.split() == ["привет", "ba_client-value", "пусто"]
    (rec,) = journal(box)
    assert (rec["server"], rec["client"], rec["tool"], rec["status"]) == ("sandbox", "оболочка-1", "run", 3)
    assert rec["params"] == {"cmd": "bash", "dir": box.work} and "ba_client" not in json.dumps(rec, ensure_ascii=False)
    assert os.path.isdir(os.path.join(box.owner, ".local", "share", "flyarchive-sandbox", "оболочка-1"))     # каталог из настроек (FR-98)


@needs_bwrap
@pytest.mark.parametrize("mask", [0o022, 0o002, 0o027])
def test_оболочка_в_песочнице_получает_маску_запустившего_а_не_закрытую_маску_команды(box, mask):
    """Команда закрывает маску процесса ради файлов архива (NFR-01а); оболочка модели работает в рабочем каталоге человека, и её файлы
    получают те же права, что и без песочницы. Журнал запуска пишет сама команда — он остаётся закрытым."""
    env = {"PATH": os.environ["PATH"], "HOME": box.owner, "FLYARCHIVE_HOME": box.archive, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, FLYARCHIVE, "run", "--dir", box.work, "--name", "маска", "--", "bash", "-c", "umask; touch новый.txt"],
                       env=env, capture_output=True, text=True, encoding="utf-8", timeout=60, umask=mask)
    assert r.returncode == 0 and int(r.stdout.strip(), 8) == mask, r.stderr
    assert os.stat(os.path.join(box.work, "новый.txt")).st_mode & 0o777 == 0o666 & ~mask
    assert os.stat(os.path.join(box.archive, "logs", "access.jsonl")).st_mode & 0o777 == 0o600


@needs_bwrap
def test_у_каждой_оболочки_свой_домашний_каталог(box, cli):
    cli("--dir", box.work, "--name", "первая", "--", "bash", "-c", "echo один > ~/след")
    code, out, _ = cli("--dir", box.work, "--name", "вторая", "--", "bash", "-c", "cat ~/след")
    assert code != 0 and "один" not in out


@pytest.mark.parametrize("args, word", [
    (["--dir", "АРХИВ/corpus", "--", "true"], "архив"),
    (["--dir", "ВЛАДЕЛЕЦ", "--", "true"], "содержит архив"),
    (["--dir", "РАБОЧИЙ", "--env", "FLYARCHIVE_LOCAL_TOKEN", "--", "true"], "служебн"),
    (["--dir", "РАБОЧИЙ", "--env", "NO_SUCH_VAR", "--", "true"], "не задана"),
    (["--dir", "РАБОЧИЙ", "--name", "../x", "--", "true"], "имя"),
    (["--dir", "РАБОЧИЙ"], "команд"),
])
def test_команда_отказывает_до_запуска(box, cli, args, word):
    args = [a.replace("АРХИВ", box.archive).replace("ВЛАДЕЛЕЦ", box.owner).replace("РАБОЧИЙ", box.work) for a in args]
    code, out, err = cli(*args, env={"FLYARCHIVE_LOCAL_TOKEN": "ba_local-value"})
    assert code not in (0, 3) and word in err.lower() and out == ""
    assert "ba_local" not in err


@needs_bwrap
def test_список_защищённых_каталогов_из_файла_в_архиве(box, cli):
    copy = box.tmp / "диск" / "копия"
    copy.mkdir(parents=True)
    with open(os.path.join(box.archive, "protected-paths"), "w", encoding="utf-8") as f:
        f.write(f"# копия корпуса на другом диске\n{copy}\n\n")
    code, _, err = cli("--dir", str(box.tmp / "диск"), "--", "true")
    assert code != 0 and "копия" in err
    assert cli("--dir", box.work, "--", "true")[0] == 0

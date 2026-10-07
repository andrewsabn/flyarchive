"""Песочница для оболочек внешних моделей (FR-61).

Оболочка внешней модели запускается через bwrap и видит:
    систему — только для чтения;
    рабочий каталог — на запись;
    отдельный домашний каталог, свой у каждой оболочки;
    сеть — чтобы достать до MCP на петле и до своей модели.
Не видит: каталог архива, домашний каталог владельца, диски Windows, сокеты служб
(docker, шина systemd, взаимодействие WSL с Windows) и чужие процессы.

Токены закрывают сеть, а не файлы: процесс под тем же пользователем читает корпус напрямую.
Песочница закрывает именно этот путь. Сеть она не ограничивает.

    flyarchive run --dir ~/projects/задача --env FLYARCHIVE_TOKEN --ro ~/.npm-global -- оболочка
"""
import os
import re
import shutil
import subprocess

BLOCKED_ENV = ("FLYARCHIVE_LOCAL_TOKEN",)          # служебный токен локального DSH наружу не отдаётся
BASE_ENV = ("USER", "LOGNAME", "TERM", "COLORTERM", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SYSTEM_DIRS = ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PROTECTED_FILE = "protected-paths"               # в каталоге архива: что ещё нельзя отдавать, путь на строку


class SandboxError(Exception):
    pass


def inside(path, root):
    """path — это root или лежит под ним."""
    root = root.rstrip("/") or "/"
    return path == root or path.startswith("/" if root == "/" else root + "/")


def kernel_blocks_tiocsti():
    """Ядро запрещает подставлять ввод в терминал — отдельный сеанс терминала не нужен."""
    try:
        with open("/proc/sys/dev/tty/legacy_tiocsti") as f:
            return f.read().strip() == "0"
    except OSError:
        return False


def read_protected(archive):
    """Дополнительные защищённые каталоги из файла в архиве, например копия корпуса на другом диске."""
    try:
        with open(os.path.join(archive, PROTECTED_FILE), encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
    except OSError:
        return []


def _allowed(path, what, roots, owner_home):
    """Каталог можно отдать песочнице: он не в архиве, не над ним и не домашний каталог владельца."""
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise SandboxError(f"{what}: нет такого каталога: {path}")
    for root in roots:
        if inside(real, root):
            raise SandboxError(f"{what} {real} лежит внутри архива ({root}): в песочнице архив не виден")
        if inside(root, real):
            raise SandboxError(f"{what} {real} содержит архив ({root}): вместе с ним архив стал бы виден")
    if inside(owner_home, real):
        raise SandboxError(f"{what} {real} — домашний каталог владельца или каталог над ним: "
                           f"выбери каталог задачи, а не весь домашний")
    return real


def build(argv, workdir, archive, owner_home, state, environ=None, pass_env=(), ro=(), protected=(),
          tiocsti_blocked=None, bwrap="bwrap"):
    """Командная строка bwrap и окружение для неё.

    Значения переменных идут через окружение, а не через командную строку: её видят все процессы машины.
    """
    if not argv:
        raise SandboxError("не задана команда: flyarchive run [ключи] -- команда")
    environ = os.environ if environ is None else environ
    roots = [os.path.realpath(p) for p in (archive, *protected) if p]
    owner_home = os.path.realpath(owner_home)
    workdir = _allowed(workdir, "рабочий каталог", roots, owner_home)
    ro = [_allowed(p, "каталог для чтения", roots, owner_home) for p in ro]

    state_real = os.path.realpath(state)
    for root in roots:
        if inside(state_real, root) or inside(root, state_real):
            raise SandboxError(f"домашний каталог песочницы {state_real} пересекается с архивом ({root})")
    if inside(owner_home, state_real):
        raise SandboxError(f"домашний каталог песочницы {state_real} — домашний каталог владельца или каталог над ним")
    os.makedirs(state_real, mode=0o700, exist_ok=True)
    os.chmod(state_real, 0o700)

    env = {name: environ[name] for name in BASE_ENV if environ.get(name)}
    for name in pass_env:
        if not ENV_NAME.fullmatch(name or ""):
            shown = (name or "").split("=")[0][:40]         # значение в сообщение не попадает
            raise SandboxError(f"--env принимает имя переменной, а не значение: «{shown}». "
                               f"Значение берётся из окружения, в командной строке его увидели бы все процессы")
        if name in BLOCKED_ENV:
            raise SandboxError(f"{name} — служебный токен локального DSH, оболочке внешней модели он не передаётся. "
                               f"Выпусти ей свой: flyarchive token add <имя>")
        if name not in environ:
            raise SandboxError(f"переменная {name} не задана в окружении")
        env[name] = environ[name]
    bins = [p if os.path.basename(p) == "bin" else os.path.join(p, "bin") for p in ro]
    env["PATH"] = ":".join([p for p in bins if os.path.isdir(p)] + [os.path.join(owner_home, ".local", "bin"), SYSTEM_PATH])
    env["HOME"] = owner_home

    cmd = [bwrap, "--unshare-all", "--share-net", "--die-with-parent", "--hostname", "flyarchive-sandbox"]
    if not (kernel_blocks_tiocsti() if tiocsti_blocked is None else tiocsti_blocked):
        cmd.append("--new-session")              # иначе процесс мог бы подставить ввод в терминал владельца
    cmd += ["--ro-bind", "/usr", "/usr"]
    for d in SYSTEM_DIRS:
        if os.path.islink(d):
            cmd += ["--symlink", os.readlink(d), d]
        elif os.path.isdir(d):
            cmd += ["--ro-bind", d, d]
    cmd += ["--ro-bind", "/etc", "/etc"]
    resolv = os.path.realpath("/etc/resolv.conf")            # в WSL это ссылка наружу из /etc
    if not inside(resolv, "/etc") and os.path.isfile(resolv):
        cmd += ["--ro-bind", resolv, resolv]
    cmd += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run", "--dir", "/var/tmp",
            "--bind", state_real, owner_home]
    for p in ro:
        cmd += ["--ro-bind", p, p]
    cmd += ["--bind", workdir, workdir, "--chdir", workdir, "--", *argv]
    return cmd, env


def run(argv, umask=-1, **kw):
    """Запускает команду в песочнице и возвращает её код возврата. umask — маска прав для команды: `flyarchive run` передаёт маску
    запустившего, потому что свою команда архива закрывает, а оболочка создаёт файлы в рабочем каталоге человека (-1 — не менять)."""
    cmd, env = build(argv, **kw)
    if not shutil.which(cmd[0]):
        raise SandboxError("не найден bwrap: sudo apt install bubblewrap")
    try:
        return subprocess.call(cmd, env=env, umask=umask)
    except KeyboardInterrupt:
        return 130

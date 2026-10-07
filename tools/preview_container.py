"""Контейнер для просмотра Office, HTML и метафайлов (FR-78): настройка образа, команда docker, запуск, уборка.

    image(home)                     образ из настройки `preview.json` (имя@sha256:дайджест) или None: не записан, испорчен, тег без дайджеста
    setup(home, image=None)         `flyarchive preview setup`: скачать образ, прочитать его дайджест, записать настройку
    command(…)                      аргументы `docker run` одним списком: без сети, только чтение, все права сняты, пределы памяти и процессов
    run(docker, image, …)           запуск, срок, остановка и удаление по имени, разбор причины отказа
    reap() / reap_later()           убрать контейнеры `flyarchive-preview-<uuid>` старше 5 минут; reap_later не ждёт docker

Образ запускается только по дайджесту: тег без дайджеста не принимается ни при записи, ни при чтении. Аргументы docker собираются списком,
без оболочки; строки из файла в них не попадают: имя входного файла — `data.<расширение>`, расширение из букв и цифр. Остановка клиента `docker`
контейнер не убивает, поэтому по сроку родитель делает `docker kill` и `docker rm -f` по имени, а внутри самого контейнера стоит `timeout -s KILL`.
`timeout` при убийстве даёт 137, как и убийство за память: память узнаётся по событию `oom` docker.

Сбой — ContainerError с сообщением из каталога; уборка не бросает ничего.
"""
import datetime
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

import messages

IMAGE_DEFAULT = "gotenberg/gotenberg:8"
SETTING = "preview.json"
NAME_PREFIX = "flyarchive-preview-"
NAME_RE = re.compile(re.escape(NAME_PREFIX) + r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
IMAGE_RE = re.compile(r"[a-z0-9][a-z0-9._/:-]{0,199}@sha256:[0-9a-f]{64}")        # имя@sha256:<64 шестнадцатеричных>; имя не начинается с «-»
SOURCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}")                    # то, что владелец называет в --image
FILE_RE = re.compile(r"data\.[a-z0-9]{1,10}")
SETTING_MAX = 64 << 10
RUN_SECONDS = 120                    # срок преобразования; стоит внутри контейнера (timeout -s KILL)
CLIENT_SECONDS = 150                 # сколько родитель ждёт клиента docker; потом kill и rm -f по имени
CALL_SECONDS = 20                    # срок на короткий вызов клиента docker (inspect, kill, rm, events)
PULL_SECONDS = 1800                  # срок на скачивание образа
STALE_SECONDS = 300                  # контейнер старше этого убирается при старте команды
REAP_SECONDS = 5                     # срок на каждый вызов уборки: зависший docker ей не помеха, а просмотр её не ждёт
MEMORY_GB = 2
POLL = 0.25                          # как часто проверяется размер выходного каталога


class ContainerError(messages.CodedError):
    """Отказ контейнерного слоя с сообщением из каталога."""


class Silent(Exception):
    """Клиент docker не вернулся за срок вызова."""


def docker_path():
    return shutil.which("docker")


def why(code, text):
    """Код возврата и последняя строка stderr только печатными знаками ASCII: пояснение, а не вывод чужой программы целиком."""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    head = f"exit {code}"
    return head + ": " + "".join(c if " " <= c <= "~" else "?" for c in lines[-1])[:120] if lines else head


# ── настройка образа ────────────────────────────────────────────
def image(home):
    """Образ из `<архив>/preview.json` или None. Читается при каждом вызове; негодное значение — как «не записан»."""
    try:
        fd = os.open(os.path.join(home, SETTING), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, SETTING_MAX + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(raw) > SETTING_MAX:
        return None
    try:
        saved = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    value = saved.get("image") if isinstance(saved, dict) else None
    return value if isinstance(value, str) and IMAGE_RE.fullmatch(value) else None


def save_image(home, digest, source):
    """Настройка: файл 0600 через временный файл и os.replace, как `inbox.json`. Негодный дайджест не пишется."""
    if not IMAGE_RE.fullmatch(digest):
        raise ValueError("дайджест образа негоден")
    os.makedirs(home, mode=0o700, exist_ok=True)
    path = os.path.join(home, SETTING)
    tmp = f"{path}.{os.getpid()}.tmp"
    body = {"image": digest, "from": source, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(body, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── вызовы docker ───────────────────────────────────────────────
def _call(docker, args, seconds):
    """Короткий вызов клиента docker: (код возврата, stdout, stderr). Срок вышел — Silent; docker не запустился — ContainerError."""
    try:
        r = subprocess.run([docker, *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        raise Silent()
    except OSError as e:
        raise ContainerError(messages.make("preview.docker_failed", why=type(e).__name__))
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def _silent():
    return ContainerError(messages.make("preview.docker_silent", seconds=CALL_SECONDS))


def check_image(docker, ref):
    """Образ с этим дайджестом есть на машине. Скачивать нельзя: `docker run` по дайджесту, которого нет, пошёл бы в сеть сам."""
    try:
        code, _, err = _call(docker, ["image", "inspect", "--format", "{{.Id}}", ref], CALL_SECONDS)
    except Silent:
        raise _silent()
    if code == 0:
        return
    if "no such image" in err.lower() or "no such object" in err.lower():
        raise ContainerError(messages.make("preview.image_missing", image=ref))
    raise ContainerError(messages.make("preview.docker_failed", why=why(code, err)))


def command(ref, name, indir, outdir, filename, uid, gid):
    """Аргументы `docker run` (без самого docker): жёсткий набор ограничений, менять его по ходу нельзя; `--pull never` — образа нет, значит отказ, а не поход в сеть. Подставляется только то, что собрал родитель: имя контейнера,
    владелец, две папки, образ и имя входного файла `data.<расширение>`."""
    if (not isinstance(uid, int) or not isinstance(gid, int) or not IMAGE_RE.fullmatch(ref) or not NAME_RE.fullmatch(name)
            or not FILE_RE.fullmatch(filename) or ":" in indir or ":" in outdir):
        raise ValueError("аргументы контейнера негодны")
    return ["run", "--rm", "--init", "--pull", "never", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp:size=512m", "--shm-size", "256m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", f"{MEMORY_GB}g", "--memory-swap",
            f"{MEMORY_GB}g", "--cpus", "2", "-u", f"{uid}:{gid}", "-e", "HOME=/tmp", "-v", f"{indir}:/in:ro", "-v", f"{outdir}:/out",
            "--entrypoint", "", ref, "timeout", "-s", "KILL", str(RUN_SECONDS), "soffice", "-env:UserInstallation=file:///tmp/lo", "--headless",
            "--convert-to", "pdf", "--outdir", "/out", f"/in/{filename}"]


def _drain(stream, keep):
    while True:
        chunk = stream.read(65_536)
        if not chunk:
            return
        keep += chunk
        del keep[:-1024]


def _stop(proc, docker, name):
    """Клиент убит вместе с группой, потом контейнер остановлен и удалён по имени: остановка клиента контейнер не убивает."""
    for stop in (lambda: os.killpg(proc.pid, signal.SIGKILL), proc.kill):
        try:
            stop()
        except OSError:
            pass
    proc.wait()
    for args in (["kill", name], ["rm", "-f", name]):
        try:
            _call(docker, args, CALL_SECONDS)
        except (Silent, ContainerError):
            pass


def oom_killed(docker, name, since):
    """Убит ли контейнер за память: событие oom о нём, пока он существовал (у контейнера с --rm потом не спросить)."""
    try:
        code, out, _ = _call(docker, ["events", "--since", f"{since:.3f}", "--until", f"{time.time():.3f}", "--filter", f"container={name}",
                                      "--filter", "event=oom", "--format", "{{.Action}}"], CALL_SECONDS)
    except (Silent, ContainerError):
        return False
    return code == 0 and "oom" in out.split()


def run(docker, ref, indir, outdir, filename, limit, size, uid=None, gid=None):
    """Один контейнер: ждёт клиента не дольше CLIENT_SECONDS. size() — сколько занято в выходном каталоге: больше limit — контейнер останавливается.
    Всё хорошо — возвращает None, иначе ContainerError."""
    name = NAME_PREFIX + str(uuid.uuid4())
    argv = [docker, *command(ref, name, indir, outdir, filename, os.getuid() if uid is None else uid, os.getgid() if gid is None else gid)]
    since, started = time.time() - 1, time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        raise ContainerError(messages.make("preview.docker_failed", why=type(e).__name__))
    tail = bytearray()
    reader = threading.Thread(target=_drain, args=(proc.stderr, tail), daemon=True)
    reader.start()
    stopped, last = None, 0.0
    try:
        while stopped is None:
            try:
                proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            if now - started >= CLIENT_SECONDS:
                stopped = "timeout"
            elif now - last >= POLL:
                last = now
                if size() > limit:
                    stopped = "output"
        if stopped:
            _stop(proc, docker, name)
    except BaseException:
        _stop(proc, docker, name)
        raise
    finally:
        reader.join(timeout=2)
        proc.stderr.close()
    if stopped:
        if stopped == "output":
            raise ContainerError(messages.make("preview.convert_output_big", limit=limit >> 20))
        raise ContainerError(messages.make("preview.convert_timeout", seconds=RUN_SECONDS))
    code = proc.returncode
    if code == 0:
        return
    text = tail.decode("utf-8", "replace")
    if code == 124:
        raise ContainerError(messages.make("preview.convert_timeout", seconds=RUN_SECONDS))
    if code == 125:                                          # сам docker не смог запустить: служба, образ, ключи
        raise ContainerError(messages.make("preview.docker_failed", why=why(code, text)))
    if code == 137:
        if oom_killed(docker, name, since):
            raise ContainerError(messages.make("preview.convert_memory", gb=MEMORY_GB))
        if time.monotonic() - started >= RUN_SECONDS - 1:
            raise ContainerError(messages.make("preview.convert_timeout", seconds=RUN_SECONDS))
    raise ContainerError(messages.make("preview.convert_failed", why=why(code, text)))


# ── уборка ──────────────────────────────────────────────────────
CREATED = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)")


def _epoch(text):
    found = CREATED.fullmatch(text.strip())
    if not found:
        return None
    base, fraction, zone = found.groups()
    try:
        when = datetime.datetime.fromisoformat(base + ("." + (fraction + "000000")[:6] if fraction else "") + ("+00:00" if zone == "Z" else zone))
    except ValueError:
        return None
    return when.timestamp()


def containers(docker, seconds=CALL_SECONDS):
    """[(имя, время создания)] своих контейнеров: имя `flyarchive-preview-<uuid>` целиком. Чужое по подстроке имени не берётся."""
    code, out, _ = _call(docker, ["ps", "-a", "--no-trunc", "--filter", f"name={NAME_PREFIX}", "--format", "{{.Names}}"], seconds)
    names = [n for n in out.split() if NAME_RE.fullmatch(n)] if code == 0 else []
    if not names:
        return []
    _, out, _ = _call(docker, ["inspect", "--type", "container", "--format", "{{.Name}} {{.Created}}", *names], seconds)
    rows = []
    for line in out.splitlines():
        name, _, created = line.strip().partition(" ")
        name, when = name.lstrip("/"), _epoch(created)
        if NAME_RE.fullmatch(name) and when is not None:
            rows.append((name, when))
    return rows


def stale(rows, now):
    return [name for name, created in rows if now - created > STALE_SECONDS]


def remove(docker, names, seconds=CALL_SECONDS):
    return _call(docker, ["rm", "-f", *names], seconds)[0] if names else 0


def reap(now=None):
    """Убирает свои контейнеры старше STALE_SECONDS. Число убранных; никакой сбой наружу не выходит и ничего не печатает."""
    docker = docker_path()
    if docker is None:
        return 0
    try:
        old = stale(containers(docker, REAP_SECONDS), time.time() if now is None else now)
        remove(docker, old, REAP_SECONDS)
        return len(old)
    except Exception:                                        # уборка — дело второстепенное: просмотру она не мешает
        return 0


def reap_later():
    """Уборка отдельным процессом, который команда не ждёт: вызов docker стоит десятые доли секунды, а просмотр текста их не должен платить."""
    if docker_path() is None:
        return
    try:
        subprocess.Popen([sys.executable, os.path.realpath(__file__), "reap"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


# ── flyarchive preview setup ──────────────────────────────────────
def _pick(text, source):
    """Дайджест из RepoDigests: тот, что того же репозитория, что назван в source, иначе первый годный."""
    try:
        found = json.loads(text)
    except ValueError:
        return None
    good = [d for d in found if isinstance(d, str) and IMAGE_RE.fullmatch(d)] if isinstance(found, list) else []
    repo = re.sub(r":[^/:]+$", "", source.split("@")[0])
    return next((d for d in good if d.split("@")[0] == repo), good[0] if good else None)


def setup(home, source=None):
    """Скачивает образ (без `source`: образ по умолчанию; с ним — уже скачанный, сеть не трогается), читает дайджест и записывает настройку.
    Возвращает {"image", "from", "pulled"}. Прежняя настройка при отказе остаётся как была."""
    ref = IMAGE_DEFAULT if source is None else source
    if not SOURCE_RE.fullmatch(ref):
        raise ContainerError(messages.make("preview.setup_bad_image", image=ref[:80]))
    docker = docker_path()
    if docker is None:
        raise ContainerError(messages.make("preview.no_docker"))
    if source is None:
        try:
            code, _, err = _call(docker, ["pull", "--quiet", ref], PULL_SECONDS)
        except Silent:
            raise ContainerError(messages.make("preview.setup_pull_failed", why=f"no answer in {PULL_SECONDS} s"))
        if code != 0:
            raise ContainerError(messages.make("preview.setup_pull_failed", why=why(code, err)))
    try:
        code, out, err = _call(docker, ["image", "inspect", "--format", "{{json .RepoDigests}}", ref], CALL_SECONDS)
    except Silent:
        raise _silent()
    if code != 0:
        if "no such" in err.lower():
            raise ContainerError(messages.make("preview.setup_no_image", image=ref))
        raise ContainerError(messages.make("preview.docker_failed", why=why(code, err)))
    digest = _pick(out, ref)
    if digest is None:
        raise ContainerError(messages.make("preview.setup_no_digest", image=ref))
    save_image(home, digest, ref)
    return {"image": digest, "from": ref, "pulled": source is None}


if __name__ == "__main__":
    if sys.argv[1:] == ["reap"]:
        reap()

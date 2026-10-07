"""Просмотр через настоящий контейнер (FR-78): docker, образ `gotenberg/gotenberg:8` и LibreOffice настоящие, образцы маленькие.

Метка `container`. Тесты пропускаются с причиной, если нет docker, образа (его ставит `flyarchive preview setup`; тесты сами ничего не скачивают) или песочницы bwrap
(PDF от контейнера описывает и рисует рабочий процесс в ней). Образ запускается только по дайджесту, как в работе.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

import pytest

import gatekit as K
import preview as P
import preview_container as C
from test_preview_container_kit import SHA, leftovers, record_image, wait_for  # noqa: F401
from test_preview_kit import BATCH, env  # noqa: F401
from sandboxkit import OK as SANDBOX_OK, WHY as SANDBOX_WHY

FLYARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")
TAG = C.IMAGE_DEFAULT


def container_state():
    docker = shutil.which("docker")
    if docker is None:
        return None, "нет docker: настоящий контейнер на этой машине недоступен"
    try:
        r = subprocess.run([docker, "image", "inspect", "--format", "{{json .RepoDigests}}", TAG], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"docker не отвечает: {type(e).__name__}"
    if r.returncode != 0:
        return None, f"образа {TAG} на машине нет: `flyarchive preview setup` (docker pull {TAG}), тесты ничего не скачивают"
    found = [d for d in json.loads(r.stdout.decode("utf-8") or "[]") if isinstance(d, str) and d.startswith("gotenberg/gotenberg@sha256:")]
    return (found[0], "") if found else (None, f"у образа {TAG} нет дайджеста репозитория")


DIGEST_REAL, WHY = container_state()
pytestmark = [pytest.mark.container, pytest.mark.skipif(DIGEST_REAL is None, reason=WHY),
              pytest.mark.skipif(not SANDBOX_OK, reason=SANDBOX_WHY)]


@pytest.fixture
def real(env):
    record_image(env, DIGEST_REAL)
    return env


@pytest.fixture
def listener():
    """Порт на петле этой машины, на который ссылается образец: изнутри контейнера до него не дойти."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    sock.settimeout(0.3)

    class Listener:
        port = sock.getsockname()[1]

        @staticmethod
        def accepted():
            try:
                sock.accept()[0].close()
                return True
            except (socket.timeout, BlockingIOError):
                return False

    yield Listener
    sock.close()


def dims(png):
    import struct
    assert png[:8] == b"\x89PNG\r\n\x1a\n", png[:16]
    return struct.unpack(">II", png[16:24])


def page_text(env, data):
    """Текст первой страницы PDF, который контейнер положил в кэш под sha256 исходного файла."""
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open(os.path.join(env.cache, SHA(data), "base.pdf")) as doc:
        return doc[0].get_text()


HTML = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Проба</title><link rel='stylesheet' href='http://127.0.0.1:{port}/style.css'></head>"
        "<body><h1>Заголовок страницы</h1><p>Видимый текст.</p><img src='http://127.0.0.1:{port}/pixel.png'>"
        "<script>document.write('СЦЕНАРИЙ-ВЫПОЛНЕН'); new Image().src='http://127.0.0.1:{port}/beacon';</script>"
        "<iframe src='http://127.0.0.1:{port}/frame.html'></iframe></body></html>")

SAMPLES = {
    "docx": (K.docx_page("Docx line one."), "Docx line one."), "xlsx": (K.xlsx_sheet(), "Apples"), "pptx": (K.pptx_slide("Slide words"), "Slide words"),
    "rtf": (b"{\\rtf1\\ansi Rtf line one.}", "Rtf line one."), "csv": (b"name,qty\nApples,3\nPears,5\n", "Apples"),
    "emf": (K.emf_rect(), None), "wmf": (K.wmf_rect(), None), "doc": (K.OFFICE_DOC, "Sample document line one."), "xls": (K.OFFICE_XLS, "Apples"),
}


@pytest.mark.parametrize("type_", sorted(SAMPLES))
def test_настоящий_контейнер_тип_становится_страницами_png(real, type_):
    data, words = SAMPLES[type_]
    path = real.put("queue", f"образец.{type_}", data)
    started = time.monotonic()
    answer = P.show(real.home, "queue", path)
    if answer["kind"] == "none" and "preview.convert_timeout" in json.dumps(answer):
        # первый запуск контейнера в наборе холодный: на занятом диске он не укладывается в срок продукта, и продукт честно отвечает отказом со сроком.
        # Такой отказ повторяется один раз — второй запуск уже тёплый (владелец на вкладке нажал бы «Try again»). Любой другой отказ не повторяется.
        import warnings
        warnings.warn(f"{type_}: первое преобразование не уложилось в срок продукта, повтор")
        started = time.monotonic()
        answer = P.show(real.home, "queue", path)
    took = time.monotonic() - started
    assert answer["kind"] == "pages" and answer["pages"] >= 1 and answer["meta"]["type"] == type_ and answer["meta"]["sha256"] == SHA(data), answer
    # своя граница времени короче сроков продукта давала ложные сбои, когда диск занят соседней работой (первый запуск контейнера в наборе — холодный);
    # преобразование, не уложившееся в срок продукта, уже ловит строка выше: тогда вместо страниц приходит отказ со сроком
    assert took < C.RUN_SECONDS + P.WORKER_TIMEOUT
    png = P.page(real.home, "queue", path, "1")
    w, h = dims(png)
    assert w > 100 and h > 50 and w * h <= 4_000_000
    if words:
        assert words in page_text(real, data)
    assert real.cached()[:2] == [f"{SHA(data)}/base.pdf", f"{SHA(data)}/desc.json"] and leftovers(real) == []


def test_html_со_сценарием_и_внешними_ссылками_наружу_никто_не_обратился(real, listener):
    data = HTML.format(port=listener.port).encode("utf-8")
    path = real.put("queue", "страница.html", data)
    answer = P.show(real.home, "queue", path)
    assert answer["kind"] == "pages" and answer["meta"]["type"] == "html", answer
    assert dims(P.page(real.home, "queue", path, "1"))[0] > 100
    text = page_text(real, data)
    assert "Заголовок страницы" in text and "СЦЕНАРИЙ-ВЫПОЛНЕН" not in text                # сценарий не выполнился
    assert not listener.accepted()                                                       # ни картинку, ни стиль, ни кадр никто не запрашивал
    assert "<" not in json.dumps(answer, ensure_ascii=False)


def test_вложение_docx_из_письма_открывается_так_же_ключ_кэша_sha256_вложения(real):
    attachment = K.docx_page("Вложенный документ.")
    letter = real.put("queue", "письмо.eml", K.eml_with([("счёт.docx", attachment)]))
    mail = P.show(real.home, "queue", letter)
    assert mail["kind"] == "mail" and mail["mail"]["attachments"][0]["name"] == "счёт.docx"
    answer = P.show(real.home, "queue", letter, [0])
    assert answer["kind"] == "pages" and answer["meta"]["type"] == "docx" and answer["meta"]["sha256"] == SHA(attachment), answer
    assert P.page(real.home, "queue", letter, "1", [0])[:8] == b"\x89PNG\r\n\x1a\n"
    assert f"{SHA(attachment)}/base.pdf" in real.cached() and leftovers(real) == []


def test_битый_файл_none_с_причиной_и_контейнер_убран(real):
    docker = shutil.which("docker")
    before = {name for name, _ in C.containers(docker)}
    answer = P.show(real.home, "queue", real.put("queue", "битый.docx", K.docx_page()[:-40]))
    assert answer["kind"] == "none" and answer["note"]["code"].startswith("preview.") and real.cached() == [] and leftovers(real) == []
    assert {name for name, _ in C.containers(docker)} <= before                  # --rm: после запуска контейнера не остаётся


def test_одно_преобразование_второй_запрос_при_занятом_замке_получает_rendering(real):
    import fcntl
    os.makedirs(real.cache, exist_ok=True)
    fd = os.open(os.path.join(real.cache, ".render.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        before = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=30).stdout
        assert P.show(real.home, "queue", real.put("queue", "а.docx", K.docx_page())) == {"kind": "rendering"}
        assert subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=30).stdout == before
    finally:
        os.close(fd)


# ── сам docker: ключи, события, уборка ──────────────────────────
def restricted(tmp_path, script):
    """Настоящая команда контейнера с теми же ключами, но с другим содержимым: что контейнеру можно и чего нельзя."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    for d in (indir, outdir):
        d.mkdir(mode=0o700)
    (indir / "data.docx").write_bytes(b"x")
    argv = C.command(DIGEST_REAL, C.NAME_PREFIX + str(uuid.uuid4()), str(indir), str(outdir), "data.docx", os.getuid(), os.getgid())
    cut = argv.index(DIGEST_REAL) + 1
    r = subprocess.run([shutil.which("docker"), *argv[:cut], "bash", "-c", script], capture_output=True, text=True, timeout=120)
    return r, outdir


def test_ограничения_контейнера_действуют_корень_вход_сеть_и_права(tmp_path):
    script = ("touch /x 2>/dev/null && echo root-writable; touch /in/y 2>/dev/null && echo input-writable; echo hi > /out/z && echo out-ok; "
              "touch /tmp/t && echo tmp-ok; (exec 3<>/dev/tcp/1.1.1.1/80) 2>/dev/null && echo network; id -u; "
              "grep -c . /proc/self/status > /dev/null; grep CapEff /proc/self/status; ls /var/run/docker.sock 2>/dev/null; ls /root 2>&1 | head -1")
    r, outdir = restricted(tmp_path, script)
    out = r.stdout
    assert "root-writable" not in out and "input-writable" not in out and "network" not in out and "docker.sock" not in out
    assert "out-ok" in out and "tmp-ok" in out and (outdir / "z").read_text() == "hi\n"
    assert f"{os.getuid()}\n" in out and "CapEff:\t0000000000000000" in out


def test_память_и_процессы_ограничены_ключами(tmp_path):
    r, _ = restricted(tmp_path, "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/pids.max /sys/fs/cgroup/cpu.max")
    memory, pids, cpu = r.stdout.split("\n")[:3]
    assert int(memory) == 2 << 30 and int(pids) == 256 and cpu.startswith("200000 ")


def test_oom_узнаётся_по_событиям_docker_для_контейнера_с_rm(tmp_path):
    docker = shutil.which("docker")
    name = C.NAME_PREFIX + str(uuid.uuid4())
    since = time.time() - 1
    r = subprocess.run([docker, "run", "--rm", "--init", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp:size=16m", "--memory", "48m",
                        "--memory-swap", "48m", "--pids-limit", "64", "--entrypoint", "", DIGEST_REAL, "bash", "-c",
                        'x=$(head -c 120000000 /dev/zero | tr "\\0" a); echo ${#x}'], capture_output=True, text=True, timeout=120)
    assert r.returncode == 137
    assert C.oom_killed(docker, name, since) is True
    assert C.oom_killed(docker, C.NAME_PREFIX + str(uuid.uuid4()), since) is False


def start_sleeper(tmp_path, seconds):
    """Контейнер с теми же ключами, что в работе, но с командой `timeout -s KILL <seconds> sleep 300`: живёт, пока его не остановят."""
    docker = shutil.which("docker")
    name = C.NAME_PREFIX + str(uuid.uuid4())
    indir, outdir = tmp_path / f"in-{name[-6:]}", tmp_path / f"out-{name[-6:]}"
    for d in (indir, outdir):
        d.mkdir(mode=0o700)
    argv = C.command(DIGEST_REAL, name, str(indir), str(outdir), "data.docx", os.getuid(), os.getgid())
    proc = subprocess.Popen([docker, *argv[:argv.index(DIGEST_REAL) + 1], "timeout", "-s", "KILL", str(seconds), "sleep", "300"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    assert wait_for(lambda: name in dict(C.containers(docker)), 30), "контейнер не поднялся"
    return docker, name, proc


def test_остановка_клиента_контейнер_не_убивает_а_timeout_внутри_и_остановка_по_имени_убивают(tmp_path):
    docker, name, proc = start_sleeper(tmp_path, 5)
    os.killpg(proc.pid, signal.SIGKILL)                       # родитель убит, клиент docker вместе с ним
    proc.wait()
    time.sleep(1)
    assert name in dict(C.containers(docker))                  # контейнер жив: остановка клиента его не трогает
    assert wait_for(lambda: name not in dict(C.containers(docker)), 30)         # timeout -s KILL внутри кончил его, --rm убрал
    docker, name, proc = start_sleeper(tmp_path, 120)
    started = time.monotonic()
    C._stop(proc, docker, name)                                # то, что родитель делает по сроку: kill клиента, docker kill, docker rm -f
    # на занятой машине docker убирает контейнер не в тот же миг, что вернулась команда: ждём исчезновения, а не требуем его сразу
    assert wait_for(lambda: name not in dict(C.containers(docker)), 30), "контейнер не убран за 30 с после остановки по имени"
    assert time.monotonic() - started < 60


def test_список_и_удаление_своих_контейнеров_на_настоящем_docker():
    docker = shutil.which("docker")
    name = C.NAME_PREFIX + str(uuid.uuid4())
    made = subprocess.run([docker, "create", "--name", name, "--network", "none", "--entrypoint", "", DIGEST_REAL, "true"], capture_output=True, text=True,
                          timeout=60)
    assert made.returncode == 0, made.stderr
    try:
        rows = dict(C.containers(docker))
        assert name in rows and abs(rows[name] - time.time()) < 120
        assert C.stale([(name, rows[name])], rows[name] + C.STALE_SECONDS + 1) == [name]
        assert C.stale([(name, rows[name])], rows[name] + C.STALE_SECONDS - 1) == []
    finally:
        C.remove(docker, [name])
    assert name not in dict(C.containers(docker))


def test_setup_на_настоящем_docker_читает_дайджест_без_скачивания_и_пишет_настройку(env):
    r = subprocess.run([sys.executable, FLYARCHIVE, "preview", "setup", "--image", TAG, "--json"], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env={**os.environ, "FLYARCHIVE_HOME": env.home, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"image": DIGEST_REAL, "from": TAG, "pulled": False}
    with open(os.path.join(env.home, "preview.json"), encoding="utf-8") as f:
        assert json.load(f)["image"] == DIGEST_REAL
    assert oct(os.stat(os.path.join(env.home, "preview.json")).st_mode & 0o777) == "0o600"

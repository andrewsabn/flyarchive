"""Команда `flyarchive preview setup` и уборка контейнеров при старте команд preview (FR-78).

Команды запускаются так, как их зовёт владелец и плагин: дочерним процессом, с подставным `docker` в PATH. Образ не скачивается никогда:
`pull` идёт только у `setup` без ключа `--image`, и там он подставной.
"""
import datetime
import json
import os
import stat
import subprocess
import sys
import time
import uuid

import pytest

import gatekit as K
import preview as P
from fake_docker import FakeDocker
from test_preview_container_kit import DIGEST, PDF_DESCRIPTION, record_image, wait_for  # noqa: F401
from test_preview_kit import env, worker  # noqa: F401

FLYARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")
SHA_B = "gotenberg/gotenberg@sha256:" + "cd" * 32


@pytest.fixture
def fake(tmp_path):
    return FakeDocker(tmp_path)


def cli(env, fake, *args, path=None, timeout=60):
    environ = {**(fake.environ if fake else os.environ), "FLYARCHIVE_HOME": env.home, "PYTHONIOENCODING": "utf-8"}
    if path is not None:
        environ["PATH"] = path
    r = subprocess.run([sys.executable, FLYARCHIVE, *args], env=environ, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")


def last_error(err):
    return json.loads(err.strip().splitlines()[-1])["error"]


def setting(env):
    with open(os.path.join(env.home, "preview.json"), encoding="utf-8") as f:
        return json.load(f)


# ── flyarchive preview setup ──────────────────────────────────────
def test_setup_с_ключом_image_записывает_дайджест_без_скачивания(env, fake):
    fake.plan(digests=[DIGEST])
    code, out, err = cli(env, fake, "preview", "setup", "--image", "gotenberg/gotenberg:8", "--json")
    assert code == 0 and err == ""
    assert json.loads(out) == {"image": DIGEST, "from": "gotenberg/gotenberg:8", "pulled": False} and len(out.strip().splitlines()) == 1
    assert "pull" not in fake.ops() and fake.ops() == ["image-inspect"]
    assert setting(env)["image"] == DIGEST


def test_setup_без_ключа_скачивает_образ_по_умолчанию_и_читает_его_дайджест(env, fake):
    fake.plan(digests=[DIGEST])
    code, out, err = cli(env, fake, "preview", "setup", "--json")
    assert code == 0, err
    assert json.loads(out) == {"image": DIGEST, "from": "gotenberg/gotenberg:8", "pulled": True}
    assert fake.ops() == ["pull", "image-inspect"] and "gotenberg/gotenberg:8" in fake.calls()[0]["argv"] and setting(env)["image"] == DIGEST


def test_настройка_лежит_в_каталоге_архива_с_правами_0600_и_пишется_через_замену(env, fake):
    fake.plan(digests=[DIGEST])
    path = os.path.join(env.home, "preview.json")
    with open(path, "w", encoding="utf-8") as f:                              # старая настройка с широкими правами
        json.dump({"image": SHA_B}, f)
    os.chmod(path, 0o644)
    before = os.stat(path).st_ino
    assert cli(env, fake, "preview", "setup", "--image", "gotenberg/gotenberg:8")[0] == 0
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and setting(env)["image"] == DIGEST
    assert os.stat(path).st_ino != before                                      # новый файл, не перезапись на месте
    assert sorted(n for n in os.listdir(env.home) if "preview" in n) == ["preview.json"]


def test_настройка_читается_родителем_после_setup(env, fake, worker, monkeypatch):
    fake.plan(digests=[DIGEST])
    assert cli(env, fake, "preview", "setup", "--image", "gotenberg/gotenberg:8")[0] == 0
    fake.install(monkeypatch)
    worker.describe = lambda ext: PDF_DESCRIPTION if ext == "pdf" else {"kind": "none", "type": "docx", "reason": "needs_converter",
                                                                      "args": {"type": "docx"}}
    fake.plan(run={"files": {"data.pdf": K.pdf_pages(1)}})
    answer = P.show(env.home, "queue", env.put("queue", "а.docx", K.docx_page()))
    argv = fake.runs()[0]["argv"]
    assert answer["kind"] == "pages" and argv[argv.index("--entrypoint") + 2] == DIGEST


def test_из_нескольких_дайджестов_берётся_тот_что_того_же_репозитория(env, fake):
    fake.plan(digests=["other/repo@sha256:" + "11" * 32, DIGEST, "gotenberg/other@sha256:" + "22" * 32])
    code, out, err = cli(env, fake, "preview", "setup", "--image", "gotenberg/gotenberg:8", "--json")
    assert code == 0 and json.loads(out)["image"] == DIGEST


def test_образ_заданный_дайджестом_записывается_как_есть(env, fake):
    fake.plan(digests=[DIGEST])
    code, out, err = cli(env, fake, "preview", "setup", "--image", DIGEST, "--json")
    assert code == 0 and json.loads(out)["image"] == DIGEST and setting(env)["image"] == DIGEST


def test_setup_без_json_печатает_для_человека(env, fake):
    fake.plan(digests=[DIGEST])
    code, out, err = cli(env, fake, "preview", "setup", "--image", "gotenberg/gotenberg:8")
    assert code == 0 and DIGEST in out and "{" not in out and err == ""


@pytest.mark.parametrize("value", ["-rm", "--privileged", "a b", "x;y", "$(id)", "`id`", "a" * 300, "", "образ", "a\nb", "-", "@sha256:" + "ab" * 32])
def test_негодное_имя_образа_отказ_без_обращения_к_docker(env, fake, value):
    code, out, err = cli(env, fake, "preview", "setup", f"--image={value}", "--json")
    assert code == 1 and out == "" and last_error(err)["code"] == "preview.setup_bad_image"
    assert fake.calls() == [] and not os.path.exists(os.path.join(env.home, "preview.json"))


def test_setup_без_docker_отказ_с_кодом(env, fake, tmp_path):
    empty = tmp_path / "пусто"
    empty.mkdir()
    code, out, err = cli(env, None, "preview", "setup", "--image", "gotenberg/gotenberg:8", "--json", path=str(empty))
    assert code == 1 and last_error(err)["code"] == "preview.no_docker" and not os.path.exists(os.path.join(env.home, "preview.json"))


def test_скачивание_не_удалось_отказ_и_прежняя_настройка_цела(env, fake):
    record_image(env, SHA_B)
    fake.plan(pull={"exit": 1, "stderr": "Error response from daemon: pull access denied"}, digests=[DIGEST])
    code, out, err = cli(env, fake, "preview", "setup", "--json")
    error = last_error(err)
    assert code == 1 and error["code"] == "preview.setup_pull_failed" and "pull access denied" in error["args"]["why"]
    assert setting(env)["image"] == SHA_B and fake.ops() == ["pull"]


def test_образа_нет_на_машине_отказ_и_прежняя_настройка_цела(env, fake):
    record_image(env, SHA_B)
    fake.plan(image={"exit": 1, "stderr": "Error response from daemon: No such image: gotenberg/gotenberg:9"})
    code, out, err = cli(env, fake, "preview", "setup", "--image", "gotenberg/gotenberg:9", "--json")
    error = last_error(err)
    assert code == 1 and error["code"] == "preview.setup_no_image" and error["args"] == {"image": "gotenberg/gotenberg:9"}
    assert setting(env)["image"] == SHA_B and "pull" not in fake.ops()


def test_docker_ответил_ошибкой_службы_отказ(env, fake):
    fake.plan(image={"exit": 1, "stderr": "Cannot connect to the Docker daemon"})
    code, out, err = cli(env, fake, "preview", "setup", "--image", "gotenberg/gotenberg:8", "--json")
    assert code == 1 and last_error(err)["code"] == "preview.docker_failed" and not os.path.exists(os.path.join(env.home, "preview.json"))


@pytest.mark.parametrize("digests", [[], ["gotenberg/gotenberg@sha256:short"], ["gotenberg/gotenberg:8"], ["--x@sha256:" + "ab" * 32],
                                     ["gotenberg/gotenberg@sha256:" + "AB" * 32], [None, 5]])
def test_у_образа_нет_годного_дайджеста_отказ_и_прежняя_настройка_цела(env, fake, digests):
    record_image(env, SHA_B)
    fake.plan(digests=digests)
    code, out, err = cli(env, fake, "preview", "setup", "--image", "gotenberg/gotenberg:8", "--json")
    assert code == 1 and last_error(err)["code"] == "preview.setup_no_digest" and setting(env)["image"] == SHA_B


def test_отказ_setup_без_json_печатается_человеку(env, fake):
    code, out, err = cli(env, fake, "preview", "setup", "--image=-x")
    assert code == 1 and out == "" and err.startswith("ошибка: ") and "{" not in err


# ── уборка контейнеров при старте команды ───────────────────────
def stamp(minutes_ago):
    when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def container():
    return "flyarchive-preview-" + str(uuid.uuid4())


def plan_containers(fake):
    old, older, young = container(), container(), container()
    foreign = ["postgres", "my-" + container(), "flyarchive-preview-probe", "flyarchive-preview-" + "z" * 36]
    fake.plan(ps=[old, young, older, *foreign], created={old: stamp(10), older: stamp(6), young: stamp(4)})
    return old, older, young


def reaped(fake):
    calls = fake.calls()
    return [c["argv"] for c in calls if c["op"] == "rm"]


COMMANDS = {
    "clean": ("preview", "clean", "--json"),
    "show": ("preview", "show", "--area", "queue", "--json", "--", "очередь/20261004-120000/нет.txt"),
    "page": ("preview", "page", "--area", "queue", "--", "очередь/20261004-120000/нет.txt", "1"),
}


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_каждая_команда_preview_при_старте_убирает_только_свои_контейнеры_старше_пяти_минут(env, fake, command):
    old, older, young = plan_containers(fake)
    cli(env, fake, *COMMANDS[command])
    assert wait_for(lambda: reaped(fake)), fake.ops()
    assert len(reaped(fake)) == 1 and reaped(fake)[0][:2] == ["rm", "-f"] and sorted(reaped(fake)[0][2:]) == sorted([old, older])
    inspected = [c["argv"] for c in fake.calls() if c["op"] == "inspect"][0]
    assert sorted(a for a in inspected if a.startswith("flyarchive-preview-")) == sorted([old, older, young])       # чужие имена не опрашивались


def test_ничего_старого_нет_rm_не_зовётся(env, fake):
    young = container()
    fake.plan(ps=[young], created={young: stamp(1)})
    cli(env, fake, *COMMANDS["clean"])
    assert wait_for(lambda: "inspect" in fake.ops()) and reaped(fake) == []


def test_контейнеров_нет_достаточно_одного_вызова(env, fake):
    fake.plan(ps=[])
    cli(env, fake, *COMMANDS["clean"])
    assert wait_for(lambda: "ps" in fake.ops())
    time.sleep(0.3)
    assert fake.ops() == ["ps"]


def test_сбой_уборки_не_мешает_команде_и_ничего_не_печатает(env, fake):
    fake.plan(ps_answer={"exit": 1, "stderr": "Cannot connect to the Docker daemon"})
    code, out, err = cli(env, fake, *COMMANDS["clean"])
    assert code == 0 and err == "" and json.loads(out) == {"removed": 0, "freed": 0, "left": 0}
    assert wait_for(lambda: "ps" in fake.ops())                                  # уборка была начата, но молча не удалась
    old = container()
    fake.plan(ps_answer={"exit": 0}, ps=[old], created={old: "не время"}, rm_exit=1)
    code, out, err = cli(env, fake, *COMMANDS["clean"])
    assert code == 0 and err == ""
    assert wait_for(lambda: "inspect" in fake.ops()) and reaped(fake) == []         # время не разобрано — контейнер не тронут


def test_без_docker_команда_работает_как_раньше_и_молчит(env, tmp_path):
    empty = tmp_path / "пусто"
    empty.mkdir()
    code, out, err = cli(env, None, *COMMANDS["clean"], path=str(empty))
    assert code == 0 and err == "" and json.loads(out) == {"removed": 0, "freed": 0, "left": 0}


def test_зависший_docker_уборку_не_ждёт_команда_возвращается_сразу(env, fake):
    fake.plan(ps_answer={"sleep": 30})
    started = time.monotonic()
    code, out, err = cli(env, fake, *COMMANDS["clean"])
    assert code == 0 and err == "" and time.monotonic() - started < 4
    assert wait_for(lambda: "ps" in fake.ops())                                  # уборка начата, но команда её не ждала

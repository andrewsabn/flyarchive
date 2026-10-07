"""Безопасность запуска контейнера (FR-78): ключи docker ключ в ключ, подключения, образ только по дайджесту, строки из файла в команду не идут.

Всё на подставном docker: тест смотрит, с какими аргументами его позвали и что лежало во входной папке в этот миг.
"""
import ast
import json
import os
import re

import pytest

import gatekit as K
import preview as P
from test_preview_container_kit import (DIGEST, NAME, OFFICE_PDF, PDF_DESCRIPTION, SHA, box, leftovers, needs, record_image,  # noqa: F401
                                         run_argv, show, walk_modes)
from test_preview_kit import env, png, worker, write  # noqa: F401

TOOLS = os.path.dirname(os.path.abspath(P.__file__))


def expected_argv(run, name, ext="docx", image=DIGEST):
    """Что должно уйти в docker, ключ в ключ, как в плане (раздел 5): подставляются только имя контейнера, владелец, две папки, образ и расширение."""
    return ["run", "--rm", "--init", "--pull", "never", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp:size=512m", "--shm-size", "256m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "2g", "--memory-swap", "2g", "--cpus", "2",
            "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", "-v", f"{run['mounts']['/in']}:/in:ro", "-v", f"{run['mounts']['/out']}:/out",
            "--entrypoint", "", image, "timeout", "-s", "KILL", "120", "soffice", "-env:UserInstallation=file:///tmp/lo", "--headless",
            "--convert-to", "pdf", "--outdir", "/out", f"/in/data.{ext}"]


def convert(box, name="договор.docx", data=None):
    box.worker.describe = lambda ext: PDF_DESCRIPTION if ext == "pdf" else needs("docx")             # тип назвал рабочий процесс, имя тут ни при чём
    show(box.env, box.env.put("queue", name, data or K.docx_page(name)))
    run = box.docker.runs()[-1]
    return run, run["argv"]


# ── команда ключ в ключ ─────────────────────────────────────────
def test_команда_контейнера_совпадает_с_планом_ключ_в_ключ(box):
    run, argv = convert(box)
    assert argv == expected_argv(run, argv[argv.index("--name") + 1])


def test_лишний_или_пропущенный_ключ_виден_по_набору_ключей(box):
    run, argv = convert(box)
    flags = [a for a in argv[:argv.index(DIGEST)] if a.startswith("-") and a != ""]
    assert sorted(flags) == sorted(["--rm", "--init", "--pull", "--name", "--network", "--read-only", "--tmpfs", "--shm-size", "--cap-drop", "--security-opt",
                                    "--pids-limit", "--memory", "--memory-swap", "--cpus", "-u", "-e", "-v", "-v", "--entrypoint"])


def test_контейнер_сам_образ_не_скачивает(box):
    """Образа с записанным дайджестом может не оказаться (удалили между проверкой и запуском): docker run без этого ключа пошёл бы за ним в сеть."""
    run, argv = convert(box)
    assert argv[argv.index("--pull") + 1] == "never" and argv.index("--pull") < argv.index(DIGEST)


def test_ограничения_контейнера_каждое_отдельно(box):
    run, argv = convert(box)
    pair = lambda key: argv[argv.index(key) + 1]                                          # noqa: E731
    assert pair("--network") == "none" and "--read-only" in argv and pair("--cap-drop") == "ALL" and pair("--security-opt") == "no-new-privileges"
    assert pair("--memory") == "2g" and pair("--memory-swap") == "2g" and pair("--cpus") == "2" and pair("--pids-limit") == "256"
    assert pair("--tmpfs") == "/tmp:size=512m" and pair("--shm-size") == "256m" and pair("-e") == "HOME=/tmp" and pair("-u") == f"{os.getuid()}:{os.getgid()}"
    assert "--init" in argv and "--rm" in argv
    assert argv[argv.index(DIGEST) + 1:argv.index(DIGEST) + 5] == ["timeout", "-s", "KILL", "120"]     # срок стоит внутри контейнера


def test_имя_контейнера_flyarchive_preview_и_uuid_у_каждого_запуска_своё(box):
    run, argv = convert(box, "а.docx", K.docx_page("один"))
    first = argv[argv.index("--name") + 1]
    run, argv = convert(box, "б.docx", K.docx_page("два"))
    second = argv[argv.index("--name") + 1]
    assert NAME.fullmatch(first) and NAME.fullmatch(second) and first != second


def test_команда_собирается_списком_без_оболочки():
    source = open(os.path.join(TOOLS, "preview_container.py"), encoding="utf-8").read()
    assert "subprocess.Popen" in source and "docker" in source                       # проверяется настоящий модуль запуска, а не пустая заготовка
    assert "shell=" not in source and "os.system" not in source and "os.popen" not in source and "getoutput" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("Popen", "run", "check_output", "call"):
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "subprocess":
                assert node.args and not isinstance(node.args[0], (ast.Constant, ast.JoinedStr)), f"строка вместо списка: строка {node.lineno}"


# ── образ только по дайджесту ───────────────────────────────────
def test_образ_запускается_по_дайджесту_из_настройки(box):
    other = "registry.example.test:5000/team/img-x_y.z@sha256:" + "cd" * 32
    record_image(box.env, other)
    run, argv = convert(box)
    assert argv[argv.index(other) - 2:argv.index(other)] == ["--entrypoint", ""] and argv == expected_argv(run, argv[argv.index("--name") + 1], image=other)


BAD_IMAGES = {
    "тег без дайджеста": "gotenberg/gotenberg:8",
    "голое имя": "gotenberg/gotenberg",
    "дайджест короче": "gotenberg/gotenberg@sha256:" + "ab" * 31,
    "дайджест длиннее": "gotenberg/gotenberg@sha256:" + "ab" * 33,
    "дайджест заглавными": "gotenberg/gotenberg@sha256:" + "AB" * 32,
    "дайджест не из шестнадцатеричных": "gotenberg/gotenberg@sha256:" + "zz" * 32,
    "другой алгоритм": "gotenberg/gotenberg@sha512:" + "ab" * 32,
    "имя — ключ docker": "--privileged@sha256:" + "ab" * 32,
    "имя начинается с дефиса": "-v/:/host@sha256:" + "ab" * 32,
    "имя с пробелом": "gotenberg gotenberg@sha256:" + "ab" * 32,
    "имя с точкой с запятой": "a;b@sha256:" + "ab" * 32,
    "имя с подстановкой": "$(id)@sha256:" + "ab" * 32,
    "имя с обратной кавычкой": "`id`@sha256:" + "ab" * 32,
    "имя с юникодом": "образ@sha256:" + "ab" * 32,
    "имя заглавными": "Gotenberg/Gotenberg@sha256:" + "ab" * 32,
    "имя пустое": "@sha256:" + "ab" * 32,
    "имя огромное": "a" * 300 + "@sha256:" + "ab" * 32,
    "перевод строки в конце": DIGEST + "\n",
    "перевод строки внутри": "gotenberg/gotenberg\n--privileged@sha256:" + "ab" * 32,
    "два дайджеста": DIGEST + "@sha256:" + "cd" * 32,
    "пробел в конце": DIGEST + " ",
    "не строка: число": 5,
    "не строка: список": [DIGEST],
    "не строка: null": None,
}


@pytest.mark.parametrize("name", sorted(BAD_IMAGES))
def test_негодное_значение_в_настройке_как_не_записанное_и_контейнер_не_запускается(box, name):
    record_image(box.env, BAD_IMAGES[name])
    answer = show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.no_image", answer
    assert box.docker.runs() == [] and "pull" not in box.docker.ops() and box.env.cached() == []


@pytest.mark.parametrize("raw", [b"", b"{", b"[1, 2]", b'"gotenberg/gotenberg:8"', b"null", b'{"image": ', b'{"Image": "' + DIGEST.encode() + b'"}',
                                 b"\xff\xfe\x00", b"x" * 100_000, b'{"image": "' + b"a" * 200_000 + b'"}'])
def test_испорченный_файл_настройки_как_не_записанный(box, raw):
    with open(os.path.join(box.env.home, "preview.json"), "wb") as f:
        f.write(raw)
    answer = show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.no_image" and box.docker.runs() == []


def test_настройка_читается_при_каждом_запросе_испорченная_руками_уже_не_действует(box):
    assert show(box.env, box.env.put("queue", "а.docx", K.docx_page("один")))["kind"] == "pages"
    record_image(box.env, "gotenberg/gotenberg:8")
    answer = show(box.env, box.env.put("queue", "б.docx", K.docx_page("два")))
    assert answer["note"]["code"] == "preview.no_image" and len(box.docker.runs()) == 1


def test_файл_настройки_ссылкой_не_читается(box, tmp_path):
    real = tmp_path / "чужая-настройка.json"
    real.write_text(json.dumps({"image": DIGEST}), encoding="utf-8")
    path = os.path.join(box.env.home, "preview.json")
    os.unlink(path)
    os.symlink(real, path)
    answer = show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    assert answer["note"]["code"] == "preview.no_image" and box.docker.runs() == []


# ── что монтируется ─────────────────────────────────────────────
def test_подключены_только_вход_для_чтения_и_выход_каталога_архива_и_сокета_нет(box):
    run, argv = convert(box)
    volumes = [argv[i + 1] for i, a in enumerate(argv) if a in ("-v", "--volume", "--mount")]
    assert len(volumes) == 2 and volumes[0].endswith(":/in:ro") and volumes[1].endswith(":/out")
    sources = [run["mounts"]["/in"], run["mounts"]["/out"]]
    forbidden = {box.env.home, box.env.cache, os.path.expanduser("~"), "/", "/var/run", "/run", "/var/run/docker.sock", "/run/docker.sock"}
    assert not forbidden & set(sources)
    assert "docker.sock" not in repr(argv)
    assert not {"--privileged", "--cap-add", "--device", "--pid", "--ipc", "--userns", "--volumes-from", "--add-host", "--env-file", "--mount",
                "--volume", "--net", "--uts", "--cgroupns", "--security-opt=seccomp=unconfined"} & {a.split("=")[0] for a in argv}
    for source in sources:
        assert os.path.dirname(source) == box.env.cache and os.path.basename(source).startswith((".in-", ".out-"))
        assert not box.env.home.startswith(source) and not box.env.cache.startswith(source)
    assert sources[0] != sources[1]


def test_через_ключ_e_передаётся_только_home_ни_токенов_ни_окружения_владельца(box, monkeypatch):
    monkeypatch.setenv("FLYARCHIVE_TOKEN", "секретный-токен")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "тоже-секрет")
    run, argv = convert(box)
    assert [argv[i + 1] for i, a in enumerate(argv) if a in ("-e", "--env")] == ["HOME=/tmp"]
    assert "секрет" not in repr(box.docker.calls())


def test_входной_каталог_во_время_запуска_содержит_один_файл_а_выходной_пуст(box):
    run, _ = convert(box)
    assert list(run["in"]) == ["data.docx"] and run["out_before"] == {} and run["in_dir_mode"] == "0o700"


# ── строки из файла в команду не попадают ───────────────────────
HOSTILE = ["$(touch pwned).docx", "a b;c|d&e`f`.docx", "--privileged.docx", "-v.docx", "имя файла.docx", "x'y\"z.docx", "..docx", "%s%n.docx",
           "a" * 200 + ".docx"]


@pytest.mark.parametrize("name", HOSTILE)
def test_имя_файла_в_команду_не_попадает_ни_в_одной_части(box, name):
    run, argv = convert(box, name, K.docx_page("x" + name))
    assert argv == expected_argv(run, argv[argv.index("--name") + 1])
    assert list(run["in"]) == ["data.docx"]


def test_сообщение_о_причине_не_приносит_имя_файла_в_команду_и_в_ответ_нет_строк_из_контейнера(box):
    box.docker.plan(run={"files": {}, "exit": 1, "stderr": "секретный-путь/внутри $(rm -rf ~)"})
    answer = show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    why = answer["note"]["args"]["why"]
    assert answer["note"]["code"] == "preview.convert_failed" and why.isascii() and why.isprintable() and len(why) <= 130
    assert "секретный" not in why


# ── результат контейнера — данные ───────────────────────────────
@pytest.mark.parametrize("out_name", ["data.pdf", "..pdf", "рисунок.pdf", ".скрытый", "a b;c$(x).pdf", "x" * 200, "-rf", "data.pdf.exe"])
def test_имя_выходного_файла_на_путь_в_кэше_не_влияет(box, out_name):
    box.docker.plan(run={"files": {out_name: OFFICE_PDF}})
    data = K.docx_page("имя вывода " + out_name)
    show(box.env, box.env.put("queue", "а.docx", data))
    assert box.env.cached() == [f"{SHA(data)}/base.pdf", f"{SHA(data)}/desc.json"]


def test_в_кэше_всё_по_шестнадцатеричным_именам_и_правам_после_разных_исходов(box):
    results = [{"files": {"data.pdf": OFFICE_PDF}}, {"files": {"data.pdf": "не pdf".encode("utf-8")}}, {"files": {}}, {"files": {}, "exit": 2},
               {"files": {"data.pdf": {"symlink": "/etc/hostname"}}}]
    for number, plan in enumerate(results):
        box.docker.plan(run=plan)
        show(box.env, box.env.put("queue", f"файл-{number}.docx", K.docx_page(f"исход {number}")))
    cached = box.env.cached()
    assert len(cached) == 2 and all(re.fullmatch(r"[0-9a-f]{64}/(base\.pdf|desc\.json)", c) for c in cached), cached
    assert all(mode in ((True, 0o700), (False, 0o600)) for mode in walk_modes(box.env.cache).values()) and leftovers(box.env) == []


# ── уборка брошенных рабочих каталогов ──────────────────────────
def test_clean_убирает_брошенные_входные_и_выходные_каталоги_контейнера(box):
    cache = box.env.cache
    os.makedirs(cache, exist_ok=True)
    long_ago = 1_500_000_000
    for name in (".in-aaaa", ".out-aaaa"):
        os.makedirs(os.path.join(cache, name))
        write(os.path.join(cache, name, "data.docx"), b"x")
        os.utime(os.path.join(cache, name), (long_ago, long_ago))
    for name in (".in-свежий", ".out-свежий"):
        os.makedirs(os.path.join(cache, name))
    P.clean(box.env.home)
    assert sorted(n for n in os.listdir(cache) if n.startswith((".in-", ".out-"))) == [".in-свежий", ".out-свежий"]

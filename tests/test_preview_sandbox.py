"""Просмотр файла: проверки настоящим bwrap (FR-76) — не подставным запуском.

Песочница настоящая, рабочий процесс настоящий, PyMuPDF и Pillow настоящие, образцы маленькие. Если на машине нет bwrap или он не может
создать пространство имён, тесты пропускаются с причиной: без песочницы файл не разбирается никогда (это проверено отдельно в
test_preview_parent.py: команда отвечает `kind: "none"`).
"""
import json
import os
import shutil
import socket
import time
import uuid

import pytest

import gatekit as K
import preview as P
from sandboxkit import needs_sandbox
from test_preview_kit import BATCH, env  # noqa: F401  — общая обвязка

TOOLS = os.path.dirname(os.path.abspath(P.__file__))


pytestmark = needs_sandbox                  # условие и причина пропуска — общие для всех тестов с настоящей песочницей (sandboxkit)


@pytest.fixture
def listener():
    """Порт на петле этой машины: изнутри песочницы до него дойти нельзя. accepted() — было ли хоть одно соединение."""
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


def sandboxed(env, code, script, args=(), timeout=60, fd=None, outdir=None):
    out = outdir or str(env.tmp / "выход")
    os.makedirs(out, exist_ok=True)
    return P.run_sandboxed(str(code), script, list(args), out, fd, timeout, env.home), out


PROBE = '''
import json, os, socket, sys
port, archive = int(sys.argv[1]), sys.argv[2]
libs = sys.argv[3:]
rep = {}


def attempt(name, fn):
    try:
        fn()
        rep[name] = "ok"
    except Exception as e:
        rep[name] = type(e).__name__


def write(path):
    def go():
        with open(path, "wb") as f:
            f.write(b"x")
    return go


attempt("connect", lambda: socket.create_connection(("127.0.0.1", port), timeout=2).close())
rep["root"] = sorted(os.listdir("/"))
rep["archive"] = os.path.exists(archive)
rep["archive_parent"] = os.path.exists(os.path.dirname(archive))
tree = []
for folder, dirs, files in os.walk("/home"):
    tree.append(folder)
    dirs[:] = [d for d in dirs if os.path.join(folder, d) not in libs]
rep["home"] = tree
for target in ("/code/new", "/usr/new", "/etc/new", "/in/data", "/out/new", "/tmp/new"):
    attempt("write " + target, write(target))
try:
    rep["input"] = open("/in/data", "rb").read().decode("utf-8")
except Exception as e:
    rep["input"] = type(e).__name__
rep["env"] = dict(os.environ)
rep["cwd"] = os.getcwd()
with open("/out/report.json", "w") as f:
    json.dump(rep, f)
'''


def test_изнутри_нет_сети_не_виден_каталог_архива_и_нет_записи_куда_не_положено(env, listener, tmp_path, monkeypatch):
    monkeypatch.setenv("FLYARCHIVE_HOME", env.home)
    monkeypatch.setenv("FLYARCHIVE_LOCAL_TOKEN", "секретный-токен-для-проверки")
    code = tmp_path / "код"
    code.mkdir()
    (code / "probe.py").write_text(PROBE, encoding="utf-8")
    source = tmp_path / "вход.txt"
    source.write_bytes("содержимое входного файла".encode("utf-8"))
    libs = P.library_dirs(env.home)
    with open(source, "rb") as fd:
        run, out = sandboxed(env, code, "probe.py", [str(listener.port), env.home, *libs], fd=fd.fileno())
    assert run.code == 0 and not run.timed_out, run
    rep = json.loads(open(os.path.join(out, "report.json"), encoding="utf-8").read())
    assert rep["connect"] != "ok" and not listener.accepted()                           # сети нет: даже до порта на петле не дойти
    assert rep["archive"] is False and rep["archive_parent"] is False
    assert set(rep["root"]) <= {"bin", "code", "dev", "etc", "home", "in", "lib", "lib32", "lib64", "libx32", "out", "proc", "sbin", "tmp", "usr"}
    assert not {"mnt", "root", "var", "run", "srv", "opt", "media"} & set(rep["root"])
    visible = set(rep["home"])                                                          # в /home — только путь к каталогам библиотек
    assert all(any(lib == p or lib.startswith(p + os.sep) for lib in libs) for p in visible), visible
    assert rep["input"] == "содержимое входного файла"
    for target in ("/code/new", "/usr/new", "/etc/new", "/in/data"):
        assert rep["write " + target] in ("OSError", "PermissionError", "FileNotFoundError"), (target, rep["write " + target])
    assert rep["write /out/new"] == "ok" and rep["write /tmp/new"] == "ok"
    assert rep["env"]["HOME"] == "/tmp" and rep["cwd"] == "/tmp"
    assert not [k for k in rep["env"] if k.startswith("FLYARCHIVE") or "TOKEN" in k.upper() or "SECRET" in k.upper()]
    assert "секретный-токен" not in json.dumps(rep, ensure_ascii=False)
    assert set(rep["env"]) <= {"HOME", "PATH", "PYTHONPATH", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "PWD", "LC_CTYPE", "LANG"}


def test_входной_файл_виден_по_дескриптору_а_исходный_путь_нет(env, tmp_path):
    code = tmp_path / "код"
    code.mkdir()
    (code / "probe.py").write_text("import json, os, sys\nrep = {'there': os.path.exists(sys.argv[1]), 'in': sorted(os.listdir('/in'))}\n"
                                   "open('/out/report.json', 'w').write(json.dumps(rep))\n", encoding="utf-8")
    source = env.tmp / "потайной-каталог" / "файл.txt"
    source.parent.mkdir()
    source.write_bytes(b"x")
    with open(source, "rb") as fd:
        run, out = sandboxed(env, code, "probe.py", [str(source)], fd=fd.fileno())
    assert run.code == 0
    assert json.loads(open(os.path.join(out, "report.json"), encoding="utf-8").read()) == {"there": False, "in": ["data"]}


def pids_with(marker):
    found = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                with open(f"/proc/{name}/cmdline", "rb") as f:
                    if marker.encode() in f.read():
                        found.append(int(name))
            except OSError:
                pass
    return found


def test_процесс_остановлен_по_сроку_и_не_остался_сиротой(env, tmp_path):
    marker = "висит_" + uuid.uuid4().hex
    code = tmp_path / "код"
    code.mkdir()
    (code / f"{marker}.py").write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    started = time.monotonic()
    run, _ = sandboxed(env, code, f"{marker}.py", timeout=1)
    assert run.timed_out and run.code is None and time.monotonic() - started < 15
    deadline = time.monotonic() + 5
    while pids_with(marker) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pids_with(marker) == []


def test_выход_больше_предела_останавливает_процесс(env, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT_MAX", 3 << 20)
    code = tmp_path / "код"
    code.mkdir()
    (code / "flood.py").write_text("import time\nfor i in range(40):\n    open(f'/out/f{i}', 'wb').write(b'x' * (1 << 20))\n    time.sleep(0.1)\n",
                                   encoding="utf-8")
    run, out = sandboxed(env, code, "flood.py", timeout=30)
    assert run.timed_out is False and run.code == -9 and "output" in run.tail, run
    assert sum(os.path.getsize(os.path.join(out, n)) for n in os.listdir(out)) < 20 << 20       # остановлен, не дописав все 40 МБ


LIMITS = '''
import json, mmap, resource
import preview_worker as W
W.apply_limits()
try:
    mmap.mmap(-1, 3 * 2 ** 30)
    taken = "выделено"
except (OSError, MemoryError, ValueError):
    taken = "отказ"
try:
    resource.setrlimit(resource.RLIMIT_AS, (8 * 2 ** 30, 8 * 2 ** 30))
    raised = True
except (ValueError, OSError):
    raised = False
rep = {"taken": taken, "raised": raised, "as": resource.getrlimit(resource.RLIMIT_AS), "cpu": resource.getrlimit(resource.RLIMIT_CPU),
       "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)}
open("/out/report.json", "w").write(json.dumps(rep))
'''


def test_пределы_действуют_и_внутри_песочницы(env, tmp_path):
    code = tmp_path / "код"
    code.mkdir()
    for name in ("preview_worker.py", "filetype_sniff.py"):
        shutil.copy(os.path.join(TOOLS, name), code / name)
    (code / "limits.py").write_text(LIMITS, encoding="utf-8")
    run, out = sandboxed(env, code, "limits.py")
    assert run.code == 0, run
    assert json.loads(open(os.path.join(out, "report.json"), encoding="utf-8").read()) == {
        "taken": "отказ", "raised": False, "as": [2 * 1024 ** 3] * 2, "cpu": [60, 60], "fsize": [200 * 1024 ** 2] * 2}


# ── настоящий рабочий процесс на образцах ───────────────────────
@pytest.fixture
def spy(monkeypatch):
    """Настоящий рабочий процесс, но с подсчётом запусков."""
    calls = []
    real = P._run_worker
    monkeypatch.setattr(P, "_run_worker", lambda home, op, fd, ext, page, outdir: (calls.append(op), real(home, op, fd, ext, page, outdir))[1])
    return calls


def dims(data):
    import struct
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_текст_и_pdf_настоящим_запуском_и_кэш_не_запускает_второй_раз(env, spy):
    pytest.importorskip("pymupdf")
    text = env.put("queue", "договор.txt", "Договор поставки.\nВторая строка.".encode("utf-8"))
    answer = P.show(env.home, "queue", text)
    assert answer["kind"] == "text" and answer["text"] == "Договор поставки.\nВторая строка." and answer["truncated"] is False
    assert answer["meta"]["type"] == "txt"
    pdf = env.put("queue", "отчёт.pdf", K.pdf_pages(3))
    answer = P.show(env.home, "queue", pdf)
    assert answer["kind"] == "pages" and (answer["pages"], answer["shown"]) == (3, 3) and answer["meta"]["type"] == "pdf"
    page = P.page(env.home, "queue", pdf, "2")
    assert dims(page) == (1224, 1584)
    before = list(spy)
    assert P.show(env.home, "queue", text) == P.show(env.home, "queue", text) and P.show(env.home, "queue", pdf)["pages"] == 3
    assert P.page(env.home, "queue", pdf, "2") == page
    assert spy == before == ["describe", "describe", "render"]
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", pdf, "4")
    assert e.value.message.code == "preview.no_page" and spy == before


def test_svg_со_сценарием_настоящим_запуском_наружу_только_png(env, listener, spy):
    pytest.importorskip("pymupdf")
    svg = env.put("queue", "вредный.svg", K.svg_bytes(script=True, text="видимая подпись").replace(b"127.0.0.1:9", f"127.0.0.1:{listener.port}".encode()))
    answer = P.show(env.home, "queue", svg)
    assert answer["kind"] == "pages" and (answer["pages"], answer["shown"]) == (1, 1)
    page = P.page(env.home, "queue", svg, "1")
    assert dims(page) == (400, 200) and b"<script" not in page and b"alert" not in page
    assert "<" not in json.dumps(answer, ensure_ascii=False) and "script" not in json.dumps(answer)
    assert not listener.accepted()


def test_картинки_настоящим_запуском_уменьшение_до_2000_и_заявка_в_сотни_мегапикселей(env, spy):
    pytest.importorskip("PIL")
    big = env.put("queue", "широкая.png", K.image_bytes("PNG", (3000, 1000)))
    answer = P.show(env.home, "queue", big)
    assert answer["kind"] == "image" and answer["meta"]["type"] == "png" and "pages" not in answer
    assert dims(P.page(env.home, "queue", big, "1")) == (2000, 667) and spy == ["describe"]
    declared = env.put("queue", "бомба.png", K.png_declared(16_000, 16_000))
    started = time.monotonic()
    answer = P.show(env.home, "queue", declared)
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.image_too_large"
    assert answer["note"]["args"] == {"megapixels": 256, "limit": 100} and time.monotonic() - started < 30
    for fmt, name in (("JPEG", "а.jpg"), ("GIF", "а.gif"), ("BMP", "а.bmp"), ("TIFF", "а.tif")):
        path = env.put("queue", name, K.image_bytes(fmt, (60, 40)))
        assert P.show(env.home, "queue", path)["kind"] == "image" and dims(P.page(env.home, "queue", path, "1")) == (60, 40)


def test_pdf_со_страницей_в_тысячи_пунктов_настоящим_запуском_в_пределе_4_мп(env, spy):
    pytest.importorskip("pymupdf")
    path = env.put("queue", "огромный.pdf", K.pdf_pages(1, (100_000, 100_000)))
    assert P.show(env.home, "queue", path)["kind"] == "pages"
    w, h = dims(P.page(env.home, "queue", path, "1"))
    assert w * h <= 4_000_000 and max(w, h) <= 10_000


def test_программа_другой_тип_и_битый_файл_настоящим_запуском_none_без_исключений(env, spy):
    pytest.importorskip("pymupdf")
    cases = (("setup.bin", K.PE, "preview.program"), ("клип.mp4", bytes(range(1, 90)), "preview.needs_converter"),
             ("данные.bin", bytes(range(256)) * 4, "preview.unsupported"), ("пустой.txt", b"", "preview.empty"),
             ("битый.pdf", b"%PDF-1.4\n" + bytes(range(256)), "preview.broken"))
    for name, data, code in cases:
        answer = P.show(env.home, "queue", env.put("queue", name, data))
        assert answer["kind"] == "none" and answer["note"]["code"] == code, (name, answer)
    assert not [n for n in os.listdir(env.cache) if n.startswith(".work")]


def test_несуществующий_рабочий_процесс_none_а_не_исключение(env, monkeypatch):
    """Каталог кода без рабочего процесса: песочница стартует, но запускать нечего."""
    empty = env.tmp / "пустой-код"
    empty.mkdir()
    monkeypatch.setattr(P, "CODE_DIR", str(empty))
    answer = P.show(env.home, "queue", env.put("queue", "а.txt", b"x"))
    assert answer["kind"] == "none" and answer["note"]["code"] in ("preview.worker_failed", "preview.bad_answer")


def test_рабочий_процесс_не_уложился_в_срок_none_с_пояснением(env, monkeypatch, tmp_path):
    code = tmp_path / "код"
    code.mkdir()
    (code / "preview_worker.py").write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    monkeypatch.setattr(P, "CODE_DIR", str(code))
    monkeypatch.setattr(P, "WORKER_TIMEOUT", 1)
    started = time.monotonic()
    answer = P.show(env.home, "queue", env.put("queue", "а.txt", b"x"))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.timeout" and answer["note"]["args"] == {"seconds": 1}
    assert time.monotonic() - started < 15 and env.cached() == []


def test_рабочий_процесс_вернул_не_то_none_и_кэш_пуст(env, monkeypatch, tmp_path):
    """Подменённый рабочий процесс (в песочнице, как настоящий) пишет ссылку вместо результата."""
    code = tmp_path / "код"
    code.mkdir()
    (code / "preview_worker.py").write_text("import os\nos.symlink('/etc/hostname', '/out/result.json')\n", encoding="utf-8")
    monkeypatch.setattr(P, "CODE_DIR", str(code))
    answer = P.show(env.home, "queue", env.put("queue", "а.txt", b"x"))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.bad_answer" and answer["note"]["args"] == {"what": "not_a_file"}
    assert env.cached() == []

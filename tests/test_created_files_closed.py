"""Всё, что команды и службы создают в каталоге архива, закрыто от других пользователей при любой маске процесса (NFR-01а, FR-107).

Находка пробы на пустой машине: `flyarchive perms check` на свежем архиве после ручного запуска сервера документов сразу находил нарушение —
каталог `out` создавался с правами 775 (маска пользователя 002). Права каталогов и файлов должны задаваться при создании, а не маской процесса
(маска только урезает: 000 — самая широкая).

Здесь настоящий путь с маской 000: init, настройка входящей папки без таймера, образцы, разбор, три службы на свободных портах, запросы, которые
создают файлы и журналы, и `perms check` без нарушений. Команды и службы — настоящие процессы в выдуманной машине (свой домашний каталог,
подставная служба векторов, подставной systemctl); живой архив и настоящие порты не трогаются.
"""
import contextlib
import json
import os
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import office_server as O
import perms
from archivekit import ROOT, Archive
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

lancedb = pytest.importorskip("lancedb")
pytestmark = [pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной"),
              pytest.mark.skipif(os.name == "nt", reason="права и маска POSIX")]

TOOLS = os.path.join(ROOT, "tools")
WIDE = 'umask 000; exec "$@"'                                    # самая широкая маска: всё, что не закрыто при создании, станет открытым
SAMPLES = {"а.txt": "Протокол встречи: перенос релиза на четверг.", "б.md": "# План\n\nСогласовать перенос работ.\n",
           "в.csv": "поле;значение\nсрок;четверг\n"}                 # форматы, которые индексируются без библиотек по форматам
CHART = {"kind": "bar", "title": "Заявки", "labels": ["пн", "вт"], "series": {"число": [3, 5]}}


@pytest.fixture
def archive(tmp_path, vectors):
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})


def wide(archive, *args, **extra):
    """Команда flyarchive с маской 000: (код, stdout, stderr)."""
    r = subprocess.run(["bash", "-c", WIDE, "bash", sys.executable, os.path.join(TOOLS, "flyarchive"), *args], env=archive.env(**extra),
                       capture_output=True, text=True, encoding="utf-8", timeout=180)
    return r.returncode, r.stdout, r.stderr


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(url, token, body=None):
    """(код ответа, объект JSON) — и для отказов: тело ответа с кодом 4xx и 5xx разбирается так же."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


@contextlib.contextmanager
def running(archive, **extra):
    """Три службы архива с маской 000 на свободных портах: поиск, документы, переходник MCP. Отдаёт (порты, токен, номера процессов по имени
    сценария); в конце гасит их. extra — переменные окружения служб сверх окружения архива."""
    ports = {"search": free_port(), "office": free_port(), "mcp": free_port()}
    env = archive.env(FLYARCHIVE_SEARCH_PORT=str(ports["search"]), FLYARCHIVE_OFFICE_PORT=str(ports["office"]),
                      FLYARCHIVE_MCP_PORT=str(ports["mcp"]), **extra)
    token = open(archive.path("secrets", "local.token"), encoding="utf-8").read().strip()
    procs = []
    try:
        for tool, key in (("webui.py", "search"), ("office_server.py", "office"), ("mcp_server.py", "mcp")):
            log = open(os.path.join(str(archive.base), tool + ".log"), "wb")
            procs.append((subprocess.Popen(["bash", "-c", WIDE, "bash", sys.executable, os.path.join(TOOLS, tool)], env=env,
                                           stdout=log, stderr=subprocess.STDOUT), ports[key], tool))
        deadline = time.time() + 30
        for proc, port, tool in procs:
            while True:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()
                    break
                except OSError:
                    assert proc.poll() is None and time.time() < deadline, f"{tool} не запустился"
                    time.sleep(0.1)
        yield ports, token, {tool: proc.pid for proc, _, tool in procs}
    finally:
        for proc, _, _ in procs:
            proc.terminate()
        for proc, _, _ in procs:
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def open_places(home):
    """Всё, что открыто группе или остальным, в том числе внутри индекса, корпуса и каталога результатов: [(путь от архива, права)]."""
    found = []
    for folder, dirs, files in os.walk(home):
        for name in dirs + files:
            full = os.path.join(folder, name)
            if not os.path.islink(full) and mode(full) & 0o077:
                found.append((os.path.relpath(full, home), oct(mode(full))))
    return sorted(found)


def test_путь_от_init_до_запросов_служб_с_маской_000_оставляет_архив_закрытым(archive):
    code, out, err = wide(archive, "init")
    assert code == 0, err
    code, out, err = wide(archive, "inbox", "set", "--path", archive.box, "--llm", "off", "--cloud", "off", "--no-timer")
    assert code == 0, err
    archive.instant()
    for name, text in SAMPLES.items():
        archive.put(name, text)
    code, out, err = wide(archive, "inbox", "run", "--wait")
    assert code == 0 and "Принято: 3" in out, (out, err)
    assert mode(archive.home) == 0o700, "каталог архива, заведённый init, закрыт"

    with running(archive) as (ports, token, _):
        office, search, mcp = (f"http://127.0.0.1:{ports[k]}" for k in ("office", "search", "mcp"))
        assert mode(archive.path("out")) == 0o700, "каталог результатов создала служба документов при запуске"
        status, found = request(f"{search}/api/search?" + urllib.parse.urlencode({"q": "перенос"}), token)
        assert status == 200 and found, found
        status, chart = request(f"{office}/chart", token, CHART)
        assert status in (200, 503), chart                                  # без matplotlib — отказ, и это не мешает проверке прав
        if status == 200:
            made = os.path.join(archive.path("out"), os.path.basename(chart["file"]))
            assert mode(made) == 0o600, f"график создан с правами {mode(made):o}"
        status, doc = request(f"{office}/document", token, {"kind": "docx", "title": "Образец", "blocks": [{"type": "text", "value": "а"}]})
        assert status in (200, 503), doc
        status, mcp_answer = request(f"{mcp}/mcp", token, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                         "params": {"name": "make_chart", "arguments": CHART}})
        assert status == 200 and "result" in mcp_answer, mcp_answer

    code, out, err = archive("perms", "check")
    assert code == 0 and out.startswith("Права в порядке"), (out, err)
    assert perms.audit(archive.home) == []
    code, out, err = archive("perms", "check", "--deep")
    assert code == 0, f"глубокая проверка нашла открытое: {out}"
    assert open_places(archive.home) == [], "всё созданное — только владельцу"


def test_каталог_результатов_и_файлы_в_нём_создаются_закрытыми_при_маске_000(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "OUT", str(tmp_path / "out"))
    old = os.umask(0)
    try:
        path, name = O.out_path("chart", ".png")
    finally:
        os.umask(old)
    assert mode(str(tmp_path / "out")) == 0o700 and mode(path) == 0o600 and os.path.basename(path) == name


def test_каталог_результатов_закрыт_и_при_запуске_службы_документов_с_маской_000(archive):
    assert archive("init")[0] == 0
    port = free_port()
    log = open(os.path.join(str(archive.base), "office.log"), "wb")
    proc = subprocess.Popen(["bash", "-c", WIDE, "bash", sys.executable, os.path.join(TOOLS, "office_server.py")],
                            env=archive.env(FLYARCHIVE_OFFICE_PORT=str(port)), stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 30
        while not os.path.isdir(archive.path("out")):
            assert proc.poll() is None and time.time() < deadline, "служба документов не запустилась"
            time.sleep(0.1)
        assert mode(archive.path("out")) == 0o700
    finally:
        proc.terminate()
        proc.wait(10)
    code, out, err = archive("perms", "check")
    assert code == 0, out


# ── места создания по одному: права задаются при создании, а не маской ─
def under_wide_mask(fn):
    old = os.umask(0)
    try:
        return fn()
    finally:
        os.umask(old)


@pytest.fixture
def home(tmp_path):
    """Каталог архива, как его оставляет init: закрыт, внутри пока ничего нет."""
    path = tmp_path / "архив"
    path.mkdir(mode=0o700)
    return path


def test_журнал_обращений_создаёт_каталог_и_файл_закрытыми_при_маске_000(home):
    import journal
    path = str(home / "logs" / "access.jsonl")
    assert under_wide_mask(lambda: journal.Journal(path).write("поиск", "клиент", "search", {}, 200, "ok", 1)) is True
    assert mode(str(home / "logs")) == 0o700 and mode(path) == 0o600


def test_замок_разбора_создаёт_каталог_архива_закрытым_при_маске_000(tmp_path):
    import inbox
    target = str(tmp_path / "новый-архив")

    def lock():
        with inbox.locked(target):
            pass

    under_wide_mask(lock)
    assert mode(target) == 0o700 and mode(os.path.join(target, "inbox.lock")) == 0o600


def test_список_проиндексированного_создаётся_закрытым_при_маске_000(home, monkeypatch):
    import ingest
    import inbox
    (home / "index").mkdir(mode=0o700)
    monkeypatch.setattr(ingest, "index_documents", lambda items, table, embed, **kw: ingest.Indexed(["входящие/п/а.txt"], [], [], 1))
    items = [{"rel": "входящие/п/а.txt", "path": str(home / "corpus" / "а.txt")}]
    result, trouble = under_wide_mask(lambda: inbox._index(str(home), items, object(), None))
    assert trouble is None and result.indexed == ["входящие/п/а.txt"]
    assert mode(str(home / "index" / "ingested.txt")) == 0o600


def test_база_известного_создаёт_каталог_индекса_закрытым_при_маске_000(home):
    import known
    db = str(home / "index" / "known.sqlite")
    under_wide_mask(lambda: known._open(db, create=True).close())
    assert mode(str(home / "index")) == 0o700 and mode(db) == 0o600


def test_службы_закрывают_маску_процесса_сами_и_при_запуске_с_маской_000(archive):
    assert archive("init")[0] == 0
    with running(archive) as (ports, token, pids):
        assert set(pids) == {"webui.py", "office_server.py", "mcp_server.py"}
        for tool, pid in pids.items():
            masks = [line.split()[1] for line in open(f"/proc/{pid}/status", encoding="utf-8") if line.startswith("Umask:")]
            assert masks == ["0077"], (tool, masks)

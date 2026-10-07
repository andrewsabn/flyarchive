"""Каталог архива берётся из настроек, индекс и корпус лежат внутри него (FR-96).

Главное, что держат тесты: модули ядра берут каталог архива из `tools/settings.py` один раз при загрузке и кладут пути от него в свои
именованные значения (`HOME`, `DB`, `CORPUS`, `BASE`, `DONE`); раскладка внутри каталога фиксирована (`index`, `corpus`), отдельных настроек
для индекса и корпуса нет, их выносят символической ссылкой; архив можно держать где угодно; ни команда, ни службы не заглядывают в каталог по умолчанию;
переменная каталога архива и каталог в домашнем каталоге другой программы не действуют (FR-105); негодные настройки при старте — одна строка
и код 2; сами тесты живого архива не читают; в исходниках ядра нет домашнего пути.

Дочерние процессы получают явное окружение (ни одной переменной набора тестов) и пустой временный `HOME`: «каталог по умолчанию» —
это он. Подслушивание обращений к нему ведёт крюк аудита в дочернем процессе, а не только осмотр каталога после работы:
попытка открыть несуществующий файл тоже обращение. Службы на настоящих портах не запускаются: сервер получает порт 0 внутри процесса,
а точка входа сценария перехватывает занятие порта.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import types

import pytest

import foreign
import settings as S

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")
FOREIGN_NAME = foreign.NAME                    # имя другой программы: каталог в домашнем
CLI_NAME = "flyarchive"                        # файл команды в tools
CLI = os.path.join(TOOLS, CLI_NAME)
FOREIGN_HOME = foreign.HOME_VARIABLE           # переменная каталога архива другой программы
FOREIGN_PREFIX = foreign.PREFIX                # приставка переменных другой программы
LEAK = "LEAKq7Zk9"                             # метка «значения»: нигде в выводе при отказе её быть не должно
DOC_NAME = "заметка.txt"
DOC_TEXT = "Текст документа из архива в другом каталоге"


# ── дочерний процесс ────────────────────────────────────────────
WATCH = r"""
import importlib, json, os, runpy, socketserver, sys, threading, types, urllib.error, urllib.parse, urllib.request

TOOLS, OUT, MODE = os.environ["WATCH_TOOLS"], os.environ["WATCH_OUT"], sys.argv[1]
HOME = os.environ["HOME"]
ROOTS = tuple({HOME, os.path.realpath(HOME)})
touched, blocked, result = [], [], {}
REAL_PORTS = {8765, 8766, 8767, 8780, 3080, 8080, 11434}               # настоящие порты служб, модели и ollama: тест их не занимает и не вызывает


def inside(value):
    try:
        text = os.fsdecode(value)
    except (TypeError, ValueError):
        return False
    return any(text == root or text.startswith(root + os.sep) for root in ROOTS)


def hook(event, args):
    if event in ("socket.connect", "socket.bind") and len(args) > 1 and isinstance(args[1], tuple) and len(args[1]) > 1 and args[1][1] in REAL_PORTS:
        blocked.append("%s %s:%s" % (event, args[1][0], args[1][1]))
        raise OSError("настоящий порт службы: тест его не трогает")
    # обращения к каталогу по умолчанию: открытие, создание, чтение списка, переименование, удаление
    if event == "open" or event.startswith(("os.", "shutil.", "glob.")):
        for value in args:
            if isinstance(value, (str, bytes, os.PathLike)) and inside(value):
                touched.append(event + " " + os.fsdecode(value))
                break


def serve_and_get(module_name, label):
    import auth
    module = importlib.import_module(module_name)
    guard = auth.Guard(label)
    token = guard.store.issue("sample", "read")
    module.Handler.guard = guard
    server = module.Server(("127.0.0.1", 0), module.Handler)       # порт 0: настоящие порты служб не занимаются
    threading.Thread(target=server.serve_forever, daemon=True).start()
    query = urllib.parse.urlencode(json.loads(os.environ["WATCH_QUERY"]))
    request = urllib.request.Request("http://127.0.0.1:%d%s?%s" % (server.server_address[1], os.environ["WATCH_PATH"], query),
                                     headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            status, body = reply.status, reply.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    server.shutdown()
    server.server_close()
    return {"status": status, "body": body.decode("utf-8", "replace")}


sys.addaudithook(hook)
sys.modules["lancedb"] = types.ModuleType("lancedb")               # настоящую базу тесты не открывают
sys.path.insert(0, TOOLS)
try:
    if MODE == "attrs":
        for name in json.loads(os.environ.get("WATCH_IMPORT", "[]")):
            importlib.import_module(name)
        values = {}
        for spec in json.loads(os.environ["WATCH_ATTRS"]):
            module, _, attr = spec.partition(".")
            value = getattr(importlib.import_module(module), attr)
            values[spec] = [row[1] for row in value] if attr == "ROOTS" else value
        result["values"] = values
    elif MODE == "cli":
        sys.argv = [os.environ["WATCH_CLI"]] + sys.argv[2:]
        runpy.run_path(os.environ["WATCH_CLI"], run_name="__main__")
    elif MODE == "entry":
        script = os.path.join(TOOLS, sys.argv[2])

        def refuse(self, *args, **kwargs):
            result.setdefault("bound", []).append(list(self.server_address))      # какой адрес служба собиралась занять
            raise SystemExit(99)                                    # дошли до занятия порта: настройки отказа не дали

        socketserver.TCPServer.server_bind = refuse
        sys.argv = [script]
        runpy.run_path(script, run_name="__main__")
    elif MODE == "snippet":
        exec(compile(open(sys.argv[2], encoding="utf-8").read(), "snippet", "exec"), {"result": result, "__name__": "snippet"})
    elif MODE == "live":
        # служба запускается точкой входа в потоке и в самом деле занимает порт; запросы идут по списку WATCH_LIVE (путь, заголовки)
        script, port = os.path.join(TOOLS, sys.argv[2]), int(sys.argv[3])
        sys.argv = [script]
        threading.Thread(target=lambda: runpy.run_path(script, run_name="__main__"), daemon=True).start()
        import socket, time
        for _ in range(150):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        replies = []
        for item in json.loads(os.environ.get("WATCH_LIVE", '[{"path": "/"}]')):
            request = urllib.request.Request("http://127.0.0.1:%d%s" % (port, item["path"]), headers=item.get("headers", {}))
            try:
                with urllib.request.urlopen(request, timeout=20) as reply:
                    replies.append({"status": reply.status, "body": reply.read().decode("utf-8", "replace")})
            except urllib.error.HTTPError as e:
                replies.append({"status": e.code, "body": e.read().decode("utf-8", "replace")})
            except OSError as e:
                replies.append({"status": 0, "body": type(e).__name__})
        result["replies"] = replies
    elif MODE == "office":
        result["answer"] = serve_and_get("office_server", "office")
    elif MODE == "search":
        result["answer"] = serve_and_get("webui", "search")
finally:
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dict(result, touched=touched, blocked=blocked), f, ensure_ascii=False)
"""


@pytest.fixture(scope="module")
def watch(tmp_path_factory):
    path = tmp_path_factory.mktemp("watch") / "watch.py"
    path.write_text(WATCH, encoding="utf-8")
    return str(path)


@pytest.fixture
def lab(tmp_path, watch):
    """Запуск дочернего процесса: пустой HOME («каталог по умолчанию»), явное окружение, результат подслушивания."""
    home = tmp_path / "дом"
    home.mkdir()
    counter = [0]

    def run(mode, *args, env=None, attrs=None, imports=None, query=None, path=None):
        counter[0] += 1
        out = tmp_path / f"watch-{counter[0]}.json"
        environment = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "USERPROFILE": str(home), "PYTHONIOENCODING": "utf-8",
                       "WATCH_TOOLS": TOOLS, "WATCH_OUT": str(out), "WATCH_CLI": CLI, **(env or {})}
        if attrs:
            environment["WATCH_ATTRS"] = json.dumps(attrs)
        if imports:
            environment["WATCH_IMPORT"] = json.dumps(imports)
        if query is not None:
            environment["WATCH_QUERY"], environment["WATCH_PATH"] = json.dumps(query), path
        r = subprocess.run([sys.executable, watch, mode, *args], capture_output=True, text=True, encoding="utf-8", env=environment,
                           timeout=120, cwd=str(tmp_path))
        data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        return types.SimpleNamespace(code=r.returncode, out=r.stdout, err=r.stderr, data=data, home=home, touched=data.get("touched"),
                                     left=sorted(os.listdir(home)))

    run.home = home
    return run


def untouched(r, *, why=""):
    """Каталог по умолчанию цел: ни одного обращения к нему и ничего в нём не создано."""
    assert r.touched == [] and r.left == [], f"{why}: обращения {r.touched}, в каталоге по умолчанию {r.left}"


def archive(base, *, doc_in=None, settings=None):
    """Каталог архива с документом в корпусе (или в каталоге doc_in) и, если нужно, файлом настроек."""
    arch = base / "архив"
    corpus = doc_in or arch / "corpus"
    corpus.mkdir(parents=True)
    (corpus / DOC_NAME).write_text(DOC_TEXT, encoding="utf-8")
    if settings is not None:
        path = arch / "settings.json"
        path.write_text(json.dumps(settings), encoding="utf-8")
        path.chmod(0o600)
    return arch


def expected(arch):
    """Что должно лежать в именованных значениях модулей: индекс и корпус внутри каталога архива, раскладка фиксирована."""
    arch = str(arch)
    index, corpus = os.path.join(arch, "index"), os.path.join(arch, "corpus")
    return {
        "tokens.HOME": arch, "tokens.PATH": os.path.join(arch, "secrets", "tokens.json"),
        "journal.HOME": arch, "journal.PATH": os.path.join(arch, "logs", "access.jsonl"),
        "known.HOME": arch, "known.DB": os.path.join(index, "known.sqlite"), "known.CORPUS": corpus,
        "intake.CORPUS": corpus,
        "auth.HOME": arch,
        "office_server.BASE": arch, "office_server.CORPUS": corpus, "office_server.OUT": os.path.join(arch, "out"),
        "search.DB": os.path.join(index, "lance"),
        "webui.CORPUS": corpus,
        "index_more.DB": os.path.join(index, "lance"), "index_more.DONE": os.path.join(index, "ingested.txt"), "index_more.CORPUS": corpus,
    }


MODULES = sorted({spec.split(".")[0] for spec in expected("/x")})


def scenarios(base):
    """Способы указать каталог архива: (идентификатор, окружение, ожидаемое). Каталоги ещё не созданы."""
    arch, decoy = base / "архив", base / "чужой"
    new, old = "FLYARCHIVE_", FOREIGN_PREFIX
    return {
        "new": ({new + "HOME": str(arch)}, expected(arch)),
        "both": ({new + "HOME": str(arch), old + "HOME": str(decoy)}, expected(arch)),       # чужая переменная рядом со своей ничего не меняет
    }


# ── набор тестов не читает живой архив ──────────────────────────
def test_во_время_набора_тестов_каталог_архива_временный_а_не_домашний():
    loaded = S.load()
    home = loaded["home"]
    user = os.path.expanduser("~")
    assert loaded.source("home") == "env" and os.environ.get("FLYARCHIVE_HOME") == home, "набор не задал временный каталог архива"
    assert home != os.path.join(user, "flyarchive"), "каталог архива в наборе — тот, что по умолчанию"
    temp = os.path.realpath(tempfile.gettempdir())
    assert os.path.commonpath([os.path.realpath(home), temp]) == temp, "каталог архива в наборе лежит не во временном каталоге"
    assert os.path.isdir(home), "временный каталог архива создаётся на время набора"


def test_модули_ядра_в_наборе_загружены_с_временным_каталогом_архива():
    import auth, index_more, intake, journal, known, office_server, search, tokens, webui   # noqa: E401
    loaded = S.load()
    want = expected(loaded["home"])
    got = {"tokens.HOME": tokens.HOME, "tokens.PATH": tokens.PATH, "journal.HOME": journal.HOME, "journal.PATH": journal.PATH,
           "known.HOME": known.HOME, "known.DB": known.DB, "known.CORPUS": known.CORPUS, "intake.CORPUS": intake.CORPUS, "auth.HOME": auth.HOME,
           "office_server.BASE": office_server.BASE, "office_server.CORPUS": office_server.CORPUS, "office_server.OUT": office_server.OUT,
           "search.DB": search.DB, "webui.CORPUS": webui.CORPUS,
           "index_more.DB": index_more.DB, "index_more.DONE": index_more.DONE, "index_more.CORPUS": index_more.CORPUS}
    assert got == want


def test_набор_создаёт_временный_каталог_перекрывает_заранее_заданный_и_убирает_после_себя(tmp_path):
    decoy = str(tmp_path / "заранее-заданный")
    code = ("import os, sys\n"
            "os.environ['FLYARCHIVE_HOME'] = %r\n" % decoy +
            "sys.path.insert(0, %r)\n" % HERE +
            "import conftest\n"
            "home = os.environ['FLYARCHIVE_HOME']\n"
            "print(home, os.path.isdir(home))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path),
                       env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "PYTHONIOENCODING": "utf-8",
                            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)})        # pytest лежит в пользовательском каталоге пакетов
    assert r.returncode == 0, r.stderr
    home, existed = r.stdout.split()[-2:]
    assert home != decoy and existed == "True", "набор не перекрыл заранее заданную переменную или не создал каталог"
    assert not os.path.exists(home), "временный каталог архива остался после набора"


# ── модули берут пути из настроек ───────────────────────────────
@pytest.mark.parametrize("scenario", list(scenarios(pathlib.Path("/x"))))
def test_модули_ядра_берут_каталог_архива_индекса_и_корпуса_из_настроек(lab, tmp_path, scenario):
    env, want = scenarios(tmp_path)[scenario]
    r = lab("attrs", env=env, attrs=list(want))
    assert r.code == 0, r.err
    assert r.data["values"] == want
    untouched(r, why=scenario)
    assert not (tmp_path / "чужой").exists(), "каталог, перебитый переменной проекта, тронут"


@pytest.mark.parametrize("module", MODULES)
def test_каждый_модуль_сам_берёт_пути_из_настроек_а_не_от_порядка_загрузки(lab, tmp_path, module):
    want = {k: v for k, v in expected(tmp_path / "архив").items() if k.startswith(module + ".")}
    r = lab("attrs", env={"FLYARCHIVE_HOME": str(tmp_path / "архив")}, attrs=list(want))
    assert r.code == 0, r.err
    assert r.data["values"] == want
    untouched(r)


def test_корпус_и_индекс_всех_переведённых_модулей_лежат_внутри_каталога_архива(lab, tmp_path):
    arch = tmp_path / "архив"
    want = expected(arch)
    r = lab("attrs", env={"FLYARCHIVE_HOME": str(arch)}, attrs=list(want) + ["index_more.ROOTS"])
    assert r.code == 0, r.err
    values = r.data["values"]
    roots = values.pop("index_more.ROOTS")
    assert values == want
    corpus = {k: v for k, v in values.items() if k.endswith(".CORPUS")}
    index = {k: v for k, v in values.items() if k in ("known.DB", "search.DB", "index_more.DB", "index_more.DONE")}
    assert set(corpus) == {"known.CORPUS", "intake.CORPUS", "office_server.CORPUS", "webui.CORPUS", "index_more.CORPUS"}
    assert set(index) == {"known.DB", "search.DB", "index_more.DB", "index_more.DONE"}
    assert all(v == str(arch / "corpus") for v in corpus.values()), corpus
    assert all(v.startswith(str(arch / "index") + os.sep) for v in index.values()), index
    assert roots and all(path.startswith(str(arch / "corpus") + os.sep) for path in roots), roots
    untouched(r)


@pytest.mark.parametrize("module", ["mcp_server", "gateway"])
def test_переходник_mcp_и_шлюз_видят_каталог_архива_из_настроек(lab, tmp_path, module):
    arch = tmp_path / "архив"
    r = lab("attrs", env={"FLYARCHIVE_HOME": str(arch)}, imports=[module], attrs=["auth.HOME", "tokens.HOME", "journal.HOME"])
    assert r.code == 0, r.err
    assert r.data["values"] == {"auth.HOME": str(arch), "tokens.HOME": str(arch), "journal.HOME": str(arch)}
    untouched(r)


# ── каталог по умолчанию один: чужого в модулях нет ─────────────
@pytest.mark.foreign_name
@pytest.mark.parametrize("new_exists, old_exists", [(False, False), (True, False), (False, True), (True, True)])
def test_без_переменных_архивом_всегда_считается_свой_каталог_а_чужой_не_читается_и_не_создаётся(lab, new_exists, old_exists):
    for name, exists in (("flyarchive", new_exists), (FOREIGN_NAME, old_exists)):
        if exists:
            (lab.home / name).mkdir()
            (lab.home / name / "settings.json").write_text(json.dumps({"search_port": 9707 if name == FOREIGN_NAME else 9606}), encoding="utf-8")
            (lab.home / name / "settings.json").chmod(0o600)
    chosen = lab.home / "flyarchive"
    r = lab("attrs", attrs=list(expected(chosen)) + ["webui.PORT"])
    assert r.code == 0, r.err
    assert r.data["values"].pop("webui.PORT") == (9606 if new_exists else 8765), "порт — из файла своего каталога или умолчание, не из чужого каталога"
    assert r.data["values"] == expected(chosen)
    assert r.left == sorted(n for n, e in (("flyarchive", new_exists), (FOREIGN_NAME, old_exists)) if e), "выбор каталога ничего не создаёт"
    # единственное, что модули открывают в домашнем каталоге, — файл настроек и таблица источников (FR-99) своего каталога:
    # в чужом они ничего не ищут и не открывают
    assert r.touched and all(t.startswith("open ") and t.endswith((str(chosen / "settings.json"), str(chosen / "sources.json"))) for t in r.touched), r.touched
    assert not any(FOREIGN_NAME in t for t in r.touched), r.touched


@pytest.mark.foreign_name
def test_чужая_переменная_каталога_архива_не_действует_ни_в_модулях_ни_в_команде(lab, tmp_path):
    arch = tmp_path / "чужой-архив"
    chosen = lab.home / "flyarchive"
    r = lab("attrs", env={FOREIGN_HOME: str(arch)}, attrs=list(expected(chosen)))
    assert r.code == 0, r.err
    assert r.data["values"] == expected(chosen), "модули взяли каталог из чужой переменной"
    assert not arch.exists()
    added = lab("cli", "token", "add", "sample", "--level", "read", env={FOREIGN_HOME: str(arch)})
    assert added.code == 0 and "ba_" in added.out, added.err
    assert (chosen / "secrets" / "tokens.json").is_file(), "токен записан не в каталог по умолчанию"
    assert not arch.exists(), "чужая переменная сработала: каталог из неё создан"


# ── команда работает в каталоге из переменной ───────────────────
ARCHIVE_VARIABLES = {
    "new": lambda arch, decoy: {"FLYARCHIVE_HOME": str(arch)},
    "both": lambda arch, decoy: {"FLYARCHIVE_HOME": str(arch), FOREIGN_HOME: str(decoy)},
}


@pytest.mark.parametrize("how", list(ARCHIVE_VARIABLES))
def test_команда_работает_в_каталоге_из_переменной_и_не_заглядывает_в_каталог_по_умолчанию(lab, tmp_path, how):
    arch, decoy = tmp_path / "архив", tmp_path / "чужой"
    env = ARCHIVE_VARIABLES[how](arch, decoy)
    added = lab("cli", "token", "add", "sample", "--level", "read", env=env)
    assert added.code == 0 and "ba_" in added.out, added.err
    listed = lab("cli", "token", "list", "--json", env=env)
    assert listed.code == 0, listed.err
    assert [row["name"] for row in json.loads(listed.out)] == ["sample"]
    assert (arch / "secrets" / "tokens.json").is_file(), "токен записан не в каталог архива из переменной"
    for r in (added, listed):
        untouched(r, why=how)
    assert not decoy.exists()


# ── сервер документов читает документ из архива в другом каталоге (дефект A2) ─
HOW = ["new", "both", "home-link", "corpus-link"]


def environment_for(how, base):
    """Окружение для способа указать архив. home-link: каталог архива — символическая ссылка на другой каталог; corpus-link: корпус внутри
    архива — ссылка на каталог на другом диске (так корпус выносят, когда отдельной настройки для него нет)."""
    arch = base / "архив"
    if how == "corpus-link":
        disk = base / "другой-диск" / "корпус"
        archive(base, doc_in=disk)
        arch.mkdir()
        (arch / "corpus").symlink_to(disk, target_is_directory=True)
        return {"FLYARCHIVE_HOME": str(arch)}
    archive(base)
    if how == "home-link":
        link = base / "ссылка-на-архив"
        link.symlink_to(arch, target_is_directory=True)
        return {"FLYARCHIVE_HOME": str(link)}
    return {"new": {"FLYARCHIVE_HOME": str(arch)}, "both": {"FLYARCHIVE_HOME": str(arch), FOREIGN_HOME: str(base / "чужой")}}[how]


@pytest.mark.parametrize("how", HOW)
def test_сервер_документов_читает_документ_из_архива_в_другом_каталоге(lab, tmp_path, how):
    r = lab("office", env=environment_for(how, tmp_path), query={"path": DOC_NAME}, path="/read")
    assert r.code == 0, r.err
    answer = r.data["answer"]
    assert answer["status"] == 200 and DOC_TEXT in json.loads(answer["body"]).get("text", ""), "сервер документов не нашёл документ в архиве"
    untouched(r, why=how)
    assert not (tmp_path / "чужой").exists()


@pytest.mark.parametrize("how", HOW)
def test_сервер_поиска_отдаёт_документ_из_корпуса_архива_в_другом_каталоге(lab, tmp_path, how):
    r = lab("search", env=environment_for(how, tmp_path), query={"p": DOC_NAME}, path="/doc")
    assert r.code == 0, r.err
    assert r.data["answer"]["status"] == 200 and DOC_TEXT in r.data["answer"]["body"], "страница поиска не нашла документ в корпусе архива"
    untouched(r, why=how)
    assert not (tmp_path / "чужой").exists()


@pytest.mark.parametrize("how", ["home-link", "corpus-link"])
def test_пути_модулей_и_команда_в_архиве_по_символической_ссылке(lab, tmp_path, how):
    env = environment_for(how, tmp_path)
    arch = env["FLYARCHIVE_HOME"]
    r = lab("attrs", env=env, attrs=list(expected(arch)))
    assert r.code == 0, r.err
    assert r.data["values"] == expected(arch), "пути считаются от каталога архива как он задан, ссылка не раскрывается и не заменяется"
    added = lab("cli", "token", "add", "sample", "--level", "read", env=env)
    assert added.code == 0 and "ba_" in added.out, added.err
    assert (tmp_path / "архив" / "secrets" / "tokens.json").is_file(), "токен записан не в архив, на который указывает ссылка"
    for run in (r, added):
        untouched(run, why=how)


# ── негодные настройки при старте: одна строка и код 2 ──────────
BAD_FILES = {
    "wrong-type": (json.dumps({"search_port": LEAK}), 0o600),
    "unknown-key": (json.dumps({"nope": LEAK}), 0o600),
    "not-json": ('{"search_port": "' + LEAK + '", ', 0o600),
    "open-permissions": (json.dumps({"search_port": 9001}), 0o666),
}


def bad_archive(base, raw, mode):
    arch = base / "архив"
    arch.mkdir()
    path = arch / "settings.json"
    path.write_text(raw, encoding="utf-8")
    path.chmod(mode)
    return arch


def one_line(text):
    return text.startswith("настройки: ") and len(text.strip().splitlines()) == 1 and text.endswith("\n") and "Traceback" not in text


@pytest.mark.parametrize("what", list(BAD_FILES))
def test_негодный_файл_настроек_команда_завершается_кодом_2_одной_строкой(tmp_path, what):
    arch = bad_archive(tmp_path, *BAD_FILES[what])
    r = subprocess.run([sys.executable, CLI, "token", "list"], capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path), timeout=60,
                       env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "PYTHONIOENCODING": "utf-8", "FLYARCHIVE_HOME": str(arch)})
    assert r.returncode == 2 and r.stdout == "", r.stderr[-300:]
    assert one_line(r.stderr) and LEAK not in r.stderr + r.stdout, r.stderr[-300:]


@pytest.mark.parametrize("script", ["office_server.py", "webui.py", "mcp_server.py"])
@pytest.mark.parametrize("what", list(BAD_FILES))
def test_негодный_файл_настроек_службы_не_запускаются_код_2_одной_строкой(lab, tmp_path, script, what):
    arch = bad_archive(tmp_path, *BAD_FILES[what])
    r = lab("entry", script, env={"FLYARCHIVE_HOME": str(arch)})
    assert r.code == 2 and r.out == "", f"{script}: код {r.code}, {r.err[-300:]}"
    assert one_line(r.err) and LEAK not in r.err + r.out, r.err[-300:]
    untouched(r, why=script)


def test_негодное_значение_переменной_команда_и_служба_завершаются_кодом_2_без_значения(lab, tmp_path):
    variable = "FLYARCHIVE_SEARCH_PORT"
    env = {"FLYARCHIVE_HOME": str(tmp_path / "архив"), variable: LEAK}
    r = lab("cli", "token", "list", env=env)
    assert r.code == 2 and one_line(r.err) and LEAK not in r.err + r.out and variable in r.err
    r = lab("entry", "office_server.py", env=env)
    assert r.code == 2 and one_line(r.err) and LEAK not in r.err + r.out and variable in r.err
    untouched(r)


@pytest.mark.foreign_name
def test_негодное_значение_чужой_переменной_ничего_не_останавливает_её_не_читают(lab, tmp_path):
    env = {"FLYARCHIVE_HOME": str(tmp_path / "архив"), FOREIGN_PREFIX + "SEARCH_PORT": LEAK}
    r = lab("cli", "token", "list", env=env)
    assert r.code == 0 and r.err == "" and LEAK not in r.out, r.err
    r = lab("entry", "office_server.py", env=env)
    assert r.code == 99 and r.data["bound"] == [["127.0.0.1", 8766]], r.err[-300:]       # служба дошла до занятия порта, как без переменной
    untouched(r)


# ── в исходниках ядра нет домашнего пути ────────────────────────
# Исключений нет (FR-100): ни одному файлу `tools` не разрешены домашние пути и данные владельца. Словарь пуст: добавить в него файл —
# значит отменить это требование, и такая правка видна в тесте.
EXCLUDED = {}
HOME_PATHS = [("домашний путь /home/", r"/home/"), ("домашний путь /Users/", r"/Users/"), ("диск Windows C:\\Users", r"(?i)\b[a-z]:[\\/]+users[\\/]"),
              ("чужой каталог по умолчанию", r"(?i)(?:~|\$HOME|\$\{HOME\})/" + re.escape(FOREIGN_NAME))]
HOME_VARIABLE = ("прямое чтение переменной каталога архива", r"\b(?:FLYARCHIVE|" + re.escape(FOREIGN_NAME.upper()) + r")_HOME\b")


def hits(text, patterns=HOME_PATHS):
    return [(name, number) for number, line in enumerate(text.splitlines(), 1) for name, pattern in patterns if re.search(pattern, line)]


def core_files():
    names = sorted(n for n in os.listdir(TOOLS) if n.endswith(".py") and os.path.isfile(os.path.join(TOOLS, n)))
    return names + [CLI_NAME]


def test_поиск_запрещённого_находит_каждый_образец_и_не_трогает_чистый_текст():
    samples = {"домашний путь /home/": "p = '" + "/home" + "/ivan/corpus'", "домашний путь /Users/": "p = '" + "/Users" + "/ivan/corpus'",
               "диск Windows C:\\Users": "p = r'" + "C:" + "\\Users\\ivan'", "чужой каталог по умолчанию": "p = expanduser('~/" + FOREIGN_NAME + "')",
               "чужой каталог по умолчанию ": "BASE=$HOME/" + FOREIGN_NAME}
    for name, line in samples.items():
        assert name.strip() in [n for n, _ in hits(line)], name
    assert hits("p = settings.load()['inbox_dir']  # " + FOREIGN_NAME + " как слово в тексте, а не путь") == []
    assert [n for n, _ in hits("os.environ.get('" + "FLYARCHIVE" + "_HOME')", [HOME_VARIABLE])] == [HOME_VARIABLE[0]]
    assert [n for n, _ in hits("os.environ.get('" + FOREIGN_HOME + "')", [HOME_VARIABLE])] == [HOME_VARIABLE[0]]


def test_список_исключений_пуст_и_проверяется_каждый_файл_ядра():
    names = set(core_files())
    assert EXCLUDED == {}, f"в списке исключений остались файлы: {sorted(EXCLUDED)}"
    assert len(names) >= 30 and {"settings.py", "index_more.py", "office_server.py", "fix_dates.py", "dedupe_index.py", CLI_NAME} <= names
    checked = {n for n in core_files() if n not in EXCLUDED}
    assert checked == names, "проверяется не каждый файл ядра"


@pytest.mark.foreign_name
@pytest.mark.parametrize("name", [n for n in core_files() if n not in EXCLUDED])
def test_в_файле_ядра_нет_домашнего_пути_и_чужого_каталога_по_умолчанию(name):
    with open(os.path.join(TOOLS, name), encoding="utf-8") as f:
        text = f.read()
    assert hits(text) == [], f"{name}: {hits(text)}"
    if name != "settings.py":
        assert hits(text, [HOME_VARIABLE]) == [], f"{name}: переменную каталога архива читает только settings.py {hits(text, [HOME_VARIABLE])}"


# ── в исходниках ядра нет адресов служб и чужих переменных под одну машину (FR-97) ──
# Адреса и порты служб — только в settings.py (умолчания), в комментариях и докстроках. Чужих переменных в коде нет нигде (FR-105): их не читает ни один модуль.
SERVICE_PORTS = (8765, 8766, 8767, 11434, 8080)                 # поиск, документы, MCP, векторы, локальная модель: их умолчания лежат в settings.py
OTHER_PORTS = (8780, 3080)                                      # шлюз и веб-оболочка: в коде ядра числом тоже не стоят
ADDRESS = re.compile(r":(?:%s)\b" % "|".join(map(str, SERVICE_PORTS)))
FOREIGN_VARIABLES = {FOREIGN_PREFIX + key.upper() for key in S.SCHEMA} | {foreign.KEY}      # чужие имена: чужая приставка + любая настройка, чужой ключ модели
FOREIGN_READ = re.compile("|".join(sorted(map(re.escape, FOREIGN_VARIABLES), key=len, reverse=True)))


def code_constants(source):
    """Строки и целые константы исходника без докстрок: [(номер строки, значение)]."""
    import ast
    tree = ast.parse(source)
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)}
    def folded(node):
        """Строка, склеенная из строковых констант через +, или None: так имя переменной не спрятать от поиска."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = folded(node.left), folded(node.right)
            return left + right if left is not None and right is not None else None
        return None

    found = [(node.lineno, node.value) for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and id(node) not in docstrings and isinstance(node.value, (str, int)) and not isinstance(node.value, bool)]
    found += [(node.lineno, text) for node in ast.walk(tree) if isinstance(node, ast.BinOp) and (text := folded(node)) is not None]
    return found


def machine_hits(source):
    """Что в исходнике привязано к одной машине: [(что, номер строки)]."""
    found = []
    for number, value in code_constants(source):
        if isinstance(value, str) and ADDRESS.search(value):
            found.append(("адрес службы строкой", number))
        if isinstance(value, str) and FOREIGN_READ.search(value):
            found.append(("чужая переменная", number))
        if isinstance(value, int) and value in SERVICE_PORTS + OTHER_PORTS:
            found.append(("порт службы числом", number))
    return found


def read_core(name):
    with open(os.path.join(TOOLS, name), encoding="utf-8") as f:
        return f.read()


@pytest.mark.foreign_name
def test_поиск_привязанного_к_машине_находит_каждый_образец_и_не_трогает_чистое():
    samples = {
        "адрес службы строкой": 'url = "http://127.0.0.1' + ":87" + '65"',
        "чужая переменная": 'env.get("' + FOREIGN_PREFIX + 'SEARCH_PORT")',
        "чужая переменная ": 'env.get("' + foreign.KEY + '")',
        "порт службы числом": "PORT = 87" + "66",
    }
    for what, line in samples.items():
        assert what.strip() in [name for name, _ in machine_hits(line)], what
    split = 'env.get("' + FOREIGN_PREFIX[:4] + '" + "' + FOREIGN_PREFIX[4:] + 'SEARCH_PORT")'
    assert "чужая переменная" in [name for name, _ in machine_hits(split)], "имя, склеенное из кусков"
    quiet = '"""Слушает http://127.0.0.1:' + '8765"""\nPORT = settings.load()["search_port"]  # адрес по умолчанию 127.0.0.1' + ":11434\n"
    assert machine_hits(quiet) == [], "адреса в докстроках и комментариях разрешены"
    clean = 'PORT = settings.load()["search_port"]\nURL = f"http://127.0.0.1:{PORT}"\nenv.get("FLYARCHIVE_LLM_KEY")\nTIMEOUT = 300\n'
    assert machine_hits(clean) == []


@pytest.mark.foreign_name
def test_чужая_переменная_и_порт_считаются_привязкой_в_любой_строке_и_между_комментариями_тоже():
    # никакая разметка файла ничего не разрешает: строки между комментариями-границами ищутся так же, как остальные
    source = "\n".join(["a = 1", "# ── раздел особых случаев ──", 'old = "' + FOREIGN_PREFIX + 'SEARCH_PORT"', "port = 87" + "65",
                        "# ── конец раздела ──", 'late = "' + FOREIGN_PREFIX + 'SEARCH_PORT"']) + "\n"
    assert sorted(machine_hits(source)) == [("порт службы числом", 4), ("чужая переменная", 3), ("чужая переменная", 6)]


@pytest.mark.foreign_name
@pytest.mark.parametrize("name", [n for n in core_files() if n not in EXCLUDED])
def test_в_файле_ядра_нет_адресов_служб_портов_числом_и_чужих_переменных_под_одну_машину(name):
    source = read_core(name)
    if name == "settings.py":
        found = [h for h in machine_hits(source) if h[0] not in ("адрес службы строкой", "порт службы числом")]    # умолчания схемы лежат здесь
    else:
        found = machine_hits(source)
    assert found == [], f"{name}: {found}"


def test_умолчания_адресов_и_портов_лежат_в_settings_py_и_только_там():
    values = [value for _, value in code_constants(read_core("settings.py"))]
    for port in SERVICE_PORTS + OTHER_PORTS:
        assert port in values or any(isinstance(v, str) and ":%d" % port in v for v in values), f"в схеме настроек нет умолчания с портом {port}"
    elsewhere = [n for n in core_files() if n not in EXCLUDED and n != "settings.py" and machine_hits(read_core(n))]
    assert elsewhere == []


@pytest.mark.foreign_name
def test_чужая_переменная_ключа_модели_не_названа_ни_в_одном_файле_ядра():
    named = {name: [number for number, line in enumerate(read_core(name).splitlines(), 1) if foreign.KEY in line] for name in core_files()}
    assert {name: lines for name, lines in named.items() if lines} == {}, "ядро называет чужую переменную ключа: читать её нельзя, советовать тоже"


# ── три службы слушают только петлю: адрес прослушивания не настраивается (FR-97) ──
# Сервер поиска и сервер документов, выставленные в сеть напрямую, обходили бы шлюз: правило «токен с других машин — только в MCP, документ —
# только по подписанной ссылке» держит заголовок, который ставит шлюз. Наружу смотрит один шлюз (у него gateway_bind, «все адреса» он отвергает).
LOOPBACK_SERVICES = ["webui.py", "office_server.py", "mcp_server.py"]


def listen_hits(source):
    """Что в исходнике службы расходится с «слушает только петлю»: [(что, номер строки)]."""
    import ast
    found = [("все адреса строкой", number) for number, value in code_constants(source) if isinstance(value, str) and "0.0.0.0" in value]
    found += [("адрес прослушивания из настроек", number) for number, line in enumerate(source.splitlines(), 1) if "bind_" + "host" in line]
    hosts = [node for node in ast.parse(source).body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "HOST" for t in node.targets)]
    if not (len(hosts) == 1 and isinstance(hosts[0].value, ast.Constant) and hosts[0].value.value == "127.0.0.1"):
        found.append(("HOST — не константа петли", hosts[0].lineno if hosts else 0))
    return found


def test_поиск_расхождений_с_петлёй_находит_каждый_образец_и_не_трогает_чистое():
    assert listen_hits('HOST, PORT = _SETTINGS["' + "bind_" + 'host"], 8765\n')[0][0] in ("адрес прослушивания из настроек", "HOST — не константа петли")
    assert {n for n, _ in listen_hits('HOST = settings.load()["' + "bind_" + 'host"]\n')} == {"адрес прослушивания из настроек", "HOST — не константа петли"}
    assert "все адреса строкой" in [n for n, _ in listen_hits('HOST = "127.0.0.1"\nSERVER = ("' + "0.0.0" + '.0", 1)\n')]
    assert "HOST — не константа петли" in [n for n, _ in listen_hits('HOST = "192.0.2.7"\n')]
    assert "HOST — не константа петли" in [n for n, _ in listen_hits('PORT = 1\n')]
    assert listen_hits('"""слушает не 0.0.0.0"""\nHOST = "127.0.0.1"  # петля\n') == []


@pytest.mark.parametrize("name", LOOPBACK_SERVICES)
def test_в_трёх_службах_нет_всех_адресов_и_чтения_адреса_прослушивания_из_настроек(name):
    assert listen_hits(read_core(name)) == [], name

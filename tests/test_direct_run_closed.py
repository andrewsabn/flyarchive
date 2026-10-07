"""Сценарии tools/, которые пишут в каталог архива при прямом запуске, закрывают маску сами (NFR-01а, FR-107).

`flyarchive` и три службы зовут `perms.close_umask()` при запуске; `python3 tools/index_more.py` (таблица индекса и `ingested.txt`) и `python3 tools/search.py`
раньше — нет: запущенные руками с маской 000 они оставляли созданное открытым. Теперь каждый сценарий с `if __name__ == "__main__"`, который создаёт файлы в
каталоге архива, закрывает маску так же; кто не создаёт — назван ниже вместе с причиной, и новый сценарий без того и другого краснит сторожа.
Файлам, которым права давала только маска, они задаются при создании: список `index/dedupe-dropped-<время>.txt` и отчёты `flyarchive check`. Проверка таких
файлов идёт с нулевой маской, которую команда сама ставит вместо 077 (подмена `perms.PRIVATE_UMASK` в sitecustomize дочернего процесса): иначе маска команды
скрыла бы, чем права заданы. Настоящий путь: процессы в выдуманной машине (свой домашний каталог, подставная служба векторов), `perms check --deep` без нарушений.
"""
import os
import site
import stat
import subprocess
import sys

import pytest

from archivekit import ROOT, Archive
from test_cli import cli, indexed  # noqa: F401 — корпус с повторами письма, база известного и индекс для `index dedupe`
from test_created_files_closed import WIDE, mode, open_places
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

lancedb = pytest.importorskip("lancedb")
pytestmark = [pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной"),
              pytest.mark.skipif(os.name == "nt", reason="права и маска POSIX")]

TOOLS = os.path.join(ROOT, "tools")
FLYARCHIVE = os.path.join(TOOLS, "flyarchive")
MAIN = 'if __name__ == "__main__":'
# сценарии с точкой входа, которые закрывают маску при запуске: пишут в каталог архива сами
CLOSES = {"index_more.py": "таблица индекса и список пройденного index/ingested.txt",
          "search.py": "читает индекс; закрыта на случай библиотеки, которой понадобится файл (кэш, временные)",
          "webui.py": "служба поиска: журнал, каталоги", "office_server.py": "служба документов: каталог результатов out",
          "mcp_server.py": "переходник MCP: журнал"}
# с точкой входа, но в каталоге архива ничего не создают
EXEMPT = {"gateway.py": "шлюз: проксирует запросы, файлов не создаёт", "messages.py": "печатает каталог сообщений",
          "settings.py": "печатает настройки", "preview_container.py": "подкоманда reap: убирает свои контейнеры docker",
          "preview_worker.py": "рабочий просмотра: пишет только в выходной каталог, который создал родитель (mkdtemp, 0700); код в песочнице, без импорта tools"}


@pytest.fixture
def archive(tmp_path, vectors):
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})


def sources_with_main():
    for name in sorted(os.listdir(TOOLS)):
        path = os.path.join(TOOLS, name)
        if name.endswith(".py") and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if MAIN in text:
                yield name, text


# ── сторож: ни один сценарий не остаётся без решения ────────────
def test_каждый_сценарий_с_точкой_входа_либо_закрывает_маску_либо_назван_среди_тех_кто_в_архиве_не_пишет():
    found = dict(sources_with_main())
    assert set(found) == set(CLOSES) | set(EXEMPT), "новый сценарий с точкой входа: закрой маску (perms.close_umask()) или назови его в EXEMPT с причиной"
    for name in CLOSES:
        assert "perms.close_umask()" in found[name].split(MAIN)[-1], f"{name}: маска при запуске не закрывается"
    for name in EXEMPT:
        assert "perms.close_umask()" not in found[name].split(MAIN)[-1] or name in CLOSES, name


# ── маска процесса к концу прямого запуска ──────────────────────
def exit_mask(archive, tool, *args):
    """Маска процесса к концу прямого запуска сценария, запущенного с маской 000: строка вида 0077."""
    folder = os.path.join(str(archive.base), "маска")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "значение")
    with open(os.path.join(folder, "sitecustomize.py"), "w", encoding="utf-8") as f:
        f.write("import atexit, os\n\n\ndef _write():\n    m = os.umask(0)\n    os.umask(m)\n"
                "    with open(os.environ['MASK_OUT'], 'w') as f:\n        f.write('%04o' % m)\n\n\natexit.register(_write)\n")
    env = archive.env(MASK_OUT=out, PYTHONPATH=os.pathsep.join(p for p in (folder, site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p))
    subprocess.run(["bash", "-c", WIDE, "bash", sys.executable, os.path.join(TOOLS, tool), *args], env=env, capture_output=True, text=True,
                   encoding="utf-8", timeout=120, cwd=str(archive.base))
    with open(out, encoding="utf-8") as f:
        return f.read()


def test_search_при_прямом_запуске_с_маской_000_закрывает_её(archive):
    assert archive("init")[0] == 0
    assert exit_mask(archive, "search.py", "запрос") == "0077"


def test_index_more_при_прямом_запуске_с_маской_000_закрывает_её(archive):
    assert archive("init")[0] == 0
    assert exit_mask(archive, "index_more.py") == "0077"


def test_проверка_маски_видит_незакрытую(archive):
    """Сама проверка не пуста: путь, который маску не закрывает (самопроверка идёт до закрытия), оставляет 0000."""
    assert exit_mask(archive, "search.py", "--selftest") == "0000"


# ── index_more.py: всё созданное закрыто ────────────────────────
def test_index_more_при_прямом_запуске_с_маской_000_оставляет_архив_закрытым(archive):
    assert archive("init")[0] == 0
    folder = os.path.join(archive.path("corpus"), "входящие", "пачка")
    before = os.umask(0o077)                                          # образцы кладутся закрытыми, как их положила бы приёмка: проверяется только то, что создаст сценарий
    try:
        os.makedirs(folder)
    finally:
        os.umask(before)
    for name, text in {"а.txt": "Протокол встречи: перенос релиза на четверг.", "б.md": "# План\n\nСогласовать перенос работ.\n"}.items():
        with os.fdopen(os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT, 0o600), "w", encoding="utf-8") as f:
            f.write(text)
    assert open_places(archive.home) == []
    r = subprocess.run(["bash", "-c", WIDE, "bash", sys.executable, os.path.join(TOOLS, "index_more.py")], env=archive.env(), capture_output=True, text=True,
                       encoding="utf-8", timeout=180, cwd=str(archive.base))
    assert r.returncode == 0 and "наполнение закончено" in r.stdout, (r.stdout, r.stderr)
    assert archive.table().count_rows() > 0, "в индекс что-то попало: иначе библиотека ничего не создавала бы"
    assert mode(archive.path("index", "ingested.txt")) == 0o600 and os.path.getsize(archive.path("index", "ingested.txt")) > 0
    code, out, err = archive("perms", "check", "--deep")
    assert code == 0, f"глубокая проверка нашла открытое: {out}"
    assert open_places(archive.home) == [], "всё созданное прямым запуском — только владельцу"


# ── файлы, которым права давала только маска ────────────────────
def neutral_mask_env(base):
    """Дочерний процесс, где команда вместо маски 077 ставит 000: права файлов тогда задаются только при создании."""
    folder = os.path.join(str(base), "нулевая-маска")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "sitecustomize.py"), "w", encoding="utf-8") as f:
        f.write("import sys\nsys.path.insert(0, %r)\nimport perms\nperms.PRIVATE_UMASK = 0\n" % TOOLS)
    user = os.path.join(str(base), "user")                    # домашний каталог выдуманный; библиотеки пользователя настоящие — через путь импорта
    os.makedirs(user, exist_ok=True)
    return {**os.environ, "HOME": user, "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join(p for p in (folder, site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p)}


def run_command(env, *args):
    r = subprocess.run([sys.executable, FLYARCHIVE, *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    return r.returncode, r.stdout, r.stderr


def test_список_удалённых_повторов_создаётся_с_правами_владельца_а_не_по_маске(cli, indexed, tmp_path):
    env = {**neutral_mask_env(tmp_path), "FLYARCHIVE_HOME": cli.home}
    code, out, err = run_command(env, "index", "dedupe", "--apply")
    assert code == 0 and "удалено фрагментов: 2" in out, (out, err)
    (listing,) = [f for f in os.listdir(os.path.join(cli.home, "index")) if f.startswith("dedupe-dropped-")]
    assert mode(os.path.join(cli.home, "index", listing)) == 0o600


def test_отчёты_команды_check_создаются_с_правами_владельца_а_не_по_маске(tmp_path):
    source = tmp_path / "пачка"
    source.mkdir()
    (source / "заметка.txt").write_text("Обычная заметка о переносе релиза.", encoding="utf-8")
    env = {**neutral_mask_env(tmp_path), "FLYARCHIVE_HOME": str(tmp_path / "архив")}
    into = tmp_path / "разбор"
    code, out, err = run_command(env, "check", str(source), "--into", str(into), "--without-archive")
    assert code == 0, (out, err)
    for name in ("отчёт.md", "отчёт.jsonl"):
        assert stat.S_IMODE(os.stat(into / name).st_mode) == 0o600, name
    assert stat.S_IMODE(os.stat(into).st_mode) == 0o700

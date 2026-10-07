"""Общая обвязка тестов.

Тесты не трогают настоящий индекс, ollama, видеокарту и сеть: сервер поиска
получает подставную функцию поиска, а серверы поднимаются на случайном порту
петлевого адреса. Так набор можно гонять на любой машине и в любой момент,
в том числе когда видеокарта занята.
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import urllib.error
import urllib.request

import pytest

# Каталог архива на время набора — временный, и он задаётся до первого импорта модулей tools: они берут путь из настроек при загрузке.
# Переменную наследуют и дочерние процессы тестов, которым каталог не задан отдельно: живой архив набор не читает ни прямо, ни через них.
_ARCHIVE = tempfile.mkdtemp(prefix="flyarchive-tests-")
_OWNER = os.getpid()
os.environ["FLYARCHIVE_HOME"] = _ARCHIVE


@atexit.register
def _drop_archive():
    if os.getpid() == _OWNER:                      # рабочие процессы, унаследовавшие обработчик, каталог набора не убирают
        shutil.rmtree(_ARCHIVE, ignore_errors=True)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

# search.py импортирует lancedb на уровне модуля. Где библиотеки нет,
# подставляем заглушку: открыть настоящую базу из тестов нельзя в любом случае.
try:
    import lancedb  # noqa: F401
except Exception:
    _stub = types.ModuleType("lancedb")

    def _no_db(*a, **k):
        raise RuntimeError("в тестах настоящая база не открывается")

    _stub.connect = _no_db
    sys.modules["lancedb"] = _stub


@pytest.fixture(autouse=True)
def _git_picks_its_own_repository(monkeypatch):
    """На время каждого теста в окружении нет переменных, которыми git выбирает репозиторий (GIT_DIR, GIT_WORK_TREE и родственные).

    Тест, который зовёт `git init` во временном каталоге, с ними переписал бы конфиг настоящего репозитория того, кто запустил набор.
    Где окружение подпроцессу собирается руками, его чистит `publication_gate.clean_git_env`."""
    import publication_gate
    for name in publication_gate.GIT_LOCAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def serve():
    """Поднимает сервер на свободном порту и гасит его после теста."""
    started = []

    def start(server_cls, handler_cls):
        srv = server_cls(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        started.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def fetch():
    """HTTP-запрос, который не бросает исключение на 4xx/5xx, а возвращает код."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    def do(url, data=None, headers=None, method=None, follow=True):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    return do


@pytest.fixture
def access(tmp_path):
    """Хозяйство доступа во временном каталоге и три выпущенных токена."""
    import auth

    class Access:
        home = str(tmp_path / "доступ")

        def guard(self, server):
            return auth.Guard(server, home=self.home)

        def journal(self):
            return self.guard("тест").journal.tail(10000)

        def journal_text(self):
            try:
                with open(self.guard("тест").journal.path, encoding="utf-8") as f:
                    return f.read()
            except FileNotFoundError:
                return ""

        @staticmethod
        def bearer(token):
            return {"Authorization": "Bearer " + token}

    a = Access()
    a.store = a.guard("тест").store
    a.read = a.store.issue("читатель", "read")
    a.full = a.store.issue("писатель", "full")
    a.local = a.store.issue("dsh-local", "local")
    return a

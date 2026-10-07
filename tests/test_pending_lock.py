"""Долги индексации под замком и отдача долгов в начале и в конце прохода (FR-74).

Файл index/pending.jsonl правят параллельные принятия и проход разбора: чтение, правка и запись — одно действие под замком
index/pending.lock, запись через временный файл. Проход отдаёт долги в начале и ещё раз в конце: долг, записанный пока он идёт,
индексируется этим же проходом. Часть тестов — с настоящими процессами, на малом входе и с пределом по времени.
"""
import contextlib
import fcntl
import json
import os
import stat
import subprocess
import sys
import threading

import pytest

import gatekit as K
import inbox as B
import ingest
import review as R
from test_review import EVIL, T0, env, embed  # noqa: F401

PENDING = os.path.join("index", "pending.jsonl")
LOCK = os.path.join("index", "pending.lock")
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
NOW = T0 + 7200


def debt(env, name, text="Долг индексации: документ лежит в корпусе, в индексе его нет."):
    """Документ в корпусе и запись о нём для файла долгов."""
    path = os.path.join(env.corpus, "mail", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"path": path, "rel": f"mail/{name}", "updated": "2024-01-01", "space": "mail", "type": "txt"}


def pending(env):
    return B._read_pending(env.home)


def rows_of(env, rel):
    return [r for r in env.table.rows if r["path"] == rel]


def run_pass(env, embed_fn=embed, **kw):
    return B.process(env.home, env.inbox, now=NOW, table=env.table, embed=embed_fn, stable_seconds=0, **kw)


def on_call(action):
    """Подставные векторы: перед ответом на n-й вызов выполняется action(n)."""
    calls = []

    def fn(texts):
        calls.append(texts)
        action(len(calls))
        return [[0.5] * ingest.DIM for _ in texts]

    fn.calls = calls
    return fn


@contextlib.contextmanager
def held(home):
    """Замок долгов занят этим тестом, как чужим процессом."""
    os.makedirs(os.path.join(home, "index"), exist_ok=True)
    fd = os.open(os.path.join(home, LOCK), os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        os.close(fd)


def _thread(action):
    done, out = threading.Event(), {}

    def work():
        try:
            out["value"] = action()
        except BaseException as e:                       # noqa: BLE001 — отдаётся тесту
            out["error"] = e
        finally:
            done.set()

    return threading.Thread(target=work, daemon=True), done, out


def _result(thread, done, out, seconds, what):
    assert done.wait(seconds), what
    thread.join()
    if "error" in out:
        raise out["error"]
    return out.get("value")


def finishes(action, seconds=10):
    """action кончается сам за разумное время (замок свободен); зависший тест не вешает набор."""
    thread, done, out = _thread(action)
    thread.start()
    return _result(thread, done, out, seconds, "действие не кончилось: замок долгов не отпущен?")


def waits_for_lock(home, action):
    """action ждёт замка: пока он занят, не кончается; как только его отпустили, кончается. Возвращает то, что вернул action."""
    thread, done, out = _thread(action)
    with held(home):
        thread.start()
        assert not done.wait(0.4), "действие не стало ждать замок долгов"
    return _result(thread, done, out, 20, "действие не кончилось, когда замок отпустили")


# ── одна функция правки долгов ──────────────────────────────────
def test_правка_долгов_читает_меняет_и_пишет_одним_действием(env):
    a, b = debt(env, "а.txt"), debt(env, "б.txt")
    assert B.edit_pending(env.home, lambda cur: cur + [a]) == [a]
    assert B.edit_pending(env.home, lambda cur: cur + [b]) == [a, b] and pending(env) == [a, b]
    assert B.edit_pending(env.home, lambda cur: [it for it in cur if it["rel"] != a["rel"]]) == [b]
    assert pending(env) == [b]


def test_пустой_итог_убирает_файл_долгов(env):
    a = debt(env, "а.txt")
    B.edit_pending(env.home, lambda cur: cur + [a])
    B.edit_pending(env.home, lambda cur: [])
    assert not os.path.exists(os.path.join(env.home, PENDING)) and pending(env) == []


def test_запись_долгов_через_временный_файл_и_замена_права_закрыты(env, monkeypatch):
    seen, real = [], os.replace

    def spy(src, dst, *a, **kw):
        if str(dst).endswith("pending.jsonl"):
            same_dir = os.path.dirname(src) == os.path.dirname(dst)
            seen.append((os.path.basename(src), os.path.basename(dst), stat.S_IMODE(os.stat(src).st_mode), same_dir))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", spy)
    B.edit_pending(env.home, lambda cur: cur + [debt(env, "а.txt")])
    assert seen == [("pending.jsonl.tmp", "pending.jsonl", 0o600, True)]
    assert not os.path.exists(os.path.join(env.home, PENDING + ".tmp"))
    assert stat.S_IMODE(os.stat(os.path.join(env.home, PENDING)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.join(env.home, LOCK)).st_mode) & 0o077 == 0


def test_сбой_внутри_правки_файл_не_тронут_и_замок_отпущен(env):
    a = debt(env, "а.txt")
    B.edit_pending(env.home, lambda cur: cur + [a])

    def broken(cur):
        raise RuntimeError("сбой правки")

    with pytest.raises(RuntimeError):
        B.edit_pending(env.home, broken)
    assert pending(env) == [a]
    assert finishes(lambda: B.edit_pending(env.home, lambda cur: cur)) == [a]                    # замок свободен


def test_правка_долгов_ждёт_замок(env):
    a = debt(env, "а.txt")
    assert waits_for_lock(env.home, lambda: B.edit_pending(env.home, lambda cur: cur + [a])) == [a]
    assert pending(env) == [a]


def test_правка_видит_то_что_записал_другой_пока_она_ждала(env):
    """Чтение — внутри замка: правка, дождавшаяся его, правит свежий файл, а не тот, что был до ожидания."""
    a, b = debt(env, "а.txt"), debt(env, "б.txt")
    done, out = threading.Event(), {}

    def work():
        out["value"] = B.edit_pending(env.home, lambda cur: cur + [b])
        done.set()

    with held(env.home):
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        assert not done.wait(0.3)
        B._write_pending(env.home, [a])                  # пока замок у нас, файл меняет «чужой» процесс
    assert done.wait(20)
    thread.join()
    assert out["value"] == [a, b] and pending(env) == [a, b]


# ── каждый, кто пишет долги, делает это под замком ──────────────
def test_принятие_ждёт_замок_долгов(env):
    r = waits_for_lock(env.home, lambda: R.queue_accept(env.home, env.q("отчёт.txt"), table=env.table, embed=embed))
    assert r == {"path": env.c("отчёт.txt"), "indexed": True} and pending(env) == []


def test_принятие_со_сбоем_индекса_долг_записан_под_замком(env):
    def broken(texts):
        raise OSError("ollama не отвечает")

    waits_for_lock(env.home, lambda: R.queue_accept(env.home, env.q("отчёт.txt"), table=env.table, embed=broken))
    assert [it["rel"] for it in pending(env)] == [env.c("отчёт.txt")]


def test_проход_ждёт_замок_когда_долги_отданы_и_надо_их_снять(env):
    a = debt(env, "а.txt")
    B._write_pending(env.home, [a])
    summary = waits_for_lock(env.home, lambda: run_pass(env))
    assert summary.batch is None and rows_of(env, a["rel"]) and pending(env) == []


def test_проход_ждёт_замок_когда_документ_не_проиндексирован_и_его_долг_надо_записать(env):
    def broken(texts):
        raise OSError("ollama не отвечает")

    with open(os.path.join(env.inbox, "новый.txt"), "wb") as f:
        f.write("Новый документ для индекса.".encode("utf-8"))      # текст: индексируется без библиотек по форматам
    summary = waits_for_lock(env.home, lambda: run_pass(env, broken))
    assert summary.counts == {"accept": 1} and [it["rel"] for it in pending(env)] == [f"входящие/{summary.batch}/новый.txt"]


class Spy:
    """Каждая запись файла долгов: был ли в этот миг занят замок. Следит за fcntl.flock, os.close и os.replace/os.remove/os.open."""

    def __init__(self, monkeypatch):
        self.held, self.writes, self.bare = set(), [], []
        flock, close, replace, remove, opener = fcntl.flock, os.close, os.replace, os.remove, os.open

        def named(fd):
            try:
                return os.readlink(f"/proc/self/fd/{fd}")
            except OSError:
                return ""

        def spy_flock(fd, op):
            r = flock(fd, op)
            if named(fd).endswith("pending.lock"):
                (self.held.add if op & fcntl.LOCK_EX else self.held.discard)(fd)
            return r

        def spy_close(fd):
            self.held.discard(fd)
            return close(fd)

        def touch(path, how):
            if str(path).endswith(("pending.jsonl", "pending.jsonl.tmp")):
                self.writes.append(how)
                if not self.held:
                    self.bare.append(how)

        def spy_replace(src, dst, *a, **kw):
            touch(dst, "replace")
            return replace(src, dst, *a, **kw)

        def spy_remove(path, *a, **kw):
            touch(path, "remove")
            return remove(path, *a, **kw)

        def spy_open(path, flags, *a, **kw):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                touch(path, "open")
            return opener(path, flags, *a, **kw)

        for owner, name, fn in ((fcntl, "flock", spy_flock), (os, "close", spy_close), (os, "replace", spy_replace),
                                (os, "remove", spy_remove), (os, "open", spy_open)):
            monkeypatch.setattr(owner, name, fn)


def test_все_записи_файла_долгов_во_всех_сценариях_идут_под_замком(env, monkeypatch):
    spy = Spy(monkeypatch)

    def broken(texts):
        raise OSError("ollama не отвечает")

    R.queue_accept(env.home, env.q("отчёт.txt"), table=env.table, embed=embed)                    # долг записан и снят
    R.queue_accept(env.home, env.q("2024-03-05_счёт.eml"), table=env.table, embed=broken)         # долг записан и остался
    gone = debt(env, "исчез.txt")
    os.remove(gone["path"])
    B._write_pending(env.home, pending(env) + [gone, debt(env, "жив.txt")])                        # подготовка теста, не работа службы
    spy.writes.clear()
    spy.bare.clear()
    run_pass(env)                                                                                  # отдача и снятие долгов, исчезнувший долг уходит
    with open(os.path.join(env.inbox, "новый.txt"), "wb") as f:
        f.write("Новый документ для индекса.".encode("utf-8"))      # текст: индексируется без библиотек по форматам
    B.process(env.home, env.inbox, now=NOW + 3600, table=env.table, embed=broken, stable_seconds=0)   # документ не индексируется: долг записан
    assert len(spy.writes) >= 4 and spy.bare == [], f"запись долгов мимо замка: {spy.bare}"


def test_в_коде_файл_долгов_пишется_только_внутри_edit_pending():
    """Запись долгов мимо edit_pending — запись мимо замка. Тесты вправе звать _write_pending для подготовки, служба — нет."""
    import ast

    class Finder(ast.NodeVisitor):
        def __init__(self):
            self.stack, self.bad = [], []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            f = node.func
            callee = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if callee == "_write_pending" and "edit_pending" not in self.stack:
                self.bad.append(node.lineno)
            self.generic_visit(node)

    bad = []
    for name in sorted(os.listdir(TOOLS)):
        full = os.path.join(TOOLS, name)
        if os.path.isfile(full) and (name.endswith(".py") or name == "flyarchive"):
            finder = Finder()
            finder.visit(ast.parse(open(full, encoding="utf-8").read()))
            bad += [f"{name}:{line}" for line in finder.bad]
    assert bad == [], "запись долгов мимо edit_pending: " + ", ".join(bad)


def test_слежение_за_замком_видит_запись_мимо_него(env, monkeypatch):
    spy = Spy(monkeypatch)
    B._write_pending(env.home, [debt(env, "а.txt")])                                              # так писал бы код без замка
    assert spy.bare, "слежение не замечает запись мимо замка"


# ── параллельные принятия не мешают друг другу на каталогах ─────
def test_каталог_пачки_созданный_параллельным_принятием_не_ошибка(env, monkeypatch):
    real = os.mkdir

    def racing(path, *a, **kw):
        real(path, *a, **kw)                              # параллельное принятие создало каталог раньше нас
        raise FileExistsError(17, "File exists", path)

    root = os.path.join(env.home, "corpus")
    target = os.path.join(root, "входящие", "20261004-120000")
    monkeypatch.setattr(os, "mkdir", racing)
    assert B._mkdirs(target, root) == target and os.path.isdir(target)


def test_каталог_который_на_деле_файл_остаётся_ошибкой(env):
    root = os.path.join(env.home, "corpus")
    open(os.path.join(root, "не-каталог"), "w").close()
    with pytest.raises(OSError):
        B._mkdirs(os.path.join(root, "не-каталог", "20261004-120000"), root)


def test_пустой_каталог_очереди_убранный_параллельным_решением_не_ошибка(env, monkeypatch):
    folder = os.path.join(env.home, "очередь", "20261004-120000")
    os.makedirs(folder)
    real = os.rmdir

    def racing(path, *a, **kw):
        real(path, *a, **kw)                              # параллельное решение убрало его раньше нас
        raise FileNotFoundError(2, "No such file or directory", path)

    monkeypatch.setattr(os, "rmdir", racing)
    R._prune(folder, os.path.join(env.home, "очередь"))
    assert not os.path.exists(folder) and os.path.isdir(os.path.join(env.home, "очередь"))


def test_непустой_каталог_очереди_остаётся_и_не_ошибка(env):
    folder = os.path.join(env.home, "очередь", "20261004-120000")
    os.makedirs(folder)
    open(os.path.join(folder, "х.txt"), "w").close()
    R._prune(folder, os.path.join(env.home, "очередь"))
    assert os.path.exists(os.path.join(folder, "х.txt"))


# ── настоящие процессы ──────────────────────────────────────────
WORKER = '''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import review
home, path, go = sys.argv[2:5]
stop = time.time() + 30
while not os.path.exists(go) and time.time() < stop:
    time.sleep(0.005)
print(json.dumps(review.queue_accept(home, path, defer_index=True)))
'''


HAMMER = '''
import os, sys, time
sys.path.insert(0, sys.argv[1])
import inbox
home, tag, go, count = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
stop = time.time() + 30
while not os.path.exists(go) and time.time() < stop:
    time.sleep(0.002)
for i in range(count):
    inbox.add_debts(home, [{"path": f"/x/{tag}-{i}", "rel": f"mail/{tag}-{i}.txt", "updated": "2024-01-01", "space": "mail", "type": "txt"}])
'''


def test_десять_процессов_дописывают_долги_одновременно_а_проход_снимает_свои_ни_один_не_потерян_и_не_задвоен(env, tmp_path):
    """Без замка чтение-правка-запись двух процессов перекрываются, и чья-то запись теряется: здесь их триста, а процессов десять."""
    olds = [{"path": f"/x/старый-{i}", "rel": f"mail/старый-{i}.txt", "updated": "2024-01-01", "space": "mail", "type": "txt"} for i in range(40)]
    B._write_pending(env.home, olds)
    script, go = tmp_path / "hammer.py", str(tmp_path / "go")
    script.write_text(HAMMER, encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, str(script), TOOLS, env.home, f"п{n}", go, "30"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8") for n in range(10)]
    try:
        open(go, "w").close()
        for it in olds:                                           # проход тем временем снимает свои долги по одному
            B.drop_debts(env.home, [it["rel"]])
        outputs = [p.communicate(timeout=60) for p in procs]
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
    assert [p.returncode for p in procs] == [0] * 10, outputs
    rels = [it["rel"] for it in pending(env)]
    assert sorted(rels) == sorted(f"mail/п{n}-{i}.txt" for n in range(10) for i in range(30)), f"долгов {len(rels)} из 300"


def test_проход_отдаёт_долги_а_десять_принятий_пишут_свои_ни_один_не_потерян_и_не_задвоен(env, tmp_path):
    ten = [f"отчёт{i}.txt" for i in range(10)]
    for name in ten:
        with open(os.path.join(env.inbox, name), "w", encoding="utf-8") as f:
            f.write(EVIL + f" Номер {name}.")
    second = B.process(env.home, env.inbox, now=T0 + 3600, table=env.table, embed=embed, stable_seconds=0)
    assert second.counts == {"review": 10}
    asked = [f"очередь/{second.batch}/{n}" for n in ten]
    taken = [f"входящие/{second.batch}/{n}" for n in ten]
    old = [debt(env, f"старый{i}.txt", f"Старый долг номер {i}.") for i in range(5)]
    B._write_pending(env.home, old)
    script, go = tmp_path / "worker.py", str(tmp_path / "go")
    script.write_text(WORKER, encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, str(script), TOOLS, env.home, path, go], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8") for path in asked]

    def at(n):
        if n == 1:
            open(go, "w").close()                         # принятия стартуют, пока проход занят первым долгом
        if n == len(old):
            for p in procs:                               # к последнему старому долгу все десять принятий уже записали свои
                p.wait(timeout=60)

    try:
        summary = run_pass(env, on_call(at))
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
    outputs = [p.communicate(timeout=30) for p in procs]
    assert [p.returncode for p in procs] == [0] * 10, outputs
    assert [json.loads(out)["indexed"] for out, _ in outputs] == [False] * 10
    assert summary.batch is None and summary.problems == []
    for rel in [it["rel"] for it in old] + taken:
        assert len(rows_of(env, rel)) == 1, f"{rel}: строк индекса {len(rows_of(env, rel))}"       # проиндексирован ровно один раз
    assert pending(env) == []                                                                     # и ни одного долга не осталось


# ── долги в начале и в конце прохода (FR-74) ────────────────────
def test_долг_записанный_пока_проход_индексирует_другой_индексируется_этим_же_проходом(env):
    first, late = debt(env, "первый.txt"), debt(env, "поздний.txt", "Долг, записанный посреди прохода.")
    B._write_pending(env.home, [first])
    s = run_pass(env, on_call(lambda n: B.edit_pending(env.home, lambda cur: cur + [late]) if n == 1 else None))
    assert s.batch is None and s.problems == []
    assert len(rows_of(env, first["rel"])) == 1 and len(rows_of(env, late["rel"])) == 1 and pending(env) == []


def test_долг_записанный_пока_проход_с_файлом_раскладывает_и_индексирует_индексируется_этим_же_проходом(env):
    late = debt(env, "поздний.txt", "Долг, записанный посреди прохода.")
    with open(os.path.join(env.inbox, "новый.txt"), "wb") as f:
        f.write("Новый документ для индекса.".encode("utf-8"))      # текст: индексируется без библиотек по форматам
    s = run_pass(env, on_call(lambda n: B.edit_pending(env.home, lambda cur: cur + [late]) if n == 1 else None))
    assert s.counts == {"accept": 1} and s.problems == []
    assert len(rows_of(env, f"входящие/{s.batch}/новый.txt")) == 1 and len(rows_of(env, late["rel"])) == 1 and pending(env) == []


def test_долги_отдаются_и_в_начале_прохода(env):
    old = debt(env, "старый.txt")
    B._write_pending(env.home, [old])
    started = []
    s = run_pass(env, on_call(lambda n: started.append(len(rows_of(env, old["rel"])))))
    assert s.batch is None and started == [0] and len(rows_of(env, old["rel"])) == 1 and pending(env) == []


def test_долг_не_прошедший_в_начале_в_конце_того_же_прохода_не_пробуется_заново(env):
    """Иначе одна беда считалась бы дважды: два замечания и вдвое больше времени, пока ollama молчит."""
    a, b = debt(env, "а.txt"), debt(env, "б.txt")
    B._write_pending(env.home, [a, b])

    def broken(texts):
        broken.calls += 1
        raise OSError("ollama не отвечает")

    broken.calls = 0
    s = run_pass(env, broken)
    assert broken.calls == 2 and len(s.problems) == 2 and sorted(it["rel"] for it in pending(env)) == sorted([a["rel"], b["rel"]])
    assert [p.code for p in s.problems] == ["problem.index_still"] * 2


def test_долг_записанный_во_время_прохода_и_не_прошедший_остаётся_с_замечанием(env):
    first, late = debt(env, "первый.txt"), debt(env, "поздний.txt")
    B._write_pending(env.home, [first])

    def fn(texts):
        fn.n += 1
        if fn.n == 1:
            B.edit_pending(env.home, lambda cur: cur + [late])
            return [[0.5] * ingest.DIM for _ in texts]
        raise OSError("ollama не отвечает")

    fn.n = 0
    s = run_pass(env, fn)
    assert len(rows_of(env, first["rel"])) == 1 and rows_of(env, late["rel"]) == []
    assert [it["rel"] for it in pending(env)] == [late["rel"]]
    assert [(p.code, p.args["rel"]) for p in s.problems] == [("problem.index_still", late["rel"])]


def test_исчезнувший_долг_снимается_а_остальные_нет(env):
    gone, live = debt(env, "исчез.txt"), debt(env, "жив.txt")
    os.remove(gone["path"])
    B._write_pending(env.home, [gone, live])
    s = run_pass(env)
    assert s.problems == [] and len(rows_of(env, live["rel"])) == 1 and rows_of(env, gone["rel"]) == [] and pending(env) == []


def test_отдача_в_конце_идёт_после_квитанций_и_её_сбой_их_не_теряет(env, monkeypatch):
    late = debt(env, "поздний.txt")
    with open(os.path.join(env.inbox, "новый.txt"), "wb") as f:
        f.write("Новый документ для индекса.".encode("utf-8"))      # текст: индексируется без библиотек по форматам
    real, calls = B._retry_pending, []

    def retry(*a, **kw):
        calls.append(1)
        if len(calls) == 2:                                # конец прохода: квитанции этой пачки уже на месте (первая пачка — из набора)
            assert len([n for n in os.listdir(os.path.join(env.home, "квитанции")) if n.endswith(".jsonl")]) == 2, "квитанций пачки ещё нет"
            raise RuntimeError("сбой отдачи")
        return real(*a, **kw)

    monkeypatch.setattr(B, "_retry_pending", retry)
    B._write_pending(env.home, [late])
    s = run_pass(env)
    assert len(calls) == 2 and s.counts == {"accept": 1} and os.path.exists(s.receipts)
    assert [p.code for p in s.problems] == ["problem.pending_failed"] and s.problems[0].args == {"error": "RuntimeError: сбой отдачи"}
    meta = json.load(open(os.path.join(env.home, "квитанции", s.batch + ".meta.json"), encoding="utf-8"))
    assert [p["code"] for p in meta["problems"]] == ["problem.pending_failed"]


def test_сбой_отдачи_в_конце_прохода_без_файлов_тоже_замечание_а_не_падение(env, monkeypatch):
    real, calls = B._retry_pending, []

    def retry(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("сбой отдачи")
        return real(*a, **kw)

    monkeypatch.setattr(B, "_retry_pending", retry)
    s = run_pass(env)
    assert s.batch is None and [p.code for p in s.problems] == ["problem.pending_failed"]


def test_ход_прохода_в_конце_показывает_этап_index_пока_идёт_отдача(env, monkeypatch):
    from test_progress import spy_replace
    log = spy_replace(monkeypatch)
    late = debt(env, "поздний.txt")
    with open(os.path.join(env.inbox, "новый.txt"), "wb") as f:
        f.write("Новый документ для индекса.".encode("utf-8"))      # текст: индексируется без библиотек по форматам
    run_pass(env, on_call(lambda n: B.edit_pending(env.home, lambda cur: cur + [late]) if n == 1 else None))
    last_index = [w for w in log if w.data["stage"] == "index"][-1].data
    assert (last_index["done"], last_index["total"], last_index["current"]) == (1, 1, late["rel"])

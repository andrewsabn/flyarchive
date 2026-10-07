"""Живой ход разбора входящих: FR-72. Файл `inbox-progress.json` в каталоге архива, пока идёт проход.

Часы подставные, поэтому частота записи проверяется без настоящих секунд. Запись ловится на `os.replace`:
к этому моменту временный файл дописан целиком, а целевой ещё не тронут.
"""
import json
import os
import stat
import subprocess
import sys
import time

import pytest

import progress as P

T0 = 1_790_000_000.0
STAMP0 = "2026-09-21T14:13:20Z"          # T0 в UTC: вид тот же, что у `time` квитанции
BATCH = "20261004-120000"
FIELDS = {"state", "batch", "stage", "done", "total", "current", "started", "updated", "pid"}
EPS = 1e-6


class Clock:
    """Подставные часы: время считается от начала заново, чтобы тысячные доли не терялись на девяти знаках секунд."""

    def __init__(self):
        self.elapsed = 0.0

    def __call__(self):
        return T0 + self.elapsed

    @property
    def t(self):
        return T0 + self.elapsed

    def advance(self, seconds):
        self.elapsed += seconds


class Write:
    """Одна запись файла хода в момент os.replace."""

    def __init__(self, src, dst, now):
        with open(src, "rb") as f:
            self.raw = f.read()
        try:
            with open(dst, "rb") as f:
                self.before = f.read()
        except OSError:
            self.before = None
        self.mode = stat.S_IMODE(os.stat(src).st_mode)
        self.same_dir = os.path.dirname(os.path.abspath(src)) == os.path.dirname(os.path.abspath(dst))
        self.src = src
        self.time = now() if now else None

    @property
    def data(self):
        return json.loads(self.raw.decode("utf-8"))

    @property
    def point(self):
        d = self.data
        return d["stage"], d["done"], d["total"], d["current"]


def spy_replace(monkeypatch, now=None):
    """Список записей файла хода. now — откуда брать время подставных часов в момент записи (без их сдвига)."""
    log, real = [], os.replace

    def spy(src, dst, *a, **kw):
        if os.path.basename(str(dst)) == P.FILE:
            log.append(Write(src, dst, now))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", spy)
    return log


def stages_of(log):
    """Этапы по порядку записей, подряд идущие повторы схлопнуты."""
    out = []
    for w in log:
        if not out or out[-1] != w.data["stage"]:
            out.append(w.data["stage"])
    return out


def forced(prev, w):
    """Запись, которой ограничение частоты не указ: смена этапа и последняя запись этапа."""
    d = w.data
    return d["stage"] != prev.data["stage"] or (d["total"] is not None and d["done"] == d["total"])


def check_rate(log):
    """Не чаще четырёх раз в секунду (кроме смены этапа и последней записи этапа) и не реже раза в секунду."""
    for prev, w in zip(log, log[1:]):
        gap = w.time - prev.time
        assert gap <= 1.0 + EPS, f"между записями {gap:.3f} с: {prev.point} -> {w.point}"
        assert gap >= 0.25 - EPS or forced(prev, w), f"две записи за {gap:.3f} с: {prev.point} -> {w.point}"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "flyarchive"
    h.mkdir()
    return str(h)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def path(home):
    return os.path.join(home, "inbox-progress.json")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def began(home, clock, batch=BATCH):
    p = P.Progress(home, clock=clock)
    p.start(batch)
    return p


# ── что лежит в файле ───────────────────────────────────────────
def test_файл_называется_inbox_progress_json():
    assert P.FILE == "inbox-progress.json" and P.STAGES == ("intake", "model", "settle", "index", "sources", "vision")


def test_пока_проход_не_начат_файла_нет(home, clock, path):
    p = P.Progress(home, clock=clock)
    p.update("intake", 1, 2, "а.txt")
    assert not os.path.exists(path) and os.listdir(home) == []


def test_начало_прохода_пишет_этап_intake_без_числа_файлов(home, clock, path):
    began(home, clock)
    assert load(path) == {"state": "running", "batch": BATCH, "stage": "intake", "done": 0, "total": None, "current": None,
                          "started": STAMP0, "updated": STAMP0, "pid": os.getpid()}


def test_запись_содержит_все_поля_договора(home, clock, path):
    p = began(home, clock)
    clock.advance(5)
    p.update("settle", 3, 10, "договор.docx")
    d = load(path)
    assert set(d) == FIELDS and d["state"] == "running" and d["batch"] == BATCH and d["pid"] == os.getpid()
    assert (d["stage"], d["done"], d["total"], d["current"]) == ("settle", 3, 10, "договор.docx")
    assert d["started"] == STAMP0 and d["updated"] == "2026-09-21T14:13:25Z"        # начало не меняется, обновление — время записи
    assert isinstance(d["pid"], int) and not isinstance(d["pid"], bool)


def test_total_может_быть_пустым_а_current_пустым(home, clock, path):
    p = began(home, clock)
    p.update("model", 0, None, None)
    d = load(path)
    assert (d["stage"], d["done"], d["total"], d["current"]) == ("model", 0, None, None)


def test_кириллица_лежит_как_есть_а_не_кодами(home, clock, path):
    p = began(home, clock)
    p.update("settle", 1, 2, "письмо.eml")
    assert "письмо.eml" in open(path, encoding="utf-8").read()


def test_имя_с_негодными_байтами_запись_не_роняет(home, clock, path):
    """Имя файла из Linux может не быть юникодом: os.walk отдаёт его с суррогатами, а в UTF-8 они не пишутся."""
    p = began(home, clock)
    p.update("settle", 1, 2, "плохое\udcffимя.txt")
    d = load(path)
    assert (d["stage"], d["done"]) == ("settle", 1) and d["current"].startswith("плохое") and d["current"].endswith("имя.txt")


# ── права и временный файл ──────────────────────────────────────
def test_права_файла_0600_даже_при_нулевой_маске(home, clock, path, monkeypatch):
    log = spy_replace(monkeypatch)
    old = os.umask(0)
    try:
        p = began(home, clock)
        clock.advance(1)
        p.update("intake", 1, 3, "а")
    finally:
        os.umask(old)
    assert len(log) == 2 and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert [w.mode for w in log] == [0o600, 0o600]


def test_права_0600_и_когда_временный_файл_остался_от_прошлого_раза_с_широкими_правами(home, clock, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("мусор")
    os.chmod(tmp, 0o666)
    began(home, clock)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and load(path)["batch"] == BATCH


def test_запись_идёт_через_временный_файл_и_os_replace_читатель_не_видит_обрезанного(home, clock, path, monkeypatch):
    log = spy_replace(monkeypatch)
    p = began(home, clock)
    clock.advance(1)
    p.update("intake", 1, 3, "а")
    clock.advance(1)
    p.update("settle", 0, 3, None)
    assert len(log) == 3                                              # каждая запись кончается подменой файла
    assert log[0].before is None                                      # до первой подмены целевого файла не было
    for prev, w in zip(log, log[1:]):
        assert w.before == prev.raw                                   # до подмены целевой файл не менялся: там прошлая запись целиком
    for w in log:
        assert w.data["pid"] == os.getpid()                           # временный файл к подмене дописан и разбирается
        assert w.src != path and w.same_dir                           # рядом с целевым: подмена остаётся атомарной
    assert open(path, "rb").read() == log[-1].raw
    assert os.listdir(os.path.dirname(path)) == [P.FILE]              # временного файла не осталось


def test_читатель_в_любой_момент_видит_целый_json(home, clock, path, monkeypatch):
    """Подмена os.replace: между открытием временного файла и подменой целевой файл читается и разбирается."""
    seen = []
    real = os.replace

    def spy(src, dst, *a, **kw):
        if os.path.basename(str(dst)) == P.FILE and os.path.exists(dst):
            seen.append(json.load(open(dst, encoding="utf-8")))       # читатель, пришедший как раз сейчас
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", spy)
    p = began(home, clock)
    for i in range(1, 6):
        clock.advance(1)
        p.update("intake", i, 9, f"файл{i}")
    assert [d["done"] for d in seen] == [0, 1, 2, 3, 4]


# ── частота записи ──────────────────────────────────────────────
def test_не_чаще_четырёх_раз_в_секунду_и_не_реже_раза_пять_тысяч_файлов(home, clock, monkeypatch):
    log = spy_replace(monkeypatch, now=lambda: clock.t)
    p = began(home, clock)
    for i in range(1, 5001):
        clock.advance(0.001)                                          # тысяча файлов в секунду: пять секунд работы
        p.update("intake", i, 5000, f"файл{i}.txt")
    assert clock.elapsed == pytest.approx(5.0)
    check_rate(log)
    assert 5 <= len(log) <= 4 * 5 + 2                                 # около четырёх в секунду, а не по записи на файл
    assert log[-1].point == ("intake", 5000, 5000, "файл5000.txt")    # последняя запись этапа есть всегда


@pytest.mark.parametrize("step", [0.05, 0.2, 0.3, 0.9])
def test_пока_этап_идёт_запись_не_реже_раза_в_секунду(home, clock, monkeypatch, step):
    log = spy_replace(monkeypatch, now=lambda: clock.t)
    p = began(home, clock)
    for i in range(1, 61):
        clock.advance(step)
        p.update("model", i, None, f"файл{i}")
    check_rate(log)
    assert len(log) >= int(60 * step)


def test_медленные_файлы_пишутся_каждый_раз(home, clock, monkeypatch):
    log = spy_replace(monkeypatch, now=lambda: clock.t)
    p = began(home, clock)
    for i in range(1, 8):
        clock.advance(0.9)
        p.update("model", i, None, f"файл{i}")
    assert len(log) == 8


def test_смена_этапа_и_последняя_запись_этапа_пишутся_всегда(home, clock, monkeypatch):
    log = spy_replace(monkeypatch)                                    # время стоит: любая обычная запись после первой лишняя
    p = began(home, clock)                                            # intake 0
    p.update("intake", 1, 3, "а")
    p.update("intake", 2, 3, "б")
    p.update("intake", 3, 3, "в")                                     # последняя этапа
    p.update("model", 0, 2, None)                                     # смена этапа
    p.update("model", 1, 2, "а")
    p.update("model", 2, 2, "б")
    p.update("settle", 0, 4, None)
    p.update("settle", 1, 4, "а")
    assert [w.point[:3] for w in log] == [("intake", 0, None), ("intake", 3, 3), ("model", 0, 2), ("model", 2, 2), ("settle", 0, 4)]


def test_неизвестный_итог_этапа_последней_записью_не_считается(home, clock, monkeypatch):
    log = spy_replace(monkeypatch)
    p = began(home, clock)
    for i in range(1, 6):
        p.update("intake", i, None, f"файл{i}")                       # total неизвестен: «готово» объявить нечем
    assert [w.point[1] for w in log] == [0]


def test_этап_без_работы_пишется_и_закрывается(home, clock, monkeypatch):
    log = spy_replace(monkeypatch)
    p = began(home, clock)
    p.update("sources", 0, 0, None)
    p.update("sources", 0, 0, None)
    assert [w.point[:3] for w in log] == [("intake", 0, None), ("sources", 0, 0), ("sources", 0, 0)]


def test_пятьдесят_тысяч_событий_за_секунду_не_дают_тысяч_записей(home, clock, monkeypatch):
    log = spy_replace(monkeypatch)
    p = began(home, clock)
    for i in range(1, 50001):
        clock.advance(0.00002)
        p.update("intake", i, None, "файл")
    assert clock.elapsed == pytest.approx(1.0) and len(log) <= 5


# ── сбой записи ─────────────────────────────────────────────────
@pytest.mark.parametrize("error", [OSError(28, "нет места на диске"), PermissionError(13, "нет прав"), RuntimeError("странный сбой"),
                                   ValueError("не пишется")])
def test_сбой_записи_не_бросается_и_следов_не_оставляет(home, clock, monkeypatch, error):
    def boom(*a, **kw):
        raise error

    monkeypatch.setattr(os, "replace", boom)
    p = P.Progress(home, clock=clock)
    p.start(BATCH)
    clock.advance(1)
    p.update("intake", 1, 3, "а")
    p.update("settle", 3, 3, "б")
    assert os.listdir(home) == []                                     # ни файла хода, ни временного: убирать их незачем и некому


def test_сбой_записи_не_отключает_ход_насовсем(home, clock, path, monkeypatch):
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError(28, "нет места")))
    p = began(home, clock)
    assert not os.path.exists(path)
    monkeypatch.setattr(os, "replace", real)                          # место появилось
    clock.advance(1)
    p.update("intake", 2, 5, "б")
    assert load(path)["done"] == 2


def test_сбой_записи_не_превращается_в_попытку_на_каждый_файл(home, clock, monkeypatch):
    attempts = []

    def boom(src, dst, *a, **kw):
        attempts.append(clock.t)
        raise OSError(28, "нет места")

    monkeypatch.setattr(os, "replace", boom)
    p = P.Progress(home, clock=clock)
    p.start(BATCH)
    for i in range(1, 3001):
        clock.advance(0.001)
        p.update("intake", i, None, "а")
    assert len(attempts) <= 4 * 3 + 2


def test_каталога_архива_нет_запись_не_падает(tmp_path, clock):
    p = P.Progress(str(tmp_path / "нет" / "такого"), clock=clock)
    p.start(BATCH)
    p.update("settle", 1, 2, "а")
    p.clear()


# ── файл убирается ──────────────────────────────────────────────
def test_clear_убирает_файл_и_не_падает_без_него(home, clock, path):
    p = began(home, clock)
    assert os.path.exists(path)
    p.clear()
    assert os.listdir(home) == []
    p.clear()


def test_после_clear_ход_этого_прохода_заново_не_пишется(home, clock, path):
    p = began(home, clock)
    p.clear()
    clock.advance(1)
    p.update("settle", 1, 2, "а")
    assert os.listdir(home) == []


def test_clear_не_падает_когда_убрать_нечем(home, clock, monkeypatch):
    p = began(home, clock)

    def boom(*a, **kw):
        raise PermissionError(13, "нет прав")

    monkeypatch.setattr(os, "remove", boom)
    monkeypatch.setattr(os, "unlink", boom)
    p.clear()


def test_менеджер_убирает_файл_на_выходе(home, clock, path):
    with P.Progress(home, clock=clock) as p:
        p.start(BATCH)
        p.update("settle", 1, 2, "а")
        assert os.path.exists(path)
    assert os.listdir(home) == []


class Killed(BaseException):
    pass


@pytest.mark.parametrize("error", [RuntimeError("взрыв"), KeyboardInterrupt(), Killed()])
def test_менеджер_убирает_файл_и_при_исключении_не_гася_его(home, clock, path, error):
    with pytest.raises(type(error)):
        with P.Progress(home, clock=clock) as p:
            p.start(BATCH)
            assert os.path.exists(path)
            raise error
    assert os.listdir(home) == []


def test_менеджер_при_вызове_убирает_остаток_брошенного_прохода(home, clock, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"state": "running", "pid": 1}')
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write("{")
    with P.Progress(home, clock=clock):
        assert os.listdir(home) == []
    assert os.listdir(home) == []


def test_менеджер_не_прячет_исключение_если_убрать_файл_не_вышло(home, clock, monkeypatch):
    def boom(*a, **kw):
        raise PermissionError(13, "нет прав")

    with pytest.raises(RuntimeError, match="взрыв"):
        with P.Progress(home, clock=clock) as p:
            p.start(BATCH)
            monkeypatch.setattr(os, "remove", boom)
            monkeypatch.setattr(os, "unlink", boom)
            raise RuntimeError("взрыв")


# ── чтение: что отдаёт состояние ────────────────────────────────
def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def put(home, **over):
    """Файл хода руками: прошедшего прохода или чужого."""
    now = over.pop("now", time.time())
    data = {"state": "running", "batch": BATCH, "stage": "index", "done": 3, "total": 9, "current": "договор.docx",
            "started": stamp(now - 60), "updated": stamp(now - 1), "pid": os.getpid()}
    data.update(over)
    with open(os.path.join(home, P.FILE), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_файла_нет_ход_пуст(home):
    assert P.read(home) is None


def test_каталога_нет_ход_пуст(tmp_path):
    assert P.read(str(tmp_path / "нет")) is None


def test_живой_процесс_свежая_запись_отдаётся_как_есть(home):
    data = put(home)
    assert P.read(home) == data


def test_живой_чужой_процесс_отдаётся_даже_если_сигнал_ему_слать_нельзя(home):
    data = put(home, pid=1)                                           # init: либо есть и доступен, либо есть и нет прав — но он есть
    assert P.read(home) == data


def test_процесса_с_таким_pid_нет_ход_пуст(home):
    put(home, pid=dead_pid())
    assert P.read(home) is None


@pytest.mark.parametrize("pid", [0, -1, -os.getpid(), True, False, None, "123", 1.5, [os.getpid()], 2 ** 31, 10 ** 30])
def test_негодный_pid_ход_пуст(home, pid):
    put(home, pid=pid)
    assert P.read(home) is None


def test_запись_старше_десяти_минут_брошена_даже_если_pid_занят(home):
    put(home, updated=stamp(time.time() - 601))
    assert P.read(home) is None                                       # свой pid жив, но запись давно не обновлялась


@pytest.mark.parametrize("age, alive", [(1, True), (120, True), (300, True), (599, True), (601, False), (900, False), (86400, False)])
def test_возраст_записи_десять_минут(home, age, alive):
    data = put(home, updated=stamp(T0 - age))
    assert P.read(home, now=T0) == (data if alive else None)


@pytest.mark.parametrize("age, alive", [(1, True), (200, True), (700, False), (3600, False)])
def test_возраст_записи_по_настоящим_часам(home, age, alive):
    data = put(home, updated=stamp(time.time() - age))
    assert P.read(home) == (data if alive else None)


def test_время_для_сравнения_подставляется(home):
    data = put(home, updated=stamp(T0 - 599))
    assert P.read(home, now=T0) == data
    assert P.read(home, now=T0 + 3) is None
    put(home, updated=stamp(T0 - 601))
    assert P.read(home, now=T0) is None


def test_запись_из_будущего_не_считается_брошенной(home):
    data = put(home, updated=stamp(time.time() + 30))
    assert P.read(home) == data


@pytest.mark.parametrize("raw", ["", "не json", "[]", "null", '"строка"', "42", '{"pid": 1}', '{"updated": "2026-10-04T12:00:00Z"}', "{",
                                 "\u0000"])
def test_негодный_файл_хода_ход_пуст(home, raw):
    with open(os.path.join(home, P.FILE), "w", encoding="utf-8") as f:
        f.write(raw)
    assert P.read(home) is None


@pytest.mark.parametrize("updated", [None, "", "вчера", "2026-10-04", "2026-10-04 12:00:00", 1790000000, True, ["2026-10-04T12:00:00Z"]])
def test_негодное_время_обновления_ход_пуст(home, updated):
    put(home, updated=updated)
    assert P.read(home) is None


def test_файл_хода_не_читается_ход_пуст(home):
    os.mkdir(os.path.join(home, P.FILE))                              # вместо файла каталог: открыть нельзя
    assert P.read(home) is None

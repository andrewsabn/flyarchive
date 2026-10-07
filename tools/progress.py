"""Живой ход разбора входящих (FR-72): файл `inbox-progress.json` в каталоге архива, пока идёт проход.

    with Progress(home) as live:      # замок разбора уже у нас: файл, оставшийся от убитого прохода, ничей — он убирается
        live.start(пачка)             # файл появляется: этап intake, готово 0
        live.update(этап, готово, всего, текущий)
    # на выходе файла нет: и после отказа, и после исключения

    with live.heartbeat():            # долгая работа без update (индексация одного документа): ход перезаписывается сам раз в BEAT секунд
        ...

    read(home)   содержимое файла или None: файла нет, процесса с этим pid нет или запись старше десяти минут

В файле `state`, `batch`, `stage` (intake, model, settle, index, sources, vision), `done`, `total` (может быть None или расти), `current`
(последний завершённый файл), `started`, `updated` (UTC), `pid`. Проход, у которого нет новых файлов, но есть долги описания изображений
(этап vision), идёт без пачки: `batch` — None (`start(None)`). Запись идёт через временный файл и os.replace, права 0600:
читатель видит либо прежнюю запись целиком, либо новую. Обычные записи — не чаще четырёх раз в секунду; смена этапа и последняя
запись этапа (готово == всего) пишутся всегда. Сбой записи разбор не останавливает.
"""
import calendar
import contextlib
import json
import os
import threading
import time

FILE = "inbox-progress.json"
STAGES = ("intake", "model", "settle", "index", "sources", "vision")
MIN_GAP = 0.25          # секунд между обычными записями: не чаще четырёх раз в секунду
STALE = 600             # запись старше десяти минут брошена, даже если этот pid занят чужим процессом
BEAT = 20               # секунд: запись, которую не трогали дольше, переписывается заново (долгий документ), с запасом до минуты
HEARTBEAT = 5           # настоящих секунд между проверками потока-сердцебиения
FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _stamp(t):
    return time.strftime(FORMAT, time.gmtime(t))


class Progress:
    def __init__(self, home, clock=None):
        """clock — часы (секунды, как time.time); подставные нужны тестам частоты записи."""
        self.path = os.path.join(home, FILE)
        self.clock = clock or time.time
        self.batch = self.started = self.stage = self.written = self.last = None
        self.on = False                                 # ход начат (start): у прохода без новых файлов пачки нет, а ход есть
        self.lock = threading.Lock()                    # файл пишут проход и поток-сердцебиение: временный файл у них один

    def __enter__(self):
        self.clear()
        return self

    def __exit__(self, *exc):
        self.clear()
        return False

    def start(self, batch, stage="intake"):
        """Проход получил номер пачки: ход начинается с этапа intake, файлов ещё не сосчитано. У прохода без новых файлов пачки нет (batch None),
        он начинается сразу с этапа vision: start(None, "vision")."""
        with self.lock:
            now = self.clock()
            self.batch, self.started, self.on = batch, now, True
            self._write(now, stage, 0, None, None)

    def update(self, stage, done, total, current=None):
        """Этап, сколько готово из скольких (total — None, пока неизвестно), имя последнего завершённого файла."""
        with self.lock:
            if not self.on:
                return
            now = self.clock()
            if stage == self.stage and not (total is not None and done == total) and now - self.written < MIN_GAP:
                return
            self._write(now, stage, done, total, current)

    def beat(self):
        """Тот же ход с новым временем, если его не трогали BEAT секунд: пока один документ индексируется, проход жив и не выглядит брошенным.
        Этап, готово, всего и текущий файл те же, что в последней записи."""
        with self.lock:
            if not self.on or self.written is None:
                return
            now = self.clock()
            if now - self.written >= BEAT:
                self._write(now, *self.last)

    @contextlib.contextmanager
    def heartbeat(self, every=None):
        """На время долгой работы без вызовов update (индексация одного документа) ход перезаписывается сам, раз в BEAT секунд.
        Поток живёт только внутри этого блока: к разбору архивов в процессах он не подмешивается. До начала прохода — ничего не делает."""
        if not self.on:
            yield
            return
        every = HEARTBEAT if every is None else every
        stop = threading.Event()

        def run():
            while not stop.wait(every):
                self.beat()

        thread = threading.Thread(target=run, name="progress-heartbeat", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(10)

    def clear(self):
        """Файла хода и его временного файла нет. Дальше в этот проход ничего не пишется."""
        with self.lock:
            self.batch, self.on = None, False
            for path in (self.path, self.path + ".tmp"):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _write(self, now, stage, done, total, current):
        # и при сбое: иначе на полном диске попытка шла бы на каждый файл
        self.stage, self.written, self.last = stage, now, (stage, done, total, current)
        tmp = self.path + ".tmp"
        try:
            data = {"state": "running", "batch": self.batch, "stage": stage, "done": done, "total": total, "current": current,
                    "started": _stamp(self.started), "updated": _stamp(now), "pid": os.getpid()}
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8", "replace")      # имя файла из Linux может не быть юникодом
            with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
                os.fchmod(f.fileno(), 0o600)            # временный файл мог остаться от прошлого раза с другими правами
                f.write(raw)
            os.replace(tmp, self.path)
        except Exception:                               # ход — удобство: разбор из-за него не останавливается
            try:
                os.remove(tmp)
            except OSError:
                pass


def read(home, now=None):
    """Ход разбора для состояния. None, если файла нет, он негоден, процесса с этим pid нет или запись старше десяти минут."""
    try:
        with open(os.path.join(home, FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:        # 0 и меньше os.kill понял бы как группу процессов
        return None
    try:
        age = (time.time() if now is None else now) - calendar.timegm(time.strptime(data.get("updated"), FORMAT))
    except (TypeError, ValueError):
        return None
    if age > STALE:
        return None
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass                                            # процесс есть, он просто чужой
    except (OSError, OverflowError):                    # нет такого процесса (или pid не бывает такого размера)
        return None
    return data

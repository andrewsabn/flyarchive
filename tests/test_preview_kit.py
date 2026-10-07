"""Общая обвязка тестов просмотра (FR-76): архив во временном каталоге, подставной рабочий процесс, проверки кэша.

Подставной рабочий процесс (`Fake`) занимает место `preview._run_worker`: пишет в выходной каталог то, что скажет тест, —
так проверяется всё, что делает родитель, не запуская песочницу. Настоящий `bwrap` — в test_preview_sandbox.py.
"""
import json
import os
import stat
import threading
import time

import pytest

import gatekit as K
import preview as P

BATCH = "20261004-120000"
FOLDER = {"queue": "очередь", "quarantine": "карантин", "corpus": os.path.join("corpus", "входящие")}
PREFIX = {"queue": "очередь", "quarantine": "карантин", "corpus": "входящие"}

PDF_DESCRIPTION = {"kind": "pages", "type": "pdf", "pages": 3, "shown": 3}
TEXT_DESCRIPTION = {"kind": "text", "type": "txt", "text": "привет", "truncated": False}
IMAGE_DESCRIPTION = {"kind": "image", "type": "png"}


class Env:
    """Архив во временном каталоге: очередь, карантин, корпус, квитанции."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.home = str(tmp / "flyarchive")
        for d in ("corpus", "очередь", "карантин", "квитанции"):
            os.makedirs(os.path.join(self.home, d))

    def put(self, where, name, data, batch=BATCH):
        """Кладёт файл в область и возвращает путь к нему в том виде, в каком его зовёт плагин."""
        full = os.path.join(self.home, FOLDER[where], batch, *name.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        os.chmod(full, 0o600)
        return f"{PREFIX[where]}/{batch}/{name}"

    def full(self, where, name, batch=BATCH):
        return os.path.join(self.home, FOLDER[where], batch, *name.split("/"))

    def receipt(self, record, batch=BATCH):
        with open(os.path.join(self.home, "квитанции", batch + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @property
    def cache(self):
        return os.path.join(self.home, "cache", "preview")

    def cached(self):
        """Всё, что лежит в кэше, кроме замков: относительные пути файлов."""
        found = []
        for folder, _, files in os.walk(self.cache):
            found += [os.path.relpath(os.path.join(folder, f), self.cache) for f in files if not f.endswith(".lock") and not f.startswith(".slot")]
        return sorted(found)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def png(width=100, height=100):
    return K.png_image(width, height)


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


class Fake:
    """Подставной рабочий процесс. describe(ext) и render(page) возвращают то, что «рабочий процесс» напишет; None — не писать.
    behave(op, outdir) — вместо обычной записи: тест сам пишет в выходной каталог любую порчу и возвращает Run."""

    def __init__(self):
        self.calls = []
        self.describe = lambda ext: PDF_DESCRIPTION
        self.render = lambda page: png()
        self.delay = 0.0
        self.op_delay = {}                 # добавка к delay для одного действия: {"describe": 0.8}
        self.behave = None
        self.member = lambda number: (f"вложение-{number}.pdf", f"байты вложения {number}".encode("utf-8"))      # (имя, байты), словарь — как есть, None — молчит
        self.lock = threading.Lock()
        self.running = self.peak = 0
        self.inputs = []

    def install(self, monkeypatch):
        monkeypatch.setattr(P, "_run_worker", self)
        monkeypatch.setattr(P, "bwrap_path", lambda: "/usr/bin/bwrap")
        monkeypatch.setattr(P, "worker_python", lambda home=None, **k: P.WorkerPython(P.PYTHON, None, None))     # где лежит Python машины, не важно
        return self

    def __call__(self, home, op, fd, ext, page, outdir):
        with self.lock:
            self.calls.append((op, ext, page))
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.inputs.append(os.pread(fd, 1 << 16, 0))
        try:
            time.sleep(self.delay + self.op_delay.get(op, 0.0))
            if self.behave is not None:
                return self.behave(op, outdir)
            if op == "member":
                answer = self.member(page)                    # у вложения «страница» — его номер в письме
                if isinstance(answer, tuple):
                    write(os.path.join(outdir, "member.bin"), answer[1])
                    answer = {"kind": "member", "name": answer[0]}
                if answer is not None:
                    write(os.path.join(outdir, "result.json"), json.dumps(answer, ensure_ascii=False).encode("utf-8"))
                return P.Run(0)
            if op == "describe":
                result = self.describe(ext)
                if result is not None:
                    write(os.path.join(outdir, "result.json"), json.dumps(result, ensure_ascii=False).encode("utf-8"))
                if isinstance(result, dict) and result.get("kind") == "image":
                    write(os.path.join(outdir, "page.png"), self.render(1))
            else:
                write(os.path.join(outdir, "result.json"), json.dumps({"kind": "page", "page": page}).encode("utf-8"))
                write(os.path.join(outdir, "page.png"), self.render(page))
            return P.Run(0)
        finally:
            with self.lock:
                self.running -= 1

    @property
    def ops(self):
        return [call[0] for call in self.calls]


@pytest.fixture
def worker(monkeypatch):
    return Fake().install(monkeypatch)


def walk_modes(root):
    """Права всего, что лежит под root: {относительный путь: (каталог ли, права)}."""
    found = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            full = os.path.join(folder, name)
            found[os.path.relpath(full, root)] = (os.path.isdir(full), stat.S_IMODE(os.stat(full).st_mode))
    return found

"""Общая обвязка тестов контейнера (FR-78): архив, подставной рабочий процесс, подставной docker, записанный дайджест.

Тесты смотрят снаружи: что `docker` получил в аргументах и во входной папке, что осталось в кэше, что ответил просмотр.
"""
import hashlib
import json
import os
import re
import types

import pytest

import gatekit as K
import preview as P
from fake_docker import FakeDocker
from test_preview_kit import BATCH, PDF_DESCRIPTION, Fake, Env, env, png, walk_modes, worker, write  # noqa: F401

DIGEST = "gotenberg/gotenberg@sha256:" + "ab" * 32
OFFICE_PDF = K.pdf_pages(3)
SHA = lambda data: hashlib.sha256(data).hexdigest()                  # noqa: E731
NAME = re.compile(r"flyarchive-preview-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TYPES = sorted(P.CONVERT_TYPES)


def needs(type_):
    """Ответ рабочего процесса на Office-файл: тип назван, преобразовывать нечем."""
    return {"kind": "none", "type": type_, "reason": "needs_converter", "args": {"type": type_}}


def record_image(env, image=DIGEST, extra=None):
    """Настройка просмотра, как её пишет `flyarchive preview setup`: в каталоге архива, права 0600."""
    path = os.path.join(env.home, "preview.json")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump({"image": image, **(extra or {})}, f)
    return path


@pytest.fixture
def box(env, worker, monkeypatch, tmp_path):
    """Дайджест записан, docker подставной и отдаёт PDF, рабочий процесс подставной: Office-файл «нуждается в преобразователе»,
    а PDF от контейнера описывается как три страницы."""
    docker = FakeDocker(tmp_path).install(monkeypatch)
    docker.plan(run={"files": {"data.pdf": OFFICE_PDF}})
    record_image(env)
    worker.describe = lambda ext: PDF_DESCRIPTION if ext == "pdf" else needs(ext)
    return types.SimpleNamespace(env=env, worker=worker, docker=docker)


def show(env, path, area="queue", members=()):
    return P.show(env.home, area, path, members)


def leftovers(env):
    """Рабочие каталоги контейнера (входной и выходной), оставшиеся в кэше после запроса."""
    try:
        return [n for n in os.listdir(env.cache) if n.startswith((".in-", ".out-", ".work"))]
    except OSError:
        return []


def run_argv(docker):
    """Аргументы единственного `docker run` (после слова run)."""
    runs = docker.runs()
    assert len(runs) == 1, [c["op"] for c in docker.calls()]
    return runs[0]["argv"]


def hold_lock(path):
    """Занимает замок-файл так же, как родитель: flock; возвращает дескриптор — закрыть, чтобы отпустить."""
    import fcntl
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def wait_for(predicate, seconds=10.0):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()

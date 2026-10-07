"""Живой ход при долгом документе (FR-72): пока один документ индексируется, запись хода обновляется не реже раза в минуту.

Иначе проход, который индексирует один тяжёлый документ дольше десяти минут, выглядел бы брошенным (progress.STALE).
Часы подставные: тест не ждёт настоящих минут. Поток-«сердцебиение» проверяется с настоящей паузой в сотые доли секунды.
"""
import os
import threading
import time

import pytest

import inbox as B
import progress as P
import review  # noqa: F401  — чтобы импорт путей tools/ был общим с остальными тестами
from test_progress import BATCH, STAMP0, T0, began, clock, home, load, path, spy_replace  # noqa: F401
from test_review import env, embed  # noqa: F401
import gatekit as K

BEATS = lambda: [t for t in threading.enumerate() if t.name == "progress-heartbeat"]  # noqa: E731


# ── запись «ничего не изменилось, процесс жив» ──────────────────
def test_обновление_хода_не_реже_раза_в_минуту_предел_меньше_минуты():
    assert 0 < P.BEAT < 60 and 0 < P.HEARTBEAT < P.BEAT


def test_до_начала_прохода_beat_ничего_не_пишет(home, clock, path):
    p = P.Progress(home, clock=clock)
    clock.advance(500)
    p.beat()
    assert not os.path.exists(path)


def test_пока_не_прошло_время_beat_не_переписывает_файл(home, clock, path, monkeypatch):
    p = began(home, clock)
    log = spy_replace(monkeypatch)
    clock.advance(P.BEAT - 1)
    p.beat()
    assert log == [] and load(path)["updated"] == STAMP0


def test_по_истечении_срока_beat_пишет_тот_же_ход_с_новым_временем(home, clock, path):
    p = began(home, clock)
    clock.advance(2)
    p.update("index", 3, 10, "договор.docx")
    before = load(path)
    clock.advance(P.BEAT)
    p.beat()
    after = load(path)
    assert after["updated"] != before["updated"] and after["updated"] == P._stamp(clock.t)
    assert {k: v for k, v in after.items() if k != "updated"} == {k: v for k, v in before.items() if k != "updated"}
    assert (after["stage"], after["done"], after["total"], after["current"]) == ("index", 3, 10, "договор.docx")


def test_beat_до_первого_update_повторяет_начало_прохода(home, clock, path):
    p = began(home, clock)
    clock.advance(P.BEAT + 1)
    p.beat()
    d = load(path)
    assert (d["stage"], d["done"], d["total"], d["current"], d["started"], d["batch"]) == ("intake", 0, None, None, STAMP0, BATCH)
    assert d["updated"] == P._stamp(clock.t)


def test_долгий_документ_запись_не_реже_раза_в_минуту_и_без_смены_done(home, clock, path, monkeypatch):
    """Документ индексируется десять минут: каждую секунду проверка beat, записи идут с шагом не больше минуты и не чаще BEAT."""
    p = began(home, clock)
    p.update("index", 0, 1, None)
    log = spy_replace(monkeypatch, now=clock)
    for _ in range(600):
        clock.advance(1)
        p.beat()
    stamps = [w.time for w in log]
    assert len(stamps) >= 600 // 60 and all(w.point == ("index", 0, 1, None) for w in log)
    gaps = [b - a for a, b in zip([T0] + stamps, stamps)]
    assert max(gaps) <= 60 and min(gaps) >= P.BEAT - 1e-6
    assert P.read(home, now=clock.t) is not None                   # через десять минут проход не выглядит брошенным


def test_без_beat_через_десять_минут_ход_считается_брошенным(home, clock):
    """Основание правки: запись, которую не обновляют, через STAMP считается брошенной."""
    began(home, clock)
    assert P.read(home, now=T0 + P.STALE - 1) is not None and P.read(home, now=T0 + P.STALE + 5) is None


def test_после_clear_beat_файл_не_возвращает(home, clock, path):
    p = began(home, clock)
    p.clear()
    clock.advance(500)
    p.beat()
    assert not os.path.exists(path)


def test_сбой_записи_beat_проход_не_останавливает(home, clock, path, monkeypatch):
    p = began(home, clock)
    clock.advance(P.BEAT + 1)
    monkeypatch.setattr(os, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError(28, "нет места")))
    p.beat()                                                       # исключения наружу нет
    assert load(path)["updated"] == STAMP0


# ── поток-сердцебиение ──────────────────────────────────────────
def wait_for(predicate, seconds=5):
    stop = time.time() + seconds
    while time.time() < stop:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_сердцебиение_само_обновляет_ход_пока_идёт_работа_и_останавливается_после(home, clock, path):
    p = began(home, clock)
    p.update("index", 2, 5, "а.docx")
    with p.heartbeat(every=0.005):
        assert len(BEATS()) == 1
        clock.advance(P.BEAT + 5)                                  # «долгий документ»: update не вызывается
        assert wait_for(lambda: load(path)["updated"] == P._stamp(clock.t)), "ход не обновился сам"
        d = load(path)
        assert (d["stage"], d["done"], d["total"], d["current"]) == ("index", 2, 5, "а.docx")
    assert BEATS() == []


def test_сердцебиение_останавливается_и_при_исключении_внутри(home, clock):
    p = began(home, clock)
    with pytest.raises(RuntimeError):
        with p.heartbeat(every=0.005):
            raise RuntimeError("сбой работы")
    assert BEATS() == []


def test_сердцебиение_до_начала_прохода_потока_не_заводит(home, clock):
    p = P.Progress(home, clock=clock)
    with p.heartbeat(every=0.005):
        assert BEATS() == []


def test_сердцебиение_не_пишет_после_clear(home, clock, path):
    p = began(home, clock)
    with p.heartbeat(every=0.005):
        p.clear()
        clock.advance(P.BEAT * 3)
        time.sleep(0.1)
        assert not os.path.exists(path)


# ── настоящий проход: документ индексируется «долго» ────────────
def test_проход_с_долгим_документом_ход_обновляется_во_время_индексации(env, clock, monkeypatch):
    monkeypatch.setattr(P, "HEARTBEAT", 0.005)
    progress_file = os.path.join(env.home, P.FILE)
    with open(os.path.join(env.inbox, "тяжёлый.txt"), "wb") as f:                # текст: индексируется без библиотек по форматам
        f.write("Тяжёлый документ, который индексируется долго.".encode("utf-8"))
    seen = {}

    def slow(texts):
        seen["before"] = load(progress_file)
        clock.advance(15 * 60)                                     # пятнадцать минут на одном документе
        seen["refreshed"] = wait_for(lambda: load(progress_file)["updated"] == P._stamp(clock.t))
        seen["after"] = load(progress_file)
        seen["readable"] = P.read(env.home, now=clock.t)
        return embed(texts)

    s = B.process(env.home, env.inbox, now=T0 + 7200, table=env.table, embed=slow, stable_seconds=0, clock=clock)
    assert s.counts == {"accept": 1}
    assert seen["refreshed"], "ход не обновился, пока индексировался один документ"
    before, after = seen["before"], seen["after"]
    assert (before["stage"], before["done"], before["total"]) == (after["stage"], after["done"], after["total"]) == ("index", 0, 1)
    assert after["batch"] == before["batch"] and after["started"] == before["started"] and after["pid"] == os.getpid()
    assert seen["readable"] is not None                            # запись живая: не старше десяти минут по часам прохода
    assert not os.path.exists(progress_file) and BEATS() == []


def test_отдача_долгов_в_конце_прохода_тоже_с_сердцебиением(env, clock, monkeypatch):
    """Долг, записанный во время прохода, индексируется в конце его же; ход при этом не должен выглядеть брошенным."""
    monkeypatch.setattr(P, "HEARTBEAT", 0.005)
    progress_file = os.path.join(env.home, P.FILE)
    late = os.path.join(env.corpus, "mail", "поздний.txt")
    os.makedirs(os.path.dirname(late), exist_ok=True)
    with open(late, "w", encoding="utf-8") as f:
        f.write("Долг, записанный посреди прохода.")
    item = {"path": late, "rel": "mail/поздний.txt", "updated": "2024-01-01", "space": "mail", "type": "txt"}
    with open(os.path.join(env.inbox, "новый.txt"), "wb") as f:                  # текст: индексируется без библиотек по форматам
        f.write("Новый документ.".encode("utf-8"))
    seen = {"n": 0}

    def slow(texts):
        seen["n"] += 1
        if seen["n"] == 1:
            B.edit_pending(env.home, lambda cur: cur + [item])
        else:                                                      # второй вызов — отдача в конце
            before = load(progress_file)
            clock.advance(15 * 60)
            seen["refreshed"] = wait_for(lambda: load(progress_file)["updated"] == P._stamp(clock.t))
            seen["stage"] = load(progress_file)["stage"], before["stage"]
        return embed(texts)

    s = B.process(env.home, env.inbox, now=T0 + 7200, table=env.table, embed=slow, stable_seconds=0, clock=clock)
    assert s.counts == {"accept": 1} and seen["n"] == 2 and seen["refreshed"] and seen["stage"] == ("index", "index")

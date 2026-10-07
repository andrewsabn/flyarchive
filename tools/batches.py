"""История пачек разбора (FR-70) и файл замечаний прохода (FR-71).

    list_batches(home, limit, before)   {"batches": [...], "more": bool}: пачки от новой к старой
    batch_detail(home, пачка)           одна пачка: запись приёмки на каждый файл, решение владельца, где файл лежит сейчас
    write_meta(home, пачка, ...)        `квитанции/<пачка>.meta.json`: когда шёл проход, сколько и какие были замечания
    read_meta(home, пачка)

Источник — квитанции `квитанции/<пачка>.jsonl`: записи приёмки и дописанные позже строки решений владельца (с полем `by`).
Записи отдаются как есть, в том числе новые поля `reason_msg`, `notes_msg` и `msg`, `where_msg` у находок (FR-73б): у квитанций
старого образца их нет, и читатель их не требует и не придумывает.
Строки, которые не разбираются, пропускаются. Пачка без `.meta.json` — старая: секунд нет, замечаний нет.
Читатели квитанций (inbox.status, review, inbox._batch_id) отбирают только `.jsonl` и файла замечаний не видят.
"""
import json
import math
import os
import re
import time
from collections import Counter

import messages

RECEIPTS, META = "квитанции", ".meta.json"
ID = re.compile(r"[0-9]{8}-[0-9]{6}(?:-(?:[2-9]|[1-9][0-9]+))?")   # как у inbox._batch_id: ГГГГММДД-ЧЧММСС, при совпадении -2, -3 …
DEFAULT_LIMIT, MAX_LIMIT = 30, 200
# что review пишет в поле decision -> решение владельца в ответе
DECISIONS = {"accept": "accept", "quarantine": "quarantine", "returned": "return", "return": "return", "deleted": "delete", "delete": "delete"}
PLACES = {"входящие": "corpus", "очередь": "queue", "карантин": "quarantine"}      # первая часть `path` квитанции -> где лежит
FOLDERS = {"corpus": ("corpus", "входящие"), "queue": ("очередь",), "quarantine": ("карантин",)}
TRASH = "удалённое"           # review.doc_delete переносит документ в удалённое/<день>/входящие/<пачка>/<имя> и записи в квитанции не делает


class BatchError(messages.CodedError):
    pass


def _check_id(batch):
    """Вид номера пачки. Имя файла из номера складывается только после этой проверки."""
    if not isinstance(batch, str) or not ID.fullmatch(batch):
        raise BatchError(messages.make("batch.bad_id", value=repr(batch)))
    return batch


def _limit(value):
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise BatchError(messages.make("batch.bad_limit", max=MAX_LIMIT))
    return value


def _key(batch):
    """Порядок пачек: сначала по секунде, потом по номеру внутри секунды (пачка без номера — первая)."""
    day, clock, *n = batch.split("-")
    return day + clock, int(n[0]) if n else 1


def _ids(home):
    """Номера пачек, у которых есть квитанции, от новой к старой."""
    try:
        names = os.listdir(os.path.join(home, RECEIPTS))
    except OSError:
        return []
    stems = (n[:-len(".jsonl")] for n in names if n.endswith(".jsonl"))
    return sorted((s for s in stems if ID.fullmatch(s)), key=_key, reverse=True)


def _records(home, batch):
    """Объекты из квитанций пачки; None, если файла нет."""
    try:
        with open(os.path.join(home, RECEIPTS, batch + ".jsonl"), "rb") as f:
            lines = f.read().split(b"\n")
    except OSError:
        return None
    out = []
    for line in lines:
        try:
            record = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError):          # не UTF-8, не JSON, обрыв строки
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _counts(done):
    return dict(Counter(r["decision"] for r in done if isinstance(r.get("decision"), str)))


def read_meta(home, batch):
    """{"seconds": число или None, "problems": [сообщения]}. Нет файла или он негоден — как у пачки старого образца."""
    try:
        with open(os.path.join(home, RECEIPTS, batch + META), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        data = {}
    seconds, problems = data.get("seconds"), data.get("problems")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
        seconds = None
    return {"seconds": seconds, "problems": [p for p in problems if isinstance(p, dict)] if isinstance(problems, list) else []}


def write_meta(home, batch, started, finished, problems):
    """Файл замечаний и длительности прохода. started и finished — время в секундах, problems — сообщения (messages.Message);
    замечание без кода записывается как generic.text: код null остался только в старых файлах."""
    def stamp(t):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

    data = {"started": stamp(started), "finished": stamp(finished), "seconds": max(0, round(finished - started)),
            "problems": [(p if isinstance(p, messages.Message) else messages.make("generic.text", text=str(p))).to_json()
                         for p in problems]}
    path = os.path.join(home, RECEIPTS, _check_id(batch) + META)
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _summary(home, batch):
    done = [r for r in _records(home, batch) or [] if "by" not in r]
    meta = read_meta(home, batch)
    return {"id": batch, "time": done[0].get("time") if done else None, "seconds": meta["seconds"], "counts": _counts(done),
            "problems": len(meta["problems"]), "files": len(done)}


def list_batches(home, limit=None, before=None):
    """Пачки от новой к старой. before — номер: берутся только пачки старше него (его самого нет), существует он или нет."""
    limit = _limit(limit)
    cut = None if before is None else _key(_check_id(before))
    ids = [i for i in _ids(home) if cut is None or _key(i) < cut]
    return {"batches": [_summary(home, i) for i in ids[:limit]], "more": len(ids) > limit}


# ── подробности одной пачки ─────────────────────────────────────
def _decision(record):
    d = record.get("decision")
    return DECISIONS.get(d) if isinstance(d, str) else None


def _lies(home, place, batch, name):
    """Файл лежит на своём месте: обычный файл, не ссылка. Имя с выходом из каталога пачки места не имеет."""
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    full = os.path.join(home, *FOLDERS[place], batch, *parts)
    return os.path.isfile(full) and not os.path.islink(full)


def _trashed(home, batch, cache):
    """Функция: удалён ли документ пачки из архива командой doc delete (он лежит в удалённое/<день>/…)."""
    def has(name):
        if cache.get("days") is None:
            try:
                cache["days"] = sorted(os.listdir(os.path.join(home, TRASH)))
            except OSError:
                cache["days"] = []
        parts = name.split("/")
        return not any(p in ("", ".", "..") for p in parts) and any(
            os.path.isfile(os.path.join(home, TRASH, day, *FOLDERS["corpus"][1:], batch, *parts)) for day in cache["days"])
    return has


def _location(home, batch, record, name, decided, trashed):
    """Где файл сейчас: corpus, queue, quarantine, returned, deleted, missing или None (хранить было нечего)."""
    if decided == "return":
        return "returned"
    if decided == "delete":
        return "deleted"
    if decided is not None:                                  # принял или отправил в карантин: файл должен лежать там
        place = "corpus" if decided == "accept" else "quarantine"
    elif record.get("returned"):
        return "returned"
    else:
        path = record.get("path")
        place = PLACES.get(path.split("/", 1)[0]) if isinstance(path, str) else None
        if place is None:
            return None
    if name is not None and _lies(home, place, batch, name):
        return place
    if place == "corpus" and name is not None and trashed(name):
        return "deleted"
    return "missing"


def batch_detail(home, batch):
    """{"id", "time", "seconds", "counts", "problems": [сообщения], "files": [запись приёмки + decided, decided_at, location]}."""
    _check_id(batch)
    records = _records(home, batch)
    if records is None:
        raise BatchError(messages.make("batch.no_batch", batch=batch))
    done = [r for r in records if "by" not in r]
    last = {}                                                # решение владельца о файле: действует последнее по порядку строк
    for r in records:
        if "by" in r and isinstance(r.get("name"), str) and _decision(r):
            last[r["name"]] = r
    trashed = _trashed(home, batch, {})
    files = []
    for r in sorted(done, key=lambda r: str(r.get("name", ""))):
        name = r["name"] if isinstance(r.get("name"), str) else None
        owner = last.get(name)
        decided = _decision(owner) if owner else None
        files.append({**r, "decided": decided, "decided_at": owner.get("time") if owner else None,
                      "location": _location(home, batch, r, name, decided, trashed)})
    meta = read_meta(home, batch)
    return {"id": batch, "time": done[0].get("time") if done else None, "seconds": meta["seconds"], "counts": _counts(done),
            "problems": meta["problems"], "files": files}

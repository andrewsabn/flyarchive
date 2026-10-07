"""Решения владельца: очередь утверждения, карантин, удаление документа (FR-52, FR-53, FR-54).

    queue_list(home)                     что ждёт решения: документ, баллы, находки
    queue_accept(home, путь)             принять: в корпус, индекс и базу известного (defer_index — без ожидания индексации: долг записан)
    queue_reject(home, путь)             в карантин
    quarantine_list(home)
    quarantine_delete(home, путь)        стереть
    quarantine_return(home, путь, inbox) вернуть: в папку возврата рядом со входящей, в архив не попадает
    doc_delete(home, путь в корпусе)     убрать документ из индекса, базы известного и корпуса
    decide_many(пути, действие)          то же над списком путей (до 200): {"results": [...]}, сбой одного пути остальных не останавливает

Действия выполняются только на машине архива: командой flyarchive и разделом «Архив» в DSH. По MCP их нет.
Путь — как в списке: `очередь/<пачка>/<имя>` или `карантин/<пачка>/<имя>`. Всё, что ведёт за пределы
своего каталога, отклоняется. Удалённый документ не стирается, а переезжает в `удалённое/<день>/…` вне корпуса.
Решение по файлу, пришедшему пачкой, дописывается в квитанции этой пачки (в том числе удаление документа из архива).
Файл долгов индексации правится только через inbox.edit_pending: параллельные решения не теряют и не задваивают долги.
"""
import json
import os
import shutil
import time

import batches
import corpus_path
import gate
import inbox
import ingest
import journal as journal_mod
import known
import messages

QUEUE, QUARANTINE, TRASH = "очередь", "карантин", "удалённое"
MAX_PATHS = 200                     # путей в одной команде решений (FR-74)


class ReviewError(messages.CodedError):
    pass


def decide_many(paths, action):
    """Решение по списку путей одной командой: {"results": [...]} в порядке запроса.

    action(путь) — одно решение (queue_accept и прочие), возвращает прежний ответ. Запись пути — {"path": как в запросе, "ok": true, …поля ответа};
    прежнее поле `path` (куда файл лёг) отдаётся как `to`, потому что `path` теперь — путь из запроса. Сбой одного пути — {"path", "ok": false,
    "error": сообщение}: остальные выполняются. Пустой список, больше MAX_PATHS путей и повтор пути отклоняются до первого действия."""
    if not paths:
        raise ReviewError(messages.make("review.paths_none"))
    if len(paths) > MAX_PATHS:
        raise ReviewError(messages.make("review.paths_too_many", max=MAX_PATHS, count=len(paths)))
    seen = set()
    for path in paths:
        if path in seen:
            raise ReviewError(messages.make("review.path_repeated", path=path))
        seen.add(path)
    results = []
    for path in paths:
        try:
            done = action(path)
        except Exception as e:                  # любой сбой одного пути — в его запись, а не конец пачки
            results.append({"path": path, "ok": False, "error": messages.of(e).to_json()})
            continue
        results.append({"path": path, "ok": True, **{("to" if key == "path" else key): value for key, value in done.items()}})
    return {"results": results}


def _journal(home, tool, path, error=None):
    journal_mod.Journal(os.path.join(home, "logs", "access.jsonl")).write(
        server="inbox", client="владелец", tool=tool, params={"path": path}, status=1 if error else 0,
        outcome=f"отказ: {error}" if error else "ok", ms=0)


def _inside(home, area, path):
    """Полный путь файла внутри своего каталога (очередь, карантин). Ссылки и выходы за каталог отклоняются."""
    if not isinstance(path, str) or not path:
        raise ReviewError(messages.make("review.path_needed"))
    parts = path.replace("\\", "/").split("/")
    if parts[0] != area or len(parts) < 3 or any(p in ("", ".", "..") for p in parts):
        raise ReviewError(messages.make("review.bad_path", area=area))
    root = os.path.realpath(os.path.join(home, area))
    full = os.path.join(home, *parts)
    if os.path.islink(full) or not os.path.isfile(full) or not os.path.realpath(full).startswith(root + os.sep):
        raise ReviewError(messages.make("review.no_file", path=path))
    return full, parts[1], "/".join(parts[2:])


def _receipts(home, batch):
    try:
        with open(os.path.join(home, "квитанции", batch + ".jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def _receipt(home, batch, name):
    """Квитанция приёмки этого файла: последняя запись с таким именем, сделанная разбором, а не решением."""
    found = [r for r in _receipts(home, batch) if r.get("name") == name and "by" not in r]
    return found[-1] if found else None


def _note(home, batch, record):
    """Решение владельца дописывается в квитанции пачки."""
    path = os.path.join(home, "квитанции", batch + ".jsonl")
    record = {**record, "by": "владелец", "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _batch_note(home, rel, record):
    """Файл пришёл пачкой (входящие/<пачка>/<имя>): решение владельца дописывается в квитанции этой пачки, как у решений очереди.
    Нет пачки, нет её квитанций или в них нет такого файла — ничего не пишется и файл квитанций не создаётся."""
    parts = rel.split("/")
    if parts[0] != ingest.SOURCE or len(parts) < 3 or not batches.ID.fullmatch(parts[1]):
        return
    batch, name = parts[1], "/".join(parts[2:])
    if _receipt(home, batch, name) is not None:
        _note(home, batch, {"name": name, **record})


def _prune(folder, stop):
    """Убирает опустевшие каталоги вверх до stop. Каталог, который параллельное решение уже убрало или в который что-то легло, не ошибка."""
    while os.path.realpath(folder) != os.path.realpath(stop):
        try:
            os.rmdir(folder)                        # непустой каталог не удаляется
        except FileNotFoundError:
            pass
        except OSError:
            return
        folder = os.path.dirname(folder)


def _list(home, area):
    root = os.path.join(home, area)
    items = []
    if not os.path.isdir(root):
        return items
    for folder, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(folder, f)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            batch, _, name = rel.partition("/")
            if not name:
                continue
            r = _receipt(home, batch, name) or {}
            items.append({"path": f"{area}/{rel}", "name": name, "batch": batch, "size": os.path.getsize(full),
                          "score": r.get("score"), "findings": r.get("findings", []), "sha256": r.get("sha256"),
                          "type": r.get("type"), "date": r.get("date"), "checked_by": r.get("checked_by"),
                          "reason": r.get("reason", ""), "reason_msg": r.get("reason_msg"), "time": r.get("time")})
    return sorted(items, key=lambda i: i["path"])


def queue_list(home):
    return _list(home, QUEUE)


def quarantine_list(home):
    return _list(home, QUARANTINE)


def _move(src, dst, home):
    inbox._mkdirs(os.path.dirname(dst), home)
    if os.path.lexists(dst):
        raise ReviewError(messages.make("review.target_exists", path=os.path.relpath(dst, home)))
    shutil.move(src, dst)
    os.chmod(dst, 0o600)


def queue_accept(home, path, table=None, embed=None, defer_index=False):
    """Принять документ из очереди. Файл должен быть тем же, что прошёл приёмку: sha256 сверяется с квитанцией.

    defer_index — не ждать индексации: файл переносится, пишется в базу известного и в долги, а индексирует его следующий разбор
    (ответ `indexed: false`). Так принимают пачкой: индексация идёт минуты, а перенос — миг."""
    try:
        full, batch, name = _inside(home, QUEUE, path)
        r = _receipt(home, batch, name)
        if r is None:
            raise ReviewError(messages.make("review.no_receipt", path=path))
        if gate.sha256_of(full) != r["sha256"]:
            raise ReviewError(messages.make("review.sha_mismatch", path=path))
        rel = f"входящие/{batch}/{name}"
        final = os.path.join(home, "corpus", *rel.split("/"))
        _move(full, final, home)
    except ReviewError as e:
        _journal(home, "queue accept", path if isinstance(path, str) else "", e)
        raise
    _prune(os.path.dirname(full), os.path.join(home, QUEUE))
    mid = fp = None
    date = r.get("date") or time.strftime("%Y-%m-%d")
    notes = []
    if r.get("type") in ("eml", "msg"):
        mid, fp, letter_date = known.letter_info(final, r["type"])
        date = letter_date or date
        note = known.missing_note(r["type"])          # письмо принято, а читать его нечем: ключи пусты, и человеку это названо (как в приёмке)
        if note is not None:
            notes.append(note.to_json())
    known.add(os.path.join(home, "index", "known.sqlite"),
              [(rel, r["size"], os.stat(final).st_mtime_ns, r["sha256"], mid, fp, date if mid or fp else None)])
    item = {"path": final, "rel": rel, "updated": date, "space": batch, "type": r.get("type")}
    # долг записывается до индексации: если команду оборвут посреди неё, документ доиндексирует следующий разбор
    inbox.add_debts(home, [item])
    indexed = False
    if not defer_index:
        result, _ = inbox._index(home, [item], table, embed)
        if not result.failed:
            inbox.drop_debts(home, [rel])
        for _, why in result.failed:                  # нет библиотеки индекса: причина названа (одно замечание), документ ждёт индекса долгом
            if inbox.library_missing(why) and why.to_json() not in notes:
                notes.append(why.to_json())
        indexed = rel in result.indexed
    _note(home, batch, {"name": name, "decision": "accept", "was": "review", "path": rel, "sha256": r["sha256"], "indexed": indexed})
    _journal(home, "queue accept", path)
    return {"path": rel, "indexed": indexed, **({"notes": notes} if notes else {})}


def queue_reject(home, path):
    """Отправить документ из очереди в карантин."""
    try:
        full, batch, name = _inside(home, QUEUE, path)
        target = f"{QUARANTINE}/{batch}/{name}"
        _move(full, os.path.join(home, *target.split("/")), home)
    except ReviewError as e:
        _journal(home, "queue quarantine", path if isinstance(path, str) else "", e)
        raise
    _prune(os.path.dirname(full), os.path.join(home, QUEUE))
    _note(home, batch, {"name": name, "decision": "quarantine", "was": "review", "path": target})
    _journal(home, "queue quarantine", path)
    return {"path": target}


def quarantine_delete(home, path):
    """Стереть файл из карантина. Безвозвратно."""
    try:
        full, batch, name = _inside(home, QUARANTINE, path)
    except ReviewError as e:
        _journal(home, "quarantine delete", path if isinstance(path, str) else "", e)
        raise
    os.remove(full)
    _prune(os.path.dirname(full), os.path.join(home, QUARANTINE))
    _note(home, batch, {"name": name, "decision": "deleted", "was": "quarantine", "path": None})
    _journal(home, "quarantine delete", path)
    return {"deleted": path}


def quarantine_return(home, path, inbox=None):
    """Вернуть файл владельцу: в папку возврата рядом со входящей. В архив он не попадает."""
    import inbox as inbox_mod
    try:
        full, batch, name = _inside(home, QUARANTINE, path)
    except ReviewError as e:
        _journal(home, "quarantine return", path if isinstance(path, str) else "", e)
        raise
    box = os.path.abspath(inbox or inbox_mod.load_config(home)["inbox"])
    rel = f"из-карантина/{batch}/{name}"
    target = os.path.join(os.path.dirname(box), os.path.basename(box) + "-возврат", *rel.split("/"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.lexists(target):
        raise ReviewError(messages.make("review.return_exists", path=rel))
    shutil.move(full, target)
    _prune(os.path.dirname(full), os.path.join(home, QUARANTINE))
    _note(home, batch, {"name": name, "decision": "returned", "was": "quarantine", "path": None, "returned": rel})
    _journal(home, "quarantine return", path)
    return {"returned": rel}


def _index_forms(rel):
    """Под какими путями документ может быть записан в индексе: с прямыми и обратными слэшами, под старым корнем."""
    head, _, rest = rel.partition("/")
    out = []
    for h in [head] + [old for old, new in corpus_path.ALIAS.items() if new == head]:
        slash = h + "/" + rest if rest else h
        out += [slash, slash.replace("/", "\\")]
    return list(dict.fromkeys(out))


def doc_delete(home, path, table=None, embed=None, now=None):
    """Убрать документ из архива: строки индекса, запись базы известного, файл корпуса.

    path — как в выдаче поиска. Файл не стирается, а переезжает в `удалённое/<день>/…` вне корпуса:
    ошибку можно исправить руками. Сначала индекс: если он недоступен, не меняется ничего."""
    corpus = os.path.realpath(os.path.join(home, "corpus"))
    try:
        if not isinstance(path, str) or not path:
            raise ReviewError(messages.make("review.doc_path_needed"))
        rel = path.replace("\\", "/")
        if rel.startswith("/") or any(p in ("", ".", "..") for p in rel.split("/")):
            raise ReviewError(messages.make("review.doc_path_outside", path=path))
        head, _, rest = rel.partition("/")
        if head in corpus_path.ALIAS and rest:       # путь из выдачи поиска под старым корнем: файл лежит под новым
            rel = corpus_path.ALIAS[head] + "/" + rest
        full = os.path.join(corpus, *rel.split("/"))
        if os.path.islink(full) or not os.path.isfile(full) or not os.path.realpath(full).startswith(corpus + os.sep):
            raise ReviewError(messages.make("review.no_doc", path=path))
        try:
            if table is None:
                table = ingest.open_table(home)
            rows = 0
            for form in _index_forms(rel):         # иначе файл уедет, а его фрагменты останутся в поиске
                where = "path = '" + form.replace("'", "''") + "'"
                rows += table.count_rows(where)
                table.delete(where)
        except ingest.LibraryMissing as e:         # нет библиотеки индекса: названа она, а не вид исключения
            raise ReviewError(messages.of(e)) from None
        except Exception as e:
            raise ReviewError(messages.make("review.index_unavailable", error_type=type(e).__name__))
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))
        moved = f"{TRASH}/{day}/{rel}"
        try:
            _move(full, os.path.join(home, *moved.split("/")), home)
        except OSError as e:
            raise ReviewError(messages.make("review.move_failed", error_type=type(e).__name__, path=path))
    except ReviewError as e:
        _journal(home, "doc delete", path if isinstance(path, str) else "", e)
        raise
    known.remove(os.path.join(home, "index", "known.sqlite"), [rel])
    _batch_note(home, rel, {"decision": "delete", "was": "corpus", "path": None})
    _journal(home, "doc delete", path)
    return {"path": path, "rows": rows, "moved_to": moved}

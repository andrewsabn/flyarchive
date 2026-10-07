"""Индексация принятых документов (FR-42): текст, нарезка, векторы, запись в таблицу индекса.

Правила те же, что у основного индексатора (index_more): тот же разбор форматов, та же нарезка,
та же модель векторов на процессоре. База у принятого через входящие одна — «входящие».

Документ пишется в таблицу целиком одним вызовом: либо все его фрагменты в индексе, либо ни одного.
Сбой на одном документе не останавливает остальные — он возвращается в списке failed.

    index_documents([{"path": ..., "rel": "входящие/пачка/файл", "updated": "2024-03-05", "space": "пачка"}], table)

Вместо чтения файла документ может принести готовый текст (`text`: строка или список частей) и свою базу (`source`): так в индекс ложится описание
картинки, сделанное моделью (FR-92, `vision.py`). Нарезка и векторы те же, второй нарезки и второго обращения к службе векторов нет.
"""
import os
import re
import tempfile
import warnings
from collections import namedtuple

import messages
import settings

SOURCE = "входящие"
DIM = settings.startup()["embed_dim"]           # размерность векторов — настройка embed_dim: зашита в таблицу индекса
TABLE = "docs"
# Мера расстояния между векторами — одна на таблицу: ею строится приближённый индекс (index_optimize, fix_dates) и ею же считает запрос (search).
# Без явного указания LanceDB считает запрос евклидовым расстоянием, пока индекса нет, и мерой индекса, когда он построен: порядок для ненормированных
# векторов у разных мер разный, и построение индекса молча менял бы выдачу. Служба векторов отдаёт векторы единичной длины, для них порядок один.
METRIC = "cosine"
# поля строки таблицы индекса по порядку схемы: их пишет index_documents, их же заводит create_table (index_schema)
ROW_FIELDS = ("path", "source", "space", "title", "updated", "url", "chunk", "text", "vector")
BATCH = 48
LEAD_MAX = 200          # первая строка готовой части, которую повторяет её продолжение, — не длиннее
Indexed = namedtuple("Indexed", "indexed skipped failed chunks")
# тип, определённый приёмкой по содержимому -> каким разбором читать файл без годного расширения
BY_TYPE = {"pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx", "eml": ".eml", "msg": ".msg",
           "html": ".html", "txt": ".txt"}


def _default_embed(texts):
    """Векторы у службы векторов: адрес, модель и счёт на процессоре или видеокарте — настройки embed_url, embed_model, embed_gpu
    (одно место: index_more). По умолчанию процессор: видеокарта занята большой моделью."""
    import json
    import urllib.request

    import index_more
    body = {"model": index_more.MODEL, "input": texts}
    options = index_more.embed_options()
    if options:
        body["options"] = options
    req = urllib.request.Request(index_more.OLLAMA, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["embeddings"]


def _read(M, path, ext, alias):
    """Текст документа. alias — у файла чужое расширение: библиотеки разбора смотрят на имя,
    поэтому файл подаётся им под временным именем с правильным расширением."""
    if not alias:
        return M.read_text(path, ext)
    with tempfile.TemporaryDirectory(prefix="flyarchive-") as tmp:
        link = os.path.join(tmp, "document" + ext)
        os.symlink(os.path.abspath(path), link)
        return M.read_text(link, ext)


def _ready_parts(M, text):
    """Куски готового текста (описание изображения, FR-92): text — строка или список частей. Каждая часть режется по тем же правилам, что документ
    (M.chunks); продолжение части начинается с её первой строки, чтобы и в нём было сказано, что это за текст и откуда."""
    out = []
    for part in [text] if isinstance(text, str) else list(text):
        pieces = M.chunks(part)
        head, sep, _ = part.strip().partition("\n")
        lead = head + "\n" if sep and len(head) <= LEAD_MAX else ""         # у части без короткой первой строки продолжение остаётся как есть
        out.extend(pieces[:1] + [lead + piece for piece in pieces[1:]])
    return out


def index_documents(items, table, embed=None, progress=None):
    """items: path (где лежит), rel (путь в корпусе), updated, space и, по желанию, title, type (тип по содержимому), source (база, по умолчанию
    «входящие») и text — готовый текст вместо чтения файла (строка или список частей: описание изображения от модели, FR-92; файл тогда не открывается).
    progress(готово, всего, rel) — ход для живого экрана: по документу после того, как он обработан
    (проиндексирован, пропущен или сбойный)."""
    import index_more as M
    embed = embed or _default_embed
    indexed, skipped, failed, total = [], [], [], 0
    for n, it in enumerate(items):
        if progress and n:
            progress(n, len(items), items[n - 1]["rel"])
        rel = it["rel"]
        ready = it.get("text")
        own = os.path.splitext(it["path"])[1].lower()
        ext = own if own in M.EXTRACT or own in M.PLAIN else BY_TYPE.get(it.get("type"))
        if ext is None and ready is None:
            skipped.append((rel, "текст из такого файла не извлекается"))
            continue
        try:
            if ready is not None:
                text = ready if isinstance(ready, str) else "\n".join(ready)
            else:
                text = _read(M, it["path"], ext, alias=ext != own)
            if not text or not text.strip():
                skipped.append((rel, "в документе нет текста"))
                continue
            title = it.get("title")
            if not title:
                subject = re.search(r"^Тема: (.+)$", text, re.M) if ready is None and ext in (".eml", ".msg") else None
                title = subject.group(1) if subject else os.path.basename(rel)
            parts = _ready_parts(M, ready) if ready is not None else M.chunks(text)
            vectors = []
            for i in range(0, len(parts), BATCH):
                vectors.extend(embed(parts[i:i + BATCH]))
            if len(vectors) != len(parts):
                raise ValueError("векторов не столько, сколько фрагментов")
            rows = [{"path": rel, "source": it.get("source") or SOURCE, "space": it["space"], "title": title[:200], "updated": it["updated"],
                     "url": "", "chunk": n, "text": part, "vector": vector} for n, (part, vector) in enumerate(zip(parts, vectors))]
            table.add(rows)
        except Exception as e:
            failed.append((rel, f"{type(e).__name__}: {e}"[:200]))
            continue
        indexed.append(rel)
        total += len(rows)
    if progress and items:
        progress(len(items), len(items), items[-1]["rel"])
    return Indexed(indexed, skipped, failed, total)


class LibraryMissing(messages.CodedError):
    """Нет библиотеки индекса (lancedb): команды индекса отвечают этим отказом — сообщением lib.missing, как поиск, — а не трассировкой."""


def require_lancedb():
    """Библиотека индекса. Нет её — LibraryMissing с сообщением lib.missing; не хватает зависимости самой библиотеки или она не загружается —
    ошибка как есть: это не «нет lancedb», и ставить уже стоящее человеку не советуем (такую поломку называет `flyarchive doctor`, lib.broken)."""
    try:
        import lancedb
    except ModuleNotFoundError as e:
        if (e.name or "").split(".")[0] != "lancedb":
            raise
        raise LibraryMissing(messages.make("lib.missing", package="lancedb", use="index", file="requirements.txt")) from None
    return lancedb


class TableMissing(messages.CodedError, ValueError):
    """Таблицы индекса нет. На чистом архиве её заводит `flyarchive init`; пропавшая таблица рабочего архива — сбой, и пустую взамен не заводят."""


def index_schema(dim=None):
    """Схема таблицы индекса `docs` — одно место: ею пишет index_documents (поля ROW_FIELDS) и заводит таблицу create_table. Текстовые поля,
    номер фрагмента int32 и вектор фиксированной длины float32; dim — размерность вектора, по умолчанию настройка embed_dim."""
    import pyarrow as pa
    kinds = {"chunk": pa.int32(), "vector": pa.list_(pa.float32(), DIM if dim is None else dim)}
    return pa.schema([(name, kinds.get(name, pa.string())) for name in ROW_FIELDS])


def index_dir(home):
    return os.path.join(home, "index", "lance")


def _has_table(db):
    with warnings.catch_warnings():                     # table_names в новых версиях библиотеки помечен устаревшим, замены во всех версиях нет
        warnings.simplefilter("ignore", DeprecationWarning)
        return TABLE in db.table_names()


def open_table(home):
    """Таблица живого индекса. Каталога индекса нет, он пуст или таблицы docs в нём нет — TableMissing; сама таблица здесь не заводится,
    как и каталог индекса (connect его создаёт, поэтому без каталога к библиотеке не обращаемся). Остальное — негодный каталог,
    испорченная таблица — отказ библиотеки как есть: это не «таблицы нет». Нет самой библиотеки — LibraryMissing (сообщение lib.missing)."""
    lancedb = require_lancedb()
    folder = index_dir(home)
    if not os.path.exists(folder):
        raise TableMissing(messages.make("index.no_table"))
    db = lancedb.connect(folder)
    if not _has_table(db):
        raise TableMissing(messages.make("index.no_table"))
    return db.open_table(TABLE)


def create_table(home, dim=None):
    """Заводит пустую таблицу индекса: схема index_schema и полнотекстовый индекс по тексту. Единственное место, где таблица создаётся
    (его зовёт `flyarchive init`). True — создана; False — таблица уже есть, и она не тронута: не открывается и не меняется, а режим создания
    библиотеки по умолчанию существующую не перезаписывает. Не удался полнотекстовый индекс — только что созданная пустая таблица убирается:
    с половиной готовности повторный запуск сказал бы «уже есть». Нет самой библиотеки — LibraryMissing (сообщение lib.missing), и до этого
    ничего не создано."""
    lancedb = require_lancedb()
    folder = index_dir(home)
    for part in (os.path.dirname(folder), folder):      # с правами владельца, как остальные каталоги архива
        os.makedirs(part, mode=0o700, exist_ok=True)
    db = lancedb.connect(folder)
    if _has_table(db):
        return False
    table = db.create_table(TABLE, schema=index_schema(dim))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            table.create_fts_index("text", replace=True)
    except BaseException:
        db.drop_table(TABLE)
        raise
    return True

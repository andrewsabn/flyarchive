"""Гибридный поиск по архиву: вектор + BM25, с весом свежести.

    python search.py "перенос релиза" --since 2025 --source tasks -k 10
"""
import argparse, json, math, os, re, sys, time, urllib.error, urllib.request
import ingest
import messages
import perms
import settings
import sources

try:
    import lancedb
except ImportError:                 # без библиотеки службе поиска надо запуститься и ответить сообщением, а не упасть при загрузке (FR-107)
    lancedb = None

_SETTINGS = settings.startup()
SOURCES = sources.startup()
DB = os.path.join(_SETTINGS["home"], "index", "lance")
OLLAMA = _SETTINGS["embed_url"]
MODEL = _SETTINGS["embed_model"]
# вектор запроса по умолчанию считается на процессоре (embed_gpu выключена): видеопамять целиком отдана большой модели,
# и пока модель векторов лежит на видеокарте, та отвечает в 4-15 раз медленнее
EMBED_OPTIONS = {} if _SETTINGS["embed_gpu"] else {"num_gpu": 0}
HALF_LIFE_DAYS = _SETTINGS["search_half_life_days"]
NPROBES = _SETTINGS["search_nprobes"]       # разделов IVF на просмотр: при 80 терялось больше трети настоящих ближайших
REFINE = _SETTINGS["search_refine"]         # во столько раз больше кандидатов уточняется по исходным векторам
# замер на таблице в сотни тысяч фрагментов: 80 и 10 — 63% первых десяти, 37 мс; 400 и 30 — 93%, 80 мс (умолчания схемы настроек)
SINCE = re.compile(r"\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?", re.A)


class BadQuery(ValueError):
    """Запрос составлен неверно: поправить его должен вызывающий, архив исправен."""


class SearchError(messages.CodedError):
    """Поиск не может ответить: нет библиотеки lancedb, таблицы индекса или службы векторов. Сообщение — из каталога, с подсказкой, что поставить
    или какую команду выполнить. status — код ответа службы поиска: нет ответа службы векторов — 502, остальное — 503 (пока не поставят и не заведут)."""

    @property
    def status(self):
        return 502 if self.message.code == "embed.down" else 503


def _today():
    """Сегодняшний день числом вида 20260831: от него считается возраст документа."""
    return int(time.strftime("%Y%m%d"))


def embed(text):
    req = urllib.request.Request(
        OLLAMA, json.dumps({"model": MODEL, "input": [text], "options": EMBED_OPTIONS}).encode(),
        {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["embeddings"][0]


def _why(e):
    """Почему не ответила служба векторов: код ответа или вид сбоя, без текста чужого ответа."""
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return e.reason if isinstance(e.reason, str) else type(e.reason).__name__
    return type(e).__name__


def open_table():
    """Таблица docs индекса. Нет библиотеки, каталога индекса или самой таблицы — SearchError с подсказкой. Каталог индекса здесь не создаётся:
    connect создал бы его с правами по умолчанию, а поиск по незаведённому архиву ничего заводить не должен."""
    if lancedb is None:
        raise SearchError(messages.make("lib.missing", package="lancedb", use="index", file="requirements.txt"))
    if not os.path.isdir(DB):
        raise SearchError(messages.make("index.no_table"))
    try:
        return lancedb.connect(DB).open_table("docs")
    except Exception:                               # вид ошибки у версий lancedb разный (ValueError «Table 'docs' was not found» и другие): судим по каталогу таблицы
        if os.path.exists(os.path.join(DB, "docs.lance")):
            raise                                   # таблица есть, но не открывается: это не «таблицы нет»
        raise SearchError(messages.make("index.no_table")) from None     # пустой каталог индекса — тоже «таблицы нет»


def with_metric(query):
    """Запрос по векторам с мерой таблицы (ingest.METRIC), той же, что у приближённого индекса: без явного указания мера запроса зависит от того, построен ли
    индекс. У запроса без метода distance_type (подставной в тестах) мера остаётся как есть."""
    setter = getattr(query, "distance_type", None)
    return query if setter is None else setter(ingest.METRIC)


def days_old(updated, today):
    """Грубая разница в днях по датам вида 2026-08-31."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", updated or "")
    if not m:
        return 3650  # без даты — считаем старым
    y, mo, d = map(int, m.groups())
    ty, tmo, td = today // 10000, today // 100 % 100, today % 100
    return max(0, (ty - y) * 365 + (tmo - mo) * 30 + (td - d))


def recency(updated, today):
    return 0.5 ** (days_old(updated, today) / HALF_LIFE_DAYS)


def search(query, k=10, since=None, source=None, space=None, today=None, pool=120):
    since = (since or "").strip()
    if since and not SINCE.fullmatch(since):
        # дата подставляется в условие отбора: всё, что не дата, отклоняется до обращения к таблице
        raise BadQuery("since: нужна дата вида 2026, 2026-08 или 2026-08-31")
    today = today or _today()
    tbl = open_table()
    def in_list(col, val):
        """Принимает 'tasks' или 'tasks,wiki' — несколько баз через запятую."""
        vals = [v.strip().replace("'", "") for v in str(val).split(",") if v.strip()]
        if not vals:
            return None
        if len(vals) == 1:
            return f"{col} = '{vals[0]}'"
        return "(" + " OR ".join(f"{col} = '{v}'" for v in vals) + ")"

    where = []
    if since:
        where.append(f"updated >= '{since}'")
    for col, val in (("source", source), ("space", space)):
        cond = in_list(col, val) if val else None
        if cond:
            where.append(cond)
    flt = " AND ".join(where) if where else None

    def run(q, ann=False):
        if flt:
            q = q.where(flt)
        if ann:
            # 20 разделов из 1019 по умолчанию дают половину нужного;
            # 80 с уточнением сравнивают кандидатов по исходным векторам
            q = with_metric(q).nprobes(NPROBES).refine_factor(REFINE)
        return q.limit(pool).to_list()


    try:
        vector = embed(query)
    except (OSError, ValueError, LookupError, TypeError) as e:       # URLError — это OSError; негодный ответ — ValueError, KeyError, IndexError
        raise SearchError(messages.make("embed.down", url=OLLAMA, why=str(_why(e))[:80], model=MODEL)) from None
    vec = run(tbl.search(vector, vector_column_name="vector"), ann=True)
    # индекс слов собран без позиций: запрос целиком в кавычках он принимает за фразу и отвечает ошибкой
    words = " ".join(query.replace('"', " ").split())
    bm25 = run(tbl.search(words, query_type="fts")) if words else []

    # обратный ранг вместо сырых оценок: шкалы вектора и BM25 несопоставимы
    scores, meta = {}, {}
    for rank, r in enumerate(vec):
        key = (r["path"], r["chunk"])
        scores[key] = scores.get(key, 0) + 1.0 / (60 + rank)
        meta[key] = r
    for rank, r in enumerate(bm25):
        key = (r["path"], r["chunk"])
        scores[key] = scores.get(key, 0) + 0.8 / (60 + rank)
        meta.setdefault(key, r)

    ranked = sorted(
        ((s * (0.55 + 0.45 * recency(meta[key]["updated"], today)), key) for key, s in scores.items()),
        reverse=True)

    # один документ лежит в нескольких выгрузках почты, поэтому в выдачу
    # он приходит несколькими одинаковыми кусками — оставляем первый
    out, seen = [], set()
    for sc, key in ranked:
        r = meta[key]
        fp = (r["text"] or "").strip()[:400]
        if fp and fp in seen:
            continue
        seen.add(fp)
        out.append((sc, r))
        if len(out) >= k:
            break
    return out


LABELS = dict(SOURCES.labels)        # подписи баз — из таблицы источников; без таблицы подписей нет


def bases():
    """Какие базы есть в индексе и сколько в каждой чанков."""
    import collections
    tbl = open_table()
    import pyarrow.compute as pc
    # считает pyarrow по одному столбцу: по строке в питоновский словарь на сотнях тысяч строк — секунды и сотни мегабайт, а форму поиска строят на каждую страницу
    column = tbl.search().limit(2_000_000).select(["source"]).to_arrow().column("source")
    return collections.Counter({row["values"].as_py(): row["counts"].as_py() for row in pc.value_counts(column)})


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # иначе кириллица бьётся в консоли Windows
    except Exception:
        pass
    if "--bases" in sys.argv:
        try:
            found = bases().most_common()
        except SearchError as e:
            return refuse(e)
        print(f"{'база':<16} {'чанков':>9}  что это")
        for k, v in found:
            print(f"{k:<16} {v:>9,}  {LABELS.get(k) or (sources.INTAKE_LABEL if k == sources.INTAKE else '')}")   # входящим — подпись, как на странице поиска
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=10)
    ap.add_argument("--since", help="дата в формате ГГГГ, ГГГГ-ММ или ГГГГ-ММ-ДД")
    ap.add_argument("--source", help="база или несколько через запятую; список см. --bases")
    ap.add_argument("--space", help="раздел базы: каталог первого уровня, ящик или номер пачки, можно через запятую")
    ap.add_argument("--today", type=int, help="ГГГГММДД для веса свежести; по умолчанию сегодня")
    ap.add_argument("--full", action="store_true", help="печатать текст чанка целиком")
    a = ap.parse_args()

    try:
        hits = search(a.query, a.k, a.since, a.source, a.space, a.today)
    except SearchError as e:
        return refuse(e)
    except BadQuery as e:                   # негодная дата: поправить должен спрашивающий, а не читать трассировку
        print(f"ошибка: {e}", file=sys.stderr)
        return 1
    show(hits, a.full)
    return 0


def show(hits, full=False):
    """Находки для человека: заголовок, путь и начало текста (или весь текст, full). То же печатает `flyarchive search`."""
    if not hits:
        print("ничего не найдено")
        return
    for i, (sc, r) in enumerate(hits, 1):
        head = f"{i:2}. [{sc:.4f}] {r['updated'] or '????-??-??'}  {r['space'] or r['source']}  {r['title'][:70]}"
        print(head)
        print(f"    {r['path']}" + (f"  #{r['chunk']}" if r["chunk"] else ""))
        body = r["text"] if full else re.sub(r"\s+", " ", r["text"])[:220]
        print(f"    {body}\n")


def refuse(e):
    """Отказ поиска: одна строка с подсказкой в stderr и код возврата 1, без трассировки."""
    print(f"ошибка: {messages.of(e)}", file=sys.stderr)
    return 1


def selftest():
    assert days_old("2026-08-31", 20260831) == 0
    assert days_old("", 20260831) == 3650
    assert recency("2026-08-31", 20260831) == 1.0
    assert 0.4 < recency("2025-03-01", 20260831) < 0.7, recency("2025-03-01", 20260831)
    assert recency("2020-01-01", 20260831) < 0.05
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        perms.close_umask()                                # всё, что создаст прямой запуск, — только владельцу, при любой маске запустившего
        sys.exit(main())

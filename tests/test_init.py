"""Команда flyarchive init: архив заводится с пустого каталога одной командой (FR-103).

Команда настоящая (процесс) в пустом временном каталоге с собственным домашним каталогом. Таблица индекса — настоящая LanceDB: схема и полнотекстовый
индекс проверяются у неё самой. Служб и модели у `init` нет: ничего из этого он не зовёт. Службу векторов он спрашивает один раз, в самом конце, одной
строкой итога (FR-107); адрес в этих тестах — несуществующий (archivekit.DEAD_VECTORS), так что живая служба не затрагивается.
"""
import hashlib
import json
import os
import stat

import pytest

import ingest as G
import known as N
import preview_container as PC
import tokens as T
from archivekit import INDEX_FIELDS, Archive, error_line

lancedb = pytest.importorskip("lancedb")
pa = pytest.importorskip("pyarrow")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

STEPS = ["home", "secrets", "token", "logs", "index", "corpus", "known", "table"]
CREATED = {"home": "init.dir_created", "secrets": "init.dir_created", "token": "init.token_created", "logs": "init.dir_created",
           "index": "init.dir_created", "corpus": "init.dir_created", "known": "init.known_created", "table": "init.table_created"}
EXISTS = {"home": "init.dir_exists", "secrets": "init.dir_exists", "token": "init.token_exists", "logs": "init.dir_exists",
          "index": "init.dir_exists", "corpus": "init.dir_exists", "known": "init.known_exists", "table": "init.table_exists"}


@pytest.fixture
def archive(tmp_path):
    return Archive(tmp_path)


def init_json(archive, *args, **env):
    code, out, err = archive("init", "--json", *args, **env)
    assert code == 0, err
    return json.loads(out)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def tree(root):
    """Что лежит в каталоге: у файла — размер, время изменения и содержимое; у каталога — список имён. Любая запись при повторе видна."""
    out = {}
    for folder, dirs, files in os.walk(root):
        out[os.path.relpath(folder, root)] = sorted(dirs + files)
        for name in files:
            path = os.path.join(folder, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = (os.stat(path).st_size, os.stat(path).st_mtime_ns, hashlib.sha256(f.read()).hexdigest())
    return out


def fill(table, n=3):
    """Документы в таблице: строки с настоящими векторами нужной размерности."""
    dim = table.schema.field("vector").type.list_size
    table.add([{"path": f"входящие/20261006-100000/док{i}.txt", "source": "входящие", "space": "20261006-100000", "title": f"док{i}.txt",
                "updated": "2026-10-06", "url": "", "chunk": 0, "text": f"Договор поставки номер {i}", "vector": [0.1 * (i + 1)] * dim}
               for i in range(n)])


def fingerprint(table):
    rows = table.search().limit(1000).to_list()
    return hashlib.sha256(json.dumps(sorted((r["path"], r["chunk"], r["text"], round(r["vector"][0], 4)) for r in rows),
                                     ensure_ascii=False).encode("utf-8")).hexdigest(), len(rows)


# ── что делает init на пустом месте ─────────────────────────────
def test_init_на_пустом_месте_создаёт_каталог_архива_и_внутренние_каталоги_с_правами_владельца(archive):
    assert not os.path.exists(archive.home)
    code, out, err = archive("init")
    assert code == 0 and err == "", err
    assert sorted(os.listdir(archive.home)) == ["corpus", "index", "logs", "secrets"]
    for path in (archive.home, *(archive.path(name) for name in ("secrets", "logs", "index", "corpus"))):
        assert os.path.isdir(path) and mode(path) & 0o077 == 0, path


def test_init_выпускает_служебный_токен_и_не_показывает_его(archive):
    code, out, err = archive("init")
    assert code == 0, err
    path = archive.path("secrets", "local.token")
    with open(path, encoding="utf-8") as f:
        token = f.read().strip()
    assert token.startswith(T.PREFIX) and mode(path) == 0o600 and mode(archive.path("secrets")) == 0o700
    assert T.Store(archive.path("secrets", "tokens.json")).verify(token) == T.Client("dsh-local", "local")
    assert token not in out + err and path in out                                    # путь назван, значения на экране нет
    code, out, err = archive("init", "--json")
    assert token not in out + err


def test_init_строит_пустую_базу_сверки_как_known_build_на_пустом_корпусе(archive):
    assert archive("init")[0] == 0
    db = archive.path("index", "known.sqlite")
    info = N.Known(db).info()
    assert (info["files"], info["mail"], info["corpus"]) == (0, 0, archive.path("corpus")) and info["built"] and mode(db) == 0o600
    code, out, _ = archive("known", "status")
    assert code == 0 and "файлов: 0" in out


def test_init_заводит_пустую_таблицу_docs_со_схемой_которую_пишет_индексация(archive):
    assert archive("init")[0] == 0
    table = archive.table()
    fields = {f.name: f.type for f in table.schema}
    assert table.count_rows() == 0 and list(fields) == INDEX_FIELDS
    assert all(fields[name] == pa.string() for name in ("path", "source", "space", "title", "updated", "url", "text"))
    assert fields["chunk"] == pa.int32() and fields["vector"] == pa.list_(pa.float32(), 1024)


def test_у_таблицы_есть_полнотекстовый_индекс_по_тексту_и_поиск_находит_добавленное_после_init(archive):
    assert archive("init")[0] == 0
    table = archive.table()
    assert [(i.index_type, list(i.columns)) for i in table.list_indices()] == [("FTS", ["text"])]
    fill(table, 2)
    found = archive.table().search("поставки", query_type="fts").limit(10).to_list()
    assert sorted(r["path"] for r in found) == ["входящие/20261006-100000/док0.txt", "входящие/20261006-100000/док1.txt"]


def test_размерность_вектора_берётся_из_настройки_embed_dim_окружения(archive):
    assert archive("init", FLYARCHIVE_EMBED_DIM="16")[0] == 0
    assert archive.table().schema.field("vector").type == pa.list_(pa.float32(), 16)


def test_размерность_вектора_берётся_из_настройки_embed_dim_файла(archive):
    os.makedirs(archive.home, mode=0o700)
    with open(archive.path("settings.json"), "w", encoding="utf-8") as f:
        json.dump({"embed_dim": 12}, f)
    os.chmod(archive.path("settings.json"), 0o600)
    assert archive("init")[0] == 0
    assert archive.table().schema.field("vector").type == pa.list_(pa.float32(), 12)


def test_init_не_создаёт_ничего_кроме_заявленного(archive):
    assert archive("init")[0] == 0
    assert sorted(os.listdir(archive.path("index"))) == ["known.sqlite", "lance"]
    assert os.listdir(archive.path("corpus")) == [] and os.listdir(archive.path("logs")) == []
    assert not os.path.exists(archive.path("inbox.json")) and not os.path.exists(archive.path("preview.json"))
    assert not os.path.exists(os.path.join(archive.user, ".config"))                 # таймер и службы init не ставит


def test_после_init_права_в_порядке_как_у_соседних_команд(archive):
    assert archive("init")[0] == 0
    code, out, err = archive("perms", "check")
    assert code == 0 and "Права в порядке" in out, out + err


# ── схема: одно место ───────────────────────────────────────────
def test_схема_таблицы_записана_одной_функцией_в_ingest_размерность_по_умолчанию_из_настройки():
    schema = G.index_schema()
    assert [f.name for f in schema] == INDEX_FIELDS == list(G.ROW_FIELDS)
    assert schema.field("vector").type == pa.list_(pa.float32(), G.DIM) and G.DIM == 1024
    assert G.index_schema(7).field("vector").type == pa.list_(pa.float32(), 7)
    assert schema.field("chunk").type == pa.int32()


def test_строка_которую_пишет_индексация_состоит_ровно_из_полей_схемы(tmp_path):
    class Table:
        rows = []

        def add(self, rows):
            self.rows.extend(rows)

    path = tmp_path / "записка.txt"
    path.write_text("Согласовать перенос работ на четверг.", encoding="utf-8")
    table = Table()
    item = {"path": str(path), "rel": "входящие/п/записка.txt", "updated": "2026-10-06", "space": "п"}
    assert G.index_documents([item], table, lambda texts: [[0.5] * G.DIM for _ in texts]).indexed == ["входящие/п/записка.txt"]
    assert [list(row) for row in table.rows] == [INDEX_FIELDS]
    assert list(table.rows[0]) == [f.name for f in G.index_schema()]


# ── повтор и готовое ────────────────────────────────────────────
def test_json_ответ_первого_запуска_список_шагов_с_итогом_каждого(archive):
    answer = init_json(archive)
    assert answer["home"] == archive.home and [s["id"] for s in answer["steps"]] == STEPS
    for step in answer["steps"]:
        message = step["message"]
        assert step["result"] == "created" and message["code"] == CREATED[step["id"]] and message["text"], step
    paths = {s["id"]: s["message"]["args"]["path"] for s in answer["steps"]}
    assert paths["home"] == archive.home and paths["secrets"] == archive.path("secrets") and paths["token"] == archive.path("secrets", "local.token")
    assert paths["known"] == archive.path("index", "known.sqlite") and paths["table"] == archive.path("index", "lance")
    table = answer["steps"][-1]["message"]
    assert table["args"]["dim"] == 1024 and "1024" in table["text"]


def test_повторный_init_ничего_не_пересоздаёт_не_очищает_и_не_пишет(archive):
    init_json(archive)
    other = archive("token", "add", "ноутбук")                                     # в хранилище уже есть чужой токен
    assert other[0] == 0
    fill(archive.table())
    before = tree(archive.home)
    version = archive.table().version
    answer = init_json(archive)
    assert [(s["id"], s["result"], s["message"]["code"]) for s in answer["steps"]] == [(i, "exists", EXISTS[i]) for i in STEPS]
    assert tree(archive.home) == before                                              # ни одного изменённого файла, ни одного нового
    assert archive.table().version == version and archive.table().count_rows() == 3


def test_служебный_токен_и_хранилище_при_повторе_те_же(archive):
    init_json(archive)
    archive("token", "add", "ноутбук")
    names = ("local.token", "tokens.json", "link.key")
    with_bytes = {n: open(archive.path("secrets", n), "rb").read() for n in names}
    init_json(archive)
    assert {n: open(archive.path("secrets", n), "rb").read() for n in names} == with_bytes
    assert [r["name"] for r in T.Store(archive.path("secrets", "tokens.json")).list()] == ["dsh-local", "ноутбук"]
    assert mode(archive.path("secrets")) == 0o700 and all(mode(archive.path("secrets", n)) == 0o600 for n in names)       # права остаются владельцу


def test_таблица_с_документами_не_трогается_отпечаток_и_версия_те_же(archive):
    init_json(archive)
    fill(archive.table(), 5)
    table = archive.table()
    before = (fingerprint(table), table.version, tree(archive.path("index", "lance")))
    answer = init_json(archive)
    (step,) = [s for s in answer["steps"] if s["id"] == "table"]
    assert step["result"] == "exists" and step["message"]["code"] == "init.table_exists"
    after = archive.table()
    assert (fingerprint(after), after.version, tree(archive.path("index", "lance"))) == before and fingerprint(after)[1] == 5


def test_таблица_с_документами_и_другой_размерностью_настройки_не_пересоздаётся(archive):
    init_json(archive)
    fill(archive.table(), 2)
    answer = init_json(archive, FLYARCHIVE_EMBED_DIM="16")
    assert [s["result"] for s in answer["steps"] if s["id"] == "table"] == ["exists"]
    assert archive.table().schema.field("vector").type == pa.list_(pa.float32(), 1024) and archive.table().count_rows() == 2


def test_готовые_каталоги_не_пересоздаются_и_права_на_них_не_меняются(archive):
    for name in ("corpus", "logs"):
        os.makedirs(archive.path(name))
        os.chmod(archive.path(name), 0o750)
    answer = init_json(archive)
    expected = {**{i: "created" for i in STEPS}, "home": "exists", "logs": "exists", "corpus": "exists"}
    assert {s["id"]: s["result"] for s in answer["steps"]} == expected
    assert mode(archive.path("corpus")) == 0o750 and mode(archive.path("logs")) == 0o750          # чужие права init не правит
    assert mode(archive.path("index")) == 0o700 and mode(archive.path("secrets")) == 0o700


def test_корпус_с_документами_и_без_базы_сверки_база_строится_по_нему_а_не_пустая(archive):
    folder = archive.path("corpus", "mail")
    os.makedirs(folder)
    with open(os.path.join(folder, "старое.txt"), "w", encoding="utf-8") as f:
        f.write("уже в архиве")
    answer = init_json(archive)
    assert {s["id"]: s["result"] for s in answer["steps"]}["known"] == "created"
    info = N.Known(archive.path("index", "known.sqlite")).info()
    assert info["files"] == 1
    assert N.Known(archive.path("index", "known.sqlite")).find(sha256=hashlib.sha256("уже в архиве".encode("utf-8")).hexdigest()) == "mail/старое.txt"


def test_готовая_база_сверки_не_перестраивается(archive):
    init_json(archive)
    # файла в корпусе нет: пересборка убрала бы запись
    N.add(archive.path("index", "known.sqlite"), [("входящие/п/а.txt", 1, 1, "a" * 64, None, None, None)])
    assert [s["result"] for s in init_json(archive)["steps"] if s["id"] == "known"] == ["exists"]
    assert N.Known(archive.path("index", "known.sqlite")).find(sha256="a" * 64) == "входящие/п/а.txt"


# ── что осталось владельцу ──────────────────────────────────────
def test_init_называет_что_осталось_сделать_руками(archive):
    code, out, err = archive("init")
    assert code == 0, err
    assert "flyarchive preview setup" in out and "llm_local_model" in out and "flyarchive inbox set" in out
    answer = init_json(archive)
    assert [t["id"] for t in answer["todo"]] == ["preview", "model", "inbox"]
    assert [t["message"]["code"] for t in answer["todo"]] == ["init.todo_preview", "init.todo_model", "init.todo_inbox"]
    assert all(t["message"]["text"] for t in answer["todo"])


def test_настроенное_из_списка_осталось_не_значится(archive):
    init_json(archive)
    PC.save_image(archive.home, "gotenberg/gotenberg:8@sha256:" + "a" * 64, "gotenberg/gotenberg:8")
    assert [t["id"] for t in init_json(archive)["todo"]] == ["model", "inbox"]
    assert [t["id"] for t in init_json(archive, FLYARCHIVE_LLM_LOCAL_MODEL="модель-1")["todo"]] == ["inbox"]
    assert archive("inbox", "set", "--path", archive.box, "--llm", "off")[0] == 0
    assert [t["id"] for t in init_json(archive, FLYARCHIVE_LLM_LOCAL_MODEL="модель-1")["todo"]] == []
    code, out, _ = archive("init", FLYARCHIVE_LLM_LOCAL_MODEL="модель-1")
    assert code == 0 and "Осталось" not in out and "готов к работе" in out


# ── отказы: как у соседних команд ───────────────────────────────
def test_каталог_секретов_открыт_другим_отказ_тот_же_что_у_token_init_local_ничего_не_создано(archive):
    os.makedirs(archive.path("secrets"))
    os.chmod(archive.path("secrets"), 0o755)
    neighbour = archive("token", "init-local")
    code, out, err = archive("init", "--json")
    assert code == neighbour[0] == 1 and out == "" and error_line(err)["code"] == "auth.secrets_dir_open"
    assert neighbour[2] == "ошибка: " + error_line(err)["text"] + "\n"
    assert sorted(os.listdir(archive.home)) == ["secrets"] and mode(archive.path("secrets")) == 0o755


def test_хранилище_токенов_открыто_другим_отказ_тот_же_что_у_token_list_и_ничего_не_тронуто(archive):
    init_json(archive)
    fill(archive.table())
    os.chmod(archive.path("secrets", "tokens.json"), 0o644)
    before = tree(archive.home)
    neighbour = archive("token", "list", "--json")
    code, out, err = archive("init", "--json")
    assert code == 1 and out == "" and error_line(err)["code"] == "token.store_open" and error_line(err) == error_line(neighbour[2])
    assert tree(archive.home) == before


def test_файл_настроек_открыт_на_запись_другим_отказ_тот_же_что_у_соседних_команд(archive):
    os.makedirs(archive.home, mode=0o700)
    with open(archive.path("settings.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    os.chmod(archive.path("settings.json"), 0o666)
    neighbour = archive("known", "status")
    code, out, err = archive("init")
    assert code == neighbour[0] == 2 and out == "" and err == neighbour[2] and "settings.json" in err
    assert sorted(os.listdir(archive.home)) == ["settings.json"]


def test_сбой_шага_называется_кодом_и_шагом_а_не_трассировкой(archive):
    os.makedirs(archive.user, exist_ok=True)
    with open(archive.home, "w", encoding="utf-8") as f:                              # на месте каталога архива лежит файл
        f.write("не каталог")
    code, out, err = archive("init", "--json")
    error = error_line(err)
    assert code == 1 and out == "" and error["code"] == "init.step_failed" and error["args"]["what"] == "home"
    assert "Traceback" not in err and error["text"]
    code, out, err = archive("init")
    assert code == 1 and "Traceback" not in err and err.startswith("ошибка: ") and "home" in err


def test_команда_названа_в_справке_и_в_начале_файла_команды(archive):
    code, out, _ = archive("--help")
    assert code == 0 and "init" in out
    code, out, _ = archive("init", "--help")
    assert code == 0 and "--json" in out
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive"), encoding="utf-8") as f:
        assert "flyarchive init" in f.read().split('"""')[1]

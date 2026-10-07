"""Без библиотеки индекса (lancedb) заведение архива и замечания называют библиотеку, а не вид исключения (FR-107, FR-103).

`flyarchive init` и `flyarchive install` без lancedb печатали «шаг «table» не выполнен: ModuleNotFoundError»; `inbox run`, `queue accept` и `doc delete`
не падали, но называли вид исключения («индекс не открылся: ModuleNotFoundError», «индекс недоступен (ModuleNotFoundError)»). Теперь везде одно
сообщение `lib.missing` из `ingest.require_lancedb()` — то же, что у поиска и команд индекса: что не хватает, что без этого не работает и что поставить.
Остальные шаги заведения выполняются как раньше, код возврата тот же, что при невыполненном шаге; прочие сбои открытия индекса называются как раньше.
Отсутствие библиотеки — блокировка импорта (sys.modules[имя] = None, в дочернем процессе — sitecustomize), не удаление.
"""
import os
import site
import sys

import pytest

import ingest as G
import inbox as B
import messages as M
import review as R
from archivekit import Archive, error_line
from test_inbox import CLEAN, EVIL, embed, env, said, written  # noqa: F401 — разбор настоящим проходом на временном корпусе
from test_install import Machine
from test_letters_missing_library import command

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

ARGS = {"package": "lancedb", "use": "index", "file": "requirements.txt"}
NOTE = M.make("lib.missing", package="lancedb", use="index", file="requirements.txt")


def block(monkeypatch):
    monkeypatch.setitem(sys.modules, "lancedb", None)


def no_kind(text):
    """Вида исключения и прежних слов в тексте нет."""
    for word in ("ModuleNotFoundError", "Traceback", "не выполнен", "индекс не открылся", "индекс недоступен"):
        assert word not in text, text


# ── flyarchive init ─────────────────────────────────────────────
@pytest.fixture
def archive(tmp_path):
    return Archive(tmp_path)


def test_init_без_lancedb_называет_библиотеку_строкой_с_кодом_а_не_видом_исключения(archive):
    code, out, err = archive("init", **archive.hide("lancedb"))
    assert code == 1, (out, err)
    assert err == "ошибка: " + str(NOTE) + "\n" and "python3 -m pip install -r requirements.txt" in err
    no_kind(out + err)


def test_init_без_lancedb_остальные_шаги_выполняет_как_раньше_таблицу_не_заводит(archive):
    code, out, err = archive("init", **archive.hide("lancedb"))
    assert code == 1
    assert "служебный токен" in out and "база сверки с архивом создана" in out, "шаги до таблицы сделаны и названы"
    assert "таблица индекса" not in out
    for name in ("secrets", "logs", "index", "corpus"):
        assert os.path.isdir(archive.path(name)), name
    assert os.path.isfile(archive.path("index", "known.sqlite")) and os.path.isfile(archive.path("secrets", "tokens.json"))
    assert not os.path.exists(archive.path("index", "lance")), "без библиотеки таблица не заводится, и пустой каталог индекса тоже"


def test_init_с_json_без_lancedb_отвечает_объектом_ошибки_с_кодом_и_пустым_stdout(archive):
    code, out, err = archive("init", "--json", **archive.hide("lancedb"))
    assert code == 1 and out == "", (out, err)
    error = error_line(err)
    assert error["code"] == "lib.missing" and error["args"] == ARGS and error["text"] == str(NOTE)


def test_после_установки_библиотеки_init_доводит_заведение_до_конца_готовое_не_трогая(archive):
    assert archive("init", **archive.hide("lancedb"))[0] == 1
    code, out, err = archive("init")
    assert code == 0, (out, err)
    assert "таблица индекса docs создана" in out and "база сверки с архивом уже есть" in out and "служебный токен уже на месте" in out
    assert os.path.isdir(archive.path("index", "lance"))


def test_другой_сбой_шага_называется_как_раньше_шагом_а_не_библиотекой(archive):
    os.makedirs(archive.user, exist_ok=True)
    with open(archive.home, "w", encoding="utf-8") as f:                      # на месте каталога архива лежит файл
        f.write("не каталог")
    code, out, err = archive("init", "--json", **archive.hide("lancedb"))
    error = error_line(err)
    assert code == 1 and error["code"] == "init.step_failed" and error["args"]["what"] == "home", err


def test_у_библиотеки_индекса_нет_зависимости_это_не_нет_lancedb_ошибка_остаётся_как_есть(tmp_path, monkeypatch):
    folder = tmp_path / "путь" / "lancedb"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("import dependency_that_is_not_installed_anywhere\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "путь"))
    monkeypatch.delitem(sys.modules, "lancedb")
    with pytest.raises(ModuleNotFoundError) as e:
        G.create_table(str(tmp_path / "flyarchive"))
    assert e.value.name == "dependency_that_is_not_installed_anywhere" and not isinstance(e.value, M.CodedError)


# ── flyarchive install ──────────────────────────────────────────
def hidden(base, *modules):
    """Переменные окружения для дочернего процесса: перечисленные библиотеки не импортируются."""
    folder = base / "скрыто"
    folder.mkdir()
    (folder / "sitecustomize.py").write_text("import sys\nfor name in %r:\n    sys.modules[name] = None\n" % (list(modules),), encoding="utf-8")
    return {"PYTHONPATH": os.pathsep.join(p for p in (str(folder), site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p)}


def test_install_без_lancedb_называет_библиотеку_тем_же_сообщением_и_служб_не_ставит(tmp_path):
    machine = Machine(tmp_path)
    r = machine.run("--no-start", "--no-plugin", **hidden(tmp_path, "lancedb"))
    assert r.code == 1, (r.out, r.err)
    assert r.err == "ошибка: " + str(NOTE) + "\n"
    no_kind(r.out + r.err)
    assert "служебный токен" in r.out, "заведение архива шло теми же шагами, что init"
    assert not machine.units.exists() and machine.log() == [], "до служб дело не дошло: установка остановилась на архиве"


def test_install_с_json_без_lancedb_отвечает_объектом_ошибки_с_кодом(tmp_path):
    r = Machine(tmp_path).run("--json", "--no-start", "--no-plugin", **hidden(tmp_path, "lancedb"))
    assert r.code == 1 and r.out == "", (r.out, r.err)
    error = error_line(r.err)
    assert error["code"] == "lib.missing" and error["args"] == ARGS


# ── ingest ──────────────────────────────────────────────────────
def test_open_table_и_create_table_без_lancedb_отказывают_сообщением_lib_missing_и_ничего_не_создают(tmp_path, monkeypatch):
    block(monkeypatch)
    home = str(tmp_path / "flyarchive")
    os.makedirs(home)
    for call in (G.open_table, G.create_table):
        with pytest.raises(G.LibraryMissing) as e:
            call(home)
        assert (e.value.message.code, e.value.message.args) == ("lib.missing", ARGS) and str(e.value) == str(NOTE)
        assert isinstance(e.value, M.CodedError)
    assert os.listdir(home) == []


# ── проход входящих ─────────────────────────────────────────────
def settled(env, monkeypatch, *names):
    env.table = None                                                    # настоящий open_table на временном архиве
    for i, name in enumerate(names):
        env.put(name, CLEAN + str(i).encode("ascii"))
    block(monkeypatch)
    return env.settle()


def test_проход_без_lancedb_называет_библиотеку_замечанием_lib_missing_а_не_видом_исключения(env, monkeypatch):
    s = settled(env, monkeypatch, "договор.txt")
    rel = f"входящие/{s.batch}/договор.txt"
    assert s.counts == {"accept": 1}
    (p,) = s.problems
    said(p, "lib.missing", ARGS, str(NOTE))
    no_kind(str(p))
    written(env, s)
    assert [d["rel"] for d in B._read_pending(env.home)] == [rel], "документ ждёт индекса долгом, как и раньше"
    assert os.path.isfile(os.path.join(env.corpus, "входящие", s.batch, "договор.txt"))


def test_несколько_документов_одно_замечание_о_библиотеке_а_не_по_одному_на_документ(env, monkeypatch):
    s = settled(env, monkeypatch, "а.txt", "б.txt", "в.txt")
    assert s.counts == {"accept": 3}
    assert [(p.code, p.args) for p in s.problems] == [("lib.missing", ARGS)]
    assert len(B._read_pending(env.home)) == 3


def test_следующий_проход_без_lancedb_называет_библиотеку_тем_же_замечанием_долг_не_теряется(env, monkeypatch):
    s = settled(env, monkeypatch, "договор.txt")
    env.now += 60
    again = env.run()
    assert again.batch is None
    (p,) = again.problems
    said(p, "lib.missing", ARGS, str(NOTE))
    assert [d["rel"] for d in B._read_pending(env.home)] == [f"входящие/{s.batch}/договор.txt"]


def test_библиотека_ставится_следующий_проход_отдаёт_долг_и_замечаний_нет(env, monkeypatch):
    with monkeypatch.context() as hidden_lib:
        settled(env, hidden_lib, "договор.txt")
    assert G.create_table(env.home) is True
    env.now += 60
    again = env.run()
    assert again.problems == [] and B._read_pending(env.home) == []


@pytest.mark.parametrize("error", [ModuleNotFoundError("No module named 'dependency'", name="dependency"), ImportError("не загружается"),
                                   OSError("нет доступа")], ids=lambda e: type(e).__name__)
def test_другой_сбой_открытия_индекса_называет_вид_исключения_как_раньше(env, monkeypatch, error):
    def broken(home):
        raise error

    monkeypatch.setattr(B.ingest, "open_table", broken)
    env.table = None
    env.put("договор.txt", CLEAN)
    s = env.settle()
    (p,) = s.problems
    assert (p.code, p.args["why"]) == ("problem.index_failed", f"индекс не открылся: {type(error).__name__}")


def test_команда_inbox_run_без_lancedb_печатает_замечание_про_библиотеку_один_раз(tmp_path):
    made = Archive(tmp_path)
    assert made("init")[0] == 0
    os.makedirs(made.box)
    code, out, err = made("inbox", "set", "--path", made.box, "--llm", "off", "--cloud", "off", "--no-timer")
    assert code == 0, (out, err)
    made.instant()
    for i in range(2):
        made.put(f"договор-{i}.txt", CLEAN + str(i).encode("ascii"))
    code, out, err = made("inbox", "run", **made.hide("lancedb"))
    assert code == 0, (out, err)
    assert err.count("замечание: " + str(NOTE)) == 1, err
    no_kind(out + err)


# ── принятие из очереди ─────────────────────────────────────────
@pytest.fixture
def queued(env):
    """Документ, который проверка отправила владельцу в очередь; индекс на этом шаге подставной, чтобы проход не зависел от библиотеки."""
    env.put("отчёт.txt", EVIL)
    s = env.settle()
    assert s.counts == {"review": 1}, s.counts
    env.q, env.c = f"очередь/{s.batch}/отчёт.txt", f"входящие/{s.batch}/отчёт.txt"
    return env


def test_принятие_без_lancedb_называет_библиотеку_в_ответе_документ_принят_долг_остаётся(queued, monkeypatch):
    block(monkeypatch)
    done = R.queue_accept(queued.home, queued.q, table=None, embed=embed)
    assert done == {"path": queued.c, "indexed": False, "notes": [NOTE.to_json()]}
    assert any(d["rel"] == queued.c for d in B._read_pending(queued.home)), "документ ждёт индекса долгом"


def test_принятие_с_таблицей_ответ_прежний_без_заметок(queued):
    done = R.queue_accept(queued.home, queued.q, table=queued.table, embed=embed)
    assert done == {"path": queued.c, "indexed": True}


def test_команда_принятия_без_lancedb_пишет_замечание_про_библиотеку_в_stderr_и_код_возврата_прежний(queued, monkeypatch, capsys):
    block(monkeypatch)
    code = command(monkeypatch, queued.home)("queue", "accept", queued.q)
    out, err = capsys.readouterr()
    assert code == 0 and out.startswith("Документ принят: " + queued.c), (out, err)
    assert err == "замечание: " + str(NOTE) + "\n"


# ── удаление документа ──────────────────────────────────────────
@pytest.fixture
def stored(tmp_path):
    home = tmp_path / "flyarchive"
    folder = home / "corpus" / "входящие" / "п"
    folder.mkdir(parents=True)
    (folder / "а.txt").write_text("текст", encoding="utf-8")
    return str(home), folder / "а.txt"


def test_удаление_без_lancedb_отказывает_сообщением_про_библиотеку_документ_не_тронут(stored, monkeypatch):
    home, doc = stored
    block(monkeypatch)
    with pytest.raises(R.ReviewError) as e:
        R.doc_delete(home, "входящие/п/а.txt")
    assert (e.value.message.code, e.value.message.args) == ("lib.missing", ARGS) and str(e.value) == str(NOTE)
    assert doc.is_file() and not os.path.exists(os.path.join(home, "index")) and not os.path.exists(os.path.join(home, "удалённое"))


def test_команда_удаления_без_lancedb_отвечает_строкой_про_библиотеку_и_кодом_1(stored, monkeypatch, capsys):
    home, doc = stored
    block(monkeypatch)
    code = command(monkeypatch, home)("doc", "delete", "--yes", "входящие/п/а.txt")
    out, err = capsys.readouterr()
    assert code == 1 and out == "" and err == "ошибка: " + str(NOTE) + "\n", (out, err)
    assert doc.is_file()


def test_удаление_без_таблицы_при_установленной_библиотеке_отказ_прежний(stored):
    home, doc = stored
    with pytest.raises(R.ReviewError) as e:
        R.doc_delete(home, "входящие/п/а.txt")
    assert e.value.message.code == "review.index_unavailable" and e.value.message.args == {"error_type": "TableMissing"}

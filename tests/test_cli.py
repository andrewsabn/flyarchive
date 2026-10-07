"""Команда flyarchive: FR-02, FR-08, FR-10."""
import json
import os
import re
import stat
import subprocess
import sys

import pytest

import auth as A
import journal as J
import known as KN
import tokens as T

FLYARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")
TOKEN = re.compile(r"ba_[A-Za-z0-9_-]{43}")


@pytest.fixture
def cli(tmp_path):
    home = str(tmp_path / "home")

    def run(*args):
        env = {**os.environ, "FLYARCHIVE_HOME": home, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, FLYARCHIVE, *args], env=env, capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout, r.stderr

    run.home = home
    run.store = lambda: T.Store(os.path.join(home, "secrets", "tokens.json"))
    run.local_file = os.path.join(home, "secrets", "local.token")
    return run


# ── token add ───────────────────────────────────────────────────
def test_выпуск_печатает_токен_и_он_действует(cli):
    code, out, err = cli("token", "add", "ноутбук")
    (tok,) = TOKEN.findall(out)
    assert code == 0 and err == ""
    assert cli.store().verify(tok) == T.Client("ноутбук", "read")       # уровень по умолчанию — чтение
    assert "второй раз" in out


def test_выпуск_полного_токена_со_сроком(cli):
    code, out, _ = cli("token", "add", "планшет", "--level", "full", "--days", "7")
    (tok,) = TOKEN.findall(out)
    assert code == 0 and cli.store().verify(tok) == T.Client("планшет", "full")
    assert cli.store().list()[0]["expires"] is not None


def test_занятое_имя_код_возврата_не_ноль(cli):
    cli("token", "add", "ноутбук")
    code, out, err = cli("token", "add", "ноутбук", "--level", "full")
    assert code == 1 and "занято" in err and not TOKEN.findall(out + err)


@pytest.mark.parametrize("args", [("token", "add", "x", "--level", "local"), ("token", "add", "x", "--level", "admin"),
                                  ("token", "add"), ("token",), ()])
def test_неверный_вызов_отклоняется(cli, args):
    code, out, _ = cli(*args)
    assert code != 0 and not TOKEN.findall(out)
    assert cli.store().list() == []


# ── token list ──────────────────────────────────────────────────
def test_список_без_значений_токенов(cli):
    _, out_add, _ = cli("token", "add", "ноутбук")
    cli("token", "add", "планшет", "--level", "full", "--days", "7")
    (tok,) = TOKEN.findall(out_add)
    code, out, _ = cli("token", "list")
    assert code == 0 and "ноутбук" in out and "планшет" in out and "read" in out and "full" in out
    assert not TOKEN.findall(out) and tok[3:] not in out and T.digest(tok) not in out
    assert out.count("действует") == 2


def test_пустой_список(cli):
    code, out, _ = cli("token", "list")
    assert code == 0 and "нет" in out


# ── token revoke ────────────────────────────────────────────────
def test_отзыв(cli):
    _, out_add, _ = cli("token", "add", "ноутбук")
    (tok,) = TOKEN.findall(out_add)
    code, out, _ = cli("token", "revoke", "ноутбук")
    assert code == 0 and "отозван" in out
    assert cli.store().verify(tok) is None
    assert "отозван" in cli("token", "list")[1]


def test_отзыв_несуществующего_код_возврата_не_ноль(cli):
    code, out, err = cli("token", "revoke", "нет-такого")
    assert code == 1 and "нет-такого" in err and out == ""


def test_хранилище_открытое_чужим_команда_отказывает(cli):
    cli("token", "add", "ноутбук")
    os.chmod(cli.store().path, 0o644)
    code, _, err = cli("token", "list")
    assert code == 1 and "600" in err


# ── служебный токен локального DSH ──────────────────────────────
def test_служебный_токен_создаётся_в_закрытом_файле_и_не_печатается(cli):
    code, out, err = cli("token", "init-local")
    tok = open(cli.local_file, encoding="utf-8").read().strip()
    assert code == 0 and not TOKEN.findall(out + err)
    assert TOKEN.fullmatch(tok) and cli.store().verify(tok) == T.Client("dsh-local", "local")
    assert stat.S_IMODE(os.stat(cli.local_file).st_mode) == 0o600


def test_повторный_запуск_служебный_токен_не_меняет(cli):
    cli("token", "init-local")
    first = open(cli.local_file, encoding="utf-8").read()
    code, out, _ = cli("token", "init-local")
    assert code == 0 and open(cli.local_file, encoding="utf-8").read() == first
    assert len(cli.store().list()) == 1


def test_пропавший_файл_служебного_токена_восстанавливается_старый_гаснет(cli):
    cli("token", "init-local")
    old = open(cli.local_file, encoding="utf-8").read().strip()
    os.unlink(cli.local_file)
    assert cli("token", "init-local")[0] == 0
    new = open(cli.local_file, encoding="utf-8").read().strip()
    assert new != old and cli.store().verify(old) is None
    assert cli.store().verify(new) == T.Client("dsh-local", "local")


def test_отозванный_служебный_токен_перевыпускается(cli):
    cli("token", "init-local")
    old = open(cli.local_file, encoding="utf-8").read().strip()
    cli("token", "revoke", "dsh-local")
    assert cli("token", "init-local")[0] == 0
    new = open(cli.local_file, encoding="utf-8").read().strip()
    assert new != old and cli.store().verify(new) == T.Client("dsh-local", "local")


def test_файл_служебного_токена_открытый_чужим_отказ(cli):
    cli("token", "init-local")
    os.chmod(cli.local_file, 0o644)
    code, _, err = cli("token", "init-local")
    assert code == 1 and "600" in err


# ── вход на страницу поиска ─────────────────────────────────────
def test_ссылка_входа_одноразовая(cli):
    code, out, _ = cli("open")
    (url,) = re.findall(r"http://127\.0\.0\.1:8765/login\?c=[A-Za-z0-9_-]+", out)
    guard = A.Guard("search", home=cli.home)
    c = url.split("c=")[1]
    assert code == 0 and guard.redeem(c) and guard.redeem(c) is None


def first_line_of_open(tmp_path, **env):
    """Первая строка ответа `flyarchive open` при заданных переменных окружения (свой каталог архива, настройки из окружения набора не идут)."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("FLYARCHIVE_")}
    r = subprocess.run([sys.executable, FLYARCHIVE, "open"], env={**base, "FLYARCHIVE_HOME": str(tmp_path / "home"), "PYTHONIOENCODING": "utf-8", **env},
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()[0]


def test_срок_ссылки_входа_по_умолчанию_назван_прежними_словами_байт_в_байт(tmp_path):
    assert first_line_of_open(tmp_path) == "Страница поиска. Ссылка одноразовая, действует пять минут:"


@pytest.mark.parametrize("seconds, said", [(30, "30 секунд"), (45, "45 секунд"), (60, "одну минуту"), (61, "61 секунду"), (90, "90 секунд"),
                                           (120, "две минуты"), (240, "четыре минуты"), (300, "пять минут"), (600, "десять минут"),
                                           (660, "11 минут"), (900, "15 минут"), (1260, "21 минуту"), (3600, "60 минут")])
def test_срок_ссылки_входа_берётся_из_настройки_login_ttl_s(tmp_path, seconds, said):
    assert first_line_of_open(tmp_path, FLYARCHIVE_LOGIN_TTL_S=str(seconds)) == f"Страница поиска. Ссылка одноразовая, действует {said}:"


def test_срок_ссылки_входа_из_файла_настроек_называется_так_же(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / "settings.json").write_text(json.dumps({"login_ttl_s": 120}), encoding="utf-8")
    (home / "settings.json").chmod(0o600)
    assert first_line_of_open(tmp_path) == "Страница поиска. Ссылка одноразовая, действует две минуты:"


# ── журнал ──────────────────────────────────────────────────────
def test_просмотр_журнала(cli):
    assert "пуст" in cli("journal")[1]
    jr = J.Journal(os.path.join(cli.home, "logs", "access.jsonl"))
    for i in range(5):
        jr.write(server="search", client="ноутбук" if i % 2 else "планшет", level="read", tool="/api/search",
                 params={"q": f"запрос {i}"}, status=200 if i else 401, outcome="ok" if i else "отказ: нет токена", ms=10 * i)
    code, out, _ = cli("journal", "-n", "2")
    assert code == 0 and "запрос 3" in out and "запрос 4" in out and "запрос 2" not in out
    code, out, _ = cli("journal", "--client", "ноутбук")
    assert "запрос 1" in out and "запрос 3" in out and "планшет" not in out
    assert "отказ: нет токена" in cli("journal")[1]


def test_журнал_показывает_местное_время(cli):
    jr = J.Journal(os.path.join(cli.home, "logs", "access.jsonl"), clock=lambda: 1_800_000_000)   # 08:00:00 UTC
    jr.write(server="search", client="ноутбук", tool="/api/search", params={}, status=200, outcome="ok", ms=1)
    env = {**os.environ, "FLYARCHIVE_HOME": cli.home, "PYTHONIOENCODING": "utf-8", "TZ": "UTC-5"}   # пояс UTC+5
    r = subprocess.run([sys.executable, FLYARCHIVE, "journal"], env=env, capture_output=True, text=True, encoding="utf-8")
    assert "2027-01-15 13:00:00" in r.stdout and "08:00:00" not in r.stdout


# ── разбор пачки ────────────────────────────────────────────────
def batch(tmp_path):
    src = tmp_path / "входящие"
    src.mkdir()
    (src / "договор.txt").write_text("Договор поставки.", encoding="utf-8")
    (src / "run.bat").write_bytes(b"@echo off")
    return src


def test_разбор_пачки_в_папку(cli, tmp_path):
    src, into = batch(tmp_path), tmp_path / "разбор"
    code, out, err = cli("check", str(src), "--into", str(into), "--without-archive")
    assert code == 0 and err == ""
    assert "Принято" in out and "Карантин" in out and "отчёт.md" in out
    assert (into / "принято" / "договор.txt").exists() and (into / "карантин" / "run.bat").exists()
    assert sorted(p.name for p in src.iterdir()) == ["run.bat", "договор.txt"]          # источник не тронут


def test_разбор_с_заданным_пределом_числа_файлов(cli, tmp_path):
    import zipfile
    src = tmp_path / "пачка.zip"
    with zipfile.ZipFile(src, "w") as z:
        for i in range(3):
            z.writestr(f"f{i}.txt", f"файл {i}")
    code, out, _ = cli("check", str(src), "--into", str(tmp_path / "разбор1"), "--max-files", "2", "--without-archive")
    assert code == 0 and "Карантин" in out and not (tmp_path / "разбор1" / "принято").exists()
    code, out, _ = cli("check", str(src), "--into", str(tmp_path / "разбор2"), "--max-files", "3", "--without-archive")
    assert len(list((tmp_path / "разбор2" / "принято" / "пачка.zip").iterdir())) == 3


def test_разбор_в_непустой_каталог_и_внутрь_корпуса_отказ(cli, tmp_path):
    src = batch(tmp_path)
    code, _, err = cli("check", str(src), "--into", str(src.parent), "--without-archive")
    assert code == 1 and "не пуст" in err
    code, _, err = cli("check", str(src), "--into", os.path.join(cli.home, "corpus", "входящие"), "--without-archive")
    assert code == 1 and "корпус" in err


def test_разбор_несуществующего_источника_отказ(cli, tmp_path):
    code, _, err = cli("check", str(tmp_path / "нет"), "--into", str(tmp_path / "разбор"), "--without-archive")
    assert code == 1 and "нет" in err


# ── сверка с архивом ────────────────────────────────────────────
def test_разбор_без_базы_сверки_отказывает_и_говорит_что_делать(cli, tmp_path):
    src = batch(tmp_path)
    code, out, err = cli("check", str(src), "--into", str(tmp_path / "разбор"))
    assert code == 1 and "flyarchive known build" in err
    assert not (tmp_path / "разбор" / "принято").exists()


def test_сборка_базы_и_сверка_через_команду(cli, tmp_path):
    import gatekit as K
    corpus = os.path.join(cli.home, "corpus", "export-a", "Inbox")
    os.makedirs(corpus)
    with open(os.path.join(corpus, "старое.eml"), "wb") as f:
        f.write(K.letter())
    code, out, err = cli("known", "build")
    assert code == 0 and "файлов: 1" in out and "писем: 1" in out
    code, out, _ = cli("known", "status")
    assert code == 0 and "файлов: 1" in out

    src = tmp_path / "входящие"
    src.mkdir()
    (src / "то же.eml").write_bytes(K.letter(extra=K.RECEIVED, crlf=False))
    (src / "новое.eml").write_bytes(K.letter(mid="<new@example.org>", body="Новый текст письма."))
    code, out, err = cli("check", str(src), "--into", str(tmp_path / "разбор"))
    assert code == 0 and "Дубликаты: 1" in out and "Принято: 1" in out
    assert "Сверка с архивом" in out


def test_состояние_базы_когда_её_нет(cli):
    code, out, _ = cli("known", "status")
    assert code == 0 and "не построена" in out


# ── чистка индекса от повторов ──────────────────────────────────
def put_sources(cli):
    """Таблица источников каталога архива: почтовый корень и корень страниц, какими они были в коде до выноса правил в таблицу источников (FR-99)."""
    os.makedirs(cli.home, exist_ok=True)
    path = os.path.join(cli.home, "sources.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"roots": {"export-a": {"kind": "mail", "source": "mail-other"}, "jira": {"kind": "pages", "source": "jira", "attachments": "jira-att"}}}, f)
    os.chmod(path, 0o600)


@pytest.fixture
def indexed(cli):
    """Корпус с письмом в двух выгрузках, база известного и индекс с фрагментами обоих файлов."""
    lancedb = pytest.importorskip("lancedb")
    import gatekit as K
    put_sources(cli)
    for box, data in (("Outlook_box_in", K.letter()), ("Outlook_box_out", K.letter(extra=K.RECEIVED))):
        folder = os.path.join(cli.home, "corpus", "export-a", box, "Inbox")
        os.makedirs(folder)
        with open(os.path.join(folder, "a.eml"), "wb") as f:
            f.write(data)
    os.makedirs(os.path.join(cli.home, "corpus", "jira", "IT"))
    with open(os.path.join(cli.home, "corpus", "jira", "IT", "задача.md"), "w", encoding="utf-8") as f:
        f.write("задача")
    assert cli("known", "build")[0] == 0
    bs = chr(92)
    rows = [{"path": p, "source": "mail", "updated": "2024-03-05", "chunk": i, "text": "текст", "vector": [0.0, 1.0]}
            for p in ("export-a" + bs + "Outlook_box_in" + bs + "Inbox" + bs + "a.eml",
                      "export-a" + bs + "Outlook_box_out" + bs + "Inbox" + bs + "a.eml", "jira" + bs + "IT" + bs + "задача.md")
            for i in range(2)]
    db = lancedb.connect(os.path.join(cli.home, "index", "lance"))
    db.create_table("docs", rows)
    return lambda: lancedb.connect(os.path.join(cli.home, "index", "lance")).open_table("docs")


def test_чистка_индекса_без_ключа_только_считает(cli, indexed):
    code, out, err = cli("index", "dedupe")
    assert code == 0 and "убрать путей: 1" in out and "фрагментов: 2" in out and "Ничего не изменено" in out
    assert indexed().count_rows() == 6


def test_чистка_индекса_с_ключом_удаляет_и_сохраняет_список(cli, indexed):
    code, out, err = cli("index", "dedupe", "--apply")
    assert code == 0 and "удалено фрагментов: 2" in out
    table = indexed()
    assert table.count_rows() == 4
    left = sorted(set(table.search().select(["path"]).limit(100).to_arrow().column("path").to_pylist()))
    assert [p.replace(chr(92), "/") for p in left] == ["export-a/Outlook_box_in/Inbox/a.eml", "jira/IT/задача.md"]
    lists = [f for f in os.listdir(os.path.join(cli.home, "index")) if f.startswith("dedupe-dropped-")]
    assert len(lists) == 1
    assert "Outlook_box_out" in open(os.path.join(cli.home, "index", lists[0]), encoding="utf-8").read()
    code, out, _ = cli("index", "dedupe", "--apply")
    assert code == 0 and "убрать путей: 0" in out and indexed().count_rows() == 4


def test_чистка_индекса_без_базы_известного_отказ(cli):
    code, _, err = cli("index", "dedupe")
    assert code == 1 and "flyarchive known build" in err


# ── настоящие даты в индексе ────────────────────────────────────
@pytest.fixture
def dated(cli):
    """Корпус с письмом и вложением, база известного и индекс, где у обоих стоит день копирования."""
    lancedb = pytest.importorskip("lancedb")
    import gatekit as K
    put_sources(cli)
    folder = os.path.join(cli.home, "corpus", "export-a", "x", "Inbox")
    att = os.path.join(folder, "_attachments", "2024-03-05_petrov_Пл_0a1b2c3d")
    os.makedirs(att)
    with open(os.path.join(folder, "2024-03-05_petrov_План_0a1b2c3d.eml"), "wb") as f:
        f.write(K.letter(date="Tue, 05 Mar 2024 14:32:00 +0500"))
    with open(os.path.join(att, "договор.docx"), "wb") as f:
        f.write(K.ooxml("docx", "Договор."))
    assert cli("known", "build")[0] == 0
    bs = chr(92)
    letter = bs.join(("export-a", "x", "Inbox", "2024-03-05_petrov_План_0a1b2c3d.eml"))
    doc = bs.join(("export-a", "x", "Inbox", "_attachments", "2024-03-05_petrov_Пл_0a1b2c3d", "договор.docx"))
    rows = [{"path": p, "source": "mail", "space": "x", "title": "т", "updated": d, "url": "", "chunk": i, "text": "текст", "vector": [0.0, 1.0]}
            for p, d in ((letter, "2024-03-05"), (doc, "2026-08-31"), ("jira" + bs + "IT" + bs + "IT-1__задача.md", "2025-06-15")) for i in range(2)]
    lancedb.connect(os.path.join(cli.home, "index", "lance")).create_table("docs", rows)

    def read():
        t = lancedb.connect(os.path.join(cli.home, "index", "lance")).open_table("docs")
        return {r["path"].replace(bs, "/").rsplit("/", 1)[-1]: r["updated"] for r in t.search().limit(100).to_list()}

    return read


def test_даты_без_ключа_только_считает(cli, dated):
    code, out, err = cli("index", "dates")
    assert code == 0 and "поменять дату у путей: 1" in out and "Ничего не изменено" in out
    assert dated()["договор.docx"] == "2026-08-31"


def test_даты_с_ключом_переписывают_индекс_и_оставляют_прежний_рядом(cli, dated):
    code, out, err = cli("index", "dates", "--apply")
    assert code == 0 and "Готово" in out
    assert dated() == {"2024-03-05_petrov_План_0a1b2c3d.eml": "2024-03-05", "договор.docx": "2024-03-05", "IT-1__задача.md": "2025-06-15"}
    kept = [d for d in os.listdir(os.path.join(cli.home, "index")) if d.startswith("lance.before-dates-")]
    assert len(kept) == 1 and not os.path.exists(os.path.join(cli.home, "index", "lance.new"))
    code, out, _ = cli("index", "dates", "--apply")
    assert code == 0 and "поменять дату у путей: 0" in out


def test_даты_без_базы_известного_отказ(cli):
    code, _, err = cli("index", "dates")
    assert code == 1 and "flyarchive known build" in err


# ── вывод для программ (--json): им пользуется раздел «Архив» в DSH ─
def access(cli):
    return J.Journal(os.path.join(cli.home, "logs", "access.jsonl")).tail(100)


def test_список_токенов_json(cli):
    cli("token", "add", "ноутбук")
    cli("token", "add", "старый", "--level", "full")
    cli("token", "revoke", "старый")
    code, out, err = cli("token", "list", "--json")
    rows = json.loads(out)
    assert code == 0 and err == ""
    assert [(r["name"], r["level"], r["state"]) for r in rows] == [("ноутбук", "read", "действует"), ("старый", "full", "отозван")]
    assert set(rows[0]) == {"name", "level", "created", "expires", "last_used", "revoked", "state"}
    assert "hash" not in out and not TOKEN.findall(out)


def test_пустой_список_токенов_json(cli):
    code, out, _ = cli("token", "list", "--json")
    assert code == 0 and json.loads(out) == []


def test_выпуск_json_отдаёт_значение_токена(cli):
    code, out, err = cli("token", "add", "планшет", "--level", "full", "--days", "7", "--json")
    d = json.loads(out)
    assert code == 0 and err == "" and set(d) == {"name", "level", "token", "expires"}
    assert (d["name"], d["level"]) == ("планшет", "full") and d["expires"]
    assert cli.store().verify(d["token"]) == T.Client("планшет", "full")


def test_отзыв_json(cli):
    cli("token", "add", "ноутбук")
    code, out, _ = cli("token", "revoke", "ноутбук", "--json")
    assert code == 0 and json.loads(out) == {"name": "ноутбук", "revoked": True}


def test_ошибка_в_режиме_json_идёт_в_stderr_а_вывод_пуст(cli):
    cli("token", "add", "ноутбук")
    code, out, err = cli("token", "add", "ноутбук", "--json")
    assert code == 1 and out == "" and "занято" in err
    code, out, err = cli("token", "revoke", "нет-такого", "--json")
    assert code == 1 and out == "" and "нет" in err


def test_журнал_json(cli):
    path = os.path.join(cli.home, "logs", "access.jsonl")
    j = J.Journal(path)
    for client, tool in (("ноутбук", "/api/search"), ("планшет", "/doc"), ("ноутбук", "mcp:read_document")):
        j.write(server="search", client=client, tool=tool, params={"q": "тест"}, status=200, outcome="ok", ms=5)
    code, out, _ = cli("journal", "--json", "-n", "2", "--client", "ноутбук")
    recs = json.loads(out)
    assert code == 0 and [r["tool"] for r in recs] == ["/api/search", "mcp:read_document"]
    assert set(recs[0]) >= {"ts", "server", "client", "tool", "params", "status", "outcome", "ms"}
    assert json.loads(cli("journal", "--json", "--client", "никто")[1]) == []


def test_выпуск_и_отзыв_токена_записаны_в_журнал(cli):
    code, out, _ = cli("token", "add", "ноутбук", "--level", "full", "--days", "30")
    (tok,) = TOKEN.findall(out)
    cli("token", "add", "ноутбук")                                    # отказ: имя занято
    cli("token", "revoke", "ноутбук")
    recs = access(cli)
    assert [(r["tool"], r["status"]) for r in recs] == [("token add", 0), ("token add", 1), ("token revoke", 0)]
    assert all(r["server"] == "cli" and r["client"] == "владелец" for r in recs)
    assert recs[0]["params"] == {"name": "ноутбук", "level": "full", "days": "30"} and recs[2]["params"] == {"name": "ноутбук"}
    assert recs[1]["outcome"].startswith("отказ") and recs[0]["outcome"] == "ok"
    with open(os.path.join(cli.home, "logs", "access.jsonl"), encoding="utf-8") as f:
        assert tok not in f.read()


def test_просмотр_списка_и_журнала_в_журнал_не_пишется(cli):
    cli("token", "list")
    cli("token", "list", "--json")
    cli("journal")
    assert access(cli) == []


# ── FR-73а: отказ с --json — последняя строка stderr {"error": {code, args, text}}; без --json — «ошибка: текст» ──
def last_line(err):
    return err.rstrip("\n").split("\n")[-1]


def error_of(err):
    """Объект ошибки из последней строки stderr; строка — одна, целая и последняя."""
    assert err.endswith("\n")
    obj = json.loads(last_line(err))
    assert list(obj) == ["error"] and set(obj["error"]) == {"code", "args", "text"}
    return obj["error"]


BUSY_NAME = "имя «ноутбук» занято действующим токеном — отзови его или выбери другое"


def test_отказ_с_json_последняя_строка_stderr_объект_с_кодом_параметрами_и_текстом(cli):
    cli("token", "add", "ноутбук")
    code, out, err = cli("token", "add", "ноутбук", "--json")
    assert code == 1 and out == ""
    assert error_of(err) == {"code": "token.name_taken", "args": {"name": "ноутбук"}, "text": BUSY_NAME}
    assert len(err.splitlines()) == 1 and not TOKEN.findall(err)


def test_отказ_без_json_как_раньше_ошибка_и_текст(cli):
    cli("token", "add", "ноутбук")
    code, out, err = cli("token", "add", "ноутбук")
    assert code == 1 and out == "" and err == "ошибка: " + BUSY_NAME + "\n" and '"error"' not in err


def test_отказ_отзыва_с_json_и_без(cli):
    code, out, err = cli("token", "revoke", "нет-такого", "--json")
    assert code == 1 and out == ""
    assert error_of(err) == {"code": "token.no_active", "args": {"name": "нет-такого"},
                             "text": "действующего токена с именем «нет-такого» нет"}
    code, out, err = cli("token", "revoke", "нет-такого")
    assert code == 1 and err == "ошибка: действующего токена с именем «нет-такого» нет\n"


def test_отказ_хранилища_с_json_несёт_путь_к_файлу(cli):
    cli("token", "add", "ноутбук")
    path = os.path.join(cli.home, "secrets", "tokens.json")
    os.chmod(path, 0o644)
    for args in (("token", "list", "--json"), ("token", "add", "другой", "--json"), ("token", "revoke", "ноутбук", "--json")):
        code, out, err = cli(*args)
        assert code == 1 and out == ""
        assert error_of(err) == {"code": "token.store_open", "args": {"path": path},
                                 "text": f"права на {path} шире 600 — хранилище токенов открыто чужим, не использую"}


def test_отказ_команды_без_ключа_json_строки_json_не_печатает(cli):
    cli("token", "add", "ноутбук")
    path = os.path.join(cli.home, "secrets", "tokens.json")
    os.chmod(path, 0o644)
    code, out, err = cli("token", "init-local")                         # у этой подкоманды ключа --json нет
    assert code == 1 and out == "" and err.startswith("ошибка: права на ") and '"error"' not in err and err.count("\n") == 1


def test_ошибка_разбора_аргументов_остаётся_за_argparse_даже_с_json(cli):
    for args in (("token", "add", "x", "--level", "admin", "--json"), ("token", "add", "x", "--days", "0", "--json"),
                 ("token", "add", "--json"), ("token", "revoke", "--json")):
        code, out, err = cli(*args)
        assert code == 2 and out == "" and '{"error"' not in err and err.strip()
    assert "--days должен быть положительным" in cli("token", "add", "x", "--days", "0", "--json")[2]
    assert cli.store().list() == []


def test_успех_с_json_stderr_пуст(cli):
    code, out, err = cli("token", "add", "ноутбук", "--json")
    assert code == 0 and err == "" and json.loads(out)["name"] == "ноутбук"


# ── FR-73а: отказы самой команды собираются из каталога (подкоманды без --json: код виден, когда команду зовут из кода) ──
def load_cli():
    """Команда как модуль: её функции зовутся напрямую, с подставными аргументами и без настоящего архива."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("flyarchive_cli", FLYARCHIVE)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("flyarchive_cli", loader))
    loader.exec_module(mod)
    return mod


def args(**kw):
    import types
    return types.SimpleNamespace(**kw)


def refused(call):
    with pytest.raises(A.AuthError) as e:
        call()
    return e.value.message.code, e.value.message.args, str(e.value)


@pytest.fixture
def mod(tmp_path, monkeypatch):
    """Команда как модуль, с домашним каталогом архива во временном месте: настоящий архив не затрагивается."""
    m = load_cli()
    monkeypatch.setattr(m.tokens, "HOME", str(tmp_path / "home"))
    m.home = str(tmp_path / "home")
    return m


def test_переключатель_не_on_и_не_off_с_кодом_и_именем_ключа(mod):
    assert refused(lambda: mod._switch("maybe", "llm")) == ("cli.bad_switch", {"name": "llm"}, "--llm: on или off")
    assert refused(lambda: mod._switch("да", "cloud"))[:2] == ("cli.bad_switch", {"name": "cloud"})
    assert mod._switch("on", "llm") is True and mod._switch("off", "llm") is False


def test_удаление_без_подтверждения_с_кодом(mod):
    assert refused(lambda: mod.doc_delete(None, args(yes=False, json=True, path="входящие/п/а.txt"))) == (
        "cli.confirm_needed", {}, "удаление документа из архива и индекса нужно подтвердить ключом --yes")


def test_имя_оболочки_негодное_с_кодом(mod):
    for name in ("плохое имя!", "", "x" * 41, "с пробелом"):
        assert refused(lambda: mod.run_sandboxed(None, args(command=["--", "true"], name=name, dir=".", env=[], ro=[]))) == (
            "cli.bad_shell_name", {}, "имя оболочки: буквы, цифры, дефис и подчёркивание, до 40 знаков")


class NoBase:
    """База известного, которой нет."""
    def info(self):
        return None


def test_нет_базы_известного_для_повторов_и_дат_с_кодами(mod, monkeypatch):
    monkeypatch.setattr(KN, "Known", lambda *a: NoBase())
    assert refused(lambda: mod.index_dedupe(None, args(apply=False))) == (
        "cli.known_for_dedupe", {}, "нужна база известного: по ней узнаются повторы. Выполни: flyarchive known build")
    assert refused(lambda: mod.index_dates(None, args(apply=False))) == (
        "cli.known_for_dates", {}, "нужна база известного: в ней даты писем. Выполни: flyarchive known build")


def test_разбор_пачки_без_базы_сверки_с_кодом_общим_с_входящими(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(KN, "Known", lambda *a: NoBase())
    a = args(source=str(tmp_path), into=str(tmp_path / "в"), max_gb=None, max_files=None, max_ratio=None, depth=None, jobs=1,
             without_archive=False, llm=False, no_cloud=False, llm_jobs=4)
    assert refused(lambda: mod.check(None, a)) == (
        "known.not_built", {}, "база сверки с архивом не построена — без неё в архив пойдут дубликаты. "
                               "Выполни: flyarchive known build (для нового архива — flyarchive init)")


def test_отказ_приёмки_в_команде_check_сохраняет_код_приёмки(mod, monkeypatch, tmp_path):
    import intake
    import messages
    mine = messages.make("intake.into_source")
    monkeypatch.setattr(intake, "run", lambda *a, **kw: (_ for _ in ()).throw(intake.IntakeError(mine)))
    a = args(source=str(tmp_path), into=str(tmp_path / "в"), max_gb=None, max_files=None, max_ratio=None, depth=None, jobs=1,
             without_archive=True, llm=False, no_cloud=False, llm_jobs=4)
    assert refused(lambda: mod.check(None, a)) == ("intake.into_source", {}, "каталог разбора нельзя класть внутрь источника")


def test_служебный_токен_открытый_чужим_с_кодом_и_путём(mod, tmp_path):
    guard = A.Guard("cli", home=str(tmp_path / "home"))
    os.makedirs(guard.secrets, mode=0o700)
    path = os.path.join(guard.secrets, "local.token")
    with open(path, "w", encoding="utf-8") as f:
        f.write("ba_чужой\n")
    os.chmod(path, 0o644)
    assert refused(lambda: mod.token_init_local(guard, None)) == (
        "cli.local_token_open", {"path": path}, f"права на {path} шире 600 — служебный токен открыт чужим")


def kick(mod, monkeypatch, result):
    import subprocess

    def run(*a, **kw):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(subprocess, "run", run)
    return refused(lambda: mod.inbox_kick(None, args(json=True)))


def test_запуск_разбора_systemd_недоступен_с_кодом_и_видом_сбоя(mod, monkeypatch):
    assert kick(mod, monkeypatch, FileNotFoundError("systemctl")) == (
        "cli.kick_no_systemd", {"error_type": "FileNotFoundError"}, "разбор не запущен: systemd недоступен (FileNotFoundError)")


def test_запуск_разбора_systemctl_отказал_с_кодом_и_словами_systemctl(mod, monkeypatch):
    import subprocess
    said = subprocess.CompletedProcess([], 1, "", "Failed to connect to bus\n")
    assert kick(mod, monkeypatch, said) == ("cli.kick_failed", {"why": "Failed to connect to bus"}, "разбор не запущен: Failed to connect to bus")
    long = subprocess.CompletedProcess([], 1, "", "x" * 300)
    assert kick(mod, monkeypatch, long) == ("cli.kick_failed", {"why": "x" * 200}, "разбор не запущен: " + "x" * 200)


def test_запуск_разбора_systemctl_молчит_с_отдельным_кодом(mod, monkeypatch):
    import subprocess
    assert kick(mod, monkeypatch, subprocess.CompletedProcess([], 1, "", "")) == (
        "cli.kick_failed_silent", {}, "разбор не запущен: systemctl вернул ошибку")


def test_отказ_входящих_в_команде_сохраняет_код_входящих(mod):
    ns = args(path=None, period=7, llm=None, cloud=None, threshold=None, max_gb=None, max_files=None, max_ratio=None, depth=None)
    assert refused(lambda: mod.inbox_set(None, ns)) == ("inbox.bad_period", {"periods": "1, 5, 10, 30, 60"},
                                                        "период разбора: 1, 5, 10, 30, 60 минут")


def test_отказ_прохода_в_команде_run_сохраняет_код_входящих(mod, monkeypatch):
    import inbox
    import messages
    os.makedirs(mod.home)
    with open(os.path.join(mod.home, "inbox.json"), "w", encoding="utf-8") as f:
        json.dump({"llm": False}, f)
    mine = messages.make("inbox.folder_missing", path="/нет")
    monkeypatch.setattr(inbox, "process", lambda *a, **kw: (_ for _ in ()).throw(inbox.InboxError(mine)))
    assert refused(lambda: mod.inbox_run(None, args(wait=False))) == ("inbox.folder_missing", {"path": "/нет"}, "входящей папки нет: /нет")


def test_отказ_решения_в_команде_сохраняет_код_решений(mod):
    assert refused(lambda: mod._review(lambda r: r.queue_reject, "очередь/x")) == (
        "review.bad_path", {"area": "очередь"}, "путь должен быть вида очередь/<пачка>/<имя>")


def test_отказ_истории_в_команде_сохраняет_код_истории(mod):
    assert refused(lambda: mod.inbox_batch(None, args(id="вчера", json=True)))[:2] == ("batch.bad_id", {"value": "'вчера'"})
    assert refused(lambda: mod.inbox_batches(None, args(limit="0", before=None, json=True))) == (
        "batch.bad_limit", {"max": 200}, "--limit: целое число от 1 до 200")


def test_отказ_чужого_модуля_команды_получает_общий_код_с_текстом_как_есть(mod, monkeypatch):
    import connect
    import perms
    monkeypatch.setattr(connect, "guide", lambda *a: (_ for _ in ()).throw(connect.ConnectError("оболочка не из списка")))
    assert refused(lambda: mod.connect_harness(None, args(harness="x", remote=False, env="FLYARCHIVE_TOKEN"))) == (
        "generic.text", {"text": "оболочка не из списка"}, "оболочка не из списка")
    monkeypatch.setattr(perms, "scan", lambda *a, **kw: (_ for _ in ()).throw(perms.PermsError("нет такого каталога")))
    assert refused(lambda: mod.perms_check(None, args(deep=False)))[:2] == ("generic.text", {"text": "нет такого каталога"})


# ── FR-73б: любой отказ с кодом при --json — строка JSON в stderr; не трассировка ──
@pytest.fixture
def jmain(mod, monkeypatch, capsys):
    """main() команды в этом же процессе: настоящий архив не трогается (охранник подставной), команда — подставная и отказывает."""
    monkeypatch.setattr(mod.auth, "Guard", lambda *a, **kw: None)

    def go(command, error, *argv):
        def boom(guard, a):
            raise error

        monkeypatch.setattr(mod, command, boom)
        code = mod.main(list(argv))
        out, err = capsys.readouterr()
        return code, out, err

    return go


def coded_errors():
    import auth
    import batches
    import inbox
    import intake
    import messages
    import review
    import tokens
    import unpack
    return [
        (KN.KnownError, messages.make("known.db_missing", path="/x/known.sqlite")),
        (inbox.InboxError, messages.make("inbox.folder_missing", path="/нет")),
        (inbox.Busy, messages.make("inbox.busy")),
        (review.ReviewError, messages.make("review.no_file", path="очередь/п/а.txt")),
        (batches.BatchError, messages.make("batch.no_batch", batch="20261004-120000")),
        (intake.IntakeError, messages.make("intake.into_source")),
        (unpack.UnpackError, messages.make("unpack.encrypted")),
        (tokens.TokenError, messages.make("token.bad_name")),
        (auth.AuthError, messages.make("auth.link_key_broken", path="/h/link.key")),
        (messages.CodedError, messages.make("generic.text", text="слова как есть")),
    ]


@pytest.mark.parametrize("index", range(10))
def test_любой_отказ_с_кодом_при_json_одна_строка_json_и_код_возврата_один(jmain, index):
    cls, message = coded_errors()[index]
    error = cls("encrypted", message) if cls.__name__ == "UnpackError" else cls(message)
    code, out, err = jmain("queue_list", error, "queue", "list", "--json")
    assert code == 1 and out == "" and err.endswith("\n") and len(err.splitlines()) == 1
    assert json.loads(err) == {"error": message.to_json()} and list(json.loads(err)["error"]) == ["code", "args", "text"]


@pytest.mark.parametrize("index", range(10))
def test_любой_отказ_с_кодом_без_json_как_раньше_ошибка_и_текст(jmain, index):
    cls, message = coded_errors()[index]
    error = cls("encrypted", message) if cls.__name__ == "UnpackError" else cls(message)
    code, out, err = jmain("queue_list", error, "queue", "list")
    assert code == 1 and out == "" and err == f"ошибка: {message}\n" and '"error"' not in err


@pytest.mark.parametrize("command, argv", [("inbox_status", ("inbox", "status", "--json")), ("inbox_batches", ("inbox", "batches", "--json")),
                                           ("inbox_batch", ("inbox", "batch", "20261004-120000", "--json")), ("inbox_kick", ("inbox", "kick", "--json")),
                                           ("quarantine_list", ("quarantine", "list", "--json")), ("queue_accept", ("queue", "accept", "--json", "п")),
                                           ("doc_delete", ("doc", "delete", "--json", "--yes", "п")), ("journal_tail", ("journal", "--json")),
                                           ("token_list", ("token", "list", "--json"))])
def test_отказ_знания_и_входящих_при_json_в_каждой_подкоманде_с_ключом_json(jmain, command, argv):
    import inbox
    import messages
    mine = messages.make("inbox.folder_missing", path="/нет")
    code, out, err = jmain(command, inbox.InboxError(mine), *argv)
    assert code == 1 and out == ""
    assert json.loads(err) == {"error": {"code": "inbox.folder_missing", "args": {"path": "/нет"}, "text": "входящей папки нет: /нет"}}


def test_отказ_без_сообщения_из_каталога_при_json_получает_общий_код(jmain):
    import messages
    code, out, err = jmain("queue_list", messages.CodedError("голые слова"), "queue", "list", "--json")
    assert code == 1 and json.loads(err) == {"error": {"code": "generic.text", "args": {"text": "голые слова"}, "text": "голые слова"}}


def test_чужое_исключение_с_json_не_прячется_за_строкой_json(jmain):
    with pytest.raises(RuntimeError):
        jmain("queue_list", RuntimeError("не наше"), "queue", "list", "--json")


def test_успех_с_json_stderr_пуст_и_код_возврата_нуль(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod.auth, "Guard", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "queue_list", lambda guard, a: mod.emit([]))
    assert mod.main(["queue", "list", "--json"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == [] and err == ""


# ── докстрока команды называет каждую подкоманду (FR-113) ───────
def parser_paths(mod, monkeypatch):
    """Пути всех подкоманд из разбора аргументов: ('doctor',), ('token', 'add'), …; у команды с действиями — пути её действий."""
    import argparse
    seen = []

    def stop(self, *args, **kw):
        seen.append(self)
        raise RuntimeError("разбор остановлен: нужен только сам разборщик")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", stop)
    with pytest.raises(RuntimeError):
        mod.main([])

    def walk(parser, prefix=()):
        groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        if not groups:
            return [prefix]
        return [path for name, child in groups[0].choices.items() for path in walk(child, prefix + (name,))]
    return sorted(walk(seen[0]))


def doc_paths(doc, parser_leaves):
    """Что докстрока называет подкомандами: из строк «    flyarchive команда [действие] …»; действие берётся, только когда у команды они есть."""
    grouped = {path[0] for path in parser_leaves if len(path) > 1}
    found = []
    for line in re.findall(r"^    flyarchive ([^\n]+)$", doc, re.M):
        words = line.split()
        found.append((words[0], words[1]) if words[0] in grouped and len(words) > 1 else (words[0],))
    return sorted(found)


def test_докстрока_команды_называет_каждую_подкоманду_из_разбора_аргументов_и_только_их(mod, monkeypatch):
    leaves = parser_paths(mod, monkeypatch)
    assert len(leaves) > 30, f"разбор аргументов прочитан не весь: {leaves}"
    named = doc_paths(mod.__doc__, leaves)
    missing = [" ".join(p) for p in leaves if p not in named]
    unknown = [" ".join(p) for p in named if p not in leaves]
    assert missing == [], f"в докстроке команды нет подкоманд: {missing}"
    assert unknown == [], f"в докстроке названы подкоманды, которых в разборе нет: {unknown}"
    assert len(named) == len(set(named)), "в докстроке одна подкоманда названа дважды"


def test_проверка_докстроки_находит_лишнюю_и_недостающую_подкоманду():
    leaves = [("doctor",), ("token", "add"), ("token", "list"), ("open",)]
    ok = "    flyarchive doctor [--json]   проверить\n    flyarchive token add <имя>   выпустить\n    flyarchive token list   список\n    flyarchive open   ссылка\n"
    assert doc_paths(ok, leaves) == sorted(leaves)
    assert doc_paths(ok.replace("    flyarchive token list   список\n", ""), leaves) == [("doctor",), ("open",), ("token", "add")]
    assert ("ghost",) in doc_paths(ok + "    flyarchive ghost   выдумка\n", leaves)
    assert ("token", "ghost") in doc_paths(ok + "    flyarchive token ghost   выдумка\n", leaves)


def test_докстрока_команды_названа_по_тому_чем_команда_стала(mod):
    first = mod.__doc__.strip().splitlines()[0]
    assert first.startswith("Команда архива"), first
    assert "доступом" not in first

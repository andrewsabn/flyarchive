"""Журнал: отказ отозванному и просроченному токену пишется под именем клиента (FR-93).

Ответ клиенту при этом не меняется: состояние токена наружу не раскрывается.
Службы поднимаются на случайных портах, поиск и модель подставные.
"""
import json
import os
import re
import subprocess
import sys
import time

import pytest

import mcp_server as M
import office_server as O
import tokens as T
import webui as W

FLYARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "flyarchive")
DENIED = "токен неверен, отозван или просрочен"
SERVICES = ("search", "office", "mcp")
STRANGER = "ba_" + "x" * 43
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


@pytest.fixture
def net(serve, fetch, access, monkeypatch, tmp_path):
    monkeypatch.setattr(O, "CORPUS", str(tmp_path / "corpus"))       # настоящий корпус не затрагивается ни при каком исходе
    monkeypatch.setattr(O, "OUT", str(tmp_path / "out"))
    monkeypatch.setattr(W, "CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setattr(W.S, "search", lambda *a, **k: [])
    monkeypatch.setattr(W.Handler, "guard", access.guard("search"))
    monkeypatch.setattr(O.Handler, "guard", access.guard("office"))
    monkeypatch.setattr(M.Handler, "guard", access.guard("mcp"))
    base = {"search": serve(W.Server, W.Handler), "office": serve(O.Server, O.Handler), "mcp": serve(M.Server, M.Handler)}

    class Net:
        pass

    n = Net()
    n.access, n.base = access, base

    def ask(service, token=None, headers=None, tool="tools/list"):
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = "Bearer " + token
        if service == "search":
            return fetch(base[service] + "/api/search?q=x", headers=h)
        if service == "office":
            return fetch(base[service] + "/read?path=x", headers=h)
        msg = {"jsonrpc": "2.0", "id": 1, "method": tool}
        if tool == "tools/call":
            msg["params"] = {"name": "search_archive", "arguments": {"q": "x"}}
        return fetch(base[service] + "/mcp", data=msg, headers=h)

    def last(server):
        return [r for r in access.journal() if r["server"] == server][-1]

    n.ask, n.last = ask, last
    return n


def issue_in_past(access, name, level="read", days=1, use=False):
    """Токен, срок которого уже вышел: выпущен в то же хранилище часами трёхдневной давности."""
    past = T.Store(access.store.path, clock=lambda: time.time() - 3 * 86400)
    token = past.issue(name, level, days)
    if use:
        past.verify(token)
    return token


def row(access, name):
    return [r for r in access.store.list() if r["name"] == name][-1]


def reasons(net):
    """Четыре негодных токена: отозван, просрочен, оба сразу и незнакомое значение."""
    a = net.access
    a.store.revoke("писатель")
    both = issue_in_past(a, "и-то-и-другое")
    a.store.revoke("и-то-и-другое")
    return {"revoked": a.full, "expired": issue_in_past(a, "гость"), "both": both, "stranger": STRANGER}


# ── запись под именем и уровнем ─────────────────────────────────
@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("level, name", [("read", "читатель"), ("full", "писатель"), ("local", "dsh-local")])
def test_отказ_отозванному_токену_записан_под_его_именем_и_уровнем(net, service, level, name):
    net.access.store.revoke(name)
    status, _, _ = net.ask(service, getattr(net.access, level))
    rec = net.last(service)
    assert status == 401
    assert (rec["server"], rec["client"], rec["level"], rec["status"]) == (service, name, level, 401)


@pytest.mark.parametrize("service", SERVICES)
def test_отказ_просроченному_токену_записан_под_его_именем_и_уровнем(net, service):
    token = issue_in_past(net.access, "гость", "full")
    net.ask(service, token)
    rec = net.last(service)
    assert (rec["client"], rec["level"], rec["status"]) == ("гость", "full", 401)


def test_исход_отказа_называет_причину_и_время(net):
    a = net.access
    a.store.revoke("писатель")
    expired = issue_in_past(a, "гость")
    both = issue_in_past(a, "и-то-и-другое")
    a.store.revoke("и-то-и-другое")
    want = {a.full: "отказ: токен отозван " + row(a, "писатель")["revoked"],
            expired: "отказ: токен просрочен " + row(a, "гость")["expires"],
            both: "отказ: токен отозван " + row(a, "и-то-и-другое")["revoked"]}
    for token, outcome in want.items():
        net.ask("search", token)
        assert net.last("search")["outcome"] == outcome
        assert STAMP.search(outcome)


def test_отозван_и_просрочен_одновременно_в_журнале_отозван(net):
    a = net.access
    both = issue_in_past(a, "и-то-и-другое")
    a.store.revoke("и-то-и-другое")
    net.ask("search", both)
    outcome = net.last("search")["outcome"]
    assert outcome.startswith("отказ: токен отозван ") and "просрочен" not in outcome
    assert outcome.endswith(row(a, "и-то-и-другое")["revoked"]) and not outcome.endswith(row(a, "и-то-и-другое")["expires"])


def test_незнакомое_значение_пишется_как_раньше_клиентом_без_имени(net):
    net.access.store.revoke("писатель")
    for service in SERVICES:
        for token in (STRANGER, "not-a-token", "ba_", None):
            net.ask(service, token)
            rec = net.last(service)
            assert (rec["client"], rec["level"], rec["status"]) == ("-", None, 401)
            assert rec["outcome"] == ("отказ: нужен токен: заголовок Authorization: Bearer <токен>" if token is None
                                      else "отказ: " + DENIED)


def test_имя_выдано_заново_старое_значение_отказ_с_именем_новое_обычная_запись(net):
    a = net.access
    old = a.read
    a.store.revoke("читатель")
    new = a.store.issue("читатель", "full")
    assert net.ask("search", old)[0] == 401
    refused = net.last("search")
    assert (refused["client"], refused["level"], refused["status"]) == ("читатель", "read", 401)
    assert refused["outcome"] == "отказ: токен отозван " + [r for r in a.store.list() if r["name"] == "читатель"][0]["revoked"]
    assert net.ask("search", new)[0] == 200
    fresh = net.last("search")
    assert (fresh["client"], fresh["level"], fresh["status"], fresh["outcome"]) == ("читатель", "full", 200, "ok")


@pytest.mark.parametrize("service", SERVICES)
def test_отказ_отозванному_одинаково_пишется_каждой_службой_через_общий_вход(net, service):
    a = net.access
    a.store.revoke("писатель")
    net.ask(service, a.full)
    rec = net.last(service)
    assert (rec["client"], rec["level"], rec["status"]) == ("писатель", "full", 401)
    assert rec["outcome"] == "отказ: токен отозван " + row(a, "писатель")["revoked"]
    assert rec["params"] == ({"q": "x"} if service == "search" else {"path": "x"} if service == "office" else {})


def test_все_три_службы_пишут_отказ_одинаково(net):
    net.access.store.revoke("писатель")
    seen = {}
    for service in SERVICES:
        net.ask(service, net.access.full)
        rec = net.last(service)
        seen[service] = (rec["client"], rec["level"], rec["status"], rec["outcome"])
    assert len(set(seen.values())) == 1, seen


def test_у_переходника_mcp_отказ_пишется_и_для_списка_и_для_вызова_инструмента(net):
    net.access.store.revoke("писатель")
    for method in ("tools/list", "tools/call"):
        assert net.ask("mcp", net.access.full, tool=method)[0] == 401
    recs = [r for r in net.access.journal() if r["server"] == "mcp"]
    assert [(r["client"], r["level"], r["tool"], r["status"]) for r in recs] == [("писатель", "full", "mcp", 401)] * 2


def test_через_шлюз_tailnet_отказ_отозванному_пишется_под_именем_с_узлом_и_пометкой(net):
    M.Handler.guard.remote_full = True                      # так переходник MCP работает в службе: токен действует и с других машин
    net.access.store.revoke("писатель")
    status, _, _ = net.ask("mcp", net.access.full, headers={"X-Flyarchive-Remote": "203.0.113.9", "X-Forwarded-For": "203.0.113.9"})
    rec = net.last("mcp")
    assert status == 401
    assert (rec["client"], rec["level"], rec["via"], rec["ip"]) == ("писатель", "full", "tailnet", "203.0.113.9")
    assert rec["outcome"].startswith("отказ: токен отозван ")


# ── клиент не узнаёт состояния токена ───────────────────────────
@pytest.mark.parametrize("service", SERVICES)
def test_ответ_клиенту_один_и_тот_же_для_отозванного_просроченного_и_незнакомого(net, service):
    answers = {}
    for label, token in reasons(net).items():
        status, headers, body = net.ask(service, token)
        answers[label] = (status, {k: v for k, v in headers.items() if k.lower() != "date"}, body)
    for label, answer in answers.items():
        assert answer == answers["stranger"], label
    status, headers, body = answers["stranger"]
    assert status == 401 and headers["WWW-Authenticate"] == "Bearer"
    assert body == json.dumps({"error": DENIED}, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("service", SERVICES)
def test_в_ответе_нет_ни_имени_ни_уровня_ни_времени(net, service):
    for token in reasons(net).values():
        status, headers, body = net.ask(service, token)
        text = body.decode("utf-8") + repr(headers)
        assert status == 401 and not STAMP.search(text)
        for name in ("писатель", "гость", "и-то-и-другое", "full", "read"):
            assert name not in text


# ── значение токена и хеш в журнал не попадают ──────────────────
def test_значение_токена_и_его_хеш_не_попадают_в_журнал_ни_в_одном_поле(net):
    tokens = reasons(net)
    for service in SERVICES:
        for token in tokens.values():
            net.ask(service, token)
    text = net.access.journal_text()
    assert len(net.access.journal()) == 3 * len(tokens)
    for token in tokens.values():
        assert token not in text and token[3:] not in text and T.digest(token) not in text
    assert "ba_" not in text.replace("ba_***", "")


# ── отказ не пишет в хранилище ──────────────────────────────────
def test_отказ_не_меняет_файл_хранилища_и_последнее_обращение(net):
    a = net.access
    issue_in_past(a, "бывалый", use=True)                   # был в работе, потом срок вышел
    tokens = reasons(net)
    path = a.store.path

    def disk():
        st = os.stat(path)
        with open(path, "rb") as f:
            return f.read(), st.st_mtime_ns, st.st_ino, sorted(os.listdir(os.path.dirname(path)))

    before, rows = disk(), a.store.list()
    for service in SERVICES:
        for token in tokens.values():
            assert net.ask(service, token)[0] == 401
    assert disk() == before and a.store.list() == rows
    assert [r["last_used"] for r in a.store.list() if r["name"] in ("писатель", "гость", "и-то-и-другое")] == [None] * 3


def test_годный_токен_по_прежнему_отмечает_последнее_обращение(net):
    assert row(net.access, "читатель")["last_used"] is None
    assert net.ask("search", net.access.read)[0] == 200
    assert row(net.access, "читатель")["last_used"] is not None


# ── имя берётся из хранилища, а не от клиента ───────────────────
@pytest.mark.parametrize("service", SERVICES)
def test_имя_в_журнале_то_что_в_хранилище_а_заголовки_клиента_на_него_не_влияют(net, service):
    net.access.store.revoke("писатель")
    liar = {"X-Flyarchive-Client": "intruder", "X-Flyarchive-Name": "intruder", "X-Client": "intruder", "X-Client-Name": "intruder",
            "X-Token-Name": "intruder", "X-Flyarchive-Level": "local", "X-Level": "local", "User-Agent": "intruder", "From": "intruder",
            "Referer": "http://intruder.example/", "Client": "intruder", "Name": "intruder"}
    net.ask(service, net.access.full, headers=liar)
    rec = net.last(service)
    assert (rec["client"], rec["level"]) == ("писатель", "full")
    assert "intruder" not in net.access.journal_text()


@pytest.mark.parametrize("service", SERVICES)
def test_чужие_заголовки_не_дают_незнакомому_значению_имя(net, service):
    net.ask(service, STRANGER, headers={"X-Flyarchive-Client": "dsh-local", "X-Client-Name": "dsh-local"})
    rec = net.last(service)
    assert (rec["client"], rec["level"]) == ("-", None)


# ── отбор журнала по клиенту ────────────────────────────────────
def test_отбор_по_клиенту_находит_отказ_и_не_относит_его_к_безымянным(net):
    a = net.access
    a.store.revoke("читатель")
    net.ask("search", a.read)
    net.ask("search", STRANGER)
    journal = a.guard("тест").journal
    found = journal.tail(10, client="читатель")
    assert [(r["status"], r["outcome"].split(" ")[:3]) for r in found] == [(401, ["отказ:", "токен", "отозван"])]
    assert [r["outcome"] for r in journal.tail(10, client="-")] == ["отказ: " + DENIED]


def run_cli(home, *args):
    env = {**os.environ, "FLYARCHIVE_HOME": home, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, FLYARCHIVE, *args], env=env, capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout, r.stderr


def test_команда_journal_с_отбором_по_клиенту_печатает_отказ_отозванному(net):
    a = net.access
    a.store.revoke("читатель")
    a.store.revoke("писатель")
    net.ask("search", a.read)
    net.ask("mcp", a.full)                                  # отказ другому клиенту в отбор не входит
    code, out, _ = run_cli(a.home, "journal", "--client", "читатель")
    assert code == 0 and "читатель" in out and "401" in out and "отказ: токен отозван " in out
    assert "писатель" not in out
    code, out, _ = run_cli(a.home, "journal", "--json", "--client", "читатель")
    (rec,) = json.loads(out)
    assert code == 0 and (rec["client"], rec["level"], rec["status"]) == ("читатель", "read", 401)
    assert rec["outcome"] == "отказ: токен отозван " + row(a, "читатель")["revoked"]
    assert a.read not in out


# ── остальные отказы не изменились ──────────────────────────────
def test_прочие_отказы_пишутся_прежним_текстом_и_без_имени(net):
    a = net.access
    net.ask("search")
    net.ask("search", a.read, headers={"Host": "evil.example"})
    recs = [r for r in a.journal() if r["status"] >= 400]
    assert [(r["client"], r["outcome"]) for r in recs] == [
        ("-", "отказ: нужен токен: заголовок Authorization: Bearer <токен>"),
        ("-", "отказ: чужой Host: служба отвечает только на адреса этой машины")]


def test_нехватка_уровня_у_живого_токена_пишется_прежним_текстом_под_его_именем(net, fetch):
    a = net.access
    status, _, _ = fetch(net.base["office"] + "/chart", data={"kind": "bar"}, headers=a.bearer(a.read))     # создание файла — уровень «полный»
    rec = net.last("office")
    assert status == 403 and (rec["client"], rec["level"]) == ("читатель", "read")
    assert rec["outcome"] == "отказ: нужен уровень «full», у токена уровень «read»"


def test_отказ_с_другой_машины_не_меняется_отозванный_токен_там_не_опознаётся(net):
    a = net.access
    a.store.revoke("читатель")
    status, _, body = net.ask("search", a.read, headers={"X-Flyarchive-Remote": "203.0.113.9"})
    rec = net.last("search")
    assert status == 403 and "только подписанные ссылки" in json.loads(body)["error"]
    assert (rec["client"], rec["level"]) == ("-", None)
    assert rec["outcome"] == "отказ: с других машин открываются только подписанные ссылки из ответов архива"


def test_испорченное_хранилище_по_прежнему_503_и_без_имени(net):
    a = net.access
    os.chmod(a.store.path, 0o644)
    status, _, body = net.ask("search", a.read)
    rec = net.last("search")
    assert status == 503 and json.loads(body)["error"].startswith("вход закрыт: ")
    assert (rec["client"], rec["status"]) == ("-", 503)

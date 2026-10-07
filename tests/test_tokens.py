"""Хранилище токенов: FR-02, FR-03, FR-05."""
import hashlib
import os
import stat

import pytest

import tokens as T


@pytest.fixture
def store(tmp_path):
    clock = {"now": 1_800_000_000.0}
    s = T.Store(str(tmp_path / "secrets" / "tokens.json"), clock=lambda: clock["now"])
    s.tick = lambda sec: clock.__setitem__("now", clock["now"] + sec)
    s.clock_ref = clock
    return s


def other_process(store):
    """Второй экземпляр на том же файле — как другая служба или команда flyarchive."""
    return T.Store(store.path, clock=lambda: store.clock_ref["now"])


# ── выпуск и проверка ───────────────────────────────────────────
def test_выпущенный_токен_опознаёт_клиента_и_уровень(store):
    tok = store.issue("ноутбук", "read")
    assert tok.startswith("ba_") and len(tok) >= 40
    assert store.verify(tok) == T.Client("ноутбук", "read")


def test_два_выпуска_дают_разные_токены(store):
    assert store.issue("a", "read") != store.issue("b", "read")


@pytest.mark.parametrize("level, read, full, local", [
    ("read", True, False, False), ("full", True, True, False), ("local", True, True, True)])
def test_уровень_даёт_права_не_выше_своего(level, read, full, local):
    c = T.Client("x", level)
    assert (c.allows("read"), c.allows("full"), c.allows("local")) == (read, full, local)


# ── в хранилище только хеш ──────────────────────────────────────
def test_в_хранилище_нет_значения_токена(store):
    tok = store.issue("ноутбук", "full")
    raw = open(store.path, encoding="utf-8").read()
    assert tok not in raw and tok[3:] not in raw
    assert hashlib.sha256(tok.encode()).hexdigest() in raw


def test_файл_и_каталог_закрыты_от_чужих(store):
    store.issue("ноутбук", "read")
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(store.path)).st_mode) == 0o700


def test_список_не_содержит_ни_токена_ни_хеша(store):
    tok = store.issue("ноутбук", "read", days=30)
    (row,) = store.list()
    assert set(row) == {"name", "level", "created", "expires", "last_used", "revoked"}
    assert tok not in str(row) and hashlib.sha256(tok.encode()).hexdigest() not in str(row)
    assert (row["name"], row["level"], row["revoked"]) == ("ноутбук", "read", None)


# ── чужое и испорченное отклоняется ─────────────────────────────
@pytest.mark.parametrize("spoil", [
    lambda t: "", lambda t: None, lambda t: t[:-1], lambda t: t + " ", lambda t: " " + t,
    lambda t: t.upper(), lambda t: t[3:], lambda t: "ba_" + "x" * 43, lambda t: t + t, lambda t: 12345])
def test_неверный_токен_отклонён(store, spoil):
    tok = store.issue("ноутбук", "read")
    assert store.verify(spoil(tok)) is None


def test_пустое_хранилище_никого_не_пускает(store):
    assert store.verify("ba_" + "x" * 43) is None
    assert store.list() == []


# ── срок ────────────────────────────────────────────────────────
def test_просроченный_токен_отклонён(store):
    tok = store.issue("гость", "read", days=1)
    store.tick(86399)
    assert store.verify(tok) is not None
    store.tick(2)
    assert store.verify(tok) is None


def test_токен_без_срока_действует_годами(store):
    tok = store.issue("ноутбук", "read")
    store.tick(10 * 365 * 86400)
    assert store.verify(tok) == T.Client("ноутбук", "read")


# ── имя занято ──────────────────────────────────────────────────
def test_занятое_имя_отказ_прежний_токен_цел(store):
    tok = store.issue("ноутбук", "read")
    with pytest.raises(T.TokenError):
        store.issue("ноутбук", "full")
    assert store.verify(tok) == T.Client("ноутбук", "read")
    assert len(store.list()) == 1


def test_имя_отозванного_можно_выпустить_заново_старый_токен_мёртв(store):
    old = store.issue("ноутбук", "read")
    store.revoke("ноутбук")
    new = store.issue("ноутбук", "full")
    assert store.verify(old) is None
    assert store.verify(new) == T.Client("ноутбук", "full")


@pytest.mark.parametrize("name", ["", "a b", "a/b", "x" * 65, None, "имя\nс переводом"])
def test_плохое_имя_отказ(store, name):
    with pytest.raises(T.TokenError):
        store.issue(name, "read")


@pytest.mark.parametrize("level", ["admin", "", None, "READ"])
def test_неизвестный_уровень_отказ(store, level):
    with pytest.raises(T.TokenError):
        store.issue("ноутбук", level)


# ── отзыв действует сразу ───────────────────────────────────────
def test_отзыв_из_другого_процесса_действует_без_перезапуска(store):
    tok = store.issue("ноутбук", "read")
    assert store.verify(tok) is not None
    other_process(store).revoke("ноутбук")
    assert store.verify(tok) is None
    (row,) = store.list()
    assert row["revoked"] is not None


def test_выпуск_из_другого_процесса_виден_сразу(store):
    assert store.list() == []
    tok = other_process(store).issue("планшет", "full")
    assert store.verify(tok) == T.Client("планшет", "full")


def test_отзыв_несуществующего_и_уже_отозванного_отказ(store):
    with pytest.raises(T.TokenError):
        store.revoke("нет такого")
    store.issue("ноутбук", "read")
    store.revoke("ноутбук")
    with pytest.raises(T.TokenError):
        store.revoke("ноутбук")


# ── права на файл ───────────────────────────────────────────────
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_хранилище_открытое_чужим_не_используется(store, mode):
    tok = store.issue("ноутбук", "read")
    os.chmod(store.path, mode)
    for action in (lambda: store.verify(tok), lambda: store.issue("b", "read"), store.list):
        with pytest.raises(T.TokenError) as e:
            action()
        assert "600" in str(e.value)


def test_битое_хранилище_не_считается_пустым(store):
    store.issue("ноутбук", "read")
    with open(store.path, "w", encoding="utf-8") as f:
        f.write("{не json")
    with pytest.raises(T.TokenError):
        store.verify("ba_" + "x" * 43)


# ── время последнего вызова ─────────────────────────────────────
def test_время_последнего_вызова_обновляется(store):
    tok = store.issue("ноутбук", "read")
    assert store.list()[0]["last_used"] is None
    store.tick(5)
    store.verify(tok)
    first = store.list()[0]["last_used"]
    assert first is not None
    store.tick(120)
    store.verify(tok)
    assert store.list()[0]["last_used"] > first


def test_частые_вызовы_не_переписывают_файл_каждый_раз(store):
    tok = store.issue("ноутбук", "read")
    store.verify(tok)
    before = os.stat(store.path).st_mtime_ns
    for _ in range(20):
        store.tick(1)
        assert store.verify(tok) is not None
    assert os.stat(store.path).st_mtime_ns == before


def test_проверка_без_отметки_узнаёт_клиента_и_ничего_не_пишет(store):
    """Так `flyarchive init` проверяет готовый служебный токен: проверка — не обращение клиента, «последний вызов» и файл остаются как были."""
    tok = store.issue("ноутбук", "read")
    before = (os.stat(store.path).st_mtime_ns, open(store.path, "rb").read())
    store.tick(500)
    assert store.verify(tok, touch=False) == T.Client("ноутбук", "read")
    assert store.verify("ba_" + "x" * 43, touch=False) is None
    assert (os.stat(store.path).st_mtime_ns, open(store.path, "rb").read()) == before and store.list()[0]["last_used"] is None


def test_отказ_не_меняет_время_последнего_вызова(store):
    store.issue("ноутбук", "read")
    store.verify("ba_" + "x" * 43)
    assert store.list()[0]["last_used"] is None


# ── FR-73а: отказы хранилища несут код и параметры, русский текст прежний ──
def refusal(call):
    with pytest.raises(T.TokenError) as e:
        call()
    return e.value.message.code, e.value.message.args, str(e.value)


def test_плохое_имя_и_уровень_с_кодом(store):
    assert refusal(lambda: store.issue("a b", "read")) == ("token.bad_name", {}, "имя клиента: от 1 до 64 букв, цифр и знаков . _ -")
    assert refusal(lambda: store.issue("ноутбук", "admin")) == (
        "token.bad_level", {"levels": "read, full, local"}, "уровень должен быть одним из: read, full, local")


def test_занятое_имя_и_отзыв_без_действующего_токена_с_кодом(store):
    store.issue("ноутбук", "read")
    assert refusal(lambda: store.issue("ноутбук", "full")) == (
        "token.name_taken", {"name": "ноутбук"}, "имя «ноутбук» занято действующим токеном — отзови его или выбери другое")
    assert refusal(lambda: store.revoke("планшет")) == (
        "token.no_active", {"name": "планшет"}, "действующего токена с именем «планшет» нет")
    store.revoke("ноутбук")
    assert refusal(lambda: store.revoke("ноутбук"))[:2] == ("token.no_active", {"name": "ноутбук"})


def test_хранилище_открытое_чужим_с_кодом_и_путём(store):
    tok = store.issue("ноутбук", "read")
    os.chmod(store.path, 0o644)
    want = ("token.store_open", {"path": store.path}, f"права на {store.path} шире 600 — хранилище токенов открыто чужим, не использую")
    for action in (lambda: store.verify(tok), lambda: store.issue("b", "read"), store.list):
        assert refusal(action) == want


def test_битое_хранилище_с_кодом_путём_и_словами_исключения(store):
    store.issue("ноутбук", "read")
    for body, error in (("{не json", None), ('{"других": []}', "'tokens'")):
        with open(store.path, "w", encoding="utf-8") as f:
            f.write(body)
        if error is None:
            try:
                import json
                json.loads(body)
            except ValueError as e:
                error = str(e)
        assert refusal(store.list) == ("token.store_broken", {"path": store.path, "error": error},
                                       f"хранилище токенов {store.path} испорчено: {error}")


def test_отказ_токена_это_CodedError_и_сообщение_собрано_из_каталога(store):
    import messages
    with pytest.raises(messages.CodedError) as e:
        store.issue("a b", "read")
    assert isinstance(e.value.message, messages.Message) and e.value.message.to_json()["code"] == "token.bad_name"


# ── FR-93: почему отказано известному токену ────────────────────
def row_of(store, name):
    return [r for r in store.list() if r["name"] == name][-1]


def snapshot(store):
    """Всё, что на диске около хранилища: содержимое, время изменения, номер файла, соседние файлы."""
    st = os.stat(store.path)
    with open(store.path, "rb") as f:
        raw = f.read()
    return raw, st.st_mtime_ns, st.st_ino, sorted(os.listdir(os.path.dirname(store.path)))


def test_отозванному_токену_отказ_называет_имя_уровень_причину_и_время(store):
    tok = store.issue("ноутбук", "full")
    store.tick(10)
    store.revoke("ноутбук")
    why = store.refusal(tok)
    assert why == T.Refusal("ноутбук", "full", "revoked", row_of(store, "ноутбук")["revoked"])
    assert (why.name, why.level, why.reason, why.when) == ("ноутбук", "full", "revoked", T.stamp(store.clock()))
    assert store.verify(tok) is None                       # договор verify прежний: клиент либо None


def test_просроченному_токену_отказ_называет_срок_а_не_время_отзыва(store):
    tok = store.issue("гость", "read", days=1)
    store.tick(86399)
    assert store.refusal(tok) is None                      # ещё действует: отказа нет
    store.tick(1)                                          # ровно срок: verify уже отказывает
    assert store.verify(tok) is None
    why = store.refusal(tok)
    assert why == T.Refusal("гость", "read", "expired", row_of(store, "гость")["expires"])
    assert why.when == T.stamp(store.clock())


def test_отозван_и_просрочен_одновременно_причина_отозван(store):
    tok = store.issue("гость", "read", days=1)
    store.tick(86400 + 100)
    store.revoke("гость")
    why = store.refusal(tok)
    row = row_of(store, "гость")
    assert (why.reason, why.when) == ("revoked", row["revoked"]) and why.when != row["expires"]


def test_имя_выдано_заново_старое_значение_отказ_с_именем_и_старым_уровнем_новое_без_отказа(store):
    old = store.issue("ноутбук", "read")
    store.tick(5)
    store.revoke("ноутбук")
    new = store.issue("ноутбук", "full")
    assert store.refusal(old) == T.Refusal("ноутбук", "read", "revoked", T.stamp(store.clock()))
    assert store.refusal(new) is None
    assert store.verify(new) == T.Client("ноутбук", "full") and store.verify(old) is None


def test_незнакомое_значение_без_отказа_с_именем(store):
    assert store.refusal("ba_" + "x" * 43) is None         # пустое хранилище
    tok = store.issue("ноутбук", "read")
    store.revoke("ноутбук")
    assert store.refusal("ba_" + "x" * 43) is None
    assert store.refusal(tok[:-1]) is None and store.refusal(tok + "x") is None


def test_годный_токен_без_отказа(store):
    tok = store.issue("ноутбук", "read")
    assert store.refusal(tok) is None
    tok2 = store.issue("гость", "read", days=1)
    assert store.refusal(tok2) is None


@pytest.mark.parametrize("spoil", [
    lambda t: None, lambda t: 12345, lambda t: t.encode("ascii"), lambda t: [t], lambda t: "",
    lambda t: t[3:], lambda t: "ноутбук", lambda t: "BA_" + t[3:], lambda t: " " + t])
def test_не_строка_и_значение_без_префикса_без_отказа_даже_у_отозванного(store, spoil):
    tok = store.issue("ноутбук", "read")
    store.revoke("ноутбук")
    assert store.refusal(spoil(tok)) is None


def test_отказ_читает_хранилище_так_же_как_проверка_и_не_молчит_на_испорченном(store):
    tok = store.issue("ноутбук", "read")
    for value in (tok, "ba_" + "x" * 43, None, 12345, "без префикса"):
        os.chmod(store.path, 0o644)
        with pytest.raises(T.TokenError) as e:
            store.refusal(value)
        assert "600" in str(e.value)
    os.chmod(store.path, 0o600)
    with open(store.path, "w", encoding="utf-8") as f:
        f.write("{не json")
    for value in (tok, None, "без префикса"):
        with pytest.raises(T.TokenError):
            store.refusal(value)


def test_отказ_не_трогает_последнее_обращение_и_не_пишет_файл(store):
    used = store.issue("рабочий", "read")
    gone = store.issue("ушедший", "read")
    late = store.issue("гость", "read", days=1)
    for tok in (used, gone, late):
        store.verify(tok)                                  # у каждого есть время последнего обращения
    store.revoke("ушедший")
    store.tick(86400 + 5 * T.TOUCH_EVERY)                  # «гость» просрочен, интервал обновления давно вышел
    before_rows, before_disk = store.list(), snapshot(store)
    assert store.refusal(gone).reason == "revoked" and store.refusal(late).reason == "expired"
    assert store.refusal(used) is None and store.refusal("ba_" + "x" * 43) is None and store.refusal(None) is None
    assert store.list() == before_rows
    assert snapshot(store) == before_disk
    other = other_process(store)
    assert other.refusal(late).reason == "expired" and snapshot(store) == before_disk


def test_отказ_не_создаёт_замок_и_временный_файл_на_чистом_каталоге(store):
    tok = store.issue("ноутбук", "read", days=1)
    store.revoke("ноутбук")
    for name in os.listdir(os.path.dirname(store.path)):
        if name != "tokens.json":
            os.unlink(os.path.join(os.path.dirname(store.path), name))
    store.refusal(tok)
    assert os.listdir(os.path.dirname(store.path)) == ["tokens.json"]


@pytest.mark.parametrize("position", [0, 1, 3])
def test_отказ_сравнивает_хеши_по_всем_строкам_без_раннего_выхода(store, monkeypatch, position):
    names = ["а", "б", "в", "г"]
    toks = [store.issue(n, "read") for n in names]
    store.revoke(names[position])
    calls = []
    real = T.hmac.compare_digest
    monkeypatch.setattr(T.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    assert store.refusal(toks[position]).name == names[position]
    assert len(calls) == 4
    calls.clear()
    assert store.refusal("ba_" + "x" * 43) is None
    assert len(calls) == 4


def test_отказ_находит_отозванный_токен_не_на_первой_строке(store):
    toks = {n: store.issue(n, "read") for n in ("первый", "второй", "третий")}
    store.revoke("третий")
    store.revoke("второй")
    got = {n: store.refusal(t) for n, t in toks.items()}
    assert got["первый"] is None and got["второй"].name == "второй" and got["третий"].name == "третий"


def test_два_отказа_подряд_одинаковы_и_значение_токена_в_ответе_не_хранится(store):
    tok = store.issue("ноутбук", "read")
    store.revoke("ноутбук")
    first, second = store.refusal(tok), store.refusal(tok)
    assert first == second
    assert tok not in repr(first) and T.digest(tok) not in repr(first)

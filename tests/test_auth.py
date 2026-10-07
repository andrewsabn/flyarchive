"""Вход в службы: Host, заголовок с токеном, подписанные ссылки, вход человека.

Требования: FR-02, FR-07, FR-09, FR-10.
"""
import os
import stat

import pytest

import auth as A


@pytest.fixture
def guard(tmp_path):
    clock = {"now": 1_800_000_000.0}
    g = A.Guard("search", home=str(tmp_path / "home"), hosts=["desktop.example.com"], clock=lambda: clock["now"])
    g.tick = lambda sec: clock.__setitem__("now", clock["now"] + sec)
    g.clock_ref = clock
    return g


def twin(guard, server="office"):
    """Другая служба на том же хозяйстве."""
    return A.Guard(server, home=guard.home, hosts=["desktop.example.com"], clock=lambda: guard.clock_ref["now"])


# ── Host ────────────────────────────────────────────────────────
@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8765", "localhost", "localhost:8767", "LOCALHOST:1",
                                  "[::1]:8765", "[::1]", "desktop.example.com", "Desktop.Example.COM:443"])
def test_свой_host_проходит(guard, host):
    assert guard.host_ok(host) is True


@pytest.mark.parametrize("host", [None, "", "evil.example", "127.0.0.1.evil.example", "evil.example:8765",
                                  "localhost.evil.example", "127.0.0.2", "0.0.0.0", "desktop.example.com.evil.example",
                                  "127.0.0.1:8765@evil.example", "evil.example@127.0.0.1", " 127.0.0.1",
                                  "[::2]:80", "127.0.0.1:8765:1", "127.0.0.1:abc", "localhost/x"])
def test_чужой_host_не_проходит(guard, host):
    assert guard.host_ok(host) is False


def test_без_настройки_принимаются_только_адреса_этой_машины(tmp_path, monkeypatch):
    monkeypatch.delenv("FLYARCHIVE_HOSTS", raising=False)
    g = A.Guard("search", home=str(tmp_path / "h"))
    assert g.host_ok("localhost:8765") and not g.host_ok("desktop.example.com")
    monkeypatch.setenv("FLYARCHIVE_HOSTS", "desktop.example.com, other.example.com")
    g = A.Guard("search", home=str(tmp_path / "h"))
    assert g.host_ok("desktop.example.com:443") and g.host_ok("other.example.com")


# ── заголовок с токеном ─────────────────────────────────────────
@pytest.mark.parametrize("header, token", [
    ("Bearer ba_abc", "ba_abc"), ("bearer ba_abc", "ba_abc"), ("BEARER ba_abc", "ba_abc"),
    (None, None), ("", None), ("Bearer", None), ("Bearer ", None), ("Basic ba_abc", None),
    ("ba_abc", None), ("Bearer ba_abc extra", None)])
def test_разбор_заголовка_authorization(header, token):
    assert A.bearer(header) == token


# ── подписанные ссылки ──────────────────────────────────────────
def test_подписанная_ссылка_принимается(guard):
    q = guard.sign("doc", "jira/IT/задача.md")
    assert set(q) == {"e", "s"} and len(q["s"]) == 32 and int(q["e"]) == 1_800_000_000 + 24 * 3600
    assert guard.signed_ok("doc", "jira/IT/задача.md", q["e"], q["s"]) is True


def test_ссылку_одной_службы_принимает_другая(guard):
    q = guard.sign("file", "chart-1.png")
    assert twin(guard).signed_ok("file", "chart-1.png", q["e"], q["s"]) is True


def test_ссылка_живёт_сутки(guard):
    q = guard.sign("doc", "a.md")
    guard.tick(24 * 3600 - 1)
    assert guard.signed_ok("doc", "a.md", q["e"], q["s"]) is True
    guard.tick(2)
    assert guard.signed_ok("doc", "a.md", q["e"], q["s"]) is False


def test_подделки_не_проходят(guard, tmp_path):
    q = guard.sign("doc", "a.md")
    e, s = q["e"], q["s"]
    later = str(int(e) + 86400)
    assert guard.signed_ok("doc", "b.md", e, s) is False            # подпись от другого файла
    assert guard.signed_ok("file", "a.md", e, s) is False           # подпись от другого вида ссылки
    assert guard.signed_ok("doc", "a.md", later, s) is False        # продлённый срок
    assert guard.signed_ok("doc", "a.md", e, s[:-1] + ("0" if s[-1] != "0" else "1")) is False
    assert guard.signed_ok("doc", "a.md", e, "") is False
    assert guard.signed_ok("doc", "a.md", "", s) is False
    assert guard.signed_ok("doc", "a.md", "завтра", s) is False
    assert guard.signed_ok("doc", "a.md", None, None) is False
    stranger = A.Guard("search", home=str(tmp_path / "чужой"), clock=guard.clock)
    q2 = stranger.sign("doc", "a.md")
    assert guard.signed_ok("doc", "a.md", q2["e"], q2["s"]) is False   # подпись чужим ключом


def test_разделитель_в_пути_не_даёт_подменить_вид_и_срок(guard):
    q = guard.sign("doc", "a.md\n9999999999")
    assert guard.signed_ok("doc", "a.md", "9999999999", q["s"]) is False


def test_ключ_подписи_закрыт_от_чужих(guard):
    guard.sign("doc", "a.md")
    key = os.path.join(guard.home, "secrets", "link.key")
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
    os.chmod(key, 0o644)
    with pytest.raises(A.AuthError) as e:
        twin(guard).sign("doc", "a.md")
    assert "600" in str(e.value)


def test_строка_запроса_для_ссылки(guard):
    q = guard.sign("doc", "a.md")
    assert guard.link_query("doc", "a.md") == "e=" + q["e"] + "&s=" + q["s"]


# ── вход человека по одноразовой ссылке ─────────────────────────
def test_одноразовый_код_даёт_сеанс(guard):
    code = twin(guard, "cli").new_login_code()          # код выпускает команда flyarchive
    sid = guard.redeem(code)                            # а принимает служба поиска
    assert sid and guard.session_ok("ba_session=" + sid) is True
    assert guard.session_ok("other=1; ba_session=" + sid) is True


def test_код_второй_раз_не_работает(guard):
    code = guard.new_login_code()
    assert guard.redeem(code)
    assert guard.redeem(code) is None


def test_код_живёт_пять_минут(guard):
    code = guard.new_login_code()
    guard.tick(301)
    assert guard.redeem(code) is None


@pytest.mark.parametrize("code", ["", None, "чужой", "x" * 32])
def test_чужой_код_не_принимается(guard, code):
    guard.new_login_code()
    assert guard.redeem(code) is None


def test_код_входа_хранится_только_хешем(guard):
    code = guard.new_login_code()
    path = os.path.join(guard.home, "secrets", "login.json")
    assert code not in open(path, encoding="utf-8").read()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.parametrize("cookie", [None, "", "ba_session=", "ba_session=чужой", "other=1", "ba_session"])
def test_без_сеанса_не_пускает(guard, cookie):
    guard.redeem(guard.new_login_code())
    assert guard.session_ok(cookie) is False


def test_сеанс_живёт_двенадцать_часов_и_не_переходит_в_другую_службу(guard):
    sid = guard.redeem(guard.new_login_code())
    assert twin(guard).session_ok("ba_session=" + sid) is False
    guard.tick(12 * 3600 + 1)
    assert guard.session_ok("ba_session=" + sid) is False


# ── проверка при старте службы ──────────────────────────────────
def test_старт_на_чистом_месте_создаёт_закрытый_каталог(guard):
    guard.startup_check()
    assert stat.S_IMODE(os.stat(os.path.join(guard.home, "secrets")).st_mode) == 0o700


def test_старт_отказывает_если_каталог_секретов_открыт_чужим(guard):
    guard.startup_check()
    os.chmod(os.path.join(guard.home, "secrets"), 0o755)
    with pytest.raises(A.AuthError) as e:
        guard.startup_check()
    assert "700" in str(e.value)


def test_старт_отказывает_если_хранилище_токенов_открыто_чужим(guard):
    guard.store.issue("ноутбук", "read")
    os.chmod(guard.store.path, 0o644)
    with pytest.raises(A.AuthError) as e:
        guard.startup_check()
    assert "600" in str(e.value)


# ── FR-73а: отказы входа несут код и параметры, русский текст прежний ──
def refusal(call):
    with pytest.raises(A.AuthError) as e:
        call()
    return e.value.message.code, e.value.message.args, str(e.value)


def test_каталог_секретов_открытый_чужим_с_кодом_и_путём(guard):
    guard.startup_check()
    os.chmod(guard.secrets, 0o755)
    assert refusal(guard.startup_check) == ("auth.secrets_dir_open", {"path": guard.secrets},
                                            f"права на каталог {guard.secrets} шире 700 — секреты открыты чужим")


def test_ключ_подписи_открытый_чужим_или_испорченный_с_кодом_и_путём(guard):
    guard.sign("doc", "a.md")
    key = os.path.join(guard.home, "secrets", "link.key")
    os.chmod(key, 0o644)
    assert refusal(lambda: twin(guard).sign("doc", "a.md")) == (
        "auth.link_key_open", {"path": key}, f"права на {key} шире 600 — ключ подписи ссылок открыт чужим")
    os.chmod(key, 0o600)
    with open(key, "wb") as f:
        f.write(b"short")
    assert refusal(lambda: twin(guard).sign("doc", "a.md")) == (
        "auth.link_key_broken", {"path": key}, f"ключ подписи ссылок {key} испорчен")


def test_отказ_хранилища_токенов_при_старте_сохраняет_свой_код(guard):
    guard.store.issue("ноутбук", "read")
    os.chmod(guard.store.path, 0o644)
    assert refusal(guard.startup_check) == (
        "token.store_open", {"path": guard.store.path},
        f"права на {guard.store.path} шире 600 — хранилище токенов открыто чужим, не использую")


def test_отказ_из_чужого_исключения_получает_общий_код_с_текстом(guard, monkeypatch):
    def boom():
        raise A.tokens.TokenError("хранилище сломалось по-своему")      # голая строка: кода у неё нет

    monkeypatch.setattr(guard.store, "list", boom)
    assert refusal(guard.startup_check) == ("generic.text", {"text": "хранилище сломалось по-своему"}, "хранилище сломалось по-своему")

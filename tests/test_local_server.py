"""Локальная модель — любой сервер с интерфейсом OpenAI (FR-108): проверка документа и описание изображений.

Три подставных сервера на петле, на свободных портах: с перечнем загруженных моделей (GET /running), без него (404 на перечень, обычный
ответ на запрос модели) и отвергающий дополнительные поля запроса (400, пока они есть). Настоящая модель, 127.0.0.1:8080, 127.0.0.1:11434
и видеокарта не трогаются: адреса настоящих серверов в тестах не встречаются.
"""
import hashlib
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import gatekit as K
import llm_check as L
import vision as V

KEY = "key-for-stub-4417"
BASE = {"model", "messages", "temperature", "max_tokens"}         # что понимает любой сервер с интерфейсом OpenAI
CLEAN = json.dumps({"findings": []})
DESCRIPTION = "Схема согласования договора.\nНадписи:\nНачало\nЮрист\nКонец.\nСвязи: Начало -> Юрист -> Конец."
FOREIGN = ("llama", "swap", "ollama", "vllm", "lm studio")       # названия чужих продуктов: в отказах их нет
TEXT = "Согласовать перенос работ на четверг."


@pytest.fixture(autouse=True)
def fresh_memory():
    """Память «этой точке поля не шлются» живёт в процессе: каждый тест начинается с чистой."""
    L._PLAIN.clear()
    yield
    L._PLAIN.clear()


# ── подставной сервер ───────────────────────────────────────────
def start(serve, running=("local-test",), listing_status=404, listing_reply=None, strict=False, post_status=200, echo=False, answers=()):
    """Сервер с интерфейсом OpenAI. running — что загружено (None — перечня нет: GET /running получает listing_status);
    listing_reply — (код, тело) на GET /running вместо всего прочего; strict — 400 на запрос с лишними полями; post_status — код отказа
    на любой запрос модели; echo — ответ с ошибкой повторяет заголовок авторизации; answers — ответы модели по очереди (иначе обычные)."""
    state = {"running": running, "listing_status": listing_status, "listing_reply": listing_reply, "strict": strict, "post_status": post_status,
             "echo": echo, "answers": list(answers), "gets": [], "posts": [], "echoed": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, data, raw=None):
            body = raw if raw is not None else json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status):
            text = "ошибка"
            if state["echo"]:
                text = "Authorization: " + self.headers.get("Authorization", "")      # коротко: цитата отказа ограничена двумястами знаками
                state["echoed"] += 1
            self._send(status, {"error": text})

        def do_GET(self):
            state["gets"].append(self.path)
            if self.path != "/running":
                return self._send(404, {"error": "нет такого адреса"})
            if state["listing_reply"] is not None:
                status, raw = state["listing_reply"]
                return self._send(status, None, raw)
            if state["running"] is None:
                return self._error(state["listing_status"])
            self._send(200, {"running": [{"model": m, "state": "ready"} for m in state["running"]]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["posts"].append({"path": self.path, "headers": dict(self.headers), "body": body})
            if state["post_status"] != 200:
                return self._error(state["post_status"])
            if (state["strict"] and set(body) - BASE) or not body.get("messages"):
                return self._error(400)
            if state["answers"]:
                answer = state["answers"].pop(0)
            else:
                answer = DESCRIPTION if "image_url" in json.dumps(body) else CLEAN
            self._send(200, {"choices": [{"message": {"role": "assistant", "content": answer}}]})

    state["url"] = serve(ThreadingHTTPServer, Handler)
    return state


def dead():
    """Адрес, на котором никто не слушает."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return {"url": f"http://127.0.0.1:{s.getsockname()[1]}", "gets": [], "posts": []}


def checker_for(state, tmp_path, key=KEY):
    """Проверяющий из окружения, как в работе: настоящая передача, настоящий запрос перечня. Часы подставные: ожидание не длится."""
    keyfile = tmp_path / "api-key"
    keyfile.write_text(key, encoding="utf-8")
    env = {"FLYARCHIVE_HOME": str(tmp_path / "архив"), "FLYARCHIVE_LLM_LOCAL_URL": state["url"], "FLYARCHIVE_LLM_KEY_FILE": str(keyfile),
           "FLYARCHIVE_LLM_LOCAL_MODEL": "local-test"}
    c = L.from_env(env, cloud=False)
    now = [0.0]
    c.clock = lambda: now[0]
    c.sleep = lambda s: now.__setitem__(0, now[0] + s)
    c.waited = lambda: now[0]
    return c


def check(state, tmp_path, **kw):
    return checker_for(state, tmp_path, **kw).check_text(TEXT, "письмо.eml", "eml")


# ── описание изображения ────────────────────────────────────────
class Viewer:
    """Подставной просмотр: готовый PNG, файл корпуса не открывается."""

    def __init__(self, sha, pages):
        self.sha, self.pages, self.png = sha, pages, K.png_image(300, 200)

    def show(self, home, area, path, members=()):
        meta = {"name": os.path.basename(path), "type": "pdf", "size": 1, "sha256": self.sha, "batch": "20261005-120000",
                "origin": {"archive": None, "inner": None}}
        return {"kind": "pages", "meta": meta, "pages": self.pages, "shown": self.pages}

    def page(self, home, area, path, number, members=()):
        return self.png


def describe(state, tmp_path, name="схема.png", pages=1, checker=None, **kw):
    """Один документ-схема через шаг описания. Возвращает Outcome."""
    home = str(tmp_path / "хранилище")
    os.makedirs(home, exist_ok=True)
    sha = hashlib.sha256(name.encode("utf-8")).hexdigest()
    model = V.Model(checker or checker_for(state, tmp_path, **kw))
    return V.describe(home, f"входящие/пачка/{name}", sha, model, viewer=Viewer(sha, pages))


def words(out_or_result):
    """Всё, что из отказа видит человек: тексты находок или сообщение остановки, с параметрами."""
    if hasattr(out_or_result, "findings"):
        return " ".join(" ".join([f.quote, f.where, str(f.msg), json.dumps(f.msg.args, ensure_ascii=False)]) for f in out_or_result.findings)
    stop = out_or_result.stop
    return str(stop) + json.dumps(stop.to_json(), ensure_ascii=False)


def plain_keys(post):
    return set(post["body"]) == BASE


# ══ сервер без перечня загруженных моделей ═══════════════════════
@pytest.mark.parametrize("code", [404, 405, 501])
def test_сервер_без_перечня_модель_вызвана_и_документ_проверен(serve, tmp_path, code):
    s = start(serve, running=None, listing_status=code)
    r = check(s, tmp_path)
    assert (r.status, r.model) == ("ok", "local-test") and r.findings == []
    assert s["gets"] == ["/running"] and len(s["posts"]) == 1 and s["posts"][0]["path"] == "/v1/chat/completions"


@pytest.mark.parametrize("code", [404, 405, 501])
def test_сервер_без_перечня_шаг_описания_не_стоит(serve, tmp_path, code):
    s = start(serve, running=None, listing_status=code)
    out = describe(s, tmp_path)
    assert out.stop is None and out.complete and out.pages == 1 and len(s["posts"]) == 1
    assert V.Model(checker_for(s, tmp_path)).ready() is None


def test_сервер_без_перечня_готовность_запрошена_а_не_пропущена(serve, tmp_path):
    """Перечень спрашивают всегда: сервер, который его умеет отдавать, не должен остаться без проверки."""
    s = start(serve, running=None)
    describe(s, tmp_path, pages=2)
    assert s["gets"] == ["/running", "/running"] and len(s["posts"]) == 2


def test_нет_перечня_читается_как_none_а_перечень_как_список(serve):
    none, listed = start(serve, running=None), start(serve, running=["local-test", "чужая"])
    assert L.swap_running(none["url"], KEY)() is None
    assert L.swap_running(listed["url"], KEY)() == ["local-test", "чужая"]
    assert listed["gets"] == ["/running"]


# ══ сервер с перечнем: правило владельца про одну видеокарту ═════
def test_с_перечнем_нужная_модель_загружена_модель_вызвана(serve, tmp_path):
    s = start(serve, running=["local-test"])
    assert check(s, tmp_path).model == "local-test" and len(s["posts"]) == 1
    assert describe(s, tmp_path).complete and len(s["posts"]) == 2


def test_с_перечнем_и_чужой_моделью_проверка_ждёт_до_срока_и_запрос_не_уходит(serve, tmp_path):
    s = start(serve, running=["чужая-модель"])
    c = checker_for(s, tmp_path)
    r = c.check_text(TEXT, "письмо.eml", "eml")
    assert (r.status, r.model) == ("unchecked", None) and s["posts"] == []
    assert 170 <= c.waited() <= 190 and len(s["gets"]) > 3                   # те же 180 секунд, перечень смотрели снова и снова
    assert "занята другой моделью (чужая-модель)" in words(r)


def test_с_перечнем_и_чужой_моделью_описание_стоит_и_запрос_не_уходит(serve, tmp_path):
    s = start(serve, running=["чужая-модель", "ещё-одна"])
    out = describe(s, tmp_path)
    assert not out.complete and s["posts"] == []
    assert (out.stop.code, out.stop.args) == ("vision.model_busy", {"loaded": "чужая-модель, ещё-одна"})


def test_с_перечнем_и_пустым_ничего_не_загружено_проверка_зовёт_модель_а_описание_стоит(serve, tmp_path):
    s = start(serve, running=[])
    assert check(s, tmp_path).model == "local-test" and len(s["posts"]) == 1      # как раньше: свободная карта, модель грузится сама
    posts = len(s["posts"])
    out = describe(s, tmp_path)
    assert out.stop.code == "vision.model_not_loaded" and not out.complete and len(s["posts"]) == posts


# ══ сервер недоступен ════════════════════════════════════════════
def test_сервер_недоступен_проверка_отказывает_без_названия_чужого_продукта(tmp_path):
    r = check(dead(), tmp_path)
    assert (r.status, r.model) == ("unchecked", None)
    shown = words(r)
    assert "состояние видеокарты неизвестно" in shown and "сервер модели не отвечает" in shown
    assert not any(w in shown.lower() for w in FOREIGN), shown


def test_сервер_недоступен_описание_стоит_без_названия_чужого_продукта(tmp_path):
    out = describe(dead(), tmp_path)
    assert not out.complete and out.stop.code == "vision.model_down"
    shown = words(out)
    assert "сервер модели не отвечает" in shown and not any(w in shown.lower() for w in FOREIGN), shown


@pytest.mark.parametrize("kind", ["5xx", "503", "401", "html", "форма"])
def test_перечень_не_получен_это_отказ_а_не_готовность(serve, tmp_path, kind):
    """Не «такого адреса нет»: сервер сломан, отверг ключ или ответил не по протоколу — модель не вызывается."""
    reply = {"5xx": None, "503": None, "401": None, "html": (200, b"<html>not json</html>"), "форма": (200, b'{"models": []}')}[kind]
    code = {"5xx": 500, "503": 503, "401": 401}.get(kind, 404)
    s = start(serve, running=None, listing_status=code, listing_reply=reply)
    r = check(s, tmp_path)
    assert (r.status, r.model) == ("unchecked", None) and s["posts"] == []
    out = describe(s, tmp_path)
    assert not out.complete and out.stop.code == "vision.model_down" and s["posts"] == []
    for shown in (words(r), words(out)):
        assert not any(w in shown.lower() for w in FOREIGN), shown


def test_перечень_не_пришёл_за_срок_это_отказ(monkeypatch):
    def silent(*a, **k):
        raise socket.timeout("timed out")

    monkeypatch.setattr(L.urllib.request, "urlopen", silent)
    with pytest.raises(L.ModelError) as e:
        L.swap_running("http://127.0.0.1:9", KEY)()
    assert "сервер модели не отвечает" in str(e.value) and not any(w in str(e.value).lower() for w in FOREIGN)


def test_отказ_перечня_и_отказ_запроса_называют_сервер_модели_а_не_чужой_продукт(serve, tmp_path):
    s = start(serve, running=None, listing_status=500)
    with pytest.raises(L.ModelError) as e:
        L.swap_running(s["url"], KEY)()
    assert str(e.value).startswith("сервер модели не отвечает") and "500" in str(e.value)


# ══ сервер отвергает дополнительные поля ═════════════════════════
def test_сервер_отвергает_поля_один_повтор_без_полей_и_проверка_удалась(serve, tmp_path):
    s = start(serve, running=None, strict=True)
    r = check(s, tmp_path)
    assert (r.status, r.model) == ("ok", "local-test") and r.findings == []
    first, second = s["posts"]
    assert {"response_format", "chat_template_kwargs"} <= set(first["body"]) and plain_keys(second) and len(s["posts"]) == 2
    for field in ("model", "messages", "temperature", "max_tokens"):          # тот же запрос, только без лишнего
        assert second["body"][field] == first["body"][field], field


def test_сервер_отвергает_поля_описание_изображения_повторяется_без_полей(serve, tmp_path):
    s = start(serve, running=None, strict=True)
    out = describe(s, tmp_path)
    assert out.stop is None and out.complete and out.pages == 1
    first, second = s["posts"]
    assert "chat_template_kwargs" in first["body"] and plain_keys(second) and len(s["posts"]) == 2
    for field in ("model", "messages", "temperature", "max_tokens"):
        assert second["body"][field] == first["body"][field], field
    assert any(p.get("type") == "image_url" for p in second["body"]["messages"][-1]["content"])      # картинка в повторе на месте


def test_сервер_с_перечнем_и_отвергающий_поля_тоже_повторяется_без_полей(serve, tmp_path):
    s = start(serve, running=["local-test"], strict=True)
    assert check(s, tmp_path).status == "ok" and len(s["posts"]) == 2 and plain_keys(s["posts"][1])


def test_ответ_400_и_без_полей_отказ_без_третьего_запроса(serve, tmp_path):
    s = start(serve, running=None, post_status=400)
    r = check(s, tmp_path)
    assert (r.status, r.model) == ("unchecked", None) and "код ответа 400" in words(r)
    first, second = s["posts"]
    assert "response_format" in first["body"] and plain_keys(second) and len(s["posts"]) == 2


def test_ответ_400_и_без_полей_описание_стоит_без_третьего_запроса(serve, tmp_path):
    s = start(serve, running=None, post_status=400)
    out = describe(s, tmp_path)
    assert not out.complete and out.stop.code == "vision.model_down" and "код ответа 400" in words(out)
    assert len(s["posts"]) == 2 and plain_keys(s["posts"][1])


def test_запрос_уже_без_полей_и_ответ_400_это_один_запрос(serve):
    s = start(serve, running=None, post_status=400)
    endpoint = L.Endpoint("local-test", s["url"] + "/v1", KEY, True, True, {})
    with pytest.raises(L.ModelError):
        L.http_transport(endpoint, {"model": "local-test", "messages": [{"role": "user", "content": "x"}], "temperature": 0, "max_tokens": 5}, 10)
    assert len(s["posts"]) == 1


def test_неудавшийся_повтор_ничего_не_запоминает_поля_уходят_и_дальше(serve, tmp_path):
    s = start(serve, running=None, post_status=400)
    check(s, tmp_path)
    check(s, tmp_path)
    assert len(s["posts"]) == 4 and "response_format" in s["posts"][2]["body"] and plain_keys(s["posts"][3])


@pytest.mark.parametrize("code", [401, 403, 404, 422, 429, 500, 503])
def test_другой_код_ошибки_не_повторяется(serve, code):
    s = start(serve, running=None, post_status=code)
    endpoint = L.Endpoint("local-test", s["url"] + "/v1", KEY, True, True, {"chat_template_kwargs": {"enable_thinking": False}})
    payload = {"model": "local-test", "messages": [{"role": "user", "content": "x"}], "temperature": 0, "max_tokens": 5,
               "response_format": {"type": "json_object"}, **endpoint.extra}
    with pytest.raises(L.ModelError) as e:
        L.http_transport(endpoint, payload, 10)
    assert len(s["posts"]) == 1 and str(code) in str(e.value) and not isinstance(e.value, L.ModelTimeout)
    assert "response_format" in s["posts"][0]["body"]


@pytest.mark.parametrize("code", [401, 503])
def test_другой_код_ошибки_проверка_и_описание_идут_одним_запросом(serve, tmp_path, code):
    s = start(serve, running=None, post_status=code)
    assert check(s, tmp_path).status == "unchecked" and len(s["posts"]) == 1
    out = describe(s, tmp_path)
    assert out.stop.code == "vision.model_down" and len(s["posts"]) == 2


def test_после_удачного_повтора_следующий_документ_уходит_сразу_без_полей(serve, tmp_path):
    s = start(serve, running=None, strict=True)
    c = checker_for(s, tmp_path)
    assert c.check_text("первый", "a.eml", "eml").status == "ok"
    assert c.check_text("второй", "b.eml", "eml").status == "ok"
    assert len(s["posts"]) == 3 and not plain_keys(s["posts"][0]) and plain_keys(s["posts"][1]) and plain_keys(s["posts"][2])
    assert "второй" in json.dumps(s["posts"][2]["body"], ensure_ascii=False)
    again = checker_for(s, tmp_path)                                      # новый проверяющий в том же процессе: память общая
    assert again.check_text("третий", "c.eml", "eml").status == "ok" and len(s["posts"]) == 4 and plain_keys(s["posts"][3])


def test_после_удачного_повтора_страницы_изображения_уходят_сразу_без_полей(serve, tmp_path):
    s = start(serve, running=None, strict=True)
    out = describe(s, tmp_path, pages=3)
    assert out.complete and out.pages == 3 and len(s["posts"]) == 4
    assert not plain_keys(s["posts"][0]) and all(plain_keys(p) for p in s["posts"][1:])


def test_память_общая_у_проверки_и_описания_одной_точки(serve, tmp_path):
    s = start(serve, running=None, strict=True)
    assert check(s, tmp_path).status == "ok"
    out = describe(s, tmp_path)
    assert out.complete and len(s["posts"]) == 3 and plain_keys(s["posts"][2])


def test_память_у_точки_а_не_у_всех_серверов_сразу(serve, tmp_path):
    first, second = start(serve, running=None, strict=True), start(serve, running=None, strict=True)
    assert check(first, tmp_path).status == "ok"
    assert check(second, tmp_path).status == "ok"
    assert len(first["posts"]) == 2 and len(second["posts"]) == 2 and not plain_keys(second["posts"][0])      # второму сервер поля сначала шлются


def test_повтор_без_полей_не_отменяет_напоминание_про_json(serve, tmp_path):
    s = start(serve, running=None, strict=True, answers=["Конечно! Документ безопасен.", CLEAN])
    r = check(s, tmp_path)
    assert r.status == "ok" and len(s["posts"]) == 3                      # 400, ответ не JSON, повтор с напоминанием
    reminder = s["posts"][2]["body"]
    assert plain_keys(s["posts"][2]) and reminder["messages"][-1]["content"] == L.REMINDER and len(reminder["messages"]) == 4


# ══ ключ не попадает в текст отказа ══════════════════════════════
@pytest.mark.parametrize("scenario", [dict(post_status=400), dict(post_status=401), dict(post_status=500), dict(listing_status=500),
                                      dict(listing_status=401), dict(strict=True, post_status=400)])
def test_ключ_не_попадает_в_текст_отказа(serve, tmp_path, scenario):
    s = start(serve, running=None, echo=True, **scenario)
    r = check(s, tmp_path)
    out = describe(s, tmp_path)
    assert r.status == "unchecked" and not out.complete
    assert s["echoed"] >= 2                                                # сервер прислал ключ в теле ответа с ошибкой
    for shown in (words(r), words(out)):
        assert KEY not in shown, shown


def test_ключ_не_попадает_в_ошибку_передачи(serve):
    s = start(serve, running=None, echo=True, post_status=400)
    endpoint = L.Endpoint("local-test", s["url"] + "/v1", KEY, True, True, {})
    with pytest.raises(L.ModelError) as e:
        L.http_transport(endpoint, {"model": "local-test", "messages": [{"role": "user", "content": "x"}],
                                    "response_format": {"type": "json_object"}}, 10)
    assert s["echoed"] == 2 and KEY not in str(e.value) and KEY not in repr(e.value)
    assert all(p["headers"]["Authorization"] == "Bearer " + KEY for p in s["posts"])      # а повтор идёт с тем же ключом, как и первый запрос

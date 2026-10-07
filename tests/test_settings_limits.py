"""Пределы и сроки из настроек; настройки приёмки остаются за командой (FR-98).

Главное, что держат тесты. Пятнадцать пределов и сроков (поиск, ответы поиска, ожидание MCP, сроки ссылки, кода входа и сеанса, проверка
моделью, сервер документов) берутся из `tools/settings.py` один раз при загрузке модуля и лежат в прежних именованных значениях модуля;
умолчания схемы равны прежним значениям в коде (таблица LIMITS пишет прежние числа отдельно от кода); изменённая настройка меняет поведение:
ссылка с новым сроком истекает в новый срок (403), код входа и сеанс — тоже, поиск ходит в таблицу с новыми разделами и уточнением, ответ
несёт новые выдержку, число находок и потолок, сервер документов отказывает в новом размере до записи на диск и убирает файлы в новый срок.
Границы схемы не дают поставить ноль или отрицательное и не дают снять предел: число находок и потолок ответа ограничены сверху,
размер документа — так, чтобы он прошёл в base64 через самое узкое тело запроса. Начало текста для модели выводится из предела в прежней доле.

Запасная облачная модель по умолчанию выключена и в настройках приёмки: новый архив без единой правки не отправляет текст с машины,
архив с записанным `cloud: true` ведёт себя как раньше, значение из inbox.json не перебивается переменной окружения. Каталог песочницы
и каталог служб берутся из настроек. В схеме нет настроек, которых никто не читает: тест разбирает исходники ядра,
а сам детектор проверен на образцах.

Дочерние процессы идут через обвязку `test_settings_paths.py` и `test_settings_services.py`: пустой `HOME`, крюк аудита, явное окружение.
Настоящие порты служб, модель и ollama тесты не трогают: подставные службы — на случайных портах, «облако» — подставной сервер.
"""
import ast
import json
import os
import re
import types

import pytest

import auth as A
import gatekit as K
import inbox as B
import foreign
import sandbox
import settings as S
from test_cli import args, load_cli
from test_settings_paths import EXCLUDED, TOOLS, core_files, lab, read_core, untouched, watch  # noqa: F401  обвязка дочерних процессов
from test_settings_services import LAYERS, no_real_ports, put, run_snippet  # noqa: F401  подставные службы; no_real_ports — autouse
from test_vision_cli import cli, cloud_server, lance, model_server, vectors  # noqa: F401  подставная модель, «облако» и сервер векторов

MIB = 1024 * 1024
FOREIGN_PREFIX = foreign.PREFIX          # приставка переменных чужой программы: не действует (FR-105)


# ══ таблица: значение модуля → настройка → умолчание ═════════════
class Limit(types.SimpleNamespace):
    """attr — значение модуля («модуль.ИМЯ»), key — настройка, old — прежнее значение в коде (до выноса в настройки, числом из кода),
    sample — пробное значение настройки (годное и не равное умолчанию), scale — во сколько раз значение модуля больше настройки."""


LIMITS = [Limit(attr=a, key=k, old=o, sample=s, scale=c) for a, k, o, s, c in (
    ("search.HALF_LIFE_DAYS", "search_half_life_days", 540, 100, 1),
    ("search.NPROBES", "search_nprobes", 400, 123, 1),
    ("search.REFINE", "search_refine", 30, 7, 1),
    ("webui.API_SNIPPET", "api_snippet_chars", 420, 111, 1),
    ("webui.API_MAX_K", "api_max_k", 20, 6, 1),
    ("webui.API_BUDGET", "api_budget_chars", 6000, 2222, 1),
    ("mcp_server.TIMEOUT", "mcp_timeout_s", 300, 77, 1),
    ("auth.LINK_TTL", "link_ttl_s", 86400, 3600, 1),
    ("auth.LOGIN_TTL", "login_ttl_s", 300, 90, 1),
    ("auth.SESSION_TTL", "session_ttl_s", 43200, 1800, 1),
    ("llm_check.TIMEOUT", "llm_timeout_s", 180, 45, 1),
    ("llm_check.MAX_CHARS", "llm_max_chars", 30000, 12345, 1),
    ("llm_check.MAX_PAGES", "llm_max_pages", 20, 4, 1),
    ("office_server.SUBMIT_MAX", "submit_max_mb", 16, 3, MIB),          # настройка в мегабайтах, значение модуля — в байтах
    ("office_server.KEEP_HOURS", "out_keep_hours", 48, 5, 1),
)]
HEAD ="llm_check.HEAD"                                                   # доля начала текста для модели: 24000 из 30000 — четыре пятых
BODY_MAX = "office_server.BODY_MAX"                                       # защитный предел тела запроса сервера документов: в код, не в настройку
OLD_HEAD, OLD_BODY_MAX = 24000, 32 * MIB


def head_of(chars):
    return chars * 4 // 5


def module_values(settings_by_key=None):
    """Что должно лежать в значениях модулей при этих настройках: все пятнадцать, начало текста и защитный предел тела запроса."""
    chosen = settings_by_key or {}
    values = {limit.attr: chosen.get(limit.key, limit.old) * limit.scale for limit in LIMITS}
    values[HEAD] = head_of(values["llm_check.MAX_CHARS"])
    values[BODY_MAX] = OLD_BODY_MAX
    return values


def ask(lab, env):
    r = lab("attrs", env=env, attrs=list(module_values()))
    assert r.code == 0, r.err[-800:]
    untouched(r)
    return r.data["values"]


def test_таблица_пределов_полная_ключи_схемы_и_значения_модулей_различны():
    assert len(LIMITS) == 15
    assert len({limit.key for limit in LIMITS}) == len({limit.attr for limit in LIMITS}) == 15
    assert all(limit.key in S.SCHEMA for limit in LIMITS)
    assert all(limit.sample != limit.old for limit in LIMITS), "пробное значение равно умолчанию: тест не отличил бы настройку от кода"
    assert len({limit.sample for limit in LIMITS}) == 15, "пробные значения различны: перепутанные настройки не спрячутся"


@pytest.mark.parametrize("limit", LIMITS, ids=lambda limit: limit.key)
def test_умолчание_схемы_равно_прежнему_значению_в_коде(limit):
    spec = S.SCHEMA[limit.key]
    assert spec.type == "int" and spec.default == limit.old, f"{limit.key}: умолчание {spec.default}, а в коде было {limit.old}"
    assert spec.low <= spec.default <= spec.high


def test_без_настроек_значения_модулей_прежние(lab, tmp_path):
    got = ask(lab, put(tmp_path, "env"))
    assert got == module_values(), "значения модулей без настроек отличаются от прежних"
    assert got[HEAD] == OLD_HEAD and got["office_server.SUBMIT_MAX"] == 16 * 1024 * 1024


@pytest.mark.parametrize("layer", LAYERS)
def test_изменённая_настройка_доходит_до_значения_своего_модуля(lab, tmp_path, layer):
    chosen = {limit.key: limit.sample for limit in LIMITS}
    got = ask(lab, put(tmp_path, layer, **chosen))
    want = module_values(chosen)
    assert got == want, {name: (got[name], want[name]) for name in want if got[name] != want[name]}


@pytest.mark.parametrize("limit", LIMITS, ids=lambda limit: limit.key)
def test_каждая_настройка_меняет_только_свой_предел(lab, tmp_path, limit):
    got = ask(lab, put(tmp_path, "env", **{limit.key: limit.sample}))
    want = module_values({limit.key: limit.sample})
    assert got == want, {name: (got[name], want[name]) for name in want if got[name] != want[name]}


def test_значение_берётся_из_настроек_один_раз_при_загрузке_модуля_а_не_при_каждом_обращении(lab, tmp_path):
    code = '''
import os
import auth, llm_check, office_server, search, webui
before = (auth.LINK_TTL, search.NPROBES, webui.API_MAX_K, llm_check.MAX_CHARS, office_server.SUBMIT_MAX)
os.environ["FLYARCHIVE_LINK_TTL_S"], os.environ["FLYARCHIVE_SEARCH_NPROBES"] = "7200", "11"
os.environ["FLYARCHIVE_API_MAX_K"], os.environ["FLYARCHIVE_LLM_MAX_CHARS"], os.environ["FLYARCHIVE_SUBMIT_MAX_MB"] = "3", "2000", "2"
result["same"] = before == (auth.LINK_TTL, search.NPROBES, webui.API_MAX_K, llm_check.MAX_CHARS, office_server.SUBMIT_MAX)
'''
    r = run_snippet(lab, tmp_path, code, put(tmp_path, "env"))
    assert r.data["same"] is True


# ── границы схемы: ни нуля, ни отрицательного, ни снятого предела ─
FLOORS = {"link_ttl_s": 60, "login_ttl_s": 30, "session_ttl_s": 300, "mcp_timeout_s": 1, "llm_timeout_s": 5, "llm_max_chars": 1000,
          "llm_max_pages": 1, "submit_max_mb": 1, "out_keep_hours": 1, "api_snippet_chars": 40, "api_max_k": 1, "api_budget_chars": 500,
          "search_half_life_days": 1, "search_nprobes": 1, "search_refine": 1}


@pytest.fixture
def archive_env(tmp_path):
    home = tmp_path / "архив"
    home.mkdir()
    return {"HOME": str(tmp_path / "user"), "FLYARCHIVE_HOME": str(home)}


def write_settings(archive_env, data):
    path = os.path.join(archive_env["FLYARCHIVE_HOME"], "settings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)


@pytest.mark.parametrize("limit", LIMITS, ids=lambda limit: limit.key)
def test_нижняя_граница_схемы_не_даёт_поставить_ноль_или_отрицательное(archive_env, limit):
    spec = S.SCHEMA[limit.key]
    assert spec.low == FLOORS[limit.key] and spec.low >= 1, f"{limit.key}: нижняя граница {spec.low}"
    for value in (0, -1, -limit.old, spec.low - 1):
        write_settings(archive_env, {limit.key: value})
        with pytest.raises(S.SettingsError) as e:
            S.load(env=archive_env)
        assert e.value.message.code == "settings.bad_int" and e.value.message.args["key"] == limit.key, (limit.key, value)
        write_settings(archive_env, {})                                              # плохое значение в файле отказало бы раньше окружения
        with pytest.raises(S.SettingsError) as e:
            S.load(env={**archive_env, S.env_name(limit.key): str(value)})
        assert e.value.message.args["key"] == limit.key and e.value.message.args["source"] == S.env_name(limit.key), (limit.key, value)


@pytest.mark.parametrize("limit", LIMITS, ids=lambda limit: limit.key)
def test_у_каждого_предела_есть_верхняя_граница_и_за_ней_отказ(archive_env, limit):
    spec = S.SCHEMA[limit.key]
    assert spec.high is not None and spec.high >= spec.default
    write_settings(archive_env, {limit.key: spec.high + 1})
    with pytest.raises(S.SettingsError) as e:
        S.load(env=archive_env)
    assert e.value.message.args["key"] == limit.key
    write_settings(archive_env, {limit.key: spec.high})
    assert S.load(env=archive_env)[limit.key] == spec.high


def test_число_находок_и_потолок_ответа_ограничены_сверху_настройка_не_снимает_предел():
    assert S.SCHEMA["api_max_k"].high <= 100 and S.SCHEMA["api_budget_chars"].high <= 100_000
    assert S.SCHEMA["api_snippet_chars"].high <= 5000


# ══ начало текста для модели: доля прежняя при любом пределе ═════
HEADS = r'''
import llm_check
limit = llm_check.MAX_CHARS
text = ("0123456789" * (3 * limit // 10 + 1))[: 3 * limit]
mark = "=====МЕТКА====="
checker = llm_check.Checker(llm_check.Endpoint("m", "http://127.0.0.1:1/v1", "k", True, True, {}), new_mark=lambda: mark)
user = checker._messages(text, "а.txt", "txt")[1]["content"]
body = user.split(mark)[1][1:-1]
head, rest = body.split("\n[… пропущено", 1)
skipped, tail = rest.split(" знаков …]\n", 1)
result.update(limit=limit, head_const=llm_check.HEAD, head=len(head), tail=len(tail), skipped=int(skipped), whole=len(text),
              starts=text.startswith(head), ends=text.endswith(tail))
'''


@pytest.mark.parametrize("chars", [1000, 1001, 4321, 30000, 999_999, 1_000_000])
def test_начало_текста_для_модели_выводится_из_предела_и_с_концом_не_превышает_его(lab, tmp_path, chars):
    r = run_snippet(lab, tmp_path, HEADS, put(tmp_path, "env", llm_max_chars=chars))
    d = r.data
    assert d["limit"] == chars and d["head_const"] == head_of(chars)
    assert 0 < d["head"] < chars and 0 < d["tail"] < chars, "и начало, и конец непустые"
    assert d["head"] == head_of(chars) and d["tail"] == chars - head_of(chars)
    assert d["head"] + d["tail"] <= chars, "начало и конец вместе не больше предела"
    assert d["skipped"] == d["whole"] - chars and d["starts"] and d["ends"], "из середины пропущено ровно лишнее, а края — края текста"


def test_при_умолчании_начало_текста_прежние_двадцать_четыре_тысячи_из_тридцати(lab, tmp_path):
    r = run_snippet(lab, tmp_path, HEADS, put(tmp_path, "env"))
    assert (r.data["limit"], r.data["head_const"], r.data["head"], r.data["tail"]) == (30000, 24000, 24000, 6000)


# ══ поведение: поиск ═════════════════════════════════════════════
SEARCH_CALLS = r'''
import sys
calls = {}


class Query:
    def where(self, flt):
        return self

    def nprobes(self, n):
        calls["nprobes"] = n
        return self

    def refine_factor(self, factor):
        calls["refine"] = factor
        return self

    def limit(self, n):
        return self

    def to_list(self):
        return []


class Table:
    def search(self, *args, **kwargs):
        return Query()


class Db:
    def open_table(self, name):
        return Table()


sys.modules["lancedb"].connect = lambda path: Db()
import os
import search
os.makedirs(search.DB, exist_ok=True)              # каталог индекса есть: без него поиск отказывает до обращения к библиотеке (FR-107)
search.embed = lambda text: [0.5]
search.search("образец")
result["calls"] = calls
result["days"] = search.days_old("2025-10-03", 20260101)
result["recency"] = search.recency("2025-10-03", 20260101)
'''


def test_поиск_без_настроек_берёт_прежние_разделы_уточнение_и_полураспад(lab, tmp_path):
    r = run_snippet(lab, tmp_path, SEARCH_CALLS, put(tmp_path, "env"))
    assert r.data["calls"] == {"nprobes": 400, "refine": 30}
    assert r.data["recency"] == pytest.approx(0.5 ** (r.data["days"] / 540))


@pytest.mark.parametrize("layer", LAYERS)
def test_поиск_ходит_в_таблицу_с_разделами_и_уточнением_из_настроек_а_свежесть_гаснет_по_полураспаду_из_настройки(lab, tmp_path, layer):
    r = run_snippet(lab, tmp_path, SEARCH_CALLS, put(tmp_path, layer, search_nprobes=123, search_refine=7, search_half_life_days=100))
    assert r.data["calls"] == {"nprobes": 123, "refine": 7}
    assert r.data["days"] == 93 and r.data["recency"] == pytest.approx(0.5 ** (93 / 100))


# ══ поведение: ответы поиска ═════════════════════════════════════
API = r'''
import urllib.parse
import auth, webui

hits = [(1.0 - i / 1000, {"title": "док %d" % i, "updated": "2026-01-01", "source": "jira", "space": "P", "path": "а/%d.txt" % i,
                          "text": "ж" * 1000}) for i in range(CASE["found"])]
asked = []


def fake(q, n, since, source, space):
    asked.append(n)
    return hits[:n]


webui.S.search = fake
guard = auth.Guard("search")
webui.Handler.guard = guard
server = webui.Server(("127.0.0.1", 0), webui.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
token = guard.store.issue("образец", "read")
query = ("&k=" + urllib.parse.quote(CASE["k"])) if "k" in CASE else ""
request = urllib.request.Request("http://127.0.0.1:%d/api/search?q=%s%s" % (server.server_address[1], urllib.parse.quote("образец"), query),
                                 headers={"Authorization": "Bearer " + token})
with urllib.request.urlopen(request, timeout=20) as reply:
    answer = json.loads(reply.read())
result["count"] = answer["count"]
result["lengths"] = [len(r["text"]) for r in answer["results"]]
result["asked"] = asked
'''


def api(lab, tmp_path, found=40, k=None, **settings):
    case = {"found": found}
    if k is not None:
        case["k"] = k
    r = run_snippet(lab, tmp_path, API, {**put(tmp_path, "env", **settings), "CASE": json.dumps(case)})
    return r.data


def test_ответ_поиска_без_настроек_прежний_двадцать_находок_выдержка_420_потолок_6000(lab, tmp_path):
    d = api(lab, tmp_path, k="99")
    assert d["asked"] == [20] and d["count"] == 20, "запрос сверх предела обрезается до двадцати, как раньше"
    assert max(d["lengths"]) == 420 and sum(d["lengths"]) <= 6000 and d["lengths"][:14] == [420] * 14 and d["lengths"][14:] == [0] * 6


def test_число_находок_из_настройки_обрезает_запрос_сверх_предела(lab, tmp_path):
    d = api(lab, tmp_path, k="99", api_max_k=3)
    assert d["asked"] == [3] and d["count"] == 3
    d = api(lab, tmp_path / "ещё", k="2", api_max_k=3)
    assert d["asked"] == [2] and d["count"] == 2, "меньше предела — столько, сколько просили"


def test_запрос_без_числа_или_с_негодным_числом_не_выходит_за_предел_из_настройки(lab, tmp_path):
    for n, k in enumerate((None, "abc", "", "1.5")):
        d = api(lab, tmp_path / str(n), k=k, api_max_k=5)
        assert d["asked"] == [5] and d["count"] == 5, f"k={k!r}: предел настройки обойдён запросом без числа"
    d = api(lab, tmp_path / "по-умолчанию", k="abc")
    assert d["asked"] == [10] and d["count"] == 10, "без настройки негодное число по-прежнему даёт десять"


def test_настройка_не_снимает_предел_число_находок_не_больше_верхней_границы_схемы(lab, tmp_path):
    high = S.SCHEMA["api_max_k"].high
    d = api(lab, tmp_path, found=high + 50, k=str(10 ** 6), api_max_k=high)
    assert d["asked"] == [high] and d["count"] == high, "запрос в миллион находок получил не больше верхней границы схемы"


def test_длина_выдержки_из_настройки(lab, tmp_path):
    d = api(lab, tmp_path, k="5", api_snippet_chars=60)
    assert d["count"] == 5 and d["lengths"] == [60] * 5


def test_потолок_ответа_из_настройки_после_него_находки_идут_без_текста(lab, tmp_path):
    d = api(lab, tmp_path, k="10", api_budget_chars=1000)
    assert d["count"] == 10 and d["lengths"] == [420, 420] + [0] * 8, "потолок 1000 вмещает две выдержки; остальные — без текста, но перечислены"
    d = api(lab, tmp_path / "ещё", k="10", api_budget_chars=500, api_snippet_chars=100)
    assert d["lengths"] == [100] * 5 + [0] * 5


def test_потолок_ответа_не_выше_верхней_границы_схемы_и_выдержки_всех_находок_в_него_укладываются(lab, tmp_path):
    spec = S.SCHEMA["api_budget_chars"]
    d = api(lab, tmp_path, found=100, k="100", api_budget_chars=spec.high, api_max_k=100, api_snippet_chars=5000)
    assert sum(d["lengths"]) <= spec.high and d["count"] == 100


# ══ поведение: ожидание переходника MCP ══════════════════════════
MCP_SLOW = r'''
import http.server, time


class Slow(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        time.sleep(CASE["sleep"])
        data = b'{"results": []}'
        try:
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["FLYARCHIVE_SEARCH_PORT"] = str(server.server_address[1])
import mcp_server
begin = time.monotonic()
try:
    mcp_server.call_upstream(mcp_server.BY_NAME["search_archive"], {"q": "образец"}, token="sample-token")
    result["outcome"] = "ответ"
except Exception as e:
    result["outcome"] = type(e).__name__
result["seconds"] = time.monotonic() - begin
'''


def test_переходник_mcp_ждёт_службу_не_дольше_срока_из_настройки(lab, tmp_path):
    r = run_snippet(lab, tmp_path, MCP_SLOW, {**put(tmp_path, "env", mcp_timeout_s=1), "CASE": json.dumps({"sleep": 3})})
    assert r.data["outcome"] != "ответ", "служба молчала три секунды, срок — одна: переходник не должен был дождаться"
    assert r.data["seconds"] < 2.6


def test_переходник_mcp_без_настройки_ждёт_службу_и_за_секунды_не_отказывает(lab, tmp_path):
    r = run_snippet(lab, tmp_path, MCP_SLOW, {**put(tmp_path, "env"), "CASE": json.dumps({"sleep": 1})})
    assert r.data["outcome"] == "ответ" and r.data["seconds"] >= 0.9


DESCRIPTIONS = r'''
import mcp_server
result["k"] = mcp_server.BY_NAME["search_archive"]["inputSchema"]["properties"]["k"]["description"]
result["submit"] = mcp_server.BY_NAME["submit_document"]["description"]
'''


def test_описание_инструментов_mcp_называет_действующие_предел_находок_и_размер_документа(lab, tmp_path):
    r = run_snippet(lab, tmp_path, DESCRIPTIONS, put(tmp_path, "env"))
    assert r.data["k"] == "Сколько вернуть, 1-20" and "до 16 МБ на документ" in r.data["submit"]
    r = run_snippet(lab, tmp_path / "ещё", DESCRIPTIONS, put(tmp_path / "ещё", "env", api_max_k=5, submit_max_mb=3))
    assert r.data["k"] == "Сколько вернуть, 1-5" and "до 3 МБ на документ" in r.data["submit"] and "16 МБ" not in r.data["submit"]


# ══ поведение: сроки ссылки, кода входа и сеанса ═════════════════
ENTRY = r'''
import http.client, urllib.parse
import auth, webui

now = [1_000_000.0]
guard = auth.Guard("search", clock=lambda: now[0])
webui.Handler.guard = guard
server = webui.Server(("127.0.0.1", 0), webui.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]
corpus = os.path.join(os.environ["FLYARCHIVE_HOME"], "corpus")
os.makedirs(corpus, exist_ok=True)
with open(os.path.join(corpus, "заметка.txt"), "w", encoding="utf-8") as f:
    f.write("текст")


def get(path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request("GET", path, headers=headers or {})
    reply = conn.getresponse()
    reply.read()
    out = (reply.status, reply.getheader("Set-Cookie"))
    conn.close()
    return out


link = "/doc?p=" + urllib.parse.quote("заметка.txt") + "&" + guard.link_query("doc", "заметка.txt")
seen = [get(link)[0]]
now[0] += CASE["link"] - 1
seen.append(get(link)[0])
now[0] += 2
seen.append(get(link)[0])
result["link"] = seen

fresh = guard.new_login_code()
now[0] += CASE["login"] - 1
login = [get("/login?c=" + fresh)[0]]
stale = guard.new_login_code()
now[0] += CASE["login"] + 1
login.append(get("/login?c=" + stale)[0])
result["login"] = login

code = guard.new_login_code()
status, cookie = get("/login?c=" + code)
cookie = cookie.split(";")[0]
began = now[0]
now[0] = began + CASE["session"] - 1
session = [status, get("/", {"Cookie": cookie})[0]]
now[0] = began + CASE["session"] + 1
session.append(get("/", {"Cookie": cookie})[0])
result["session"] = session
'''


def test_без_настроек_ссылка_живёт_сутки_код_входа_пять_минут_сеанс_двенадцать_часов(lab, tmp_path):
    case = {"link": 86400, "login": 300, "session": 43200}
    d = run_snippet(lab, tmp_path, ENTRY, {**put(tmp_path, "env"), "CASE": json.dumps(case)}).data
    assert d["link"] == [200, 200, 403], "ссылка жива за секунду до суток и просрочена через секунду после"
    assert d["login"] == [302, 401], "код входа принят за секунду до пяти минут и не принят через секунду после"
    assert d["session"] == [302, 200, 401], "сеанс жив за секунду до двенадцати часов и закрыт через секунду после"


@pytest.mark.parametrize("layer", LAYERS)
def test_срок_ссылки_кода_входа_и_сеанса_из_настройки_действует_просроченное_новым_сроком_отказ_403_и_401(lab, tmp_path, layer):
    case = {"link": 60, "login": 30, "session": 300}
    r = run_snippet(lab, tmp_path, ENTRY, {**put(tmp_path, layer, link_ttl_s=60, login_ttl_s=30, session_ttl_s=300), "CASE": json.dumps(case)})
    assert r.data["link"] == [200, 200, 403], "ссылка со сроком в минуту просрочена: 403"
    assert r.data["login"] == [302, 401], "код входа со сроком в полминуты просрочен"
    assert r.data["session"] == [302, 200, 401], "сеанс со сроком в пять минут закрыт"


def test_срок_ссылки_больше_умолчания_ссылка_живёт_дольше_прежних_суток(lab, tmp_path):
    case = {"link": 3 * 86400, "login": 300, "session": 43200}
    r = run_snippet(lab, tmp_path, ENTRY, {**put(tmp_path, "env", link_ttl_s=3 * 86400), "CASE": json.dumps(case)})
    assert r.data["link"] == [200, 200, 403], "срок в трое суток: за секунду до них ссылка жива"


# ══ поведение: проверка моделью ══════════════════════════════════
LLM = r'''
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.environ["WATCH_TOOLS"]), "tests"))
import gatekit
import llm_check

seen = []


def transport(endpoint, payload, timeout):
    seen.append(timeout)
    return json.dumps({"findings": []})


checker = llm_check.from_env()
checker.transport = transport
checker.running = lambda: [checker.local.name]
checker.check_text("образец текста", "а.txt", "txt")
result["timeout"], result["checker_timeout"] = seen[0], checker.timeout
del seen[:]
pages = [gatekit.png_image(8, 8)] * CASE["pages"]
done = checker.check_images(pages, "скан.pdf", total=CASE["pages"])
result["sent"] = len(seen)
result["partial"] = [f.quote for f in done.findings if f.rule == "llm_partial"]
'''


def llm_data(lab, tmp_path, pages=40, **settings):
    env = {**put(tmp_path, "env", **settings), "FLYARCHIVE_LLM_LOCAL_MODEL": "локальная-образец", "CASE": json.dumps({"pages": pages})}
    return run_snippet(lab, tmp_path, LLM, env).data


def test_проверка_моделью_без_настроек_ждёт_сто_восемьдесят_секунд_и_смотрит_двадцать_страниц(lab, tmp_path):
    d = llm_data(lab, tmp_path)
    assert (d["timeout"], d["checker_timeout"], d["sent"]) == (180, 180, 20)
    assert d["partial"] and "20" in d["partial"][0] and "40" in d["partial"][0], "в замечании — сколько страниц проверено и сколько всего"


def test_срок_ожидания_модели_и_число_страниц_скана_из_настроек(lab, tmp_path):
    d = llm_data(lab, tmp_path, llm_timeout_s=45, llm_max_pages=3)
    assert (d["timeout"], d["checker_timeout"], d["sent"]) == (45, 45, 3), "запрос к модели идёт со сроком из настройки, страниц — три"
    assert d["partial"] and "3" in d["partial"][0] and "40" in d["partial"][0]


def test_срок_ожидания_модели_в_один_запрос_верхнее_значение_схемы_без_ограничения_сверху_в_коде(lab, tmp_path):
    high = S.SCHEMA["llm_timeout_s"].high
    d = llm_data(lab, tmp_path, llm_timeout_s=high, llm_max_pages=S.SCHEMA["llm_max_pages"].high, pages=3)
    assert (d["timeout"], d["sent"]) == (high, 3)


# ══ поведение: сервер документов ═════════════════════════════════
SUBMIT = r'''
import base64
import office_server

folder = os.path.join(CASE["dir"], "входящая")                   # папки ещё нет: до записи на диск её не создают
office_server.SUBMIT_DIR = folder
code, answer, facts = office_server.submit_document("образец", "а.bin", base64.b64encode(b"x" * CASE["size"]).decode("ascii"))
result.update(code=code, answer=answer, limit=office_server.SUBMIT_MAX, exists=os.path.exists(folder),
              entries=sorted(os.listdir(folder)) if os.path.isdir(folder) else [])
'''


def submitted(lab, tmp_path, size, **settings):
    r = run_snippet(lab, tmp_path, SUBMIT, {**put(tmp_path, "env", **settings), "CASE": json.dumps({"dir": str(tmp_path / "ящик"), "size": size})})
    return r.data


def test_документ_больше_предела_из_настройки_отказ_413_до_записи_на_диск(lab, tmp_path):
    d = submitted(lab, tmp_path, MIB + 1, submit_max_mb=1)
    assert d["code"] == 413 and d["limit"] == MIB and "предел" in d["answer"]["error"]
    assert d["exists"] is False and d["entries"] == [], "отказ — до того, как во входящей папке что-то создано"


def test_документ_ровно_в_пределе_из_настройки_принимается(lab, tmp_path):
    d = submitted(lab, tmp_path, MIB, submit_max_mb=1)
    assert d["code"] == 200 and d["answer"]["accepted"] is True and d["answer"]["size"] == MIB and len(d["entries"]) == 1


def test_без_настройки_документ_в_мегабайт_и_два_проходит_а_предел_шестнадцать_мегабайт(lab, tmp_path):
    d = submitted(lab, tmp_path, 2 * MIB + 1)
    assert d["code"] == 200 and d["limit"] == 16 * MIB


def test_мегабайт_в_настройке_это_мегабайт_а_не_килобайт(lab, tmp_path):
    d = submitted(lab, tmp_path, 1024 * 1024 // 2, submit_max_mb=1)
    assert d["code"] == 200, "половина мегабайта при пределе в один мегабайт принята"


SWEEP = r'''
import time
import office_server

os.makedirs(office_server.OUT, exist_ok=True)
now = time.time()
for name, hours in CASE["files"].items():
    path = os.path.join(office_server.OUT, name)
    open(path, "w").close()
    os.utime(path, (now - hours * 3600, now - hours * 3600))
office_server.sweep()
result["left"] = sorted(os.listdir(office_server.OUT))
'''
AGES = {"полчаса.txt": 0.5, "два-часа.txt": 2, "двое-суток-с-лишним.txt": 49}


def swept(lab, tmp_path, **settings):
    return run_snippet(lab, tmp_path, SWEEP, {**put(tmp_path, "env", **settings), "CASE": json.dumps({"files": AGES})}).data["left"]


def test_созданные_файлы_без_настройки_живут_двое_суток(lab, tmp_path):
    assert swept(lab, tmp_path) == ["два-часа.txt", "полчаса.txt"]


def test_созданные_файлы_убираются_в_срок_из_настройки(lab, tmp_path):
    assert swept(lab, tmp_path, out_keep_hours=1) == ["полчаса.txt"]
    assert swept(lab, tmp_path / "ещё", out_keep_hours=100) == ["два-часа.txt", "двое-суток-с-лишним.txt", "полчаса.txt"]


# ── тело запроса покрывает размер документа с учётом base64 ─────
ENVELOPE = MIB          # запас на оболочку JSON-RPC, имя файла и экранирование знаков


def encoded(size):
    """Длина base64 для size байт."""
    return 4 * ((size + 2) // 3)


def test_верхняя_граница_размера_документа_покрыта_телом_запроса_сервера_документов_и_шлюза():
    import gateway
    import office_server
    for megabytes in (S.SCHEMA["submit_max_mb"].default, S.SCHEMA["submit_max_mb"].high):
        need = encoded(megabytes * MIB) + ENVELOPE
        assert need <= office_server.BODY_MAX, f"{megabytes} МБ в base64 не проходят в тело запроса сервера документов"
        assert need <= gateway.MAX_BODY, f"{megabytes} МБ в base64 не проходят через шлюз"
    assert office_server.BODY_MAX == OLD_BODY_MAX, "защитный предел тела запроса в код, а не в настройку: растёт не он, а граница настройки"


BODY = r'''
import base64, http.client
import auth, office_server

office_server.SUBMIT_DIR = os.path.join(CASE["dir"], "входящая")
guard = auth.Guard("office")
office_server.Handler.guard = guard
server = office_server.Server(("127.0.0.1", 0), office_server.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
token = guard.store.issue("образец", "full")
statuses = []
for size in CASE["sizes"]:
    payload = json.dumps({"name": "а%d.bin" % size, "content_base64": base64.b64encode(b"x" * size).decode("ascii")}).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=120)
    conn.request("POST", "/submit", payload, {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    reply = conn.getresponse()
    statuses.append((reply.status, json.loads(reply.read()).get("accepted")))
    conn.close()
result["statuses"] = statuses
result["limit"] = office_server.SUBMIT_MAX
'''


def test_документ_максимального_размера_из_схемы_проходит_через_настоящий_запрос_а_на_байт_больше_отклонён(lab, tmp_path):
    high = S.SCHEMA["submit_max_mb"].high
    assert high <= 32, "граница размера документа не ограничена: настоящий запрос такого размера тест не посылает (память)"
    env = {**put(tmp_path, "env", submit_max_mb=high), "CASE": json.dumps({"dir": str(tmp_path / "ящик"), "sizes": [high * MIB, high * MIB + 1]})}
    r = run_snippet(lab, tmp_path, BODY, env)
    assert r.data["limit"] == high * MIB
    assert r.data["statuses"] == [[200, True], [413, None]], "тело запроса не оборвало документ на самой границе настройки"


# ══ запасная облачная модель по умолчанию выключена и в настройках приёмки ══
CLEAN = K.ooxml("docx", "Договор поставки оборудования для серверной.")
OTHER = K.ooxml("docx", "Акт приёмки серверной стойки в машинном зале.")


def write_inbox(cli, data):
    with open(os.path.join(cli.home, "inbox.json"), "w", encoding="utf-8") as f:
        json.dump({"inbox": cli.box, "stable_seconds": 0, **data}, f)


def one_pass(cli, document):
    """Документ во входящую и один проход при молчащей локальной модели (порт без службы) и живом «облаке»: адрес и имя запасной заданы."""
    with open(os.path.join(cli.box, f"док-{len(os.listdir(cli.box))}.docx"), "wb") as f:
        f.write(document)
    return cli("inbox", "run", local="http://127.0.0.1:9", FLYARCHIVE_LLM_CLOUD_MODEL="cloud-test")


def test_умолчание_запасной_модели_в_настройках_приёмки_выключено():
    assert B.DEFAULTS["cloud"] is False
    assert B.DEFAULTS["llm"] is True, "проверка локальной моделью по-прежнему включена по умолчанию"


def test_новый_архив_без_inbox_json_получает_выключенную_запасную_модель(tmp_path):
    assert not (tmp_path / "inbox.json").exists()
    cfg = B.load_config(str(tmp_path))
    assert cfg["cloud"] is False and cfg["llm"] is True


def test_архив_с_записанной_включённой_запасной_моделью_ведёт_себя_как_раньше(tmp_path):
    (tmp_path / "inbox.json").write_text(json.dumps({"cloud": True}), encoding="utf-8")
    assert B.load_config(str(tmp_path))["cloud"] is True
    (tmp_path / "inbox.json").write_text(json.dumps({"cloud": False}), encoding="utf-8")
    assert B.load_config(str(tmp_path))["cloud"] is False


def test_старый_inbox_json_без_ключа_cloud_получает_новое_умолчание(tmp_path):
    (tmp_path / "inbox.json").write_text(json.dumps({"period": 10, "llm": False}), encoding="utf-8")
    cfg = B.load_config(str(tmp_path))
    assert cfg["cloud"] is False and cfg["period"] == 10 and cfg["llm"] is False


def test_состояние_нового_архива_запасная_модель_выключена(cli):
    code, out, err = cli("inbox", "status", "--json")
    assert code == 0, err
    assert json.loads(out)["cloud"] is False


def test_настройка_приёмки_записывает_запасную_модель_выключенной_явно_и_не_создаёт_settings_json(cli):
    code, out, err = cli("inbox", "set", "--path", cli.box, "--period", "5")
    assert code == 0, err
    assert cli.config()["cloud"] is False and "запасная облачная модель: нет" in out
    assert not os.path.exists(os.path.join(cli.home, "settings.json")), "настройки приёмки остаются в inbox.json, общий файл не создаётся"


def test_новый_архив_проверка_при_недоступной_локальной_модели_к_запасной_не_обращается_пока_её_не_включили(cli):
    lance(cli.home)
    write_inbox(cli, {})                                       # ключа cloud в файле нет вовсе: новый архив без единой правки
    code, out, err = one_pass(cli, CLEAN)
    assert code == 0, err
    assert "На утверждение: 1" in out, "локальной нет, запасной нет: документ ждёт человека"
    assert cli.cloud["requests"] == [] and cli.model["requests"] == [], "ни одного обращения к запасной модели: она не включена"
    assert cli("inbox", "set", "--cloud", "on")[0] == 0 and cli.config()["cloud"] is True
    code, out, err = one_pass(cli, OTHER)
    assert code == 0, err
    assert len(cli.cloud["posts"]()) >= 1, "владелец включил запасную и задал адрес и имя: теперь она вызывается"


def test_новый_архив_запасная_не_вызывается_если_адрес_и_имя_заданы_а_включения_нет(cli):
    lance(cli.home)
    write_inbox(cli, {"cloud": False})
    one_pass(cli, CLEAN)
    assert cli.cloud["requests"] == []


def test_архив_с_записанным_cloud_true_идёт_к_запасной_как_раньше(cli):
    lance(cli.home)
    write_inbox(cli, {"cloud": True})
    code, out, err = one_pass(cli, CLEAN)
    assert code == 0, err
    assert len(cli.cloud["posts"]()) >= 1
    assert json.loads(cli("inbox", "status", "--json")[1])["cloud"] is True


def test_настройки_приёмки_не_перебиваются_переменными_окружения(cli):
    write_inbox(cli, {"period": 10, "llm": True, "cloud": False})
    env = {"FLYARCHIVE_PERIOD": "5", "FLYARCHIVE_LLM": "off", "FLYARCHIVE_CLOUD": "on", "FLYARCHIVE_THRESHOLD": "99", "FLYARCHIVE_VISION": "off",
           "FLYARCHIVE_INBOX_DIR": "/нигде"}
    code, out, err = cli("inbox", "status", "--json", **env)
    s = json.loads(out)
    assert code == 0, err
    assert (s["period"], s["llm"], s["cloud"], s["threshold"], s["vision"], s["inbox"]) == (10, True, False, 20, True, cli.box), \
        "значение из inbox.json перебито переменной окружения"


# ══ каталог песочницы и каталог служб из настроек ════════════════
def run_shell(tmp_path, monkeypatch, name="оболочка"):
    """Команда `run` как модуль с подставной песочницей: что за каталог состояния она передала."""
    mod = load_cli()
    seen = {}
    monkeypatch.setattr(sandbox, "run", lambda command, **kw: (seen.update(kw), 0)[1])
    monkeypatch.setattr(mod.tokens, "HOME", str(tmp_path / "архив"))
    guard = A.Guard("тест", home=str(tmp_path / "архив"))
    assert mod.run_sandboxed(guard, args(command=["--", "true"], name=name, dir=str(tmp_path), env=[], ro=[])) == 0
    return seen


def test_каталог_песочницы_без_настройки_из_схемы_а_XDG_DATA_HOME_не_читается(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "чужой-xdg"))
    monkeypatch.delenv("FLYARCHIVE_SANDBOX_DIR", raising=False)
    seen = run_shell(tmp_path, monkeypatch)
    assert seen["state"] == os.path.join(os.environ["HOME"], ".local", "share", "flyarchive-sandbox", "оболочка")
    assert "чужой-xdg" not in json.dumps(seen, default=str)


def test_каталог_песочницы_из_настройки_каждая_оболочка_в_своём_подкаталоге(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "чужой-xdg"))
    monkeypatch.setenv("FLYARCHIVE_SANDBOX_DIR", str(tmp_path / "своя-песочница"))
    assert run_shell(tmp_path, monkeypatch, "первая")["state"] == str(tmp_path / "своя-песочница" / "первая")
    assert run_shell(tmp_path, monkeypatch, "вторая")["state"] == str(tmp_path / "своя-песочница" / "вторая")


@pytest.mark.foreign_name
def test_каталог_песочницы_из_чужой_переменной_не_берётся(tmp_path, monkeypatch):
    monkeypatch.delenv("FLYARCHIVE_SANDBOX_DIR", raising=False)
    monkeypatch.setenv(FOREIGN_PREFIX + "SANDBOX_DIR", str(tmp_path / "чужая-песочница"))
    seen = run_shell(tmp_path, monkeypatch)
    assert seen["state"] == os.path.join(os.environ["HOME"], ".local", "share", "flyarchive-sandbox", "оболочка"), "каталог взят из переменной с чужой приставкой"
    assert "чужая-песочница" not in json.dumps(seen, default=str)



UNITS = r'''
import inbox
result["units_dir"] = inbox.units_dir()
if CASE.get("install"):
    class Done:
        returncode = 0
        stderr = ""

    calls = []
    home = os.environ["FLYARCHIVE_HOME"]
    result["trouble"] = inbox.install_timer(home, 5, run=lambda *a: (calls.append(a), Done())[1])
    result["files"] = sorted(os.listdir(inbox.units_dir()))
    result["timer"] = inbox.status(home)["timer"]
    result["calls"] = [" ".join(a) for a in calls]
'''


def test_каталог_служб_без_настройки_из_схемы(lab, tmp_path):
    r = run_snippet(lab, tmp_path, UNITS, put(tmp_path, "env"))
    assert r.data["units_dir"] == str(lab.home / ".config" / "systemd" / "user")
    untouched(r)


@pytest.mark.parametrize("layer", LAYERS)
def test_каталог_служб_из_настройки_таймер_пишется_в_него_и_состояние_видит_его_там(lab, tmp_path, layer):
    units = tmp_path / "свои-службы"
    r = run_snippet(lab, tmp_path, UNITS, {**put(tmp_path, layer, units_dir=str(units)), "CASE": json.dumps({"install": True})})
    assert r.data["units_dir"] == str(units) and r.data["trouble"] is None
    assert r.data["files"] == sorted(os.listdir(units)) == sorted([B.SERVICE, B.TIMER])
    assert r.data["timer"] is True and r.data["calls"] == ["daemon-reload", "enable --now " + B.TIMER, "restart " + B.TIMER]
    untouched(r, why="службы записаны в каталог из настройки, а не в каталог по умолчанию")


# ══ в схеме нет настроек, которых никто не читает ═════════════════
# Исключений нет (FR-104): настройки оболочки читают установка (`tools/install.py`, объект настроек — из `settings.load`) и запускалки на shell
# (`python3 tools/settings.py --get КЛЮЧ`). Словарь оставлен пустым, чтобы вернуть настройку в список исключений было видно в тесте.
INSTALLER_ONLY = {}
SETTINGS_FUNCTIONS = ("load", "startup")


def is_settings_call(node):
    """settings.load(...) или settings.startup(...): вызов, который возвращает объект настроек."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in SETTINGS_FUNCTIONS
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "settings")


def stored_names(target):
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}


def settings_reads(source, keys):
    """Ключи из keys, которые исходник читает у объекта настроек: `объект["ключ"]`, `объект.ключ` и ключ строкой в вызов функции настроек
    (`settings.функция("ключ")`, `объект.метод("ключ")`). Объект настроек — имя, которому в этой же области присвоено только settings.load(...)
    или settings.startup(...), либо сам такой вызов. Имя из внешней области действует во вложенных, пока его не перекрыли присваиванием,
    параметром, циклом, with, импортом или except: чужой словарь с таким же именем и ключом ничего не прочтёт. Строки, комментарии,
    докстроки и переменные окружения чтением не считаются."""
    found = set()

    def children(scope):
        """Узлы области без вложенных функций, лямбд и классов: их области разбираются отдельно."""
        stack, out = list(ast.iter_child_nodes(scope)), []
        while stack:
            node = stack.pop()
            out.append(node)
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                stack.extend(ast.iter_child_nodes(node))
        return out

    def visit(scope, inherited):
        nodes = children(scope)
        mine, other = {}, set()
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            every = scope.args
            other |= {a.arg for a in every.posonlyargs + every.args + every.kwonlyargs + [x for x in (every.vararg, every.kwarg) if x]}
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and getattr(node, "value", None) is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for name in stored_names(target):
                        mine.setdefault(name, []).append(isinstance(target, ast.Name) and is_settings_call(node.value))
            elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.NamedExpr, ast.comprehension)):
                other |= stored_names(node.target)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    other |= stored_names(item.optional_vars) if item.optional_vars else set()
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                other |= {(a.asname or a.name).split(".")[0] for a in node.names}
            elif isinstance(node, ast.ExceptHandler) and node.name:
                other.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                other.add(node.name)
        own = {name for name, flags in mine.items() if all(flags)}
        other |= {name for name in mine if name not in own}
        names = (inherited - other) | own

        def is_object(node):
            return (isinstance(node, ast.Name) and node.id in names) or is_settings_call(node)

        for node in nodes:
            if isinstance(node, ast.Subscript) and is_object(node.value):
                if isinstance(node.slice, ast.Constant) and node.slice.value in keys:
                    found.add(node.slice.value)
            elif isinstance(node, ast.Attribute) and is_object(node.value) and node.attr in keys:
                found.add(node.attr)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                base = node.func.value
                if (isinstance(base, ast.Name) and base.id == "settings") or is_object(base):
                    found.update(a.value for a in node.args if isinstance(a, ast.Constant) and a.value in keys)
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                visit(node, names)

    visit(ast.parse(source), set())
    return found


KEYS = {"alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta"}


@pytest.mark.parametrize("source, want", [
    ('import settings\n_S = settings.startup()\nx = _S["alpha"]\n', {"alpha"}),
    ('import settings\ndef f(env):\n    s = settings.load(env=env)\n    return s["beta"], s.gamma\n', {"beta", "gamma"}),
    ('import settings\nx = settings.load(home=h)["delta"]\ny = settings.startup().eps\n', {"delta", "eps"}),
    ('import settings\nname = settings.env_name("zeta")\n', {"zeta"}),
    ('import settings\n_S = settings.startup()\nok = _S.source("eta") != "default"\n', {"eta"}),
    ('import settings\n_S = settings.startup()\ndef f():\n    return _S["theta"]\n', {"theta"}),
    ('import settings\ndef f():\n    s = settings.load()\n    def g():\n        return s["alpha"]\n    return g\n', {"alpha"}),
    ('import settings\nlambda_ = settings.startup()\nvalue = (lambda: lambda_["beta"])()\n', {"beta"}),
])
def test_детектор_чтения_настроек_находит_каждую_форму_чтения(source, want):
    assert settings_reads(source, KEYS) == want


@pytest.mark.parametrize("source", [
    'd = {"alpha": 1}\nx = d["alpha"]\n',                                                      # чужой словарь с таким же ключом
    'import inbox\ns = inbox.status(h)\nx = s["alpha"]\n',                                      # объект не из настроек
    'import settings\n"""читает alpha и beta"""\n# gamma\nx = "delta"\n',                      # докстрока, комментарий, голая строка
    'import os\nx = os.environ.get("FLYARCHIVE_ALPHA")\n',                                      # переменная окружения мимо настроек
    'import settings\ndef f(s):\n    return s["alpha"]\n',                                      # параметр с именем объекта настроек
    'import settings\n_S = settings.startup()\ndef f():\n    _S = {"alpha": 1}\n    return _S["alpha"]\n',            # внутри функции имя перекрыто
    'import settings\n_S = settings.startup()\ndef f(items):\n    for _S in items:\n        pass\n    return _S["alpha"]\n',   # перекрыто циклом
    'import settings\ns = settings.load()\ns = {"alpha": 1}\nx = s["alpha"]\n',                 # присвоено и настройками, и чужим: не считается
    'import settings\ndef f():\n    s = settings.load()\ndef g():\n    return s["alpha"]\n',    # локальное имя другой функции не видно
    'import settings\nx = print("alpha")\ny = foo.bar("beta")\n',                                # строка в чужую функцию
    'import settings\n_S = settings.startup()\nx = _S.нет_такого\ny = _S["чужой"]\n',          # ключа нет среди настроек
    'import settings\nclass K:\n    alpha = 1\nx = K.alpha\n',                                  # атрибут класса
])
def test_детектор_чтения_настроек_не_засчитывает_то_что_чтением_не_является(source):
    assert settings_reads(source, KEYS) == set()


def shell_reads(text, keys):
    """Ключи из keys, которые скрипт shell читает командой `python3 …/settings.py --get КЛЮЧ` (запускалки оболочки). Строка разбирается как слова
    shell: комментарий и слова в кавычках (`echo "… settings.py --get ключ"`) чтением не считаются, ключ должен стоять в команде буквально
    (`--get "$1"` ничего не называет), а командой должна быть именно программа настроек."""
    import shlex
    found = set()
    for line in text.splitlines():
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        for i in range(len(words) - 2):
            name = re.match(r"([a-z][a-z0-9_]*)\W*$", words[i + 2])
            if os.path.basename(words[i]) == "settings.py" and words[i + 1] == "--get" and name and name.group(1) in keys:
                found.add(name.group(1))
    return found


@pytest.mark.parametrize("text, want", [
    ('BASE=$(python3 "$TOOLS/settings.py" --get alpha)\n', {"alpha"}),
    ('python3 settings.py --get beta | head -n 1\n', {"beta"}),
    ('v=$(python3 "$T/settings.py" --get gamma); echo "$v"\n', {"gamma"}),
    ("names=$(python3 '/opt/x/tools/settings.py' --get delta)\n", {"delta"}),
    ('a=$(python3 "$T/settings.py" --get alpha)\nb=$(python3 "$T/settings.py" --get eps)\n', {"alpha", "eps"}),
    ('done < <(python3 "$T/settings.py" --get zeta)\n', {"zeta"}),
])
def test_детектор_запускалок_находит_каждую_форму_чтения_через_get(text, want):
    assert shell_reads(text, KEYS) == want


@pytest.mark.parametrize("text", [
    '# python3 settings.py --get alpha\n',                                                 # комментарий
    'echo "python3 settings.py --get alpha"\n',                                            # слова в кавычках
    'x=$(python3 other.py --get alpha)\n',                                                 # не программа настроек
    'x=$(python3 "$T/settings.py" --get неизвестный)\n',                                  # ключа нет среди настроек
    'x=$(python3 "$T/settings.py" --get nope_not_a_key)\n',
    'get() { python3 "$T/settings.py" --get "$1"; }\n',                                    # ключ не назван буквально
    'x=$(python3 "$T/settings.py" --json)\n',                                              # другой режим
    'x=$(python3 "$T/settings.py")\ny=--get alpha\n',                                      # --get без программы настроек
    'echo ключ alpha\n',
])
def test_детектор_запускалок_не_засчитывает_то_что_чтением_не_является(text):
    assert shell_reads(text, KEYS) == set()


def shell_files():
    """Запускалки и скрипты shell в tools: файл с шебангом на sh или bash в первой строке."""
    found = []
    for name in sorted(os.listdir(TOOLS)):
        path = os.path.join(TOOLS, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                if re.match(rb"#!.*\b(?:ba)?sh\b", f.readline()):
                    found.append(name)
    return found


def core_reads():
    keys = set(S.SCHEMA)
    found = set()
    for name in core_files():
        if name != "settings.py" and name not in EXCLUDED:
            found |= settings_reads(read_core(name), keys)
    for name in shell_files():
        found |= shell_reads(read_core(name), keys)
    return found


def dead_keys(read=None):
    read = core_reads() if read is None else read
    return sorted(key for key in S.SCHEMA if key not in read and key not in INSTALLER_ONLY)


def test_в_схеме_нет_настроек_которые_никто_не_читает():
    assert dead_keys() == [], "настройки, которых не читают ни ядро, ни установка, ни запускалки"


def test_список_исключений_пуст():
    assert INSTALLER_ONLY == {}, "в списке исключений остались настройки: установка и запускалки читают настройки сами"


def test_запускалки_для_детектора_найдены_и_читают_настройки_только_через_get():
    shells = shell_files()
    assert {"dsh-web-start", "dsh-url"} <= set(shells), shells
    for name in shells:
        text = read_core(name)
        assert "FLYARCHIVE_DSH_PORT" not in text and "DSH_WEB_PORT" not in text, f"{name}: порт оболочки читается мимо настроек"
    assert {"home", "dsh_port", "mcp_port", "env_file", "env_names", "dsh_patches", "dsh_llm_key_env", "llm_key_file"} <= core_reads_shell_only()


def core_reads_shell_only():
    keys, found = set(S.SCHEMA), set()
    for name in shell_files():
        found |= shell_reads(read_core(name), keys)
    return found


def test_установка_читает_настройки_каталогов_служб_профиля_и_портов():
    keys = set(S.SCHEMA)
    read = settings_reads(read_core("install.py"), keys)
    assert {"home", "units_dir", "dsh_profile", "dsh_port", "search_port", "office_port", "mcp_port", "gateway_port", "gateway_bind",
            "public_url"} <= read, read


def test_тест_мёртвых_настроек_замечает_ключ_схемы_которого_никто_не_читает(monkeypatch):
    spec = S.Spec("probe_dead", "int", 1, 1, 2, None, "Пробная настройка, которую никто не читает")
    monkeypatch.setitem(S.SCHEMA, "probe_dead", spec)
    assert dead_keys() == ["probe_dead"]


def test_тест_мёртвых_настроек_замечает_ключ_которого_ядро_перестало_читать():
    read = core_reads()
    assert dead_keys(read - {"units_dir"}) == ["units_dir"]
    assert dead_keys(read - {"sandbox_dir"}) == ["sandbox_dir"]


def test_все_настройки_схемы_читает_ядро_установка_или_запускалки_с_каждой_стороны():
    read = core_reads()
    assert read <= set(S.SCHEMA)
    assert set(S.SCHEMA) == read, set(S.SCHEMA) ^ read


def test_каждый_из_пятнадцати_пределов_и_каталоги_читаются_ядром_а_не_только_схемой():
    read = core_reads()
    assert {limit.key for limit in LIMITS} | {"units_dir", "sandbox_dir"} <= read

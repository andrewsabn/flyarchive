"""Описания инструментов и справка ключей называют только условные имена баз и примеры из набора образцов `examples/`.

Читатель с пустой машины видит примеры в описаниях инструментов для модели и в справке команды, и они должны быть его собственными: имена баз условные
(`tasks`, `wiki`, `входящие`), схемы — из образцов `examples/` (приложение для записи лодочных прогулок: «Экраны», «Журнал прогулок», «Хранилище»).
Раздел базы назван словами «каталог первого уровня, ящик или номер пачки», формат даты `since` везде в трёх видах (`ГГГГ`, `ГГГГ-ММ`, `ГГГГ-ММ-ДД`), предел
числа находок в подсказке — настройка `api_max_k`. Договор инструментов (имена и типы полей) держит тест ниже. Открытый тест не хранит списка слов, которых в описаниях
быть не должно: он держит то, что в описаниях есть (образцы только из названного набора), а запретные слова ищет закрытая часть.
"""
import json
import os
import re
import subprocess
import sys

import pytest

import mcp_server as MCP
import office_server as O
import search as S
import webui as W

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLYARCHIVE = os.path.join(ROOT, "tools", "flyarchive")
SEARCH_PY = os.path.join(ROOT, "tools", "search.py")
SECTION = "раздел базы: каталог первого уровня, ящик или номер пачки"
SINCE = ("ГГГГ", "ГГГГ-ММ", "ГГГГ-ММ-ДД")
BASES = {"tasks", "wiki", "входящие"}                                           # условные имена баз в примерах
SAMPLE_APP = ("Экраны", "Данные", "Маршрут", "Журнал прогулок", "Хранилище", "Экран", "Отчёт")   # узлы и слои приложения для записи прогулок
SCHEME_FIELDS = ("groups", "edges", "dot")
EXAMPLE = re.compile(r"\[\{.*\}\]|\[\[.*\]\]|digraph G \{.*\}")                 # образец схемы в описании: слои, связи или исходник DOT
FOR_EXAMPLE = re.compile(r"например\s+([\w-]+(?:\s+или\s+[\w-]+)*)")            # «например tasks или входящие»
LIMIT_TO = re.compile(r"Ограничить базой:\s*([\w-]+)")


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from strings(key)
            yield from strings(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from strings(item)


def descriptions():
    """Всё, что читает модель: схемы инструментов MCP, спецификации серверов документов и поиска."""
    return {"mcp": [t for t in MCP.TOOLS], "office": O.spec(), "web": W.openapi()}


def run(*argv):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "FLYARCHIVE_HOME": os.path.join(os.environ.get("TMPDIR", "/tmp"), "flyarchive-описания-проверка")}
    for name in [n for n in env if n.startswith("FLYARCHIVE_") and n != "FLYARCHIVE_HOME"]:
        del env[name]
    r = subprocess.run([sys.executable, *argv], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    return " ".join(r.stdout.split())


def has_all_since_forms(text):
    return (re.search(r"ГГГГ(?![-\w])", text) is not None and re.search(r"ГГГГ-ММ(?!-ДД)", text) is not None
            and re.search(r"ГГГГ-ММ-ДД", text) is not None)


# ── договор не меняется ─────────────────────────────────────────
MCP_CONTRACT = {
    "search_archive": ({"q": "string", "k": "integer", "source": "string", "space": "string", "since": "string"}, ["q"]),
    "read_document": ({"path": "string", "pages": "string"}, ["path"]),
    "read_document_rich": ({"path": "string", "pages": "string"}, ["path"]),
    "make_landscape": ({"title": "string", "direction": "string", "groups": "array", "edges": "array"}, ["groups"]),
    "make_diagram": ({"dot": "string", "fmt": "string"}, ["dot"]),
    "make_chart": ({"kind": "string", "title": "string", "ylabel": "string", "labels": "array", "series": "object"}, ["labels", "series"]),
    "make_document": ({"kind": "string", "title": "string", "blocks": "array"}, ["kind"]),
    "submit_document": ({"name": "string", "content_base64": "string"}, ["name", "content_base64"]),
}
OFFICE_CONTRACT = {
    "read_document": ("get", "/read", [("path", True, "string"), ("pages", False, "string")], None),
    "read_document_rich": ("get", "/rich", [("path", True, "string"), ("pages", False, "string")], None),
    "make_chart": ("post", "/chart", [], ({"kind": "string", "title": "string", "ylabel": "string", "labels": "array", "series": "object"},
                                         ["labels", "series"])),
    "make_diagram": ("post", "/diagram", [], ({"dot": "string", "fmt": "string"}, ["dot"])),
    "make_landscape": ("post", "/landscape", [], ({"title": "string", "direction": "string", "groups": "array", "edges": "array"}, ["groups"])),
    "make_document": ("post", "/document", [], ({"kind": "string", "title": "string", "blocks": "array"}, ["kind"])),
}
WEB_CONTRACT = [("q", True, "string"), ("k", False, "integer"), ("source", False, "string"), ("space", False, "string"), ("since", False, "string")]


def test_договор_инструментов_mcp_имена_и_типы_полей_прежние():
    live = {t["name"]: ({k: v["type"] for k, v in t["inputSchema"]["properties"].items()}, sorted(t["inputSchema"].get("required", [])))
            for t in MCP.TOOLS}
    assert live == {name: (props, sorted(required)) for name, (props, required) in MCP_CONTRACT.items()}


def test_договор_сервера_документов_имена_и_типы_полей_прежние():
    live = {}
    for path, methods in O.spec()["paths"].items():
        for method, op in methods.items():
            params = [(p["name"], p["required"], p["schema"]["type"]) for p in op.get("parameters", [])]
            body = None
            if "requestBody" in op:
                schema = op["requestBody"]["content"]["application/json"]["schema"]
                body = ({k: v["type"] for k, v in schema["properties"].items()}, sorted(schema.get("required", [])))
            live[op["operationId"]] = (method, path, params, body)
    assert live == {name: (m, p, params, None if body is None else (body[0], sorted(body[1]))) for name, (m, p, params, body) in OFFICE_CONTRACT.items()}


def test_договор_поиска_для_оболочек_имена_и_типы_параметров_прежние():
    (op,) = [op for methods in W.openapi()["paths"].values() for op in methods.values()]
    assert op["operationId"] == "search_archive"
    assert [(p["name"], p["required"], p["schema"]["type"]) for p in op["parameters"]] == WEB_CONTRACT


# ── условные имена баз и образцы из examples/ ───────────────────
def scheme_fields(node):
    """Описания полей, в которых стоит образец схемы: `groups` (слои), `edges` (связи), `dot` (исходник DOT) — у любого инструмента и любого сервера."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SCHEME_FIELDS and isinstance(value, dict) and isinstance(value.get("description"), str):
                yield value["description"]
            yield from scheme_fields(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from scheme_fields(item)


def examples_in(text):
    """Образцы схем, найденные в тексте описания: слои, связи, исходник DOT."""
    return EXAMPLE.findall(text)


def foreign_words(example):
    """Русские слова образца, которых нет среди узлов и слоёв приложения для записи прогулок; пустой список — образец только из набора."""
    for word in sorted(SAMPLE_APP, key=len, reverse=True):
        example = example.replace(word, " ")
    return re.findall(r"[А-Яа-яЁё]+", example)


def test_проверка_образцов_находит_чужое_слово_и_не_трогает_образцы_приложения_для_записи_прогулок():
    assert foreign_words("[{name: Экраны, nodes: [Маршрут, Журнал прогулок]}, {name: Данные, nodes: [Хранилище]}]") == []
    assert foreign_words("digraph G { Экран -> Хранилище -> Отчёт }") == []
    assert foreign_words("[[Маршрут, Касса], [Журнал прогулок, Хранилище]]") == ["Касса"]
    assert examples_in("Слои: [{name: A, nodes: [B]}]") == ["[{name: A, nodes: [B]}]"]
    assert FOR_EXAMPLE.findall("база индекса, например tasks или входящие, и всё") == ["tasks или входящие"]
    assert LIMIT_TO.findall("Ограничить базой: входящие; можно несколько") == ["входящие"]


@pytest.mark.parametrize("which", ["mcp", "office", "web"])
def test_образцы_схем_в_описаниях_только_из_набора_образцов(which):
    found = [example for text in scheme_fields(descriptions()[which]) for example in examples_in(text)]
    assert [(example, foreign_words(example)) for example in found if foreign_words(example)] == []
    assert len(found) >= {"mcp": 2, "office": 3, "web": 0}[which], f"образцы схем в описаниях ({which}) не нашлись: {found}"


def test_пример_схемы_в_описаниях_взят_из_образцов_приложения_для_записи_прогулок():
    for text in (" ".join(strings(MCP.BY_NAME["make_landscape"])), " ".join(strings(O.spec()["paths"]["/landscape"]))):
        assert "Экраны" in text and "Журнал прогулок" in text, text


def test_имена_баз_в_описаниях_и_справке_условные():
    shown = {"mcp": " ".join(strings(descriptions()["mcp"])), "web": " ".join(strings(descriptions()["web"])),
             "search.py": run(SEARCH_PY, "--help"), "flyarchive search": run(FLYARCHIVE, "search", "--help"),
             "vision backfill": run(FLYARCHIVE, "vision", "backfill", "--help")}
    for where, text in shown.items():
        named = {name for match in FOR_EXAMPLE.findall(text) for name in re.split(r"\s+или\s+", match)}
        named |= set(LIMIT_TO.findall(text))
        assert named <= BASES, (where, sorted(named - BASES))
    assert {name for match in FOR_EXAMPLE.findall(shown["vision backfill"]) for name in re.split(r"\s+или\s+", match)} == {"tasks", "входящие"}
    assert set(LIMIT_TO.findall(shown["mcp"])) == {"входящие"}


def test_справка_search_py_и_команды_search_называет_раздел_базы_теми_же_словами():
    assert SECTION in run(SEARCH_PY, "--help")
    assert SECTION in run(FLYARCHIVE, "search", "--help")
    assert "только эта база индекса, например tasks или входящие" in run(FLYARCHIVE, "vision", "backfill", "--help")


def test_раздел_базы_описан_одними_словами_в_инструментах_и_справке():
    mcp = MCP.BY_NAME["search_archive"]["inputSchema"]["properties"]["space"]["description"]
    (web,) = [p["description"] for p in W.openapi()["paths"]["/api/search"]["get"]["parameters"] if p["name"] == "space"]
    assert SECTION in mcp.lower() and SECTION in web.lower()
    assert SECTION in run(SEARCH_PY, "--help").lower()


# ── дата since: три вида везде ──────────────────────────────────
def test_формат_since_везде_в_трёх_видах():
    mcp = MCP.BY_NAME["search_archive"]["inputSchema"]["properties"]["since"]["description"]
    (web,) = [p["description"] for p in W.openapi()["paths"]["/api/search"]["get"]["parameters"] if p["name"] == "since"]
    shown = {"mcp": mcp, "web": web, "search.py": run(SEARCH_PY, "--help"), "flyarchive search": run(FLYARCHIVE, "search", "--help")}
    for where, text in shown.items():
        assert has_all_since_forms(text), (where, text)


def test_отказ_поиска_по_негодной_дате_называет_те_же_три_вида():
    with pytest.raises(S.BadQuery) as e:
        S.search("запрос", since="вчера", today="20260101")
    assert all(form in str(e.value) for form in ("2026", "2026-08", "2026-08-31"))


# ── предел находок — из настройки ───────────────────────────────
def test_подсказка_числа_находок_в_описании_поиска_берёт_предел_из_настройки(monkeypatch):
    monkeypatch.setattr(W, "API_MAX_K", 7)
    (k,) = [p["description"] for p in W.openapi()["paths"]["/api/search"]["get"]["parameters"] if p["name"] == "k"]
    assert "1-7" in k and "50" not in k
    monkeypatch.setattr(W, "API_MAX_K", 33)
    (k,) = [p["description"] for p in W.openapi()["paths"]["/api/search"]["get"]["parameters"] if p["name"] == "k"]
    assert "1-33" in k


def test_подсказка_числа_находок_в_инструменте_mcp_тоже_из_настройки():
    k = MCP.BY_NAME["search_archive"]["inputSchema"]["properties"]["k"]["description"]
    assert f"1-{MCP._SETTINGS['api_max_k']}" in k

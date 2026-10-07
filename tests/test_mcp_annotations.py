"""Пометки инструментов MCP: поле annotations в ответе tools/list (FR-93).

Клиент, который спрашивает у человека подтверждение на инструменты без пометок,
читающие пропускает сам. Служебные поля route и need наружу не уходят.
"""
import json

import pytest

import mcp_server as M
import tokens as T

READ = {"search_archive", "read_document", "read_document_rich"}
MAKE = {"make_landscape", "make_diagram", "make_chart", "make_document", "submit_document"}
SPEC_HINTS = {"title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
PUBLIC = {"name", "description", "inputSchema", "annotations"}


def rpc(method, id=1):
    return {"jsonrpc": "2.0", "method": method, "id": id}


def problems(tools):
    """Что не так с пометками: правило «уровень → readOnlyHint» целиком в одном месте, чтобы его можно было проверить на порче."""
    found = []
    for t in tools:
        ann, name = t.get("annotations"), t.get("name")
        if not isinstance(ann, dict) or not ann:
            found.append(f"{name}: нет annotations")
            continue
        if not set(ann) <= SPEC_HINTS or not all(isinstance(v, bool) for k, v in ann.items() if k != "title"):
            found.append(f"{name}: поля не по спецификации MCP: {ann}")
        if ann.get("openWorldHint") is not False:
            found.append(f"{name}: архив замкнутый мир, нужно openWorldHint: false")
        if t.get("need") == "read" and ann.get("readOnlyHint") is not True:
            found.append(f"{name}: уровень read, а readOnlyHint не true")
        elif t.get("need") == "full" and (ann.get("readOnlyHint") is not False or ann.get("destructiveHint") is not False):
            found.append(f"{name}: уровень full, нужны readOnlyHint false и destructiveHint false")
        elif t.get("need") not in ("read", "full"):
            found.append(f"{name}: уровень {t.get('need')!r} — решите, какие пометки ему положены, и допишите правило")
    return found


# ── правило держится тестом ─────────────────────────────────────
def test_у_каждого_инструмента_есть_пометки_и_они_подчиняются_правилу_уровня():
    assert problems(M.TOOLS) == []


def test_читающие_инструменты_помечены_только_для_чтения_и_без_выхода_в_мир():
    got = {t["name"]: t["annotations"] for t in M.TOOLS if t["need"] == "read"}
    assert set(got) == READ
    assert all(a == {"readOnlyHint": True, "openWorldHint": False} for a in got.values())


def test_создающие_инструменты_помечены_не_читающими_и_не_разрушающими():
    got = {t["name"]: t["annotations"] for t in M.TOOLS if t["need"] == "full"}
    assert set(got) == MAKE
    assert all(a == {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False} for a in got.values())


def test_правило_замечает_новый_инструмент_без_пометок():
    new = {"name": "новый", "description": "d", "inputSchema": {"type": "object"}, "need": "read"}
    assert any("нет annotations" in p for p in problems(M.TOOLS + [new]))
    assert any("нет annotations" in p for p in problems(M.TOOLS + [{**new, "annotations": {}}]))


def test_правило_замечает_читающий_инструмент_с_пометкой_записи_и_наоборот():
    wrong_read = {**M.TOOLS[0], "annotations": {"readOnlyHint": False, "openWorldHint": False}}
    wrong_make = {**M.TOOLS[-1], "annotations": {"readOnlyHint": True, "openWorldHint": False}}
    assert any("уровень read" in p for p in problems([wrong_read]))
    assert any("уровень full" in p for p in problems([wrong_make]))
    open_world = {**M.TOOLS[0], "annotations": {"readOnlyHint": True, "openWorldHint": True}}
    assert any("openWorldHint" in p for p in problems([open_world]))
    unknown = {**M.TOOLS[0], "annotations": {"readOnlyHint": True, "openWorldHint": False, "safe": True}}
    assert any("по спецификации" in p for p in problems([unknown]))
    local_tool = {**M.TOOLS[0], "need": "local", "annotations": {"readOnlyHint": True, "openWorldHint": False}}
    assert any("уровень" in p for p in problems([local_tool]))


# ── что уходит наружу ───────────────────────────────────────────
@pytest.mark.parametrize("level, names", [("read", READ), ("full", READ | MAKE), ("local", READ | MAKE)])
def test_список_для_уровня_содержит_пометки_и_не_содержит_служебных_полей(level, names):
    out = M.handle(rpc("tools/list"), T.Client("проверка", level))["result"]["tools"]
    assert {t["name"] for t in out} == names
    for t in out:
        assert set(t) == PUBLIC, t["name"]
        assert t["annotations"] == M.BY_NAME[t["name"]]["annotations"]
    assert not any(k in t for t in out for k in ("route", "need"))


def test_читающий_токен_видит_только_читающие_пометки_а_полный_и_те_и_другие():
    read = M.handle(rpc("tools/list"), T.Client("р", "read"))["result"]["tools"]
    assert [t["annotations"]["readOnlyHint"] for t in read] == [True] * 3
    full = M.handle(rpc("tools/list"), T.Client("п", "full"))["result"]["tools"]
    assert sorted(t["name"] for t in full if t["annotations"]["readOnlyHint"]) == sorted(READ)
    assert sorted(t["name"] for t in full if not t["annotations"]["readOnlyHint"]) == sorted(MAKE)


def test_пометки_уходят_в_json_ответа_а_служебных_полей_в_нём_нет():
    text = json.dumps(M.handle(rpc("tools/list"), T.Client("п", "full")), ensure_ascii=False)
    assert text.count('"readOnlyHint": true') == 3 and text.count('"readOnlyHint": false') == 5
    assert text.count('"openWorldHint": false') == 8 and text.count('"destructiveHint": false') == 5
    assert '"route"' not in text and '"need"' not in text and "http://127.0.0.1" not in text


def test_по_http_tools_list_читающего_и_полного_токена(serve, fetch, access, monkeypatch):
    monkeypatch.setattr(M.Handler, "guard", access.guard("mcp"))
    base = serve(M.Server, M.Handler)
    for token, names in ((access.read, READ), (access.full, READ | MAKE), (access.local, READ | MAKE)):
        status, _, body = fetch(base + "/mcp", data=rpc("tools/list"), headers=access.bearer(token))
        tools = json.loads(body.decode("utf-8"))["result"]["tools"]
        assert status == 200 and {t["name"] for t in tools} == names
        assert all(set(t) == PUBLIC for t in tools)
        assert {t["name"]: t["annotations"]["readOnlyHint"] for t in tools} == {n: n in READ for n in names}
        assert b'"route"' not in body and b'"need"' not in body

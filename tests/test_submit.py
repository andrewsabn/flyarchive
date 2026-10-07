"""Второй вход во входящие: передача документа по MCP токеном «полный» (FR-40).

Переданный документ не попадает в архив сам: он ложится во входящую папку и проходит ту же приёмку,
что и положенный руками.
"""
import base64
import hashlib
import json
import os

import pytest

import gatekit as K
import inbox as B
import ingest
import known as N
import mcp_server as M
import office_server as O

CLEAN = K.ooxml("docx", "Договор поставки оборудования.")


def b64(data):
    return base64.b64encode(data).decode("ascii")


@pytest.fixture
def srv(serve, fetch, access, monkeypatch, tmp_path):
    box = tmp_path / "входящие"
    monkeypatch.setattr(O, "SUBMIT_DIR", str(box))
    monkeypatch.setattr(O.Handler, "guard", access.guard("office"))
    base = serve(O.Server, O.Handler)

    class Srv:
        pass

    s = Srv()
    s.box, s.access = box, access

    def submit(token, body):
        status, _, raw = fetch(base + "/submit", data=body, headers=access.bearer(token) if token else {})
        return status, json.loads(raw) if raw else None

    def files():
        out = {}
        for root, _, names in os.walk(box):
            for n in names:
                p = os.path.join(root, n)
                out[os.path.relpath(p, box).replace(os.sep, "/")] = open(p, "rb").read()
        return out

    s.submit, s.files = submit, files
    return s


# ── приём ───────────────────────────────────────────────────────
def test_переданный_документ_ложится_во_входящую_папку_целиком(srv):
    status, body = srv.submit(srv.access.full, {"name": "договор.docx", "content_base64": b64(CLEAN)})
    assert status == 200 and body["accepted"] is True
    assert body["sha256"] == hashlib.sha256(CLEAN).hexdigest() and body["size"] == len(CLEAN)
    ((rel, data),) = srv.files().items()
    folder, name = rel.split("/")
    assert name == "договор.docx" and data == CLEAN
    assert folder.startswith("mcp-писатель-") and not any(n.startswith(".") for n in os.listdir(srv.box))
    assert "приёмк" in body["note"]


def test_два_документа_с_одним_именем_не_затирают_друг_друга(srv):
    srv.submit(srv.access.full, {"name": "договор.docx", "content_base64": b64(CLEAN)})
    srv.submit(srv.access.full, {"name": "договор.docx", "content_base64": b64(CLEAN + b" ")})
    assert len(srv.files()) == 2


def test_передача_записана_в_журнал_без_содержимого(srv):
    srv.submit(srv.access.full, {"name": "договор.docx", "content_base64": b64(CLEAN)})
    rec = [r for r in srv.access.journal() if r["tool"] == "/submit"][-1]
    assert (rec["client"], rec["status"]) == ("писатель", 200)
    assert rec["params"]["name"] == "договор.docx" and rec["params"]["size"] == str(len(CLEAN))
    assert b64(CLEAN)[:40] not in srv.access.journal_text()


# ── кто может передавать ────────────────────────────────────────
def test_токену_чтения_передача_запрещена(srv):
    status, _ = srv.submit(srv.access.read, {"name": "договор.docx", "content_base64": b64(CLEAN)})
    assert status == 403 and srv.files() == {}


def test_без_токена_передача_запрещена(srv):
    status, _ = srv.submit(None, {"name": "договор.docx", "content_base64": b64(CLEAN)})
    assert status == 401 and srv.files() == {}


# ── негодные запросы ────────────────────────────────────────────
@pytest.mark.parametrize("name", ["../договор.docx", "папка/договор.docx", "папка\\договор.docx", ".скрытый.docx", "", " ",
                                  "x" * 201 + ".docx", "договор\n.docx", None, 5, "CON", "..", "."])
def test_негодное_имя_отклоняется(srv, name):
    status, body = srv.submit(srv.access.full, {"name": name, "content_base64": b64(CLEAN)})
    assert status == 400 and "имя" in body["error"] and srv.files() == {}


@pytest.mark.parametrize("content", ["это не base64 !!!", "", None, 12, "QUJD=", "QUJD QUJD", "QUJ!QUJD", "!!!!", "===="])
def test_негодное_содержимое_отклоняется(srv, content):
    status, body = srv.submit(srv.access.full, {"name": "договор.docx", "content_base64": content})
    assert status == 400 and srv.files() == {}


def test_слишком_большой_документ_отклоняется(srv, monkeypatch):
    monkeypatch.setattr(O, "SUBMIT_MAX", 1000)
    status, body = srv.submit(srv.access.full, {"name": "большой.bin", "content_base64": b64(bytes(1001))})
    assert status == 413 and "1000" in body["error"] and srv.files() == {}
    assert srv.submit(srv.access.full, {"name": "в-самый-раз.bin", "content_base64": b64(bytes(1000))})[0] == 200


def test_сбой_записи_не_оставляет_обрывков(srv, monkeypatch):
    moved = []

    def broken(src, dst):
        moved.append((os.path.basename(src), os.path.basename(dst), os.listdir(src)))
        raise OSError("диск полон")

    monkeypatch.setattr(O.os, "rename", broken)
    status, body = srv.submit(srv.access.full, {"name": "договор.docx", "content_base64": b64(CLEAN)})
    assert status == 500 and srv.files() == {} and os.listdir(srv.box) == []
    ((hidden, final, inside),) = moved
    # документ пишется в скрытый каталог, который разбор не видит, и появляется под настоящим именем уже целым
    assert hidden == "." + final and final.startswith("mcp-") and inside == ["договор.docx"]


# ── инструмент MCP ──────────────────────────────────────────────
def test_инструмент_передачи_виден_только_полному_уровню():
    assert "submit_document" in [t["name"] for t in M.public_tools("full")]
    assert "submit_document" in [t["name"] for t in M.public_tools("local")]
    assert "submit_document" not in [t["name"] for t in M.public_tools("read")]
    tool = M.BY_NAME["submit_document"]
    assert tool["route"] == ("POST", M.OFFICE + "/submit") and tool["need"] == "full"
    assert set(tool["inputSchema"]["required"]) == {"name", "content_base64"}
    assert "приёмк" in tool["description"] and "base64" in tool["description"]


# ── дальше — обычная приёмка ────────────────────────────────────
def test_переданное_проходит_приёмку_как_положенное_руками(srv, tmp_path):
    home = tmp_path / "flyarchive"
    (home / "corpus").mkdir(parents=True)
    N.build(str(home / "corpus"), str(home / "index" / "known.sqlite"))
    srv.submit(srv.access.full, {"name": "договор.docx", "content_base64": b64(CLEAN)})
    srv.submit(srv.access.full, {"name": "setup.exe", "content_base64": b64(b"MZ" + bytes(200))})

    class Table:
        rows = []

        def add(self, rows):
            self.rows.extend(rows)

    s = B.process(str(home), str(srv.box), table=Table(), embed=lambda texts: [[0.1] * ingest.DIM for _ in texts], stable_seconds=0)
    assert s.counts == {"accept": 1, "quarantine": 1}
    with open(s.receipts, encoding="utf-8") as f:
        recs = [json.loads(line) for line in f]
    doc = next(r for r in recs if r["decision"] == "accept")
    assert doc["path"].startswith(f"входящие/{s.batch}/mcp-писатель-") and doc["path"].endswith("/договор.docx")
    assert os.listdir(srv.box) == []

"""Что сервер документов принимает на вход: страницы, пути под старыми корнями, отдача созданных файлов.

Исправлено по итогам ревью: диапазон страниц строился до отсечения по числу страниц; документы,
проиндексированные под старыми корнями, находились поиском, но не читались; SVG отдавался как страница.
"""
import json
import time
import tracemalloc
import urllib.parse

import pytest

import corpus_path
import gatekit as K
import office_server as O
import webui as W


# ── страницы ────────────────────────────────────────────────────
@pytest.mark.parametrize("spec, total, pages", [
    ("", 3, [0, 1, 2]), (None, 100, list(range(40))), ("2", 5, [1]), ("1-3", 5, [0, 1, 2]), ("1,3,5-7", 10, [0, 2, 4, 5, 6]),
    ("4-9", 5, [3, 4]), ("9", 5, []), ("0-2", 5, [0, 1]), ("5-3", 9, []), ("-5", 9, []), ("2-", 9, []), ("a-b", 9, []),
    (" 2 , 4 ", 9, [1, 3]), ("3-3", 9, [2]), ("1-5,2-3", 9, [0, 1, 2, 3, 4]), ("²", 9, []), ("x", 9, [])])
def test_разбор_страниц(spec, total, pages):
    assert O.parse_pages(spec, total) == pages


@pytest.mark.parametrize("spec", ["1-3000000", "1-9999999999", "1-" + "9" * 30, "3000000", ",".join(["1-9999999999"] * 50)])
def test_огромный_диапазон_страниц_не_стоит_ни_памяти_ни_времени(spec):
    """Границы применяются до построения: иначе «1-9999999999» — это множество из миллиардов чисел."""
    tracemalloc.start()
    started = time.time()
    try:
        pages = O.parse_pages(spec, 10)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert pages == (list(range(10)) if "-" in spec else []) and peak < 1_000_000 and time.time() - started < 1


def test_частей_в_перечне_страниц_не_больше_предела():
    spec = ",".join(str(n) for n in range(1, 2000))
    assert O.parse_pages(spec, 10_000) == list(range(O.MAX_PAGE_PARTS))


# ── пути под старыми корнями ──────────────────────────────
@pytest.fixture
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    (root / "jira" / "IT").mkdir(parents=True)
    (root / "confluence" / "DOC").mkdir(parents=True)
    (root / "jira" / "IT" / "вложение.txt").write_text("Текст вложения задачи", encoding="utf-8")
    (root / "confluence" / "DOC" / "схема.txt").write_text("Текст вложения страницы", encoding="utf-8")
    (tmp_path / "снаружи.txt").write_text("секрет", encoding="utf-8")
    monkeypatch.setattr(O, "CORPUS", str(root))
    monkeypatch.setattr(W, "CORPUS", str(root))
    monkeypatch.setattr(corpus_path, "ALIAS", {"sample_jira_export": "jira", "sample_confluence_export": "confluence"})     # из таблицы источников (FR-99)
    return root


@pytest.mark.parametrize("rel, name", [
    ("sample_jira_export\\IT\\вложение.txt", "jira/IT/вложение.txt"), ("sample_jira_export/IT/вложение.txt", "jira/IT/вложение.txt"),
    ("sample_confluence_export\\DOC\\схема.txt", "confluence/DOC/схема.txt"), ("jira\\IT\\вложение.txt", "jira/IT/вложение.txt"),
    ("/jira/IT/вложение.txt", "jira/IT/вложение.txt")])
def test_путь_под_старым_корнем_читается_обоими_серверами(corpus, rel, name):
    want = str(corpus.joinpath(*name.split("/")))
    assert O.resolve(rel) == want and W.resolve(rel) == want


@pytest.mark.parametrize("rel", ["sample_jira_export/../../снаружи.txt", "sample_jira_export\\..\\..\\снаружи.txt", "../снаружи.txt",
                                 "sample_jira_export", "sample_jira_export/IT", "sample_other_export/IT/вложение.txt", "", None,
                                 "sample_jira_export/IT/нет.txt"])
def test_выход_за_корпус_через_старый_корень_закрыт(corpus, rel):
    assert O.resolve(rel) is None
    if rel is not None:
        assert W.resolve(rel) is None


# ── через сервер ────────────────────────────────────────────────
@pytest.fixture
def srv(serve, fetch, access, monkeypatch, corpus, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "diagram-1.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
    (out / "chart-1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (out / "report-1.docx").write_bytes(K.ooxml("docx"))
    (corpus / "jira" / "IT" / "отчёт.pdf").write_bytes(K.PDF)
    monkeypatch.setattr(O, "OUT", str(out))
    monkeypatch.setattr(O.Handler, "guard", access.guard("office"))
    base = serve(O.Server, O.Handler)

    def get(route, **params):
        return fetch(base + route + "?" + urllib.parse.urlencode(params), headers=access.bearer(access.read))

    return get


def test_документ_под_старым_корнем_читается_через_сервер(srv):
    status, _, body = srv("/read", path="sample_jira_export\\IT\\вложение.txt")
    assert status == 200 and "Текст вложения задачи" in body.decode("utf-8")


def test_огромный_диапазон_страниц_через_сервер_отвечает_сразу(srv):
    started = time.time()
    status, _, body = srv("/read", path="jira/IT/отчёт.pdf", pages="1-9999999999")
    assert status == 200 and "error" not in json.loads(body) and time.time() - started < 5


@pytest.mark.parametrize("name, sandbox", [("diagram-1.svg", True), ("report-1.docx", True), ("chart-1.png", False)])
def test_созданный_файл_отдаётся_без_права_исполнять_сценарии(srv, name, sandbox):
    status, headers, _ = srv("/file", n=name)
    assert status == 200 and headers.get("X-Content-Type-Options") == "nosniff"
    assert (headers.get("Content-Security-Policy") == "sandbox") is sandbox


# ── Visio: .vsdx читается разбором zip, двоичный .vsd — нет ─────
VSDX = K.zip_bytes({"visio/document.xml": b"<VisioDocument/>", "visio/pages/page1.xml": "<Page><Text>Блок Экраны</Text><Text>Блок Хранилище</Text></Page>".encode("utf-8")})


def test_vsdx_читается_как_раньше_текст_фигур(corpus):
    (corpus / "схема.vsdx").write_bytes(VSDX)
    answer = O.read_document("схема.vsdx", "")
    assert "error" not in answer and "Блок Экраны" in answer["text"] and "Блок Хранилище" in answer["text"] and answer["info"] == {"листов": 1}


def test_vsd_отказ_понятным_текстом_а_не_ошибкой_разбора_zip(corpus):
    (corpus / "схема.vsd").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(range(1, 200)))
    answer = O.read_document("схема.vsd", "")
    assert answer == {"error": "старый формат Visio (.vsd) не читается; читается .vsdx", "path": "схема.vsd"}
    assert "BadZipFile" not in json.dumps(answer, ensure_ascii=False)


def test_vsd_не_значится_среди_читаемых_форматов():
    assert ".vsd" not in O.READERS and ".vsdx" in O.READERS


def test_vsd_через_сервер_отвечает_отказом_без_сбоя(srv, corpus):
    (corpus / "схема.vsd").write_bytes(b"\xd0\xcf\x11\xe0" + bytes(100))
    status, _, body = srv("/read", path="схема.vsd")
    assert status == 200 and "старый формат Visio" in json.loads(body)["error"] and "vsdx" in json.loads(body)["error"]

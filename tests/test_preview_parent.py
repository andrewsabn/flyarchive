"""Просмотр файла: родитель (FR-76) — сведения, проверка того, что вернул рабочий процесс, кэш, замки, чистка.

Рабочий процесс подставной (`Fake` из test_preview_kit): тест сам решает, что он «вернул», в том числе порчу. Родитель файл не разбирает —
он считает sha256 потоком, запускает рабочий процесс и принимает от него только то, что прошло проверку.
"""
import ast
import fcntl
import hashlib
import json
import os
import stat
import threading
import time

import pytest

import preview as P
from test_preview_kit import BATCH, IMAGE_DESCRIPTION, PDF_DESCRIPTION, TEXT_DESCRIPTION, env, png, walk_modes, worker, write  # noqa: F401

SHA = lambda data: hashlib.sha256(data).hexdigest()           # noqa: E731
TOOLS = os.path.dirname(os.path.abspath(P.__file__))


def show(env, path, area="queue", members=()):
    return P.show(env.home, area, path, members)


# ── что отдаётся ────────────────────────────────────────────────
def test_текст_ответ_по_договору(env, worker):
    worker.describe = lambda ext: {"kind": "text", "type": "txt", "text": "первая строка", "truncated": False}
    data = "первая строка".encode("utf-8")
    path = env.put("queue", "договор.txt", data)
    answer = show(env, path)
    assert answer == {"kind": "text", "text": "первая строка", "truncated": False,
                      "meta": {"name": "договор.txt", "type": "txt", "size": len(data), "sha256": SHA(data), "batch": BATCH,
                               "origin": {"archive": None, "inner": None}}}
    assert json.loads(json.dumps(answer, ensure_ascii=False)) == answer


def test_truncated_присылается_всегда_у_текста_и_shown_всегда_с_pages(env, worker):
    worker.describe = lambda ext: {"kind": "text", "type": "txt", "text": "x" * 20_000, "truncated": True}
    assert show(env, env.put("queue", "длинный.txt", b"x"))["truncated"] is True
    worker.describe = lambda ext: {"kind": "text", "type": "txt", "text": "коротко", "truncated": False}
    assert show(env, env.put("queue", "короткий.txt", b"y"))["truncated"] is False
    worker.describe = lambda ext: {"kind": "pages", "type": "pdf", "pages": 25, "shown": 20}
    answer = show(env, env.put("queue", "толстый.pdf", b"z"))
    assert answer["pages"] == 25 and answer["shown"] == 20
    worker.describe = lambda ext: {"kind": "pages", "type": "pdf", "pages": 1, "shown": 1}
    answer = show(env, env.put("queue", "тонкий.pdf", b"w"))
    assert (answer["pages"], answer["shown"]) == (1, 1)


def test_набор_ключей_ответа_по_виду(env, worker):
    answers = {}
    for kind, description, name in (("text", TEXT_DESCRIPTION, "а.txt"), ("pages", PDF_DESCRIPTION, "а.pdf"), ("image", IMAGE_DESCRIPTION, "а.png")):
        worker.describe = lambda ext, d=description: d
        answers[kind] = show(env, env.put("queue", name, name.encode("utf-8")))
    assert set(answers["text"]) == {"kind", "meta", "text", "truncated"}
    assert set(answers["pages"]) == {"kind", "meta", "pages", "shown"}
    assert set(answers["image"]) == {"kind", "meta"}


def test_картинка_страница_один_работает_а_два_нет(env, worker):
    worker.describe = lambda ext: {"kind": "image", "type": "png"}
    worker.render = lambda page: png(50, 40)
    path = env.put("queue", "фото.png", b"png-bytes")
    assert show(env, path)["kind"] == "image"
    first = P.page(env.home, "queue", path, "1")
    assert first == png(50, 40) and worker.ops == ["describe"]            # картинку нарисовало описание: страница из кэша
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "2")
    assert e.value.message.code == "preview.no_page" and e.value.message.args == {"page": 2, "shown": 1}


def test_meta_из_квитанции_архив_путь_внутри_и_тип(env, worker):
    worker.describe = lambda ext: {"kind": "text", "type": "txt", "text": "т", "truncated": False}
    data = b"inside"
    path = env.put("queue", "архив.zip/внутри/файл.txt", data)
    env.receipt({"name": "архив.zip/внутри/файл.txt", "sha256": SHA(data), "size": 6, "type": "docx", "archive": "архив.zip", "inner": "внутри/файл.txt"})
    env.receipt({"name": "архив.zip/внутри/файл.txt", "decision": "quarantine", "by": "владелец", "archive": "чужой.zip", "inner": "не то"})
    meta = show(env, path)["meta"]
    assert meta == {"name": "файл.txt", "type": "txt", "size": 6, "sha256": SHA(data), "batch": BATCH,
                    "origin": {"archive": "архив.zip", "inner": "внутри/файл.txt"}}      # тип по содержимому — от рабочего процесса, не из квитанции


def test_meta_корпуса_берёт_пачку_и_квитанцию_из_пути_и_без_квитанции_пустое_происхождение(env, worker):
    worker.describe = lambda ext: PDF_DESCRIPTION
    path = env.put("corpus", "а/б.pdf", b"corpus")
    env.receipt({"name": "а/б.pdf", "type": "pdf", "archive": "а", "inner": "б.pdf"})
    meta = show(env, path, "corpus")["meta"]
    assert meta["batch"] == BATCH and meta["name"] == "б.pdf" and meta["origin"] == {"archive": "а", "inner": "б.pdf"}
    os.makedirs(os.path.join(env.home, "corpus", "jira"))
    write(os.path.join(env.home, "corpus", "jira", "Пример-1.txt"), b"x")
    meta = show(env, "jira/Пример-1.txt", "corpus")["meta"]
    assert meta["batch"] is None and meta["origin"] == {"archive": None, "inner": None} and meta["name"] == "Пример-1.txt"


def test_meta_когда_квитанции_нет_или_она_негодная(env, worker):
    worker.describe = lambda ext: PDF_DESCRIPTION
    path = env.put("queue", "без-квитанции.pdf", b"x")
    assert show(env, path)["meta"]["origin"] == {"archive": None, "inner": None}
    with open(os.path.join(env.home, "квитанции", BATCH + ".jsonl"), "wb") as f:
        f.write("не json\n[1, 2]\n".encode("utf-8") + json.dumps({"name": "без-квитанции.pdf", "archive": 5, "inner": ["x"]}).encode() + b"\n\xff\xfe\n")
    meta = show(env, path)["meta"]
    assert meta["origin"] == {"archive": None, "inner": None} and meta["type"] == "pdf"


def test_тип_в_meta_когда_рабочий_процесс_не_ответил_берётся_из_квитанции_а_иначе_null(env, worker):
    worker.behave = lambda op, outdir: P.Run(1)
    data = b"x"
    path = env.put("queue", "а.bin", data)
    assert show(env, path)["meta"]["type"] is None
    env.receipt({"name": "а.bin", "type": "elf"})
    answer = show(env, path)
    assert answer["kind"] == "none" and answer["meta"]["type"] == "elf" and answer["meta"]["sha256"] == SHA(data)


# ── пояснения kind none ─────────────────────────────────────────
NOTES = [
    ({"reason": "program", "args": {}}, "preview.program", {}),
    ({"reason": "unsupported", "args": {"type": "zip"}}, "preview.unsupported", {"type": "zip"}),
    ({"reason": "needs_converter", "args": {"type": "mp4"}}, "preview.needs_converter", {"type": "mp4"}),      # docx идёт в контейнер (FR-78)
    ({"reason": "empty", "args": {}}, "preview.empty", {}),
    ({"reason": "encrypted", "args": {}}, "preview.encrypted", {}),
    ({"reason": "memory", "args": {}}, "preview.memory", {}),
    ({"reason": "too_large", "args": {"megapixels": 256, "limit": 100}}, "preview.image_too_large", {"megapixels": 256, "limit": 100}),
    ({"reason": "broken", "args": {"error_type": "FileDataError"}}, "preview.broken", {"error_type": "FileDataError"}),
]


@pytest.mark.parametrize("body,code,args", NOTES)
def test_none_с_пояснением_из_каталога_и_в_кэш_не_попадает(env, worker, body, code, args):
    worker.describe = lambda ext: {"kind": "none", "type": "pe", **body}
    answer = show(env, env.put("queue", "а.bin", b"MZ"))
    assert answer["kind"] == "none" and answer["note"]["code"] == code and answer["note"]["args"] == args
    assert answer["note"]["text"] and answer["meta"]["type"] == "pe" and set(answer) == {"kind", "meta", "note"}
    assert env.cached() == []                                          # none не кэшируется: с новыми типами в следующих порциях ответ изменится
    show(env, env.put("queue", "а.bin", b"MZ"))
    assert worker.ops == ["describe", "describe"]


# ── порча ответа рабочего процесса ──────────────────────────────
def say(obj, name="result.json"):
    """Рабочий процесс «вернул» obj (словарь — как JSON, байты — как есть) и вышел с кодом 0."""
    def behave(op, outdir):
        write(os.path.join(outdir, name), json.dumps(obj).encode("utf-8") if not isinstance(obj, bytes) else obj)
        return P.Run(0)
    return behave


def sparse(path, size):
    with open(path, "wb") as f:
        f.truncate(size)


def link_to(target):
    def behave(op, outdir):
        os.symlink(target, os.path.join(outdir, "result.json"))
        return P.Run(0)
    return behave


def good_result(over=None, **extra):
    return {**{"kind": "text", "type": "txt", "text": "т", "truncated": False}, **(over or {}), **extra}


def with_png(data, result=None):
    def behave(op, outdir):
        write(os.path.join(outdir, "result.json"), json.dumps(result or {"kind": "image", "type": "png"}).encode("utf-8"))
        write(os.path.join(outdir, "page.png"), data)
        return P.Run(0)
    return behave


def png_link(op, outdir):
    write(os.path.join(outdir, "result.json"), json.dumps({"kind": "image", "type": "png"}).encode("utf-8"))
    os.symlink("/etc/hostname", os.path.join(outdir, "page.png"))
    return P.Run(0)


def png_sparse(op, outdir):
    write(os.path.join(outdir, "result.json"), json.dumps({"kind": "image", "type": "png"}).encode("utf-8"))
    sparse(os.path.join(outdir, "page.png"), 33 << 20)
    return P.Run(0)


def result_big(op, outdir):
    write(os.path.join(outdir, "result.json"), b" " * (2 << 20) + json.dumps(good_result()).encode("utf-8"))
    return P.Run(0)


def result_dir(op, outdir):
    os.mkdir(os.path.join(outdir, "result.json"))
    return P.Run(0)


GARBAGE = {
    "ничего не записал": (lambda op, outdir: P.Run(0), "missing"),
    "не JSON": (say("{не json".encode("utf-8")), "schema"),
    "JSON не объект": (say([1, 2]), "schema"),
    "очень глубокий JSON": (say(b"[" * 100_000 + b"]" * 100_000), "schema"),
    "вид из будущего": (say(good_result(kind="mail")), "schema"),
    "лишний ключ": (say(good_result(html="<b>разметка</b>")), "schema"),
    "у текста нет truncated": (say({"kind": "text", "type": "txt", "text": "т"}), "schema"),
    "truncated не булево": (say(good_result(truncated="нет")), "schema"),
    "текст не строка": (say(good_result(text=["т"])), "schema"),
    "текст длиннее 20000": (say(good_result(text="я" * 20_001)), "schema"),
    "тип с путём": (say(good_result(type="../x")), "schema"),
    "тип слишком длинный": (say(good_result(type="a" * 21)), "schema"),
    "тип не строка": (say(good_result(type=7)), "schema"),
    "страниц не число": (say({"kind": "pages", "type": "pdf", "pages": "3", "shown": 3}), "schema"),
    "страниц булево": (say({"kind": "pages", "type": "pdf", "pages": True, "shown": 1}), "schema"),
    "страниц дробное": (say({"kind": "pages", "type": "pdf", "pages": 3.0, "shown": 3}), "schema"),
    "показано дробное": (say({"kind": "pages", "type": "pdf", "pages": 3, "shown": 3.0}), "schema"),
    "страниц ноль": (say({"kind": "pages", "type": "pdf", "pages": 0, "shown": 0}), "schema"),
    "показано больше 20": (say({"kind": "pages", "type": "pdf", "pages": 30, "shown": 21}), "schema"),
    "показано больше, чем есть": (say({"kind": "pages", "type": "pdf", "pages": 3, "shown": 4}), "schema"),
    "показано меньше возможного": (say({"kind": "pages", "type": "pdf", "pages": 30, "shown": 5}), "schema"),
    "показано ноль": (say({"kind": "pages", "type": "pdf", "pages": 3, "shown": 0}), "schema"),
    "none без причины": (say({"kind": "none", "type": "pe", "args": {}}), "schema"),
    "none с чужой причиной": (say({"kind": "none", "type": "pe", "reason": "хочу", "args": {}}), "schema"),
    "none без параметра": (say({"kind": "none", "type": "pe", "reason": "unsupported", "args": {}}), "schema"),
    "none с лишним параметром": (say({"kind": "none", "type": "pe", "reason": "program", "args": {"x": "y"}}), "schema"),
    "none с параметром не того вида": (say({"kind": "none", "type": "pe", "reason": "unsupported", "args": {"type": 5}}), "schema"),
    "none с параметром-разметкой": (say({"kind": "none", "type": "pe", "reason": "unsupported", "args": {"type": "<script>"}}), "schema"),
    "none с отрицательным размером": (say({"kind": "none", "type": "png", "reason": "too_large", "args": {"megapixels": -1, "limit": 100}}), "schema"),
    "result.json — ссылка": (link_to("/etc/hostname"), "not_a_file"),
    "result.json — каталог": (result_dir, "not_a_file"),
    "result.json больше предела": (result_big, "too_big"),
    "картинка без png": (say({"kind": "image", "type": "png"}), "missing"),
    "вместо png другое": (with_png(b"<html><script>alert(1)</script></html>"), "not_png"),
    "png оборван": (with_png(png(20, 20)[:-30]), "not_png"),
    "png без конца": (with_png(png(20, 20)[:-12]), "not_png"),
    "png — ссылка": (png_link, "not_a_file"),
    "png больше предела": (png_sparse, "too_big"),
    "картинка больше 2000 точек": (with_png(png(2001, 10)), "dimensions"),
    "картинка 2000 на 2001": (with_png(png(10, 2001)), "dimensions"),
}


@pytest.mark.parametrize("name", sorted(GARBAGE))
def test_негодный_ответ_описания_none_с_пояснением_и_в_кэш_не_попадает(env, worker, name):
    behave, what = GARBAGE[name]
    worker.behave = behave
    answer = show(env, env.put("queue", "а.bin", "данные".encode("utf-8")))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.bad_answer" and answer["note"]["args"] == {"what": what}, answer
    assert env.cached() == [] and not [n for n in os.listdir(env.cache) if n.startswith(".work")]     # рабочий каталог убран


def test_хорошие_граничные_ответы_принимаются(env, worker):
    for name, result, data in (("а.txt", good_result(text="я" * 20_000, truncated=True), None),
                               ("б.pdf", {"kind": "pages", "type": "pdf", "pages": 20, "shown": 20}, None),
                               ("в.pdf", {"kind": "pages", "type": "pdf", "pages": 100_000, "shown": 20}, None),
                               ("г.png", {"kind": "image", "type": "png"}, png(2000, 2000))):
        worker.behave = (with_png(data, result) if data else say(result))
        answer = show(env, env.put("queue", name, name.encode("utf-8")))
        assert answer["kind"] == result["kind"], (name, answer)


@pytest.mark.parametrize("run,code,args", [
    (P.Run(1), "preview.worker_failed", {"why": "exit 1"}),
    (P.Run(139), "preview.worker_failed", {"why": "exit 139"}),
    (P.Run(-9), "preview.worker_failed", {"why": "signal 9"}),
    (P.Run(-24), "preview.worker_failed", {"why": "signal 24"}),
    (P.Run(1, False, "bwrap: No permissions to create new namespace"), "preview.worker_failed",
     {"why": "exit 1: bwrap: No permissions to create new namespace"}),
    (P.Run(None, True), "preview.timeout", {"seconds": 90}),
])
def test_рабочий_процесс_упал_или_не_уложился_none_и_исключения_нет(env, worker, run, code, args):
    def behave(op, outdir):
        write(os.path.join(outdir, "result.json"), json.dumps(good_result()).encode("utf-8"))       # даже готовый ответ упавшего не принимается
        return run
    worker.behave = behave
    answer = show(env, env.put("queue", "а.txt", b"x"))
    assert answer["kind"] == "none" and answer["note"]["code"] == code and answer["note"]["args"] == args
    assert env.cached() == []


def test_исключение_при_запуске_рабочего_процесса_не_выходит_наружу(env, worker):
    def explode(op, outdir):
        raise RuntimeError("не запустилось")
    worker.behave = explode
    answer = show(env, env.put("queue", "а.txt", b"x"))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.worker_failed" and answer["note"]["args"] == {"why": "RuntimeError"}


def test_хвост_ошибки_запуска_очищен_от_лишнего(env, worker):
    worker.behave = lambda op, outdir: P.Run(1, False, "строка\x00с\x1bнулём и русским \n" + "я" * 500)
    why = show(env, env.put("queue", "а.txt", b"x"))["note"]["args"]["why"]
    assert why.isascii() and why.isprintable() and why.startswith("exit 1: ") and len(why) <= 200


# ── без песочницы файл не разбирается ───────────────────────────
def test_без_bwrap_none_с_пояснением_рабочий_процесс_не_запущен(env, monkeypatch):
    calls = []
    monkeypatch.setattr(P, "bwrap_path", lambda: None)
    monkeypatch.setattr(P, "_run_worker", lambda *a: calls.append(a))
    data = "содержимое".encode("utf-8")
    path = env.put("queue", "а.pdf", data)
    env.receipt({"name": "а.pdf", "type": "pdf"})
    answer = show(env, path)
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.no_sandbox"
    assert answer["meta"]["sha256"] == SHA(data) and answer["meta"]["type"] == "pdf"       # сведения без разбора: sha256 и квитанция
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "1")
    assert e.value.message.code == "preview.no_sandbox"
    assert calls == [] and env.cached() == []


def test_bwrap_ищется_в_PATH(tmp_path, monkeypatch):
    empty = tmp_path / "пусто"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert P.bwrap_path() is None
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "bwrap").write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(fake / "bwrap", 0o755)
    monkeypatch.setenv("PATH", str(fake))
    assert P.bwrap_path() == str(fake / "bwrap")


# ── родитель файл не разбирает ──────────────────────────────────
def imports_of(path):
    names = set()
    with open(path, encoding="utf-8") as f:
        for node in ast.walk(ast.parse(f.read())):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_родитель_не_подключает_ничего_что_разбирает_содержимое_файла():
    names = imports_of(os.path.join(TOOLS, "preview.py"))
    reading = {"fitz", "pymupdf", "PIL", "filetype_sniff", "gate", "zipfile", "tarfile", "gzip", "bz2", "lzma", "email", "olefile", "xml", "html",
               "extract_msg", "unpack", "intake", "review", "inbox", "preview_worker", "imghdr", "mimetypes"}
    assert names & reading == set(), names & reading
    assert {"hashlib", "subprocess", "messages"} <= names


def test_sha256_считается_потоком_кусками_не_больше_мегабайта(env, worker, monkeypatch):
    worker.describe = lambda ext: TEXT_DESCRIPTION
    data = os.urandom(3_500_000)
    path = env.put("queue", "большой.bin", data)
    asked = []
    real = os.read
    monkeypatch.setattr(os, "read", lambda fd, n: (asked.append(n), real(fd, n))[1])
    answer = show(env, path)
    assert answer["meta"]["sha256"] == SHA(data) and answer["meta"]["size"] == len(data)
    assert asked and max(asked) <= 1 << 20 and len(asked) >= 4


def test_рабочий_процесс_получает_тот_же_файл_что_хэшировался(env, worker):
    data = "содержимое файла".encode("utf-8")
    show(env, env.put("queue", "а.txt", data))
    assert worker.inputs == [data]


def test_расширение_для_рабочего_процесса_только_буквы_и_цифры(env, worker):
    for name, ext in (("а.PDF", "pdf"), ("архив.tar.gz", "gz"), ("без-расширения", ""), ("а.p d f", ""), ("точка.", ""), ("а.тхт", ""),
                      ("а.слишкомдлинное1", ""), ("а.abcdefghij", "abcdefghij"), ("а.abcdefghijk", "")):
        show(env, env.put("queue", name, name.encode("utf-8")))
    assert [call[1] for call in worker.calls] == ["pdf", "gz", "", "", "", "", "", "abcdefghij", ""]


# ── кэш ─────────────────────────────────────────────────────────
def test_кэш_лежит_по_sha256_и_повторный_show_рабочий_процесс_не_запускает(env, worker):
    data = b"pdf-bytes"
    path = env.put("queue", "а.pdf", data)
    first = show(env, path)
    assert first["kind"] == "pages" and worker.ops == ["describe"]
    assert env.cached() == [f"{SHA(data)}/desc.json"]
    with open(os.path.join(env.cache, SHA(data), "desc.json"), encoding="utf-8") as f:
        assert json.load(f) == {"v": 1, **PDF_DESCRIPTION}
    assert show(env, path) == first and show(env, path) == first
    assert worker.ops == ["describe"]


def test_одинаковое_содержимое_в_разных_файлах_делит_кэш_а_meta_у_каждого_своя(env, worker):
    first = show(env, env.put("queue", "один.pdf", b"same"))
    second = show(env, env.put("quarantine", "два.pdf", b"same"), "quarantine")
    assert worker.ops == ["describe"] and first["pages"] == second["pages"]
    assert (first["meta"]["name"], second["meta"]["name"]) == ("один.pdf", "два.pdf")
    show(env, env.put("queue", "иной.pdf", b"other"))
    assert worker.ops == ["describe", "describe"]


def test_изменённый_файл_описывается_заново(env, worker):
    path = env.put("queue", "а.pdf", b"v1")
    show(env, path)
    write(env.full("queue", "а.pdf"), b"v2")
    show(env, path)
    assert worker.ops == ["describe", "describe"] and len(os.listdir(env.cache)) >= 2


def test_страница_рисуется_один_раз_и_ложится_в_кэш(env, worker):
    data = b"pdf-bytes"
    path = env.put("queue", "а.pdf", data)
    show(env, path)
    worker.render = lambda page: png(10 + page, 10)
    first = P.page(env.home, "queue", path, "2")
    assert first == png(12, 10) and worker.calls[-1] == ("render", "pdf", 2)
    assert P.page(env.home, "queue", path, "2") == first and P.page(env.home, "queue", path, "2") == first
    assert worker.ops == ["describe", "render"]
    assert P.page(env.home, "queue", path, "3") == png(13, 10) and worker.ops == ["describe", "render", "render"]
    assert env.cached() == [f"{SHA(data)}/desc.json", f"{SHA(data)}/page-2.png", f"{SHA(data)}/page-3.png"]


def test_страница_без_предварительного_show_сама_описывает_файл(env, worker):
    path = env.put("queue", "а.pdf", b"x")
    assert P.page(env.home, "queue", path, "1") == png() and worker.ops == ["describe", "render"]


def test_номер_страницы_вне_диапазона_отказ_png_не_отдан_и_рабочий_процесс_не_запущен(env, worker):
    path = env.put("queue", "а.pdf", b"x")
    show(env, path)
    for number in ("4", "5", "20", "999999"):
        with pytest.raises(P.PreviewError) as e:
            P.page(env.home, "queue", path, number)
        assert e.value.message.code == "preview.no_page" and e.value.message.args == {"page": int(number), "shown": 3}
    assert worker.ops == ["describe"]
    worker.describe = lambda ext: {"kind": "pages", "type": "pdf", "pages": 25, "shown": 20}
    path = env.put("queue", "толстый.pdf", b"y")
    assert P.page(env.home, "queue", path, "20") == png()
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "21")                                         # страница есть в файле, но предел показа — 20
    assert e.value.message.args == {"page": 21, "shown": 20}


def test_страница_у_объекта_без_страниц_отказ(env, worker):
    worker.describe = lambda ext: TEXT_DESCRIPTION
    path = env.put("queue", "а.txt", b"x")
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "1")
    assert e.value.message.code == "preview.no_pages" and worker.ops == ["describe"]
    worker.describe = lambda ext: {"kind": "none", "type": "elf", "reason": "program", "args": {}}
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", env.put("queue", "б.bin", b"y"), "1")
    assert e.value.message.code == "preview.program"                                  # причина отказа — та же, что в пояснении к show


PAGE_GARBAGE = {
    "ничего не записал": (lambda op, outdir: P.Run(0), "missing"),
    "не JSON": (say(b"{"), "schema"),
    "не тот номер страницы": (say({"kind": "page", "page": 3}), "schema"),
    "номер страницы дробный": (say({"kind": "page", "page": 2.0}), "schema"),
    "вид не page": (say({"kind": "pages", "type": "pdf", "pages": 3, "shown": 3}), "schema"),
    "лишний ключ": (say({"kind": "page", "page": 2, "html": "x"}), "schema"),
    "png нет": (say({"kind": "page", "page": 2}), "missing"),
    "не png": (with_png("%PDF-1.4 вместо картинки".encode("utf-8"), {"kind": "page", "page": 2}), "not_png"),
    "png оборван": (with_png(png(30, 30)[:-20], {"kind": "page", "page": 2}), "not_png"),
    "png — ссылка": (None, "not_a_file"),
    "страница больше 4 Мп": (with_png(png(2001, 2000), {"kind": "page", "page": 2}), "dimensions"),
    "страница слишком длинная": (with_png(png(1, 10_001), {"kind": "page", "page": 2}), "dimensions"),
    "png больше предела": (None, "too_big"),
}


@pytest.mark.parametrize("name", sorted(PAGE_GARBAGE))
def test_негодная_страница_отказ_с_пояснением_и_в_кэш_не_попадает(env, worker, name):
    behave, what = PAGE_GARBAGE[name]
    data = b"pdf"
    path = env.put("queue", "а.pdf", data)
    show(env, path)
    if behave is None:
        def behave(op, outdir, kind=name):
            write(os.path.join(outdir, "result.json"), json.dumps({"kind": "page", "page": 2}).encode("utf-8"))
            if kind == "png — ссылка":
                os.symlink("/etc/hostname", os.path.join(outdir, "page.png"))
            else:
                sparse(os.path.join(outdir, "page.png"), 33 << 20)
            return P.Run(0)
    worker.behave = behave
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "2")
    assert e.value.message.code == "preview.bad_answer" and e.value.message.args == {"what": what}
    assert env.cached() == [f"{SHA(data)}/desc.json"]


def test_страница_с_больших_размеров_для_картинки_берёт_предел_2000(env, worker):
    worker.describe = lambda ext: {"kind": "image", "type": "png"}
    worker.render = lambda page: png(100, 100)
    path = env.put("queue", "фото.png", b"x")
    show(env, path)
    os.remove(os.path.join(env.cache, SHA(b"x"), "page-1.png"))
    worker.render = lambda page: png(2001, 5)
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "1")
    assert e.value.message.args == {"what": "dimensions"}
    worker.render = lambda page: png(3000, 1300)                                      # а страница PDF до 4 Мп допускается
    worker.describe = lambda ext: PDF_DESCRIPTION
    assert P.page(env.home, "queue", env.put("queue", "а.pdf", b"y"), "1") == png(3000, 1300)


def test_страница_отказ_рабочего_процесса_с_причиной_из_каталога(env, worker):
    path = env.put("queue", "а.pdf", b"x")
    show(env, path)
    worker.behave = say({"kind": "none", "type": "pdf", "reason": "memory", "args": {}})
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "1")
    assert e.value.message.code == "preview.memory"
    worker.behave = lambda op, outdir: P.Run(None, True)
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "1")
    assert e.value.message.code == "preview.timeout"
    assert env.cached() == [f"{SHA(b'x')}/desc.json"]


# ── права и атомарность записи ──────────────────────────────────
def test_каталоги_кэша_0700_файлы_0600_и_при_любой_маске(env, worker):
    old = os.umask(0)
    try:
        path = env.put("queue", "а.pdf", b"x")
        show(env, path)
        P.page(env.home, "queue", path, "1")
    finally:
        os.umask(old)
    modes = walk_modes(os.path.join(env.home, "cache"))
    assert modes and "preview" in modes and stat.S_IMODE(os.stat(os.path.join(env.home, "cache")).st_mode) == 0o700
    for name, (is_dir, mode) in modes.items():
        assert mode == (0o700 if is_dir else 0o600), (name, oct(mode))
    assert {os.path.basename(n) for n in modes} >= {"preview", f"{SHA(b'x')}", "desc.json", "page-1.png"}


def test_запись_в_кэш_через_временный_файл_и_os_replace(env, worker, monkeypatch):
    seen = []
    real = os.replace

    def spy(src, dst, *a, **k):
        seen.append((os.path.basename(dst), os.path.dirname(src) == os.path.dirname(dst), os.path.exists(dst), os.path.getsize(src), src != dst))
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", spy)
    path = env.put("queue", "а.pdf", b"x")
    show(env, path)
    P.page(env.home, "queue", path, "1")
    assert [s[0] for s in seen] == ["desc.json", "page-1.png"]
    assert all(same_dir and not existed and size > 0 and differs for _, same_dir, existed, size, differs in seen)
    assert not [n for _, _, files in os.walk(env.cache) for n in files if n.endswith(".tmp")]


def test_сбой_записи_в_кэш_none_без_обломков(env, worker, monkeypatch):
    def full(*a, **k):
        raise OSError(28, "нет места")

    monkeypatch.setattr(os, "replace", full)
    path = env.put("queue", "а.pdf", b"x")
    answer = show(env, path)
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.cache_failed" and answer["note"]["args"] == {"error_type": "OSError"}
    assert env.cached() == []
    monkeypatch.undo()
    worker.install(monkeypatch)
    assert show(env, path)["kind"] == "pages"
    monkeypatch.setattr(os, "replace", full)
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "1")
    assert e.value.message.code == "preview.cache_failed" and not [n for n in env.cached() if n.endswith(".tmp") or "page" in n]


def test_испорченное_описание_в_кэше_считается_отсутствующим(env, worker):
    data = b"x"
    path = env.put("queue", "а.pdf", data)
    show(env, path)
    target = os.path.join(env.cache, SHA(data), "desc.json")
    for bad in (b"", "{не json".encode("utf-8"), b"[]", json.dumps({"v": 2, **PDF_DESCRIPTION}).encode(), json.dumps({"v": 1, "kind": "mail"}).encode(),
                json.dumps({"v": 1, **PDF_DESCRIPTION, "shown": 99}).encode(), b"\xff\xfe"):
        write(target, bad)
        assert show(env, path)["kind"] == "pages"
    assert worker.ops == ["describe"] * 8


def test_испорченная_страница_в_кэше_рисуется_заново(env, worker):
    data = b"x"
    path = env.put("queue", "а.pdf", data)
    show(env, path)
    P.page(env.home, "queue", path, "1")
    target = os.path.join(env.cache, SHA(data), "page-1.png")
    for bad in (b"", b"<html>", png(10, 10)[:-5], png(2001, 2000)):
        write(target, bad)
        assert P.page(env.home, "queue", path, "1") == png()
    assert worker.ops == ["describe"] + ["render"] * 5


# ── замки и семафор ─────────────────────────────────────────────
def hold(path):
    """Занимает замок-файл так, как занимает его родитель: flock на отдельном открытом файле."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def waits_for(condition, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def in_thread(fn):
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:                       # noqa: BLE001 — результат потока: и значение, и исключение
            box["error"] = e

    t = threading.Thread(target=target)
    t.start()
    return t, box


def test_занят_замок_объекта_show_сразу_отвечает_rendering_и_рабочий_процесс_не_запущен(env, worker):
    data = b"x"
    path = env.put("queue", "а.pdf", data)
    fd = hold(os.path.join(env.cache, SHA(data) + ".lock"))
    try:
        started = time.monotonic()
        assert show(env, path) == {"kind": "rendering"} and time.monotonic() - started < 1
    finally:
        os.close(fd)
    assert worker.calls == []
    assert show(env, path)["kind"] == "pages"                                          # замок отпущен — описание делается


def test_готовое_описание_отдаётся_даже_когда_замок_занят(env, worker):
    data = b"x"
    path = env.put("queue", "а.pdf", data)
    show(env, path)
    fd = hold(os.path.join(env.cache, SHA(data) + ".lock"))
    try:
        assert show(env, path)["kind"] == "pages"
    finally:
        os.close(fd)


def test_два_show_одного_объекта_одновременно_второй_получает_rendering(env, worker):
    path = env.put("queue", "а.pdf", b"x")
    worker.delay = 0.6
    first, box = in_thread(lambda: show(env, path))
    assert waits_for(lambda: worker.calls)
    started = time.monotonic()
    assert show(env, path) == {"kind": "rendering"} and time.monotonic() - started < 0.4
    first.join()
    assert box["value"]["kind"] == "pages" and worker.ops == ["describe"]


def test_страница_ждёт_описания_которое_делает_другой_запрос(env, worker):
    path = env.put("queue", "а.pdf", b"x")
    worker.delay = 0.4
    first, box = in_thread(lambda: show(env, path))
    assert waits_for(lambda: worker.calls)
    assert P.page(env.home, "queue", path, "1") == png()
    first.join()
    assert worker.ops == ["describe", "render"]                                        # описание одно: страница дождалась чужого


def test_два_одновременных_запроса_одной_страницы_рисуют_её_один_раз(env, worker):
    path = env.put("queue", "а.pdf", b"x")
    show(env, path)
    worker.delay = 0.5
    pages = [in_thread(lambda: P.page(env.home, "queue", path, "2")) for _ in range(3)]
    for t, _ in pages:
        t.join()
    assert [box.get("value") for _, box in pages] == [png()] * 3, [box.get("error") for _, box in pages]
    assert worker.ops == ["describe", "render"]


def test_разные_страницы_одного_файла_рисуются_параллельно_но_не_больше_двух(env, worker):
    path = env.put("queue", "а.pdf", b"x")
    worker.describe = lambda ext: {"kind": "pages", "type": "pdf", "pages": 20, "shown": 20}
    show(env, path)
    worker.delay = 0.4
    started = time.monotonic()
    pages = [in_thread(lambda n=n: P.page(env.home, "queue", path, str(n))) for n in range(1, 5)]
    for t, _ in pages:
        t.join()
    assert all("error" not in box for _, box in pages) and worker.ops.count("render") == 4
    assert worker.peak == 2 and time.monotonic() - started >= 0.75                    # четыре страницы по 0,4 с в два потока: не меньше двух кругов


def test_больше_двух_рабочих_процессов_одновременно_третий_ждёт(env, worker):
    worker.delay = 0.4
    paths = [env.put("queue", f"файл{i}.pdf", f"файл {i}".encode("utf-8")) for i in range(5)]
    started = time.monotonic()
    runs = [in_thread(lambda p=p: show(env, p)) for p in paths]
    for t, _ in runs:
        t.join()
    assert all(box["value"]["kind"] == "pages" for _, box in runs), [box for _, box in runs]
    assert worker.peak == 2 and worker.ops == ["describe"] * 5
    assert time.monotonic() - started >= 1.1                                           # пять по 0,4 с в два потока: три круга


def test_слоты_называются_и_лежат_в_кэше_второй_берётся_когда_занят_первый(env, worker):
    show(env, env.put("queue", "а.pdf", b"x"))
    assert P.SLOTS == 2 and ".slot0" in os.listdir(env.cache) and ".slot1" not in os.listdir(env.cache)          # свободный первый — второй не нужен
    fd = hold(os.path.join(env.cache, ".slot0"))
    try:
        show(env, env.put("queue", "б.pdf", b"y"))
    finally:
        os.close(fd)
    assert ".slot1" in os.listdir(env.cache) and ".slot2" not in os.listdir(env.cache)


def test_слоты_заняты_show_rendering_страница_отказ_рабочий_процесс_не_запущен(env, worker, monkeypatch):
    monkeypatch.setattr(P, "SLOT_WAIT", 0.2)
    path = env.put("queue", "а.pdf", b"x")
    fds = [hold(os.path.join(env.cache, f".slot{i}")) for i in range(2)]
    try:
        assert show(env, path) == {"kind": "rendering"}
        with pytest.raises(P.PreviewError) as e:
            P.page(env.home, "queue", path, "1")
        assert e.value.message.code == "preview.still_rendering"
    finally:
        for fd in fds:
            os.close(fd)
    assert worker.calls == [] and env.cached() == []


def test_один_слот_свободен_рабочий_процесс_идёт(env, worker):
    fd = hold(os.path.join(env.cache, ".slot0"))
    try:
        assert show(env, env.put("queue", "а.pdf", b"x"))["kind"] == "pages"
    finally:
        os.close(fd)


def test_страница_занята_другим_запросом_ждёт_не_дольше_предела_и_не_рисует_сама(env, worker, monkeypatch):
    data = b"x"
    path = env.put("queue", "а.pdf", data)
    show(env, path)
    monkeypatch.setattr(P, "PAGE_WAIT", 0.3)
    fd = hold(os.path.join(env.cache, f"{SHA(data)}.p2.lock"))
    try:
        started = time.monotonic()
        with pytest.raises(P.PreviewError) as e:
            P.page(env.home, "queue", path, "2")
        assert e.value.message.code == "preview.still_rendering" and 0.25 <= time.monotonic() - started < 3
        assert P.page(env.home, "queue", path, "1") == png()                          # замок страницы 2 страницу 1 не держит
    finally:
        os.close(fd)
    assert worker.ops == ["describe", "render"]


def test_страница_ждёт_описание_не_дольше_предела(env, worker, monkeypatch):
    data = b"x"
    path = env.put("queue", "а.pdf", data)
    monkeypatch.setattr(P, "PAGE_WAIT", 0.3)
    fd = hold(os.path.join(env.cache, SHA(data) + ".lock"))
    try:
        with pytest.raises(P.PreviewError) as e:
            P.page(env.home, "queue", path, "1")
        assert e.value.message.code == "preview.still_rendering"
    finally:
        os.close(fd)
    assert worker.calls == []


def test_параметры_ожидания_по_плану():
    assert (P.WORKER_TIMEOUT, P.PAGE_WAIT, P.SLOTS, P.CACHE_DAYS, P.CACHE_MAX) == (90, 60, 2, 7, 2 * 1024 ** 3)


# ── чистка кэша ─────────────────────────────────────────────────
NOW = 1_800_000_000.0
DAY = 86_400


def entry(env, name, size, age_days, now=NOW):
    folder = os.path.join(env.cache, name)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    write(os.path.join(folder, "page-1.png"), b"p" * size)
    stamp = now - age_days * DAY
    os.utime(os.path.join(folder, "page-1.png"), (stamp, stamp))
    os.utime(folder, (stamp, stamp))
    return folder


def test_clean_убирает_старше_семи_дней_и_оставляет_свежее(env):
    old, fresh = entry(env, "a" * 64, 100, 8), entry(env, "b" * 64, 100, 6)
    answer = P.clean(env.home, now=NOW)
    assert not os.path.exists(old) and os.path.exists(fresh)
    assert answer == {"removed": 1, "freed": 100, "left": 100}


def test_clean_по_объёму_убирает_самые_старые_пока_не_влезет(env, monkeypatch):
    monkeypatch.setattr(P, "CACHE_MAX", 1000)
    folders = [entry(env, c * 64, 400, age) for c, age in (("a", 3), ("b", 2), ("c", 1))]
    answer = P.clean(env.home, now=NOW)
    assert [os.path.exists(f) for f in folders] == [False, True, True]
    assert answer == {"removed": 1, "freed": 400, "left": 800}


def test_clean_пустого_или_отсутствующего_кэша(env):
    assert P.clean(env.home, now=NOW) == {"removed": 0, "freed": 0, "left": 0}
    os.makedirs(env.cache)
    assert P.clean(env.home, now=NOW) == {"removed": 0, "freed": 0, "left": 0}


def test_clean_не_трогает_объект_с_занятым_замком(env):
    old = entry(env, "a" * 64, 100, 30)
    fd = hold(os.path.join(env.cache, "a" * 64 + ".lock"))
    try:
        assert P.clean(env.home, now=NOW)["removed"] == 0 and os.path.exists(old)
    finally:
        os.close(fd)
    assert P.clean(env.home, now=NOW)["removed"] == 1 and not os.path.exists(old)


def test_clean_убирает_брошенные_рабочие_каталоги_и_старые_замки_но_не_слоты(env):
    os.makedirs(env.cache, mode=0o700)
    stale = os.path.join(env.cache, ".work-старый")
    fresh = os.path.join(env.cache, ".work-свежий")
    for folder, age in ((stale, 1), (fresh, 0)):
        os.mkdir(folder, 0o700)
        write(os.path.join(folder, "result.json"), b"{}")
        stamp = NOW - age * DAY
        os.utime(folder, (stamp, stamp))
    lock_old, lock_new, slot = (os.path.join(env.cache, n) for n in ("б" * 4 + ".lock", "в" * 4 + ".lock", ".slot0"))
    for p, age in ((lock_old, 30), (lock_new, 0), (slot, 30)):
        write(p, b"")
        os.utime(p, (NOW - age * DAY, NOW - age * DAY))
    P.clean(env.home, now=NOW)
    assert not os.path.exists(stale) and os.path.exists(fresh)
    assert not os.path.exists(lock_old) and os.path.exists(lock_new) and os.path.exists(slot)


def test_самочистка_по_объёму_после_записи_новое_не_удаляется(env, worker, monkeypatch):
    monkeypatch.setattr(P, "CACHE_MAX", 300)
    now = time.time()
    older = entry(env, "a" * 64, 400, 2, now=now)
    newer = entry(env, "b" * 64, 400, 1, now=now)
    data = "новый".encode("utf-8")
    path = env.put("queue", "а.pdf", data)
    assert show(env, path)["kind"] == "pages"
    assert not os.path.exists(older) and not os.path.exists(newer)                    # старые больше предела: ушли оба, самые старые первыми, новое осталось
    assert os.path.exists(os.path.join(env.cache, SHA(data), "desc.json"))
    worker.render = lambda page: png(40, 40)
    assert P.page(env.home, "queue", path, "1") == png(40, 40)
    assert os.path.exists(os.path.join(env.cache, SHA(data), "page-1.png"))


def test_самочистка_не_работает_пока_объём_в_пределе(env, worker):
    keep = entry(env, "a" * 64, 400, 30, now=time.time())                              # старше семи дней, но самочистка идёт по объёму, а не по возрасту
    show(env, env.put("queue", "а.pdf", b"x"))
    assert os.path.exists(keep)


# ── сборка командной строки bwrap ───────────────────────────────
def binds(argv):
    """Все привязки командной строки: [(ключ, источник, назначение)]."""
    found, i = [], 0
    while i < len(argv):
        if argv[i] in ("--bind", "--ro-bind", "--dev-bind", "--bind-try", "--ro-bind-try", "--ro-bind-fd", "--bind-fd"):
            found.append((argv[i], argv[i + 1], argv[i + 2]))
            i += 3
        else:
            i += 1
    return found


@pytest.fixture
def command(env, monkeypatch):
    monkeypatch.setattr(P, "bwrap_path", lambda: "/usr/bin/bwrap")
    code = env.tmp / "код"
    out = env.tmp / "выход"
    argv, environ = P.sandbox_command(str(code), "preview_worker.py", ["describe", "/in/data", "/out", "pdf"], str(out), 7,
                                      ["/home/u/.local/lib/python3.12/site-packages"])
    return argv, environ, str(code), str(out)


def test_команда_bwrap_без_сети_с_гибелью_вместе_с_родителем(command):
    argv, _, _, _ = command
    assert argv[0] == "/usr/bin/bwrap"
    assert "--unshare-all" in argv and "--die-with-parent" in argv and "--clearenv" in argv
    assert "--share-net" not in argv and "--dev-bind" not in argv and "--dev-bind-try" not in argv
    assert argv.index("--") > argv.index("--unshare-all")


def test_команда_bwrap_видит_только_свои_привязки_и_пишет_только_в_выход(command, env):
    argv, _, code, out = command
    found = binds(argv)
    writable = [dst for key, src, dst in found if key in ("--bind", "--bind-try", "--bind-fd")]
    assert writable == ["/out"] and [src for key, src, dst in found if dst == "/out"] == [out]
    ro = {dst: src for key, src, dst in found if key.startswith("--ro-bind")}
    assert ro["/usr"] == "/usr" and ro["/etc"] == "/etc" and ro["/code"] == code
    assert ro["/in/data"] == "7" and ro["/home/u/.local/lib/python3.12/site-packages"] == "/home/u/.local/lib/python3.12/site-packages"
    assert [k for k, s, d in found if d == "/in/data"] == ["--ro-bind-fd"]              # входной файл — по уже открытому дескриптору
    for key, src, dst in found:
        assert not src.startswith(env.home) and not env.home.startswith(src), (key, src)        # каталога архива среди привязок нет
    assert "/home" not in {dst for _, _, dst in found} and "/root" not in {dst for _, _, dst in found}


def test_команда_bwrap_окружение_и_запуск_кода(command):
    argv, environ, _, _ = command
    tail = argv[argv.index("--") + 1:]
    assert tail == [P.PYTHON, "-s", "-B", "/code/preview_worker.py", "describe", "/in/data", "/out", "pdf"]
    pairs = {argv[i + 1]: argv[i + 2] for i, a in enumerate(argv) if a == "--setenv"}
    assert pairs["HOME"] == "/tmp" and pairs["PYTHONNOUSERSITE"] == "1" and pairs["PYTHONDONTWRITEBYTECODE"] == "1"
    assert pairs["PYTHONPATH"] == "/home/u/.local/lib/python3.12/site-packages" and pairs["PATH"] == "/usr/bin:/bin"
    assert "--chdir" in argv and argv[argv.index("--chdir") + 1] == "/tmp"
    assert set(environ) <= {"PATH", "LANG", "LC_ALL"} and "FLYARCHIVE_HOME" not in environ and "FLYARCHIVE_LOCAL_TOKEN" not in environ


def test_команда_bwrap_память_tmp_ограничена(command):
    argv, _, _, _ = command
    i = argv.index("--tmpfs")
    assert argv[i + 1] == "/tmp" and argv[i - 2] == "--size" and 0 < int(argv[i - 1]) <= 256 << 20


def test_системные_каталоги_ссылками_или_привязкой_только_для_чтения(command):
    argv, _, _, _ = command
    links = [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == "--symlink"]
    for d in ("/bin", "/sbin", "/lib", "/lib64"):
        if os.path.islink(d):
            assert (os.readlink(d), d) in links
        elif os.path.isdir(d):
            assert ("--ro-bind", d, d) in binds(argv)


def test_библиотеки_берутся_из_site_packages_вне_usr_и_вне_архива(env, tmp_path):
    good, other, inside = tmp_path / "а" / "site-packages", tmp_path / "б" / "dist-packages", os.path.join(env.home, "venv", "site-packages")
    for d in (good, other, inside, tmp_path / "в" / "lib"):
        os.makedirs(d)
    found = P.library_dirs(env.home, [str(good), str(other), inside, str(tmp_path / "в" / "lib"), str(tmp_path / "нет" / "site-packages"),
                                      "/usr/lib/python3/dist-packages", str(good) + "/", str(good)])
    assert found == [str(good), str(other)]


def test_библиотеки_по_умолчанию_находят_настоящие_pymupdf_и_pillow_там_где_они_стоят(env):
    pymupdf = pytest.importorskip("pymupdf")
    found = P.library_dirs(env.home)
    where = os.path.dirname(os.path.dirname(os.path.realpath(pymupdf.__file__)))
    assert where.startswith("/usr/") or where in found


# ── что попадает в запуск ───────────────────────────────────────
def test_имя_файла_и_путь_в_командную_строку_bwrap_не_попадают_вход_только_по_дескриптору(env, monkeypatch):
    seen = []

    def popen(argv, **kw):
        seen.append((argv, kw))
        raise OSError("настоящий запуск не нужен")

    monkeypatch.setattr(P, "bwrap_path", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(P.subprocess, "Popen", popen)
    odd = "a b; rm -rf x $(id) `id` \"q\" 'ş'.txt"
    answer = show(env, env.put("queue", odd, b"data"))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.worker_failed" and answer["note"]["args"] == {"why": "OSError"}
    ((argv, kw),) = seen
    assert not [a for a in argv if "rm -rf" in a or odd in a or BATCH in a or "очередь" in a]
    assert [a for a in argv if env.home in a] == [argv[argv.index("--bind") + 1]]         # от архива в запуске — только пустой выходной каталог
    assert os.path.basename(argv[argv.index("--bind") + 1]).startswith(".work-")
    assert kw["env"] == {"PATH": "/usr/bin:/bin"} and len(kw["pass_fds"]) == 1 and kw["start_new_session"] is True
    assert kw["stdin"] == P.subprocess.DEVNULL and kw["stdout"] == P.subprocess.DEVNULL


@pytest.mark.parametrize("where", ["cache", "cache/preview"])
def test_кэш_не_каталог_none_и_отказ_страницы_с_пояснением(env, worker, where):
    target = os.path.join(env.home, *where.split("/"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    write(target, b"file instead of a directory")
    path = env.put("queue", "а.pdf", b"x")
    answer = show(env, path)
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.cache_failed" and answer["note"]["args"] == {"error_type": "NotADirectoryError"}
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, "1")
    assert e.value.message.code == "preview.cache_failed" and worker.calls == []


def test_кэш_есть_но_рабочий_каталог_создать_нельзя_none_с_пояснением(env, worker, monkeypatch):
    path = env.put("queue", "а.pdf", b"x")
    show(env, path)                                                                    # кэш и каталоги созданы

    def denied(*a, **k):
        raise PermissionError(13, "нет прав")

    monkeypatch.setattr(P.tempfile, "mkdtemp", denied)
    answer = show(env, env.put("queue", "б.pdf", b"y"))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.cache_failed" and answer["note"]["args"] == {"error_type": "PermissionError"}

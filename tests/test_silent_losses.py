"""Молчаливых потерь без библиотек нет (FR-107).

Три места, где отсутствие библиотеки проглатывалось: olefile (старые doc, xls, ppt и письма msg считались «не документ» без объяснения), PyMuPDF
(pdf принимался с пустым текстом, как будто проверен) и extract_msg (письмо msg получало пустые ключи сверки, повторы не узнавались). Теперь документ
не принимается «как будто проверен» и не пропадает молча: отказ или находка называют библиотеку (сообщение lib.missing); сборка базы известного
называет её в замечании. Поведение при установленных библиотеках то же — рядом каждый раз проверка с настоящей библиотекой.
Отсутствие библиотеки — блокировка импорта (sys.modules[имя] = None), не удаление.
"""
import os
import sys

import pytest

import filetype_sniff as FT
import gate as G
import gatekit as K
import known as N
import messages as M
from archivekit import Archive

# Тесты «без библиотеки» блокируют импорт сами и идут на любой машине; тесты «с библиотекой» берут настоящую и без неё пропускаются, называя её.


def put(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def block(monkeypatch, *names):
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)


def lib_missing(package, use, file):
    return M.make("lib.missing", package=package, use=use, file=file)


# ── olefile: старые форматы Office и письма msg ─────────────────
def test_с_olefile_письмо_msg_опознаётся_как_письмо_как_раньше(tmp_path):
    pytest.importorskip("olefile")
    k = FT.detect(put(tmp_path, "письмо.msg", K.msg_bytes()))
    assert (k.family, k.type, k.detail) == ("mail", "msg", "")


def test_без_olefile_контейнер_ole_называет_причину_в_детали_а_не_просто_не_документ(tmp_path, monkeypatch):
    path = put(tmp_path, "письмо.msg", K.msg_bytes())
    block(monkeypatch, "olefile")
    k = FT.detect(path)
    assert (k.family, k.type, k.detail) == ("other", "ole", "no-olefile")


def test_без_olefile_приёмка_отказывает_и_называет_библиотеку_а_не_пишет_тип_не_поддерживается(tmp_path, monkeypatch):
    path = put(tmp_path, "договор.doc", K.msg_bytes())
    block(monkeypatch, "olefile")
    v = G.check_file(path)
    assert v["decision"] == "skip", "не принят «как будто проверен»"
    assert v["reason_msg"]["code"] == "lib.missing" and v["reason_msg"]["args"] == {"package": "olefile", "use": "ole", "file": "requirements-optional.txt"}
    assert "olefile" in v["reason"] and v["reason"] != str(M.make("reason.not_document"))


def test_с_olefile_приёмка_старых_форматов_та_же_письмо_принимается(tmp_path):
    pytest.importorskip("olefile")
    pytest.importorskip("extract_msg")                              # письмо msg принимается, когда его читает extract_msg
    v = G.check_file(put(tmp_path, "письмо.msg", K.msg_bytes()))
    assert (v["family"], v["type"], v["decision"]) == ("mail", "msg", "accept") and v["reason"] == ""


def test_контейнер_ole_без_потоков_и_с_olefile_остаётся_не_документом_без_упоминания_библиотеки(tmp_path):
    pytest.importorskip("olefile")
    k = FT.detect(put(tmp_path, "заготовка.doc", K.OLE))
    assert k.detail != "no-olefile"


# ── PyMuPDF: pdf ────────────────────────────────────────────────
def test_с_pymupdf_pdf_принимается_а_без_текста_идёт_моделью_как_раньше(tmp_path):
    pytest.importorskip("pymupdf")
    ok = G.check_file(put(tmp_path, "а.pdf", K.pdf_plain(b"Hello")))
    assert ok["decision"] == "accept" and [f["rule"] for f in ok["findings"]] == []
    scan = G.check_file(put(tmp_path, "скан.pdf", K.pdf_blank()))
    assert [f["rule"] for f in scan["findings"]] == ["needs_vision"]


def test_без_pymupdf_pdf_не_принимается_как_проверенный_находка_называет_библиотеку(tmp_path, monkeypatch):
    path = put(tmp_path, "а.pdf", K.pdf_plain(b"Hello"))
    block(monkeypatch, "pymupdf", "fitz")
    v = G.check_file(path)
    assert v["decision"] == "review", "раньше pdf принимался с пустым текстом"
    (f,) = v["findings"]
    assert (f["rule"], f["level"]) == ("unreadable", "HIGH")
    assert f["msg"] == lib_missing("pymupdf", "pdf", "requirements.txt").to_json() and "pymupdf" in f["quote"]
    assert f["where_msg"]["code"] == "where.file"


def test_без_pymupdf_находки_по_сырому_файлу_остаются_и_к_ним_добавляется_библиотека(tmp_path, monkeypatch):
    path = put(tmp_path, "сценарий.pdf", K.pdf_with_js())
    block(monkeypatch, "pymupdf", "fitz")
    v = G.check_file(path)
    assert v["decision"] == "review" and sorted(f["rule"] for f in v["findings"]) == ["active_content", "unreadable"]


def test_старое_имя_библиотеки_fitz_при_отсутствии_нового_считается_библиотекой_на_месте(tmp_path, monkeypatch):
    class Page:
        def get_text(self):
            return "Текст страницы"

    class Doc:
        needs_pass = False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def xref_length(self):
            return 1

        def __iter__(self):
            return iter([Page()])

    class Fitz:                                                     # PyMuPDF до 1.24.3: модуль называется только fitz
        class TOOLS:
            mupdf_display_errors = staticmethod(lambda flag: None)

        open = staticmethod(lambda path: Doc())

    path = put(tmp_path, "а.pdf", K.pdf_plain(b"Hello"))
    block(monkeypatch, "pymupdf")
    monkeypatch.setitem(sys.modules, "fitz", Fitz)
    v = G.check_file(path)
    assert v["decision"] == "accept" and v["findings"] == []


def test_сообщение_об_отсутствии_библиотеки_помещается_в_цитату_находки():
    for package, use, file in (("pymupdf", "pdf", "requirements.txt"), ("extract-msg", "msg", "requirements-optional.txt"),
                               ("olefile", "ole", "requirements-optional.txt"), ("lancedb", "index", "requirements.txt")):
        assert len(lib_missing(package, use, file)) <= G.QUOTE, package


# ── extract_msg: письма msg в приёмке ───────────────────────────
def test_с_extract_msg_письмо_читается_и_принимается_как_раньше(tmp_path):
    pytest.importorskip("olefile")                                  # письмо msg узнаётся по olefile, читает его extract_msg
    pytest.importorskip("extract_msg")
    v = G.check_file(put(tmp_path, "письмо.msg", K.msg_bytes(subject="Договор", body="Добрый день.")))
    assert v["decision"] == "accept" and (v["mid"] or v["fp"])


def test_без_extract_msg_письмо_msg_не_принимается_а_находка_называет_библиотеку(tmp_path, monkeypatch):
    pytest.importorskip("olefile")                                  # без olefile письмо msg не опознаётся: находка называла бы её, а не extract-msg
    path = put(tmp_path, "письмо.msg", K.msg_bytes())
    block(monkeypatch, "extract_msg")
    v = G.check_file(path)
    assert v["decision"] == "review"
    (f,) = v["findings"]
    assert (f["rule"], f["level"]) == ("unreadable", "HIGH")
    assert f["msg"] == lib_missing("extract-msg", "msg", "requirements-optional.txt").to_json()


# ── extract_msg: база известного ────────────────────────────────
@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    (root / "входящие" / "п").mkdir(parents=True)
    (root / "входящие" / "п" / "письмо.msg").write_bytes(K.msg_bytes(subject="Договор поставки"))
    (root / "входящие" / "п" / "записка.txt").write_text("Просто записка.", encoding="utf-8")
    return root, str(tmp_path / "known.sqlite")


def test_с_extract_msg_база_читает_ключи_письма_и_пропусков_нет(corpus):
    pytest.importorskip("extract_msg")
    root, db = corpus
    stats = N.build(str(root), db)
    assert stats["files"] == 2 and stats["mail"] == 1 and stats["missing"] == {}


def test_без_extract_msg_сборка_базы_называет_библиотеку_и_число_писем_которых_она_не_прочла(corpus, monkeypatch):
    pytest.importorskip("olefile")                                  # без olefile файл msg не узнаётся как письмо: считать нечего
    root, db = corpus
    block(monkeypatch, "extract_msg")
    stats = N.build(str(root), db)
    assert stats["files"] == 2 and stats["mail"] == 0 and stats["missing"] == {"extract-msg": 1}


def test_письмо_без_ключей_читается_заново_когда_библиотека_появилась(corpus, monkeypatch):
    pytest.importorskip("extract_msg")
    root, db = corpus
    with monkeypatch.context() as m:
        block(m, "extract_msg")
        assert N.build(str(root), db)["mail"] == 0
    stats = N.build(str(root), db)
    assert (stats["added"], stats["mail"], stats["missing"]) == (1, 1, {}), "ключи не посчитаны — запись не должна считаться готовой"
    assert N.build(str(root), db)["added"] == 0


def test_сообщение_о_непрочитанных_письмах_называет_библиотеку_число_и_команду():
    text = str(M.make("known.mail_not_read", count=3, package="extract-msg"))
    assert "3" in text and "extract-msg" in text and "flyarchive known build" in text


@pytest.fixture
def archive(tmp_path):
    return Archive(tmp_path)


def msg_in_corpus(archive):
    folder = os.path.join(archive.path("corpus"), "входящие", "п")
    os.makedirs(folder)
    with open(os.path.join(folder, "письмо.msg"), "wb") as f:
        f.write(K.msg_bytes())


def test_команда_known_build_без_extract_msg_пишет_замечание(archive):
    pytest.importorskip("olefile")                                  # без olefile файл msg не узнаётся как письмо
    msg_in_corpus(archive)
    code, out, err = archive("known", "build", **archive.hide("extract_msg"))
    assert code == 0 and "База известного собрана" in out
    assert err.splitlines()[-1] == "замечание: " + str(M.make("known.mail_not_read", count=1, package="extract-msg")) and "Traceback" not in err


def test_команда_known_build_с_extract_msg_читает_заново_и_молчит(archive):
    pytest.importorskip("extract_msg")
    msg_in_corpus(archive)
    assert archive("known", "build", **archive.hide("extract_msg"))[0] == 0
    code, out, err = archive("known", "build")
    assert code == 0 and "прочитано заново: 1" in out and "замечание" not in err

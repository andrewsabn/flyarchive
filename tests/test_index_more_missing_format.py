"""Дозаполнитель не теряет файл молча, когда нет библиотеки формата (FR-107).

`python3 tools/index_more.py` читает docx, xlsx, pptx, msg и pdf библиотеками формата. Раньше файл, который не прочитан из-за отсутствия библиотеки,
печатался как «нечитаемо» и записывался в `index/ingested.txt` как пройденный: после установки библиотеки он уже не попадал в индекс. Теперь такой файл
в список пройденного не пишется: причина названа сообщением `lib.missing` с именем библиотеки (один раз на библиотеку за запуск), число пропущенных
файлов стоит в итоговой строке, код возврата прежний, а повторный запуск с библиотекой файл индексирует. Файл, который не читается по другой причине
(битый), ведёт себя как раньше. Отсутствие библиотеки — блокировка импорта в дочернем процессе (sitecustomize), не удаление.
"""
import os
import re

import pytest

import index_more as X
import messages as M
import gatekit as K
from archivekit import Archive
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

TEXT = "Договор поставки оборудования для серверной."
# формат -> (библиотеки, чей импорт блокируется, пакет и вид слов в сообщении, файл с требованиями, имя файла, содержимое)
FORMATS = {
    "docx": (("docx",), "python-docx", "requirements-optional.txt", "договор.docx", lambda: K.docx_page(TEXT)),
    "xlsx": (("openpyxl",), "openpyxl", "requirements-optional.txt", "таблица.xlsx", lambda: K.xlsx_sheet((("Позиция", "Сервер"),))),
    "pptx": (("pptx",), "python-pptx", "requirements-optional.txt", "слайды.pptx", lambda: K.pptx_slide(TEXT)),
    "msg": (("extract_msg",), "extract-msg", "requirements-optional.txt", "письмо.msg", lambda: K.msg_bytes(subject="Договор", body=TEXT)),
    "pdf": (("pymupdf", "fitz"), "pymupdf", "requirements.txt", "скан.pdf", lambda: K.pdf_plain(b"Hello")),
}
SUMMARY = re.compile(r"^наполнение закончено: \+(\d+) чанков из (\d+) файлов(.*)$", re.M)


def note(kind):
    _, package, file, _, _ = FORMATS[kind]
    return str(M.make("lib.missing", package=package, use=kind, file=file))


@pytest.fixture
def archive(tmp_path, vectors):
    made = Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})
    assert made("init")[0] == 0
    return made


def put(archive, name, data):
    folder = archive.path("corpus", "входящие", "пачка")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def done(archive):
    with open(archive.path("index", "ingested.txt"), encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f]


def indexed(archive):
    return sorted({row["path"] for row in archive.table().search().limit(1000).to_list()})


def summary(out):
    found = SUMMARY.findall(out)
    assert len(found) == 1, out
    return found[0]


# ── один файл без библиотеки: не потерян, причина названа ───────
@pytest.mark.parametrize("kind", list(FORMATS))
def test_файл_без_библиотеки_формата_не_пишется_в_пройденные_и_причина_названа(archive, kind):
    modules, _, _, name, make = FORMATS[kind]
    pytest.importorskip(modules[0])
    path = put(archive, name, make())
    code, out, err = archive.tool("index_more.py", **archive.hide(*modules))
    assert code == 0 and "Traceback" not in err, (out, err)
    assert out.count(note(kind)) == 1 and "нечитаемо" not in out, out
    assert path not in done(archive), "файл не записан как пройденный: после установки библиотеки он должен попасть в индекс"
    assert indexed(archive) == []
    chunks, files, rest = summary(out)
    assert (chunks, files) == ("0", "0") and "пропущено без библиотек: 1" in rest, out


@pytest.mark.parametrize("kind", list(FORMATS))
def test_после_установки_библиотеки_повторный_запуск_индексирует_пропущенный_файл(archive, kind):
    modules, _, _, name, make = FORMATS[kind]
    pytest.importorskip(modules[0])
    path = put(archive, name, make())
    assert archive.tool("index_more.py", **archive.hide(*modules))[0] == 0
    code, out, err = archive.tool("index_more.py")
    assert code == 0 and "lib.missing" not in out and "нет библиотеки" not in out, (out, err)
    assert indexed(archive) == ["входящие/пачка/" + name] and path in done(archive)
    assert "пропущено без библиотек" not in summary(out)[2], "в этот раз пропусков нет"
    code, out, err = archive.tool("index_more.py")                               # и в третий раз файл уже пройден, а не берётся снова
    assert code == 0 and summary(out)[:2] == ("0", "0"), out


# ── несколько файлов ────────────────────────────────────────────
def test_причина_называется_один_раз_на_библиотеку_а_в_итоге_считаются_все_пропущенные(archive):
    pytest.importorskip("docx")
    pytest.importorskip("openpyxl")
    put(archive, "а.docx", K.docx_page("Первый документ."))
    put(archive, "б.docx", K.docx_page("Второй документ."))
    put(archive, "в.xlsx", K.xlsx_sheet((("Позиция", "Сервер"),)))
    plain = put(archive, "г.txt", TEXT)
    code, out, err = archive.tool("index_more.py", **archive.hide("docx", "openpyxl"))
    assert code == 0, (out, err)
    assert out.count(note("docx")) == 1 and out.count(note("xlsx")) == 1, out
    assert "нечитаемо" not in out
    chunks, files, rest = summary(out)
    assert files == "1" and int(chunks) >= 1 and "пропущено без библиотек: 3" in rest, out
    assert indexed(archive) == ["входящие/пачка/г.txt"] and done(archive) == [plain], "читаемое в индексе и в пройденных, остальное — нет"


def test_читаемое_рядом_с_пропущенным_индексируется_в_том_же_запуске(archive):
    pytest.importorskip("docx")
    put(archive, "договор.docx", K.docx_page(TEXT))
    put(archive, "заметка.md", "# Заметка\n\nПеренос релиза на четверг.\n")
    code, out, err = archive.tool("index_more.py", **archive.hide("docx"))
    assert code == 0, (out, err)
    assert indexed(archive) == ["входящие/пачка/заметка.md"]


# ── прежнее поведение ───────────────────────────────────────────
def test_битый_файл_при_установленной_библиотеке_печатается_нечитаемым_и_пишется_в_пройденные_как_раньше(archive):
    pytest.importorskip("pptx")
    path = put(archive, "битый.pptx", "это не презентация")
    code, out, err = archive.tool("index_more.py")
    assert code == 0, (out, err)
    assert "нечитаемо (" in out and path in out and "lib.missing" not in out and "нет библиотеки" not in out, out
    assert path in done(archive)
    assert "пропущено без библиотек" not in summary(out)[2]


def test_итоговая_строка_без_пропусков_прежняя(archive):
    put(archive, "заметка.md", "# Заметка\n\nПеренос релиза на четверг.\n")
    code, out, err = archive.tool("index_more.py")
    assert code == 0, (out, err)
    assert re.search(r"^наполнение закончено: \+\d+ чанков из 1 файлов$", out, re.M), out


# ── определение «нет библиотеки» ────────────────────────────────
@pytest.mark.parametrize("name, package, use", [("docx", "python-docx", "docx"), ("docx.shared", "python-docx", "docx"),
                                                ("openpyxl", "openpyxl", "xlsx"), ("pptx", "python-pptx", "pptx"),
                                                ("extract_msg", "extract-msg", "msg"), ("fitz", "pymupdf", "pdf"), ("pymupdf", "pymupdf", "pdf")])
def test_нет_модуля_формата_называется_сообщением_с_пакетом(name, package, use):
    found = X.missing_library(ModuleNotFoundError(f"No module named {name!r}", name=name))
    assert found is not None and (found.code, found.args["package"], found.args["use"]) == ("lib.missing", package, use)


@pytest.mark.parametrize("error", [ModuleNotFoundError("No module named 'lxml'", name="lxml"), ImportError("cannot import name", name="docx"),
                                   ValueError("битый"), OSError("нет файла"), ModuleNotFoundError("без имени")])
def test_чужой_сбой_импорта_и_прочие_ошибки_отсутствием_библиотеки_не_называются(error):
    assert X.missing_library(error) is None

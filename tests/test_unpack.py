"""Безопасная распаковка: FR-30, FR-32, FR-33, FR-34."""
import bz2
import gzip
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import zipfile

import pytest

import gatekit as K
import messages as M
import unpack as U

SMALL = U.Limits(max_bytes=1024 * 1024, max_files=5, max_ratio=100, max_depth=3, ratio_floor=64 * 1024)
has_7z = pytest.mark.skipif(shutil.which("7z") is None, reason="нет 7z")


@pytest.fixture
def box(tmp_path):
    class Box:
        dest = str(tmp_path / "out")

        def put(self, name, data):
            p = tmp_path / name
            p.write_bytes(data)
            return str(p)

        def tree(self):
            out = {}
            for root, _, files in os.walk(self.dest):
                for f in files:
                    p = os.path.join(root, f)
                    out[os.path.relpath(p, self.dest).replace(os.sep, "/")] = open(p, "rb").read()
            return out

        def outside(self):
            """Всё, что появилось во временном каталоге теста помимо исходных архивов и каталога распаковки."""
            return sorted(n for n in os.listdir(tmp_path) if n != "out" and not n.startswith("src"))

    return Box()


def fails(box, path, reason, limits=U.Limits()):
    with pytest.raises(U.UnpackError) as e:
        U.unpack(path, box.dest, limits)
    assert e.value.reason == reason, e.value
    assert not os.path.exists(box.dest) or box.tree() == {}      # при отказе ничего не остаётся
    assert box.outside() == []
    return e.value


# ── обычные архивы ──────────────────────────────────────────────
def test_zip_распаковывается_с_вложенными_каталогами(box):
    src = box.put("src.zip", K.zip_bytes({"a.txt": b"one", "папка/письмо.eml": K.EML, "папка/глубже/b.pdf": K.PDF}))
    names = U.unpack(src, box.dest)
    assert box.tree() == {"a.txt": b"one", "папка/письмо.eml": K.EML, "папка/глубже/b.pdf": K.PDF}
    assert sorted(n for n, _ in names) == ["a.txt", "папка/глубже/b.pdf", "папка/письмо.eml"]
    assert all(os.path.isfile(p) for _, p in names)


def test_русское_имя_в_кодировке_dos_восстанавливается(box):
    src = box.put("src.zip", K.zip_cp866("Договор поставки.txt", b"text"))
    U.unpack(src, box.dest)
    assert list(box.tree()) == ["Договор поставки.txt"]


def test_обратные_слэши_в_именах_это_каталоги(box):
    src = box.put("src.zip", K.zip_raw([("Inbox\\2024\\письмо.eml", K.EML)]))
    U.unpack(src, box.dest)
    assert list(box.tree()) == ["Inbox/2024/письмо.eml"]


def test_право_на_исполнение_снимается_каталоги_закрыты(box):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("run.sh")
        info.external_attr = 0o100755 << 16
        z.writestr(info, b"#!/bin/sh\n")
        z.writestr("d/x.txt", b"x")
    U.unpack(box.put("src.zip", buf.getvalue()), box.dest)
    assert stat.S_IMODE(os.stat(os.path.join(box.dest, "run.sh")).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.join(box.dest, "d")).st_mode) == 0o700


def test_одинаковые_имена_не_затирают_друг_друга(box):
    src = box.put("src.zip", K.zip_raw([("a.txt", b"first"), ("a.txt", b"second"), ("A.TXT", b"third")]))
    names = U.unpack(src, box.dest)
    assert sorted(box.tree().values()) == [b"first", b"second", b"third"]
    assert len(names) == 3


def test_пустой_архив_даёт_пустой_список(box):
    assert U.unpack(box.put("src.zip", K.zip_bytes({})), box.dest) == []


@pytest.mark.parametrize("mode, name", [("w", "src.tar"), ("w:gz", "src.tar.gz"), ("w:bz2", "src.tar.bz2"), ("w:xz", "src.tar.xz")])
def test_tar_и_сжатый_tar(box, mode, name):
    src = box.put(name, K.tar_bytes({"a.txt": b"one", "папка/b.pdf": K.PDF}, mode))
    U.unpack(src, box.dest)
    assert box.tree() == {"a.txt": b"one", "папка/b.pdf": K.PDF}


@pytest.mark.parametrize("pack, name", [(gzip.compress, "src-note.txt.gz"), (bz2.compress, "src-note.txt.bz2")])
def test_одиночный_сжатый_файл(box, pack, name):
    U.unpack(box.put(name, pack("заметка".encode("utf-8"))), box.dest)
    assert box.tree() == {"src-note.txt": "заметка".encode("utf-8")}


@has_7z
def test_7z_распаковывается(box, tmp_path):
    inner = tmp_path / "src-in"
    (inner / "папка").mkdir(parents=True)
    (inner / "a.txt").write_bytes(b"one")
    (inner / "папка" / "b.pdf").write_bytes(K.PDF)
    subprocess.run(["7z", "a", "-bd", str(tmp_path / "src.7z"), "."], cwd=inner, check=True, capture_output=True)
    U.unpack(str(tmp_path / "src.7z"), box.dest)
    assert box.tree() == {"a.txt": b"one", "папка/b.pdf": K.PDF}


# ── выход за каталог ────────────────────────────────────────────
@pytest.mark.parametrize("name", ["../evil.txt", "../../.bashrc", "a/../../evil.txt", "/etc/passwd", "/tmp/evil.txt",
                                  "C:\\Windows\\evil.txt", "..\\..\\evil.txt", "a/b/../../../evil.txt", "\\\\server\\share\\x"])
def test_zip_с_выходом_за_каталог_отвергается_целиком(box, name):
    src = box.put("src.zip", K.zip_raw([("ok.txt", b"ok"), (name, b"evil")]))
    fails(box, src, "traversal")


@pytest.mark.parametrize("name", ["../evil.txt", "/etc/passwd", "a/../../evil.txt"])
def test_tar_с_выходом_за_каталог_отвергается(box, name):
    fails(box, box.put("src.tar", K.tar_bytes({"ok.txt": b"ok", name: b"evil"})), "traversal")


def test_tar_со_ссылкой_отвергается(box):
    fails(box, box.put("src.tar", K.tar_bytes({"ok.txt": b"ok"}, links={"link": "/etc/passwd"})), "link")


def test_zip_со_ссылкой_отвергается(box):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        z.writestr(info, b"/etc/passwd")
    fails(box, box.put("src.zip", buf.getvalue()), "link")


# ── пароль и повреждение ────────────────────────────────────────
def test_zip_с_паролем_отвергается(box):
    fails(box, box.put("src.zip", K.zip_encrypted_flag({"a.txt": b"secret"})), "encrypted")


@has_7z
@pytest.mark.parametrize("extra", [[], ["-mhe=on"]])
def test_7z_с_паролем_отвергается(box, tmp_path, extra):
    (tmp_path / "src-a.txt").write_bytes(b"secret")
    subprocess.run(["7z", "a", "-bd", "-pS3cret", *extra, str(tmp_path / "src.7z"), str(tmp_path / "src-a.txt")],
                   check=True, capture_output=True)
    fails(box, str(tmp_path / "src.7z"), "encrypted")


@pytest.mark.parametrize("name, data", [("src.zip", b"PK\x03\x04" + b"\x00" * 60), ("src.tar", b"\x00" * 257 + b"ustar\x00" + b"junk" * 64),
                                        ("src.gz", b"\x1f\x8b\x08\x00junkjunkjunk"), ("src.7z", K.SEVENZ)])
def test_битый_архив_отвергается(box, name, data):
    if name.endswith(".7z") and shutil.which("7z") is None:
        pytest.skip("нет 7z")
    fails(box, box.put(name, data), "broken")


def test_не_архив_отвергается(box):
    fails(box, box.put("src.pdf", K.PDF), "unsupported")


# ── пределы ─────────────────────────────────────────────────────
def test_слишком_много_файлов(box):
    src = box.put("src.zip", K.zip_bytes({f"f{i}.txt": b"x" for i in range(6)}))
    assert "файлов" in str(fails(box, src, "bomb", SMALL))
    assert len(U.unpack(box.put("src2.zip", K.zip_bytes({f"f{i}.txt": b"x" for i in range(5)})), box.dest, SMALL)) == 5


def test_слишком_большой_объём_распаковка_прерывается(box):
    src = box.put("src.zip", K.zip_bytes({"a.bin": os.urandom(700 * 1024), "b.bin": os.urandom(700 * 1024)}))
    fails(box, src, "bomb", SMALL)


def test_сжатие_сильнее_предела_это_бомба(box):
    src = box.put("src.zip", K.zip_bytes({"zeros.bin": b"\x00" * (900 * 1024)}))
    assert "сжат" in str(fails(box, src, "bomb", SMALL))


def test_мелкий_сильно_сжатый_файл_не_бомба(box):
    src = box.put("src.zip", K.zip_bytes({"zeros.txt": b"0" * (30 * 1024)}))      # ниже порога, с которого считается сжатие
    assert len(U.unpack(src, box.dest, SMALL)) == 1


def test_бомба_в_одиночном_сжатом_файле(box):
    fails(box, box.put("src.gz", gzip.compress(b"\x00" * (3 * 1024 * 1024))), "bomb", SMALL)


def test_бомба_в_tar(box):
    fails(box, box.put("src.tar.gz", K.tar_bytes({"zeros.bin": b"\x00" * (2 * 1024 * 1024)}, "w:gz")), "bomb", SMALL)


def test_общий_счёт_на_вложенные_архивы(box, tmp_path):
    budget = U.Budget()
    U.unpack(box.put("src1.zip", K.zip_bytes({f"a{i}.txt": b"x" for i in range(3)})), box.dest, SMALL, budget=budget)
    with pytest.raises(U.UnpackError) as e:
        U.unpack(box.put("src2.zip", K.zip_bytes({f"b{i}.txt": b"x" for i in range(3)})), str(tmp_path / "out2"), SMALL, budget=budget)
    assert e.value.reason == "bomb" and budget.files >= 3


def test_пределы_по_умолчанию_из_требований():
    d = U.Limits()
    assert (d.max_bytes, d.max_files, d.max_ratio, d.max_depth) == (2 * 1024 ** 3, 5000, 100, 3)


def test_каталог_назначения_должен_быть_пуст(box):
    os.makedirs(box.dest)
    with open(os.path.join(box.dest, "чужой.txt"), "w") as f:
        f.write("x")
    with pytest.raises(U.UnpackError) as e:
        U.unpack(box.put("src.zip", K.zip_bytes({"a.txt": b"x"})), box.dest)
    assert e.value.reason == "dest" and os.path.exists(os.path.join(box.dest, "чужой.txt"))


# ── время изменения файлов сохраняется: по нему берётся дата документа (FR-45) ──
def test_zip_время_изменения_файла_сохраняется(box):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("отчёт.txt", date_time=(2019, 5, 17, 10, 30, 0)), "текст")
    ((_, path),) = U.unpack(box.put("src.zip", buf.getvalue()), box.dest)
    import time
    assert time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))) == "2019-05-17 10:30"


def test_tar_время_изменения_файла_сохраняется(box):
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo("отчёт.txt")
        data = "текст".encode("utf-8")
        info.size, info.mtime = len(data), 1_558_089_000
        t.addfile(info, io.BytesIO(data))
    ((_, path),) = U.unpack(box.put("src.tar", buf.getvalue()), box.dest)
    assert int(os.path.getmtime(path)) == 1_558_089_000


def test_негодное_время_в_архиве_распаковку_не_роняет(box):
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo("отчёт.txt")
        info.size, info.mtime = 5, -5_000_000_000_000
        t.addfile(info, io.BytesIO(b"12345"))
    assert len(U.unpack(box.put("src.tar", buf.getvalue()), box.dest)) == 1


@pytest.mark.parametrize("seconds", [1e20, float("nan"), float("inf"), None, "вчера", -1, 0, 4_102_444_800])
def test_негодное_время_не_ставится_и_не_роняет(box, seconds):
    path = box.put("src-файл.txt", b"x")
    before = os.stat(path).st_mtime_ns
    U._stamp(path, seconds)
    assert os.stat(path).st_mtime_ns == before


# ── точки с пробелом или управляющим знаком в имени: выход за каталог ──
@pytest.mark.parametrize("name", [".. /evil.txt", "a/.. /.. /evil.txt", " ../evil.txt", "..\t/evil.txt", "..\x01/evil.txt",
                                  ".. /evil.txt", ". ./evil.txt", ".../evil.txt", ".. ./evil.txt", "a\\.. \\..\\evil.txt"])
def test_точки_с_пробелом_или_управляющим_знаком_это_тоже_выход_за_каталог(box, name):
    """Часть имени сначала очищается, потом проверяется: «.. » после очистки — это «..»."""
    src = box.put("src.zip", K.zip_raw([("ok.txt", b"ok"), (name, b"evil")]))
    fails(box, src, "traversal")


@pytest.mark.parametrize("name", [".. /x", "a/ .. /b", "..\t", ". .", "..."])
def test_разбор_имени_не_пропускает_точки_ни_в_каком_виде(name):
    with pytest.raises(U.UnpackError) as e:
        U.safe_rel(name)
    assert e.value.reason == "traversal"


@pytest.mark.parametrize("name, parts", [("a/./b.txt", ["a", "b.txt"]), ("отчёт за год.docx", ["отчёт за год.docx"]),
                                         (" a / b.txt ", ["a", "b.txt"]), ("a/.скрытый", ["a", ".скрытый"]),
                                         ("v1..2/файл..txt", ["v1..2", "файл..txt"]), ("a//b", ["a", "b"])])
def test_обычные_имена_с_точками_и_пробелами_проходят(name, parts):
    assert U.safe_rel(name) == parts


def test_каталоги_вне_назначения_не_создаются(box, tmp_path):
    deep = tmp_path / "x" / "y"
    deep.mkdir(parents=True)
    dest = str(deep / "out")
    src = box.put("src.zip", K.zip_raw([("ok.txt", b"ok"), (".. /.. /ESCAPED/sub/f.txt", b"evil")]))
    with pytest.raises(U.UnpackError) as e:
        U.unpack(src, dest)
    assert e.value.reason == "traversal" and not os.path.exists(dest)
    assert sorted(os.listdir(tmp_path)) == ["src.zip", "x"] and os.listdir(tmp_path / "x") == ["y"] and os.listdir(deep) == []


def test_каталог_создаётся_только_после_проверки_что_он_внутри_назначения(box, tmp_path):
    """Вторая линия: даже если разбор имени пропустил «..», каталог снаружи не появляется."""
    os.makedirs(box.dest)
    with pytest.raises(U.UnpackError) as e:
        U._target(box.dest, ["..", "ESCAPED", "sub", "f.txt"])
    assert e.value.reason == "traversal" and not (tmp_path / "ESCAPED").exists() and os.listdir(box.dest) == []


@pytest.mark.parametrize("pack, name", [(K.zip_raw, "src.zip"), (lambda e: K.tar_bytes(dict(e)), "src.tar")])
def test_файл_и_каталог_с_одним_именем_архив_повреждён(box, pack, name):
    fails(box, box.put(name, pack([("ok.txt", b"ok"), ("a", b"1"), ("a/b", b"2")])), "broken")


def test_имя_в_ответе_то_под_которым_файл_лежит_на_диске(box):
    src = box.put("src.zip", K.zip_raw([("d/a.txt", b"first"), ("d/a.txt", b"second"), ("b.txt", b"x"), ("b.txt", b"y")]))
    names = U.unpack(src, box.dest)
    assert sorted(n for n, _ in names) == ["b (2).txt", "b.txt", "d/a (2).txt", "d/a.txt"]
    assert {n: open(p, "rb").read() for n, p in names} == box.tree()


def test_tar_имя_в_ответе_то_под_которым_файл_лежит_на_диске(box):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for data in (b"first", b"second"):
            info = tarfile.TarInfo("d/a.txt")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    names = U.unpack(box.put("src.tar", buf.getvalue()), box.dest)
    assert {n: open(p, "rb").read() for n, p in names} == box.tree() == {"d/a.txt": b"first", "d/a (2).txt": b"second"}


@pytest.mark.parametrize("name", [".. .gz", " .gz", "...gz", ".gz"])
def test_сжатый_файл_с_негодным_именем_распаковку_не_роняет(tmp_path, name):
    src = tmp_path / "in" / name
    src.parent.mkdir()
    src.write_bytes(gzip.compress(b"text"))
    dest = str(tmp_path / "out")
    try:
        names = U.unpack(str(src), dest)
    except U.UnpackError as e:
        assert e.reason == "traversal" and not os.path.exists(dest)
    else:
        assert [open(p, "rb").read() for _, p in names] == [b"text"] and [os.path.dirname(p) for _, p in names] == [dest]
    assert sorted(os.listdir(tmp_path)) in (["in"], ["in", "out"])


@pytest.mark.parametrize("error", [PermissionError("нет прав"), FileExistsError("занято"), NotADirectoryError("не каталог")])
def test_сбой_файловой_системы_при_распаковке_это_отказ_а_не_падение(box, monkeypatch, error):
    def boom(path, dest, meter):
        os.makedirs(os.path.join(dest, "partial"))
        raise error

    monkeypatch.setattr(U, "_unzip", boom)
    e = fails(box, box.put("src.zip", K.zip_bytes({"a.txt": b"one"})), "broken")
    assert type(error).__name__ in str(e) or str(error) in str(e)


# ── сообщения отказов (FR-73б): код по виду отказа, параметры, прежний русский текст ──
def refused(box, path, reason, limits=U.Limits()):
    """Отказ вида reason: у него сообщение из каталога, код начинается с unpack.<вид>, текст исключения — русский текст сообщения."""
    e = fails(box, path, reason, limits)
    m = e.message
    assert isinstance(m, M.Message) and str(e) == str(m) and type(str(e)) is str and M.of(e) is m
    assert m.code == f"unpack.{reason}" or m.code.startswith(f"unpack.{reason}_"), m.code
    assert m.code in M.CATALOG and json.loads(json.dumps(m.to_json(), ensure_ascii=False)) == m.to_json()
    return m


@pytest.mark.parametrize("name, code, shown", [
    ("../evil.txt", "unpack.traversal_dots", "../evil.txt"), ("a/../../evil.txt", "unpack.traversal_dots", "a/../../evil.txt"),
    ("..\\..\\evil.txt", "unpack.traversal_dots", "../../evil.txt"), (".. /evil.txt", "unpack.traversal_dots", ".. /evil.txt"),
    ("/etc/passwd", "unpack.traversal_absolute", "/etc/passwd"), ("C:\\Windows\\evil.txt", "unpack.traversal_absolute", "C:/Windows/evil.txt"),
    ("\\\\server\\share\\x", "unpack.traversal_absolute", "//server/share/x")])
def test_отказ_zip_с_выходом_за_каталог_несёт_код_и_имя(box, name, code, shown):
    m = refused(box, box.put("src.zip", K.zip_raw([("ok.txt", b"ok"), (name, b"evil")])), "traversal")
    assert (m.code, m.args) == (code, {"name": shown})
    assert m == ("абсолютный путь в архиве: " if "absolute" in code else "путь с «..» в архиве: ") + shown


def test_отказ_tar_с_выходом_за_каталог_несёт_код_и_имя(box):
    m = refused(box, box.put("src.tar", K.tar_bytes({"ok.txt": b"ok", "../evil.txt": b"evil"})), "traversal")
    assert (m.code, m.args, str(m)) == ("unpack.traversal_dots", {"name": "../evil.txt"}, "путь с «..» в архиве: ../evil.txt")


def test_длинное_имя_в_отказе_обрезано_до_120_знаков_и_в_тексте_и_в_параметре(box):
    long = "x" * 300
    m = refused(box, box.put("src.zip", K.zip_raw([("/" + long, b"evil")])), "traversal")
    assert m.args == {"name": ("/" + long)[:120]} and len(m.args["name"]) == 120 and m == "абсолютный путь в архиве: " + m.args["name"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo(long)
        info.external_attr = 0o120777 << 16
        z.writestr(info, b"/etc/passwd")
    m = refused(box, box.put("src2.zip", buf.getvalue()), "link")
    assert m.args == {"name": long[:120]} and len(m) == len("в архиве ссылка или устройство: ") + 120


def test_отказ_за_каталог_назначения_несёт_свой_код(box):
    os.makedirs(box.dest)
    with pytest.raises(U.UnpackError) as e:
        U._target(box.dest, ["..", "ESCAPED", "f.txt"])
    assert e.value.reason == "traversal" and (e.value.message.code, e.value.message.args) == ("unpack.traversal_escape", {})
    assert str(e.value) == "путь вышел за каталог назначения"


def test_отказ_за_ссылку_несёт_код_и_имя(box):
    m = refused(box, box.put("src.tar", K.tar_bytes({"ok.txt": b"ok"}, links={"link": "/etc/passwd"})), "link")
    assert (m.code, m.args, str(m)) == ("unpack.link_device", {"name": "link"}, "в архиве ссылка или устройство: link")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("ссылка")
        info.external_attr = 0o120777 << 16
        z.writestr(info, b"/etc/passwd")
    m = refused(box, box.put("src.zip", buf.getvalue()), "link")
    assert (m.code, m.args) == ("unpack.link_device", {"name": "ссылка"})


def test_отказ_за_пароль_несёт_код_encrypted(box):
    m = refused(box, box.put("src.zip", K.zip_encrypted_flag({"a.txt": b"secret"})), "encrypted")
    assert (m.code, m.args, str(m)) == ("unpack.encrypted", {}, "архив с паролем: проверить содержимое нельзя")


def test_отказ_за_пароль_от_zipfile_несёт_слова_библиотеки_параметром(box, monkeypatch):
    def boom(src, target, meter):
        raise RuntimeError("File is encrypted, password required")

    monkeypatch.setattr(U, "_copy", boom)
    m = refused(box, box.put("src.zip", K.zip_bytes({"a.txt": b"one"})), "encrypted")
    assert (m.code, m.args) == ("unpack.encrypted_error", {"error": "File is encrypted, password required"})
    assert m == "архив с паролем: File is encrypted, password required"


@has_7z
def test_7z_с_паролем_несёт_код_encrypted(box, tmp_path):
    (tmp_path / "src-a.txt").write_bytes(b"secret")
    subprocess.run(["7z", "a", "-bd", "-pS3cret", str(tmp_path / "src.7z"), str(tmp_path / "src-a.txt")], check=True, capture_output=True)
    assert refused(box, str(tmp_path / "src.7z"), "encrypted").code == "unpack.encrypted"


def test_повреждения_несут_каждое_свой_код(box):
    toc = refused(box, box.put("src-a.zip", b"PK\x03\x04" + b"\x00" * 60), "broken")
    assert (toc.code, toc.args, str(toc)) == ("unpack.broken_zip_toc", {}, "архив повреждён: оглавление zip не читается")
    tar = refused(box, box.put("src-b.tar", b"\x00" * 257 + b"ustar\x00" + b"junk" * 64), "broken")
    assert tar.code == "unpack.broken" and tar.args["error"] and str(tar) == "архив повреждён: " + tar.args["error"]
    gz = refused(box, box.put("src-c.gz", b"\x1f\x8b\x08\x00junkjunkjunk"), "broken")
    assert gz.code == "unpack.broken" and set(gz.args) == {"error"}


@pytest.mark.parametrize("pack, name", [(K.zip_raw, "src.zip"), (lambda e: K.tar_bytes(dict(e)), "src.tar")])
def test_файл_и_каталог_с_одним_именем_отказ_broken_с_текстом_ошибки(box, pack, name):
    m = refused(box, box.put(name, pack([("ok.txt", b"ok"), ("a", b"1"), ("a/b", b"2")])), "broken")
    assert m.code == "unpack.broken" and m == "архив повреждён: " + m.args["error"] and m.args["error"]


def test_не_архив_несёт_семейство_и_тип_кодами(box):
    m = refused(box, box.put("src.pdf", K.PDF), "unsupported")
    assert (m.code, m.args, str(m)) == ("unpack.unsupported_not_archive", {"family": "document", "detected": "pdf"}, "это не архив: document pdf")


def test_нет_программы_7z_это_отказ_unsupported(box, monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda name: None)
    m = refused(box, box.put("src.7z", K.SEVENZ), "unsupported")
    assert (m.code, m.args, str(m)) == ("unpack.unsupported_no_7z", {}, "нет программы 7z: архивы 7z и rar распаковать нечем")


def test_пределы_несут_вид_предела_и_его_значение(box):
    files = refused(box, box.put("src-a.zip", K.zip_bytes({f"f{i}.txt": b"x" for i in range(6)})), "bomb", SMALL)
    assert (files.code, files.args, str(files)) == ("unpack.bomb_files", {"limit": 5}, "файлов больше предела 5")
    size = refused(box, box.put("src-b.zip", K.zip_bytes({"a.bin": os.urandom(700 * 1024), "b.bin": os.urandom(700 * 1024)})), "bomb", SMALL)
    assert (size.code, size.args, str(size)) == ("unpack.bomb_bytes", {"mb": 1}, "распакованный объём больше предела 1 МБ")
    ratio = refused(box, box.put("src-c.zip", K.zip_bytes({"zeros.bin": b"\x00" * (900 * 1024)})), "bomb", SMALL)
    assert (ratio.code, ratio.args) == ("unpack.bomb_ratio", {"ratio": 100})
    assert ratio == "архив сжат сильнее, чем 100 к 1 — похоже на архивную бомбу"
    assert refused(box, box.put("src-d.gz", gzip.compress(b"\x00" * (3 * 1024 * 1024))), "bomb", SMALL).code.startswith("unpack.bomb_")
    assert refused(box, box.put("src-e.tar.gz", K.tar_bytes({"z.bin": b"\x00" * (2 * 1024 * 1024)}, "w:gz")), "bomb", SMALL).code.startswith("unpack.bomb_")


def test_непустой_каталог_назначения_несёт_путь(box):
    os.makedirs(box.dest)
    with open(os.path.join(box.dest, "чужой.txt"), "w") as f:
        f.write("x")
    with pytest.raises(U.UnpackError) as e:
        U.unpack(box.put("src.zip", K.zip_bytes({"a.txt": b"x"})), box.dest)
    assert e.value.reason == "dest" and (e.value.message.code, e.value.message.args) == ("unpack.dest_not_empty", {"path": box.dest})
    assert str(e.value) == f"каталог назначения не пуст: {box.dest}"


@pytest.mark.parametrize("error", [PermissionError("нет прав"), FileExistsError("занято")])
def test_сбой_файловой_системы_несёт_тип_и_слова_ошибки(box, monkeypatch, error):
    def boom(path, dest, meter):
        raise error

    monkeypatch.setattr(U, "_unzip", boom)
    m = refused(box, box.put("src.zip", K.zip_bytes({"a.txt": b"one"})), "broken")
    assert (m.code, m.args) == ("unpack.broken_os", {"error_type": type(error).__name__, "error": str(error)})
    assert m == f"архив не распаковался: {type(error).__name__}: {error}"


# 7z без настоящей программы: ответы 7z подставные
def seven(monkeypatch, *answers):
    calls = list(answers)
    monkeypatch.setattr(U.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(U, "_seven", lambda args, timeout, limit_bytes=None: calls.pop(0))
    return calls


def reply(code=0, out="", err=""):
    import types
    return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)


ENTRY = "Path = {path}\nSize = 3\nFolder = -\n{extra}\n"


def listing(*entries):
    return "Listing archive: x.7z\n\n----------\n" + "".join(ENTRY.format(path=p, extra=x) for p, x in entries)


def test_7z_не_читается_несёт_слова_программы_параметром(box, monkeypatch):
    seven(monkeypatch, reply(2, "", "ERROR: Unsupported Method\n"))
    m = refused(box, box.put("src.7z", K.SEVENZ), "broken")
    assert (m.code, m.args) == ("unpack.broken_format", {"detail": "ERROR: Unsupported Method"})
    assert m == "архив повреждён или формат не поддержан: ERROR: Unsupported Method"


def test_7z_распаковка_не_удалась_несёт_слова_программы_параметром(box, monkeypatch):
    seven(monkeypatch, reply(0, listing(("a.txt", ""))), reply(2, "", "ERROR: Data Error\n"))
    m = refused(box, box.put("src.7z", K.SEVENZ), "broken")
    assert (m.code, m.args, str(m)) == ("unpack.broken_extract", {"detail": "ERROR: Data Error"}, "распаковка не удалась: ERROR: Data Error")


def test_7z_не_уложилась_во_время_несёт_свой_код(box, monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda name: "/usr/bin/7z")

    def slow(*a, **kw):
        raise subprocess.TimeoutExpired("7z", 1)

    monkeypatch.setattr(U.subprocess, "run", slow)
    m = refused(box, box.put("src.7z", K.SEVENZ), "broken")
    assert (m.code, m.args, str(m)) == ("unpack.broken_timeout", {}, "распаковка не уложилась в отведённое время")


def test_7z_со_ссылкой_несёт_код_link_и_имя(box, monkeypatch):
    seven(monkeypatch, reply(0, listing(("evil/ссылка", "Symbolic Link = /etc/passwd\n"))))
    m = refused(box, box.put("src.7z", K.SEVENZ), "link")
    assert (m.code, m.args, str(m)) == ("unpack.link", {"name": "evil/ссылка"}, "в архиве ссылка: evil/ссылка")


def test_7z_с_паролем_в_оглавлении_несёт_код_encrypted(box, monkeypatch):
    seven(monkeypatch, reply(0, listing(("a.txt", "Encrypted = +\n"))))
    assert refused(box, box.put("src.7z", K.SEVENZ), "encrypted").code == "unpack.encrypted"
    seven(monkeypatch, reply(2, "", "ERROR: Wrong password : a.txt\n"))
    assert refused(box, box.put("src2.7z", K.SEVENZ), "encrypted").code == "unpack.encrypted"


def test_7z_с_выходом_за_каталог_и_с_бомбой_несёт_свои_коды(box, monkeypatch):
    seven(monkeypatch, reply(0, listing(("../evil.txt", ""))))
    assert refused(box, box.put("src-a.7z", K.SEVENZ), "traversal").code == "unpack.traversal_dots"
    seven(monkeypatch, reply(0, listing(*[(f"f{i}.txt", "") for i in range(6)])))
    assert refused(box, box.put("src-b.7z", K.SEVENZ), "bomb", SMALL).code == "unpack.bomb_files"


# ни одного отказа без сообщения из каталога: голая строка в UnpackError — отказ без кода
def _unpack_errors(source):
    import ast
    return [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == "UnpackError") or (isinstance(n.func, ast.Attribute) and n.func.attr == "UnpackError"))]


def test_сверка_видит_отказ_без_сообщения():
    assert len(_unpack_errors('raise UnpackError("bomb", "текст")\nraise unpack.UnpackError("depth", f"{x}")\nraise ValueError("не наш")')) == 2


@pytest.mark.parametrize("name", ["unpack.py", "intake.py"])
def test_каждый_отказ_распаковки_собирается_из_каталога_с_кодом_своего_вида(name):
    import ast
    with open(os.path.join(os.path.dirname(os.path.abspath(U.__file__)), name), encoding="utf-8") as f:
        calls = _unpack_errors(f.read())
    bad = []
    for call in calls:
        reason, text = (call.args + [None, None])[:2]
        make = text if isinstance(text, ast.Call) and isinstance(text.func, ast.Attribute) and text.func.attr == "make" else None
        code = make.args[0].value if make is not None and make.args and isinstance(make.args[0], ast.Constant) else None
        ok = isinstance(reason, ast.Constant) and isinstance(code, str) and (
            code == f"unpack.{reason.value}" or code.startswith(f"unpack.{reason.value}_"))
        if not ok:
            bad.append(f"{name}:{call.lineno}")
    assert not bad, "отказ без кода своего вида: " + ", ".join(bad)
    assert len(calls) >= (30 if name == "unpack.py" else 2)

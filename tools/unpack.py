"""Безопасная распаковка входящих архивов.

    unpack(путь, каталог, limits, budget) -> [(имя внутри архива, путь распакованного файла)]

Правила:
- распаковка только в пустой каталог назначения, файлы 600, каталоги 700;
- имя с «..», абсолютный путь, ссылка или устройство — архив отвергается целиком;
- пароль, повреждение, превышение пределов — архив отвергается целиком;
- при отказе в каталоге назначения ничего не остаётся.
Пределы общие на всё дерево вложенных архивов: счёт ведёт Budget.
"""
import bz2
import gzip
import hashlib
import lzma
import os
import re
import shutil
import stat
import subprocess
import time
import tarfile
import zipfile
import zlib
from collections import namedtuple

import filetype_sniff
import messages

Limits =namedtuple("Limits", "max_bytes max_files max_ratio max_depth ratio_floor",
                    defaults=(2 * 1024 ** 3, 5000, 100, 3, 100 * 1024 ** 2))
CHUNK = 1024 * 1024
NO_PASSWORD = "-pflyarchive-no-password"      # 7z без ключа -p спрашивает пароль с клавиатуры и висит
DRIVE = re.compile(r"^[A-Za-z]:")
BOX_DRAWING = set(range(0x2500, 0x25A0))


class UnpackError(messages.CodedError):
    """reason: traversal | link | encrypted | broken | bomb | unsupported | dest | depth.
    text — сообщение из каталога (messages.make); код сообщения начинается с «unpack.<reason>»."""

    def __init__(self, reason, text):
        super().__init__(text)
        self.reason = reason


class Budget:
    """Сколько уже распаковано во всём дереве вложенных архивов."""

    def __init__(self):
        self.bytes = 0
        self.files = 0


class _Meter:
    def __init__(self, limits, budget, archive_size):
        self.limits, self.budget, self.archive_size, self.local = limits, budget, max(archive_size, 1), 0

    def add_file(self, n=1):
        self.budget.files += n
        if self.budget.files > self.limits.max_files:
            raise UnpackError("bomb", messages.make("unpack.bomb_files", limit=self.limits.max_files))

    def add_bytes(self, n):
        self.budget.bytes += n
        self.local += n
        self.check()

    def check(self, planned=0):
        if self.budget.bytes + planned > self.limits.max_bytes:
            raise UnpackError("bomb", messages.make("unpack.bomb_bytes", mb=self.limits.max_bytes // 1024 ** 2))
        size = self.local + planned
        if size > self.limits.ratio_floor and size > self.archive_size * self.limits.max_ratio:
            raise UnpackError("bomb", messages.make("unpack.bomb_ratio", ratio=self.limits.max_ratio))


# ── имена ───────────────────────────────────────────────────────
def safe_rel(name):
    """Путь внутри архива -> список частей. Выход за каталог назначения — отказ."""
    name = name.replace("\\", "/")
    if name.startswith("/") or DRIVE.match(name):
        raise UnpackError("traversal", messages.make("unpack.traversal_absolute", name=name[:120]))
    parts = []
    for part in name.split("/"):
        # часть сначала очищается, потом проверяется: «.. » и «..» с управляющим знаком после очистки — это «..»
        part = "".join(ch for ch in part if ord(ch) >= 32).strip()
        if part.count(".") >= 2 and not part.replace(".", "").strip():
            raise UnpackError("traversal", messages.make("unpack.traversal_dots", name=name[:120]))
        if part and part != ".":
            parts.append(_fit(part))
    return parts or None


def _fit(part):
    """Имя длиннее, чем допускает файловая система, укорачивается с сохранением расширения."""
    if len(part.encode("utf-8")) <= 240:
        return part
    stem, ext = os.path.splitext(part)
    mark = hashlib.sha256(part.encode("utf-8")).hexdigest()[:8]
    while len((stem + mark + ext).encode("utf-8")) > 230 and stem:
        stem = stem[:-1]
    return f"{stem}_{mark}{ext[:20]}"


def _zip_name(info):
    """Имя из zip. Без признака UTF-8 архиваторы Windows пишут имена в кодировке DOS или ANSI."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    if raw.isascii():
        return info.filename
    best, best_score = info.filename, None
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        score = sum(1 if ch.isalnum() else -3 if ord(ch) in BOX_DRAWING else 0 for ch in text)
        if best_score is None or score > best_score:
            best, best_score = text, score
    return best


def _target(dest, parts):
    """Создаёт закрытые каталоги и возвращает свободный путь для файла. Место проверяется до создания каталогов."""
    root = os.path.realpath(dest)
    if os.path.commonpath([root, os.path.realpath(os.path.join(dest, *parts[:-1]))]) != root:
        raise UnpackError("traversal", messages.make("unpack.traversal_escape"))
    folder = dest
    for part in parts[:-1]:
        folder = os.path.join(folder, part)
        if not os.path.isdir(folder):
            os.mkdir(folder, 0o700)
    stem, ext = os.path.splitext(parts[-1])
    target, n = os.path.join(folder, parts[-1]), 1
    while os.path.exists(target):
        n += 1
        target = os.path.join(folder, f"{stem} ({n}){ext}")
    return target


def _rel(dest, target):
    """Имя файла в ответе — то, под которым он лёг на диск: второй файл с тем же именем получает « (2)»."""
    return os.path.relpath(target, dest).replace(os.sep, "/")


def _copy(src, target, meter):
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            meter.add_bytes(len(chunk))
            out.write(chunk)


def _stamp(target, seconds):
    """Время изменения файла — как в архиве: по нему потом берётся дата документа. Негодное время пропускается."""
    try:
        if 0 < seconds < 4_102_444_800:           # до 2100 года
            os.utime(target, (seconds, seconds))
    except (OSError, OverflowError, ValueError, TypeError):
        pass


# ── zip ─────────────────────────────────────────────────────────
def _unzip(path, dest, meter):
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise UnpackError("broken", messages.make("unpack.broken", error=str(e)))
    with z:
        plan = []
        for info in z.infolist():             # всё оглавление проверяется до записи первого байта
            if info.flag_bits & 0x1:
                raise UnpackError("encrypted", messages.make("unpack.encrypted"))
            if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise UnpackError("link", messages.make("unpack.link_device", name=info.filename[:120]))
            name = _zip_name(info)
            if info.is_dir() or name.endswith(("/", "\\")):
                continue
            parts = safe_rel(name)
            if parts:
                plan.append((info, parts))
        if meter.budget.files + len(plan) > meter.limits.max_files:
            raise UnpackError("bomb", messages.make("unpack.bomb_files", limit=meter.limits.max_files))
        meter.check(planned=sum(info.file_size for info, _ in plan))
        out = []
        for info, parts in plan:
            meter.add_file()
            try:
                target = _target(dest, parts)
                with z.open(info) as src:
                    _copy(src, target, meter)
                try:
                    _stamp(target, time.mktime(info.date_time + (0, 0, -1)))
                except (OverflowError, ValueError):
                    pass
            except RuntimeError as e:         # так zipfile сообщает о пароле
                raise UnpackError("encrypted", messages.make("unpack.encrypted_error", error=str(e)))
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, OSError) as e:
                raise UnpackError("broken", messages.make("unpack.broken", error=str(e)))
            out.append((_rel(dest, target), target))
        return out


# ── tar и одиночные сжатые файлы ────────────────────────────────
def _untar(path, dest, meter):
    try:
        t = tarfile.open(path, "r:*")
    except (tarfile.TarError, OSError, EOFError, zlib.error, lzma.LZMAError) as e:
        raise UnpackError("broken", messages.make("unpack.broken", error=str(e)))
    out = []
    with t:
        try:
            for member in t:
                if member.isdir():
                    continue
                if not member.isreg():
                    raise UnpackError("link", messages.make("unpack.link_device", name=member.name[:120]))
                parts = safe_rel(member.name)
                if not parts:
                    continue
                meter.add_file()
                target = _target(dest, parts)
                _copy(t.extractfile(member), target, meter)
                _stamp(target, member.mtime)
                out.append((_rel(dest, target), target))
        except (tarfile.TarError, EOFError, zlib.error, lzma.LZMAError, OSError) as e:
            raise UnpackError("broken", messages.make("unpack.broken", error=str(e)))
    return out


def _unstream(path, dest, meter, type_):
    """gz, bz2, xz: либо сжатый tar, либо один сжатый файл."""
    try:
        with tarfile.open(path, "r:*") as t:
            is_tar = t.next() is not None      # поток нулей читается как пустой tar — это не он
    except (tarfile.TarError, OSError, EOFError, zlib.error, lzma.LZMAError):
        is_tar = False
    if is_tar:
        return _untar(path, dest, meter)
    opener = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}[type_]
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    parts = safe_rel(stem if ext.lower() in (".gz", ".bz2", ".xz") and stem else name + ".out") or ["файл.out"]
    meter.add_file()
    try:
        target = _target(dest, parts)
        with opener(path, "rb") as src:
            _copy(src, target, meter)
    except (OSError, EOFError, zlib.error, lzma.LZMAError) as e:
        raise UnpackError("broken", messages.make("unpack.broken", error=str(e)))
    return [(_rel(dest, target), target)]


# ── 7z и rar через внешнюю программу ────────────────────────────
def _seven(args, timeout, limit_bytes=None):
    exe = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
    if not exe:
        raise UnpackError("unsupported", messages.make("unpack.unsupported_no_7z"))

    def cap():
        if limit_bytes is not None:
            import resource
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit_bytes, limit_bytes))

    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, errors="replace", timeout=timeout,
                              stdin=subprocess.DEVNULL, preexec_fn=cap if os.name == "posix" else None)
    except subprocess.TimeoutExpired:
        raise UnpackError("broken", messages.make("unpack.broken_timeout"))


def _un7z(path, dest, meter):
    r = _seven(["l", "-slt", NO_PASSWORD, "--", path], timeout=300)
    text = r.stdout + r.stderr
    if r.returncode != 0:
        if "password" in text.lower() or "encrypted" in text.lower():
            raise UnpackError("encrypted", messages.make("unpack.encrypted"))
        raise UnpackError("broken", messages.make("unpack.broken_format", detail=text.strip().splitlines()[-1][:160]))
    entries, cur = [], {}
    for line in text.split("----------", 1)[-1].splitlines() + [""]:
        if " = " in line:
            key, value = line.split(" = ", 1)
            cur[key.strip()] = value.strip()
        elif not line.strip() and cur:
            entries.append(cur)
            cur = {}
    files, declared = 0, 0
    for e in entries:
        if e.get("Encrypted") == "+":
            raise UnpackError("encrypted", messages.make("unpack.encrypted"))
        attrs = e.get("Attributes", "")
        unix = attrs.split()[-1] if attrs.split() else ""
        if e.get("Symbolic Link") or e.get("Hard Link") or (len(unix) == 10 and unix[0] == "l"):
            raise UnpackError("link", messages.make("unpack.link", name=e.get("Path", "")[:120]))
        if e.get("Folder") == "+" or attrs.startswith("D"):
            continue
        safe_rel(e.get("Path", ""))
        files += 1
        declared += int(e.get("Size") or 0)
    if meter.budget.files + files > meter.limits.max_files:
        raise UnpackError("bomb", messages.make("unpack.bomb_files", limit=meter.limits.max_files))
    meter.check(planned=declared)
    left = meter.limits.max_bytes - meter.budget.bytes
    r = _seven(["x", "-y", "-bd", NO_PASSWORD, "-o" + dest, "--", path], timeout=6 * 3600, limit_bytes=left + 1)
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).strip().splitlines()[-1][:160] if (r.stdout + r.stderr).strip() else ""
        raise UnpackError("broken", messages.make("unpack.broken_extract", detail=tail))
    out = []
    for root, dirs, names in os.walk(dest):
        for d in dirs:
            p = os.path.join(root, d)
            if os.path.islink(p):
                raise UnpackError("link", messages.make("unpack.link", name=d[:120]))
            os.chmod(p, 0o700)
        for n in names:
            p = os.path.join(root, n)
            if os.path.islink(p) or not os.path.isfile(p):
                raise UnpackError("link", messages.make("unpack.link_device", name=n[:120]))
            os.chmod(p, 0o600)
            meter.add_file()
            meter.add_bytes(os.path.getsize(p))
            out.append((os.path.relpath(p, dest).replace(os.sep, "/"), p))
    return sorted(out)


# ── вход ────────────────────────────────────────────────────────
def unpack(path, dest, limits=Limits(), budget=None):
    budget = budget if budget is not None else Budget()
    if os.path.exists(dest) and os.listdir(dest):
        raise UnpackError("dest", messages.make("unpack.dest_not_empty", path=dest))
    kind = filetype_sniff.detect(path)
    if kind.type == "broken-zip":
        raise UnpackError("broken", messages.make("unpack.broken_zip_toc"))
    if kind.family != "archive":
        raise UnpackError("unsupported", messages.make("unpack.unsupported_not_archive", family=kind.family, detected=kind.type))
    os.makedirs(dest, mode=0o700, exist_ok=True)
    os.chmod(dest, 0o700)
    meter = _Meter(limits, budget, os.path.getsize(path))
    try:
        if kind.type == "zip":
            return _unzip(path, dest, meter)
        if kind.type == "tar":
            return _untar(path, dest, meter)
        if kind.type in ("gz", "bz2", "xz"):
            return _unstream(path, dest, meter, kind.type)
        return _un7z(path, dest, meter)
    except UnpackError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except OSError as e:                       # занятое имя, нет прав, нет места: архив не принят, разбор не падает
        shutil.rmtree(dest, ignore_errors=True)
        raise UnpackError("broken", messages.make("unpack.broken_os", error_type=type(e).__name__, error=str(e)))

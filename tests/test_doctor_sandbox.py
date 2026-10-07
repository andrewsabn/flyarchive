"""`flyarchive doctor` проверяет, что песочница работает, и называет интерпретатор просмотра (FR-107).

Раньше проверка видела только программу: на обычной Ubuntu 24.04 (не WSL2) и в контейнерах непривилегированные пространства имён могут быть запрещены,
bwrap в PATH есть, а песочницу создать не может. Теперь, если bwrap найден, идёт один короткий пробный запуск (`bwrap --unshare-all --ro-bind / / true`
со сроком в несколько секунд, вывод не показывается целиком). Не вышло — строка «по желанию» с отдельным сообщением: программа есть, песочницу создать
не может, что без неё не работает, слова для поиска причины. В `--json` у неё тот же `id`, что у программы (`tool.bwrap`), и отдельный код сообщения.
Рядом — строка `preview_python`: каким интерпретатором просмотр запускает рабочий процесс и можно ли отдать его песочнице. Подставной bwrap лежит в PATH
машины теста; настоящая песочница, служба векторов и живой архив не трогаются.
"""
import os
import re
import subprocess
import sys

import pytest

import doctor as D
import messages as M
import preview as P
from test_doctor import archive, by_id, report  # noqa: F401
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

DENIED = "bwrap: No permissions to create new namespace"


def with_bwrap(archive, body):
    """Каталог PATH, где есть подставной bwrap с телом body и заглушка systemctl архива: путь для переменной PATH."""
    folder = archive.base / "подставной-bwrap"
    folder.mkdir(exist_ok=True)
    stub = folder / "bwrap"
    stub.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    stub.chmod(0o755)
    return f"{folder}:{archive.bin}"


# ── пробный запуск настоящей командой ───────────────────────────
def test_bwrap_найден_и_песочница_создаётся_программа_ok_и_запуск_ровно_один_названной_формы(archive):
    path = with_bwrap(archive, 'printf "%s\\n" "$@" >> "$HOME/bwrap.args"')
    code, answer, _ = report(archive, PATH=path)
    check = by_id(answer)["tool.bwrap"]
    assert code == 0 and (check["status"], check["message"]["code"], check["message"]["args"]) == ("ok", "doctor.tool_ok", {"name": "bwrap"})
    with open(os.path.join(archive.user, "bwrap.args"), encoding="utf-8") as f:
        assert f.read().split() == ["--unshare-all", "--ro-bind", "/", "/", "true"], "ровно один запуск"


def test_bwrap_найден_но_песочницу_создать_не_может_строка_по_желанию_с_тем_же_id_и_отдельным_кодом(archive):
    path = with_bwrap(archive, f"echo '{DENIED}' >&2\nexit 1")
    code, answer, _ = report(archive, PATH=path)
    check = by_id(answer)["tool.bwrap"]
    assert code == 0 and answer["ok"] is True, "программа необязательная: код возврата прежний"
    assert (check["status"], check["message"]["code"]) == ("optional_missing", "doctor.sandbox_blocked")
    assert check["message"]["args"] == {"name": "bwrap", "why": "exit 1: " + DENIED, "use": "bwrap"}
    text = check["message"]["text"]
    assert "найдена" in text and "песочницу создать не может" in text and DENIED in text
    assert "kernel.apparmor_restrict_unprivileged_userns" in text and "AppArmor" in text
    assert "не открывается просмотр" in text, "названо, что без песочницы не работает"
    assert [c["id"] for c in answer["checks"]].count("tool.bwrap") == 1


def test_в_обычном_выводе_та_же_строка_с_пометкой_по_желанию_а_итог_не_красный(archive):
    path = with_bwrap(archive, f"echo '{DENIED}' >&2\nexit 1")
    code, out, _ = archive("doctor", PATH=path)
    line = next(line for line in out.splitlines() if "песочницу создать не может" in line)
    assert code == 0 and line.strip().startswith("по желанию") and out.splitlines()[-1] == str(M.make("doctor.summary_ok"))


def test_вывод_пробного_запуска_целиком_не_показывается_одна_строка_без_управляющих_знаков(archive):
    path = with_bwrap(archive, "for i in {1..40}; do echo \"строка $i с русским текстом и \\x1b[31mцветом\" >&2; done\necho громкий вывод\nexit 2")
    _, answer, _ = report(archive, PATH=path)
    check = by_id(answer)["tool.bwrap"]
    why = check["message"]["args"]["why"]
    assert why.startswith("exit 2: ") and why.isascii() and why.isprintable() and len(why) <= len("exit 2: ") + D.WHY_MAX
    assert "громкий" not in check["message"]["text"] and "40" not in why.replace("exit 2", "")


def test_bwrap_нет_в_PATH_строка_прежняя_нет_программы(archive):
    _, answer, _ = report(archive, PATH=str(archive.bin))
    check = by_id(answer)["tool.bwrap"]
    assert (check["status"], check["message"]["code"]) == ("optional_missing", "doctor.tool_missing")


def test_сообщение_о_запрете_без_советов_отключать_защиту_системы_только_причина_и_слова_для_поиска():
    template = M.CATALOG["doctor.sandbox_blocked"][0]
    assert not re.search(r"(?i)выключ|отключ|sysctl|disable|=\s*0\b|apparmor_parser|\bsudo\b", template), template
    assert "kernel.apparmor_restrict_unprivileged_userns" in template and "AppArmor" in template and "контейнер" in template


def test_слова_что_без_песочницы_не_работает_у_нового_сообщения_те_же_что_у_отсутствующей_программы():
    assert M.WORDS["doctor.sandbox_blocked"]["use"]["bwrap"] == M.WORDS["doctor.tool_missing"]["use"]["bwrap"]


# ── сама проверка: подставной запуск ────────────────────────────
class Done:
    def __init__(self, code=0, stderr=b""):
        self.returncode, self.stderr = code, stderr


def probe(**kw):
    seen = []

    def run(argv, **options):
        seen.append((argv, options))
        if "raises" in kw:
            raise kw["raises"]
        return Done(kw.get("code", 0), kw.get("stderr", b""))

    return D.probe_sandbox("/usr/bin/bwrap", run=run), seen


def test_пробный_запуск_называет_команду_срок_короткий_и_ввода_нет():
    why, seen = probe()
    ((argv, options),) = seen
    assert why is None and argv == ["/usr/bin/bwrap", "--unshare-all", "--ro-bind", "/", "/", "true"]
    assert options["timeout"] == D.SANDBOX_TIMEOUT_S and 0 < D.SANDBOX_TIMEOUT_S <= 15
    assert options["stdin"] == subprocess.DEVNULL and options["capture_output"] is True


@pytest.mark.parametrize("kw, want", [
    ({"code": 1, "stderr": DENIED.encode()}, "exit 1: " + DENIED),
    ({"code": 3}, "exit 3"),
    ({"code": 1, "stderr": "ошибка\x00с\x1bнулём\n".encode("utf-8")}, "exit 1: " + "?" * 14),
    ({"code": 1, "stderr": "\n\n   \nпервая\nвторая".encode("utf-8")}, "exit 1: " + "?" * 6),
    ({"raises": subprocess.TimeoutExpired("bwrap", 10)}, f"timeout {D.SANDBOX_TIMEOUT_S} s"),
    ({"raises": PermissionError(13, "нет прав")}, "PermissionError"),
    ({"raises": FileNotFoundError(2, "нет файла")}, "FileNotFoundError"),
], ids=["вывод", "без вывода", "управляющие знаки", "первая непустая строка", "срок вышел", "нет прав", "нет файла"])
def test_пробный_запуск_называет_причину_одной_строкой_ascii(kw, want):
    why, _ = probe(**kw)
    assert why == want


def test_слишком_длинная_строка_причины_обрезается():
    why, _ = probe(code=1, stderr=("x" * 1000).encode())
    assert why == "exit 1: " + "x" * D.WHY_MAX


def test_check_program_пробует_только_найденную_программу_и_только_с_пробой():
    asked = []

    def which(name):
        return {"bwrap": "/usr/bin/bwrap", "dot": "/usr/bin/dot"}.get(name)

    def blocked(path):
        asked.append(path)
        return "exit 1: " + DENIED

    check = D.check_program("bwrap", ("bwrap",), "bwrap", which, blocked)
    assert (check.id, check.status, check.message.code) == ("tool.bwrap", "optional_missing", "doctor.sandbox_blocked") and asked == ["/usr/bin/bwrap"]
    ok = D.check_program("bwrap", ("bwrap",), "bwrap", which, lambda path: None)
    assert (ok.status, ok.message.code) == ("ok", "doctor.tool_ok")
    missing = D.check_program("bwrap", ("bwrap",), "bwrap", lambda name: None, blocked)
    assert (missing.status, missing.message.code) == ("optional_missing", "doctor.tool_missing") and asked == ["/usr/bin/bwrap"], "нет программы — пробы нет"
    other = D.check_program("dot", ("dot",), "dot", which)
    assert (other.status, other.message.code) == ("ok", "doctor.tool_ok")


# ── интерпретатор просмотра ─────────────────────────────────────
def test_строка_интерпретатора_просмотра_стоит_после_программ_и_называет_интерпретатор_родителя(archive):
    _, answer, _ = report(archive)
    ids = list(by_id(answer))
    assert ids[ids.index("tool.tailscale") + 1] == "preview_python"
    check = by_id(answer)["preview_python"]
    assert (check["status"], check["message"]["code"]) == ("ok", "doctor.preview_python_ok")
    assert check["message"]["args"] == {"python": os.path.realpath(sys.executable)}


def test_интерпретатор_вне_системных_каталогов_который_отдать_нельзя_строка_по_желанию_с_сообщением_просмотра(tmp_path, monkeypatch):
    refusal = M.make("preview.python_outside", python="/opt/свой/bin/python3")
    monkeypatch.setattr(P, "worker_python", lambda home=None, **k: P.WorkerPython("/opt/свой/bin/python3", None, refusal))
    check = D.check_preview_python(str(tmp_path / "архив"))
    assert (check.id, check.status, check.message.code) == ("preview_python", "optional_missing", "preview.python_outside")
    assert check.message.args == {"python": "/opt/свой/bin/python3"}


def test_интерпретатор_который_отдаётся_песочнице_каталогом_установки_строка_ok(tmp_path, monkeypatch):
    python = "/home/u/.pyenv/versions/3.12.1/bin/python3.12"
    monkeypatch.setattr(P, "worker_python", lambda home=None, **k: P.WorkerPython(python, "/home/u/.pyenv/versions/3.12.1", None))
    check = D.check_preview_python(str(tmp_path / "архив"))
    assert (check.status, check.message.code, check.message.args) == ("ok", "doctor.preview_python_ok", {"python": python})

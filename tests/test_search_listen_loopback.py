"""Страница поиска слушает только петлю и при ключах командной строки (FR-97).

Раньше точка входа `webui.py` понимала ключ `--wsl=АДРЕС` и открывала второй адрес прослушивания. Ключ нигде не использовался, а служба,
выставленная на чужой адрес, обходит шлюз. Тест запускает точку входа в дочернем процессе с перехваченным занятием порта и смотрит, какие адреса
служба собиралась занять.
"""
import json
import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI = os.path.join(ROOT, "tools", "webui.py")

CHILD = textwrap.dedent('''
    import json, os, runpy, socketserver, sys
    bound = []

    def refuse(self, *args, **kwargs):
        bound.append(self.server_address[0])
        raise OSError("занятие порта перехвачено тестом")

    socketserver.TCPServer.server_bind = refuse
    script = sys.argv[1]
    sys.argv = [script] + sys.argv[2:]
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit:
        pass
    finally:
        with open(os.environ["LISTEN_OUT"], "w", encoding="utf-8") as f:
            json.dump(bound, f)
''')


def addresses(tmp_path, *args):
    """Адреса, которые точка входа страницы поиска собиралась занять при таких ключах."""
    home, arch, out = tmp_path / "дом", tmp_path / "архив", tmp_path / "bound.json"
    home.mkdir(exist_ok=True)
    arch.mkdir(exist_ok=True)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "USERPROFILE": str(home), "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": os.pathsep.join(p for p in sys.path if p), "FLYARCHIVE_HOME": str(arch), "LISTEN_OUT": str(out)}
    r = subprocess.run([sys.executable, "-c", CHILD, WEBUI, *args], env=env, capture_output=True, text=True, timeout=120)
    assert out.exists(), "точка входа не дошла до занятия порта: " + r.stderr[-400:]
    return json.loads(out.read_text(encoding="utf-8"))


def test_страница_поиска_без_ключей_занимает_только_петлю(tmp_path):
    assert addresses(tmp_path) == ["127.0.0.1"]


def test_ключ_второго_адреса_прослушивания_не_открывает_ничего_кроме_петли(tmp_path):
    for extra in ("--wsl=127.0.0.2", "--wsl=0.0.0.0", "--wsl=192.0.2.7"):
        assert addresses(tmp_path, extra) == ["127.0.0.1"], extra


def test_в_исходнике_страницы_поиска_нет_ключа_второго_адреса():
    source = open(WEBUI, encoding="utf-8").read()
    assert "--wsl" not in source and "sys.argv[1:]" not in source.split('if __name__ == "__main__":')[1]

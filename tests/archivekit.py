"""Обвязка тестов «архив с пустого каталога» (FR-103): команда flyarchive как дочерний процесс.

Общая для test_init.py и test_clean_install.py. Каталог архива — свой временный, домашний каталог — свой, служб и модели нет:
вместо systemctl — заглушка в PATH, вместо службы векторов — сервер теста (адрес — настройка embed_url). Настоящие порты, живой архив и
настоящая модель не трогаются.
"""
import json
import os
import site
import subprocess
import sys

import settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLYARCHIVE = os.path.join(ROOT, "tools", "flyarchive")
# переменные проекта из окружения набора в дочерний процесс не идут
OWN = settings.ENV_PREFIX
# адрес службы векторов, которой нет (порт discard закрыт): команды, которые спрашивают службу (doctor, итог init и install), не дойдут до живой
DEAD_VECTORS = "http://127.0.0.1:9/api/embed"
INDEX_FIELDS = ["path", "source", "space", "title", "updated", "url", "chunk", "text", "vector"]


class Archive:
    """Пустой каталог архива: home ещё нет, user (домашний каталог) уже есть. Вызов — команда flyarchive, ответ (код, stdout, stderr)."""

    def __init__(self, base, env=None):
        self.base = base
        self.user = str(base / "user")
        self.home = os.path.join(self.user, "flyarchive")
        self.box = str(base / "входящие")
        bin_dir = base / "bin"
        os.makedirs(self.user)
        bin_dir.mkdir()
        stub = bin_dir / "systemctl"                      # таймер входящих ставится через systemctl: настоящий не нужен
        stub.write_text('#!/bin/bash\necho "$@" >> "$HOME/systemctl.log"\n', encoding="utf-8")
        stub.chmod(0o755)
        self.bin = str(bin_dir)
        self.extra = dict(env or {})

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(OWN)}
        env.update(HOME=self.user, FLYARCHIVE_HOME=self.home, PYTHONIOENCODING="utf-8", PATH=f"{self.bin}:{os.environ['PATH']}",
                   # домашний каталог в тесте подменён, а библиотеки стоят в домашнем каталоге настоящего пользователя
                   PYTHONPATH=os.pathsep.join(p for p in (site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p),
                   # модель недостижима: если команда пойдёт к ней, документ останется непроверенным, а не пройдёт
                   FLYARCHIVE_LLM_LOCAL_URL="http://127.0.0.1:9", FLYARCHIVE_LLM_CLOUD_URL="http://127.0.0.1:9",
                   FLYARCHIVE_EMBED_URL=DEAD_VECTORS)
        env.update(self.extra)
        env.update(extra)
        return env

    def __call__(self, *args, **extra):
        r = subprocess.run([sys.executable, FLYARCHIVE, *args], env=self.env(**extra), capture_output=True, text=True,
                           encoding="utf-8", timeout=180)
        return r.returncode, r.stdout, r.stderr

    def tool(self, name, *args, **extra):
        """Сценарий каталога tools (search.py и другие) как дочерний процесс с тем же окружением: (код, stdout, stderr)."""
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", name), *args], env=self.env(**extra), capture_output=True, text=True,
                           encoding="utf-8", timeout=180, cwd=str(self.base))
        return r.returncode, r.stdout, r.stderr

    def path(self, *parts):
        return os.path.join(self.home, *parts)

    def hide(self, *modules, system=None):
        """Переменные окружения для процесса команды: перечисленные библиотеки не импортируются (как будто не стоят), а система названа
        `system` (platform.system()). Ничего не удаляется и не подменяется в самом тесте: подмена живёт в sitecustomize дочернего процесса."""
        folder = os.path.join(str(self.base), "скрыто")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "sitecustomize.py"), "w", encoding="utf-8") as f:
            f.write("import sys\nfor name in %r:\n    sys.modules[name] = None\n" % (list(modules),))
            if system:
                f.write("import platform\nplatform.system = lambda: %r\n" % system)
        return {"PYTHONPATH": os.pathsep.join(p for p in (folder, site.getusersitepackages(), os.environ.get("PYTHONPATH")) if p)}

    def config(self):
        with open(self.path("inbox.json"), encoding="utf-8") as f:
            return json.load(f)

    def instant(self):
        """Файл из входящей папки берётся сразу, без выстойки тридцать секунд (настройка stable_seconds в inbox.json)."""
        cfg = self.config()
        cfg["stable_seconds"] = 0
        with open(self.path("inbox.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def put(self, name, data):
        with open(os.path.join(self.box, name), "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))

    def table(self):
        """Таблица индекса, открытая заново."""
        import lancedb
        return lancedb.connect(self.path("index", "lance")).open_table("docs")


def error_line(err):
    """Последняя строка stderr при --json: {"error": {"code", "args", "text"}}."""
    return json.loads(err.strip().splitlines()[-1])["error"]

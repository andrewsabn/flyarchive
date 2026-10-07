"""«Первые пять минут»: команды из README исполняет тест (FR-107).

В README есть раздел с блоком команд от клонирования до найденного документа, без служб и без оболочки. Блок помечен строкой-меткой в комментарии HTML; тест
берёт блок из README как есть и исполняет его строки дословно, по одной: в подменённом домашнем каталоге, с подставной службой векторов (настройка
embed_url из окружения), проверка моделью выключена самим блоком. Строки `git clone`, `pip` и `ollama` тест не исполняет (сеть, скачивание), но проверяет,
что они в блоке есть. В конце поиск находит документ из `examples/`. Изменили блок в README — тест идёт по новому тексту; команда, которой нет, краснит тест. Сценарий не ставит таймер разбора (inbox set --no-timer): после него
в подменённом домашнем каталоге нет ни файлов служб, ни вызовов systemctl. Поиск в конце — `flyarchive search`.
В `examples/` — три выдуманных документа без названий организаций, людей и адресов, в форматах, которые индексируются без библиотек по форматам.
"""
import os
import re
import shlex
import subprocess

import pytest

import gate as G
from archivekit import Archive
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")
EXAMPLES = os.path.join(ROOT, "examples")
MARK = "<!-- first-five-minutes -->"
NOT_RUN = ("git clone", "python3 -m venv", ". .venv/bin/activate", "python3 -m pip", "pip ", "pip3 ", "ollama")     # сеть, скачивание, окружение: не исполняются
PLAIN_IN_INDEX = {".txt", ".md", ".csv", ".tsv", ".log", ".json", ".yaml", ".yml", ".ini", ".conf", ".properties", ".sql"}   # index_more.PLAIN: без библиотек


def readme_text():
    with open(README, encoding="utf-8") as f:
        return f.read()


def block():
    """(текст раздела до метки, строки блока): блок — первое ограждение ```bash сразу за меткой; пустые строки и строки-комментарии отброшены."""
    text = readme_text()
    assert text.count(MARK) == 1, "метка блока должна стоять в README ровно один раз"
    before, after = text.split(MARK)
    m = re.match(r"\s*```bash\n(.*?)\n```", after, re.S)
    assert m, "сразу за меткой должен идти блок ```bash"
    lines = [line.strip() for line in m.group(1).splitlines() if line.strip() and not line.strip().startswith("#")]
    return before, lines


def runnable(lines):
    return [line for line in lines if not line.startswith(NOT_RUN)]


def examples():
    return sorted(os.listdir(EXAMPLES))


# ── блок в README ───────────────────────────────────────────────
def test_блок_создаёт_и_включает_виртуальное_окружение_раньше_чем_ставит_библиотеки():
    before, lines = block()
    text = "\n".join(lines)
    clone = next(i for i, line in enumerate(lines) if line.startswith("git clone"))
    venv = next(i for i, line in enumerate(lines) if line.startswith("python3 -m venv .venv"))
    pip = next(i for i, line in enumerate(lines) if "pip install" in line)
    assert text.count("python3 -m venv .venv") == 1 and text.count(". .venv/bin/activate") == 1, "окружение создаётся и включается один раз"
    assert text.index("python3 -m venv .venv") < text.index(". .venv/bin/activate") < text.index("pip install"), "окружение — раньше pip"
    assert clone < venv < pip, "окружение — в каталоге клона, после клонирования"
    section = before[before.rindex("## Первые пять минут"):]
    assert "виртуальное окружение" in section and "externally-managed-environment" in section, "сказано, зачем: без него pip отказывает"
    assert "Ubuntu 24.04" in section and "Debian 12" in section


def test_раздел_тесты_напоминает_что_они_идут_в_том_же_окружении():
    text = readme_text()
    section = text[text.index("\n## Тесты"):]
    section = section[:section.index("\n## ", 4)]
    assert "в том же" in section and "окружении" in section and ".venv" in section, section


def test_блок_начинается_с_клонирования_ставит_библиотеки_и_модель_и_только_потом_команды_архива():
    _, lines = block()
    assert lines[0].startswith("git clone")
    assert any(line.startswith(("python3 -m pip install", "pip install")) and "requirements.txt" in line for line in lines)
    assert any(line.startswith("ollama pull") and "bge-m3" in line for line in lines)
    first_own = min(i for i, line in enumerate(lines) if "tools/flyarchive" in line)
    assert all(line.startswith(NOT_RUN) for line in lines[:first_own]), "до первой команды архива только клонирование и установка"


@pytest.mark.parametrize("rel, heading", [("README.md", "\n## Постоянная установка"), ("README.en.md", "\n## Permanent installation")])
def test_блок_постоянной_установки_начинается_с_включения_окружения(rel, heading):
    """Назавтра человек открывает новый терминал: установка пишет в службы и в запускалку интерпретатор, которым её запустили,
    и без включённого окружения это системный Python без библиотек. Поэтому первая строка блока — включение окружения."""
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"```bash\n(.*?)\n```", text[text.index(heading):], re.S)
    assert m, f"{rel}: после заголовка нет блока ```bash"
    lines = [line.strip() for line in m.group(1).splitlines() if line.strip() and not line.strip().startswith("#")]
    assert lines[0].startswith(". .venv/bin/activate"), f"{rel}: блок установки начинается не с включения окружения: {lines[0]}"
    assert any("flyarchive install" in line for line in lines[1:]), f"{rel}: в блоке нет команды установки"


def test_блок_идёт_от_проверки_окружения_до_поиска_в_нужном_порядке():
    _, lines = block()
    order = [next(i for i, line in enumerate(lines) if part in line) for part in
             ("tools/flyarchive doctor", "tools/flyarchive init", "tools/flyarchive inbox set", "examples/", "tools/flyarchive inbox run",
              "tools/flyarchive search")]
    assert order == sorted(order) and len(set(order)) == len(order), order
    assert lines[-1].startswith("python3 tools/flyarchive search ") and "--llm off" in " ".join(lines)
    assert "tools/search.py" not in " ".join(lines), "поиск — подкоманда flyarchive search, а не отдельный сценарий"


def test_настройка_входящей_папки_в_блоке_без_таймера_чтобы_попробовавший_не_получил_включённую_службу():
    _, lines = block()
    (setting,) = [line for line in lines if "tools/flyarchive inbox set" in line]
    assert "--no-timer" in setting.split(), setting


def test_в_блоке_нет_служб_оболочки_и_systemctl():
    _, lines = block()
    text = " ".join(lines)
    assert not re.search(r"systemctl|dsh|flyarchive install|flyarchive connect", text), "сценарий — без служб и без оболочки"


def test_раздел_называет_требования_и_что_разбор_ждёт_тридцать_секунд():
    before, _ = block()
    section = before[before.rindex("## Первые пять минут"):]
    assert "Linux" in section and "WSL2" in section and "Python 3.10" in section and "bge-m3" in section
    assert "30 секунд" in section and "--wait" in section, "про выстойку файлов сказано"
    assert "--no-timer" in section and "таймер" in section and "flyarchive install" in section, "сказано, что таймер не ставится и чем его поставить"


# ── образцы ─────────────────────────────────────────────────────
def test_образцов_три_русский_и_английский_и_форматы_без_библиотек_по_форматам():
    names = examples()
    assert len(names) == 3, names
    assert {os.path.splitext(n)[1] for n in names} <= PLAIN_IN_INDEX
    cyrillic = [bool(re.search("[а-яё]", open(os.path.join(EXAMPLES, n), encoding="utf-8").read().lower())) for n in names]
    assert any(cyrillic) and not all(cyrillic), "есть и русский документ, и английский"


def test_образцы_выдуманы_ни_адресов_ни_ссылок_ни_имён_узлов_ни_домашних_путей():
    for name in examples():
        with open(os.path.join(EXAMPLES, name), encoding="utf-8") as f:
            text = f.read()
        assert not re.search(r"@|https?://|www\.|\d{1,3}(\.\d{1,3}){3}|/home/|/Users/|[A-Za-z]:\\|\.(com|org|net|info|biz)\b", text), name


def test_каждый_образец_проходит_приёмку_без_находок():
    for name in examples():
        v = G.check_file(os.path.join(EXAMPLES, name))
        assert v["decision"] == "accept" and v["findings"] == [], (name, v["findings"])


# ── сам сценарий ────────────────────────────────────────────────
@pytest.fixture
def machine(tmp_path, vectors):
    pytest.importorskip("lancedb")
    archive = Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})
    env = archive.env()
    env.pop("FLYARCHIVE_HOME")                       # каталог архива — по умолчанию, ~/flyarchive в подменённом домашнем каталоге
    return archive, env


def test_команды_блока_исполняются_дословно_и_поиск_находит_документ_из_examples(machine, vectors):
    archive, env = machine
    _, lines = block()
    outputs = []
    for line in runnable(lines):
        r = subprocess.run(["bash", "-c", line], env=env, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=300)
        assert r.returncode == 0, f"команда из README не сработала: {line}\n{r.stdout}\n{r.stderr}"
        assert "Traceback" not in r.stderr, line
        outputs.append((line, r.stdout))
    assert vectors["requests"], "векторы считала подставная служба"

    # сценарий не оставляет в системе ни файлов служб, ни следа вызова systemctl (заглушка пишет каждый вызов в журнал)
    assert not os.path.exists(os.path.join(archive.user, "systemctl.log")), "сценарий звал systemctl"
    assert not os.path.exists(os.path.join(archive.user, ".config", "systemd")), "сценарий записал файлы служб"

    # все образцы лежат в индексе; каталог архива — по умолчанию
    rows = archive.table().search().limit(100).to_list()
    assert sorted({os.path.basename(r["path"]) for r in rows}) == examples()
    assert os.path.isdir(os.path.join(archive.user, "flyarchive", "corpus"))

    # поиск: у каждого запроса из блока (русский и английский) документ, в котором есть слова запроса, — первым в выдаче
    searches = [(command, out) for command, out in outputs if command.startswith("python3 tools/flyarchive search ")]
    assert outputs[-1] in searches and len(searches) >= 2, "блок кончается поиском, и запросов два: русский и английский"
    for command, out in searches:
        words_of_command = shlex.split(command)
        assert words_of_command[:3] == ["python3", "tools/flyarchive", "search"], command
        query = words_of_command[3]
        words = [w.lower() for w in re.findall(r"\w+", query)]
        wanted = [n for n in examples() if all(w in open(os.path.join(EXAMPLES, n), encoding="utf-8").read().lower() for w in words)]
        assert wanted, f"в образцах нет документа со словами запроса {query!r}"
        paths = [line.strip() for line in out.splitlines() if line.startswith("    входящие/")]
        assert paths and os.path.basename(paths[0].split("  #")[0]) in wanted, out


# ── документ эксплуатации: то же про окружение ──────────────────
OPERATIONS = os.path.join(ROOT, "docs", "operations.md")


def operations_part(start, end):
    with open(OPERATIONS, encoding="utf-8") as f:
        text = f.read()
    part = text[text.index(start):]
    return part[:part.index(end, len(start))]


def test_эксплуатация_в_разделе_про_систему_и_python_называет_виртуальное_окружение_и_зачем_оно():
    part = operations_part("### Система и Python", "\n### ")
    assert "python3 -m venv .venv" in part and ". .venv/bin/activate" in part, part
    assert "externally-managed-environment" in part and "Ubuntu 24.04" in part and "Debian 12" in part


def test_эксплуатация_в_разделе_про_установку_говорит_что_службам_пишется_интерпретатор_запустившего_установку():
    part = operations_part("### Одной командой", "\n### ")
    assert "интерпретатор" in part and "виртуальн" in part and "окружени" in part, part
    at = part.index("интерпретатор")
    assert "служб" in part[max(0, at - 200):at + 200], "сказано про файлы служб, а не вообще"


def test_команды_установки_библиотек_в_документах_идут_после_включения_окружения():
    for path in (OPERATIONS, README):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        first_pip = text.index("python3 -m pip install")
        assert ". .venv/bin/activate" in text[:first_pip], f"{os.path.basename(path)}: первая установка библиотек раньше, чем включено окружение"

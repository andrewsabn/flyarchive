"""Документы эксплуатации и устройства написаны для читателя, у которого ничего нет.

Читатель — человек с Linux, который ставит FlyArchive у себя: у него другой каталог, другие модели, другой архив. Документ, написанный автором
для самого себя, ему не помогает: он называет файлы и сценарии, которых в открытой части нет, зовёт команды и настройки, которых в коде нет, и
несёт следы автора. Тест сверяет `docs/operations.md` и `docs/architecture.md` с кодом, а не с памятью автора.

Что держат тесты (каждая проверка — отдельный случай на каждый документ):
    paths     каждый путь к файлу репозитория (`tools/…`, `tests/…`, `docs/…`, `dsh-plugin/…`, `examples/…`, `requirements*.txt`, `*.example.json`),
              названный в обратных кавычках, в ссылке или в блоке кода, есть в открытой части (перечень — `tests/openpart.py`);
    commands  каждая команда `flyarchive <подкоманда> [<действие>]` есть в справке настоящей команды, и каждый ключ `--слово`, названный рядом с ней
              в той же строке, есть в справке этой подкоманды (способ — как у `tests/test_requirements_doc.py`: `--help` с временными домами);
    settings  каждая настройка, названная рядом со словом «настройка», с `settings.json` или в таблице настроек, и каждый ключ в примере
              `settings.json` есть в схеме `settings.SCHEMA`; каждая переменная окружения названа в схеме или есть в коде;
    services  каждая служба `flyarchive-…` есть среди тех, что пишет установка (список — из `flyarchive install --dry-run --json`);
    traces    нет меток внутреннего учёта, дат и слов автора «у меня» и «на этой машине»;
    sections  ссылка вида «см. «Раздел»» ведёт на существующий заголовок.
Ворота публикации (адреса, домашние пути, секреты, слова владельца) держит `tests/test_open_part_gate.py` — здесь они не повторяются. Следы
автора, которые зависят от настоящих значений (железо, модели, каталоги, раскладка корпуса, числа архива), и ссылки на закрытые документы ищет
закрытая часть: открытый тест настоящих значений не хранит.

Кроме общих проверок, тест держит полноту справочников документа эксплуатации: каждая настройка схемы названа в таблице раздела «Настройки» (одна строка
на настройку), перечень ключей `flyarchive search` и перечень состояний ссылки на команду (`--json` установки) называют всё, что есть в справке и в коде;
замена ссылки, оставшейся от сборки до версии 0.1, названа только в перечне состояний.

Все четыре документа (`docs/operations.md`, `docs/architecture.md`, `README.md`, `docs/for-llm.md`) проходят все проверки без исключений. Словарь LATER
пуст: в него вносят пару «проверка — файл» с названной причиной, если одну проверку для одного файла нужно ослабить на время; такой случай идёт
как xfail без строгости (он не краснит набор, а когда файл исправлен, показывается как «неожиданно прошёл»), и пара уходит из словаря.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys

import pytest

import settings
from openpart import open_files
from test_requirements_doc import Cli

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SELF = "tests/test_docs_for_reader.py"
DOCS = ("docs/operations.md", "docs/architecture.md")
OTHER = ("README.md", "docs/for-llm.md")
CHECKS = ("paths", "commands", "settings", "services", "traces")        # sections — только у DOCS (в README «раздел» — часть экрана DSH)

# Пары «файл — проверка», которые на время не проходят. Сейчас таких нет: каждый документ проходит каждую проверку. Появилась пара — у неё есть причина,
# и случай идёт как xfail без строгости; когда файл исправлен, строка отсюда убирается, и проверка снова держит файл.
LATER = {}


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── разбор текста: что в обратных кавычках, что в блоках кода ───
SPAN = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")


def lines_of(text):
    """[(номер строки, 'prose' или 'block', строка)]: ограждения ``` в разбор не идут."""
    out, fenced = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        else:
            out.append((number, "block" if fenced else "prose", line))
    return out


def units(text):
    """Места, где документ что-то называет: [(номер строки, вид, фрагмент)]. Вид span — обратные кавычки и цель ссылки, block — строка кода целиком."""
    out = []
    for number, kind, line in lines_of(text):
        if kind == "block":
            out.append((number, "block", line))
        else:
            out += [(number, "span", span) for span in SPAN.findall(line)]
            out += [(number, "span", target) for target in LINK.findall(line)]
    return out


def shown(found):
    return [f"строка {number}: {what}" for number, what in sorted(found)]


# ── пути ────────────────────────────────────────────────────────
ROOTS = ("tools", "tests", "docs", "dsh-plugin", "examples")
PATH = re.compile(r"(?<![\w~.-])(?:(?:%s)/[^\s`'\"<>(){}\[\],;:|…]*|requirements[\w-]*\.txt|[\w-]+(?:\.[\w-]+)*\.example\.json)" % "|".join(ROOTS))


def exists_in_open_part(path, files):
    """Путь назван верно: файл (или каталог, или образец с «*») есть в открытой части."""
    path = path.split("#")[0].rstrip(".:!?")
    if "*" in path:
        return any(fnmatch.fnmatchcase(name, path) for name in files)
    if path.endswith("/"):
        return any(name.startswith(path) for name in files)
    return path in files or any(name.startswith(path + "/") for name in files)


NOT_FILES = ("tools/list", "tools/call")                  # методы MCP (запросы клиента к серверу), а не файлы


def path_findings(text, files):
    """Пути, названные в документе, которых нет в открытой части (закрытые файлы в снимке тоже нет)."""
    found = []
    for number, _, unit in units(text):
        for path in PATH.findall(unit):
            if path not in NOT_FILES and not exists_in_open_part(path, files):
                found.append((number, f"нет в открытой части: {path.rstrip('.:!?')}"))
    return found


# ── команды и их ключи ──────────────────────────────────────────
# `flyarchive <подкоманда> [<действие>]`; а `tools/flyarchive` и `python3 tools/flyarchive` — та же команда, только вызванная по пути
COMMAND = re.compile(r"(?:(?<![\w/.~-])|(?<=tools/))flyarchive[ \t]+(\S+)(?:[ \t]+(\S+))?")
NAME = re.compile(r"[^\W\d_][\w-]*")                  # так выглядит подкоманда или действие (буквы, дефис); ключ, образец и многоточие — нет
KEY = re.compile(r"(?<![\w-])--[\w][\w-]*")
TAIL_END = re.compile(r"\s#(?:\s|$)|\s--(?:\s|$)")     # комментарий и «--» перед путями: дальше ключей команды нет


class Keys(Cli):
    """Справка команды, как в test_requirements_doc, и ключи подкоманды из её полного вывода."""

    def full(self, *args):
        if ("full",) + args not in self.cache:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "flyarchive"), *args, "--help"], env=self.env, capture_output=True,
                               text=True, encoding="utf-8", cwd=ROOT, timeout=120)
            assert r.returncode == 0, f"flyarchive {' '.join(args)} --help: {r.stderr[-300:]}"
            self.cache[("full",) + args] = r.stdout
        return self.cache[("full",) + args]

    def keys(self, *args):
        return set(KEY.findall(self.full(*args)))


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    return Keys(tmp_path_factory.mktemp("home"))


def command_findings(text, cli):
    """Команды и ключи, которых в справке нет: [(номер строки, что не так)]."""
    bad, known = [], cli.subcommands()
    for number, kind, unit in units(text):
        for found in COMMAND.finditer(unit):
            sub, action = found.group(1), found.group(2)
            if not NAME.fullmatch(sub):
                continue                                  # «flyarchive …», «flyarchive --help», «flyarchive <подкоманда>»: не названа
            if sub not in known:
                bad.append((number, f"подкоманды нет: flyarchive {sub}"))
                continue
            if action in ("…", "..."):
                continue                                  # «flyarchive preview …»: подкоманда названа, действие — любое
            named = action is not None and NAME.fullmatch(action.split("|")[0]) is not None
            args = (sub,)
            actions = cli.actions(sub)
            if actions is not None:
                if not named:
                    bad.append((number, f"не названо действие у {sub}"))
                    continue
                missing = [one for one in action.split("|") if one not in actions]
                bad.extend((number, f"действия нет: flyarchive {sub} {one}") for one in missing)
                if missing:
                    continue
                args = (sub, action.split("|")[0])
            elif named and cli.choices(sub) is not None and action not in cli.choices(sub):
                bad.append((number, f"значения нет: flyarchive {sub} {action}"))
                continue
            tail = unit[found.end(1):]
            tail = TAIL_END.split(tail, 1)[0]
            for key in KEY.findall(tail):
                if key not in cli.keys(*args):
                    bad.append((number, f"ключа нет: flyarchive {' '.join(args)} {key}"))
    return bad


# ── настройки ───────────────────────────────────────────────────
SETTING_KEY = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
ABOUT_SETTINGS = re.compile(r"настройк|settings\.json", re.I)
TABLE_ROW = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|")
ENV_NAME = re.compile(r"\b(?:FLYARCHIVE|BA_E2E)_[A-Z0-9_]*[A-Z0-9]")
JSON_KEY = re.compile(r'^\s{0,4}"([a-z][a-z0-9_]*)"\s*:')
TOOL_NAMES = ("search_archive", "read_document", "read_document_rich", "make_landscape", "make_diagram", "make_chart", "make_document",
              "submit_document")                           # имена инструментов MCP тоже пишутся строчными с подчёркиваниями: это не настройки
_code_cache = []


def code_text():
    """Открытый код служб и плагина, без тестов (в тестах стоят и выдуманные имена): где искать имена переменных окружения."""
    if not _code_cache:
        parts = []
        for rel in open_files():
            if rel.startswith(("tools/", "dsh-plugin/")) and not rel.startswith("dsh-plugin/test/"):
                with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
                    parts.append(f.read())
        _code_cache.append("\n".join(parts))
    return _code_cache[0]


def env_known(name):
    """Переменная названа верно: это переменная настройки из схемы, переменная, которую читают другие программы архива, или имя из кода."""
    return name in {settings.env_name(key) for key in settings.SCHEMA} or name in settings.ELSEWHERE or name in code_text()


def heading_of(lines):
    """Для каждой строки — ближайший заголовок выше: [(номер строки, текст заголовка)]."""
    out, current = {}, ""
    for number, kind, line in lines:
        if kind == "prose" and re.match(r"#{1,6} ", line):
            current = line.lstrip("# ").strip()
        out[number] = current
    return out


def json_blocks(text):
    """Блоки ```json, перед которыми (в трёх строках выше) назван settings.json: [(номер строки первого ключа, строки блока)]."""
    out, rows = [], text.splitlines()
    for index, row in enumerate(rows):
        if re.match(r"\s*```json\s*$", row) and any("settings.json" in before for before in rows[max(0, index - 3):index]):
            block = []
            for follow in rows[index + 1:]:
                if follow.lstrip().startswith("```"):
                    break
                block.append(follow)
            out.append((index + 2, block))
    return out


def setting_findings(text):
    """Названо как настройка, а в схеме её нет; переменная окружения, которой нет ни в схеме, ни в коде."""
    found, lines = [], lines_of(text)
    headings = heading_of(lines)
    for number, kind, line in lines:
        if kind == "prose" and ABOUT_SETTINGS.search(line):
            for span in SPAN.findall(line):
                if SETTING_KEY.fullmatch(span) and span not in settings.SCHEMA and span not in TOOL_NAMES:
                    found.append((number, f"настройки нет в схеме: {span}"))
        row = TABLE_ROW.match(line) if kind == "prose" else None
        if row and re.search(r"настройк", headings[number], re.I) and row.group(1) not in settings.SCHEMA:
            found.append((number, f"в таблице настроек ключа нет в схеме: {row.group(1)}"))
    for first, block in json_blocks(text):
        for offset, row in enumerate(block):
            key = JSON_KEY.match(row)
            if key and key.group(1) not in settings.SCHEMA:
                found.append((first + offset, f"в примере settings.json ключа нет в схеме: {key.group(1)}"))
    for number, _, unit in units(text):
        for name in ENV_NAME.findall(unit):
            if not env_known(name):
                found.append((number, f"переменной окружения нет ни в схеме, ни в коде: {name}"))
    return sorted(found)


# ── службы ──────────────────────────────────────────────────────
SERVICE = re.compile(r"(?<![\w/.~-])(flyarchive-[a-z][a-z-]*)(\.service|\.timer)?(?![\w/])")
NOT_UNITS = ("flyarchive-dsh-plugin", "flyarchive-sandbox", "flyarchive-preview")       # плагин, каталог песочницы, приставка имён контейнеров просмотра


@pytest.fixture(scope="module")
def units_written(tmp_path_factory):
    """Файлы служб и таймера, которые пишет установка с заданным шлюзом и оболочкой в PATH: из её пробного прогона, а не из памяти."""
    base = tmp_path_factory.mktemp("install")
    shell = base / "bin"
    shell.mkdir()
    (shell / "dsh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (shell / "dsh").chmod(0o755)
    env = {"PATH": f"{shell}:/usr/bin:/bin", "HOME": str(base / "user"), "FLYARCHIVE_HOME": str(base / "archive"), "PYTHONIOENCODING": "utf-8",
           "FLYARCHIVE_GATEWAY_BIND": "203.0.113.10", "FLYARCHIVE_PUBLIC_URL": "http://archive.example.com:8780"}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "flyarchive"), "install", "--dry-run", "--json"], env=env, capture_output=True,
                       text=True, encoding="utf-8", cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    names = sorted({os.path.basename(f["path"]) for f in json.loads(r.stdout)["files"] if f["path"].endswith((".service", ".timer"))})
    assert len(names) >= 5, names
    return names


def service_findings(text, written):
    stems = {name.rsplit(".", 1)[0] for name in written}
    found = []
    for number, _, unit in units(text):
        for match in SERVICE.finditer(unit):
            stem, suffix = match.group(1).rstrip("-"), match.group(2) or ""
            if suffix and stem + suffix not in written:
                found.append((number, f"такой службы установка не пишет: {stem}{suffix}"))
            elif not suffix and stem not in stems and stem not in NOT_UNITS:
                found.append((number, f"такой службы установка не пишет: {stem}"))
    return found


# ── следы автора ────────────────────────────────────────────────
# Каждая запись — (что это, образец). Общий вид: метки внутреннего учёта, даты, слова автора о себе. Железо, модели, каталоги, раскладку корпуса
# и числа архива автора ищет закрытая часть.
TRACES = (
    ("метка внутреннего учёта «Н-N»", re.compile(r"(?<!\w)Н-\d")),
    ("номер порции", re.compile(r"(?<![а-я])порци", re.I)),
    ("дата вида 2026-…", re.compile(r"\b202[5-9]-\d\d-\d\d\b")),
    ("слова автора о себе и о своей машине", re.compile(r"на этой машине|сейчас там|\bмо(?:й|я|ё|и|его|ей|им|ём)\b|у меня", re.I)),
)


def trace_findings(text):
    return [(number, f"{what}: «{line.strip()[:70]}»") for number, line in enumerate(text.splitlines(), 1) for what, pattern in TRACES
            if pattern.search(line)]


# ── ссылки на разделы ───────────────────────────────────────────
SECTION_REF = re.compile(r"(?:см\.|смотри|раздел[а-я]*)\s+«([^»]+)»", re.I)


def heading_key(name):
    """Заголовок и ссылка на него сравниваются без обратных кавычек, без краевых пробелов и знаков и без различия регистра."""
    return name.replace("`", "").strip(" .:").casefold()


def headings_of(text):
    return {heading_key(line.lstrip("# ")) for _, kind, line in lines_of(text) if kind == "prose" and re.match(r"#{1,6} ", line)}


def section_findings(text, known):
    return [(number, f"заголовка нет: «{name}»") for number, _, line in lines_of(text) for name in SECTION_REF.findall(line)
            if heading_key(name) not in known]


# ── сами проверки находят образцы и не трогают чистое ───────────
FILES = {"tools/flyarchive", "tools/settings.py", "docs/operations.md", "examples/file-format.txt", "requirements.txt", "settings.example.json",
         "dsh-plugin/lib/index.js", "dsh-plugin/test/client.test.mjs"}


def test_проверка_путей_находит_выдуманное_и_не_трогает_настоящее():
    sample = ("Есть: `tools/flyarchive`, `tools/settings.py`, `docs/operations.md#раздел`, `dsh-plugin/test/*.test.mjs`, `tools/`, `requirements.txt`, "
              "[пример](settings.example.json), ~/flyarchive/index/pending.jsonl, flyarchive-dsh-plugin/lib.\n"
              "```bash\npython3 tools/settings.py --reference\ncp examples/file-format.txt ~/папка/\n```\n"
              "Нет: `tools/nope.py`, `docs/ghost.md`, `examples/нет.txt`, `requirements-none.txt`, `sources.example.json`, `tests/test_*.nope`.\n")
    assert [what for _, what in path_findings(sample, FILES)] == [
        "нет в открытой части: tools/nope.py", "нет в открытой части: docs/ghost.md", "нет в открытой части: examples/нет.txt",
        "нет в открытой части: requirements-none.txt", "нет в открытой части: sources.example.json", "нет в открытой части: tests/test_*.nope"]
    assert [what for _, what in path_findings("Нет файла `docs/extra-notes.md` и `docs/en/ghost.md`.\n", FILES)] == [
        "нет в открытой части: docs/extra-notes.md", "нет в открытой части: docs/en/ghost.md"]


def test_проверка_команд_находит_выдуманное_и_не_трогает_настоящее(cli):
    bad = ("Выдумано: `flyarchive index squash`, `flyarchive inbox status --несуществующий`, `flyarchive token add x --level read --nope`.\n"
           "```bash\nflyarchive несуществующая --json\ntools/flyarchive install --nope-key\nflyarchive inbox\n```\n")
    assert [what for _, what in command_findings(bad, cli)] == [
        "действия нет: flyarchive index squash", "ключа нет: flyarchive inbox status --несуществующий", "ключа нет: flyarchive token add --nope",
        "подкоманды нет: flyarchive несуществующая", "ключа нет: flyarchive install --nope-key", "не названо действие у inbox"]
    good = ("Настоящее: `flyarchive token add <имя> --level read|full --days 30`, `flyarchive queue accept|quarantine`, `flyarchive init --json`,\n"
            "`flyarchive connect codex --remote`, `flyarchive run --dir каталог --env ИМЯ -- команда --nope`.\n"
            "```bash\npython3 tools/flyarchive install --dry-run --gateway auto   # комментарий с --nope\n"
            "flyarchive queue accept --json -- 'очередь/пачка/--nope'\nflyarchive …\nflyarchive-dsh.service и ~/flyarchive/logs\n```\n")
    assert command_findings(good, cli) == []


def test_проверка_настроек_находит_выдуманное_и_не_трогает_настоящее():
    bad = ("Задай настройку `no_such_setting` в `settings.json`. Переменная `FLYARCHIVE_NO_SUCH_THING`.\n"
           "## Настройки\n| Ключ | Что |\n|---|---|\n| `llm_local_model` | есть |\n| `ghost_key` | нет |\n"
           "В `settings.json`:\n```json\n{\n  \"search_port\": 8765,\n  \"phantom_key\": 1\n}\n```\n")
    assert [what for _, what in setting_findings(bad)] == [
        "настройки нет в схеме: no_such_setting", "переменной окружения нет ни в схеме, ни в коде: FLYARCHIVE_NO_SUCH_THING",
        "в таблице настроек ключа нет в схеме: ghost_key", "в примере settings.json ключа нет в схеме: phantom_key"]
    good = ("Настройка `llm_local_model` и `gateway_bind` в `settings.json`; инструмент `search_archive` в настройках оболочки.\n"
            "Переменные `FLYARCHIVE_HOME`, `FLYARCHIVE_EMBED_URL`, `FLYARCHIVE_TOKEN`. Файл `inbox.json` и имя `inbox_run` вне разговора о ключах.\n")
    assert setting_findings(good) == []


def test_проверка_служб_находит_выдуманное_и_не_трогает_настоящее():
    written = ["flyarchive-dsh.service", "flyarchive-inbox.service", "flyarchive-inbox.timer", "flyarchive-search.service"]
    bad = "Нет: `flyarchive-nope.service`, `systemctl --user restart flyarchive-ghost`.\n"
    assert [what for _, what in service_findings(bad, written)] == [
        "такой службы установка не пишет: flyarchive-nope.service", "такой службы установка не пишет: flyarchive-ghost"]
    good = ("Есть: `flyarchive-dsh.service`, `flyarchive-inbox.timer`, `systemctl --user restart flyarchive-search`, "
            "`~/.local/share/flyarchive-sandbox/<имя>`, `flyarchive-dsh-plugin`, `flyarchive-preview-<uuid>`, ~/flyarchive-inbox/.\n")
    assert service_findings(good, written) == []


def test_проверка_следов_автора_находит_каждый_вид_и_не_трогает_чистое():
    samples = ("Подтверждено находкой " + "Н" + "-12.", "Так делали в " + "порц" + "ии 7.", "Сделано 2026-10-05.", "Это мой сценарий.", "У меня так.",
               "На этой машине стоит ollama.", "Сейчас там пусто.")
    for line in samples:
        assert trace_findings(line) != [], line
    clean = ("Модель задаёт владелец установки. Проверено на Ubuntu 24.04 и Python 3.12. Порог 20 баллов, срок 30 секунд. "
             "Тесты идут минуты; число тестов растёт. Дата документа 2019-05-17. Адрес archive.example.com.\n")
    assert trace_findings(clean) == []


def test_проверка_ссылок_на_разделы_находит_выдуманное_и_не_трогает_настоящее():
    known = headings_of("# Название\n## Описание изображений\n### `Settings` → Archive\n```\n## не заголовок\n```\n")
    assert [what for _, what in section_findings("см. «Описание изображений» и раздел «Нет такого»; см. «ещё нет». Раздел «Архив» на экране.\n", known)] == [
        "заголовка нет: «Нет такого»", "заголовка нет: «ещё нет»", "заголовка нет: «Архив»"]
    assert section_findings("См. «описание изображений». В разделе «Описание изображений» всё есть.\n", known) == []


# ── документы ───────────────────────────────────────────────────
def marked(rel, check):
    """pytest.param для пары «файл — проверка»: пара из LATER идёт как xfail без строгости."""
    if (rel, check) in LATER:
        return pytest.param(rel, id=rel, marks=pytest.mark.xfail(reason="позже: " + LATER[(rel, check)], strict=False))
    return pytest.param(rel, id=rel)


def cases(check):
    return [marked(rel, check) for rel in DOCS + OTHER]


def test_документы_и_перечень_открытой_части_прочитаны():
    files = set(open_files())
    assert len(files) > 150, f"в открытой части файлов: {len(files)}; список файлов не прочитан"
    assert set(DOCS + OTHER) <= files
    assert {"tools/flyarchive", "tools/settings.py", "requirements.txt", "settings.example.json", "sources.example.json"} <= files
    for rel in DOCS:
        assert len(units(read(rel))) > 40, f"{rel}: обратные кавычки и блоки кода не разобраны"


def test_в_списке_LATER_только_известные_файлы_и_проверки_и_причина_у_каждой():
    for (rel, check), why in LATER.items():
        assert rel in OTHER and check in CHECKS and len(why) > 20, (rel, check)


@pytest.mark.parametrize("rel", cases("paths"))
def test_каждый_названный_путь_есть_в_открытой_части(rel):
    assert shown(path_findings(read(rel), set(open_files()))) == []


@pytest.mark.parametrize("rel", cases("commands"))
def test_каждая_команда_и_каждый_её_ключ_есть_в_справке(rel, cli):
    assert shown(command_findings(read(rel), cli)) == []


@pytest.mark.parametrize("rel", cases("settings"))
def test_каждая_настройка_и_каждая_переменная_окружения_есть_в_схеме_или_в_коде(rel):
    assert shown(setting_findings(read(rel))) == []


@pytest.mark.parametrize("rel", cases("services"))
def test_каждая_служба_есть_среди_тех_что_пишет_установка(rel, units_written):
    assert shown(service_findings(read(rel), units_written)) == []


@pytest.mark.parametrize("rel", cases("traces"))
def test_следов_автора_в_тексте_нет(rel):
    assert shown(trace_findings(read(rel))) == []


@pytest.mark.parametrize("rel", DOCS)
def test_каждая_ссылка_на_раздел_ведёт_на_существующий_заголовок(rel):
    known = set().union(*(headings_of(read(name)) for name in DOCS))
    assert shown(section_findings(read(rel), known)) == []


@pytest.mark.parametrize("key", list(settings.SCHEMA))
def test_каждая_настройка_схемы_названа_в_документе_эксплуатации(key):
    """Читателю, который открыл только документ эксплуатации, ни одна настройка не должна оставаться неназванной."""
    assert f"`{key}`" in read("docs/operations.md"), f"в docs/operations.md настройка не названа в обратных кавычках: {key}"


def test_справочник_настроек_в_документе_эксплуатации_это_таблица_по_строке_на_каждую_настройку_схемы():
    """Справочник — таблица в разделе «Настройки»: ключ, умолчание, что это. Ключи в ней — ровно ключи схемы, каждый один раз."""
    text, lines = read("docs/operations.md"), []
    start = text.index("\n## Настройки\n")
    section = text[start:text.index("\n## ", start + 5)]
    for line in section.splitlines():
        row = TABLE_ROW.match(line)
        if row:
            lines.append(row.group(1))
    assert sorted(lines) == sorted(settings.SCHEMA), (sorted(set(settings.SCHEMA) - set(lines)), sorted(set(lines) - set(settings.SCHEMA)))
    for line in section.splitlines():
        if TABLE_ROW.match(line):
            assert line.strip().strip("|").count("|") == 2, f"в справочнике три столбца: ключ, умолчание, что это: {line[:60]}"


def test_в_документе_эксплуатации_команды_разобраны_и_их_много():
    named = [unit for _, _, unit in units(read("docs/operations.md")) if COMMAND.search(unit)]
    assert len(named) > 30, f"команд названо: {len(named)}; обратные кавычки и блоки кода не разобраны"


def test_документ_эксплуатации_называет_обязательное_читателю_с_пустой_машины():
    """Смысловая проверка, которой нет у остальных: без этого разделы можно «вычистить» до пустоты, и проверки останутся зелёными."""
    text = read("docs/operations.md")
    for need in ("flyarchive doctor", "requirements.txt", "requirements-optional.txt", "flyarchive install", "flyarchive init", "settings.example.json",
                 "sources.example.json", "flyarchive inbox run", "flyarchive inbox set", "flyarchive connect", "flyarchive token add", "--no-dsh",
                 "@deepseek-ai/dsh", "MIT", "github.com/deepseek-ai/deepseek-harness", "--llm off", "llm_local_url", "llm_local_model", "gateway_bind", "public_url",
                 "settings.py --reference", "FLYARCHIVE_HOME", "systemctl"):
        assert need in text, f"в документе эксплуатации не названо: {need}"


# ── ссылка на команду: состояния в документах и первая открытая версия ──
def link_states():
    """Состояния ссылки на команду, которые даёт код установки: то, что возвращает `command_state`, и то, что `command` записывает в `state`."""
    import ast
    with open(os.path.join(ROOT, "tools", "install.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("command_state", "command"):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Constant) and isinstance(inner.value.value, str):
                    found.add(inner.value.value)
                if (isinstance(inner, ast.Assign) and isinstance(inner.value, ast.Constant) and isinstance(inner.value.value, str)
                        and any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == "state" for t in inner.targets)):
                    found.add(inner.value.value)
    return found


def test_состояния_ссылки_на_команду_из_кода_найдены():
    assert link_states() == {"disabled", "free", "exists", "legacy", "foreign", "created", "replaced", "failed"}


@pytest.mark.parametrize("rel", ["docs/operations.md", "docs/en/operations.md"])
def test_документ_эксплуатации_называет_все_состояния_ссылки_на_команду_какие_даёт_код(rel):
    paragraph = next(part for part in read(rel).split("\n\n") if "`launcher`" in part and "`state`" in part)
    missing = sorted(state for state in link_states() if f"`{state}`" not in paragraph)
    assert missing == [], f"{rel}: в перечне состояний ссылки не названы: {missing}"


OLD_LINK = re.compile(r"более ранн|раньше, чем стала|прежн[а-яё]+ (?:ссылк|установк)|сделанная раньше|клала|earlier (?:installation|link)|installation was made before", re.I)


@pytest.mark.parametrize("rel", ["docs/operations.md", "docs/en/operations.md", "docs/architecture.md", "docs/en/architecture.md", "docs/requirements.md",
                                 "docs/en/requirements.md", "tools/install.py"])
def test_замена_ссылки_от_более_ранней_установки_названа_только_в_перечне_состояний(rel):
    """У читателя первой открытой версии ранней установки не было: ссылка, оставшаяся от сборки до версии 0.1, названа одной фразой в перечне
    состояний (`replaced` и `legacy`), а в остальных местах документов, справки и докстрок о ней нет."""
    text = read(rel)
    found = [(number, line.strip()[:90]) for number, line in enumerate(text.splitlines(), 1) if OLD_LINK.search(line)]
    assert found == [], f"{rel}: о прежней установке сказано не в перечне состояний: {found}"


@pytest.mark.parametrize("rel, anchor", [("docs/operations.md", "`flyarchive search` (то же, что"), ("docs/en/operations.md", "`flyarchive search` (the same as")])
def test_перечень_ключей_flyarchive_search_в_документе_эксплуатации_называет_все_ключи_из_справки(rel, anchor, cli):
    text = read(rel)
    paragraph = text[text.index(anchor):].split("\n\n", 1)[0]
    keys = sorted(cli.keys("search") - {"--help"})
    assert "--json" in keys and "--today" in keys and len(keys) >= 6, keys
    assert [key for key in keys if f"`{key}`" not in paragraph] == [], f"{rel}: в перечне ключей `flyarchive search` не названы ключи из справки"
    assert "`-k`" in paragraph

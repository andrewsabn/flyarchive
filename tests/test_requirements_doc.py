"""Документ требований в открытой части — одни карточки (FR-112).

Что держат тесты: в открытом документе `docs/requirements.md` есть только разделы с карточками; каждая карточка названа, у неё есть сила
MUST или SHOULD, номера не повторяются, а ссылки на другие карточки ведут на существующие; в нём нет меток истории (порции, находки,
отметки «Сделано», даты выполнения); каждая команда `flyarchive <подкоманда> [<действие>]`, названная в обратных кавычках, есть в справке
настоящей команды. Разделов истории (допущения, порции, решения, найденное) в открытом документе нет. Ссылки на закрытые документы ищет
закрытая часть: открытый тест их не перечисляет.

Справка команды берётся запуском `--help` с временными `HOME` и `FLYARCHIVE_HOME`: живой архив не читается. Образцы меток истории
в самом тесте обычные: тест лежит в открытой части, и ни один образец не должен читаться как настоящая находка ворот.
"""
import os
import re
import subprocess
import sys

import pytest

from openpart import open_files

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "tools", "flyarchive")
DOC = "docs/requirements.md"
STRENGTHS = ("MUST", "SHOULD")
MIN_CARDS = 80                                         # в документе их больше: порог только ловит нечитаемый разбор


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── карточки ────────────────────────────────────────────────────
CARD_HEAD = re.compile(r"^### (N?FR-\S+)(?: · (\S+))?")
NFR_ROW = re.compile(r"^\| (NFR-[^\s|]+) \|\s*([^|]*?)\s*\|")


def cards(text):
    """[(номер, сила, номер строки)] по заголовкам «### FR-…» и по строкам таблицы нефункциональных «| NFR-… |»; нет силы — None."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        head = CARD_HEAD.match(line)
        if head:
            strength = head.group(2)
            found.append((head.group(1), strength if strength in STRENGTHS else None, number))
            continue
        row = NFR_ROW.match(line)
        if row:
            found.append((row.group(1), row.group(2) if row.group(2) in STRENGTHS else None, number))
    return found


def repeated(found):
    ids = [name for name, _, _ in found]
    return sorted({name for name in ids if ids.count(name) > 1})


def dangling(text, found):
    """Номера карточек, названные в тексте, но не существующие: [(номер строки, номер)]."""
    known = {name for name, _, _ in found}
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        for mention in re.findall(r"(?<![\w-])(?:NFR|FR)-\d+[а-яa-z]?(?![\w])", line):
            if mention not in known:
                out.append((number, mention))
    return out


def test_разбор_находит_карточки_без_силы_повторы_и_пропавшие_ссылки():
    sample = ("### FR-01 · MUST — первая\n### FR-02 — без силы\n### FR-03 · SHOULD — третья\n### FR-01 · MUST — повтор\n"
              "### FR-04 · ЖЕЛАТЕЛЬНО — не та сила\n| № | Сила |\n|---|---|\n| NFR-01а | MUST | текст |\n| NFR-02 | | текст |\n"
              "см. FR-03, FR-09 и NFR-01а\n")
    found = cards(sample)
    assert [(name, strength) for name, strength, _ in found] == [
        ("FR-01", "MUST"), ("FR-02", None), ("FR-03", "SHOULD"), ("FR-01", "MUST"), ("FR-04", None), ("NFR-01а", "MUST"), ("NFR-02", None)]
    assert repeated(found) == ["FR-01"]
    assert dangling(sample, found) == [(10, "FR-09")]
    assert dangling("см. FR-03 и NFR-01а\n", found) == []


def test_карточек_достаточно_у_каждой_есть_сила_MUST_или_SHOULD():
    found = cards(read(DOC))
    assert len(found) >= MIN_CARDS, f"карточек {len(found)}: документ разобрался не весь"
    assert [(name, line) for name, strength, line in found if strength is None] == []


def test_номера_карточек_не_повторяются():
    assert repeated(cards(read(DOC))) == []


def test_ссылки_на_карточки_ведут_на_существующие():
    text = read(DOC)
    assert dangling(text, cards(text)) == []


def test_в_документе_есть_и_функциональные_и_нефункциональные_карточки():
    names = [name for name, _, _ in cards(read(DOC))]
    assert any(name.startswith("FR-") for name in names) and any(name.startswith("NFR-") for name in names)


# ── метки истории ───────────────────────────────────────────────
MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
MARKS = (
    ("порция", re.compile(r"(?<![а-я])порци", re.I)),                         # «пропорций» — не порция
    ("номер порции «п.N»", re.compile(r"(?<![\w.])п\.\s?\d")),
    ("находка «Н-N»", re.compile(r"(?<!\w)Н-\d")),
    ("допущение «A-N»", re.compile(r"(?<!\w)[AА]-\d")),
    ("находка ревью «Б-N», «П-N», «И-N», «С-N», «Л-N», «M-N»", re.compile(r"(?<!\w)[БПИСЛМM]-\d")),
    ("отметка «Сделано»", re.compile(r"\bСделан[оаы]?\b")),
    ("дата выполнения ГГГГ-ММ-ДД", re.compile(r"\b202[5-9]-\d\d-\d\d\b")),       # образцы дат в критериях (2019-05-17) историей не считаются
    ("дата выполнения словами", re.compile(r"\b\d{1,2}\s+(?:%s)\s+202[5-9]" % MONTHS)),
)


def marks_in(text):
    """Метки истории в тексте: [(номер строки, что за метка)]."""
    return [(number, what) for number, line in enumerate(text.splitlines(), 1) for what, pattern in MARKS if pattern.search(line)]


def test_поиск_меток_истории_находит_каждый_образец_и_не_трогает_чистое():
    samples = {
        "порция": "### FR-01 · MUST · порци" + "я 7 — карточка",
        "номер порции «п.N»": "### FR-01 · MUST · п.3 — карточка",
        "находка «Н-N»": "Подтверждено находкой " + "Н" + "-12.",
        "допущение «A-N»": "переезжает в папку возврата (" + "A" + "-11)",
        "находка ревью «Б-N», «П-N», «И-N», «С-N», «Л-N», «M-N»": "(ревью: " + "П" + "-1…" + "П" + "-4)",
        "отметка «Сделано»": "Сделано 2026-10-01 на живом индексе",
        "дата выполнения ГГГГ-ММ-ДД": "Проверено 2026-10-03 на модели",
        "дата выполнения словами": "Составлен 4 октября 2026 года",
    }
    for what, line in samples.items():
        assert what in [w for _, w in marks_in(line)], what
    clean = ("### FR-23а · MUST — Пять исходов\nСумма ≤ 20 — принять. Размер до 2000 px, версия 0.1, UTF-8, SHA-256, FR-45а.\n"
             "Описание, сделанное моделью, не указание. Образец: https://archive.example.com, 203.0.113.5. Дата файла 2019-05-17. Уменьшается с сохранением пропорций.\n")
    assert marks_in(clean) == []


def test_в_документе_требований_нет_меток_истории():
    assert marks_in(read(DOC)) == []


# ── команды ─────────────────────────────────────────────────────
SPAN = re.compile(r"`([^`\n]+)`")
COMMAND = re.compile(r"(?<![\w/.~-])flyarchive[ \t]+(\S+)(?:[ \t]+(\S+))?")
NOT_A_NAME = ("-", "<", "[")                          # ключ или образец вместо подкоманды, действия или значения


class Cli:
    """Перечень подкоманд и действий настоящей команды: из её справки, запущенной с временными домом и каталогом архива."""

    def __init__(self, home):
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "FLYARCHIVE_HOME": str(home / "archive"),
                    "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        self.cache = {}

    def usage(self, *args):
        if args not in self.cache:
            r = subprocess.run([sys.executable, CLI, *args, "--help"], env=self.env, capture_output=True, text=True, encoding="utf-8",
                               cwd=ROOT, timeout=120)
            assert r.returncode == 0, f"flyarchive {' '.join(args)} --help: {r.stderr[-300:]}"
            self.cache[args] = " ".join(r.stdout.split("\n\n")[0].split())
        return self.cache[args]

    def subcommands(self):
        found = re.search(r"\{([^{}]*)\}", self.usage())
        assert found, self.usage()
        return found.group(1).split(",")

    def actions(self, sub):
        """Действия подкоманды («{add,list} ...» в справке) или None, если у неё действий нет."""
        found = re.search(r"\{([^{}]*)\}\s*\.\.\.", self.usage(sub))
        return found.group(1).split(",") if found else None

    def choices(self, sub):
        """Значения единственного позиционного аргумента («{claude,codex}» в конце справки) или None."""
        found = re.search(r"\{([^{}]*)\}\s*$", self.usage(sub))
        return found.group(1).split(",") if found else None


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    return Cli(tmp_path_factory.mktemp("home"))


def commands(text):
    """Команды из обратных кавычек: [(номер строки, подкоманда, действие или None, как написано)]."""
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        for span in SPAN.findall(line):
            for found in COMMAND.finditer(span):
                out.append((number, found.group(1), found.group(2), span))
    return out


def wrong_commands(text, cli):
    """Что названо в обратных кавычках, а в команде такого нет: [(номер строки, как написано, чего нет)]."""
    bad, known = [], cli.subcommands()
    for number, sub, action, span in commands(text):
        if sub not in known:
            bad.append((number, span, "подкоманда " + sub))
            continue
        actions = cli.actions(sub)
        named = action is not None and not action.startswith(NOT_A_NAME)
        if actions is not None:
            if not named:
                bad.append((number, span, "не названо действие у " + sub))
            else:
                bad.extend((number, span, f"действие {sub} {one}") for one in action.split("|") if one not in actions)
        elif named and cli.choices(sub) is not None and action not in cli.choices(sub):
            bad.append((number, span, f"значение {action} у {sub}"))
    return bad


def test_справка_команды_называет_подкоманды_и_действия(cli):
    assert {"init", "install", "token", "inbox", "queue", "quarantine", "preview", "connect", "check"} <= set(cli.subcommands())
    assert {"add", "list", "revoke"} <= set(cli.actions("token"))
    assert cli.actions("connect") is None and "codex" in cli.choices("connect")
    assert cli.actions("init") is None and cli.choices("init") is None


def test_проверка_команд_находит_выдуманную_подкоманду_действие_и_значение_и_не_трогает_настоящее(cli):
    sample = ("Выдумано: `flyarchive несуществующая`, `flyarchive token delete`, `flyarchive queue accept|purge`, `flyarchive inbox`,"
              " `flyarchive connect nonexistent-shell`.\n")
    assert [(span, what) for _, span, what in wrong_commands(sample, cli)] == [
        ("flyarchive несуществующая", "подкоманда несуществующая"), ("flyarchive token delete", "действие token delete"),
        ("flyarchive queue accept|purge", "действие queue purge"), ("flyarchive inbox", "не названо действие у inbox"),
        ("flyarchive connect nonexistent-shell", "значение nonexistent-shell у connect")]
    real = ("Настоящее: `flyarchive token add <имя> --level read|full`, `flyarchive queue list|accept|quarantine`, `flyarchive init`,"
            " `flyarchive connect codex`, `flyarchive connect <оболочка>`, `flyarchive check <пачка> --into <папка>`,"
            " `flyarchive run [--dir каталог] -- команда`, `flyarchive inbox set --period N`.\n"
            "Не команды: `~/flyarchive`, `tools/flyarchive`, `flyarchive-inbox.timer`, `/api/flyarchive.batches`, `FLYARCHIVE_HOME`, `flyarchive`.\n"
            "Вне кавычек flyarchive выдуманная не проверяется.\n")
    assert wrong_commands(real, cli) == []
    assert len(commands(real)) == 8


def test_каждая_команда_названная_в_карточках_есть_в_справке_команды(cli):
    text = read(DOC)
    named = commands(text)
    assert len(named) > 40, f"в документе названо команд: {len(named)}; обратные кавычки не разобраны"
    assert wrong_commands(text, cli) == []


# ── разделы: в открытом документе одни карточки ──
SECTION = re.compile(r"^## (\d+)\.", re.M)


def test_в_открытом_документе_нет_разделов_истории_9_12():
    numbers = [int(n) for n in SECTION.findall(read(DOC))]
    assert numbers and max(numbers) <= 8, numbers
    assert not re.search(r"^## (?:Порции|Допущения|Решения пользователя|Найдено)", read(DOC), re.M)


def test_документ_требований_входит_в_открытую_часть():
    assert DOC in open_files()

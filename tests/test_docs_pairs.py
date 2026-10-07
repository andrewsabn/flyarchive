"""Открытые документы есть на русском и на английском, и перевод не расходится с оригиналом (решение владельца: документация на двух языках).

Русский документ — оригинал, английский лежит рядом: `README.ru.md` → `README.md`, `CHANGELOG.ru.md` → `CHANGELOG.md` (первая страница
репозитория — английская: хостинг показывает посетителю `README.md` из корня), `docs/<имя>.md` →
`docs/en/<имя>.md`. Тест не читает смысл — он держит то, что можно сверить: у пары те же заголовки тех же уровней в том же порядке, те же блоки
кода с теми же командами, те же таблицы, те же имена в обратных кавычках, ссылки ведут на документы своего языка, а в английском тексте нет
русского вне имён и цитат. Правка одного документа пары без другого краснит тест.
"""
import os
import re

import pytest

from openpart import open_files

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIRS = {
    "README.ru.md": "README.md",
    "CHANGELOG.ru.md": "CHANGELOG.md",
    "docs/operations.md": "docs/en/operations.md",
    "docs/architecture.md": "docs/en/architecture.md",
    "docs/for-llm.md": "docs/en/for-llm.md",
    "docs/requirements.md": "docs/en/requirements.md",
}
CYRILLIC = re.compile("[а-яёА-ЯЁ]")
FENCE = re.compile(r"^(```|~~~)\s*(\S*)")
TICK = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")
QUOTED = re.compile(r"«[^»]*»|\"[^\"\n]*\"")          # цитата сообщения программы: в английском тексте она остаётся русской
PLACEHOLDER = re.compile(r"<[^<>!/\s][^<>]*>")       # подстановка в угловых скобках; метки и теги HTML сюда не входят
DATE_WORD = re.compile(r"(?<![а-яёА-ЯЁ])[ГМДЧС]{2,}(?![а-яёА-ЯЁ])")
DATE_LETTERS = str.maketrans("ГМДЧС", "YMDHS")
HEADING_SAMPLE = re.compile(r"^(#{1,6} .*? — ).*$")
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./~:-]*")
CARD = re.compile(r"\b(?:FR|NFR)-\d+[а-я]")       # номер карточки с буквой: имя, одно на оба языка
CASES = list(PAIRS.items())
SAME = {en: ru for ru, en in PAIRS.items()}       # имя перевода — то же имя, что имя оригинала: каждый язык отсылает читателя к своему варианту


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def split(text):
    """(строки вне блоков кода, блоки кода как пары «язык блока, строки»)."""
    prose, blocks, block = [], [], None
    for line in text.splitlines():
        fence = FENCE.match(line.strip())
        if fence:
            if block is None:
                block = (fence.group(2), [])
            else:
                blocks.append(block)
                block = None
            continue
        (prose if block is None else block[1]).append(line)
    assert block is None, "блок кода не закрыт"
    return prose, blocks


def headings(prose):
    return [len(m.group(1)) for m in (re.match(r"^(#{1,6}) \S", line) for line in prose) if m]


def tables(prose):
    """Таблицы как списки чисел столбцов по строкам."""
    found, current = [], None
    for line in prose:
        if line.strip().startswith("|"):
            current = current if current is not None else []
            current.append(line.strip().strip("|").count("|") + 1)
        elif current is not None:
            found.append(current)
            current = None
    if current is not None:
        found.append(current)
    return found


def neutral(text):
    """Имя или строка кода без того, что перевод вправе заменить: слова подстановки в угловых скобках (`<токен>` — `<token>`)
    и буквы образца даты (`ГГГГ-ММ-ДД` — `YYYY-MM-DD`); у образца заголовка (`### FR-23 · MUST — Баллы и порог`) — название после тире.
    Всё остальное обязано совпасть знак в знак."""
    text = HEADING_SAMPLE.sub(r"\1", text)
    return DATE_WORD.sub(lambda m: m.group().translate(DATE_LETTERS), PLACEHOLDER.sub("<>", text))


def command(line):
    """Строка блока кода без пояснения после «#»: команды и пути у пары обязаны совпасть, пояснение переводится."""
    return neutral(re.sub(r"(^|\s+)#.*$", "", line).rstrip())


def names(lines):
    """Латинские имена в строках схемы с числом вхождений: службы, порты, файлы, команды — то, что перевод подписей обязан сохранить.
    Имя документа пары считается одним именем, как и в `ticks`: перечень файлов называет читателю вариант на его языке."""
    found = {}
    for line in lines:
        for token in NAME.findall(line):
            token = token.rstrip(".:-")
            token = SAME.get(token, token)
            if token:
                found[token] = found.get(token, 0) + 1
    return found


def block_gap(ru, en):
    """Чем блок кода перевода расходится с блоком оригинала: слова для отказа или None.

    Блок с названным языком (`bash`, `json`) — команды и данные: строки совпадают, переводится только пояснение после «#».
    Блок без языка — схема или перечень с подписями: подписи переводятся, поэтому сверяются число строк и латинские имена,
    которые оригинал называет (порт, служба, файл): в переводе каждое из них остаётся."""
    (tag_a, a), (tag_b, b) = ru, en
    if tag_a != tag_b:
        return f"язык блока «{tag_a}» и «{tag_b}»"
    if len(a) != len(b):
        return f"строк {len(a)} и {len(b)}"
    if tag_a:
        for number, (x, y) in enumerate(zip(a, b), 1):
            if command(x) != command(y):
                return f"строка {number}: {command(x)!r} и {command(y)!r}"
        return None
    lost = {token: counts for token, counts in differs(names(a), names(b)).items() if counts[0] > counts[1]}
    return f"в переводе схемы пропали имена (имя: в оригинале, в переводе): {lost}" if lost else None


def ticks(prose):
    """Имена в обратных кавычках с числом вхождений: команды, пути, настройки, службы, каталоги архива. Перевод их не трогает.

    Одно исключение — имена самих документов пары: читателя английский текст отсылает к английскому варианту (`docs/en/operations.md`),
    поэтому имя перевода считается тем же именем, что и имя оригинала."""
    found = {}
    for line in prose:
        for token in TICK.findall(line):
            token = neutral(SAME.get(token, token))
            found[token] = found.get(token, 0) + 1
    return found


def differs(a, b):
    """Чем расходятся два набора имён: {имя: (в оригинале, в переводе)} — чтобы отказ теста называл место, а не два словаря целиком."""
    return {token: (a.get(token, 0), b.get(token, 0)) for token in sorted(set(a) | set(b)) if a.get(token, 0) != b.get(token, 0)}


def stray_cyrillic(prose):
    """Строки английского документа, где русское стоит не в обратных кавычках и не в цитате: [(номер строки, строка)]."""
    out = []
    for number, line in enumerate(prose, 1):
        bare = CARD.sub("", QUOTED.sub("", TICK.sub("", line)))
        if CYRILLIC.search(bare):
            out.append((number, line.strip()[:120]))
    return out


# ── сами проверки на образцах ───────────────────────────────────
def test_разбор_документа_отделяет_блоки_кода_считает_заголовки_и_таблицы():
    prose, blocks = split("# A\n\ntext `x`\n\n```bash\ncmd one   # пояснение\n```\n\n## B\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nсхема\n```\n")
    assert headings(prose) == [1, 2] and tables(prose) == [[2, 2, 2]]
    assert blocks == [("bash", ["cmd one   # пояснение"]), ("", ["схема"])]
    assert command("cmd one   # пояснение") == "cmd one" and command("cmd # x") == "cmd" and command("# только пояснение") == ""
    assert ticks(prose) == {"x": 1} and differs({"x": 1, "y": 2}, {"x": 1, "y": 1, "z": 1}) == {"y": (2, 1), "z": (0, 1)}
    assert ticks(["see `docs/en/operations.md` and `README.md`"]) == ticks(["см. `docs/operations.md` и `README.ru.md`"])


def test_перевод_вправе_заменить_только_слова_подстановки_и_буквы_образца_даты():
    assert neutral("flyarchive connect <оболочка>") == neutral("flyarchive connect <shell>") == "flyarchive connect <>"
    assert neutral("ГГГГММДД-ЧЧММСС") == "YYYYMMDD-HHMMSS" and neutral("ГГГГ-ММ-ДД") == neutral("YYYY-MM-DD")
    assert neutral("входящие") == "входящие" != neutral("inbox"), "имя каталога архива — не подстановка: оно остаётся русским"
    assert neutral("<!-- first-five-minutes -->") == "<!-- first-five-minutes -->" and neutral("ГОД") == "ГОД"
    assert neutral("### FR-23 · MUST — Баллы и порог") == neutral("### FR-23 · MUST — Scores and the threshold") != neutral("### FR-24 · MUST — x")
    assert command("read_document path=\"<path из выдачи>\"  # пояснение") == command("read_document path=\"<path from the results>\"")


def test_блок_с_языком_сверяется_построчно_а_схема_по_числу_строк_и_именам():
    assert block_gap(("bash", ["flyarchive init   # завести архив"]), ("bash", ["flyarchive init   # create the archive"])) is None
    assert "строка 1" in block_gap(("bash", ["flyarchive init"]), ("bash", ["flyarchive install"]))
    assert "язык блока" in block_gap(("bash", ["x"]), ("", ["x"]))
    scheme = ("", ["браузер ── DSH :3080 ─┐", "   поиск :8765   tools/webui.py"])
    assert block_gap(scheme, ("", ["browser ── DSH :3080 ─┐", "   search :8765   tools/webui.py"])) is None
    assert "8765" in block_gap(scheme, ("", ["browser ── DSH :3080 ─┐", "   search   tools/webui.py"]))
    assert "строк 2 и 1" in block_gap(scheme, ("", ["browser ── DSH :3080 ─┐ search :8765 tools/webui.py"]))
    assert block_gap(("", ["CHANGELOG.ru.md   что изменилось"]), ("", ["CHANGELOG.md   what changed"])) is None, "перечень называет вариант своего языка"
    assert "CHANGELOG.ru.md" in block_gap(("", ["CHANGELOG.ru.md   что изменилось"]), ("", ["LICENSE   what changed"]))


def test_поиск_русского_в_английском_тексте_пропускает_имена_и_цитаты_и_находит_остальное():
    assert stray_cyrillic(["the `входящие` (inbox) folder", "it prints «Принято: 3» (accepted: 3)"]) == []
    assert stray_cyrillic(["plain text", "забытая строка"]) == [(2, "забытая строка")]


# ── пары ────────────────────────────────────────────────────────
@pytest.mark.parametrize("ru, en", CASES, ids=[ru for ru, _ in CASES])
def test_у_документа_есть_английский_вариант(ru, en):
    assert os.path.isfile(os.path.join(ROOT, ru)), ru
    assert os.path.isfile(os.path.join(ROOT, en)), f"нет английского варианта {en} для {ru}"


@pytest.mark.parametrize("ru, en", CASES, ids=[ru for ru, _ in CASES])
def test_заголовки_пары_совпадают_по_уровням_и_порядку(ru, en):
    assert headings(split(read(ru))[0]) == headings(split(read(en))[0])


@pytest.mark.parametrize("ru, en", CASES, ids=[ru for ru, _ in CASES])
def test_блоки_кода_пары_совпадают(ru, en):
    a, b = split(read(ru))[1], split(read(en))[1]
    assert len(a) == len(b), f"блоков кода {len(a)} и {len(b)}"
    assert {number: gap for number, (x, y) in enumerate(zip(a, b), 1) if (gap := block_gap(x, y))} == {}, "номер блока: расхождение"


@pytest.mark.parametrize("ru, en", CASES, ids=[ru for ru, _ in CASES])
def test_таблицы_пары_совпадают_числом_строк_и_столбцов(ru, en):
    assert tables(split(read(ru))[0]) == tables(split(read(en))[0])


@pytest.mark.parametrize("ru, en", CASES, ids=[ru for ru, _ in CASES])
def test_имена_в_обратных_кавычках_у_пары_те_же(ru, en):
    assert differs(ticks(split(read(ru))[0]), ticks(split(read(en))[0])) == {}, "имя: (раз в оригинале, раз в переводе)"


@pytest.mark.parametrize("ru, en", CASES, ids=[ru for ru, _ in CASES])
def test_в_английском_документе_нет_русского_вне_имён_и_цитат(ru, en):
    assert stray_cyrillic(split(read(en))[0]) == []


@pytest.mark.parametrize("ru, en", CASES, ids=[ru for ru, _ in CASES])
def test_ссылки_ведут_на_документы_своего_языка_и_на_существующие_файлы(ru, en):
    # на другой язык ведёт одна законная ссылка — на свой же документ в переводе («English» / «Russian» в шапке)
    for rel, pair, other in ((ru, en, set(PAIRS.values())), (en, ru, set(PAIRS))):
        base = os.path.dirname(rel)
        for target in LINK.findall(TICK.sub("", "\n".join(split(read(rel))[0]))):
            if "://" in target or target.startswith("#"):
                continue
            path = os.path.normpath(os.path.join(base, target.split("#")[0])).replace(os.sep, "/")
            assert os.path.exists(os.path.join(ROOT, path)), f"{rel}: ссылка на несуществующее {target}"
            assert path not in other or path == pair, f"{rel}: ссылка на документ другого языка {target}"


def test_русский_и_английский_варианты_в_корне_ссылаются_друг_на_друга():
    for name in ("README", "CHANGELOG"):
        assert f"({name}.md)" in read(f"{name}.ru.md") and f"({name}.ru.md)" in read(f"{name}.md"), name


def cyrillic_share(text):
    letters = [ch for ch in text if ch.isalpha()]
    return len(CYRILLIC.findall(text)) / max(1, len(letters))


@pytest.mark.parametrize("name", ["README", "CHANGELOG"])
def test_первая_страница_репозитория_английская_а_оригинал_лежит_рядом(name):
    """Хостинг показывает посетителю файл `README.md` из корня. Описание и темы репозитория английские, значит и первая страница английская;
    русский оригинал лежит рядом под именем с `.ru`. В английском варианте русское остаётся только в цитатах вывода программы."""
    assert cyrillic_share(read(f"{name}.md")) < 0.2 < cyrillic_share(read(f"{name}.ru.md")), name


def test_прежних_имён_переводов_нет_ни_файлами_ни_в_тексте():
    """Перевод больше не носит имя с `.en`: такой файл в корне или ссылка на него — след прежней раскладки, читателя она ведёт в никуда."""
    old = [name + ".en.md" for name in ("README", "CHANGELOG")]
    assert not [rel for rel in old if os.path.exists(os.path.join(ROOT, rel))]
    found = []
    for rel in open_files():
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore") as f:
            text = f.read()
        found += [(rel, name) for name in old if name in text]
    assert found == []


def test_карточки_требований_у_пары_те_же_номера_и_та_же_сила():
    card = re.compile(r"^### ((?:FR|NFR)-[0-9а-яa-z]+) · (MUST|SHOULD)", re.M)
    cards = card.findall(read("docs/requirements.md"))
    assert cards and cards == card.findall(read("docs/en/requirements.md"))

"""Тесты ворот публикации (FR-90): сами ворота, не проверка репозитория.

Все образцы выдуманные и собираются из кусков: тест лежит в каталоге tests/, который ворота
проверяют тоже, и ни один образец не должен читаться как настоящая находка в самом исходнике.
Проверка всего репозитория — отдельной командой: python3 tests/publication_gate.py
"""
import hashlib
import json
import os
import random
import shutil
import string
import subprocess
import sys

import pytest

import publication_gate as G

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "publication_gate.py")
ALLOW = "tests/publication_allow.txt"
HOSTS = "tests/publication_hosts.txt"

# ── образцы (собираются из кусков) ──────────────────────────────
WORK = "demoproject"                      # условное рабочее имя проекта: его задаёт список имён, а не сами ворота
BS = chr(92)                              # обратная косая
LINUX = "/home" + "/"
WIN = "C:" + BS + "Users" + BS
WIN2 = "C:" + BS * 2 + "Users" + BS * 2
WINFS = "C:" + "/" + "Users" + "/"
WSL = "/mnt" + "/c/" + "Users" + "/"
MAC = "/Users" + "/"
HOME_PREFIXES = {"linux": LINUX, "windows": WIN, "windows_escaped": WIN2, "windows_slash": WINFS, "wsl": WSL, "mac": MAC}
SEP = {"linux": "/", "windows": BS, "windows_escaped": BS * 2, "windows_slash": "/", "wsl": "/", "mac": "/"}
PRIVATE_IP = "192.168." + "1.5"
HOSTNAME = "ivanov-pc"                    # выдуманное имя машины и слово списка
ORG = "Зеленоградгрупп"                    # выдуманная организация
PERSON = "Пётр Сидоров"                   # выдуманный человек
GITIGNORE = "secrets/\nlogs/\nreports/\nindex/\ncorpus/\ncache/\n__pycache__/\n"
DUMMY = ("слово-которого" + "-нет-в-образцах",)


def token_like(n, alphabet=string.ascii_letters + string.digits + "_-", seed=1):
    """Случайная строка с буквами обоих регистров и цифрой."""
    rnd = random.Random(seed)
    while True:
        s = "".join(rnd.choice(alphabet) for _ in range(n))
        if any(c.islower() for c in s) and any(c.isupper() for c in s) and any(c.isdigit() for c in s):
            return s


def wordlist(tmp_path, *lines):
    p = tmp_path / "words.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def snap(tmp_path, files, gitignore=True):
    """Каталог-снимок из словаря {путь: строка или байты}."""
    root = tmp_path / "snap"
    root.mkdir(exist_ok=True)
    if gitignore and ".gitignore" not in files:
        files = {**files, ".gitignore": GITIGNORE}
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return str(root)


def namelist(tmp_path, *lines):
    p = tmp_path / "names.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def run(tmp_path, files, words=DUMMY, gitignore=True, names=(WORK,)):
    """Прогон по каталогу-снимку со списком слов и списком рабочих имён (names=() — без списка имён)."""
    return G.scan(root=snap(tmp_path, files, gitignore), words_path=wordlist(tmp_path, *words), names_path=namelist(tmp_path, *names) if names else None)


def kind(rep, name):
    return [f for f in rep.findings if f["kind"] == name]


def kinds(rep):
    return sorted({f["kind"] for f in rep.findings})


def one(tmp_path, line, words=DUMMY, name="a.txt"):
    """Находки одной строки, лежащей в файле name."""
    return run(tmp_path, {name: line}, words)


def cli(*args, env_extra=None, script=GATE, cwd=None, path=None):
    env = {k: v for k, v in G.clean_git_env().items() if k not in ("FLYARCHIVE_GATE_WORDS", "FLYARCHIVE_GATE_NAMES")}
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra or {})
    if path is not None:
        env["PATH"] = path
    p = subprocess.run([sys.executable, script, *args], capture_output=True, env=env, cwd=cwd, timeout=120)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


# ── общее устройство ────────────────────────────────────────────
def test_чистый_снимок_даёт_пустой_отчёт_и_код_0(tmp_path):
    root = snap(tmp_path, {"README.md": "# Проект\nЗдесь нет ничего личного.\n", "tools/a.py": "print('привет')\n"})
    rep = G.scan(root=root, words_path=wordlist(tmp_path, *DUMMY))
    assert rep.findings == [] and rep.files_checked == 3
    assert cli("--root", root, "--words", wordlist(tmp_path, *DUMMY))[0] == 0


def test_находка_называет_файл_строку_вид_и_текст(tmp_path):
    rep = run(tmp_path, {"tools/x.py": "a = 1\nb = 2\npath = '" + LINUX + "ivanov/archive'\n"})
    (f,) = kind(rep, "home_path")
    assert f["file"] == "tools/x.py" and f["line"] == 3
    assert f["text"].startswith(LINUX + "ivanov")


def test_вывод_команды_называет_файл_и_строку_и_код_1(tmp_path):
    root = snap(tmp_path, {"tools/x.py": "a = 1\nb = 2\nhost = '" + PRIVATE_IP + "'\n"})
    code, out, err = cli("--root", root, "--words", wordlist(tmp_path, *DUMMY))
    assert code == 1
    assert "tools/x.py:3: ip: " + PRIVATE_IP in out


def test_номера_строк_считаются_с_единицы_и_не_сбиваются_переводами_строки(tmp_path):
    crlf = "первая\r\nвторая\r\n\r\nчетвёртая " + PRIVATE_IP + "\r\nпятая\n"
    odd = "а\x0cб\u2028в\n" + PRIVATE_IP + "\n"          # разрыв страницы и разделитель строк — не новые строки
    rep = run(tmp_path, {"a.txt": crlf, "b.txt": odd})
    assert [(f["file"], f["line"]) for f in kind(rep, "ip")] == [("a.txt", 4), ("b.txt", 2)]


def test_найденный_текст_не_длиннее_80_знаков(tmp_path):
    url = "https:" + "//" + "long." * 40 + "zelenogradgroup.test/page"
    rep = run(tmp_path, {"a.txt": "см. " + url})
    assert rep.findings and all(len(f["text"]) <= 80 for f in rep.findings)


def test_json_даёт_находки_и_сводку_по_видам_и_файлам(tmp_path):
    root = snap(tmp_path, {"tools/x.py": PRIVATE_IP + "\n" + WORK + "\n", "docs/y.md": WORK + "\n"})
    code, out, err = cli("--root", root, "--words", wordlist(tmp_path, *DUMMY), "--names", namelist(tmp_path, WORK), "--json")
    assert code == 1
    data = json.loads(out)
    assert {"file", "line", "kind", "text"} <= set(data["findings"][0])
    s = data["summary"]
    assert s["by_kind"] == {"ip": 1, "working_name": 2}
    assert s["by_file"] == {"tools/x.py": 2, "docs/y.md": 1}
    assert s["by_dir"] == {"tools": 2, "docs": 1}
    assert s["total"] == 3 and s["files_checked"] == 3


def test_находки_отсортированы_по_файлу_и_строке(tmp_path):
    rep = run(tmp_path, {"b.txt": WORK, "a.txt": "x\n" + WORK + "\n" + WORK, "c/d.txt": WORK})
    keys = [(f["file"], f["line"]) for f in rep.findings]
    assert keys == sorted(keys) and len(keys) == 4


# ── список слов ─────────────────────────────────────────────────
def test_без_списка_слов_код_2_и_понятное_сообщение(tmp_path):
    root = snap(tmp_path, {"a.txt": "чисто\n"})
    code, out, err = cli("--root", root)
    assert code == 2
    assert "--words" in err and "FLYARCHIVE_GATE_WORDS" in err
    assert out.strip() == ""                                   # «чисто» не печатается


def test_scan_без_списка_слов_бросает_ошибку_ворот(tmp_path, monkeypatch):
    monkeypatch.delenv("FLYARCHIVE_GATE_WORDS", raising=False)
    with pytest.raises(G.GateError):
        G.scan(root=snap(tmp_path, {"a.txt": "чисто\n"}))


def test_несуществующий_файл_слов_даёт_код_2(tmp_path):
    root = snap(tmp_path, {"a.txt": "чисто\n"})
    assert cli("--root", root, "--words", str(tmp_path / "нет-такого.txt"))[0] == 2


def test_список_из_одних_комментариев_не_считается_списком(tmp_path):
    root = snap(tmp_path, {"a.txt": "чисто\n"})
    assert cli("--root", root, "--words", wordlist(tmp_path, "# только комментарий", "", "   "))[0] == 2


def test_список_слов_берётся_из_переменной_окружения(tmp_path):
    root = snap(tmp_path, {"a.txt": "здесь " + HOSTNAME + "\n"})
    code, out, err = cli("--root", root, env_extra={"FLYARCHIVE_GATE_WORDS": wordlist(tmp_path, HOSTNAME)})
    assert code == 1 and "a.txt:1: word: " + HOSTNAME in out


def test_ключ_words_главнее_переменной_окружения(tmp_path):
    root = snap(tmp_path, {"a.txt": "здесь " + HOSTNAME + "\n"})
    other = tmp_path / "other.txt"
    other.write_text("совсем-другое-слово\n", encoding="utf-8")
    code, out, err = cli("--root", root, "--words", wordlist(tmp_path, HOSTNAME), env_extra={"FLYARCHIVE_GATE_WORDS": str(other)})
    assert code == 1


def test_домашний_знак_в_пути_к_списку_раскрывается(tmp_path):
    (tmp_path / "w.txt").write_text(HOSTNAME + "\n", encoding="utf-8")
    root = snap(tmp_path, {"a.txt": HOSTNAME + "\n"})
    env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "FLYARCHIVE_GATE_WORDS": "~/w.txt"}
    assert cli("--root", root, env_extra=env)[0] == 1


def test_не_каталог_в_root_даёт_код_2(tmp_path):
    assert cli("--root", str(tmp_path / "нет"), "--words", wordlist(tmp_path, *DUMMY))[0] == 2


@pytest.mark.parametrize("text", [ORG, ORG.upper(), ORG.lower(), "IVANOV-PC", "Ivanov-PC"])
def test_слово_ловится_в_любом_регистре(tmp_path, text):
    rep = one(tmp_path, "строка с " + text + " внутри", words=(ORG, HOSTNAME))
    (f,) = kind(rep, "word")
    assert f["line"] == 1 and f["text"].lower() in (ORG.lower(), HOSTNAME)


@pytest.mark.parametrize("text", ["под" + ORG.lower(), ORG.lower() + "ом", "x" + HOSTNAME + "x", "my" + HOSTNAME, HOSTNAME + "z"])
def test_слово_не_ловится_внутри_другого_слова(tmp_path, text):
    assert kind(one(tmp_path, "строка " + text + " конец", words=(ORG, HOSTNAME)), "word") == []


@pytest.mark.parametrize("text", [HOSTNAME + "2", HOSTNAME + "_backup", "(" + HOSTNAME + ")", HOSTNAME + ",", "«" + ORG + "»", HOSTNAME + "/x"])
def test_слово_ловится_рядом_со_знаками_цифрами_и_подчёркиванием(tmp_path, text):
    assert len(kind(one(tmp_path, "строка " + text + " конец", words=(ORG, HOSTNAME)), "word")) == 1


@pytest.mark.parametrize("text, hits", [("Пётр Сидоров", 1), ("ПЁТР   СИДОРОВ", 1), ("пётр\tсидоров", 1), ("ПётрСидоров", 0),
                                         ("Пётр\nСидоров", 0)])
def test_словосочетание_ловится_при_любых_пробелах_внутри_строки(tmp_path, text, hits):
    assert len(kind(one(tmp_path, text, words=(PERSON,)), "word")) == hits


def test_основа_со_звёздочкой_ловит_склонения(tmp_path):
    words = ("сидоров*",)
    for ok in ("Сидоров", "Сидорова", "Сидоровым", "СИДОРОВУ"):
        assert len(kind(one(tmp_path, "автор " + ok, words=words), "word")) == 1, ok
    assert kind(one(tmp_path, "автор Несидоров", words=words), "word") == []


def test_комментарии_и_пустые_строки_списка_не_слова(tmp_path):
    words = ("# закомментированное-слово", "", "   ", HOSTNAME + "   # ноутбук", "\t# ещё-одно")
    rep = one(tmp_path, "закомментированное-слово ещё-одно ноутбук " + HOSTNAME, words=words)
    assert [f["text"].lower() for f in kind(rep, "word")] == [HOSTNAME]


def test_находка_слова_называет_номер_строки_списка_а_не_само_слово_списка(tmp_path):
    words = ("# первая строка — комментарий", "", "альфа-слово", ORG, "основа-слова*", PERSON)
    text = "а " + ORG + "\nб основа-словаXY\nв Пётр Сидоров и " + ORG.upper() + "\nг альфа-слово\n"
    rep = one(tmp_path, text, words=words)
    assert sorted((f["line"], f["entry"]) for f in kind(rep, "word")) == [(1, 4), (2, 5), (3, 4), (3, 6), (4, 3)]
    assert rep.summary()["by_entry"] == {4: 2, 3: 1, 5: 1, 6: 1}


def test_сводка_по_записям_списка_в_json_и_в_тексте_без_слов(tmp_path):
    root = snap(tmp_path, {"a.txt": (HOSTNAME + "\n") * 3 + ORG + "\n"})
    words = wordlist(tmp_path, "# список", HOSTNAME, ORG)
    code, out, err = cli("--root", root, "--words", words, "--json")
    assert json.loads(out)["summary"]["by_entry"] == {"2": 3, "3": 1}
    code, out, err = cli("--root", root, "--words", words)
    last = [ln for ln in out.splitlines() if "записи списка" in ln]
    assert last and "2: 3" in last[0] and "3: 1" in last[0]
    assert HOSTNAME not in last[0] and ORG not in last[0]


@pytest.mark.parametrize("word, hit, miss", [("c++", "на c++ пишут", "на cxx пишут"), ("a.b", "это a.b тут", "это axb тут"),
                                              ("[x]", "метка [x] тут", "метка x тут"), ("a|b", "это a|b тут", "это a тут")])
def test_знаки_в_слове_списка_понимаются_буквально(tmp_path, word, hit, miss):
    assert len(kind(one(tmp_path, hit, words=(word,)), "word")) == 1
    assert kind(one(tmp_path, miss, words=(word,)), "word") == []


def test_список_с_меткой_порядка_байтов_и_переводами_строки_windows_читается(tmp_path):
    (tmp_path / "w.txt").write_bytes(b"\xef\xbb\xbf# \xd0\xba\xd0\xbe\xd0\xbc\xd0\xbc\xd0\xb5\xd0\xbd\xd1\x82\r\n" + HOSTNAME.encode() + b"\r\n")
    rep = G.scan(root=snap(tmp_path, {"a.txt": "здесь " + HOSTNAME + "\n"}), words_path=str(tmp_path / "w.txt"))
    assert len(kind(rep, "word")) == 1


def test_файл_с_меткой_порядка_байтов_и_ломаной_кодировкой_читается(tmp_path):
    rep = run(tmp_path, {"a.txt": b"\xef\xbb\xbf" + HOSTNAME.encode() + b"\n\xff\xfe\xfd " + PRIVATE_IP.encode() + b"\n"}, words=(HOSTNAME,))
    assert [(f["kind"], f["line"]) for f in rep.findings] == [("word", 1), ("ip", 2)]


def test_слово_в_имени_файла_ловится_на_строке_0(tmp_path):
    rep = run(tmp_path, {"docs/" + HOSTNAME + "-notes.md": "ничего\n"}, words=(HOSTNAME,))
    (f,) = kind(rep, "word")
    assert f["file"] == "docs/" + HOSTNAME + "-notes.md" and f["line"] == 0


def test_слово_кириллицей_в_имени_файла_ловится(tmp_path):
    rep = run(tmp_path, {"письма/" + ORG + ".txt": "ничего\n"}, words=(ORG,))
    assert [f["line"] for f in kind(rep, "word")] == [0]


# ── home_path ───────────────────────────────────────────────────
@pytest.mark.parametrize("flavor", sorted(HOME_PREFIXES))
def test_домашний_путь_ловится_во_всех_видах(tmp_path, flavor):
    line = "корень = '" + HOME_PREFIXES[flavor] + "ivanov" + SEP[flavor] + "archive'"
    (f,) = kind(one(tmp_path, "первая\n" + line + "\n"), "home_path")
    assert f["line"] == 2 and "ivanov" in f["text"]


@pytest.mark.parametrize("flavor", sorted(HOME_PREFIXES))
def test_домашний_путь_без_хвоста_в_конце_строки_ловится(tmp_path, flavor):
    assert len(kind(one(tmp_path, HOME_PREFIXES[flavor] + "ivanov"), "home_path")) == 1


@pytest.mark.parametrize("flavor", sorted(HOME_PREFIXES))
@pytest.mark.parametrize("name", ["x", "u", "a", "Z", "7"])
def test_однобуквенное_имя_в_домашнем_пути_считается_заглушкой(tmp_path, flavor, name):
    assert kind(one(tmp_path, HOME_PREFIXES[flavor] + name + SEP[flavor] + "archive"), "home_path") == []


def test_двухбуквенное_имя_в_домашнем_пути_уже_имя(tmp_path):
    assert len(kind(one(tmp_path, LINUX + "ab/archive"), "home_path")) == 1


@pytest.mark.parametrize("flavor", sorted(HOME_PREFIXES))
@pytest.mark.parametrize("placeholder", ["user", "username", "example", "you", "User", "<имя>", "<name>", "<user>", "$USER", "{user}"])
def test_заглушка_в_домашнем_пути_не_ловится(tmp_path, flavor, placeholder):
    line = HOME_PREFIXES[flavor] + placeholder + SEP[flavor] + "archive"
    assert kind(one(tmp_path, line), "home_path") == [], line


@pytest.mark.parametrize("line", ["~/archive", "$HOME/archive", "${HOME}/archive", "%USERPROFILE%" + BS + "archive", "~/.config/app"])
def test_домашние_знаки_без_имени_не_ловятся(tmp_path, line):
    assert kind(one(tmp_path, line), "home_path") == []


def test_каждая_заглушка_из_кода_ворот_не_ловится(tmp_path):
    spec = {"user", "username", "example", "you"}
    assert spec <= set(G.PLACEHOLDER_NAMES)
    for name in sorted(G.PLACEHOLDER_NAMES):
        for flavor, prefix in HOME_PREFIXES.items():
            line = prefix + name + SEP[flavor] + "x"
            assert kind(one(tmp_path, line), "home_path") == [], line


def test_настоящее_имя_рядом_с_заглушкой_всё_равно_ловится(tmp_path):
    line = LINUX + "user/a и " + LINUX + "ivanov/b"
    assert [f["text"] for f in kind(one(tmp_path, line), "home_path")] == [LINUX + "ivanov"]


def test_домашний_путь_в_середине_другого_пути_ловится(tmp_path):
    assert len(kind(one(tmp_path, "/mnt/data" + LINUX + "ivanov/x"), "home_path")) == 1


# ── email ───────────────────────────────────────────────────────
@pytest.mark.parametrize("addr", ["ivan" + "@mail.zelenogradgroup.test", "i.petrov+tag" + "@gmail.com", "x_y" + "@sub.domain.org",
                                  "noreply" + "@users.noreply.example.io"])
def test_почтовый_адрес_не_из_примеров_ловится(tmp_path, addr):
    (f,) = kind(one(tmp_path, "пишите на " + addr + ".\n"), "email")
    assert f["text"] == addr and f["line"] == 1


@pytest.mark.parametrize("addr", ["a@example.com", "a.b@example.org", "x@example.net", "x@example.test",
                                  "X@EXAMPLE.COM", "me@mail.example.com"])
def test_почтовый_адрес_из_доменов_примеров_не_ловится(tmp_path, addr):
    assert kind(one(tmp_path, "пишите на " + addr), "email") == []


ZONE_ONLY = "company.example"             # зона .example зарезервирована для ссылок-образцов, но не для почты: адрес в ней всё равно находка


@pytest.mark.parametrize("addr", ["x@" + ZONE_ONLY, "Petrov@" + ZONE_ONLY.upper(), "me@mail." + ZONE_ONLY, "a.b@sub." + ZONE_ONLY])
def test_почтовый_адрес_на_домене_не_из_примеров_в_зоне_example_ловится(tmp_path, addr):
    (f,) = kind(one(tmp_path, "пишите на " + addr + " сегодня"), "email")
    assert f["text"] == addr and f["line"] == 1


OTHER_DOMAIN = "company.info"             # обычный домен: для документации не зарезервирован


@pytest.mark.parametrize("url", ["https:" + "//" + OTHER_DOMAIN + "/", "https:" + "//portal." + OTHER_DOMAIN + "/upload", "http:" + "//a.b." + OTHER_DOMAIN + ":8080/x"])
def test_ссылка_на_домен_не_из_примеров_ловится(tmp_path, url):
    (f,) = kind(one(tmp_path, "см. " + url + " там"), "host")
    assert f["text"] == url


def test_обычного_домена_нет_среди_доменов_образцов():
    assert OTHER_DOMAIN not in G.EXAMPLE_DOMAINS and not any(OTHER_DOMAIN.endswith("." + d) for d in G.EXAMPLE_DOMAINS)


@pytest.mark.parametrize("text", ["@property", "pkg@1.2.3", "pkg@latest", "icon@2x.png", "user@host", "а @ б.в", "x @example.com"])
def test_не_адрес_почты_не_ловится(tmp_path, text):
    assert kind(one(tmp_path, "образец " + text), "email") == []


# ── ip ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("addr", [PRIVATE_IP, "10." + "1.2.3", "172." + "16.0.5", "100." + "64.1.2", "8.8." + "8.8", "192.0." + "20.5"])
def test_адрес_ip_вне_петли_и_документации_ловится(tmp_path, addr):
    (f,) = kind(one(tmp_path, "хост " + addr + ":8080 далее"), "ip")
    assert f["text"] == addr


@pytest.mark.parametrize("addr", ["127.0.0.1", "127.1.2.3", "127.255.255.254", "0.0.0.0", "192.0.2.5", "198.51.100.7", "203.0.113.9"])
def test_петля_нулевой_и_адреса_документации_не_ловятся(tmp_path, addr):
    assert kind(one(tmp_path, "хост " + addr + ":8080"), "ip") == []


@pytest.mark.parametrize("line", ["version 1.2.3.4", "v1.2.3.4", "версия 1.2.3.4 вышла", "Версии 1.2.3.4", "foo==1.2.3.4", "release 1.2.3.4",
                                  "ver. 1.2.3.4", "pkg>=1.2.3.4", "1.2.3.4.5", "build 1.2.3.4", "256.1.1.1", "1.2.3.400"])
def test_номер_версии_и_не_адрес_не_ловится(tmp_path, line):
    assert kind(one(tmp_path, line), "ip") == []


@pytest.mark.parametrize("line", ["см. 1.2." + "3.4 пожалуйста", "сервер на 1.2." + "3.4", "1.2." + "3.4", "ip=1.2." + "3.4"])
def test_спорный_случай_с_четырьмя_числами_без_слов_про_версию_ловится(tmp_path, line):
    assert len(kind(one(tmp_path, line), "ip")) == 1


# ── host ────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["ivanov-pc.tail1234." + "ts.net", "nas." + "local", "printer." + "lan", "gitlab." + "internal",
                                  "wiki.team." + "corp", "NAS." + "LOCAL"])
def test_имя_узла_частной_сети_ловится(tmp_path, name):
    (f,) = kind(one(tmp_path, "подключись к " + name + ":22 сейчас"), "host")
    assert f["text"].lower() == name.lower()


@pytest.mark.parametrize("line", ["settings.local.json", "threading.local()", "config.local.yml", "x.localhost", "my.internals", "a.lane",
                                  "corpus.corpse", "http:" + "//localhost:8080/api", "http:" + "//127.0.0.1:8080/", "http:" + "//<host>:8080/",
                                  "https:" + "//{host}/x", "http:" + "//$HOST/x", "https:" + "//example.com/page", "https:" + "//www.example.org/"])
def test_не_частный_узел_и_заглушки_не_ловятся(tmp_path, line):
    assert kind(one(tmp_path, line), "host") == []


@pytest.mark.parametrize("line", ["self.local", "this.lan", "cls.internal", "self.corp = 1", "Self.Local, other"])
def test_атрибут_объекта_не_имя_узла(tmp_path, line):
    assert kind(one(tmp_path, line), "host") == []


@pytest.mark.parametrize("url", ["https:" + "//evil.example/collect", "http:" + "//a.b.example", "https:" + "//x.invalid/y"])
def test_ссылка_на_зарезервированный_домен_не_ловится(tmp_path, url):
    assert kind(one(tmp_path, "см. " + url), "host") == []


def test_ссылка_не_захватывает_знак_конца_строки_в_тексте(tmp_path):
    (f,) = kind(one(tmp_path, 'url = "http:' + "//zelenogradgroup-box.test:8780" + BS + 'n"'), "host")
    assert f["text"] == "http:" + "//zelenogradgroup-box.test:8780"


def test_ссылка_на_узел_не_из_списка_разрешённых_ловится(tmp_path):
    url = "https:" + "//intranet.zelenogradgroup.test/page?q=1"
    (f,) = kind(one(tmp_path, "смотри " + url + " там"), "host")
    assert f["text"] == url


def test_ссылка_на_узел_из_списка_разрешённых_не_ловится(tmp_path):
    files = {"a.md": "см. https:" + "//docs.fake-docs.zz/guide и https:" + "//api.fake-docs.zz/v1\n",
             HOSTS: "# разрешённые узлы\ndocs.fake-docs.zz | официальная документация\n*.fake-docs.zz | её поддомены\n"}
    assert kind(run(tmp_path, files), "host") == []


def test_ссылка_на_узел_с_другим_поддоменом_не_разрешена_без_звёздочки(tmp_path):
    files = {"a.md": "см. https:" + "//api.fake-docs.zz/v1\n", HOSTS: "fake-docs.zz | корень без поддоменов\n"}
    assert len(kind(run(tmp_path, files), "host")) == 1


def test_узел_в_списке_разрешённых_без_причины_не_принимается(tmp_path):
    files = {"a.md": "см. https:" + "//docs.fake-docs.zz/x\n", HOSTS: "docs.fake-docs.zz\nother.fake-docs.zz |   \n"}
    rep = run(tmp_path, files)
    assert len(kind(rep, "host")) == 1
    assert [(f["file"], f["line"]) for f in kind(rep, "allowlist")] == [(HOSTS, 1), (HOSTS, 2)]


def test_ссылка_на_адрес_ip_даёт_одну_находку_ip(tmp_path):
    rep = one(tmp_path, "curl http:" + "//" + PRIVATE_IP + ":8080/x")
    assert kinds(rep) == ["ip"]


def test_ссылка_на_узел_частной_сети_даёт_одну_находку(tmp_path):
    rep = one(tmp_path, "curl http:" + "//nas." + "local:8080/x")
    assert [f["kind"] for f in rep.findings] == ["host"]


# ── secret ──────────────────────────────────────────────────────
def secret_samples():
    return {
        "токен проекта": "ba" + "_" + token_like(43),
        "ключ sk": "sk" + "-" + token_like(40),
        "ключ ghp": "ghp" + "_" + token_like(36, string.ascii_letters + string.digits),
        "ключ aws": "AKIA" + "Q7ZP2XK9M4TW8NBV",
        "случайная base62": token_like(32, string.ascii_letters + string.digits, seed=2),
        "случайная base64": token_like(44, string.ascii_letters + string.digits + "+/", seed=3) + "==",
        "хеш sha256": hashlib.sha256(b"fixture").hexdigest(),
        "хеш md5": hashlib.md5(b"fixture").hexdigest(),
    }


@pytest.mark.parametrize("label", sorted(secret_samples()))
def test_значение_похожее_на_ключ_ловится_и_не_выводится_целиком(tmp_path, label):
    value = secret_samples()[label]
    rep = one(tmp_path, "первая\nключ = \"" + value + "\"\n")
    (f,) = kind(rep, "secret")
    assert f["line"] == 2 and len(f["text"]) <= 80
    assert value not in json.dumps(rep.findings, ensure_ascii=False)          # значение не утекает в вывод


@pytest.mark.parametrize("header", ["RSA PRIVATE KEY", "PRIVATE KEY", "OPENSSH PRIVATE KEY", "EC PRIVATE KEY"])
def test_заголовок_закрытого_ключа_ловится(tmp_path, header):
    (f,) = kind(one(tmp_path, "-----" + "BEGIN " + header + "-----\nабв\n"), "secret")
    assert f["line"] == 1


@pytest.mark.parametrize("value", [
    "ba" + "_" + "x" * 43, "0" * 64, "0123456789abcdef" * 4, "deadbeef" * 8, "a1" * 32, "f" * 32, "x" * 40, "-" * 60, "=" * 40,
    "this_is_a_long_snake_case_identifier_name_for_tests", "docs/very-long-directory-name/another-long-directory-name/file",
    "ThisIsAVeryLongClassNameForSomethingImportantHere", "123e4567-e89b-12d3-a456-426614174000", "sk" + "-abc", "ghp" + "_" + "x" * 36,
    "AKIA" + "X" * 3, "a3f9c1d", "Authorization: Bearer <token>", "ba" + "_session", token_like(31, string.ascii_letters + string.digits, seed=5),
    "abcdefghijklmnopqrstuvwxyz0123456789ABCDEF", "AKIAIOSFODNN7" + "EXAMPLE", "ba" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v",
    "Environment=SERVICE_GATEWAY_BIND=100", "Environment=SERVICE_GATEWAY_BIND=100.64." + "0.5:8780"])
def test_образец_хеш_идентификатор_и_короткое_не_ловится(tmp_path, value):
    assert kind(one(tmp_path, "значение = " + value), "secret") == [], value


@pytest.mark.parametrize("prefix", ["TOKEN=", "export KEY=", "api_key: ", "Environment=SECRET="])
def test_значение_после_знака_равно_ловится(tmp_path, prefix):
    value = token_like(40, seed=11)
    (f,) = kind(one(tmp_path, prefix + value), "secret")
    assert value not in f["text"]


def test_две_находки_секрета_в_строке_не_двоятся_с_префиксом(tmp_path):
    value = "ba" + "_" + token_like(43, seed=9)
    assert len(kind(one(tmp_path, "export TOKEN=" + value), "secret")) == 1


# ── working_name ────────────────────────────────────────────────
def test_без_списка_рабочих_имён_вид_working_name_не_срабатывает(tmp_path):
    rep = run(tmp_path, {"a.txt": WORK + "\n", WORK + ".txt": "x\n"}, names=())
    assert kind(rep, "working_name") == []


def test_список_имён_читает_несколько_имён_комментарии_и_регистр(tmp_path, monkeypatch):
    names = namelist(tmp_path, "# первая строка — комментарий", "", "firstname", "SecondName  # пояснение", "  ")
    regex = G.load_names(names)
    assert [m.group(0) for m in regex.finditer("FirstName, secondname и thirdname")] == ["FirstName", "secondname"]
    monkeypatch.delenv(G.NAMES_ENV, raising=False)            # у запускающего переменная может быть задана: тест про её отсутствие
    assert G.load_names(None) is None and G.resolve_names_path(None) is None
    monkeypatch.setenv(G.NAMES_ENV, names)
    assert G.resolve_names_path(None) == names and G.resolve_names_path("другой.txt") == "другой.txt", "ключ главнее переменной"


def test_список_имён_берётся_из_ключа_и_из_переменной_окружения(tmp_path):
    root = snap(tmp_path, {"a.txt": "строка " + WORK + "\n"})
    by_key = cli("--root", root, "--words", wordlist(tmp_path, *DUMMY), "--names", namelist(tmp_path, WORK))
    by_env = cli("--root", root, "--words", wordlist(tmp_path, *DUMMY), env_extra={"FLYARCHIVE_GATE_NAMES": namelist(tmp_path, WORK)})
    plain = cli("--root", root, "--words", wordlist(tmp_path, *DUMMY))
    assert by_key[0] == by_env[0] == 1 and "a.txt:1: working_name: " + WORK in by_key[1] and "a.txt:1: working_name: " + WORK in by_env[1]
    assert plain[0] == 0, "без списка имён находок нет"


def test_список_имён_который_не_открывается_или_пуст_даёт_код_2(tmp_path):
    root = snap(tmp_path, {"a.txt": "чисто\n"})
    assert cli("--root", root, "--words", wordlist(tmp_path, *DUMMY), "--names", str(tmp_path / "нет-такого.txt"))[0] == 2
    assert cli("--root", root, "--words", wordlist(tmp_path, *DUMMY), "--names", namelist(tmp_path, "# только комментарий", ""))[0] == 2



@pytest.mark.parametrize("text", [WORK, WORK.upper(), WORK.capitalize(), "~/" + WORK + "/index", WORK.upper() + "_HOME", "dsh-" + WORK + ".yml",
                                  "/api/" + WORK + ".search"])
def test_рабочее_имя_ловится_в_любом_регистре_и_внутри_слов(tmp_path, text):
    (f,) = kind(one(tmp_path, "а\nб\nстрока " + text + " конец"), "working_name")
    assert f["line"] == 3 and f["text"].lower() == WORK


def test_имя_проекта_не_считается_рабочим_именем(tmp_path):
    assert kind(one(tmp_path, "flyarchive FlyArchive FLYARCHIVE_HOME"), "working_name") == []


def test_рабочее_имя_в_имени_файла_ловится_на_строке_0(tmp_path):
    rep = run(tmp_path, {"tools/" + WORK: "#!/bin/sh\n"})
    assert [(f["file"], f["line"]) for f in kind(rep, "working_name")] == [("tools/" + WORK, 0)]


# ── tracked_private ─────────────────────────────────────────────
@pytest.mark.parametrize("path, text", [
    ("secrets/tokens.json", "secrets"), ("a/b/logs/x.log", "logs"), ("reports/r.txt", "reports"), ("index/data", "index"),
    ("corpus/c.txt", "corpus"), ("cache/z", "cache"), ("tests/__pycache__/m.txt", "__pycache__")])
def test_файл_в_каталоге_секретов_журналов_отчётов_индекса_корпуса_кэша_ловится(tmp_path, path, text):
    (f,) = kind(run(tmp_path, {path: "ничего\n"}), "tracked_private")
    assert f["file"] == path and f["text"] == text


@pytest.mark.parametrize("path", [".env", ".env.local", "keys/server.pem", "deploy.key", "api-key", "home/id_rsa", "id_ed25519"])
def test_файл_ключей_и_окружения_ловится(tmp_path, path):
    assert len(kind(run(tmp_path, {path: "ничего\n"}), "tracked_private")) == 1


@pytest.mark.parametrize("path", ["docs/logs.md", "tools/index.py", "tests/cache_test.py", "src/indexer/a.py", "reports.md", ".env.example",
                                  "tools/secretsmith.py"])
def test_похожее_имя_не_каталог_и_не_файл_ключа_не_ловится(tmp_path, path):
    assert kind(run(tmp_path, {path: "ничего\n"}), "tracked_private") == []


def test_gitignore_без_каталога_кэша_даёт_находку(tmp_path):
    rep = run(tmp_path, {".gitignore": "secrets/\nlogs/\nreports/\nindex/\ncorpus/\n__pycache__/\n"})
    assert [(f["file"], f["text"]) for f in kind(rep, "tracked_private")] == [(".gitignore", "cache/")]


def test_gitignore_называет_каждый_недостающий_каталог(tmp_path):
    rep = run(tmp_path, {".gitignore": "*.pyc\n"})
    assert {f["text"] for f in kind(rep, "tracked_private")} >= {"secrets/", "logs/", "reports/", "index/", "corpus/", "cache/"}


@pytest.mark.parametrize("form", ["cache/", "/cache/", "cache", "/cache", "**/cache/", "cache/**", "  cache/  "])
def test_gitignore_принимает_обычные_записи_каталога(tmp_path, form):
    text = "secrets/\nlogs/\nreports/\nindex/\ncorpus/\n__pycache__/\n" + form + "\n"
    assert kind(run(tmp_path, {".gitignore": text}), "tracked_private") == []


def test_закомментированная_запись_в_gitignore_не_считается(tmp_path):
    text = "secrets/\nlogs/\nreports/\nindex/\ncorpus/\n__pycache__/\n# cache/\n"
    assert [f["text"] for f in kind(run(tmp_path, {".gitignore": text}), "tracked_private")] == ["cache/"]


def test_снимок_без_gitignore_даёт_находку(tmp_path):
    rep = run(tmp_path, {"README.md": "чисто\n"}, gitignore=False)
    assert [f["file"] for f in kind(rep, "tracked_private")] == [".gitignore"]


def test_каталог_git_в_снимке_ловится_и_не_читается(tmp_path):
    rep = run(tmp_path, {".git/config": "url = " + PRIVATE_IP + "\n", "a.txt": "чисто\n"})
    assert [(f["file"], f["text"]) for f in rep.findings] == [(".git", ".git")] and rep.findings[0]["kind"] == "tracked_private"


# ── исключения ──────────────────────────────────────────────────
def test_исключение_с_причиной_снимает_только_свой_файл_и_текст(tmp_path):
    files = {"a.py": "x = '" + PRIVATE_IP + "'\n", "b.py": "x = '" + PRIVATE_IP + "'\n", "c.py": "x = '10." + "1.2.3'\n",
             ALLOW: "a.py | ip | " + PRIVATE_IP + " | адрес из примера в документации\n"}
    rep = run(tmp_path, files)
    assert sorted((f["file"], f["text"]) for f in kind(rep, "ip") if f["file"] != ALLOW) == [("b.py", PRIVATE_IP), ("c.py", "10." + "1.2.3")]
    assert rep.suppressed == 1


def test_исключение_без_причины_не_принимается_и_называется(tmp_path):
    files = {"a.py": "x = '" + PRIVATE_IP + "'\n", "b.py": "x = '" + PRIVATE_IP + "'\n",
             ALLOW: "# образцы\na.py | ip | " + PRIVATE_IP + "\nb.py | ip | " + PRIVATE_IP + " |   \n"}
    rep = run(tmp_path, files)
    assert {f["file"] for f in kind(rep, "ip")} >= {"a.py", "b.py"}
    assert [(f["file"], f["line"]) for f in kind(rep, "allowlist")] == [(ALLOW, 2), (ALLOW, 3)]
    assert rep.suppressed == 0


def test_исключение_по_образцу_пути_со_звёздочкой(tmp_path):
    files = {"tests/a.py": PRIVATE_IP, "tests/sub/b.py": PRIVATE_IP, "tools/c.py": PRIVATE_IP,
             ALLOW: "tests/*.py | ip | " + PRIVATE_IP + " | образец в тестах\n"}
    got = {f["file"] for f in kind(run(tmp_path, files), "ip") if f["file"] != ALLOW}
    assert got == {"tools/c.py"}


def test_исключение_должно_совпасть_с_текстом_целиком(tmp_path):
    files = {"a.py": "х " + PRIVATE_IP + "0 и " + PRIVATE_IP + "\n", ALLOW: "a.py | ip | " + PRIVATE_IP + " | образец\n"}
    got = [f["text"] for f in kind(run(tmp_path, files), "ip") if f["file"] == "a.py"]
    assert got == [PRIVATE_IP + "0"]


def test_исключение_другого_вида_не_снимает_находку(tmp_path):
    files = {"a.py": PRIVATE_IP, ALLOW: "a.py | email | " + PRIVATE_IP + " | не тот вид\n"}
    assert [f["file"] for f in kind(run(tmp_path, files), "ip")].count("a.py") == 1


def test_исключение_с_неизвестным_видом_не_принимается(tmp_path):
    files = {"a.py": PRIVATE_IP, ALLOW: "a.py | ipv9 | " + PRIVATE_IP + " | причина\n"}
    rep = run(tmp_path, files)
    assert [f["line"] for f in kind(rep, "allowlist")] == [1]


def test_строка_исключения_не_из_четырёх_частей_не_принимается(tmp_path):
    files = {"a.py": PRIVATE_IP, ALLOW: "a.py | ip | причина\njust text\n"}
    assert [f["line"] for f in kind(run(tmp_path, files), "allowlist")] == [1, 2]


def test_вид_не_только_ip_снимается_целиком_для_файла_звёздочкой(tmp_path):
    files = {"a.py": PRIVATE_IP + " и 10." + "1.2.3\n", ALLOW: "a.py | ip | * | в файле только образцы адресов\n"}
    assert [f for f in kind(run(tmp_path, files), "ip") if f["file"] == "a.py"] == []


@pytest.mark.parametrize("text", ["*", ""])
def test_home_path_целиком_для_файла_исключить_нельзя(tmp_path, text):
    files = {"a.py": LINUX + "ivanov/x", ALLOW: "a.py | home_path | " + text + " | причина\n"}
    rep = run(tmp_path, files)
    assert len(kind(rep, "home_path")) >= 1 and [f for f in rep.findings if f["file"] == "a.py"]
    assert [f["line"] for f in kind(rep, "allowlist")] == [1]


def test_word_без_текста_исключить_нельзя(tmp_path):
    files = {"a.py": HOSTNAME, ALLOW: "a.py | word |  | причина\n"}
    rep = run(tmp_path, files, words=(HOSTNAME,))
    assert len(kind(rep, "word")) >= 1 and [f["line"] for f in kind(rep, "allowlist")] == [1]


def test_word_звёздочкой_с_причиной_снимает_слова_в_этом_файле_целиком_а_в_других_файлах_нет(tmp_path):
    files = {"LICENSE": "один " + HOSTNAME + "\nдругой " + ORG + "\n", "a.py": "один " + HOSTNAME, ALLOW: "LICENSE | word | * | дословный текст лицензии: слова не меняются\n"}
    rep = run(tmp_path, files, words=(HOSTNAME, ORG))
    assert [(f["file"], f["line"]) for f in kind(rep, "word")] == [("a.py", 1)] and kind(rep, "allowlist") == [] and rep.suppressed == 2


def test_word_звёздочкой_без_причины_не_принимается_и_слова_остаются_находками(tmp_path):
    files = {"LICENSE": "один " + HOSTNAME, ALLOW: "LICENSE | word | *\nLICENSE | word | * |   \n"}
    rep = run(tmp_path, files, words=(HOSTNAME,))
    assert [(f["file"], f["line"]) for f in kind(rep, "word")] == [("LICENSE", 1)] and [f["line"] for f in kind(rep, "allowlist")] == [1, 2]


def test_word_исключается_только_конкретным_текстом(tmp_path):
    files = {"a.py": "один " + HOSTNAME + "\nдругой " + HOSTNAME.upper() + "\n",
             ALLOW: "a.py | word | " + HOSTNAME + " | название узла в примере\n"}
    rep = run(tmp_path, files, words=(HOSTNAME,))
    assert [(f["file"], f["line"]) for f in kind(rep, "word") if f["file"] == "a.py"] == [("a.py", 2)]


def test_home_path_исключается_конкретным_текстом(tmp_path):
    files = {"a.py": LINUX + "ivanov/x и " + LINUX + "petrov/y", ALLOW: "a.py | home_path | " + LINUX + "ivanov | образец\n"}
    assert [f["text"] for f in kind(run(tmp_path, files), "home_path") if f["file"] == "a.py"] == [LINUX + "petrov"]


def test_слово_в_самом_файле_исключений_покрывается_его_же_строкой(tmp_path):
    files = {"a.py": "один " + HOSTNAME, ALLOW: "a.py | word | " + HOSTNAME + " | название узла в примере\n"}
    rep = run(tmp_path, files, words=(HOSTNAME,))
    assert kind(rep, "word") == [] and rep.suppressed == 1


def test_слово_в_чужой_строке_файла_исключений_не_покрывается(tmp_path):
    files = {"a.py": "один " + HOSTNAME, ALLOW: "a.py | word | " + HOSTNAME + " | причина\n# заметка про " + HOSTNAME + "\n"}
    rep = run(tmp_path, files, words=(HOSTNAME,))
    assert [(f["file"], f["line"]) for f in kind(rep, "word")] == [(ALLOW, 2)]


def test_исключение_снимает_находку_секрета_по_выводимому_тексту(tmp_path):
    value = hashlib.sha256(b"image").hexdigest()
    files = {"tests/fake.py": "DIGEST = 'sha256:" + value + "'\n"}
    (f,) = kind(run(tmp_path, files), "secret")
    files[ALLOW] = "tests/fake.py | secret | " + f["text"] + " | дайджест образа в образце\n"
    rep = run(tmp_path, files)
    assert kind(rep, "secret") == [] and rep.suppressed == 1


def test_исключение_снимает_working_name_и_tracked_private(tmp_path):
    files = {"tools/" + WORK: "#!/bin/sh\n", "logs/x.txt": "ничего\n",
             ALLOW: "tools/* | working_name | * | образец исключения для рабочего имени\nlogs/x.txt | tracked_private | logs | образец\n"}
    assert run(tmp_path, files).findings == []


# ── двоичные и большие файлы ────────────────────────────────────
def test_двоичный_файл_со_строкой_внутри_ловится(tmp_path):
    data = b"\x00\x01\x02\x89PNG\x00" + ("запись про " + ORG + " внутри").encode("utf-8") + b"\x00\xff\xfe"
    rep = run(tmp_path, {"img/blob.bin": data}, words=(ORG,))
    (f,) = kind(rep, "word")
    assert f["file"] == "img/blob.bin" and f["line"] == 0 and f["offset"] == 8          # начало печатной строки


@pytest.mark.parametrize("line, want", [("host " + PRIVATE_IP + " port", "ip"), ("see " + WORK + " here", "working_name"),
                                         ("path " + LINUX + "ivanov/x ok", "home_path")])
def test_двоичный_файл_ловится_и_по_другим_видам(tmp_path, line, want):
    data = b"\x00\x00\x01" + line.encode("utf-8") + b"\x00\x02\x03"
    assert [f["kind"] for f in run(tmp_path, {"a.bin": data}).findings] == [want]


def test_короткие_печатные_куски_в_двоичном_файле_не_читаются(tmp_path):
    data = b"\x00" + PRIVATE_IP[:5].encode() + b"\x00ab\x00" + b"\x01\x02" * 50
    assert run(tmp_path, {"a.bin": data}).findings == []


def test_ровно_шесть_знаков_в_двоичном_файле_читаются_а_пять_нет(tmp_path):
    files = {"six.bin": b"\x00\x01zzqzzq\x00\x02", "five.bin": b"\x00\x01wwvww\x00\x02"}
    rep = run(tmp_path, files, words=("zzqzzq", "wwvww"))
    assert [(f["file"], f["kind"]) for f in rep.findings] == [("six.bin", "word")]


def test_двоичный_файл_ловится_по_имени(tmp_path):
    rep = run(tmp_path, {"img/" + WORK + ".png": b"\x89PNG\x00\x00\x01\x02"})
    assert [(f["kind"], f["line"]) for f in rep.findings] == [("working_name", 0)]


def test_двоичный_файл_считается_проверенным_а_не_пропущенным(tmp_path):
    rep = run(tmp_path, {"a.bin": b"\x00\x01\x02", "b.txt": "чисто"})
    assert rep.files_checked == 3                      # и .gitignore


def test_большой_файл_не_читается_целиком_и_называется(tmp_path):
    head = "первая строка " + HOSTNAME + "\n"
    tail = "\nпоследняя строка " + HOSTNAME + "\n"
    data = head.encode("utf-8") + b"." * G.MAX_READ_BYTES + tail.encode("utf-8")
    rep = run(tmp_path, {"docs/big.txt": data}, words=(HOSTNAME,))
    assert [(f["file"], f["line"]) for f in kind(rep, "word")] == [("docs/big.txt", 1)]      # хвост за пределом не прочитан
    (big,) = kind(rep, "too_large")
    assert big["file"] == "docs/big.txt" and big["line"] == 0


def test_из_большого_файла_просят_не_больше_предела_плюс_один_байт(tmp_path, monkeypatch):
    root = snap(tmp_path, {"big.bin": b"." * (G.MAX_READ_BYTES + 5000)})
    words = wordlist(tmp_path, *DUMMY)
    reads, real_open = [], open

    class Spy:
        def __init__(self, f):
            self.f = f

        def read(self, n=-1):
            reads.append(n)
            return self.f.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

    monkeypatch.setattr(G, "open", lambda p, *a, **k: Spy(real_open(p, *a, **k)) if str(p).endswith("big.bin") else real_open(p, *a, **k),
                        raising=False)
    G.scan(root=root, words_path=words)
    assert reads and all(0 < n <= G.MAX_READ_BYTES + 1 for n in reads), reads


def test_файл_ровно_в_предел_читается_целиком(tmp_path):
    body = b"." * (G.MAX_READ_BYTES - 1 - len(HOSTNAME)) + b"\n" + HOSTNAME.encode()
    assert len(body) == G.MAX_READ_BYTES
    rep = run(tmp_path, {"a.txt": body}, words=(HOSTNAME,))
    assert kind(rep, "too_large") == [] and len(kind(rep, "word")) == 1


def test_предел_чтения_разумный():
    assert 256 * 1024 <= G.MAX_READ_BYTES <= 16 * 1024 * 1024


def test_слишком_большой_файл_снимается_исключением_с_причиной(tmp_path):
    data = b"." * (G.MAX_READ_BYTES + 10)
    files = {"docs/big.txt": data, ALLOW: "docs/big.txt | too_large | * | сводная таблица, просмотрена руками\n"}
    assert run(tmp_path, files).findings == []


# ── режим git ───────────────────────────────────────────────────
def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo, env=G.clean_git_env(), check=True, capture_output=True)


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="нет git")


@needs_git
def test_без_root_проверяются_только_отслеживаемые_git_файлы(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (repo / "tracked.txt").write_text("строка " + HOSTNAME + "\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("строка " + HOSTNAME + "\n", encoding="utf-8")
    (repo / "файл-с-кириллицей.txt").write_text("строка " + HOSTNAME + "\n", encoding="utf-8")
    git(repo, "add", ".gitignore", "tracked.txt", "файл-с-кириллицей.txt")
    rep = G.scan(words_path=wordlist(tmp_path, HOSTNAME), repo=str(repo))
    assert sorted(f["file"] for f in kind(rep, "word")) == ["tracked.txt", "файл-с-кириллицей.txt"]


@needs_git
def test_отслеживаемый_но_удалённый_с_диска_файл_не_роняет_ворота(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (repo / "gone.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", ".gitignore", "gone.txt")
    (repo / "gone.txt").unlink()
    assert G.scan(words_path=wordlist(tmp_path, *DUMMY), repo=str(repo)).findings == []


@needs_git
def test_скрипт_в_репозитории_проверяет_свой_репозиторий(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    git(repo, "init", "-q")
    shutil.copy(GATE, repo / "tests" / "publication_gate.py")
    (repo / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (repo / "a.txt").write_text("адрес " + PRIVATE_IP + "\n", encoding="utf-8")
    git(repo, "add", "-A")
    code, out, err = cli("--words", wordlist(tmp_path, *DUMMY), script=str(repo / "tests" / "publication_gate.py"))
    assert code == 1 and "a.txt:1: ip: " + PRIVATE_IP in out


def test_не_репозиторий_без_root_даёт_ошибку_ворот_и_код_2(tmp_path):
    plain = tmp_path / "plain"
    (plain / "tests").mkdir(parents=True)
    shutil.copy(GATE, plain / "tests" / "publication_gate.py")
    with pytest.raises(G.GateError):
        G.scan(words_path=wordlist(tmp_path, *DUMMY), repo=str(plain))
    code, out, err = cli("--words", wordlist(tmp_path, *DUMMY), script=str(plain / "tests" / "publication_gate.py"))
    assert code == 2 and err.strip() and "чисто" not in out


def test_нет_программы_git_даёт_код_2(tmp_path):
    plain = tmp_path / "plain"
    (plain / "tests").mkdir(parents=True)
    shutil.copy(GATE, plain / "tests" / "publication_gate.py")
    empty = tmp_path / "empty-path"
    empty.mkdir()
    code, out, err = cli("--words", wordlist(tmp_path, *DUMMY), script=str(plain / "tests" / "publication_gate.py"), path=str(empty))
    assert code == 2 and "git" in err


# ── ворота проверяют и свои файлы ───────────────────────────────
def test_собственные_файлы_ворот_чисты_по_всем_видам_кроме_слов(tmp_path):
    """Слова списка тут не проверить (образцы тестов — тоже слова); их ловит прогон по репозиторию со списком владельца."""
    own = {}
    for name in ("publication_gate.py", "test_publication_gate.py", "publication_allow.txt", "publication_hosts.txt"):
        path = os.path.join(HERE, name)
        if os.path.exists(path):
            with open(path, "rb") as f:
                own["tests/" + name] = f.read()
    assert "tests/publication_gate.py" in own and "tests/test_publication_gate.py" in own
    rep = run(tmp_path, own, names=())
    assert rep.findings == [], [(f["file"], f["line"], f["kind"]) for f in rep.findings]


# ── вывод ───────────────────────────────────────────────────────
def test_чистый_прогон_пишет_итог_и_не_печатает_слов_списка(tmp_path):
    root = snap(tmp_path, {"a.txt": "чисто\n"})
    code, out, err = cli("--root", root, "--words", wordlist(tmp_path, HOSTNAME, ORG, PERSON))
    assert code == 0 and "Ворота пройдены" in out
    assert HOSTNAME not in out + err and ORG not in out + err and PERSON not in out + err


def test_итог_при_находках_называет_числа_по_видам(tmp_path):
    root = snap(tmp_path, {"a.txt": WORK + " " + PRIVATE_IP})
    code, out, err = cli("--root", root, "--words", wordlist(tmp_path, *DUMMY), "--names", namelist(tmp_path, WORK))
    assert code == 1 and "Итого находок: 2" in out
    assert "  ip: 1" in out and "  working_name: 1" in out
    assert out.count("a.txt:1:") == 2


def test_сводка_по_каталогам_кладёт_файлы_корня_под_точку(tmp_path):
    rep = run(tmp_path, {"README.md": WORK, "tools/a.py": WORK, "tools/sub/b.py": WORK})
    assert rep.summary()["by_dir"] == {".": 1, "tools": 2}

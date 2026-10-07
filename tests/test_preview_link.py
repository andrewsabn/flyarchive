"""Просмотр файла: проверка ссылки до открытия файла и ключа --member (FR-76).

Ссылка — область и путь. Что ведёт за область (`..`, абсолютный путь, ссылка, чужая область), отклоняется ещё до того, как
файл открыт и запущен рабочий процесс.
"""
import os

import pytest

import corpus_path
import preview as P
from test_preview_kit import BATCH, env, worker  # noqa: F401  — общая обвязка


class Touched(BaseException):
    """Не Exception: общий перехват в просмотре её не проглотит."""


@pytest.fixture
def untouched(monkeypatch):
    """Файл не открывается и рабочий процесс не запускается: любая такая попытка записывается и обрывает тест."""
    calls = []

    def deny(name):
        def trap(*a, **k):
            calls.append(name)
            raise Touched(name)
        return trap

    monkeypatch.setattr(P, "_open_input", deny("открытие файла"))
    monkeypatch.setattr(P, "_run_worker", deny("рабочий процесс"))
    monkeypatch.setattr(P, "bwrap_path", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(P, "worker_python", lambda home=None, **k: P.WorkerPython(P.PYTHON, None, None))
    return calls


def refused(env, area, path, codes):
    with pytest.raises(P.PreviewError) as e:
        P.show(env.home, area, path)
    assert e.value.message.code in codes, e.value.message.code
    return e.value.message


OUTSIDE = ("review.bad_path", "review.path_needed", "review.no_file")
OUTSIDE_CORPUS = ("review.doc_path_outside", "review.doc_path_needed", "review.no_doc")
PREFIX_OF = {"queue": "очередь", "quarantine": "карантин", "corpus": "входящие"}


@pytest.mark.parametrize("area,folder", [("queue", "очередь"), ("quarantine", "карантин")])
def test_путь_не_из_области_отклонён_файл_не_открыт_рабочий_процесс_не_запущен(env, untouched, area, folder):
    for secret in (os.path.join(env.home, "секрет.txt"), os.path.join(env.home, folder, "секрет.txt")):
        with open(secret, "wb") as f:
            f.write(b"secret")
    other = "карантин" if folder == "очередь" else "очередь"
    inside = f"{folder}/{BATCH}/а.txt"
    env.put(area, "а.txt", b"text")
    env.put(area, "б.txt", b"text", batch="20261004-130000")
    paths = [
        f"{folder}/{BATCH}/../../секрет.txt",         # выход за каталог пачки и области
        f"../{folder}/{BATCH}/а.txt",
        f"{folder}/{BATCH}/../20261004-130000/б.txt",  # `..` внутри области: файл есть, но `..` не нужен никогда
        f"{folder}/{BATCH}/./а.txt",
        f"{folder}//{BATCH}/а.txt",
        f"{folder}/{BATCH}/",
        f"{folder}/{BATCH}",
        folder,
        "/etc/passwd",
        "/" + inside,                                  # абсолютный путь с правильным хвостом
        os.path.join(env.home, folder, BATCH, "а.txt"),
        f"{other}/{BATCH}/а.txt",                      # чужая область
        f"corpus/входящие/{BATCH}/а.txt",
        f"{folder}\\{BATCH}\\..\\..\\секрет.txt",      # путь с обратными слэшами читается как с прямыми: `..` всё равно `..`
        inside + "\x00.png",
        "",
        None,
        5,
        [inside],
    ]
    for path in paths:
        refused(env, area, path, OUTSIDE)
    assert untouched == []


def test_путь_корпуса_с_выходом_за_корпус_отклонён(env, untouched):
    with open(os.path.join(env.home, "секрет.txt"), "wb") as f:
        f.write(b"secret")
    env.put("corpus", "а.txt", b"text")
    env.put("corpus", "б.txt", b"text", batch="20261004-130000")
    for path in ("../секрет.txt", f"входящие/{BATCH}/../../../секрет.txt", f"входящие/{BATCH}/../20261004-130000/б.txt",
                 f"входящие/{BATCH}/./а.txt", f"входящие//{BATCH}/а.txt", "/etc/passwd", f"/входящие/{BATCH}/а.txt",
                 os.path.join(env.home, "corpus", "входящие", BATCH, "а.txt"), f"входящие\\{BATCH}\\..\\..\\..\\секрет.txt",
                 f"входящие/{BATCH}/а.txt\x00", "", None, 7, f"входящие/{BATCH}/нет-такого.txt", f"очередь/{BATCH}/а.txt"):
        refused(env, "corpus", path, OUTSIDE_CORPUS)
    assert untouched == []


def test_область_названа_неверно_отказ(env, untouched):
    env.put("queue", "а.txt", b"text")
    for area in ("queues", "", None, "очередь", "QUEUE", 3):
        m = refused(env, area, f"очередь/{BATCH}/а.txt", ("preview.bad_area",))
        assert m.args == {"areas": "queue, quarantine, corpus"}
    assert untouched == []


@pytest.mark.parametrize("area", ["queue", "quarantine", "corpus"])
def test_ссылка_вместо_файла_отклонена_файл_не_открыт(env, untouched, area):
    secret = env.tmp / "снаружи.txt"
    secret.write_bytes(b"outside")
    real = env.put(area, "настоящий.txt", b"inside")
    folder = os.path.dirname(env.full(area, "настоящий.txt"))
    os.symlink(secret, os.path.join(folder, "на-чужой.txt"))                       # ссылка на файл вне архива
    os.symlink(os.path.join(folder, "настоящий.txt"), os.path.join(folder, "на-свой.txt"))   # ссылка на файл внутри области
    os.symlink(os.path.join(folder, "нет"), os.path.join(folder, "висячая.txt"))
    prefix = real.rsplit("/", 1)[0]
    for name in ("на-чужой.txt", "на-свой.txt", "висячая.txt"):
        refused(env, area, f"{prefix}/{name}", OUTSIDE + OUTSIDE_CORPUS)
    assert untouched == []


@pytest.mark.parametrize("area", ["queue", "quarantine", "corpus"])
def test_каталог_пачки_ссылкой_отклонён_даже_если_ведёт_внутрь_области(env, untouched, area):
    env.put(area, "а.txt", b"text", batch="20261004-130000")
    batch_dir = os.path.dirname(env.full(area, "а.txt", batch="20261004-130000"))
    os.symlink(batch_dir, os.path.join(os.path.dirname(batch_dir), BATCH))
    folder = PREFIX_OF[area]
    refused(env, area, f"{folder}/{BATCH}/а.txt", OUTSIDE + OUTSIDE_CORPUS)
    assert untouched == []


def test_каталог_пачки_ссылкой_наружу_отклонён(env, untouched):
    outside = env.tmp / "снаружи"
    outside.mkdir()
    (outside / "а.txt").write_bytes(b"outside")
    os.symlink(outside, os.path.join(env.home, "очередь", BATCH))
    refused(env, "queue", f"очередь/{BATCH}/а.txt", OUTSIDE)
    assert untouched == []


def test_каталог_и_канал_вместо_файла_отклонены(env, untouched):
    env.put("queue", "а.txt", b"text")
    os.mkdir(os.path.join(os.path.dirname(env.full("queue", "а.txt")), "папка"))
    os.mkfifo(os.path.join(os.path.dirname(env.full("queue", "а.txt")), "канал"))
    for name in ("папка", "канал"):
        refused(env, "queue", f"очередь/{BATCH}/{name}", OUTSIDE)
    assert untouched == []


def test_хороший_путь_каждой_области_принимается_и_разбирается(env):
    queue = env.put("queue", "договор.txt", b"q")
    quarantine = env.put("quarantine", "яд.txt", b"k")
    corpus = env.put("corpus", "отчёт.txt", b"c")
    for area, path, name in (("queue", queue, "договор.txt"), ("quarantine", quarantine, "яд.txt"), ("corpus", corpus, "отчёт.txt")):
        ref = P.resolve(env.home, area, path)
        assert ref.full == os.path.realpath(env.full(area, name)) and ref.batch == BATCH and ref.name == name and ref.area == area


def test_имя_со_слэшем_внутри_пачки(env):
    path = env.put("queue", "архив.zip/внутри/файл.txt", b"x")
    ref = P.resolve(env.home, "queue", path)
    assert ref.name == "архив.zip/внутри/файл.txt" and ref.batch == BATCH
    assert P.resolve(env.home, "queue", path.replace("/", "\\")).full == ref.full         # обратные слэши читаются как прямые


def test_корпус_принимает_путь_любой_глубины_и_старый_корень(env, monkeypatch):
    monkeypatch.setattr(corpus_path, "ALIAS", {"sample_jira_export": "jira"})          # старое имя корня — из таблицы источников (FR-99)
    os.makedirs(os.path.join(env.home, "corpus", "jira"))
    with open(os.path.join(env.home, "corpus", "jira", "ПРОЕКТ-1.txt"), "wb") as f:
        f.write(b"x")
    with open(os.path.join(env.home, "corpus", "readme.txt"), "wb") as f:
        f.write(b"x")
    for path in ("jira/ПРОЕКТ-1.txt", "sample_jira_export/ПРОЕКТ-1.txt", "readme.txt"):       # старый корень читается как новый
        ref = P.resolve(env.home, "corpus", path)
        assert os.path.isfile(ref.full) and ref.area == "corpus"
    assert P.resolve(env.home, "corpus", "jira/ПРОЕКТ-1.txt").batch is None and P.resolve(env.home, "corpus", "readme.txt").batch is None


def test_корень_области_может_быть_ссылкой_на_другой_диск(env):
    """Корпус владелец вправе держать на другом диске: ссылкой может быть сам корень, но не то, что под ним."""
    real = env.tmp / "другой-диск"
    (real / "входящие" / BATCH).mkdir(parents=True)
    (real / "входящие" / BATCH / "а.txt").write_bytes(b"x")
    os.rmdir(os.path.join(env.home, "corpus"))
    os.symlink(real, os.path.join(env.home, "corpus"))
    assert P.resolve(env.home, "corpus", f"входящие/{BATCH}/а.txt").full == str(real / "входящие" / BATCH / "а.txt")


# ── ключ --member ───────────────────────────────────────────────
@pytest.mark.parametrize("values,expected", [([], []), (["0"], [0]), (["3", "0"], [3, 0]), (["12", "7"], [12, 7]), ((), []), (None, [])])
def test_member_разбирается_в_целые(values, expected):
    assert P.check_members(values) == expected


@pytest.mark.parametrize("values", [["3", "1", "2"], ["0", "0", "0"], ["-1"], ["x"], [" 1"], ["1.5"], [""], ["+1"], ["1e2"], ["١"],
                                    ["1", "-2"], ["9" * 20], [None], [1.5], [True]])
def test_member_негодный_или_больше_двух_отказ(values):
    with pytest.raises(P.PreviewError) as e:
        P.check_members(values)
    assert e.value.message.code == "preview.bad_member" and e.value.message.args == {"max": 2}


def test_три_ключа_member_отказ_до_открытия_файла(env, untouched):
    path = env.put("queue", "а.txt", b"text")
    with pytest.raises(P.PreviewError) as e:
        P.show(env.home, "queue", path, ["3", "1", "2"])
    assert e.value.message.code == "preview.bad_member" and untouched == []


def test_вложение_с_негодной_ссылкой_всё_равно_отказ_по_ссылке(env, untouched):
    refused(env, "queue", f"очередь/{BATCH}/../x", OUTSIDE)
    with pytest.raises(P.PreviewError) as e:
        P.show(env.home, "queue", f"очередь/{BATCH}/../x", ["0"])
    assert e.value.message.code in OUTSIDE


# ── номер страницы ──────────────────────────────────────────────
@pytest.mark.parametrize("number", ["0", "-1", "abc", "1.5", "", " 1", "+1", "1e1", "0001a", "1" * 8, None, 1.5])
def test_негодный_номер_страницы_отказ_до_открытия_файла(env, untouched, number):
    path = env.put("queue", "а.pdf", b"x")
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", path, number)
    assert e.value.message.code == "preview.bad_page" and untouched == []

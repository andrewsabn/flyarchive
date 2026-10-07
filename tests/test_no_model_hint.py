"""Без локальной модели новичок понимает, что делать (FR-107).

Подсказка заведения архива (`init.todo_model`) и причина в очереди, когда локальная модель не задана (`llm.not_configured`), называют оба пути: задать
модель — настройка `llm_local_model` — или выключить проверку моделью: `flyarchive inbox set --llm off`. Значение по умолчанию (проверка моделью включена)
не менялось: непроверенное моделью ждёт человека в очереди, это решение владельца. Команда настоящая, служба векторов — подставная.
"""
import json

import pytest

import inbox
import messages as M
from archivekit import Archive
from test_inbox_cli import vectors  # noqa: F401 — подставной сервер векторов

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")

BOTH = ("llm_local_model", "flyarchive inbox set --llm off")


@pytest.fixture
def archive(tmp_path, vectors):
    return Archive(tmp_path, env={"FLYARCHIVE_EMBED_URL": vectors["url"]})


def names_both(text):
    return all(part in text for part in BOTH)


def test_подсказка_заведения_архива_называет_оба_пути():
    assert names_both(str(M.make("init.todo_model")))


def test_причина_когда_модель_не_задана_называет_оба_пути_и_помещается_в_цитату_находки():
    text = str(M.make("llm.not_configured"))
    assert names_both(text)
    assert len("модель не проверила: " + text) <= 200, "находка обрезала бы подсказку: цитата не длиннее 200 знаков"


def test_init_печатает_оба_пути_и_в_тексте_и_в_json(archive):
    code, out, err = archive("init")
    assert code == 0, err
    assert names_both(out)
    code, out, err = archive("init", "--json")
    todo = {t["id"]: t["message"] for t in json.loads(out)["todo"]}
    assert todo["model"]["code"] == "init.todo_model" and names_both(todo["model"]["text"])


def test_значение_по_умолчанию_проверка_моделью_включена_и_не_менялась():
    assert inbox.DEFAULTS["llm"] is True and inbox.DEFAULTS["cloud"] is False


def test_документ_без_модели_ждёт_человека_а_причина_в_очереди_называет_оба_пути(archive):
    assert archive("init")[0] == 0
    code, out, err = archive("inbox", "set", "--path", archive.box, "--period", "30")       # проверка моделью — по умолчанию
    assert code == 0, err
    archive.instant()
    archive.put("записка.txt", "Согласовать перенос работ на четверг.")
    code, out, err = archive("inbox", "run", "--wait")
    assert code == 0, err
    code, out, err = archive("queue", "list", "--json")
    assert code == 0, err
    (item,) = json.loads(out)
    quote = item["findings"][0]["quote"]
    assert item["findings"][0]["rule"] == "llm_unchecked" and names_both(quote), quote


def test_после_выключения_проверки_моделью_тот_же_документ_принимается(archive):
    assert archive("init")[0] == 0
    assert archive("inbox", "set", "--path", archive.box, "--llm", "off", "--cloud", "off", "--period", "30")[0] == 0
    archive.instant()
    archive.put("записка.txt", "Согласовать перенос работ на четверг.")
    code, out, err = archive("inbox", "run", "--wait")
    assert code == 0 and "Принято: 1" in out, out + err
    assert json.loads(archive("queue", "list", "--json")[1]) == []


def test_команда_check_с_моделью_называет_причину_с_обоими_путями(archive):
    assert archive("init")[0] == 0
    source = archive.base / "пачка"
    source.mkdir()
    (source / "записка.txt").write_text("Согласовать перенос работ на четверг.", encoding="utf-8")
    code, out, err = archive("check", str(source), "--into", str(archive.base / "разбор"), "--llm", "--no-cloud")
    assert code == 0, err
    assert str(M.make("llm.not_configured")) in out

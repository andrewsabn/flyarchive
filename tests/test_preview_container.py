"""Просмотр Office, HTML и метафайлов через контейнер (FR-78): родитель на подставном docker и подставном рабочем процессе.

Контейнер подставной (`FakeDocker`): тест решает, что он «вернул», в том числе порчу. Настоящий контейнер — в test_preview_container_real.py,
безопасность запуска (ключи, подключения, имена) — в test_preview_container_safety.py, настройка образа и уборка — в test_preview_setup.py.
"""
import os
import threading
import time

import pytest

import gatekit as K
import preview as P
import preview_container as C
from test_preview_container_kit import (BATCH, DIGEST, NAME, OFFICE_PDF, PDF_DESCRIPTION, SHA, TYPES, box, hold_lock, leftovers, needs,  # noqa: F401
                                         record_image, run_argv, show, walk_modes)
from test_preview_kit import env, png, worker, write  # noqa: F401


# ── что отдаётся ────────────────────────────────────────────────
def test_docx_отвечает_страницами_как_pdf_с_типом_исходного_файла(box):
    data = K.docx_page()
    path = box.env.put("queue", "договор.docx", data)
    answer = show(box.env, path)
    assert answer == {"kind": "pages", "pages": 3, "shown": 3,
                      "meta": {"name": "договор.docx", "type": "docx", "size": len(data), "sha256": SHA(data), "batch": BATCH,
                               "origin": {"archive": None, "inner": None}}}
    assert len(box.docker.runs()) == 1


@pytest.mark.parametrize("type_", TYPES)
def test_каждый_тип_из_карточки_идёт_в_контейнер_и_отвечает_pages(box, type_):
    data = b"x" + type_.encode()
    path = box.env.put("queue", f"файл.{type_}", data)
    answer = show(box.env, path)
    assert answer["kind"] == "pages" and answer["meta"]["type"] == type_ and answer["meta"]["sha256"] == SHA(data), answer
    assert list(box.docker.runs()[0]["in"]) == [f"data.{type_}"]


def test_в_карточке_двадцать_два_типа_и_они_названы_как_в_плане():
    assert sorted(P.CONVERT_TYPES) == sorted("doc docx rtf odt xls xlsx xlsm xlsb csv ods ppt pptx pptm ppsx odp vsd vsdx emf wmf wmz emz html".split())
    assert len(P.CONVERT_TYPES) == 22


def test_типы_без_контейнера_по_прежнему_нужен_преобразователь(box):
    for type_ in ("bpmn", "drawio", "mp4", "mov", "mkv", "mp3"):
        box.worker.describe = lambda ext, t=type_: needs(t)
        answer = show(box.env, box.env.put("queue", f"файл.{type_}", b"x"))
        assert answer["kind"] == "none" and answer["note"]["code"] == "preview.needs_converter" and answer["note"]["args"] == {"type": type_}
    assert box.docker.calls() == []


def test_pdf_и_текст_контейнера_не_касаются(box):
    box.worker.describe = lambda ext: {"kind": "text", "type": "txt", "text": "т", "truncated": False}
    assert show(box.env, box.env.put("queue", "а.txt", b"x"))["kind"] == "text"
    box.worker.describe = lambda ext: PDF_DESCRIPTION
    assert show(box.env, box.env.put("queue", "а.pdf", b"y"))["kind"] == "pages"
    assert box.docker.calls() == []


# ── кэш: base.pdf под sha256 исходного ──────────────────────────
def test_pdf_ложится_в_кэш_под_sha256_исходного_файла_с_правами_0600(box):
    data = K.docx_page("исходный")
    box.docker.plan(run={"files": {"data.pdf": OFFICE_PDF}})
    show(box.env, box.env.put("queue", "а.docx", data))
    assert box.env.cached() == [f"{SHA(data)}/base.pdf", f"{SHA(data)}/desc.json"]
    assert SHA(data) != SHA(OFFICE_PDF)
    with open(os.path.join(box.env.cache, SHA(data), "base.pdf"), "rb") as f:
        assert f.read() == OFFICE_PDF
    modes = walk_modes(box.env.cache)
    assert modes[SHA(data)] == (True, 0o700)
    assert modes[f"{SHA(data)}/base.pdf"] == (False, 0o600) and modes[f"{SHA(data)}/desc.json"] == (False, 0o600)
    assert leftovers(box.env) == []


def test_описание_pdf_читает_рабочий_процесс_а_не_родитель(box):
    show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    assert box.worker.calls == [("describe", "docx", None), ("describe", "pdf", None)]
    assert box.worker.inputs[1].startswith(b"%PDF-") and box.worker.inputs[1] == OFFICE_PDF[:65536]


def test_повторный_show_контейнер_и_рабочий_процесс_не_запускает(box):
    path = box.env.put("queue", "а.docx", K.docx_page())
    first = show(box.env, path)
    assert show(box.env, path) == first
    assert len(box.docker.runs()) == 1 and len(box.worker.calls) == 2
    other = box.env.put("queue", "копия.docx", K.docx_page(), batch="20261004-130000")       # тот же файл в другой пачке: ключ кэша тот же
    assert show(box.env, other)["pages"] == 3 and len(box.docker.runs()) == 1


def test_описание_негодного_pdf_none_и_base_pdf_не_остаётся(box):
    box.worker.describe = lambda ext: ({"kind": "none", "type": "pdf", "reason": "broken", "args": {"error_type": "FileDataError"}}
                                       if ext == "pdf" else needs(ext))
    data = K.docx_page()
    answer = show(box.env, box.env.put("queue", "а.docx", data))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.broken" and answer["meta"]["type"] == "docx"
    assert box.env.cached() == [] and leftovers(box.env) == []


def test_описание_pdf_с_чужим_видом_не_принимается(box):
    box.worker.describe = lambda ext: {"kind": "text", "type": "txt", "text": "т", "truncated": False} if ext == "pdf" else needs(ext)
    answer = show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.bad_answer" and box.env.cached() == []


# ── страница: из готового base.pdf, преобразование не запускает ───
def test_page_рисует_из_base_pdf_а_контейнер_не_запускает(box):
    path = box.env.put("queue", "а.docx", K.docx_page())
    show(box.env, path)
    box.worker.render = lambda page: png(30 + page, 40)
    assert P.page(box.env.home, "queue", path, "2") == png(32, 40)
    assert len(box.docker.runs()) == 1
    assert box.worker.calls[-1] == ("render", "pdf", 2) and box.worker.inputs[-1] == OFFICE_PDF[:65536]    # рисуется PDF, а не исходный docx
    assert P.page(box.env.home, "queue", path, "2") == png(32, 40) and box.worker.ops.count("render") == 1
    with pytest.raises(P.PreviewError) as e:
        P.page(box.env.home, "queue", path, "4")
    assert e.value.message.code == "preview.no_page" and e.value.message.args == {"page": 4, "shown": 3}


def test_page_до_show_отказ_с_сообщением_и_ни_контейнера_ни_docker(box):
    path = box.env.put("queue", "а.docx", K.docx_page())
    with pytest.raises(P.PreviewError) as e:
        P.page(box.env.home, "queue", path, "1")
    assert e.value.message.code == "preview.not_converted" and str(e.value)
    assert box.docker.calls() == [] and box.env.cached() == []


def test_page_без_base_pdf_отказ_и_преобразование_не_запускается(box):
    data = K.docx_page()                                  # один раз: zip хранит время сборки, вторая сборка даёт другой sha256
    path = box.env.put("queue", "а.docx", data)
    show(box.env, path)
    os.unlink(os.path.join(box.env.cache, SHA(data), "base.pdf"))
    with pytest.raises(P.PreviewError) as e:
        P.page(box.env.home, "queue", path, "3")
    assert e.value.message.code == "preview.not_converted"
    assert len(box.docker.runs()) == 1 and box.worker.ops.count("render") == 0


def test_page_с_base_pdf_ссылкой_отказ(box):
    data = K.docx_page()
    path = box.env.put("queue", "а.docx", data)
    show(box.env, path)
    base = os.path.join(box.env.cache, SHA(data), "base.pdf")
    os.unlink(base)
    os.symlink("/etc/hostname", base)
    with pytest.raises(P.PreviewError) as e:
        P.page(box.env.home, "queue", path, "1")
    assert e.value.message.code == "preview.not_converted" and box.worker.ops.count("render") == 0


def test_page_после_отказа_show_тоже_без_контейнера(box):
    box.docker.plan(run={"files": {"data.pdf": "<html>не pdf</html>".encode("utf-8")}})
    path = box.env.put("queue", "а.docx", K.docx_page())
    assert show(box.env, path)["kind"] == "none"
    with pytest.raises(P.PreviewError):
        P.page(box.env.home, "queue", path, "1")
    assert len(box.docker.runs()) == 1


# ── один рендер за раз ──────────────────────────────────────────
def test_общий_замок_занят_rendering_и_второй_контейнер_не_запущен(box):
    fd = hold_lock(os.path.join(box.env.cache, ".render.lock"))
    try:
        assert show(box.env, box.env.put("queue", "а.docx", K.docx_page())) == {"kind": "rendering"}
        assert box.docker.calls() == [] and box.env.cached() == []
    finally:
        os.close(fd)
    assert show(box.env, box.env.put("queue", "а.docx", K.docx_page()))["kind"] == "pages"           # замок отпущен — преобразование идёт


def test_готовый_объект_и_pdf_отвечают_и_при_занятом_общем_замке(box):
    path = box.env.put("queue", "а.docx", K.docx_page())
    show(box.env, path)
    fd = hold_lock(os.path.join(box.env.cache, ".render.lock"))
    try:
        assert show(box.env, path)["kind"] == "pages"
        box.worker.describe = lambda ext: PDF_DESCRIPTION
        assert show(box.env, box.env.put("queue", "а.pdf", b"pdf"))["kind"] == "pages"
        box.worker.describe = lambda ext: {"kind": "text", "type": "txt", "text": "т", "truncated": False}
        assert show(box.env, box.env.put("queue", "а.txt", b"t"))["kind"] == "text"
    finally:
        os.close(fd)


def test_замок_объекта_занят_rendering(box):
    data = K.docx_page()
    path = box.env.put("queue", "а.docx", data)
    os.makedirs(box.env.cache, exist_ok=True)
    fd = hold_lock(os.path.join(box.env.cache, SHA(data) + ".lock"))
    try:
        assert show(box.env, path) == {"kind": "rendering"} and box.docker.calls() == []
    finally:
        os.close(fd)


def test_два_разных_файла_одновременно_один_контейнер_второй_rendering(box):
    box.docker.plan(run={"files": {"data.pdf": OFFICE_PDF}, "sleep": 0.8})
    paths = [box.env.put("queue", "а.docx", K.docx_page("первый")), box.env.put("queue", "б.docx", K.docx_page("второй"))]
    answers = {}
    threads = [threading.Thread(target=lambda p=p: answers.update({p: show(box.env, p)})) for p in paths]
    for t in threads:
        t.start()
        time.sleep(0.3)                      # первый успевает занять общий замок
    for t in threads:
        t.join()
    assert [answers[p]["kind"] for p in paths] == ["pages", "rendering"]
    assert len(box.docker.runs()) == 1
    box.docker.plan(run={"files": {"data.pdf": OFFICE_PDF}, "sleep": 0})
    assert show(box.env, paths[1])["kind"] == "pages" and len(box.docker.runs()) == 2          # замок отпущен — второй файл теперь идёт


def test_два_запроса_одного_файла_контейнер_один(box):
    box.docker.plan(run={"files": {"data.pdf": OFFICE_PDF}, "sleep": 0.8})
    path = box.env.put("queue", "а.docx", K.docx_page())
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(show(box.env, path))) for _ in range(2)]
    for t in threads:
        t.start()
        time.sleep(0.25)
    for t in threads:
        t.join()
    assert sorted(a["kind"] for a in answers) == ["pages", "rendering"] and len(box.docker.runs()) == 1


def test_общий_замок_отпущен_после_отказа_контейнера(box):
    box.docker.plan(run={"files": {}, "exit": 1})
    assert show(box.env, box.env.put("queue", "а.docx", K.docx_page()))["kind"] == "none"
    box.docker.plan(run={"files": {"data.pdf": OFFICE_PDF}, "exit": 0})
    assert show(box.env, box.env.put("queue", "а.docx", K.docx_page()))["kind"] == "pages"


# ── отказы без исключений: kind none с кодом, в кэше ничего ─────
def refused(box, code, name="а.docx", data=None, **args):
    answer = show(box.env, box.env.put("queue", name, data or K.docx_page()))
    assert answer["kind"] == "none" and answer["note"]["code"] == code, answer
    for key, value in args.items():
        assert answer["note"]["args"][key] == value, answer
    assert answer["note"]["text"] and answer["meta"]["type"] == "docx" and answer["meta"]["sha256"]
    assert box.env.cached() == [] and leftovers(box.env) == []
    return answer


def test_предел_размера_по_умолчанию_сто_мегабайт():
    assert P.CONVERT_MAX == 100 << 20 and P.PDF_MAX == 200 << 20
    assert (C.RUN_SECONDS, C.CLIENT_SECONDS) == (120, 150) and C.STALE_SECONDS == 300


def test_файл_больше_предела_none_и_docker_не_тронут(box, monkeypatch):
    monkeypatch.setattr(P, "CONVERT_MAX", 2 << 20)
    refused(box, "preview.convert_too_big", data=K.docx_page() + b"\0" * ((2 << 20) + 1), limit=2)
    assert box.docker.calls() == []
    box.docker.plan(run={"files": {"data.pdf": OFFICE_PDF}})
    exact = K.docx_page()
    exact += b"\0" * ((2 << 20) - len(exact))                    # ровно на пределе — идёт
    assert show(box.env, box.env.put("queue", "б.docx", exact))["kind"] == "pages"


def test_docker_не_установлен_none_с_пояснением(box, monkeypatch, tmp_path):
    empty = tmp_path / "пусто"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    refused(box, "preview.no_docker")


def test_docker_не_отвечает_none_за_срок_вызова(box, monkeypatch):
    monkeypatch.setattr(C, "CALL_SECONDS", 1)
    box.docker.plan(image={"sleep": 20})
    started = time.monotonic()
    refused(box, "preview.docker_silent", seconds=1)
    assert time.monotonic() - started < 8 and box.docker.runs() == []


def test_docker_ответил_ошибкой_службы_none_с_причиной(box):
    box.docker.plan(image={"exit": 1, "stderr": "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"})
    answer = refused(box, "preview.docker_failed")
    assert "exit 1" in answer["note"]["args"]["why"] and "Cannot connect" in answer["note"]["args"]["why"] and box.docker.runs() == []


def test_дайджест_не_записан_none_подсказывает_setup_и_ничего_не_скачивается(box):
    os.unlink(os.path.join(box.env.home, "preview.json"))
    answer = refused(box, "preview.no_image")
    assert "flyarchive preview setup" in answer["note"]["text"]
    assert box.docker.calls() == []                                   # даже docker image inspect не нужен, а pull тем более


def test_образа_с_таким_дайджестом_нет_none_и_docker_run_не_вызван(box):
    box.docker.plan(image={"exit": 1, "stderr": f"Error response from daemon: No such image: {DIGEST}"})
    answer = refused(box, "preview.image_missing", image=DIGEST)
    assert "flyarchive preview setup" in answer["note"]["text"]
    assert box.docker.ops() == ["image-inspect"] and DIGEST in box.docker.calls()[0]["argv"]            # проверка наличия — по дайджесту, без pull


def test_срок_внутри_контейнера_вышел_none_преобразование_не_уложилось(box, monkeypatch):
    monkeypatch.setattr(C, "RUN_SECONDS", 2)
    box.docker.plan(run={"files": {}, "exit": 137, "sleep": 2.3})         # timeout -s KILL даёт 137, как и убийство за память
    refused(box, "preview.convert_timeout", seconds=2)


def test_timeout_вернул_124_тоже_срок(box):
    box.docker.plan(run={"files": {}, "exit": 124})
    refused(box, "preview.convert_timeout", seconds=C.RUN_SECONDS)


def test_клиент_docker_не_вернулся_за_срок_родитель_убивает_и_удаляет_контейнер_по_имени(box, monkeypatch):
    monkeypatch.setattr(C, "CLIENT_SECONDS", 1)
    box.docker.plan(run={"files": {}, "sleep": 30})
    started = time.monotonic()
    refused(box, "preview.convert_timeout", seconds=C.RUN_SECONDS)
    assert time.monotonic() - started < 15
    argv = run_argv(box.docker)
    name = argv[argv.index("--name") + 1]
    assert NAME.fullmatch(name)
    stops = [c["argv"] for c in box.docker.calls() if c["op"] in ("kill", "rm")]
    assert stops == [["kill", name], ["rm", "-f", name]]                   # сначала kill, потом rm -f, оба по имени


def test_сбой_уборки_по_сроку_не_ломает_ответ(box, monkeypatch):
    monkeypatch.setattr(C, "CLIENT_SECONDS", 1)
    box.docker.plan(run={"files": {}, "sleep": 30}, kill_exit=1, rm_exit=1)
    refused(box, "preview.convert_timeout")


def test_контейнер_завершился_с_ошибкой_none_с_кодом_и_последней_строкой(box):
    box.docker.plan(run={"files": {}, "exit": 1, "stderr": "первая\nError: source file could not be loaded"})
    answer = refused(box, "preview.convert_failed")
    assert answer["note"]["args"]["why"] == "exit 1: Error: source file could not be loaded"


def test_контейнер_убит_быстро_и_без_события_oom_это_ошибка_а_не_память(box):
    box.docker.plan(run={"files": {}, "exit": 137})
    answer = refused(box, "preview.convert_failed")
    assert answer["note"]["args"]["why"] == "exit 137" and "events" in box.docker.ops()


def test_нехватка_памяти_в_контейнере_none_про_память(box):
    box.docker.plan(run={"files": {}, "exit": 137}, events="oom\n")
    refused(box, "preview.convert_memory", gb=2)


def test_запрос_событий_только_по_имени_этого_контейнера(box):
    box.docker.plan(run={"files": {}, "exit": 137})
    show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    name = run_argv(box.docker)[run_argv(box.docker).index("--name") + 1]
    asked = [c["argv"] for c in box.docker.calls() if c["op"] == "events"][0]
    assert f"container={name}" in asked and "event=oom" in asked


def test_на_выходе_нет_файла_none(box):
    box.docker.plan(run={"files": {}})
    refused(box, "preview.convert_no_pdf")


def test_на_выходе_не_pdf_none(box):
    box.docker.plan(run={"files": {"data.pdf": b"<html><script>alert(1)</script></html>"}})
    refused(box, "preview.convert_not_pdf")
    box.docker.plan(run={"files": {"data.pdf": b""}})
    refused(box, "preview.convert_not_pdf")
    box.docker.plan(run={"files": {"data.pdf": b"%PDF"}})                           # четыре знака вместо пяти
    refused(box, "preview.convert_not_pdf")


def test_на_выходе_ссылка_none_даже_если_она_ведёт_на_настоящий_pdf(box, tmp_path):
    real = tmp_path / "настоящий.pdf"
    real.write_bytes(OFFICE_PDF)
    box.docker.plan(run={"files": {"data.pdf": {"symlink": str(real)}}})
    refused(box, "preview.convert_not_a_file")


@pytest.mark.parametrize("spec", [{"dir": 1}, {"fifo": 1}])
def test_на_выходе_каталог_или_канал_none_и_без_зависания(box, spec):
    box.docker.plan(run={"files": {"data.pdf": spec}})
    started = time.monotonic()
    refused(box, "preview.convert_not_a_file")
    assert time.monotonic() - started < 10


def test_на_выходе_больше_одного_файла_none(box):
    box.docker.plan(run={"files": {"data.pdf": OFFICE_PDF, "лишний.txt": b"x"}})
    refused(box, "preview.convert_several")


def test_имя_выходного_файла_значения_не_имеет_но_файл_должен_быть_один(box):
    box.docker.plan(run={"files": {"что-угодно ..pdf ; $(x)": OFFICE_PDF}})
    data = K.docx_page()
    assert show(box.env, box.env.put("queue", "а.docx", data))["kind"] == "pages"
    assert box.env.cached() == [f"{SHA(data)}/base.pdf", f"{SHA(data)}/desc.json"]


def test_выход_больше_предела_none_и_на_пределе_принимается(box, monkeypatch):
    monkeypatch.setattr(P, "PDF_MAX", 2 << 20)
    big = b"%PDF-1.4\n" + b"\0" * ((2 << 20) - 8)                                    # на байт больше предела
    box.docker.plan(run={"files": {"data.pdf": big}})
    refused(box, "preview.convert_output_big", limit=2)
    exact = b"%PDF-1.4\n" + b"\0" * ((2 << 20) - 9)
    box.docker.plan(run={"files": {"data.pdf": exact}})
    assert show(box.env, box.env.put("queue", "а.docx", K.docx_page()))["kind"] == "pages"


def test_выход_разрастается_пока_контейнер_идёт_родитель_останавливает_его(box, monkeypatch):
    monkeypatch.setattr(P, "PDF_MAX", 3 << 20)
    box.docker.plan(run={"files": {"data.pdf": {"grow": 200}}})
    started = time.monotonic()
    refused(box, "preview.convert_output_big", limit=3)
    assert time.monotonic() - started < 8                                           # не ждал все 200 МБ
    ops = box.docker.ops()
    assert "kill" in ops and "rm" in ops


def test_рабочие_каталоги_убраны_после_удачи_и_после_отказа(box):
    assert show(box.env, box.env.put("queue", "а.docx", K.docx_page()))["kind"] == "pages"
    assert leftovers(box.env) == [] and len(box.docker.runs()) == 1
    box.docker.plan(run={"files": {}})
    assert show(box.env, box.env.put("queue", "б.docx", K.docx_page("другой")))["kind"] == "none"
    assert leftovers(box.env) == [] and len(box.docker.runs()) == 2


def test_непредвиденное_исключение_при_запуске_docker_не_выходит_наружу(box, monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("нет сил")
    monkeypatch.setattr(C, "run", explode)
    answer = show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.worker_failed" and answer["note"]["args"] == {"why": "RuntimeError"}
    assert box.env.cached() == [] and leftovers(box.env) == []


def test_ошибка_записи_кэша_none_а_не_исключение(box, monkeypatch):
    def refuse(*a, **k):
        raise OSError(28, "нет места")
    monkeypatch.setattr(P, "_store_base", refuse)
    answer = show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.cache_failed" and answer["note"]["args"] == {"error_type": "OSError"}


# ── вход: копия байтов из открытого дескриптора ─────────────────
def test_во_входную_папку_кладётся_копия_data_расширение_с_правами_0600_и_sha_сошёлся(box):
    data = K.docx_page("копия")
    show(box.env, box.env.put("queue", "договор.docx", data))
    run = box.docker.runs()[0]
    assert list(run["in"]) == ["data.docx"] and run["in"]["data.docx"]["sha256"] == SHA(data) and run["in"]["data.docx"]["mode"] == "0o600"
    assert run["in_dir_mode"] == "0o700" and run["out_before"] == {}


def test_имя_и_путь_из_очереди_во_входную_папку_не_попадают(box):
    data = K.docx_page()
    path = box.env.put("queue", "секретный-путь/внутри/файл.docx", data)
    show(box.env, path)
    text = repr(box.docker.calls())
    assert "секретный-путь" not in text and "внутри" not in text and "файл.docx" not in text and BATCH not in text
    assert box.docker.runs()[0]["mounts"]["/in"].rsplit("/", 1)[-1].startswith(".in-")


def test_подмена_файла_после_подсчёта_sha_контейнер_получает_прежние_байты(box, monkeypatch):
    """Файл заменён другим (новый inode) между подсчётом sha256 и запуском: копия идёт из уже открытого дескриптора, а не по пути."""
    data = K.docx_page("исходный")
    path = box.env.put("queue", "а.docx", data)
    full = box.env.full("queue", "а.docx")
    real = P._sha256

    def swap(fd):
        result = real(fd)
        with open(full + ".new", "wb") as f:
            f.write(K.docx_page("подмена"))
        os.replace(full + ".new", full)
        return result

    monkeypatch.setattr(P, "_sha256", swap)
    answer = show(box.env, path)
    assert answer["kind"] == "pages" and box.docker.runs()[0]["in"]["data.docx"]["sha256"] == SHA(data)


def test_файл_изменён_на_месте_после_подсчёта_sha_отказ_и_контейнер_не_запущен(box, monkeypatch):
    data = K.docx_page("исходный")
    path = box.env.put("queue", "а.docx", data)
    full = box.env.full("queue", "а.docx")
    real = P._sha256

    def grow(fd):
        result = real(fd)
        with open(full, "ab") as f:                          # тот же inode, другое содержимое
            f.write("дописано".encode("utf-8"))
        return result

    monkeypatch.setattr(P, "_sha256", grow)
    answer = show(box.env, path)
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.input_changed"
    assert box.docker.runs() == [] and box.env.cached() == [] and leftovers(box.env) == []


def test_расширение_берётся_из_имени_только_если_это_тип_из_списка_иначе_из_типа(box):
    cases = (("а.DOCX", "docx", "docx"), ("а.bak", "docx", "docx"), ("без-расширения", "xlsx", "xlsx"), ("а.doc", "docx", "doc"),
             ("а." + "x" * 11, "rtf", "rtf"), ("а.docm", "docx", "docx"), ("а.htm", "html", "html"), ("а.xlsx", "xlsx", "xlsx"))
    for number, (name, type_, expected) in enumerate(cases):
        box.worker.describe = lambda ext, t=type_: PDF_DESCRIPTION if ext == "pdf" else needs(t)
        show(box.env, box.env.put("queue", name, f"данные {number}".encode()))
        assert list(box.docker.runs()[-1]["in"]) == [f"data.{expected}"], name


# ── письмо ──────────────────────────────────────────────────────
def test_вложение_письма_открывается_цепочкой_member_ключ_кэша_sha256_вложения(box):
    att = K.docx_page("во вложении")
    box.worker.member = lambda number: ("отчёт.docx", att)
    letter = box.env.put("queue", "письмо.eml", K.EML)
    box.worker.describe = lambda ext: PDF_DESCRIPTION if ext == "pdf" else needs("docx") if ext == "docx" else {
        "kind": "mail", "type": "eml", "mail": {"from": "", "to": [], "cc": [], "date": None, "subject": "", "text": "", "truncated": False,
                                                "attachments": []}}
    answer = show(box.env, letter, members=[0])
    assert answer["kind"] == "pages" and answer["meta"]["name"] == "отчёт.docx" and answer["meta"]["sha256"] == SHA(att)
    assert box.docker.runs()[0]["in"]["data.docx"]["sha256"] == SHA(att)
    assert box.env.cached() == [f"{SHA(att)}/base.pdf", f"{SHA(att)}/desc.json"]
    box.worker.render = lambda page: png(10, 10 + page)
    assert P.page(box.env.home, "queue", letter, "2", [0]) == png(10, 12) and len(box.docker.runs()) == 1


def test_одно_вложение_из_двух_писем_преобразуется_один_раз(box):
    att = K.docx_page("общее")
    box.worker.member = lambda number: ("а.docx", att)
    box.worker.describe = lambda ext: PDF_DESCRIPTION if ext == "pdf" else needs("docx") if ext == "docx" else needs("eml")
    first = box.env.put("queue", "первое.eml", b"a")
    second = box.env.put("queue", "второе.eml", b"b")
    show(box.env, first, members=[0])
    show(box.env, second, members=[0])
    assert len(box.docker.runs()) == 1


def test_имя_вложения_с_оболочкой_и_путями_в_команду_не_попадает(box):
    hostile = "$(touch -- ../pwned) ; rm -rf ~ `id` ../../x.docx"
    box.worker.member = lambda number: (hostile, K.docx_page("вложение"))
    box.worker.describe = lambda ext: PDF_DESCRIPTION if ext == "pdf" else needs("docx") if ext == "docx" else needs("eml")
    show(box.env, box.env.put("queue", "письмо.eml", b"a"), members=[0])
    argv = run_argv(box.docker)
    assert not any("touch" in a or "rm -rf" in a or "pwned" in a or "`" in a for a in argv) and argv[-1] == "/in/data.docx"
    assert list(box.docker.runs()[0]["in"]) == ["data.docx"] and "pwned" not in repr(box.docker.calls())

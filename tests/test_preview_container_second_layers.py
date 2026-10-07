"""Вторые слои защиты просмотра и уборка непринятого PDF (FR-78).

Каждый закрывает порчу кода, которую остальные тесты не ловили: проверка стояла вторым слоем, и первый слой не давал до неё дойти.
"""
import json
import os

import pytest

import gatekit as K
import preview as P
import preview_container as C
from test_preview_container_kit import DIGEST, box, leftovers, needs, show  # noqa: F401
from test_preview_kit import env, worker, write  # noqa: F401

NAME = "flyarchive-preview-00000000-0000-0000-0000-000000000000"
GOOD = dict(ref=DIGEST, name=NAME, indir="/tmp/in", outdir="/tmp/out", filename="data.docx", uid=1000, gid=1000)


def test_команда_собирается_с_годными_аргументами():
    argv = C.command(**GOOD)
    assert argv[-1] == "/in/data.docx" and DIGEST in argv and NAME in argv


@pytest.mark.parametrize("filename", ["data.x y", "../data.pdf", "data.docx;id", "-data.docx", "data.", "data.DOCX", "x.docx", "data.docx\n",
                                      "data.abcdefghijk", "data.docx /etc/passwd", "data.d$(id)"])
def test_команда_не_собирается_с_негодным_именем_входного_файла(filename):
    """Родитель и так даёт только `data.<расширение>`, но сборка команды проверяет имя сама: строка из чужого файла в аргументы docker не пройдёт."""
    with pytest.raises(ValueError):
        C.command(**{**GOOD, "filename": filename})


@pytest.mark.parametrize("key,value", [("ref", "gotenberg/gotenberg:8"), ("ref", "--privileged@sha256:" + "ab" * 32), ("name", "flyarchive-preview-x"),
                                       ("name", "other-container"), ("uid", "0"), ("gid", None), ("indir", "/tmp/in:/etc"), ("outdir", "/tmp/out:rw")])
def test_команда_не_собирается_с_негодным_образом_именем_владельцем_или_папкой(key, value):
    with pytest.raises(ValueError):
        C.command(**{**GOOD, key: value})


@pytest.mark.parametrize("digest", ["gotenberg/gotenberg:8", "gotenberg/gotenberg@sha256:" + "ab" * 31, "-x@sha256:" + "ab" * 32, "",
                                    "gotenberg/gotenberg@sha256:" + "AB" * 32, "gotenberg/gotenberg@sha256:" + "ab" * 32 + "\n"])
def test_негодный_дайджест_в_настройку_не_пишется(tmp_path, digest):
    """`setup` отбирает дайджест раньше, но запись проверяет его сама: в настройке не окажется тег, по которому образ мог бы подмениться."""
    with pytest.raises(ValueError):
        C.save_image(str(tmp_path), digest, "gotenberg/gotenberg:8")
    assert os.listdir(tmp_path) == [] and C.image(str(tmp_path)) is None


def test_pdf_от_контейнера_не_остаётся_в_кэше_если_рабочий_процесс_упал_на_нём(box):
    """Контейнер отдал PDF, он уже лёг в кэш, а рабочий процесс на нём упал: непринятый PDF убирается, иначе страницы рисовались бы из него."""
    calls = []

    def behave(op, outdir):
        calls.append(op)
        if len(calls) == 1:                                  # первый запуск называет тип исходного файла
            write(os.path.join(outdir, "result.json"), json.dumps(needs("docx")).encode("utf-8"))
            return P.Run(0)
        return P.Run(1)                                      # второй запуск — описание PDF от контейнера

    box.worker.behave = behave
    answer = show(box.env, box.env.put("queue", "а.docx", K.docx_page()))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.worker_failed", answer
    assert calls == ["describe", "describe"] and len(box.docker.runs()) == 1
    assert box.env.cached() == [] and leftovers(box.env) == []

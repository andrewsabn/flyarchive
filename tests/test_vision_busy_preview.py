"""Занятый просмотр — не неудача страницы (FR-92).

Просмотр держит не больше двух рабочих процессов разом; если вкладку Archive в этот момент листают, страница для описания получит
«ещё рисуется». Это ожидание, а не сбой: за три таких прохода страницу нельзя списывать навсегда.
"""
import os

import messages as M
import preview as P
import vision as V
from test_vision import Script, home, scene  # noqa: F401


def busy():
    return P.PreviewError(M.make("preview.still_rendering"))


def test_занятый_просмотр_не_считается_неудачей_страницы_и_документ_ждёт(home):
    sc = scene(home, pages=3, fail={2: busy()})
    out = sc.run(tries=V.MAX_TRIES - 1)                      # даже на последнем из отведённых проходов
    assert out.failed == 0 and not out.complete and out.pages == 1 and out.stop is None
    assert V.load(home, sc.sha)["skipped"] == []             # страница не списана как неудавшаяся
    assert out.why.code == "vision.failed"
    assert [a[2] for a in sc.viewer.asked] == [1, 2]         # за занятый просмотр шаг не идёт: остальное ждёт следующего прохода
    assert len(sc.script.calls) == 1


def test_после_занятого_просмотра_документ_описывается_целиком(home):
    sc = scene(home, pages=3, fail={2: busy()})
    for _ in range(V.MAX_TRIES + 2):                         # сколько бы проходов просмотр ни был занят
        out = sc.run(tries=0)
        assert out.failed == 0 and not out.complete
    sc.viewer.fail.clear()
    done = sc.run(tries=0)
    assert done.complete and done.pages == 3 and V.load(home, sc.sha)["skipped"] == []
    assert len(sc.script.calls) == 3                         # страница 1 второй раз модели не уходила


def test_настоящий_сбой_просмотра_по_прежнему_считается_неудачей(home):
    sc = scene(home, pages=2, fail={1: P.PreviewError(M.make("preview.worker_failed", why="сбой песочницы"))})
    out = sc.run()
    assert out.failed == 1 and out.pages == 1 and [a[2] for a in sc.viewer.asked] == [1, 2]


def test_описатель_собирается_без_облачной_точки(monkeypatch):
    """Модель берёт только локальную точку, но и запасная облачная для этого шага не создаётся вовсе: второй слой правила «картинки в облако не уходят»."""
    import llm_check as L
    seen = {}
    real = L.from_env

    def spy(env=None, cloud=True):
        seen["cloud"] = cloud
        return real(env, cloud=cloud)

    monkeypatch.setattr(V.llm_check, "from_env", spy)
    made = V.from_env({"FLYARCHIVE_HOME": os.environ["FLYARCHIVE_HOME"], "FLYARCHIVE_LLM_KEY": "test-key", "FLYARCHIVE_LLM_CLOUD_URL": "http://127.0.0.1:2"})
    assert seen == {"cloud": False} and made.endpoint.local is True

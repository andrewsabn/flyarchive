"""Обрезка сообщения находки до предела цитаты (gate.fit): граница предела, знак в знак с прежней обрезкой."""
import pytest

import gate as G
import messages as M


def build(tail):
    return M.make("finding.llm_unchecked", reason=tail)


HEAD = len(str(build("")))                        # длина шаблона без хвоста


@pytest.mark.parametrize("over", [1, 2, 3, 5, 6, 50])
def test_текст_длиннее_предела_обрезается_ровно_до_предела(over):
    tail = "я" * (G.QUOTE - HEAD + over)
    msg = G.fit(build, tail)
    assert len(str(msg)) == G.QUOTE
    assert str(msg) == str(build(tail))[:G.QUOTE]  # то же, что прежняя цитата, обрезанная до предела
    assert msg.args["reason"] == tail[:len(tail) - over]


@pytest.mark.parametrize("short", [0, 1, 40])
def test_текст_не_длиннее_предела_не_меняется(short):
    tail = "я" * (G.QUOTE - HEAD - short)
    msg = G.fit(build, tail)
    assert str(msg) == str(build(tail))
    assert msg.args["reason"] == tail


def test_хвост_короче_излишка_обрезается_до_пустого_а_не_до_отрицательной_длины():
    inner = "я" * (G.QUOTE - HEAD + 1)             # текст без хвоста уже на знак длиннее предела; с хвостом «abc» излишек — 4
    msg = G.fit(lambda s: M.make("finding.llm_unchecked", reason=inner + s), "abc")
    assert msg.args["reason"] == inner             # хвост убран целиком; срез с отрицательным концом оставил бы «ab»

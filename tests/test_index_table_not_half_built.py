"""Таблица индекса не остаётся наполовину готовой (FR-103).

`ingest.create_table` заводит таблицу и строит полнотекстовый индекс. Если второй шаг не удался, таблицу без индекса оставлять нельзя:
повторный `flyarchive init` увидел бы «таблица уже есть» и ничего не достроил, а поиск по словам молча перестал бы работать.
Настоящая LanceDB.
"""
import pytest

import ingest as G

lancedb = pytest.importorskip("lancedb")
pytestmark = pytest.mark.skipif(not hasattr(lancedb, "__version__"), reason="нет lancedb: в тестах подставной")


def test_сбой_полнотекстового_индекса_не_оставляет_таблицу_и_повтор_заводит_её_заново(tmp_path, monkeypatch):
    from lancedb.table import LanceTable
    home = str(tmp_path / "flyarchive")

    def refuse(self, *args, **kwargs):
        raise RuntimeError("полнотекстовый индекс не построился")

    monkeypatch.setattr(LanceTable, "create_fts_index", refuse)
    with pytest.raises(RuntimeError):
        G.create_table(home)
    with pytest.raises(G.TableMissing):          # таблицы без индекса не осталось: архив по-прежнему «не заведён»
        G.open_table(home)
    monkeypatch.undo()
    assert G.create_table(home) is True          # повтор не говорит «уже есть», а заводит таблицу целиком
    assert G.open_table(home).count_rows() == 0
    assert G.create_table(home) is False

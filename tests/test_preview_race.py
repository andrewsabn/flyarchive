"""Просмотр: ответ рабочего процесса, подменённый ссылкой между проверкой и открытием (FR-76).

Рабочий процесс — непроверенный код рядом с непроверенным файлом. Проверка «это обычный файл» и открытие — два шага;
если между ними файл заменить ссылкой, родитель прочитал бы чужой файл и положил бы его в кэш. От этого защищает открытие без перехода по ссылке.
"""
import os

import pytest

import preview as P


def swap_after_check(monkeypatch, path, target):
    """После проверки вида файла на его месте оказывается ссылка на target — как сделал бы рабочий процесс, успевший между шагами."""
    real, done = os.lstat, []

    def lstat(p, *a, **kw):
        st = real(p, *a, **kw)
        if os.fspath(p) == path and not done:
            done.append(True)
            os.remove(path)
            os.symlink(target, path)
        return st

    monkeypatch.setattr(P.os, "lstat", lstat)
    return done


def test_ответ_подменённый_ссылкой_после_проверки_не_читается(tmp_path, monkeypatch):
    secret = tmp_path / "чужой.txt"
    secret.write_bytes(b"not for the cache")
    answer = tmp_path / "out" / "result.json"
    answer.parent.mkdir()
    answer.write_bytes(b"{}")
    done = swap_after_check(monkeypatch, str(answer), str(secret))
    with pytest.raises(P._Bad) as e:
        P._read_regular(str(answer), 1024)
    assert done, "подмена не случилась: тест ничего не проверил"
    assert e.value.args[0] == "not_a_file"
    assert os.path.islink(str(answer))             # на месте ответа действительно ссылка, и по ней не пошли


def test_обычный_ответ_читается_целиком(tmp_path):
    answer = tmp_path / "result.json"
    answer.write_bytes(b'{"kind": "none"}')
    assert P._read_regular(str(answer), 1024) == b'{"kind": "none"}'

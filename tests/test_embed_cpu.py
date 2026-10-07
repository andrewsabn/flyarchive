"""Вектор запроса считается на процессоре.

Видеопамять целиком отдана большой модели. Когда ollama грузит bge-m3 на
видеокарту, большая модель отвечает в 4-15 раз медленнее, пока та не выгрузится.
Проверки обслуживания индекса (сценарий владельца) лежат в закрытой части, рядом с самим сценарием.
"""
import io
import json

import pytest

import index_more as IM
import search as S
import settings as ST


def test_вектор_запроса_считается_на_процессоре(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return io.BytesIO(json.dumps({"embeddings": [[0.1, 0.2]]}).encode())

    monkeypatch.setattr(S.urllib.request, "urlopen", fake_urlopen)
    assert S.embed("запрос") == [0.1, 0.2]
    assert seen["url"] == "http://127.0.0.1:11434/api/embed"
    assert seen["body"]["model"] == "bge-m3" and seen["body"]["input"] == ["запрос"]
    assert seen["body"]["options"] == {"num_gpu": 0}


# ── обслуживание индекса тоже не занимает видеокарту ──
def ollama(monkeypatch, module):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return io.BytesIO(json.dumps({"embeddings": [[0.1, 0.2], [0.3, 0.4]]}).encode())

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_массовая_индексация_считает_векторы_на_процессоре(monkeypatch):
    monkeypatch.delenv("FLYARCHIVE_EMBED_GPU", raising=False)
    seen = ollama(monkeypatch, IM)
    assert IM.embed(["а", "б"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert seen["body"] == {"model": "bge-m3", "input": ["а", "б"], "options": {"num_gpu": 0}}


def test_видеокарта_для_массовой_индексации_только_по_явному_решению(monkeypatch):
    """Плановая пересборка при выгруженной большой модели: владелец включает видеокарту сам."""
    monkeypatch.setenv("FLYARCHIVE_EMBED_GPU", "1")
    seen = ollama(monkeypatch, IM)
    IM.embed(["а"])
    assert seen["body"] == {"model": "bge-m3", "input": ["а"]}


# Переключатель читает модуль настроек (FR-97): годные значения — 1, 0, true, false, on, off, yes, no (любой регистр, пробелы по краям не мешают);
# что-то иное — отказ настроек с названием переменной, а не молчаливый процессор (опечатка не проходит молча).
@pytest.mark.parametrize("value", ["0", "false", "off", "no", "NO"])
def test_выключающие_значения_переменной_оставляют_процессор(monkeypatch, value):
    monkeypatch.setenv("FLYARCHIVE_EMBED_GPU", value)
    seen = ollama(monkeypatch, IM)
    IM.embed(["а"])
    assert seen["body"]["options"] == {"num_gpu": 0}


@pytest.mark.parametrize("value", ["yes", "true", " 1", "on", "TRUE"])
def test_включающие_значения_переменной_включают_видеокарту(monkeypatch, value):
    monkeypatch.setenv("FLYARCHIVE_EMBED_GPU", value)
    seen = ollama(monkeypatch, IM)
    IM.embed(["а"])
    assert seen["body"] == {"model": "bge-m3", "input": ["а"]}


@pytest.mark.parametrize("value", ["", "11", "может быть"])
def test_негодное_значение_переменной_видеокарты_отказ_настроек_запроса_нет(monkeypatch, value):
    monkeypatch.setenv("FLYARCHIVE_EMBED_GPU", value)
    seen = ollama(monkeypatch, IM)
    with pytest.raises(ST.SettingsError) as e:
        IM.embed(["а"])
    assert e.value.message.code == "settings.bad_switch" and e.value.message.args["key"] == "embed_gpu" and "body" not in seen


def test_видеокарта_включается_и_новой_переменной_и_она_сильнее_прежней(monkeypatch):
    monkeypatch.setenv("FLYARCHIVE_EMBED_GPU", "0")
    monkeypatch.setenv("FLYARCHIVE_EMBED_GPU", "1")
    seen = ollama(monkeypatch, IM)
    IM.embed(["а"])
    assert seen["body"] == {"model": "bge-m3", "input": ["а"]}

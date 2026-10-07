"""Образцы в открытой части выглядят выдуманными (FR-113).

Что держат тесты: среди разрешённых доменов-образцов ворот (`EXAMPLE_DOMAINS`) только зарезервированные для документации (RFC 2606 и 6761):
`example.com`, `example.org`, `example.net` и всё в зонах `.test`, `.example`, `.invalid`, `.localhost`; обычного домена или домена организации там нет;
проверка зарезервированности принимает образцы из этих зон и отклоняет обычные домены и домены организаций. Образцы в самом тесте выдуманные.

Имена из раскладки настоящего корпуса и с машины автора ищет закрытая часть: открытая часть их не хранит ни целиком, ни из кусков.
Вместо них в образцах стоят очевидно выдуманные имена: `export-a`, `DEMO`, `TEAM-26`, `20260101-120000`.
"""
import pytest

import publication_gate as G

RESERVED_SECOND_LEVEL = frozenset({"example.com", "example.org", "example.net"})
RESERVED_ZONES = frozenset({"test", "example", "invalid", "localhost"})


def reserved_for_documentation(domain):
    """Домен, который стандарты оставили для документации: сам example.com/.org/.net, его поддомены и любое имя в зонах .test и подобных."""
    domain = domain.lower().strip(".")
    return (any(domain == d or domain.endswith("." + d) for d in RESERVED_SECOND_LEVEL)
            or domain.rsplit(".", 1)[-1] in RESERVED_ZONES)


def test_среди_доменов_образцов_только_зарезервированные_для_документации():
    assert G.EXAMPLE_DOMAINS, "список доменов-образцов пуст"
    not_reserved = [d for d in G.EXAMPLE_DOMAINS if not reserved_for_documentation(d)]
    assert not_reserved == [], f"в EXAMPLE_DOMAINS домены, не зарезервированные для документации: {not_reserved}"


def test_зоны_для_ссылок_образцов_только_зарезервированные_для_документации():
    assert G.RESERVED_TLDS and set(G.RESERVED_TLDS) <= RESERVED_ZONES, f"в RESERVED_TLDS зоны, не зарезервированные для документации: {G.RESERVED_TLDS}"


@pytest.mark.parametrize("domain", ["example.com", "example.org", "example.net", "mail.example.org", "example.test", "box.example.test", "a.example",
                                    "x.invalid", "app.localhost", "EXAMPLE.ORG"])
def test_зарезервированные_домены_проверка_принимает(domain):
    assert reserved_for_documentation(domain)


@pytest.mark.parametrize("domain", ["example.biz", "portal.example.biz", "example.info", "myexample.com", "example.com.biz", "shop.test.info", "gmail.com"])
def test_обычные_домены_и_домены_организаций_проверка_отклоняет(domain):
    assert not reserved_for_documentation(domain)

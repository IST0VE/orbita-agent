"""
Задача 2.6, вторая половина: маскирование перед публикацией.

Маска применяется к тексту до конвертации в storage format, поэтому шаблоны
пишутся под исходные символы, а не под экранированные сущности.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent import config as cfg
from agent.confluence import mask_text
from agent.graph import render_body


def test_no_patterns_means_no_changes():
    assert mask_text("Клиент acc-1024 ждёт ответа") == "Клиент acc-1024 ждёт ответа"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-" + "x" * 24,
        "AKIA" + "A" * 16,
        "ghp_" + "x" * 24,
        "xoxb-" + "1" * 16,
        "-----BEGIN " + "PRIVATE KEY-----\nsecret material\n-----END PRIVATE KEY-----",
    ],
)
def test_common_secret_formats_are_masked_without_configuration(secret: str) -> None:
    assert secret not in mask_text(f"before {secret} after")


def test_single_pattern(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_MASK_PATTERNS", r"acc-\d+")
    assert mask_text("Клиент acc-1024 ждёт") == "Клиент *** ждёт"


def test_several_patterns_are_split_by_double_pipe(monkeypatch: pytest.MonkeyPatch):
    """Разделитель — две черты: одна занята альтернативой внутри регулярки."""
    monkeypatch.setenv(
        "CONFLUENCE_MASK_PATTERNS", r"acc-\d+||[\w.]+@[\w.]+||(ozon|wildberries)"
    )

    masked = mask_text("acc-1024, me@org.com, синхронизация wildberries")

    assert masked == "***, ***, синхронизация ***"


def test_replacement_is_configurable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_MASK_PATTERNS", r"acc-\d+")
    monkeypatch.setenv("CONFLUENCE_MASK_REPLACEMENT", "[скрыто]")
    assert mask_text("Клиент acc-1024") == "Клиент [скрыто]"


def test_broken_pattern_fails_loudly(monkeypatch: pytest.MonkeyPatch):
    """
    Молча пропущенный шаблон означает, что данные уедут на wiki открытым
    текстом. Поэтому это ошибка конфигурации, а не предупреждение в лог.
    """
    monkeypatch.setenv("CONFLUENCE_MASK_PATTERNS", "acc-(")

    with pytest.raises(cfg.ConfigError, match="CONFLUENCE_MASK_PATTERNS"):
        mask_text("Клиент acc-1024")


def test_empty_text_is_safe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_MASK_PATTERNS", r"acc-\d+")
    assert mask_text("") == ""
    assert mask_text(None) == ""


def test_mask_applies_to_the_published_page(monkeypatch: pytest.MonkeyPatch):
    """Проверка на месте применения: вопрос оператора тоже проходит через маску."""
    monkeypatch.setenv("CONFLUENCE_MASK_PATTERNS", r"acc-\d+")

    body = render_body(
        {
            "messages": [
                HumanMessage("Клиент acc-1024 жалуется на экспорт"),
                AIMessage("Проверил acc-1024, всё в порядке"),
            ],
            "usage": {},
        }
    )

    assert "acc-1024" not in body
    assert body.count("***") == 2

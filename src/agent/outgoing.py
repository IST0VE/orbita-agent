"""
Общая проверка исходящего текста: одна для всех, кто пишет наружу.

Маскирование в проекте было с самого начала, но жило внутри сборки документа
треда (`documents._markup`). Всё, что писало наружу мимо этой сборки, писало и
мимо маски: новая версия документа в графе обновления собирается из готового
текста, а описание задачи Jira — из полей плана. Синтетический ключ проходил
обоими путями до файла и до payload трекера.

Отсюда разделение на два шага, и они разные по смыслу.

    sanitize   заменить известные секреты на CONFLUENCE_MASK_REPLACEMENT —
               то же, что делала `confluence.mask_text`, но по любой
               структуре: строки, списки, словари, вложенные документы ADF;
    verify     перечитать то, что получилось, и сказать, что осталось.

Второй шаг не дублирует первый. Он смотрит на итоговый payload — тот самый
объект, который уйдёт в сеть, — и находит и пропущенные пути, и то, что
маскировать нельзя. Адрес с логином и паролем внутри (`https://u:p@host`)
переписывать нельзя: замена меняет адрес, то есть смысл. Такое блокируется.

Блокировка называет тип находки и поле, но не значение: сообщение об утечке,
в котором лежит утёкший ключ, уезжает в тред, в логи и в состояние, и это
второй экземпляр того же секрета.

Исходные материалы не правятся: проверка работает с копией, которая уедет.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from agent import config as cfg

#: Что умеем находить и заменять. Порядок важен: блок приватного ключа ищется
#: целиком, иначе от него останутся заголовок и хвост.
MASKED: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
)

#: Что находим, но не переписываем. Замена здесь испортила бы данные: адрес
#: без логина ведёт в другое место, и подменённый адрес хуже отсутствующего.
BLOCKED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("url-credentials", re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")),
)


class Finding(NamedTuple):
    """Находка: где и какого рода. Значения в ней нет намеренно."""

    field: str
    kind: str

    def describe(self) -> str:
        return f"{self.field or 'текст'}: {self.kind}"


class OutgoingBlocked(RuntimeError):
    """Отправлять нельзя: в подготовленном payload осталась находка."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        super().__init__(
            "проверка исходящего текста остановила отправку — "
            + "; ".join(sorted({finding.describe() for finding in findings}))
        )


def _user_patterns() -> list[str]:
    """Пользовательские шаблоны CONFLUENCE_MASK_PATTERNS — как и раньше."""
    return cfg.confluence_mask_patterns()


def mask_text(text: str) -> str:
    """
    Замаскировать в одной строке всё известное и всё заказанное оператором.

    Нерабочее регулярное выражение — ошибка конфигурации, а не повод пропустить
    шаблон: пропущенная маска означает, что данные уедут открытым текстом.
    """
    if not text:
        return text or ""
    replacement = cfg.confluence_mask_replacement()
    out = text
    for _, pattern in MASKED:
        out = pattern.sub(replacement, out)
    for pattern in _user_patterns():
        try:
            out = re.sub(pattern, replacement, out)
        except re.error as exc:
            raise cfg.ConfigError(
                f"CONFLUENCE_MASK_PATTERNS: не компилируется {pattern!r} ({exc})"
            ) from exc
    return out


def sanitize(value: Any, field: str = "") -> Any:
    """
    Копия значения с замаскированными секретами — включая вложенные структуры.

    Обходятся словари, списки и кортежи: описание задачи Cloud уезжает
    документом ADF, и текст в нём лежит на третьем уровне вложенности.
    Ключи словарей тоже проверяются: имя поля приходит из схемы проекта.
    """
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {mask_text(str(key)): sanitize(item, _below(field, key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        kind = type(value)
        return kind(sanitize(item, f"{field}[{number}]") for number, item in enumerate(value))
    return value


def verify(value: Any, field: str = "") -> list[Finding]:
    """Что осталось в готовом payload. Пустой список — отправлять можно."""
    found: list[Finding] = []
    if isinstance(value, str):
        for kind, pattern in (*MASKED, *BLOCKED):
            if pattern.search(value):
                found.append(Finding(field, kind))
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            found += verify(key, field)
            found += verify(item, _below(field, key))
        return found
    if isinstance(value, (list, tuple)):
        for number, item in enumerate(value):
            found += verify(item, f"{field}[{number}]")
    return found


def guard(value: Any, field: str = "") -> Any:
    """
    Подготовить значение к отправке: замаскировать и перечитать.

    Возвращает то, что можно отправлять. Находка после маскирования — повод
    не отправлять вовсе: либо её не умеют переписывать (адрес с паролем),
    либо маска настроена так, что не сработала.
    """
    prepared = sanitize(value, field)
    findings = verify(prepared, field)
    if findings:
        raise OutgoingBlocked(findings)
    return prepared


def _below(field: str, key: Any) -> str:
    name = str(key)
    return f"{field}.{name}" if field else name

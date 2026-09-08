"""
Изоляция окружения для тестов пакета.

`costmeter` читает из окружения ровно четыре переменные — `PRICE_*`, которыми
тариф из таблицы можно перебить руками. На машине разработчика они лежат
в `.env` агента и подхватываются его конфигом, а тесты пакета должны видеть
чистое окружение независимо от того, из какой папки их запустили: из корня
репозитория вместе со всеми остальными или из `packages/costmeter` отдельно.

Свой conftest, а не общий из корня: пакет обязан оставаться самодостаточным.
Он и его тесты переезжают в отдельный репозиторий копированием папки.
"""

from __future__ import annotations

import os

import pytest

_PREFIXES = ("PRICE_", "LLM_")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(_PREFIXES):
            monkeypatch.delenv(name, raising=False)

"""
Переопределения текущего хода: единственное место, где читается config прогона.

Модуль существует ради границы, а не ради двенадцати строк кода. `options()`
нужна и графу, и инструментам; инструменты после появления Jira и Confluence
переехали в `tools.py`, а `tools.py` импортировать `graph.py` не может — граф
импортирует роли, роли импортируют инструменты. Общая зависимость двух модулей
живёт под ними обоими, а не внутри одного из них.

Здесь же — провайдер и модель по умолчанию. Они лежат рядом с `options()` по
той же причине, по которой лежат рядом в любом коде: это две половины одного
ответа на вопрос «какой моделью считать». `options()` даёт выбор хода, эти две
строки — то, что подставляется, когда ход не выбрал ничего.

`graph.options` остался как имя: им пользуются другие графы и тесты.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agent import config as cfg

# Снимок окружения на момент импорта. Тред может выбрать своё через Options;
# менять модель внутри одного треда всё равно нельзя — сменится префикс
# и обнулится кеш. Цены читаются на каждом вызове: тарифы меняются чаще,
# чем перезапускается сервер.
PROVIDER = cfg.llm_provider()
MODEL = cfg.model_name()


def options(config: RunnableConfig | None = None) -> dict:
    """
    Переопределения на текущий ход.

    Два канала, потому что их два в самой LangGraph: `configurable` в config
    задаёт вызывающий код и веб-интерфейс, `context` — Studio и SDK. Значение
    из `configurable` важнее: оно ближе к месту вызова.
    """
    explicit = dict((config or {}).get("configurable") or {})
    try:
        from langgraph.runtime import get_runtime

        context = dict(get_runtime().context or {})
    except Exception:  # вне прогона графа рантайма нет — это нормально
        context = {}
    return {**context, **explicit}

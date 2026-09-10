"""
Переопределения текущего хода: единственное место, где читается config прогона.

Модуль существует ради границы, а не ради двенадцати строк кода. `options()`
нужна и графу, и инструментам; инструменты после появления Jira и Confluence
переехали в `tools.py`, а `tools.py` импортировать `graph.py` не может — граф
импортирует роли, роли импортируют инструменты. Общая зависимость двух модулей
живёт под ними обоими, а не внутри одного из них.

Здесь же — снимок провайдера и модели для демо. Вызовы модели читают
`LLM_MODEL` из конфигурации сервера; тред не может переопределить модель.

`graph.options` остался как имя: им пользуются другие графы и тесты.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agent import config as cfg

# Снимок окружения для совместимости с graph.MODEL / graph.PROVIDER и демо.
# Рабочие вызовы модели и подписи документов используют cfg.model_name().
PROVIDER = cfg.llm_provider()
MODEL = cfg.model_name()


def options(config: RunnableConfig | None = None) -> dict:
    """
    Переопределения на текущий ход.

    Два канала, потому что их два в самой LangGraph: `configurable` в config
    задаёт вызывающий код и веб-интерфейс, `context` — Studio и SDK. Значение
    из `configurable` важнее: оно ближе к месту вызова.

    Старое поле `model` игнорируется в обоих каналах: сохранённые настройки
    Assistant/Thread не должны перекрывать серверную `LLM_MODEL`.
    """
    explicit = dict((config or {}).get("configurable") or {})
    try:
        from langgraph.runtime import get_runtime

        context = dict(get_runtime().context or {})
    except Exception:  # вне прогона графа рантайма нет — это нормально
        context = {}
    chosen = {**context, **explicit}
    chosen.pop("model", None)
    return chosen

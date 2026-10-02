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
from agent import inputs
from agent.security import SERVICE_SUBJECT

# Снимок окружения для совместимости с graph.MODEL / graph.PROVIDER и демо.
# Рабочие вызовы модели и подписи документов используют cfg.model_name().
PROVIDER = cfg.default_llm_provider()
MODEL = cfg.default_model_name()


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
    _scope_folders(chosen, explicit or _run_configurable())
    return chosen


def nested(config: RunnableConfig | None = None) -> bool:
    """
    Идёт ли прогон вложенным — вызванным другим графом ради результата.

    Вложенный граф не публикует, не заводит задач, не запускает нагрузку и не
    спрашивает оператора: всё это он отдаёт вызывающему предложениями
    (`proposals.py`). Иначе под оркестратором оператор подтверждал бы одно и
    то же дважды — у вложенного графа и у внешнего, — а публикация уезжала бы
    дважды, если внешний публикует итог сам.

    Флаг может прислать и клиент: `configurable` сервер пропускает как есть.
    Это безопасно по построению: режим только убирает действия и ничего не
    разрешает сверх обычного прогона. Пауза оператора в нём остаётся — это
    не вопрос графа, а просьба человека.
    """
    return options(config).get("nested") is True


#: Ключи, в которых приезжает папка с материалами: у графа обновления их две.
FOLDER_KEYS = ("input_dir", "base_dir")


def _run_configurable() -> dict:
    """`configurable` идущего прогона — для вызова `options()` без config."""
    try:
        from langgraph.config import get_config

        return dict(get_config().get("configurable") or {})
    except Exception:  # вне прогона графа конфига нет — это нормально
        return {}


def _scope_folders(chosen: dict, configurable: dict) -> None:
    """
    Папка с материалами — файлы чата этого треда, а не имя из запроса.

    Имя папки присылает браузер, и раньше оно доезжало до графа как есть: любой
    вошедший мог вписать имя чужой папки и прочитать её прогоном. Теперь у
    пользователя папка одна — файлы его чата, — и берётся она из треда
    прогона. Тред и пользователя в `configurable` кладёт сервер LangGraph
    поверх присланного (`thread_id` дописывается последним, а
    `langgraph_auth_*` клиенту запрещены), и чужой тред он не запустит
    (`auth.py`). Поэтому здесь ни одно значение из запроса не читается.

    Админ-токену (`service`) и прогону без сервера — тестам, скриптам, демо —
    по-прежнему можно назвать папку задачи: у них нет чужих файлов. Имя
    `@chat` понимают все: так интерфейс просит файлы текущего чата.
    """
    thread = str(configurable.get("thread_id") or "")
    user = configurable.get("langgraph_auth_user_id")
    personal = bool(user) and user != SERVICE_SUBJECT
    chat = inputs.chat_task(thread) if thread else ""
    for key in FOLDER_KEYS:
        value = str(chosen.get(key) or "")
        if personal or inputs.is_chat(value):
            chosen[key] = chat

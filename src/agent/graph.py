"""
Конвейер проектной аналитики на LangGraph с учётом cache hit/miss.

На вход — задача и папка с материалами: требования, записи встреч, переписка,
выгрузки. На выходе — пять документов, каждый от своей роли (`roles.py`):
требования, контракт API, данные и события, архитектура, ревью с финальной
версией. Результат этапа целиком уезжает следующим по конвейеру.

Граф:

  START -> context -> requirements -> (tools -> requirements)*
        -> gate_api          -> api
        -> gate_data         -> data
        -> gate_architecture -> architecture
        -> gate_review       -> review
        -> remember -> approve -> publish -> END

Состояние копит статистику по токенам, чтобы после каждого прогона было видно,
сколько денег ушло и какая доля входа приехала из кеша.

Что делает каждая нода:

  context   подставляет в КОНЕЦ задачи список файлов (`inputs.py`), справку из
            базы знаний (`knowledge.py`) и то, что помним по проекту
            (`memory.py`). Именно в конец: префикс промпта обязан остаться
            неподвижным, иначе кеш обнулится и вся экономика проекта развалится;
  <роль>    вызов модели со стабильным префиксом этой роли. Первые несколько
            тысяч токенов префикса у всех пяти ролей побайтово одинаковы
            (`prompts.COMMON`), поэтому вторая и следующие роли попадают
            в кеш уже на первом своём вызове;
  tools     чтение файлов задачи. В этом конвейере — только для первой роли:
            остальные работают с документом аналитика, а не с сырыми
            материалами. Узел общий, а спрашивать его могут несколько ролей:
            у конвейера подготовки задачи (`prep_graph.py`) их две, и возврат
            адресуется по `state["stage"]`. Набор инструментов свой у каждого
            конвейера (`pipeline.Pipeline.tools`, `tools.py`);
  gate_*    ворота перед этапом. Считают бюджет и, если включено
            PIPELINE_REQUIRE_APPROVAL, показывают оператору результат
            предыдущего этапа и ждут подтверждения через `interrupt()`;
  remember  запись в долгую память: над чем работали и чем кончилось;
  approve   необязательная остановка на подтверждение перед публикацией
            (PUBLISH_REQUIRE_APPROVAL);
  publish   документы уезжают в цель из `publishers.py`: Confluence, файлы
            на диске или никуда.

Ворота бюджета стоят перед каждым обращением к модели. Кончились деньги на
третьем этапе — конвейер не падает: оставшиеся роли пропускаются, а то, что
успели выпустить, публикуется с честной отметкой о недоделанных этапах.

Настройки — провайдер, модель, адрес API, ключ, цены, имя агента — читаются из
окружения через `config.py`. В коде остались только значения по умолчанию.
Привязки к конкретному вендору нет: класс модели выбирается в `providers.py` по
переменной LLM_PROVIDER.

Код графа лежит в шести модулях, а `graph.py` остался их общим входом. Имя
`agent.graph` знают три соседних конвейера, полтора десятка тестов и
`langgraph.json`; менять его ради красоты дерева файлов незачем, а полторы
тысячи строк в одном файле мешали читать любую из шести частей:

  state.py      состояние конвейера и настройки хода;
  cost.py       учёт кеша и денег;
  documents.py  документы прогона и этапов, вопрос оператора без контекста;
  nodes.py      сами узлы: контекст, роли, память, подтверждение, публикация;
  routes.py     маршруты и два узла, которыми кончается прерванный ход;
  builder.py    сборка топологии — одна на все конвейеры проекта.
"""

from __future__ import annotations

# Реэкспорт. `agent.graph` — общий вход в код графа, и почти всё, что здесь
# перечислено, в самом этом модуле не используется: «не используется здесь» —
# это и есть определение фасада, поэтому F401 выключен на файл целиком.
# Приватные имена в списке (`_merge_usage`, `_usage_table`) — не оговорка:
# на них ссылаются тесты, и молча увести их в другой модуль значило бы
# сломать вызывающих ради чистоты списка.
# ruff: noqa: F401
from agent.builder import build_graph
from agent.cost import (
    cost_summary,
    estimate_cost,
    extract_usage,
    format_publication,
    format_usage,
    hit_rate,
    input_tokens,
    naive_cost,
)
from agent.documents import (
    CONTEXT_SEPARATOR,
    _usage_table,
    document_header,
    operator_question,
    page_title,
    render_body,
    render_document,
    render_pipeline_body,
    render_stage_body,
    split_turns,
    stage_pages,
    task_of,
    text_of,
)
from agent.nodes import (
    approval_of,
    approve_node,
    context_node,
    make_gate_node,
    make_role_node,
    model_for,
    publish_node,
    publish_plan,
    remember_node,
    trim_history,
    without_tool_calls,
)
from agent.pipeline import Pipeline
from agent.routes import (
    Admission,
    budget_gate,
    halted_node,
    make_entry_router,
    make_gate_router,
    make_no_input_node,
    make_role_router,
    make_tools_router,
    over_budget_node,
)
from agent.runtime import MODEL, PROVIDER, options
from agent.state import USAGE_KEYS, Options, State, _merge_artifacts, _merge_usage
from agent.tools import FILE_TOOLS as TOOLS
from agent.tools import list_task_files, read_task_file

# Для Studio / langgraph dev: компилируем БЕЗ чекпоинтера.
# Персистентность на сервере своя, свой чекпоинтер тут только конфликтует.
graph = build_graph().compile()

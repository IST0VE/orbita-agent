"""
Ноды конвейера: всё, что делает ход, и ничего о том, в каком порядке.

Порядок собирает `builder.py`, решения о ходе принимает `routes.py`, а здесь
лежит работа: подставить контекст, позвать модель, записать в память,
спросить оператора, опубликовать. Разделение не косметическое — у нод есть
побочные эффекты и сеть, у сборки их нет, и путать эти два вида кода
в одном файле значит проверять их одинаково трудно.

Узлы этапов порождаются фабрикой, а не пишутся руками: пять этапов
отличаются только данными роли, и переписанный пятый раз узел — это пятый
шанс ошибиться в одном и том же месте. То же с воротами.

Модель собирается один раз на набор «провайдер, модель, temperature,
инструменты» и лежит в кеше клиентов: пересобирать её на каждый узел значит
заново поднимать HTTP-клиент пять раз за прогон.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from agent import config as cfg
from agent import (
    inputs,
    knowledge,
    memory,
    pause,
    providers,
    roles,
    sources,
    tool_compat,
)
from agent.cost import charge, cost_summary, extract_usage
from agent.documents import (
    operator_question,
    split_turns,
    task_of,
    text_of,
)
from agent.pipeline import Pipeline
from agent.runtime import options
from agent.state import State, _merge_spend, _merge_usage
from agent.tools import FILE_TOOLS as TOOLS

# Провайдер, модель, ключ (LLM_API_KEY), адрес API (LLM_API_BASE — прокси,
# self-hosted шлюз, совместимый эндпоинт), temperature, число ретраев, лимит
# выхода и таймаут приезжают из окружения. Провайдера, модель и temperature
# можно настроить на сервере; на тред переопределяются только провайдер
# и temperature — см. Options. Модель всегда берётся из LLM_MODEL.
# Ключ кеша — провайдер, модель, temperature И набор инструментов: конвейеры
# отличаются источником данных, а привязка инструментов меняет форму запроса.
# Без набора в ключе граф подготовки получил бы клиента, собранного для графа
# аналитики, и ходил бы в Jira инструментами, которых у него нет.
_BOUND: dict[tuple[str, str, float, tuple[str, ...]], Any] = {}


def model_for(config: RunnableConfig | None = None, tools: list | None = None) -> Any:
    """
    Чат-модель с привязанными инструментами под текущий ход.

    Собирается лениво: на импорте модуля ключа может не быть вообще — тесты
    и линтер импортируют `agent.graph`, но ни одного хода не делают. И
    кешируется по ключу (провайдер, модель, temperature, набор инструментов):
    клиент создаётся один раз на комбинацию, но выбирается в момент вызова ноды.

    tools — набор конвейера. Не передан — файловые: так вызывали до того, как
    наборов стало больше одного. Пустой набор означает «не привязывать
    вовсе», и это не то же самое, что привязать пустой список: набор уезжает
    в тело запроса, и эндпоинт, который не умеет tool calling, отказывает
    и на пустом.
    """
    chosen = options(config)
    tools = TOOLS if tools is None else tools
    key = (
        *providers.resolve_key(
            provider=chosen.get("provider"), temperature=chosen.get("temperature")
        ),
        tuple(item.name for item in tools),
    )
    bound = _BOUND.get(key)
    if bound is None:
        model = providers.build_llm(*key[:3])
        bound = model.bind_tools(tools) if tools else model
        _BOUND[key] = bound
    return bound


def trim_history(messages: list) -> list:
    """
    Подрезать историю до LLM_MAX_HISTORY_TOKENS. 0 — не трогать (по умолчанию).

    Внимание: на работающем кеше тримминг обходится дороже, чем его отсутствие
    — каждый выброшенный ход сдвигает префикс и обнуляет совпадение. Цифры
    и условия, при которых он всё-таки нужен, — в config.max_history_tokens()
    и в README.

    Считается только история; системный префикс в state["messages"] не лежит
    и под лимит не попадает — он и должен оставаться нетронутым, иначе
    сломается ровно тот кеш, ради которого всё затевалось. `start_on="human"`
    не даёт срезать историю посреди пары «вызов инструмента — ответ».
    """
    limit = cfg.max_history_tokens()
    if not limit:
        return messages
    kept = trim_messages(
        messages,
        max_tokens=limit,
        token_counter=count_tokens_approximately,
        strategy="last",
        start_on="human",
        include_system=False,
        allow_partial=False,
    )
    if kept or not messages:
        return kept
    # Ни одно окно не начинается с человеческого сообщения — значит, оно в
    # истории одно и стоит первым. Так выглядит расследование НТ: бриф, а за ним
    # только ходы модели и ответы инструментов. Пустая история здесь — это вызов
    # модели без задачи и без прочитанного: она отвечает наугад, а платит за это
    # оператор. Поэтому задача остаётся, а режется середина переписки.
    head, rest = messages[:1], messages[1:]
    return head + trim_messages(
        rest,
        max_tokens=max(limit - count_tokens_approximately(head), 0),
        token_counter=count_tokens_approximately,
        strategy="last",
        start_on=("human", "ai"),
        include_system=False,
        allow_partial=False,
    )


def without_tool_calls(message: Any) -> Any:
    """
    Копия ответа без вызовов инструментов. Ответ без вызовов возвращается как есть.

    Снимается и разобранная форма (`tool_calls`), и сырая, в которой её прислал
    провайдер (`additional_kwargs`): по второй некоторые клиенты восстанавливают
    первую, и снятый наполовину вызов вернулся бы на следующем чтении истории.

    Висячий вызов в истории — это не только потерянный документ. Провайдер
    требует, чтобы за каждым вызовом шёл ответ инструмента, и запрос с висячим
    вызовом отбивается ошибкой у того, кто эту историю потом прочитает.
    """
    if not getattr(message, "tool_calls", None):
        return message

    extra = dict(getattr(message, "additional_kwargs", None) or {})
    extra.pop("tool_calls", None)
    return message.model_copy(
        update={"tool_calls": [], "invalid_tool_calls": [], "additional_kwargs": extra}
    )


def make_role_node(
    role: roles.Role,
    unstable_prefix: bool = False,
    llm: Any = None,
    pipeline: Pipeline = roles.PIPELINE,
):
    """
    Узел одного этапа конвейера.

    Роли отличаются тремя вещами: префиксом, тем, чьи документы им нужны, и
    правом читать файлы задачи. Всё это лежит в `roles.py`, поэтому здесь одна
    фабрика на пять узлов, а не пять почти одинаковых функций.

    unstable_prefix=True — намеренный антипаттерн для демонстрации:
    в начало промпта подставляется текущее время, из-за чего префикс
    меняется на каждом запросе и кеш перестаёт срабатывать вообще.

    llm — готовая модель вместо собранной из окружения. Нужна тестам, чтобы
    прогнать граф целиком на подделке, без ключа и без сети. Инструменты к ней
    не привязываются: что передали, то и вызывается.
    """

    def role_node(state: State, config: RunnableConfig) -> dict:
        # Остановленный конвейер больше не платит за модель. Проверка стоит
        # первой строкой узла и потому работает в любом графе: у собранных
        # общим сборщиком остановку перехватит роутер и уведёт в `halted`, у
        # конвейеров со своей топологией (НТ) ветки `halted` нет — и роль,
        # которая не смотрит на `halt` сама, честно сходила бы в модель после
        # того, как оператор сказал «хватит».
        if state.get("halt"):
            return {}

        # Пауза оператора: единственная остановка конвейера, о которой заранее
        # не договаривались. Стоит перед сборкой сообщения, а не после: смысл
        # паузы в том, чтобы дописанное оператором попало в ЭТОТ запрос, а не
        # в следующий. Заявки нет — не стоит ничего (`pause.checkpoint`).
        paused = pause.checkpoint(
            state, config, stage=role.key, title=role.title, pipeline=pipeline
        )
        if paused.get("halt"):
            return paused
        # Указание из только что снятой паузы в состоянии ещё не лежит: оно
        # уедет туда этим же обновлением. Роль обязана увидеть его сразу —
        # иначе первый же ответ на паузу пришёл бы на этап позже, чем его дали.
        notes = [*(state.get("notes") or []), *paused.get("notes", [])]

        # Из чего собран префикс, знает конвейер: у аналитики он зависит от
        # режима базы знаний, у разбора схем — нет. Узлу важно одно: внутри
        # треда префикс не меняется, иначе кеш не засчитает совпадение.
        prefix = pipeline.prompt_for(role.key)
        if unstable_prefix:
            prefix = f"Текущее время: {datetime.now().isoformat(timespec='seconds')}\n\n" + prefix

        # Потолок ходов в инструменты. Роль решает «спросить ещё раз» сама, и
        # без потолка петля упирается только в recursion_limit LangGraph —
        # тысячи оплаченных вызовов и обрыв ошибкой мимо публикации сделанного.
        # Исчерпав его, роль делает последний ход БЕЗ инструментов: так же
        # кончается расследование в конвейере НТ. Схемы при этом не
        # отвязываются — они часть кешируемого префикса, и снять их значило бы
        # оплатить весь префикс заново; снимается только право спрашивать.
        limit = cfg.tool_turns_per_run()
        used = int(state.get("tool_turns") or 0)
        asking = role.reads_files and (limit <= 0 or used < limit)

        if role.reads_files:
            # Роль с инструментами ведёт переписку: её вопрос к файлам и ответы
            # файлов обязаны остаться в истории, иначе следующий заход в ноду
            # не увидит, что уже прочитано. Берётся только текущий ход —
            # прошлые прогоны конвейера этой роли не нужны, а тащить их значило
            # бы платить за них на каждом вызове.
            turns = split_turns(state.get("messages") or [])
            history = trim_history(turns[-1] if turns else [])
            if not asking:
                # Предупреждение уезжает в КОНЕЦ переписки, а не в префикс:
                # префикс обязан остаться побайтово тем же, иначе последний
                # ход роли оплачивается по полной вместе со всем кешем.
                history = [
                    *history,
                    HumanMessage(
                        content=(
                            f"Достигнут предел обращений к инструментам за прогон "
                            f"({limit}, TOOL_TURNS_PER_RUN). Это последний ход: "
                            "инструменты недоступны. Напиши документ по тому, что "
                            "уже прочитано, и отметь в нём, чего не успел посмотреть."
                        )
                    ),
                ]
            # Указания, добавленные оператором на паузах, — отдельным ходом
            # человека в конце переписки. Дописывать их внутрь уже собранного
            # хода нельзя: это разорвало бы пару «вызов инструмента — ответ».
            if block := pause.notes_block(notes):
                history = [*history, HumanMessage(content=block.strip())]
        else:
            # Остальным сообщение собирается заново из задачи и документов
            # предыдущих этапов. Переписка аналитика с файлами им не нужна:
            # их вход — его документ, а не то, как он его добывал. Указания
            # оператора приписываются к нему в конец — туда же, куда нода
            # контекста кладёт справку и список файлов, и ровно по той же
            # причине.
            history = [
                HumanMessage(
                    content=pipeline.brief(role, task_of(state), state.get("artifacts"))
                    + pause.notes_block(notes)
                )
            ]

        # SystemMessage подставляется здесь и НЕ хранится в state.messages —
        # так гарантировано, что префикс каждый раз побайтово одинаковый.
        # Инструменты привязываются всем ролям конвейера — иначе у ролей
        # был бы разный кешируемый префикс. Но конвейеру, в котором в цикл
        # с инструментами не уходит НИ ОДНА роль, привязка не даёт ничего:
        # спрашивать некому, а схемы всё равно уезжают в каждый запрос. Тот
        # же довод, по которому сборщик не заводит таким конвейерам узел
        # `tools`, — и та же проверка, чтобы эти два решения не разошлись.
        #
        # Разница видна не только в счёте. Эндпоинт без поддержки tool calling
        # отказывает на любом запросе со схемами, и три конвейера из пяти
        # переставали работать там, где им инструменты не нужны вовсе.
        tools = (pipeline.tools or TOOLS) if pipeline.has_tools else ()
        messages = [SystemMessage(content=prefix)] + history
        if llm is not None:
            response = llm.invoke(messages)
        else:
            response = tool_compat.invoke(
                model_for, messages, config, tools, allow_tools=asking
            )

        # Роль без права спрашивать тоже может позвать инструмент: схемы
        # привязаны ко всем ролям конвейера ради общего префикса, и модель
        # иногда ими пользуется. Вести её в `tools` нельзя — вход ей собирается
        # заново, ответа инструмента она уже не увидит, — поэтому вызов
        # снимается, а написанное остаётся документом этапа. Без этого
        # документ, за который заплачено, молча пропадал бы, а следующая роль
        # получала бы «этап не выполнен». На исчерпанном потолке довод тот же,
        # и он же закрывает петлю: без вызовов роутер уводит на следующий этап.
        if not asking:
            response = without_tool_calls(response)

        # Счётчики за этот вызов уедут в редьюсер, а деньги нужны уже готовыми:
        # складываем ровно то же, что сложат редьюсеры. Деньги считаются здесь,
        # тарифом этого вызова, и дальше только складываются: пересчёт итоговых
        # счётчиков текущей ценой переоценил бы историю при смене модели.
        turn = extract_usage(response)
        money = charge(turn, state=state)
        update = {
            # Приписка с паузы уезжает в состояние тем же обновлением, что и
            # ответ роли: до него она жила только в локальной переменной, и
            # следующий этап не увидел бы её вовсе.
            **paused,
            "messages": [response],
            "usage": turn,
            "spend": money,
            "cost": cost_summary(
                _merge_usage(state.get("usage"), turn), _merge_spend(state.get("spend"), money)
            ),
            "stage": role.key,
        }

        # Документ этапа — это финальный текст роли. Пока она зовёт инструменты,
        # документа ещё нет: она не ответила, а спросила.
        text = text_of(response)
        if getattr(response, "tool_calls", None):
            update["tool_turns"] = used + 1
        elif text:
            update["artifacts"] = {role.key: text}
        return update

    return role_node



def context_node(state: State, config: RunnableConfig, *, external_sources: bool = False) -> dict:
    """
    Подставить в КОНЕЦ задачи список файлов, справку из базы знаний и то, что
    помним по проекту.

    Здесь же задача запоминается отдельным полем состояния: дальше её читает
    каждая из пяти ролей, и вычленять её из истории по-своему они не должны.

    Почему в конец и почему прямо в сообщение, а не в промпт: кеш провайдера
    считает совпадение от нулевого токена. Всё найденное, вставленное в начало,
    сдвинуло бы префикс и обнулило кеш на каждом ходе. А записать дополненный
    вопрос обратно в состояние обязательно — иначе на следующем ходе история
    поехала бы уже без справки, и префикс всё равно сместился бы, только
    незаметно.

    Сообщение заменяется по своему же id: `add_messages` понимает это как
    правку, а не как новую реплику.

    Здесь же обнуляется счётчик ходов в инструменты. Нода контекста стоит
    первой на каждом прогоне, и это единственное место, которое знает, что
    начался новый ход оператора: потолок ограничивает прогон, а не тред,
    иначе второй запрос в том же треде достался бы роли без инструментов.
    """
    # Счётчик обнуляется на любом исходе ноды: не состоявшаяся подстановка —
    # это всё равно начало нового прогона.
    fresh = {"tool_turns": 0}
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if last is None or getattr(last, "type", "") != "human":
        return fresh

    question = text_of(last)
    if (
        knowledge.BLOCK_TITLE in question
        or memory.BLOCK_TITLE in question
        or inputs.BLOCK_TITLE in question
        or sources.LINKS_TITLE in question
    ):
        return fresh  # справка уже подставлена: повторный вход в ноду

    task = operator_question(question)

    blocks = []
    if external_sources:
        blocks.append(sources.linked_context(task))
    if cfg.knowledge_enabled():
        blocks.append(knowledge.block_for(question))
    if cfg.memory_enabled():
        blocks.append(memory.block_for(question))
    # Список файлов выбранной задачи — имена и размеры, без содержимого:
    # читать файл будет инструмент, и только тот, который понадобился.
    chosen = options(config)
    blocks.append(
        inputs.block_for(
            str(chosen.get("input_dir") or ""),
            chosen.get("input_file") or (),
        )
    )

    addition = "".join(block for block in blocks if block)
    if not addition:
        return {**fresh, "task": task}
    return {
        **fresh,
        "task": task,
        "messages": [HumanMessage(content=question + addition, id=last.id)],
    }


def remember_node(state: State, config: RunnableConfig) -> dict:
    """
    Записать в долгую память, о чём был ход.

    Без дополнительного вызова модели: строка собирается из уже готовых
    вопроса и ответа. Лишний вызов стоил бы денег на каждом ходе и не добавлял
    бы ничего, чего в тексте уже нет.
    """
    if not cfg.memory_enabled():
        return {}

    turns = split_turns(state.get("messages") or [])
    if not turns:
        return {}

    turn = turns[-1]
    question = next(
        (operator_question(text_of(m)) for m in turn if getattr(m, "type", "") == "human"),
        "",
    )
    answer = next((text_of(m) for m in reversed(turn) if getattr(m, "type", "") == "ai"), "")
    if not question or not answer:
        return {}

    # Аккаунт ищется и в вопросе, и в аргументах вызванных инструментов:
    # оператор мог назвать его один раз, а модель — подставить в запрос.
    haystack = " ".join(
        [question]
        + [str(call.get("args")) for m in turn for call in (getattr(m, "tool_calls", None) or [])]
    )
    fact = memory.fact_from(question, answer)
    for account in memory.accounts_in(haystack):
        memory.remember(account, fact)
    return {}


# --------------------------------------------------------------------------
# Ворота этапов
#
# Здесь LangGraph делает то, чего последовательный запуск ролей не умеет в
# принципе: между этапами можно вклиниться. Требования, написанные по неверно
# понятой задаче, стоят не одного вызова модели, а четырёх — по одному на
# каждый следующий этап, — и ещё рабочего дня человека, который будет читать
# получившуюся архитектуру. Ворота дают остановиться на первой странице.
#
# По умолчанию ворота молчат: PIPELINE_REQUIRE_APPROVAL выключен, и конвейер
# идёт насквозь. Включённые — показывают документ предыдущего этапа и ждут
# `Command(resume=...)`, как и подтверждение публикации.
# --------------------------------------------------------------------------
def make_gate_node(role: roles.Role, pipeline: Pipeline = roles.PIPELINE):
    """Ворота перед `role`: подтверждение документа предыдущего этапа."""
    previous = pipeline.before(role)

    def gate_node(state: State, config: RunnableConfig) -> dict:
        # Уже остановленный конвейер подтверждать нечего: решение оператор
        # принял на паузе, и второй вопрос про тот же документ — это вопрос,
        # на который он только что ответил.
        if state.get("halt"):
            return {}
        if not cfg.pipeline_require_approval():
            return {}

        artifact = ((state.get("artifacts") or {}).get(previous.key) or "").strip()
        if not artifact:
            # Предыдущий этап не состоялся — подтверждать нечего. Спрашивать
            # оператора про пустой документ значит будить его зря.
            return {}

        answer = interrupt(
            {
                "action": "stage",
                "stage": previous.key,
                "next": role.key,
                "title": f"{previous.number}. {previous.title}",
                # Документ этапа как есть, в Markdown: до разметки цели
                # публикации он ещё не доехал и доедет только в конце.
                "format": "markdown",
                "document": artifact,
                "hint": (
                    "ответьте true, чтобы продолжить конвейер, или "
                    '{"decision": "rejected", "reason": ...}, чтобы остановить'
                ),
            }
        )

        decision = approval_of(answer)
        if decision["decision"] == "approved":
            return {}
        return {
            "halt": {
                "stage": previous.key,
                "reason": decision.get("reason") or "оператор остановил конвейер",
            }
        }

    return gate_node


# --------------------------------------------------------------------------
# Публикация
#
# Переехала в `publish_nodes.py`: узлы ролей отвечают на вопрос «что написать»,
# публикация — «куда это уедет и кто на это согласился». Имена остаются здесь
# ради вызывающих: на `nodes.publish_node` ссылаются сборка графа, три
# конвейера и тесты, и переименовывать их ради перемещения файла незачем.
# --------------------------------------------------------------------------
from agent.publish_nodes import (  # noqa: E402
    approval_of,
    approve_node,
    commitment_changes,
    commitment_digest,
    prepare_node,
    publish_commitment,
    publish_node,
    publish_plan,
)

__all__ = [
    "approval_of",
    "approve_node",
    "commitment_changes",
    "commitment_digest",
    "context_node",
    "make_gate_node",
    "make_role_node",
    "model_for",
    "prepare_node",
    "publish_commitment",
    "publish_node",
    "publish_plan",
    "remember_node",
    "text_of",
    "trim_history",
    "without_tool_calls",
]

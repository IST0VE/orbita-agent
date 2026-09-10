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

import hashlib
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from agent import config as cfg
from agent import (
    confluence,
    drafts,
    inputs,
    knowledge,
    memory,
    providers,
    publishers,
    roles,
    sources,
    tool_compat,
)
from agent.cost import cost_summary, extract_usage
from agent.documents import (
    document_header,
    operator_question,
    page_title,
    render_body,
    render_pipeline_body,
    split_turns,
    stage_pages,
    task_of,
    text_of,
)
from agent.pipeline import Pipeline
from agent.runtime import options
from agent.state import State, _merge_usage
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
    return trim_messages(
        messages,
        max_tokens=limit,
        token_counter=count_tokens_approximately,
        strategy="last",
        start_on="human",
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
        # Из чего собран префикс, знает конвейер: у аналитики он зависит от
        # режима базы знаний, у разбора схем — нет. Узлу важно одно: внутри
        # треда префикс не меняется, иначе кеш не засчитает совпадение.
        prefix = pipeline.prompt_for(role.key)
        if unstable_prefix:
            prefix = f"Текущее время: {datetime.now().isoformat(timespec='seconds')}\n\n" + prefix

        if role.reads_files:
            # Роль с инструментами ведёт переписку: её вопрос к файлам и ответы
            # файлов обязаны остаться в истории, иначе следующий заход в ноду
            # не увидит, что уже прочитано. Берётся только текущий ход —
            # прошлые прогоны конвейера этой роли не нужны, а тащить их значило
            # бы платить за них на каждом вызове.
            turns = split_turns(state.get("messages") or [])
            history = trim_history(turns[-1] if turns else [])
        else:
            # Остальным сообщение собирается заново из задачи и документов
            # предыдущих этапов. Переписка аналитика с файлами им не нужна:
            # их вход — его документ, а не то, как он его добывал.
            history = [
                HumanMessage(content=pipeline.brief(role, task_of(state), state.get("artifacts")))
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
                model_for, messages, config, tools, allow_tools=role.reads_files
            )

        # Роль без инструментов тоже может их позвать: схемы привязаны ко всем
        # ролям конвейера ради общего префикса, и модель иногда ими пользуется.
        # Вести её в `tools` нельзя — вход ей собирается заново, ответа
        # инструмента она уже не увидит, — поэтому вызов снимается, а
        # написанное остаётся документом этапа. Без этого документ, за который
        # заплачено, молча пропадал бы, а следующая роль получала бы
        # «этап не выполнен».
        if not role.reads_files:
            response = without_tool_calls(response)

        # Счётчики за этот вызов уедут в редьюсер, а деньги нужны уже готовыми:
        # складываем ровно то же, что сложит редьюсер, и переводим в доллары.
        turn = extract_usage(response)
        update = {
            "messages": [response],
            "usage": turn,
            "cost": cost_summary(_merge_usage(state.get("usage"), turn)),
            "stage": role.key,
        }

        # Документ этапа — это финальный текст роли. Пока она зовёт инструменты,
        # документа ещё нет: она не ответила, а спросила.
        text = text_of(response)
        if text and not getattr(response, "tool_calls", None):
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
    """
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if last is None or getattr(last, "type", "") != "human":
        return {}

    question = text_of(last)
    if (
        knowledge.BLOCK_TITLE in question
        or memory.BLOCK_TITLE in question
        or inputs.BLOCK_TITLE in question
        or sources.LINKS_TITLE in question
    ):
        return {}  # справка уже подставлена: повторный вход в ноду

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
        return {"task": task}
    return {
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
# Публикация
#
# Куда именно уедет документ, решает `publishers.py`; здесь остаётся решение,
# уезжать ли ему вообще. Оба решения нужны дважды — в ноде подтверждения и в
# самой публикации, — поэтому они собраны в один «план».
# --------------------------------------------------------------------------
def publish_plan(
    state: State,
    config: RunnableConfig | None = None,
    pipeline: Pipeline = roles.PIPELINE,
) -> dict:
    """
    Что нода публикации сделает на этом ходе: цель, готовые страницы, общий
    хеш и причина пропуска, если публиковать не нужно.
    """
    publisher = publishers.current()
    header = document_header(config, publisher.renderer, pipeline)
    pages = stage_pages(state, config, publisher.renderer, header, pipeline)

    if not pages:
        # Ни один этап не состоялся: конвейер упёрся в бюджет на первой же роли
        # или тред пришёл вообще без прогона. Публиковать всё равно есть что —
        # переписку и расход, — и промолчать здесь хуже, чем показать пустой
        # результат: человек должен увидеть, что прогон был и чем кончился.
        body = render_body(state, publisher.renderer)
        pages = [
            {
                "role": "",
                "title": page_title(state, config or {}),
                "document": publisher.renderer.join([header, body]),
                "digest": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        ]

    # Общий хеш — по хешам страниц: режим `changed` смотрит на конвейер целиком.
    # Публиковать заново только изменившуюся страницу из пяти можно было бы и
    # точнее, но тогда `document_hash` пришлось бы вести по каждой, а весь
    # выигрыш достаётся случаю, которого не бывает: этапы меняются вместе.
    digest = hashlib.sha256("".join(page["digest"] for page in pages).encode("utf-8")).hexdigest()

    # Документ треда целиком: показывается оператору на подтверждении и
    # остаётся в состоянии для интерфейса. Собирается заново, а не склейкой
    # страниц: там задача и таблица расходов повторились бы по разу на этап.
    whole = (
        publisher.renderer.join([header, render_pipeline_body(state, publisher.renderer, pipeline)])
        if state.get("artifacts")
        else pages[0]["document"]
    )

    return {
        "publisher": publisher,
        "pages": pages,
        "document": whole,
        "digest": digest,
        "skip": _publish_skip(state, config, digest, publisher),
    }


def _publish_skip(
    state: State,
    config: RunnableConfig | None,
    digest: str,
    publisher: publishers.Publisher,
) -> tuple[str, str] | None:
    """Причина не публиковать в виде пары «статус — объяснение», или None."""
    if not publishers.is_enabled():
        return ("disabled", "этап выключен через CONFLUENCE_PUBLISH")
    if publisher.name == "none":
        return ("disabled", "PUBLISH_TARGET=none: документы собраны, но никуда не уходят")

    mode = cfg.confluence_publish_mode()
    if mode == "manual" and not options(config).get("publish"):
        return ("postponed", "режим manual: публикация не запрошена")
    if mode == "changed" and digest == state.get("document_hash"):
        return ("unchanged", "документы не изменились с прошлой публикации")

    absent = publisher.missing()
    if absent:
        return ("skipped", "не заданы в .env: " + ", ".join(absent))
    return None


def approval_of(answer: Any) -> dict:
    """
    Ответ оператора — в решение.

    Интерфейсы возвращают разное: Studio — введённый JSON, чат — строку, свой
    код — просто True. Понимаем все три, потому что человеку на другом конце
    не должно быть важно, чем он пользуется.
    """
    if isinstance(answer, dict):
        raw = answer.get("decision", answer.get("approved"))
        reason = str(answer.get("reason", "") or "")
    else:
        raw, reason = answer, ""

    if isinstance(raw, str):
        approved = raw.strip().lower() in {"approve", "approved", "yes", "y", "да", "ок"}
    else:
        approved = bool(raw)
    return {"decision": "approved" if approved else "rejected", "reason": reason}


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


def approve_node(state: State, config: RunnableConfig, pipeline: Pipeline = roles.PIPELINE) -> dict:
    """
    Остановка на подтверждение оператором перед публикацией.

    `interrupt()` замораживает тред и отдаёт наружу собранные документы;
    возобновление — `Command(resume=...)`. В Studio и в чат-интерфейсе это
    работает без дополнительного кода, поэтому кнопки писать не нужно.

    Оператора не дёргают зря: если публикация и так не состоится (выключена,
    отложена, документы не изменились), нода молча пропускает ход.
    """
    if not cfg.publish_require_approval():
        return {}

    plan = publish_plan(state, config, pipeline)
    if plan["skip"]:
        return {}

    answer = interrupt(
        {
            "action": "publish",
            "target": plan["publisher"].name,
            # Разметка документа: интерфейсу нужно знать, показывать его как
            # Markdown или как XHTML. Выводить это из имени цели — значит
            # завести знание о публикаторах на другом конце провода.
            "format": plan["publisher"].renderer.name,
            "title": page_title(state, config),
            "pages": [page["title"] for page in plan["pages"]],
            # Черновики страниц: тело каждой в том виде, в каком она уедет, и
            # судьба заголовка — создастся страница или перезапишет чужую.
            # Склеенный документ рядом остаётся: по нему конвейер читают
            # целиком, а решение принимают по страницам (см. drafts.py).
            "drafts": drafts.pages(plan),
            "document": plan["document"],
            "hint": 'ответьте true/false или {"decision": "rejected", "reason": ...}',
        }
    )
    decision = approval_of(answer)
    if isinstance(answer, dict) and answer.get("decision") == "drafts":
        decision["decision"] = "drafts"
    return {"approval": decision}


def _rollup(results: list[dict]) -> str:
    """Один статус на всю публикацию по статусам отдельных страниц."""
    failed = [r for r in results if r.get("status") == "failed"]
    if failed:
        return "failed" if len(failed) == len(results) else "partial"
    return "created" if all(r.get("status") == "created" for r in results) else "updated"


def publish_node(state: State, config: RunnableConfig, pipeline: Pipeline = roles.PIPELINE) -> dict:
    """
    Финальный этап: разложить документы конвейера по страницам цели публикации.

    Страниц теперь столько, сколько этапов состоялось, и падение одной не
    отменяет остальные: у каждой свой upsert по заголовку. Сеть отваливается,
    токены протухают, диск кончается — ронять из-за этого весь прогон нельзя,
    документы к этому моменту уже написаны и оплачены. Поэтому любая проблема
    оседает в state["publication"], а граф идёт в END.
    """
    title = page_title(state, config)
    plan = publish_plan(state, config, pipeline)
    document = plan["document"]

    def skip(status: str, reason: str) -> dict:
        """Документы обновляем всегда, хеш — нет: он описывает опубликованное."""
        return {
            "document": document,
            "publication": {"status": status, "title": title, "reason": reason},
        }

    if plan["skip"]:
        return skip(*plan["skip"])

    decision = state.get("approval") or {}
    if cfg.publish_require_approval() and decision.get("decision") == "drafts":
        if plan["publisher"].name != "confluence":
            return skip("failed", "внешние черновики доступны только для Confluence")
        results = []
        for page in plan["pages"]:
            try:
                result = confluence.create_draft(
                    page["title"],
                    page["document"].replace(
                        "Правки руками затрёт следующий прогон треда.",
                        "Черновик для проверки. Правки и публикация выполняются в Confluence.",
                        1,
                    ),
                    draft_key=page["digest"],
                )
            except confluence.ConfluenceError as exc:
                result = {"status": "failed", "title": page["title"], "reason": str(exc)}
            results.append({**result, "role": page["role"]})
        failed = sum(item["status"] == "failed" for item in results)
        return {
            "document": document,
            "publication": {
                "status": "drafts" if not failed else "failed" if failed == len(results) else "partial",
                "title": title,
                "pages": results,
                "reason": "Откройте черновики по ссылкам. Правки и публикация — в Confluence.",
            },
        }

    if cfg.publish_require_approval():
        if decision.get("decision") != "approved":
            return skip(
                "rejected",
                decision.get("reason") or "оператор не подтвердил публикацию",
            )

    results = []
    for page in plan["pages"]:
        try:
            result = dict(plan["publisher"].publish(page["title"], page["document"]))
        except publishers.PublishError as exc:
            result = {"status": "failed", "title": page["title"], "reason": str(exc)}
        result["role"] = page["role"]
        results.append(result)

    status = _rollup(results)
    publication = {"status": status, "title": title, "pages": results}

    # Причина — только когда есть о чём говорить, и собранная из страниц:
    # «не удалось» без указания, какая именно, отправляет человека читать логи.
    broken = [r for r in results if r.get("status") == "failed"]
    if broken:
        publication["reason"] = "; ".join(
            f"{r['title']}: {r.get('reason', 'без причины')}" for r in broken
        )

    update = {"document": document, "publication": publication}
    # Хеш обновляем, только если уехало всё: иначе следующий прогон в режиме
    # `changed` решил бы, что публиковать нечего, и упавшая страница осталась
    # бы ненаписанной навсегда.
    if not broken:
        update["document_hash"] = plan["digest"]
    return update

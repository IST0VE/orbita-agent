"""
Документы конвейера: всё, что превращает готовое состояние в текст.

Собирается без единого дополнительного вызова модели: весь текст уже написан
ролями, и просить модель «оформить» его значило бы платить за перестановку
абзацев. Поэтому здесь только разметка, заголовки и склейка — цена этого
модуля равна нулю, и так и должно остаться.

Два вида результата. Документ прогона — вся история одним текстом, с задачей
в шапке и таблицей расхода в конце. Документы этапов — по одному на роль,
каждый самостоятельный: за контрактом API приходят к странице API, а не
к истории проекта, поэтому задача повторяется в шапке каждого. Дублирование
намеренное.

Здесь же — вопрос оператора без того, что мы к нему дописали
(`operator_question`). Он лежит рядом с `text_of` и `task_of` не случайно:
все трое отвечают на один вопрос «что именно спросил человек», и разошлись
бы по разным модулям только затем, чтобы разойтись и по смыслу.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from langchain_core.runnables import RunnableConfig

from agent import config as cfg
from agent import confluence, jira, render, roles
from agent.cost import estimate_cost, hit_rate
from agent.pipeline import Pipeline
from agent.runtime import MODEL, options
from agent.state import State

# Этой строкой начинаются оба подставляемых блока — и справка из базы знаний,
# и память по аккаунту. По ней же вопрос оператора отделяется обратно: в память
# должно уехать то, что он спросил, а не то, что мы сами дописали.
CONTEXT_SEPARATOR = "\n\n---\n"


def operator_question(text: str) -> str:
    """Вопрос без подставленного контекста."""
    return text.split(CONTEXT_SEPARATOR, 1)[0].strip()



def text_of(message: Any) -> str:
    content = getattr(message, "content", "") or ""
    if isinstance(content, list):  # мультимодальный формат LangChain
        content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content.strip()


_URL = re.compile(r"https?://\S+")


def _title_topic(text: str) -> str:
    """
    Текст для заголовка: ссылки схлопнуты, пробелы сжаты.

    Обычный вход конвейера подготовки — «напиши документацию по задаче
    <ссылка>», и в восьмидесяти символах заголовка ссылка занимает шестьдесят,
    не сообщая ничего: адрес трекера у всех страниц пространства один и тот же.
    Ключ задачи из неё, наоборот, — самое полезное, что в заголовке может быть,
    поэтому ссылка заменяется ключом, а не выбрасывается.
    """

    def collapse(match: re.Match) -> str:
        keys = jira.find_keys(match.group(0))
        return keys[0] if keys else ""

    return " ".join(_URL.sub(collapse, text or "").split())


def page_title(state: State, config: RunnableConfig, role: roles.Role | None = None) -> str:
    """
    Заголовок страницы. Обязан быть одинаковым на всех ходах треда — по нему
    идёт upsert, иначе каждый ход создавал бы новую страницу. Поэтому берём
    первую задачу оператора (она больше не изменится) и thread_id.

    Роль дописывается в конец отдельным куском: страниц у конвейера пять, и
    различать их обязано именно то, что у них разное. Без роли возвращается
    общий заголовок треда — он нужен ноде подтверждения и сводке публикации.
    """
    stage = f" — {role.number} {role.title}" if role else ""

    override = cfg.confluence_page_title()
    if override:
        return override + stage

    thread_id = (config.get("configurable") or {}).get("thread_id") or "no-thread"
    first = next((m for m in state["messages"] if getattr(m, "type", "") == "human"), None)
    limit = cfg.confluence_title_max_len()
    # Маска нужна и здесь: заголовок виден в списке страниц пространства и
    # в имени файла на диске — то есть ровно там, где данные заказчика заметнее
    # всего. Шаблоны стабильны, поэтому upsert по заголовку не ломается.
    topic = confluence.mask_text(_title_topic(text_of(first))[:limit]) if first else ""
    return f"{cfg.agent_name()}: {topic or 'задача без описания'} [{thread_id}]{stage}"


def _usage_table(usage: dict, renderer: render.Renderer | None = None) -> str:
    rows = [
        ("Вызовов LLM", usage.get("calls", 0)),
        ("Вход из кеша, токенов", usage.get("cache_hit", 0)),
        ("Вход пересчитан, токенов", usage.get("cache_miss", 0)),
    ]
    # Запись в кеш есть не у всех провайдеров — пустой строки в таблице быть
    # не должно, иначе на DeepSeek она вечно висит с нулём.
    if usage.get("cache_write", 0):
        rows.append(("Записано в кеш, токенов", usage["cache_write"]))
    rows += [
        ("Выход, токенов", usage.get("output", 0)),
        ("Cache hit rate", f"{hit_rate(usage):.1f}%"),
        ("Стоимость", f"${estimate_cost(usage):.6f}"),
    ]
    return (renderer or render.STORAGE).table(rows)


def _markup(text: str, renderer: render.Renderer) -> str:
    """Текст в документ: сначала маска, потом разметка выбранного формата."""
    return renderer.body(confluence.mask_text(text))


def split_turns(messages: list) -> list[list]:
    """
    Разбить тред на ходы. Ход начинается вопросом оператора и включает всё,
    что агент сделал в ответ: вызовы инструментов, их ответы и финальный текст.
    """
    turns: list[list] = []
    for message in messages:
        if getattr(message, "type", "") == "human" or not turns:
            turns.append([])
        turns[-1].append(message)
    return turns


def _render_turn(messages: list, renderer: render.Renderer) -> list[str]:
    include_tools = cfg.confluence_include_tool_output()
    parts: list[str] = []

    for message in messages:
        kind = getattr(message, "type", "")
        text = text_of(message)

        if kind == "human":
            parts.append(renderer.heading("Вопрос оператора"))
            parts.append(_markup(text, renderer))
        elif kind == "ai":
            for call in getattr(message, "tool_calls", None) or []:
                # Аргументы вызова — это тоже данные клиента (идентификатор
                # аккаунта, маркетплейс), поэтому они идут на страницу по тому
                # же флагу, что и ответы систем.
                args = f" {call.get('args')}" if include_tools else ""
                parts.append(_markup(f"Запрос в систему: {call.get('name')}{args}", renderer))
            if text:
                parts.append(renderer.heading("Ответ агента"))
                parts.append(_markup(text, renderer))
        elif kind == "tool" and include_tools:
            parts.append(
                _markup(
                    f"Ответ системы ({getattr(message, 'name', 'инструмент')}): {text}",
                    renderer,
                )
            )

    return parts


def _hidden_turns_block(turns: list[list], renderer: render.Renderer) -> str:
    """Свёрнутый указатель на ходы, которые не поместились на страницу."""
    limit = cfg.confluence_title_max_len()
    items = []
    for number, turn in enumerate(turns, start=1):
        first = next((m for m in turn if getattr(m, "type", "") == "human"), None)
        topic = " ".join(text_of(first).split())[:limit] if first else "без вопроса"
        items.append(f"Ход {number}: {confluence.mask_text(topic)}")

    body = renderer.join(
        [
            renderer.paragraph(
                "Полный текст этих ходов на странице не хранится — он остаётся в состоянии треда."
            ),
            renderer.bullets(items),
        ]
    )
    return renderer.collapsed(f"Ранние ходы: {len(turns)}", body)


def document_header(
    config: RunnableConfig | None = None,
    renderer: render.Renderer | None = None,
    pipeline: Pipeline = roles.PIPELINE,
) -> str:
    """
    Шапка со временем сборки. Вынесена отдельно от тела: время меняется на
    каждом рендере, и если бы оно попадало в хеш, режим `changed` считал бы
    документ изменившимся всегда.

    Модель берётся из переопределений треда, а не из константы модуля: тред
    мог выбрать свою.
    """
    stamp = datetime.now().isoformat(timespec="seconds")
    model = options(config).get("model") or MODEL
    return (renderer or render.STORAGE).paragraph(
        f"Страница собрана автоматически {pipeline.byline} {cfg.agent_name()} "
        f"(модель {model}). Обновлено: {stamp}. "
        "Правки руками затрёт следующий прогон треда."
    )


def render_body(state: State, renderer: render.Renderer | None = None) -> str:
    """
    Тело страницы без шапки: ходы и таблица расходов.

    На странице остаются последние CONFLUENCE_MAX_TURNS ходов целиком, всё
    более раннее сворачивается в один свёрнутый блок с указателем вопросов.
    Без этого тело PUT росло бы линейно вместе с тредом.

    Формат по умолчанию — storage Confluence: он исторический и им пользуется
    большая часть кода. Нода публикации передаёт сюда рендерер своей цели.
    """
    renderer = renderer or render.STORAGE
    turns = split_turns(state["messages"])
    limit = cfg.confluence_max_turns()

    hidden: list[list] = []
    if limit > 0 and len(turns) > limit:
        hidden, turns = turns[:-limit], turns[-limit:]

    parts: list[str] = []
    if hidden:
        parts.append(_hidden_turns_block(hidden, renderer))
    for turn in turns:
        parts += _render_turn(turn, renderer)

    parts.append(renderer.heading("Расход токенов по треду"))
    parts.append(_usage_table(state.get("usage") or {}, renderer))
    return renderer.join(parts)


def render_document(
    state: State,
    config: RunnableConfig | None = None,
    renderer: render.Renderer | None = None,
) -> str:
    """Готовая страница: шапка плюс тело."""
    renderer = renderer or render.STORAGE
    return renderer.join([document_header(config, renderer), render_body(state, renderer)])


# --------------------------------------------------------------------------
# Документы этапов
#
# У конвейера пять результатов, и каждый — самостоятельный документ: по нему
# работает следующий специалист, а потом читает человек. Склеить их в одну
# страницу было бы проще для кода и хуже для читателя: он приходит за контрактом
# API, а не за всей историей проекта.
#
# Задача повторяется в шапке каждого документа. Это дублирование намеренное:
# страницу открывают по ссылке из тикета, и документ, который начинается с
# ответа на неизвестно какой вопрос, читать невозможно.
# --------------------------------------------------------------------------
def task_of(state: State) -> str:
    """
    Задача конвейера. В состоянии она есть со времён ноды контекста, но тред
    мог прийти и без неё — из теста, из старого чекпоинта, из чужого кода.
    """
    task = (state.get("task") or "").strip()
    if task:
        return task
    first = next(
        (m for m in state.get("messages") or [] if getattr(m, "type", "") == "human"),
        None,
    )
    return operator_question(text_of(first)) if first else ""


def _stage_section(role: roles.Role, state: State, renderer: render.Renderer) -> list[str]:
    artifact = (state.get("artifacts") or {}).get(role.key) or ""
    return [
        renderer.heading(f"{role.number}. {role.title}"),
        _markup(artifact, renderer),
    ]


def render_stage_body(
    role: roles.Role, state: State, renderer: render.Renderer | None = None
) -> str:
    """Тело документа этапа: задача, документ роли и расход по треду."""
    renderer = renderer or render.STORAGE
    parts = [
        renderer.heading("Задача"),
        _markup(task_of(state), renderer),
        *_stage_section(role, state, renderer),
        renderer.heading("Расход токенов по треду"),
        _usage_table(state.get("usage") or {}, renderer),
    ]
    return renderer.join(parts)


def render_pipeline_body(
    state: State,
    renderer: render.Renderer | None = None,
    pipeline: Pipeline = roles.PIPELINE,
) -> str:
    """
    Все этапы одним документом: задача, документы по порядку, расход.

    Нужен там, где документ читают целиком, а не по ссылке на конкретный этап:
    в окне подтверждения и в состоянии треда для интерфейса. Склейка готовых
    страниц там не годится — задача и таблица расходов повторились бы пять раз,
    и человек, которому это показывают перед публикацией, читал бы одно и то же.
    """
    renderer = renderer or render.STORAGE
    parts = [renderer.heading("Задача"), _markup(task_of(state), renderer)]
    for role in pipeline.done(state.get("artifacts")):
        parts += _stage_section(role, state, renderer)
    parts.append(renderer.heading("Расход токенов по треду"))
    parts.append(_usage_table(state.get("usage") or {}, renderer))
    return renderer.join(parts)


def stage_pages(
    state: State,
    config: RunnableConfig | None = None,
    renderer: render.Renderer | None = None,
    header: str | None = None,
    pipeline: Pipeline = roles.PIPELINE,
) -> list[dict]:
    """
    Готовые к публикации страницы — по одной на состоявшийся этап.

    Сколько их — решает конвейер (`Pipeline.one_page`): по странице на этап или
    одна на весь прогон со всеми этапами разделами.

    Хеш считается по телу, без шапки: в шапке стоит время сборки, и с ним
    документ «менялся» бы на каждом ходе.

    Шапку можно передать готовой. Она одна на все страницы прогона, и собирать
    её заново на каждую — значит проставить пяти страницам одного прогона пять
    разных времён сборки.
    """
    renderer = renderer or render.STORAGE
    header = document_header(config, renderer, pipeline) if header is None else header

    if pipeline.one_page:
        # Заголовок без роли — общий заголовок треда. Он же у документа целиком
        # в окне подтверждения, и это не совпадение: страница ровно одна, и
        # различать ей нечего.
        body = render_pipeline_body(state, renderer, pipeline)
        return (
            [
                {
                    "role": "",
                    "title": page_title(state, config or {}),
                    "document": renderer.join([header, body]),
                    "digest": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                }
            ]
            if pipeline.done(state.get("artifacts"))
            else []
        )

    pages = []
    for role in pipeline.done(state.get("artifacts")):
        body = render_stage_body(role, state, renderer)
        pages.append(
            {
                "role": role.key,
                "title": page_title(state, config or {}, role),
                "document": renderer.join([header, body]),
                "digest": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        )
    return pages

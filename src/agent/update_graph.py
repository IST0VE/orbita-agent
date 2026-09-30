"""One run: explicit input files, grounded edits, review, publish a new version."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent import (
    actions,
    credentials,
    drafts,
    inputs,
    llm_retry,
    metrics,
    nodes,
    outgoing,
    pause,
    proposals,
    publish_nodes,
    publishers,
    update_plan,
    update_roles,
)
from agent import config as cfg
from agent.cost import charge, cost_summary, extract_usage
from agent.routes import budget_gate
from agent.runtime import nested, options
from agent.sources import question_of
from agent.state import Options as BaseOptions
from agent.state import State as BaseState
from agent.state import _merge_spend, _merge_usage


class Options(BaseOptions, total=False):
    base_dir: str
    base_file: str


class State(BaseState, total=False):
    source: dict
    proposal: str
    revision: dict
    publication_plan: dict
    error: str


def failed(reason: str) -> dict:
    # Предложения снимаются и отказом: на провалившемся ходе предлагать
    # нечего, а прошлый ход треда вызывающему уже не относится.
    return {
        "error": reason,
        "publication": {"status": "failed", "reason": reason},
        "proposals": [],
        "messages": [AIMessage(content=reason)],
    }


def read_node(state: State, config: RunnableConfig) -> dict:
    chosen = options(config)
    extra_dir = chosen.get("input_dir")
    # Основной документ лежит там же, где новые материалы, если его папка не
    # названа отдельно: в чате папка одна, и оба выбора делаются в ней.
    base_dir, base_file = chosen.get("base_dir") or extra_dir, chosen.get("base_file")
    extras = inputs.picked_names(chosen.get("input_file"))
    if not isinstance(base_file, str) or not base_file.strip() or not base_dir:
        return failed("Отметьте в файлах чата один основной документ.")
    if not extra_dir or not extras:
        return failed(
            "Отметьте в файлах чата новые материалы: все файлы чата разом не подставляются."
        )
    try:
        base_path = inputs.resolve(str(base_dir), base_file)
        if any(inputs.resolve(str(extra_dir), name) == base_path for name in extras):
            return failed("Основной документ не может одновременно быть новым материалом.")
        # Read one character beyond the shared limit; never update from a cut-off source.
        limit = cfg.input_max_chars()
        remaining = limit

        def read(folder: str, name: str) -> str:
            nonlocal remaining
            content = inputs.read(folder, name, max_chars=remaining + 1 if limit else 0)
            if limit and len(content) > remaining:
                raise inputs.InputError(
                    "Документы превышают AGENT_INPUT_MAX_CHARS. Увеличьте лимит "
                    "или выберите меньший комплект: обрезанный текст обновлять нельзя."
                )
            if not content.strip():
                raise inputs.InputError(f"{name}: документ пуст.")
            remaining -= len(content)
            return content

        original = read(str(base_dir), base_file)
        materials = {name: read(str(extra_dir), name) for name in extras}
    except (inputs.InputError, OSError) as exc:
        return failed(f"Материалы не прочитаны: {exc}")
    return {
        "task": question_of(state),
        "stage": "source",
        "error": "",
        "publication_plan": {},
        "approval": {},
        "publication": {},
        "proposals": [],
        "source": {
            "original": original,
            "materials": materials,
            "name": base_file,
            "title": f"{Path(base_file).stem[:60]} — обновление {uuid4().hex[:12]}",
        },
        "messages": [
            AIMessage(
                content=f"Основной документ: {base_file}. "
                f"Новые материалы: {', '.join(extras)}. Все прочитаны целиком."
            )
        ],
    }


def make_propose_node(llm: Any = None):
    def propose(state: State, config: RunnableConfig) -> dict:
        if budget_gate(state) == "over_budget":
            return failed("Бюджет треда исчерпан до предложения изменений.")
        # Пауза оператора: у этого графа один вызов модели, и это
        # единственная его граница, на которой ещё можно что-то добавить.
        # Остановка на ней уходит той же веткой, что и любой другой отказ
        # этого графа, — через `error` в END (см. `build_graph`).
        paused = pause.checkpoint(
            state, config, stage="changes", title="Предложение изменений"
        )
        if halt := paused.get("halt"):
            return {
                **paused,
                **failed(
                    "Правка остановлена оператором на паузе. "
                    f"Причина: {halt.get('reason') or 'не указана'}."
                ),
            }
        notes = [*(state.get("notes") or []), *paused.get("notes", [])]
        source = state["source"]
        payload = {
            "request": state["task"],
            "original": source["original"],
            "materials": source["materials"],
        }
        model = llm if llm is not None else nodes.model_for(config, ())
        try:
            answer = llm_retry.invoke(
                model,
                [
                    SystemMessage(content=update_roles.PROMPT),
                    # Указания оператора — в конец сообщения, как и везде: префикс
                    # роли обязан остаться неподвижным.
                    HumanMessage(
                        content=json.dumps(payload, ensure_ascii=False)
                        + pause.notes_block(notes)
                    ),
                ]
            )
        except llm_retry.ResponseTruncated as error:
            # Оборванное предложение не применяется, но оплачено: расход
            # уходит в тред той же веткой отказа, иначе бюджет его не увидит.
            return {**paused, **failed(str(error)), **nodes.truncation_charge(error, state)}
        usage = extract_usage(answer)
        money = charge(usage, state=state)
        return {
            **paused,
            "proposal": nodes.text_of(answer),
            "usage": usage,
            "spend": money,
            "cost": cost_summary(
                _merge_usage(state.get("usage"), usage), _merge_spend(state.get("spend"), money)
            ),
            "stage": "changes",
        }

    return propose


def apply_node(state: State) -> dict:
    source = state["source"]
    try:
        result = update_plan.apply_plan(source["original"], source["materials"], state["proposal"])
    except update_plan.UpdateError as exc:
        return failed(f"Изменения не применены: {exc}")
    summary = update_plan.report(result)
    return {
        "revision": result,
        "document": result["document"],
        "stage": "apply",
        "artifacts": {"changes": summary, "updated": result["document"]},
        "messages": [AIMessage(content=summary)],
    }


#: Назначение считается там же, где для конвейера: правило одно, и расходиться
#: этим двум описаниям «куда именно» нельзя.
destination = publishers.destination


def prepare_node(state: State, config: RunnableConfig) -> dict:
    if not state["revision"]["changes"]:
        return {"publication": {"status": "unchanged", "reason": "Нет подтверждённых изменений."}}
    publisher = publishers.current()
    if not publishers.is_enabled() or publisher.name == "none":
        return {
            "publication": {"status": "disabled", "reason": "Новая версия доступна в результатах."}
        }
    absent = publisher.missing()
    if absent:
        return failed("Публикация не настроена: " + credentials.missing_message(absent))
    title = state["source"]["title"]
    document = (
        state["document"]
        if publisher.name == "file"
        else publisher.renderer.body(state["document"])
    )
    # Новая версия документа собирается из готового текста и до этой строки шла
    # мимо маскирования: оно жило внутри сборки документов конвейера, а сюда
    # приходит результат `update_plan.apply_plan`. Проверка — та же, что у всех
    # остальных исходящих, и проверенное тело идёт и в черновик, и в запись.
    try:
        title = outgoing.guard(title, "title")
        document = outgoing.guard(document, "document")
    except outgoing.OutgoingBlocked as exc:
        return failed(f"Новая версия не сохранена: {exc}")
    plan = {
        "title": title,
        "document": document,
        "digest": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "target": publisher.name,
        "format": publisher.renderer.name,
        "destination": destination(publisher),
        "where": publisher.location_key(title),
    }
    plan["expected"] = publisher.preview(title)
    plan["draft"] = drafts.page(
        role="updated",
        title=title,
        document=document,
        fmt=plan["format"],
        where=publisher.name,
        preview=plan["expected"],
    )
    update = {"publication_plan": plan, "stage": "prepare"}
    if nested(config):
        # Предложение той же формы, что у конвейеров (`publish_nodes`): одна
        # страница, одобряемый набор с версией на момент предпросмотра. По
        # нему вызывающий и сохранит новую версию — `publish_proposed`.
        page = {"role": "updated", "title": title, "document": document, "digest": plan["digest"]}
        pages = {"publisher": publisher, "pages": [page], "digest": plan["digest"]}
        update["proposals"] = proposals.offer(
            state,
            {
                "kind": "publish",
                "graph": "update",
                # Этот граф спрашивает всегда, независимо от
                # PUBLISH_REQUIRE_APPROVAL: см. `approve_node`.
                "approval_required": True,
                "prompt": _prompt(state, plan),
                "effect": publish_nodes.proposed_effect(
                    pages, publish_nodes.publish_commitment(pages, [plan["expected"]])
                ),
            },
        )
    return update


def _prompt(state: State, plan: dict) -> dict:
    """Что оператор видит перед сохранением новой версии."""
    return {
        "action": "publish",
        "title": "Сохранить новую версию документа",
        "target": plan["target"],
        "drafts": [plan["draft"]],
        "document": state["artifacts"]["changes"],
        "warnings": [
            "Будет сохранена отдельная новая версия. Основной документ останется на месте.",
            *state["revision"]["questions"],
        ],
    }


def save_action(plan: dict, config: RunnableConfig) -> dict:
    """
    Сохранение новой версии как предложение общего порядка (`actions.py`).

    Операция одна — страница, и в её отпечаток входит всё, на что соглашается
    оператор: текст, цель, назначение и состояние страницы на момент показа.
    """
    thread = str((config.get("configurable") or {}).get("thread_id") or "")
    expected = plan.get("expected") or {}
    return actions.seal(
        "publish",
        graph="update",
        target={"system": plan["target"], "format": plan["format"],
                "destination": plan["destination"]},
        operations=[{
            "op": "page",
            "key": plan.get("where") or plan["title"],
            "title": plan["title"],
            "hash": plan["digest"],
            "action": str(expected.get("action") or "unknown"),
            "version": expected.get("version"),
            "page_id": str(expected.get("page_id") or ""),
        }],
        thread=thread,
        owner=actions.actor(),
        scope=actions.run_key(thread, "publish", plan["target"]),
    )


def approve_node(state: State, config: RunnableConfig) -> dict:
    if nested(config):
        # Вопрос уехал предложением: вложенный прогон оператора не спрашивает.
        return {"stage": "approve"}
    action = save_action(state["publication_plan"], config)
    answer = interrupt(actions.shown(_prompt(state, state["publication_plan"]), action))
    # Согласие — на эту версию текста в это место, а не на слово «сохранить».
    approval = actions.bind(answer, action["digest"])
    actions.Recorder(action).decide(approval)
    return {"approval": approval, "stage": "approve"}


def publish_node(state: State, config: RunnableConfig) -> dict:
    if nested(config):
        return {
            "publication": {
                "status": "proposed",
                "reason": "вложенный прогон: сохранение передано вызывающему графу",
            },
            "stage": "publish",
        }
    approval = state.get("approval") or {}
    plan = state["publication_plan"]
    action = save_action(plan, config)
    stale = actions.stale(approval, action["digest"])
    if stale:
        return {"publication": {"status": "stale", "reason": stale}, "stage": "publish",
                "messages": [AIMessage(content=f"Новая версия не сохранена: {stale}.")]}
    if approval.get("decision") != "approved":
        return {"publication": {"status": "rejected", "reason": "Сохранение отклонено."}}
    publisher = publishers.current()
    if (
        not publishers.is_enabled()
        or publisher.name != plan["target"]
        or destination(publisher) != plan["destination"]
    ):
        return failed(
            "Настройки публикации изменились после подготовки. Запустите обновление заново."
        )
    recorder = actions.Recorder(action)
    page = {"role": "updated", "title": plan["title"], "document": plan["document"],
            "digest": plan["digest"]}
    expected = (
        {publisher.location_key(plan["title"]): plan.get("expected", {})}
        if publisher.name == "confluence" else None
    )
    (result,) = publish_nodes.write_pages(publisher, [page], expected=expected, recorder=recorder)
    recorder.done(result.get("status", "failed"), result)
    if result.get("status") == "failed":
        return failed(f"Новая версия не сохранена: {result.get('reason', 'без причины')}")
    note = f"Новая версия сохранена: {plan['title']}."
    if result.get("verified") == actions.DIFFERS:
        note += f" Сверка после записи: {result.get('verify_detail')}."
    return {
        "publication": {**result, "pages": [result]},
        "stage": "publish",
        "messages": [AIMessage(content=note)],
    }


def build_graph(llm: Any = None) -> StateGraph:
    builder = StateGraph(State, context_schema=Options)
    for name, node in (
        ("source", read_node),
        ("changes", make_propose_node(llm)),
        ("apply", apply_node),
        ("prepare", prepare_node),
        ("approve", approve_node),
        ("publish", publish_node),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, "source")
    for name, target in (("source", "changes"), ("changes", "apply"), ("apply", "prepare")):
        builder.add_conditional_edges(
            name,
            lambda state: "stop" if state.get("error") else "next",
            {"stop": END, "next": target},
        )
    builder.add_conditional_edges(
        "prepare",
        lambda state: (
            "next" if state.get("publication_plan") and not state.get("error") else "stop"
        ),
        {"next": "approve", "stop": END},
    )
    builder.add_edge("approve", "publish")
    builder.add_edge("publish", END)
    return builder


graph = metrics.observe(build_graph().compile(), "update")

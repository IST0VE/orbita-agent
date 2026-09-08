"""One run: explicit input files, grounded edits, review, publish a new version."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent import config as cfg
from agent import drafts, inputs, nodes, publishers, update_plan, update_roles
from agent.cost import cost_summary, extract_usage
from agent.routes import budget_gate
from agent.runtime import options
from agent.sources import question_of
from agent.state import Options as BaseOptions
from agent.state import State as BaseState
from agent.state import _merge_usage


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
    return {
        "error": reason,
        "publication": {"status": "failed", "reason": reason},
        "messages": [AIMessage(content=reason)],
    }


def read_node(state: State, config: RunnableConfig) -> dict:
    chosen = options(config)
    base_dir, base_file = chosen.get("base_dir"), chosen.get("base_file")
    extra_dir = chosen.get("input_dir")
    extras = inputs.picked_names(chosen.get("input_file"))
    if not isinstance(base_file, str) or not base_file.strip() or not base_dir:
        return failed("Выберите один основной документ и его папку.")
    if not extra_dir or not extras:
        return failed("Выберите новые материалы и их папку. Папка целиком не подставляется.")
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
        source = state["source"]
        payload = {
            "request": state["task"],
            "original": source["original"],
            "materials": source["materials"],
        }
        model = llm if llm is not None else nodes.model_for(config, ())
        answer = model.invoke(
            [
                SystemMessage(content=update_roles.PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        usage = extract_usage(answer)
        return {
            "proposal": nodes.text_of(answer),
            "usage": usage,
            "cost": cost_summary(_merge_usage(state.get("usage"), usage)),
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


def destination(publisher: publishers.Publisher) -> dict:
    if publisher.name == "file":
        return {"directory": str(publishers.directory().resolve())}
    return {
        "base_url": cfg.confluence_base_url(),
        "space_key": cfg.confluence_space_key(),
        "space_id": cfg.confluence_space_id(),
        "parent_id": cfg.confluence_parent_id(),
        "api_path": cfg.confluence_api_path(),
        "version": cfg.confluence_api_version(),
    }


def prepare_node(state: State) -> dict:
    if not state["revision"]["changes"]:
        return {"publication": {"status": "unchanged", "reason": "Нет подтверждённых изменений."}}
    publisher = publishers.current()
    if not publishers.is_enabled() or publisher.name == "none":
        return {
            "publication": {"status": "disabled", "reason": "Новая версия доступна в результатах."}
        }
    absent = publisher.missing()
    if absent:
        return failed("Публикация не настроена: " + ", ".join(absent))
    title = state["source"]["title"]
    document = (
        state["document"]
        if publisher.name == "file"
        else publisher.renderer.body(state["document"])
    )
    plan = {
        "title": title,
        "document": document,
        "target": publisher.name,
        "format": publisher.renderer.name,
        "destination": destination(publisher),
    }
    plan["draft"] = drafts.page(
        role="updated",
        title=title,
        document=document,
        fmt=plan["format"],
        where=publisher.name,
        preview=publisher.preview(title),
    )
    return {"publication_plan": plan, "stage": "prepare"}


def approve_node(state: State) -> dict:
    plan = state["publication_plan"]
    answer = interrupt(
        {
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
    )
    return {"approval": nodes.approval_of(answer), "stage": "approve"}


def publish_node(state: State) -> dict:
    if (state.get("approval") or {}).get("decision") != "approved":
        return {"publication": {"status": "rejected", "reason": "Сохранение отклонено."}}
    plan = state["publication_plan"]
    publisher = publishers.current()
    if (
        not publishers.is_enabled()
        or publisher.name != plan["target"]
        or destination(publisher) != plan["destination"]
    ):
        return failed(
            "Настройки публикации изменились после подготовки. Запустите обновление заново."
        )
    try:
        result = dict(publisher.publish(plan["title"], plan["document"]))
    except publishers.PublishError as exc:
        return failed(f"Новая версия не сохранена: {exc}")
    result["role"] = "updated"
    return {
        "publication": {**result, "pages": [result]},
        "stage": "publish",
        "messages": [AIMessage(content=f"Новая версия сохранена: {plan['title']}.")],
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


graph = build_graph().compile()

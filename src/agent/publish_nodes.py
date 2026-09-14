"""
Публикация и подтверждение: план, одобряемый набор и запись наружу.

Отделено от `nodes.py` по ответственности, а не по числу строк. Узлы ролей
отвечают на вопрос «что написать»: подставить контекст, позвать модель,
запомнить ход. Здесь — «куда это уедет и кто на это согласился»: собрать план,
показать его оператору, сверить перед записью и записать.

Смешивать их было дорого ровно в одном месте: правила публикации нужны трём
конвейерам и двум графам с собственными узлами, и каждый раз тянули за собой
весь модуль ролей вместе с моделью, инструментами и памятью.

Общие правила остались функциями, а не превратились в наследование узлов:
`publish_plan`, `publish_commitment`, `commitment_changes` проверяются по
отдельности и вызываются из любого графа.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from agent import config as cfg
from agent import confluence, drafts, publishers, roles
from agent.documents import (
    document_header,
    page_title,
    render_body,
    render_pipeline_body,
    stage_pages,
)
from agent.pipeline import Pipeline
from agent.runtime import options
from agent.state import State
from agent.summary import summary_of


def publish_plan(
    state: State,
    config: RunnableConfig | None = None,
    pipeline: Pipeline = roles.PIPELINE,
    *,
    publisher: publishers.Publisher | None = None,
) -> dict:
    """
    Что нода публикации сделает на этом ходе: цель, готовые страницы, общий
    хеш и причина пропуска, если публиковать не нужно.
    """
    publisher = publisher or publishers.current()
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
        # Коллизии считаются по всему плану и до записи: страницы уезжают по
        # одной, и цель, не различившая два заголовка, оставит вместо двух
        # документов один — с содержимым того, который писался вторым.
        "collisions": publishers.collisions(publisher, [page["title"] for page in pages]),
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

    # Флаг трёхпозиционный: ключа нет — решает режим, True — публиковать, False —
    # не публиковать ни в каком режиме. Последнее нужно вложенным прогонам: граф,
    # который вызывает чужой конвейер ради одного лишь результата, не должен
    # выпускать наружу его промежуточный документ и просить на это оператора.
    requested = options(config).get("publish")
    if requested is False:
        return ("postponed", "публикация отключена вызывающей стороной")
    mode = cfg.confluence_publish_mode()
    if mode == "manual" and not requested:
        return ("postponed", "режим manual: публикация не запрошена")
    if mode == "changed" and digest == state.get("document_hash"):
        return ("unchanged", "документы не изменились с прошлой публикации")

    absent = publisher.missing()
    if absent:
        return ("skipped", "не заданы в .env: " + ", ".join(absent))
    return None


def publish_commitment(plan: dict, previews: list[dict] | None = None) -> dict:
    """
    Одобряемый набор: что именно, куда именно и поверх чего именно.

    Обычная публикация пересчитывает план после подтверждения — и это верно
    само по себе: между остановкой и записью проходит время, за которое
    страница на той стороне могла измениться. Неверно другое: пересчитанный
    план исполнялся с прежним `approved`. Смены `PUBLISH_DIR` хватало, чтобы
    одобренный документ уехал в другую папку.

    Поэтому одобряется не «опубликовать», а набор: цель, точное назначение,
    идентичность каждого документа, хеш проверенного содержимого, create или
    update и версия существующей страницы. Набор сравним и сравнивается перед
    самой записью.

    `previews` — уже собранные ответы цели: остановка спрашивает их ради
    черновиков, и спрашивать второй раз значит удвоить запросы к wiki.
    """
    publisher = plan["publisher"]
    pages = plan["pages"]
    if previews is None:
        # Цель без `preview` ничего не рассказывает о том, что там уже лежит.
        # Это не повод не фиксировать всё остальное: назначение, содержимое
        # и идентичность документов от её словоохотливости не зависят.
        ask = getattr(publisher, "preview", None) or (lambda title: {"action": "unknown"})
        previews = [ask(page["title"]) for page in pages]

    return {
        "target": publisher.name,
        "format": publisher.renderer.name,
        "destination": publishers.destination(publisher),
        "digest": plan["digest"],
        "pages": [
            {
                "role": page.get("role", ""),
                "title": page["title"],
                # Идентичность документа отдельно от заголовка: у файловой цели
                # это путь, у wiki — сам заголовок (см. `location_key`).
                "where": _location_of(publisher, page["title"]),
                "digest": page["digest"],
                "action": str(preview.get("action") or "unknown"),
                # Версия страницы на момент предпросмотра. Чужая правка между
                # показом и записью поднимает её, и обновление затёрло бы
                # то, чего оператор не видел.
                "version": preview.get("version"),
                "page_id": str(preview.get("page_id") or ""),
            }
            for page, preview in zip(pages, previews, strict=True)
        ],
    }


def _location_of(publisher: publishers.Publisher, title: str) -> str:
    where = getattr(publisher, "location_key", None)
    return where(title) if where else title


def commitment_digest(commitment: dict) -> str:
    """Отпечаток набора: по нему решение оператора привязано к своему плану."""
    canonical = json.dumps(commitment, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def commitment_changes(approved: dict, fresh: dict) -> list[str]:
    """Чем пересчитанный набор отличается от одобренного, человеческими словами."""
    if not approved:
        return ["подготовленный план не сохранён"]

    changes: list[str] = []
    if approved.get("target") != fresh.get("target"):
        changes.append(f"цель публикации: {approved.get('target')} → {fresh.get('target')}")
    if approved.get("destination") != fresh.get("destination"):
        changes.append("назначение публикации")
    if approved.get("digest") != fresh.get("digest"):
        changes.append("содержимое документов")

    before = {page.get("where"): page for page in approved.get("pages") or []}
    after = {page.get("where"): page for page in fresh.get("pages") or []}
    if set(before) != set(after):
        changes.append("состав документов")
    for where, page in after.items():
        was = before.get(where)
        if was is None:
            continue
        if was.get("action") != page.get("action"):
            changes.append(f"{page.get('title')}: {was.get('action')} → {page.get('action')}")
        if was.get("version") != page.get("version"):
            changes.append(
                f"{page.get('title')}: страница изменилась после предпросмотра "
                f"(версия {was.get('version')} → {page.get('version')})"
            )
        if was.get("page_id") != page.get("page_id"):
            changes.append(f"{page.get('title')}: идентификатор страницы изменился")
    return changes


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


def prepare_node(
    state: State, config: RunnableConfig, pipeline: Pipeline = roles.PIPELINE
) -> dict:
    """
    Собрать и сохранить план публикации до остановки.

    Отдельный узел, а не первые строки `approve_node`, и это не косметика.
    `interrupt()` прерывает узел, а состояние LangGraph фиксирует только
    возвращённое: план, посчитанный внутри остановки, не переживает ожидания
    и пересчитывается заново при возобновлении — уже по настройкам, которые
    к тому времени успели поменяться. Тогда сверять было бы не с чем: новый
    план совпадал бы сам с собой.

    Поэтому подготовка заканчивается записью в состояние, а остановка
    показывает сохранённое и ничего не считает.
    """
    if not cfg.publish_require_approval():
        return {}

    plan = publish_plan(state, config, pipeline)
    if plan["skip"] or plan["collisions"]:
        return {"publication_plan": {}}

    # Один опрос цели на остановку: он же рисует черновики, он же фиксирует
    # create/update и версию страницы в одобряемом наборе.
    ask = getattr(plan["publisher"], "preview", None) or (lambda title: {"action": "unknown"})
    previews = [ask(page["title"]) for page in plan["pages"]]
    commitment = publish_commitment(plan, previews)
    commitment["drafts"] = drafts.pages(plan, previews)
    return {"publication_plan": commitment}


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

    prepared = state.get("publication_plan") or {}
    plan = publish_plan(state, config, pipeline)
    if plan["skip"] or plan["collisions"] or not prepared:
        # Нечего подтверждать: публикация не состоится, план невыполним или
        # подготовка его не сохранила.
        return {}

    payload = {
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
        "drafts": prepared["drafts"],
        "document": plan["document"],
        "hint": 'ответьте true/false или {"decision": "rejected", "reason": ...}',
    }
    if (pipeline.rejection_fallback
            and plan["publisher"].name != pipeline.rejection_fallback):
        payload["reject_label"] = (
            "не публиковать; сохранить файл"
            if pipeline.rejection_fallback == "file"
            else "не публиковать; сохранить в резервную цель"
        )
        payload["reject_hint"] = (
            "Отказ отменит внешнюю публикацию, но готовый отчёт будет сохранён локально."
        )
    answer = interrupt(payload)
    decision = approval_of(answer)
    if isinstance(answer, dict) and answer.get("decision") == "drafts":
        decision["decision"] = "drafts"
    # Решение относится к показанному набору, а не к слову «опубликовать».
    decision["plan_digest"] = commitment_digest(prepared)
    return {"approval": decision}


def _rollup(results: list[dict]) -> str:
    """Один статус на всю публикацию по статусам отдельных страниц."""
    failed = [r for r in results if r.get("status") == "failed"]
    if failed:
        return "failed" if len(failed) == len(results) else "partial"
    return "created" if all(r.get("status") == "created" for r in results) else "updated"


def _publish_fallback(
    state: State,
    config: RunnableConfig,
    pipeline: Pipeline,
    *,
    source_target: str,
    source_status: str,
    source_reason: str,
) -> dict:
    """Сохранить документ в явно разрешённую конвейером резервную цель."""
    fallback = publishers.named(pipeline.rejection_fallback or "")
    plan = publish_plan(state, config, pipeline, publisher=fallback)
    results = []
    for page in plan["pages"]:
        try:
            result = dict(fallback.publish(page["title"], page["document"]))
        except publishers.PublishError as exc:
            result = {"status": "failed", "title": page["title"], "reason": str(exc)}
        result["role"] = page["role"]
        results.append(result)

    status = _rollup(results) if results else "failed"
    broken = [item for item in results if item.get("status") == "failed"]
    if broken:
        reason = source_reason + "; резервное сохранение не удалось: " + "; ".join(
            f"{item['title']}: {item.get('reason', 'без причины')}" for item in broken
        )
    else:
        reason = source_reason + "; готовый отчёт сохранён локально в PUBLISH_DIR"
    return {
        "document": plan["document"],
        "publication": {
            "status": status,
            "title": page_title(state, config),
            "target": fallback.name,
            "fallback_from": source_target,
            "external_status": source_status,
            "pages": results,
            "reason": reason,
        },
    }


def _stale_approval(state: State, plan: dict, decision: dict) -> str:
    """
    Почему прежнее согласие к этому плану неприменимо. Пусто — применимо.

    Отказ проверять нечего: он и так не публикует. Проверяются согласие и
    выбор черновиков — то есть те решения, после которых что-то пишется.
    """
    if decision.get("decision") not in {"approved", "drafts"}:
        return ""

    approved = state.get("publication_plan") or {}
    if not approved or not decision.get("plan_digest"):
        # Чекпоинт, сделанный до появления одобряемого набора. Исполнять
        # согласие, о содержании которого ничего не известно, нельзя.
        return (
            "подготовленный план не сохранён в этом треде (старый checkpoint): "
            "подтвердите публикацию заново"
        )
    if decision["plan_digest"] != commitment_digest(approved):
        return "сохранённый план не совпадает с решением оператора: подтвердите заново"

    changes = commitment_changes(approved, publish_commitment(plan))
    if changes:
        return "план изменился после подтверждения (" + "; ".join(changes) + "): подтвердите заново"
    return ""


def publish_node(state: State, config: RunnableConfig, pipeline: Pipeline = roles.PIPELINE) -> dict:
    """
    Публикация плюс итог хода.

    Итог считается здесь, потому что здесь заканчивается ход: это последний
    узел перед END у всех конвейеров этой формы, и всё, из чего складывается
    итог, к этому моменту уже есть. Считать его в интерфейсе значило бы отдать
    браузеру состояние целиком — вместе с историей сообщений, которая к итогу
    отношения не имеет, — и пересчитывать на каждом кадре.
    """
    update = _publish(state, config, pipeline)
    return {**update, "summary": summary_of({**state, **update})}


def _publish(state: State, config: RunnableConfig, pipeline: Pipeline = roles.PIPELINE) -> dict:
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

    if plan["collisions"]:
        # До первой записи: половина документов, перезаписанная второй
        # половиной, выглядит как успешная публикация.
        return skip(
            "failed",
            "цель публикации не различает документы плана: " + "; ".join(plan["collisions"]),
        )

    if plan["skip"]:
        status, reason = plan["skip"]
        if (status == "skipped" and pipeline.rejection_fallback
                and plan["publisher"].name != pipeline.rejection_fallback):
            return _publish_fallback(
                state,
                config,
                pipeline,
                source_target=plan["publisher"].name,
                source_status=status,
                source_reason=reason,
            )
        return skip(status, reason)

    decision = state.get("approval") or {}
    stale = _stale_approval(state, plan, decision) if cfg.publish_require_approval() else ""
    if stale:
        # Прежнее согласие к этому плану не относится. Публиковать нечего до
        # нового подтверждения; документы при этом остаются в состоянии треда.
        return skip("stale", stale)

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
            reason = decision.get("reason") or "оператор не подтвердил публикацию"
            if (pipeline.rejection_fallback
                    and plan["publisher"].name != pipeline.rejection_fallback):
                return _publish_fallback(
                    state,
                    config,
                    pipeline,
                    source_target=plan["publisher"].name,
                    source_status="rejected",
                    source_reason=reason,
                )
            return skip("rejected", reason)

    results = []
    approved_pages = {p["title"]: p for p in (state.get("publication_plan") or {}).get("pages", [])}
    for page in plan["pages"]:
        try:
            kwargs = {}
            if cfg.publish_require_approval() and plan["publisher"].name == "confluence":
                kwargs["expected"] = approved_pages.get(page["title"], {})
            result = dict(plan["publisher"].publish(page["title"], page["document"], **kwargs))
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

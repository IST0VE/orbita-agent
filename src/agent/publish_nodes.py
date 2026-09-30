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

Публикация идёт общим порядком внешних действий (`actions.py`): одобряемый
набор — предложение, его отпечаток показывается на остановке и возвращается
в ответе, каждая страница проходит через журнал операций и перечитывается
после записи. Журнал здесь терпим к отказу: страница уезжает и без него, а
причина остаётся в итоге публикации.
"""

from __future__ import annotations

import hashlib
import json

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from agent import actions, confluence, credentials, drafts, proposals, publishers, roles
from agent import config as cfg
from agent.actions import approval_of
from agent.documents import (
    document_header,
    page_title,
    render_body,
    render_pipeline_body,
    stage_pages,
)
from agent.pipeline import Pipeline
from agent.runtime import nested, options
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
        # переписку, — и промолчать здесь хуже, чем показать пустой
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
    # страниц: там задача повторилась бы по разу на этап.
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
    if state.get("refused"):
        # Проверка входа не пустила прогон к модели: документов нет, есть один
        # отказ, и он уже стоит в треде. Выпускать его отдельной страницей и
        # останавливаться ради этого на подтверждении — значит просить человека
        # одобрить публикацию сообщения «публиковать нечего».
        return ("nothing", "прогон не состоялся: вход не прошёл проверку, публиковать нечего")
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
        return ("skipped", credentials.missing_message(absent))
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
    if approved.get("format") != fresh.get("format"):
        changes.append("формат публикации")
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


def _thread(config: RunnableConfig | None) -> str:
    return str(((config or {}).get("configurable") or {}).get("thread_id") or "")


def publish_action(commitment: dict, *, graph: str, thread: str = "") -> dict:
    """
    Одобряемый набор как предложение общего порядка (`actions.seal`).

    Отпечаток — тот же `commitment_digest`, к которому привязано решение
    оператора (`plan_digest`): в журнале и на остановке один и тот же.
    Операции — страницы по их идентичности у цели (`where`).
    """
    return actions.seal(
        "publish",
        graph=graph,
        target={
            "system": commitment.get("target", ""),
            "format": commitment.get("format", ""),
            "destination": commitment.get("destination") or {},
        },
        operations=[
            {
                "op": "page",
                "key": page.get("where", ""),
                "title": page.get("title", ""),
                "hash": page.get("digest", ""),
                "action": page.get("action", "unknown"),
                "version": page.get("version"),
                "page_id": page.get("page_id", ""),
            }
            for page in commitment.get("pages") or []
        ],
        thread=thread,
        owner=actions.actor(),
        scope=actions.run_key(thread, "publish", str(commitment.get("target") or "")),
        fingerprint=commitment_digest(commitment),
    )


def write_pages(
    publisher: publishers.Publisher,
    pages: list[dict],
    *,
    expected: dict[str, dict] | None = None,
    recorder: actions.Recorder | None = None,
    completed: dict[str, dict] | None = None,
) -> list[dict]:
    """
    Страницы — в цель по одной: запись в журнал, публикация, сверка.

    `expected` — одобренное состояние каждой страницы по её идентичности у
    цели (`where`): с ним Confluence откажет, если страницу успели поменять
    после показа. Сверка после записи (`publishers.verify`) ничего не
    отменяет — документ уже записан, — но расхождение видно в итоге.
    """
    scope = recorder.proposal["scope"] if recorder else ""
    results = []
    for page in pages:
        title, document = page["title"], page["document"]
        where = _location_of(publisher, title)
        if completed and where in completed:
            results.append({**completed[where], "role": page.get("role", "")})
            continue
        if recorder:
            recorder.step(lambda book, w=where, p=page: book.begin(
                scope, "page", w, content_hash=p.get("digest", ""), title=p["title"]))
        try:
            kwargs = {"expected": expected.get(where, {})} if expected is not None else {}
            result = dict(publisher.publish(title, document, **kwargs))
        except publishers.PublishError as exc:
            result = {"status": "failed", "title": title, "reason": str(exc)}
            if recorder:
                recorder.step(lambda book, w=where, r=str(exc): book.fail(scope, "page", w, r))
        else:
            check = publishers.verify(publisher, result, title, document)
            if check:
                result["verified"] = check.get("verified", actions.UNVERIFIED)
                if check.get("detail"):
                    result["verify_detail"] = check["detail"]
            if recorder:
                remote = str(result.get("page_id") or result.get("path") or "")

                def done(book, w=where, r=result, c=check, remote=remote):
                    book.finish(scope, "page", w, remote=remote, url=str(r.get("url") or ""))
                    if c:
                        book.checked(scope, "page", w, c.get("verified", actions.UNVERIFIED),
                                     detail=c.get("detail", ""), remote_hash=c.get("remote_hash"))

                recorder.step(done)
        result["role"] = page.get("role", "")
        results.append(result)
    return results


def _resumed_pages(publisher: publishers.Publisher, pages: list[dict], approved: dict,
                   fresh: dict, recorder: actions.Recorder) -> tuple[dict, str]:
    """Уже записанные страницы этого действия; чужая правка остаётся конфликтом."""
    previous = {item["where"]: item for item in approved.get("pages") or []}
    current = {item["where"]: item for item in fresh.get("pages") or []}
    if (approved.get("target") != fresh.get("target")
            or approved.get("format") != fresh.get("format")
            or approved.get("destination") != fresh.get("destination")
            or approved.get("digest") != fresh.get("digest")
            or set(previous) != set(current)):
        return {}, "; ".join(commitment_changes(approved, fresh))
    done: dict[str, dict] = {}
    for page in pages:
        where = _location_of(publisher, page["title"])
        before = previous.get(where)
        if before is None:
            continue
        record = recorder.step(lambda book, w=where: book.record(
            recorder.proposal["scope"], "page", w))
        if not record or record.get("action_id") != recorder.proposal["id"]:
            continue
        if record.get("content_hash") != page.get("digest"):
            return {}, f"{page['title']}: журнал относится к другому содержимому"
        if record.get("state") not in (actions.PENDING, actions.UNKNOWN, actions.COMPLETED):
            continue
        remote = str(record.get("remote") or (current.get(where) or {}).get("page_id") or "")
        result = {"status": "updated", "title": page["title"], "page_id": remote,
                  "path": str(record.get("remote") or where), "url": str(record.get("url") or "")}
        check = publishers.verify(publisher, result, page["title"], page["document"])
        if check.get("verified") != actions.VERIFIED:
            return {}, f"{page['title']}: прежняя запись не подтверждена чтением цели"
        if record["state"] != actions.COMPLETED:
            recorder.step(lambda book, w=where, r=result: book.finish(
                recorder.proposal["scope"], "page", w,
                remote=str(r.get("page_id") or r.get("path") or ""), url=r["url"],
                detail="восстановлено чтением цели"))
        recorder.step(lambda book, w=where, c=check: book.checked(
            recorder.proposal["scope"], "page", w, actions.VERIFIED,
            remote_hash=c.get("remote_hash")))
        done[where] = {**result, "status": "created" if before.get("action") == "create"
                       else "updated", "verified": actions.VERIFIED, "recovered": True}
        # Собственная запись могла изменить create → update и версию страницы.
        # Для сравнения одобряемого набора она остаётся прежним предложением.
        current[where] = before
    normalized = {**fresh, "pages": [current.get(item["where"], item)
                                    for item in fresh.get("pages") or []]}
    differences = commitment_changes(approved, normalized)
    return done, ("; ".join(differences) if differences else "")


def verification_of(results: list[dict]) -> dict:
    """Итог сверки публикации по страницам. Пусто — цель не сверяет."""
    checked = [r for r in results if r.get("verified")]
    if not checked:
        return {}
    return {
        "verified": [r["title"] for r in checked if r["verified"] == actions.VERIFIED],
        "differs": [{"title": r["title"], "reason": r.get("verify_detail", "")}
                    for r in checked if r["verified"] == actions.DIFFERS],
        "unverified": [{"title": r["title"], "reason": r.get("verify_detail", "")}
                       for r in checked if r["verified"] == actions.UNVERIFIED],
    }


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

    Во вложенном прогоне здесь же выпускается предложение публикации — план
    считается независимо от PUBLISH_REQUIRE_APPROVAL: спрашивать или нет,
    решит вызывающий, а показать ему есть что в любом случае.
    """
    inner = nested(config)
    if not inner and not cfg.publish_require_approval():
        return {}

    plan = publish_plan(state, config, pipeline)
    if plan["skip"] or plan["collisions"]:
        return {"publication_plan": {}}

    # Один опрос цели на остановку: он же рисует черновики, он же фиксирует
    # create/update и версию страницы в одобряемом наборе.
    ask = getattr(plan["publisher"], "preview", None) or (lambda title: {"action": "unknown"})
    previews = [ask(page["title"]) for page in plan["pages"]]
    commitment = publish_commitment(plan, previews)
    update = {"publication_plan": {**commitment, "drafts": drafts.pages(plan, previews)}}
    if not inner:
        # Предложение — в журнал действий до вопроса: по нему видно, что
        # показали оператору, даже если ответа так и не было.
        actions.Recorder(
            publish_action(update["publication_plan"], graph=pipeline.key, thread=_thread(config))
        ).propose()
    if inner:
        update["proposals"] = proposals.offer(
            state,
            {
                "kind": "publish",
                "graph": pipeline.key,
                "approval_required": cfg.publish_require_approval(),
                "prompt": _approval_payload(
                    state, config, pipeline, plan, update["publication_plan"]["drafts"]
                ),
                "effect": proposed_effect(plan, commitment),
            },
        )
    return update


def proposed_effect(plan: dict, commitment: dict) -> dict:
    """
    Что уедет по предложению: одобряемый набор и сами страницы.

    Страницы лежат целиком, с шапкой: вызывающий граф их не пересобирает — у
    него нет ни состояния, ни описания конвейера, по которым они собирались.
    Сверяется перед записью набор (`publish_proposed`), а не пересборка.
    """
    return {
        "commitment": commitment,
        "pages": [
            {
                "role": page.get("role", ""),
                "title": page["title"],
                "document": page["document"],
                "digest": page["digest"],
            }
            for page in plan["pages"]
        ],
    }


def _approval_payload(
    state: State, config: RunnableConfig, pipeline: Pipeline, plan: dict, pages: list[dict]
) -> dict:
    """Что оператор видит на остановке перед публикацией — или в предложении."""
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
        "drafts": pages,
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
    return payload


def approve_node(state: State, config: RunnableConfig, pipeline: Pipeline = roles.PIPELINE) -> dict:
    """
    Остановка на подтверждение оператором перед публикацией.

    `interrupt()` замораживает тред и отдаёт наружу собранные документы;
    возобновление — `Command(resume=...)`. В Studio и в чат-интерфейсе это
    работает без дополнительного кода, поэтому кнопки писать не нужно.

    Оператора не дёргают зря: если публикация и так не состоится (выключена,
    отложена, документы не изменились), нода молча пропускает ход. Во
    вложенном прогоне не дёргают вовсе: вопрос уехал предложением.
    """
    if nested(config) or not cfg.publish_require_approval():
        return {}

    prepared = state.get("publication_plan") or {}
    plan = publish_plan(state, config, pipeline)
    if plan["skip"] or plan["collisions"] or not prepared:
        # Нечего подтверждать: публикация не состоится, план невыполним или
        # подготовка его не сохранила.
        return {}

    action = publish_action(prepared, graph=pipeline.key, thread=_thread(config))
    answer = interrupt(
        actions.shown(_approval_payload(state, config, pipeline, plan, prepared["drafts"]), action)
    )
    # Решение относится к показанному набору, а не к слову «опубликовать»:
    # отпечаток из ответа сверяется с показанным (`actions.bind`), а сам
    # набор — с пересчитанным перед записью (`_stale_approval`).
    decision = actions.bind(answer, action["digest"])
    decision["plan_digest"] = action["digest"]
    actions.Recorder(action).decide(decision)
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
    if decision.get("decision") == actions.STALE:
        # Ответ пришёл с отпечатком другой версии набора (`actions.bind`).
        return str(decision.get("reason") or "решение относится к другой версии плана")
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
    return {**update, "summary": summary_of({**state, **update}, pipeline)}


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

    inner = nested(config)
    if plan["skip"]:
        status, reason = plan["skip"]
        # Резервное сохранение — тоже запись наружу, пусть и на свой диск:
        # вложенный прогон его не делает, документ остаётся в состоянии.
        if (status == "skipped" and not inner and pipeline.rejection_fallback
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

    if inner:
        # Предложение выпущено подготовкой (`prepare_node`); писать по нему
        # будет вызывающий граф, после своего решения (`publish_proposed`).
        return skip("proposed", "вложенный прогон: публикация передана вызывающему графу")

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
        publication = {
            "status": "drafts" if not failed else "failed" if failed == len(results) else "partial",
            "title": title,
            "pages": results,
            "reason": "Откройте черновики по ссылкам. Правки и публикация — в Confluence.",
        }
        actions.Recorder(
            publish_action(state.get("publication_plan") or {}, graph=pipeline.key,
                           thread=_thread(config))
        ).done(publication["status"], publication)
        return {"document": document, "publication": publication}

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

    publisher = plan["publisher"]
    prepared = state.get("publication_plan") or {}
    approved = cfg.publish_require_approval() and bool(prepared)
    # Предложение этой записи: одобренный набор, а без вопроса — набор без
    # предпросмотра цели (судьба страниц «unknown»): спрашивать wiki второй
    # раз ради журнала незачем.
    commitment = prepared if approved else publish_commitment(plan, [{} for _ in plan["pages"]])
    recorder = actions.Recorder(publish_action(commitment, graph=pipeline.key, thread=_thread(config)))
    if not approved:
        recorder.propose()
    expected = None
    if cfg.publish_require_approval() and publisher.name == "confluence":
        expected = {page.get("where"): page for page in prepared.get("pages") or []}
    results = write_pages(publisher, plan["pages"], expected=expected, recorder=recorder)

    status = _rollup(results)
    publication = {"status": status, "title": title, "pages": results}

    # Причина — только когда есть о чём говорить, и собранная из страниц:
    # «не удалось» без указания, какая именно, отправляет человека читать логи.
    broken = [r for r in results if r.get("status") == "failed"]
    if broken:
        publication["reason"] = "; ".join(
            f"{r['title']}: {r.get('reason', 'без причины')}" for r in broken
        )
    check = verification_of(results)
    if check:
        publication["verification"] = check
    recorder.done(status, publication)
    if recorder.error:
        publication["journal_error"] = recorder.error

    update = {"document": document, "publication": publication}
    # Хеш обновляем, только если уехало всё: иначе следующий прогон в режиме
    # `changed` решил бы, что публиковать нечего, и упавшая страница осталась
    # бы ненаписанной навсегда.
    if not broken:
        update["document_hash"] = plan["digest"]
    return update


def publish_proposed(effect: dict, *, approval: dict | None = None, thread: str = "",
                     graph: str = "") -> dict:
    """
    Опубликовать страницы из предложения вложенного прогона.

    Сверяется то же, что сверяет `publish_node` перед записью по согласию:
    набор, пересчитанный по текущим настройкам и текущему состоянию цели,
    обязан совпасть с предложенным. Между предложением и решением оператора
    успевают поменяться PUBLISH_TARGET, PUBLISH_DIR и сама страница на той
    стороне — и тогда писать нельзя: согласие давали другому.

    Запись идёт тем же порядком, что у самого графа: действие и решение — в
    журнал треда вызывающего (`thread`), страницы — через журнал операций и
    сверку после записи.
    """
    approved = effect.get("commitment") or {}
    pages = effect.get("pages") or []
    if not pages:
        return {"status": "nothing", "reason": "в предложении нет страниц"}

    if not publishers.is_enabled():
        return {"status": "disabled", "reason": "этап выключен через CONFLUENCE_PUBLISH"}
    publisher = publishers.current()
    absent = publisher.missing()
    if absent:
        return {"status": "skipped", "reason": credentials.missing_message(absent)}

    plan = {"publisher": publisher, "pages": pages, "digest": approved.get("digest")}
    recorder = actions.Recorder(publish_action(approved, graph=graph, thread=thread))
    completed, changes = _resumed_pages(
        publisher, pages, approved, publish_commitment(plan), recorder
    )
    if changes:
        return {
            "status": "stale",
            "reason": "план изменился после предложения (" + changes + ")",
        }

    if approval:
        recorder.decide({**approval_of(approval), "digest": recorder.proposal["digest"]})
    else:
        recorder.propose()
    expected = None
    if publisher.name == "confluence":
        expected = {page.get("where"): page for page in approved.get("pages") or []}
    results = write_pages(publisher, pages, expected=expected, recorder=recorder,
                          completed=completed)

    publication = {"status": _rollup(results), "target": publisher.name, "pages": results}
    broken = [r for r in results if r.get("status") == "failed"]
    if broken:
        publication["reason"] = "; ".join(
            f"{r['title']}: {r.get('reason', 'без причины')}" for r in broken
        )
    check = verification_of(results)
    if check:
        publication["verification"] = check
    recorder.done(publication["status"], publication)
    if recorder.error:
        publication["journal_error"] = recorder.error
    return publication

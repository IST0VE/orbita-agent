"""
Предложения вложенного прогона: внешнее действие, отданное вызывающему графу.

Граф, запущенный сам по себе, доводит ход до конца: спрашивает оператора и
пишет наружу — публикует страницы, заводит задачи, запускает нагрузку. Под
оркестратором так нельзя. Вложенный граф спросил бы оператора сам, потом тот
же вопрос задал бы внешний; публикация уехала бы у вложенного и ещё раз у
внешнего. Поэтому вложенный прогон (`runtime.nested`) на месте каждой такой
остановки ничего не спрашивает и ничего не пишет, а кладёт в `proposals`
предложение — и вызывающий собирает их, спрашивает оператора один раз
и применяет одобренное здесь же, через `apply`.

Предложение — это та же остановка, только не заданная:

  kind               вид действия; совпадает с `action` остановки, которую
                     граф задал бы сам, — по нему интерфейс выбирает форму;
  graph              какой граф предложил;
  approval_required  спросил бы граф оператора по своим настройкам. Решает
                     политика графа, а не вызывающего: PUBLISH_REQUIRE_APPROVAL
                     и JIRA_CREATE_REQUIRE_APPROVAL остаются в силе;
  prompt             ровно то, что граф показал бы в `interrupt()`;
  effect             что именно будет сделано: документы, план карточек,
                     одобряемый набор публикации;
  id                 отпечаток всего перечисленного. Решение оператора
                     привязывается к нему, как `plan_digest` к плану публикации.

Применяются не все виды. Публикацию и заведение задач можно выполнить по
одному предложению: всё нужное в нём есть, а перед записью сверяется то же,
что сверяет сам граф. Запуск НТ и вопрос уточнения — нет: за ними идёт
продолжение графа (прогоны, анализ, новый план), и выполнить их значит
запустить `nt_run` самостоятельным прогоном.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Виды, которые `apply` умеет выполнить. Остальные предложения — сведения
#: для вызывающего, а не действие.
APPLICABLE = frozenset({"publish", "jira"})


class NotApplicable(ValueError):
    """Предложение этого вида по одному предложению не выполнить."""


def digest_of(item: dict) -> str:
    """Отпечаток предложения — всего, кроме самого отпечатка."""
    body = {key: value for key, value in item.items() if key != "id"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def offer(state: dict, item: dict) -> list[dict]:
    """
    Список предложений хода с новым: не больше одного на вид действия.

    Узел, выпустивший предложение, после остановки выполняется заново, а
    внешний граф может вызвать вложенный повторно в том же ходе. Второе
    предложение того же вида заменяет первое, а не встаёт рядом: иначе
    вызывающий показал бы оператору одну публикацию дважды.
    """
    sealed = {**item, "id": digest_of(item)}
    kept = [p for p in state.get("proposals") or [] if p.get("kind") != item["kind"]]
    return [*kept, sealed]


def apply(proposal: dict, approval: Any = None, *, thread: str = "") -> dict:
    """
    Выполнить предложение после решения вызывающего графа.

    `approval` — решение оператора в тех же формах, что понимает граф
    (`publish_nodes.approval_of`), и с полем `id`: одобряется конкретное
    предложение, а не вид действия. Без решения выполняется только то, о чём
    граф по своим настройкам не спросил бы. Отказы и расхождения возвращаются
    статусом, как у узлов публикации: к этому моменту документы уже написаны
    и оплачены, и ронять вызывающего из-за них нельзя.

    `thread` — тред вызывающего: по нему журнал Jira узнаёт повтор заведения.
    Для заведения задач он обязателен (`jira_graph.create_proposed`), и его
    отсутствие — ошибка вызывающего, а не отказ: она всплывает сразу, а не
    после согласия оператора.
    """
    from agent.publish_nodes import approval_of

    kind = str(proposal.get("kind") or "")
    if kind not in APPLICABLE:
        raise NotApplicable(
            f"предложение {kind!r} выполняется только самим графом {proposal.get('graph')!r}"
        )
    if kind == "jira" and not str(thread or "").strip():
        raise ValueError("заведение задач по предложению требует треда вызывающего графа")
    if digest_of(proposal) != proposal.get("id"):
        return {"status": "stale", "reason": "предложение изменено после выпуска"}

    if approval is None:
        if proposal.get("approval_required", True):
            return {"status": "rejected", "reason": "нет решения оператора"}
        answer: dict = {}
    else:
        answer = approval if isinstance(approval, dict) else {"decision": approval}
        if answer.get("decision") == "drafts":
            # Черновики Confluence и формы Jira граф создаёт сам, на своей
            # остановке. По предложению их нет: отказ, а не молчаливая запись.
            return {"status": "rejected", "reason": "черновики по предложению не создаются"}
        decision = approval_of(answer)
        if decision["decision"] != "approved":
            return {"status": "rejected", "reason": decision["reason"] or "оператор отказал"}
        if answer.get("id") != proposal["id"]:
            return {"status": "stale", "reason": "решение относится к другому предложению"}

    # Дальше — тот же порядок, что у графа на своей остановке (`actions.py`):
    # решение и итог пишутся в журнал действий треда вызывающего, записи
    # проходят через журнал операций и сверку после записи.
    if kind == "publish":
        from agent.publish_nodes import publish_proposed

        return publish_proposed(
            proposal["effect"], approval=answer or None, thread=thread,
            graph=str(proposal.get("graph") or ""),
        )

    from agent.jira_graph import create_proposed

    return create_proposed(
        proposal["effect"], thread=thread, project=str(answer.get("project") or ""),
        approval=answer or None,
    )

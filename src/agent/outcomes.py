"""
Оценка результата прогона: принят ли он командой и чего стоила проверка.

Главная метрика пилота — трудозатраты до принятого результата. Из треда
видно, сколько было ходов и сколько стоила модель, но не видно главного:
принял ли человек результат и сколько минут ушло на проверку и правку. Это
знает только он, поэтому интерфейс спрашивает (`OutcomeCard`), а здесь ответ
сохраняется вместе со снимком треда на момент оценки.

Оценка одна на тред и принадлежит его владельцу. Повторная оценка заменяет
прежнюю, но время первой остаётся: от него считается время до результата,
и уточнение через неделю не должно его сдвигать.

Хранилище — Postgres (миграция 6), как у изменений: без базы оценки некуда
положить, и роут отвечает 503, а не притворяется, что сохранил.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage

from agent import config as cfg
from agent import cost, critic, db, flow

#: Вердикты и их названия. Порядок — от лучшего к худшему, так их и показывают.
VERDICTS: dict[str, str] = {
    "accepted": "принят как есть",
    "edited": "принят с правками",
    "reworked": "переделан",
    "rejected": "отклонён",
}
MAX_MINUTES = 10000
MAX_REASON = 2000

_COLUMNS = (
    "thread_id", "owner", "graph", "verdict", "minutes", "reason", "thread_created_at",
    "turns", "cost_usd", "llm_calls", "refs", "verified", "findings", "jira_created",
    "created_at", "updated_at",
)


class OutcomeError(ValueError):
    """Оценку не принять: неизвестный вердикт, минуты не числом."""


def available() -> None:
    if not cfg.postgres_configured():
        raise db.DatabaseUnavailable("база не настроена (POSTGRES_URI не задан)")


def parse(body: Mapping[str, Any]) -> dict:
    """Вердикт, минуты и причина из тела запроса — или отказ словами."""
    verdict = str(body.get("verdict") or "")
    if verdict not in VERDICTS:
        raise OutcomeError("вердикт — один из: " + ", ".join(VERDICTS))
    minutes = body.get("minutes")
    if minutes in (None, ""):
        minutes = None
    else:
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float, str)):
            raise OutcomeError("минуты — целое число")
        try:
            minutes = int(str(minutes).strip())
        except ValueError as exc:
            raise OutcomeError("минуты — целое число") from exc
        if not 0 <= minutes <= MAX_MINUTES:
            raise OutcomeError(f"минуты — от 0 до {MAX_MINUTES}")
    reason = str(body.get("reason") or "").strip()
    if len(reason) > MAX_REASON:
        raise OutcomeError(f"причина длиннее {MAX_REASON} символов")
    return {"verdict": verdict, "minutes": minutes, "reason": reason}


def snapshot(thread: Mapping[str, Any], values: Mapping[str, Any] | None) -> dict:
    """
    Что известно о треде на момент оценки: из его метаданных и состояния.

    Ходы — сообщения человека: каждое запускало прогон. Проверка ссылок есть
    только у конвейера подготовки (`citations`), задачи Jira — у декомпозиции
    (`issues` с ключами). Чего у графа нет, то остаётся пустым, а не нулём:
    ноль найденных ссылок и «проверки не было» — разные ответы.
    """
    values = values or {}
    metadata = thread.get("metadata") or {}
    messages = values.get("messages") or []
    turns = sum(
        1 for message in messages
        if isinstance(message, HumanMessage)
        or (isinstance(message, Mapping) and message.get("type") in {"human", "user"})
    )
    usage = values.get("usage") or {}
    checked = critic.summary(values["citations"]) if values.get("citations") else None
    issues = values.get("issues")
    return {
        "graph": str(metadata.get("graph_id") or ""),
        "thread_created_at": flow.parse_time(thread.get("created_at")),
        "turns": turns or None,
        "cost_usd": round(cost.spent_usd(dict(values)), 6) if values else None,
        "llm_calls": int(usage.get("calls") or 0) if usage else None,
        "refs": checked["refs"] if checked else None,
        "verified": checked["verified"] if checked else None,
        "findings": checked["findings"] if checked else None,
        "jira_created": (
            sum(1 for item in issues if isinstance(item, Mapping) and item.get("key"))
            if isinstance(issues, list) else None
        ),
    }


class PostgresOutcomes:
    """Оценки в базе приложения."""

    def get(self, owner: str, thread_id: str) -> dict | None:
        with db.connection() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM run_outcomes"
                " WHERE thread_id = %s AND owner = %s",
                (thread_id, owner),
            ).fetchone()
        return _row(row) if row else None

    def save(self, owner: str, thread_id: str, answer: Mapping[str, Any],
             seen: Mapping[str, Any]) -> dict:
        """
        Записать оценку. Чужой тред с оценкой не перезаписывается.

        Владельца треда проверяет роут, но и здесь запись идёт только поверх
        своей строки: `WHERE run_outcomes.owner = excluded.owner`. Иначе тред,
        переданный другому человеку, унёс бы с собой и право переписать чужую
        оценку.
        """
        with db.connection() as conn, conn.transaction():
            row = conn.execute(
                f"INSERT INTO run_outcomes ({', '.join(_COLUMNS[:-2])})"
                f" VALUES ({', '.join(['%s'] * (len(_COLUMNS) - 2))})"
                " ON CONFLICT (thread_id) DO UPDATE SET"
                " graph = excluded.graph, verdict = excluded.verdict,"
                " minutes = excluded.minutes, reason = excluded.reason,"
                " thread_created_at = excluded.thread_created_at, turns = excluded.turns,"
                " cost_usd = excluded.cost_usd, llm_calls = excluded.llm_calls,"
                " refs = excluded.refs, verified = excluded.verified,"
                " findings = excluded.findings, jira_created = excluded.jira_created,"
                " updated_at = now()"
                " WHERE run_outcomes.owner = excluded.owner"
                f" RETURNING {', '.join(_COLUMNS)}",
                (
                    thread_id, owner, seen.get("graph") or "", answer["verdict"],
                    answer.get("minutes"), answer.get("reason") or "",
                    seen.get("thread_created_at"), seen.get("turns"), seen.get("cost_usd"),
                    seen.get("llm_calls"), seen.get("refs"), seen.get("verified"),
                    seen.get("findings"), seen.get("jira_created"),
                ),
            ).fetchone()
        if row is None:
            raise OutcomeError("тред оценил другой человек")
        return _row(row)

    def summary(self, days: int) -> list[dict]:
        """
        Оценки за `days` дней по сценариям: сколько каких, сколько минут и денег.

        Без владельцев и причин: сводку смотрит администратор пилота, и ему
        нужно, где результат принимают, а где переделывают, а не кто именно.
        """
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT graph, verdict, count(*),"
                " percentile_cont(0.5) WITHIN GROUP (ORDER BY minutes),"
                " sum(minutes), avg(cost_usd),"
                " percentile_cont(0.5) WITHIN GROUP"
                " (ORDER BY extract(epoch FROM created_at - thread_created_at) / 3600)"
                " FROM run_outcomes WHERE created_at >= now() - make_interval(days => %s)"
                " GROUP BY graph, verdict ORDER BY graph, verdict",
                (int(days),),
            ).fetchall()
        return [
            {
                "graph": graph, "verdict": verdict, "count": count,
                "minutes_p50": _float(minutes_p50), "minutes_total": minutes_total,
                "cost_avg": _float(cost_avg), "hours_to_verdict_p50": _float(hours_p50),
            }
            for graph, verdict, count, minutes_p50, minutes_total, cost_avg, hours_p50 in rows
        ]


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _row(row: Sequence[Any]) -> dict:
    found = dict(zip(_COLUMNS, row, strict=True))
    for key in ("thread_created_at", "created_at", "updated_at"):
        if isinstance(found[key], datetime):
            found[key] = found[key].isoformat()
    found.pop("owner", None)
    found["verdict_title"] = VERDICTS.get(found["verdict"], found["verdict"])
    return found


store = PostgresOutcomes()

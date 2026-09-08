"""Validated, source-backed replacements applied to the original text."""

from __future__ import annotations

import difflib
import json
import re


class UpdateError(ValueError):
    pass


def apply_plan(original: str, materials: dict[str, str], response: str) -> dict:
    raw = response.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", raw, re.DOTALL)
    if fence:
        raw = fence[1]
    try:
        plan = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise UpdateError("Модель не вернула корректный JSON изменений.") from exc
    if not isinstance(plan, dict) or set(plan) != {"changes", "questions"}:
        raise UpdateError("План должен содержать только changes и questions.")
    changes, questions = plan["changes"], plan["questions"]
    if not isinstance(changes, list) or len(changes) > 100:
        raise UpdateError("Ожидается список не более 100 изменений.")
    if not isinstance(questions, list) or any(
        not isinstance(q, str) or not q.strip() for q in questions
    ):
        raise UpdateError("Вопросы должны быть списком непустых строк.")

    spans = []
    for index, change in enumerate(changes, 1):
        required = {"old", "new", "source", "quote", "reason"}
        if not isinstance(change, dict) or set(change) != required:
            raise UpdateError(f"Изменение {index}: неверный набор полей.")
        if any(not isinstance(value, str) for value in change.values()):
            raise UpdateError(f"Изменение {index}: поля должны быть строками.")
        old, new = change["old"], change["new"]
        if not old or original.count(old) != 1:
            raise UpdateError(f"Изменение {index}: исходный фрагмент не найден однозначно.")
        if old == new or not change["reason"].strip():
            raise UpdateError(f"Изменение {index}: нет изменения или его обоснования.")
        source, quote = change["source"], change["quote"]
        if source not in materials or not quote.strip() or quote not in materials[source]:
            raise UpdateError(f"Изменение {index}: цитата не найдена в выбранном источнике.")
        start = original.index(old)
        spans.append((start, start + len(old), new))
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:], strict=False)):
        raise UpdateError("Изменения пересекаются. Нужен новый план с отдельными фрагментами.")
    updated = original
    for start, end, replacement in reversed(spans):
        updated = updated[:start] + replacement + updated[end:]
    if not updated.strip():
        raise UpdateError("Обновление удаляет документ целиком.")
    diff = "\n".join(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            lineterm="",
            fromfile="Основной документ",
            tofile="Обновлённый документ",
        )
    )
    return {"document": updated, "changes": changes, "questions": questions, "diff": diff}


def report(result: dict) -> str:
    parts = ["# Изменения"]
    for index, change in enumerate(result["changes"], 1):
        parts.append(
            f"## {index}. {change['reason']}\n\n"
            f"Источник: {change['source']}\n\nЦитата:\n{change['quote']}"
        )
    if not result["changes"]:
        parts.append("Подтверждённых изменений нет.")
    if result["questions"]:
        parts.append(
            "# Открытые вопросы\n\n"
            + "\n".join(f"- {question}" for question in result["questions"])
        )
    if result["diff"]:
        # A long fence keeps source code fences inside the diff intact.
        fence = "`" * max(
            4, max((len(m[0]) + 1 for m in re.finditer(r"`+", result["diff"])), default=4)
        )
        parts.append(f"# Разница\n\n{fence}diff\n{result['diff']}\n{fence}")
    return "\n\n".join(parts)

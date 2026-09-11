"""What metrics exist for a scoped service in the test period, as exporters describe them."""

from __future__ import annotations

import json
import re

from agent.nt.models import failure

# Имена метрик в тексте задачи выглядят одинаково: слова из латиницы через
# подчёркивание. Их выбор из описания НТ ничего не разрешает дополнительно —
# только двигает нужное наверх списка, в котором на боевом Prometheus тысячи
# имён, а модели достанется несколько десятков.
_HINT = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+){1,5}")
_STOP = frozenset({
    "test_id", "started_at", "finished_at", "target_service", "target_rps", "previous_test_id",
    "error_rate", "jira_key", "max_stable_rps", "load_testing", "content_type",
})

MAX_HELP = 160


def hints(text: str, limit: int = 12) -> list[str]:
    """Похожие на метрики слова из текста задачи, в порядке появления."""
    found: list[str] = []
    for match in _HINT.finditer((text or "").lower()):
        word = match[0]
        if word not in _STOP and word not in found:
            found.append(word)
        if len(found) >= limit:
            break
    return found


def selector(namespace: str, service: str) -> str:
    """Селектор области: только этот сервис и только этот namespace."""
    return f"{{namespace={json.dumps(namespace)}, service={json.dumps(service)}}}"


def discover(sources, state: dict, service: str, *, contains: str = "", limit: int = 40) -> dict:
    """Список метрик сервиса с типом и HELP, ограниченный по объёму."""
    prometheus = getattr(sources, "prometheus", None)
    if not prometheus:
        return failure("NOT_CONFIGURED", "Prometheus is not configured on the server")
    if service not in state.get("services", []):
        return failure("OUT_OF_SCOPE", "service is outside the analysis scope")

    found = prometheus.series(selector(state["namespace"], service),
                              state["started_at"], state["finished_at"])
    if not found.get("success"):
        return found

    labels: dict[str, set] = {}
    for row in found["series"]:
        name = row.get("__name__")
        if isinstance(name, str) and name:
            labels.setdefault(name, set()).update(k for k in row if k != "__name__")
    if not labels:
        return {"success": False, "error_type": "NO_METRICS",
                "message": "no series for this service in the test period"}

    # HELP берётся одним запросом на весь сервер: он не зависит от namespace,
    # а отдельный запрос на каждое имя превратил бы discovery в сотню вызовов.
    described = prometheus.metadata()
    meta = described.get("metadata", {}) if described.get("success") else {}

    wanted = [word for word in hints(state.get("task_description", "")) if any(
        word in name for name in labels)]
    needle = (contains or "").strip().lower()[:60]
    names = sorted(name for name in labels if needle in name.lower())
    names.sort(key=lambda name: (not any(word in name for word in wanted), name))

    metrics = []
    for name in names[:limit]:
        entry = meta.get(name, {})
        metrics.append({"metric": name, "type": entry.get("type", ""),
                        "help": (entry.get("help", "") or "")[:MAX_HELP],
                        "labels": sorted(labels[name])})
    return {"success": True, "service": service, "namespace": state["namespace"],
            "metrics": metrics, "total": len(names), "shown": len(metrics),
            "hints": wanted,
            "note": "names and labels existed during the test period; units are not verified"}

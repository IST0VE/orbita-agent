"""Map plan facts to fields actually exposed by the project's create screen."""

from __future__ import annotations

import re
from urllib.parse import quote

from agent import jira, jira_plan

ALIASES = {
    "component": {"components", "component", "компоненты", "компонент"},
    "service": {"service", "сервис", "служба"},
    "layer": {"layer", "слой"},
    "acceptance": {"acceptance criteria", "критерии приемки", "критерии приёмки"},
    "estimate": {"story points", "story point estimate", "оценка в story points", "оценка"},
}


def read(project: str, type_id: str, settings: jira.Settings) -> dict[str, dict]:
    """Read paginated Cloud/DC metadata, falling back to the legacy create API."""
    path = f"{settings.api_path}/issue/createmeta/{quote(project, safe='')}/issuetypes/{quote(type_id, safe='')}"
    found: dict[str, dict] = {}
    start = 0
    try:
        while True:
            data = jira.call("GET", path, settings, params={"startAt": start, "maxResults": 200})
            rows = data.get("values", data.get("fields", [])) if isinstance(data, dict) else data
            if isinstance(rows, dict):
                return rows
            for row in rows or []:
                if isinstance(row, dict):
                    key = row.get("fieldId") or row.get("key")
                    if key:
                        found[str(key)] = row
            start += len(rows or [])
            if (
                not isinstance(data, dict)
                or not rows
                or data.get("isLast") is True
                or (data.get("total") is not None and start >= data["total"])
                or ("total" not in data and data.get("isLast") is not False)
            ):
                return found
    except jira.JiraError:
        if found:
            return found
    try:
        data = jira.call(
            "GET",
            f"{settings.api_path}/issue/createmeta",
            settings,
            params={
                "projectKeys": project,
                "issuetypeIds": type_id,
                "expand": "projects.issuetypes.fields",
            },
        )
    except jira.JiraError:
        return {}
    for project_row in data.get("projects", []):
        for row in project_row.get("issuetypes", []):
            if str(row.get("id")) == type_id:
                return row.get("fields") or {}
    return {}


def map_item(item: jira_plan.Item, metadata: dict[str, dict]) -> tuple[dict, set[str], list[str]]:
    """Only unambiguous field names and allowed values may leave the description."""
    fields: dict = {}
    mapped: set[str] = set()
    warnings: list[str] = []
    for attr, aliases in ALIASES.items():
        value = getattr(item, attr)
        if not value:
            continue
        matches = [
            (key, row)
            for key, row in metadata.items()
            if str(row.get("name", key)).strip().casefold() in aliases
        ]
        if len(matches) != 1:
            warnings.append(
                f"{item.local}: поле {attr} не сопоставлено; значение оставлено в описании."
            )
            continue
        key, row = matches[0]
        schema = row.get("schema") or {}
        kind = schema.get("type")
        encoded = None
        if row.get("allowedValues"):
            options = [
                option
                for option in row["allowedValues"]
                if isinstance(option, dict)
                and str(option.get("name", option.get("value", ""))).casefold()
                == str(value).casefold()
            ]
            if len(options) == 1 and options[0].get("id") is not None:
                encoded = {"id": str(options[0]["id"])}
                if kind == "array":
                    encoded = [encoded]
        elif kind in {"number", "integer"} and attr == "estimate":
            match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:SP|story points)?", str(value), re.I)
            if match and (kind != "integer" or float(match[1]).is_integer()):
                encoded = float(match[1]) if kind == "number" else int(float(match[1]))
        elif kind == "string":
            encoded = "\n".join(value) if attr == "acceptance" else value
        if encoded is None:
            warnings.append(
                f"{item.local}: значение {attr} не подходит полю {row.get('name', key)}; оставлено в описании."
            )
            continue
        fields[key] = encoded
        mapped.add(attr)
    return fields, mapped, warnings


def form_values(fields: dict) -> dict:
    """Legacy create URLs use option IDs, while REST uses objects with an id."""

    def value(item):
        return item["id"] if isinstance(item, dict) else item

    return {
        key: [value(item) for item in val] if isinstance(val, list) else value(val)
        for key, val in fields.items()
    }

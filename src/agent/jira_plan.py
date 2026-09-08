"""
Разбор карточек: из документа последнего этапа — список задач для трекера.

Между текстом, который читает человек, и запросом, который уходит в Jira,
обязан стоять разбор кодом. Модель пишет документы: карту реализации, backlog,
ревью — их читают глазами. Заводить по ним задачи нельзя: в тексте «Epic 1.
Платежи» нет границы между заголовком и содержанием, и любая попытка выделить
её регуляркой ломается на первом же документе, написанном чуть иначе.

Поэтому последний этап конвейера выпускает документ, у которого в конце стоит
блок ```json``` с теми же задачами в машинной форме, а этот модуль его достаёт,
проверяет и нормализует. Всё, чего в контракте нет, отбрасывается с записанной
причиной: заведённая не туда задача дороже незаведённой.

Что здесь проверяется и почему именно здесь:

  тип        Jira откажет на неизвестном типе всей пачкой, а не одной задачей;
  заголовок  пустой summary — это 400 от API, и узнать об этом надо до сети;
  родитель   ссылка на несуществующий локальный ключ или на самого себя даёт
             цикл, которого в трекере уже не будет видно;
  порядок    эпики заводятся первыми: у детей должен быть настоящий ключ
             родителя, а не локальный;
  потолок    декомпозиция, поехавшая в сотню задач, — это ошибка модели, и
             платить за неё сотней карточек в чужом проекте нельзя.

Локальные ключи (`EPIC-1`, `TASK-3`) остаются локальными до самого конца: они
живут в плане и в описании зависимостей, а настоящие ключи появляются только
после ответа трекера — см. `jira_writer.create_issues`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace

from agent import config as cfg

# Типы, которые понимает конвейер. Слева — то, что может написать модель
# (включая русские названия из локализованных инстансов), справа — канон,
# который дальше сопоставляется с типами конкретного проекта.
_TYPES = {
    "epic": "Epic",
    "эпик": "Epic",
    "story": "Story",
    "история": "Story",
    "task": "Task",
    "задача": "Task",
    "sub-task": "Sub-task",
    "subtask": "Sub-task",
    "sub task": "Sub-task",
    "подзадача": "Sub-task",
    "bug": "Bug",
    "баг": "Bug",
}

EPIC = "Epic"
SUBTASK = "Sub-task"

# Потолок Jira на заголовок — 255 символов. Обрезаем здесь, а не ждём отказа
# API: заголовок в 300 символов это плохая формулировка, а не сбой, и терять
# из-за неё всю пачку незачем.
SUMMARY_LIMIT = 255
# Описание одной карточки. Столько же, сколько читается из задачи: описание,
# которое не помещается в этот лимит, читать всё равно не будут.
DESCRIPTION_LIMIT = 12000

_FENCE = re.compile(r"```(?:json|JSON)?\s*(?P<body>[\[{].*?)```", re.DOTALL)


class PlanError(ValueError):
    """Документ последнего этапа не разбирается в список задач."""


@dataclass(frozen=True)
class Item:
    """Одна карточка плана: то, из чего собирается запрос на создание задачи."""

    local: str
    type: str
    summary: str
    description: str = ""
    parent: str = ""
    labels: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    estimate: str = ""
    service: str = ""
    component: str = ""
    layer: str = ""

    @property
    def is_epic(self) -> bool:
        return self.type == EPIC

    def body(self, keys: dict[str, str] | None = None, source: str = "", *,
             mapped: set[str] | None = None) -> str:
        """
        Описание задачи для трекера.

        Собирается здесь, а не моделью, по той же причине, по которой здесь
        стоит разбор: поля плана должны попасть в задачу все и в одном и том же
        порядке, а не так, как в этот раз написалось.

        `keys` — уже заведённые задачи по локальным ключам. Зависимость на
        задачу, которой ещё нет, остаётся локальным ключом: врать про `ORB-14`,
        которого не существует, хуже, чем показать `TASK-2` и связь в плане.
        """
        keys = keys or {}
        mapped = mapped or set()
        parts = [self.description.strip()] if self.description.strip() else []

        if self.acceptance and "acceptance" not in mapped:
            parts.append(
                "Критерии приёмки:\n" + "\n".join(f"- {line}" for line in self.acceptance)
            )

        facts = []
        where = " / ".join(getattr(self, attr) for attr in ("service", "component", "layer")
                           if attr not in mapped and getattr(self, attr))
        if where:
            facts.append(f"Область: {where}")
        if self.depends_on:
            facts.append(
                "Зависит от: "
                + ", ".join(keys.get(local, local) for local in self.depends_on)
            )
        if self.estimate and "estimate" not in mapped:
            facts.append(f"Оценка: {self.estimate}")
        if facts:
            parts.append("\n".join(facts))

        if source:
            parts.append(f"Заведено конвейером Orbita по источнику: {source}")
        return "\n\n".join(parts)[:DESCRIPTION_LIMIT]


@dataclass
class Plan:
    """Разобранный план: карточки в порядке заведения и всё, что отброшено."""

    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def epics(self) -> list[Item]:
        return [item for item in self.items if item.is_epic]

    def summary(self) -> str:
        """Одна строка о плане: сколько чего. Уезжает оператору на подтверждение."""
        if not self.items:
            return "план пуст"
        epics = len(self.epics)
        rest = len(self.items) - epics
        parts = []
        if epics:
            parts.append(f"эпиков {epics}")
        if rest:
            parts.append(f"задач {rest}")
        return ", ".join(parts)

    def table(self) -> str:
        """План списком — то, что оператор видит перед заведением задач."""
        lines = []
        for item in self.items:
            parent = f" ← {item.parent}" if item.parent else ""
            lines.append(f"- [{item.local}] {item.type}: {item.summary}{parent}")
        return "\n".join(lines)


def _text(value: object, limit: int = DESCRIPTION_LIMIT, *, one_line: bool = False) -> str:
    """Строка из значения плана: не объект, не длиннее лимита, без сюрпризов."""
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    text = str(value)
    return (" ".join(text.split()) if one_line else text.strip())[:limit]


def _lines(value: object) -> tuple[str, ...]:
    """Список строк из чего угодно: модель пишет и списком, и одним абзацем."""
    if isinstance(value, list):
        return tuple(" ".join(str(item).split()) for item in value if str(item).strip())
    text = str(value or "").strip()
    if not text:
        return ()
    return tuple(line.strip(" -•\t") for line in text.splitlines() if line.strip(" -•\t"))


def extract_json(document: str) -> object:
    """
    Достать машинный блок из документа этапа.

    Документ у этапа двойной: сверху таблица для человека, снизу блок ```json```
    для трекера. Сначала ищется именно блок в тройных кавычках — он объявлен в
    промпте, и полагаться надо на объявленное. Если модель кавычки потеряла,
    берётся первая сбалансированная структура от `{` или `[`: это уже спасение
    прогона, а не контракт, поэтому и стоит вторым.
    """
    text = document or ""
    for match in _FENCE.finditer(text):
        try:
            return json.loads(match.group("body"))
        except json.JSONDecodeError:
            continue

    opening = [position for position in (text.find("{"), text.find("[")) if position >= 0]
    start = min(opening, default=-1)
    if start < 0:
        raise PlanError("в документе нет ни блока ```json```, ни объекта JSON")

    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise PlanError(f"блок JSON не разбирается: {exc}") from exc
    return value


def parse(document: str, limit: int | None = None) -> Plan:
    """
    Документ последнего этапа — в план заведения задач.

    Порядок на выходе — порядок заведения: сначала эпики, потом задачи, потом
    подзадачи. Это не косметика, а требование трекера: у ребёнка в запросе
    стоит настоящий ключ родителя, а он появляется только после его создания.
    """
    raw = extract_json(document)
    if isinstance(raw, dict):
        rows = raw.get("issues") or raw.get("tasks") or raw.get("items")
    else:
        rows = raw
    if not isinstance(rows, list):
        raise PlanError("в блоке JSON нет списка `issues`")

    plan = Plan()
    seen: dict[str, Item] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            plan.warnings.append(f"строка {index}: не объект, пропущена")
            continue

        # Локальный ключ читается первым, хотя обязателен заголовок: причина
        # отказа должна называть карточку так, как её видит оператор в
        # документе, а «строка 4» ему ни о чём не говорит.
        local = _text(row.get("id") or row.get("local_id") or row.get("key"), 60, one_line=True)
        local = local or f"ITEM-{index}"
        if local in seen:
            plan.warnings.append(f"{local}: локальный ключ повторяется, вторая карточка пропущена")
            continue

        summary = _text(row.get("summary") or row.get("title"), SUMMARY_LIMIT, one_line=True)
        if not summary:
            plan.warnings.append(f"{local}: нет заголовка, карточка пропущена")
            continue

        named = _text(row.get("type") or row.get("issue_type"), 60, one_line=True)
        kind = _TYPES.get(named.lower())
        if kind is None:
            plan.warnings.append(f"{local}: неизвестный тип {row.get('type')!r}, заведём задачей")
            kind = "Task"

        item = Item(
            local=local,
            type=kind,
            summary=summary,
            description=_text(row.get("description")),
            parent=_text(row.get("parent"), 60, one_line=True),
            labels=tuple(
                label
                for label in _lines(row.get("labels"))
                if re.fullmatch(r"[\w./-]{1,60}", label)
            ),
            acceptance=_lines(row.get("acceptance") or row.get("acceptance_criteria")),
            depends_on=_lines(row.get("depends_on") or row.get("dependencies")),
            estimate=_text(row.get("estimate"), SUMMARY_LIMIT, one_line=True),
            service=_text(row.get("service"), SUMMARY_LIMIT, one_line=True),
            component=_text(row.get("component"), SUMMARY_LIMIT, one_line=True),
            layer=_text(row.get("layer"), SUMMARY_LIMIT, one_line=True),
        )
        seen[local] = item
        plan.items.append(item)

    _resolve_parents(plan, seen)
    _order(plan)
    _cap(plan, cfg.jira_max_issues() if limit is None else limit)
    return plan


def _resolve_parents(plan: Plan, seen: dict[str, Item]) -> None:
    """
    Убрать родителей, которых нет, и родителей, которые ими быть не могут.

    Проверок три, и каждая закрывает свою поломку в трекере: ссылка на
    несуществующий локальный ключ — 400 при заведении; ссылка на самого себя —
    цикл; подзадача под эпиком — Jira такого не допускает, а модель пишет так
    регулярно, потому что в документе это выглядит логично.
    """
    fixed: list[Item] = []
    for item in plan.items:
        parent = item.parent
        if not parent:
            fixed.append(item)
            continue

        problem = ""
        if parent == item.local:
            problem = "родитель — она сама"
        elif parent not in seen:
            problem = f"родителя {parent} нет в плане"
        elif item.is_epic:
            problem = "у эпика не бывает родителя"
        elif item.type == SUBTASK and seen[parent].is_epic:
            problem = "подзадача не может лежать прямо в эпике"
        elif item.type != SUBTASK and not seen[parent].is_epic:
            problem = f"родитель {parent} — не эпик"

        if problem:
            plan.warnings.append(f"{item.local}: {problem}, связь снята")
            item = replace(item, parent="")
        fixed.append(item)
    plan.items = fixed


def _order(plan: Plan) -> None:
    """
    Эпики, потом их дети, потом подзадачи: порядок заведения в трекере.

    Сортировка устойчивая, и порядок внутри уровня остаётся тем, в каком
    карточки написаны: он не случайный — это порядок реализации из плана.
    """
    rank = {EPIC: 0, SUBTASK: 2}
    plan.items.sort(key=lambda item: rank.get(item.type, 1))


def _cap(plan: Plan, limit: int) -> None:
    """
    Потолок на пачку.

    Отрезается хвост, а не случайные карточки: план отсортирован от эпиков к
    подзадачам, и потерять лучше самое мелкое. Обрезанное перечисляется —
    оператор должен видеть, что заведено не всё.
    """
    if len(plan.items) <= limit:
        return
    dropped = plan.items[limit:]
    plan.items = plan.items[:limit]
    plan.warnings.append(
        f"план длиннее потолка JIRA_MAX_ISSUES={limit}: не заведены "
        + ", ".join(item.local for item in dropped)
    )

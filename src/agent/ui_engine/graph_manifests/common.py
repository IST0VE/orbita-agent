"""Shared manifest fragments for the five pipeline-shaped graphs."""

from __future__ import annotations

from agent.pipeline import Pipeline


def pipeline_nodes(pipeline: Pipeline) -> dict:
    """
    Описания узлов конвейера — ровно тех, которые у графа есть.

    Управляющие узлы у пяти конвейеров разные, и объявлять их скопом
    нельзя: манифест — это подпись под топологией, а не список того, что
    бывает у графов вообще. Лишняя запись здесь не роняет интерфейс: она
    описывает коробку, которой на экране никогда не будет, — и ровно поэтому
    её никто не заметит и не поправит.

    Раньше состав узлов приезжал сюда флагами (`admission=True`,
    `tools_description=None`), то есть объявлялся второй раз и руками, хотя
    всё это уже сказано в описании конвейера: узел инструментов есть тогда,
    когда есть роль, которая в него ходит; `no_input` — когда объявлена
    проверка входа; `source`, `ticket`, `create` — когда объявлены прелюдия
    и постлюдия. Теперь состав выводится, и разойтись описанию конвейера
    с его подписью больше нечем.

    Сверку с настоящей топологией по-прежнему держит тест
    `test_manifest_nodes_match_the_compiled_graph`: имена узлов манифеста
    обязаны совпасть с именами узлов скомпилированного графа.
    """
    role_list = list(pipeline.roles)
    nodes: dict[str, dict] = {
        role.key: {
            "title": role.title,
            "description": role.summary,
            "kind": "task",
            "group": "pipeline",
            "color": "info" if index < len(role_list) - 1 else "success",
            "output": {"path": f"artifacts.{role.key}", "widget": "markdown"},
            "layout": {"rank": index * 2 + 2, "order": index},
        }
        for index, role in enumerate(role_list)
    }
    for index, role in enumerate(role_list[1:], start=1):
        nodes[f"gate_{role.key}"] = {
            "title": f"Ворота · {role.title}",
            "description": "Проверяет бюджет и, если настроено, подтверждение оператора.",
            "kind": "router",
            "group": "control",
            "color": "warning",
            "layout": {"rank": index * 2 + 1, "order": index},
        }
    nodes.update(
        {
            "context": {
                "title": "Контекст",
                "description": "Добавляет справку, память и список файлов задачи.",
                "kind": "system",
                "group": "control",
            },
            "over_budget": {
                "title": "Лимит бюджета",
                "description": "Оставшиеся модельные этапы пропущены из-за лимита стоимости.",
                "kind": "system",
                "color": "warning",
            },
            "halted": {
                "title": "Остановлено оператором",
                "description": "Продолжение конвейера отклонено на воротах этапа.",
                "kind": "system",
                "color": "warning",
            },
            "remember": {
                "title": "Память",
                "description": "Сохраняет безопасное резюме хода в долгую память.",
                "kind": "system",
                "group": "finalization",
            },
            "approve": {
                "title": "Подтверждение публикации",
                "description": "Ожидает решения оператора перед внешним side effect.",
                "kind": "approval",
                "group": "finalization",
                "color": "warning",
            },
            "publish": {
                "title": "Публикация",
                "description": "Публикует документы разрешённой настроенной целью.",
                "kind": "task",
                "group": "finalization",
                "color": "success",
            },
        }
    )
    if pipeline.has_tools:
        nodes["tools"] = {
            "title": "Инструменты",
            "description": pipeline.tools_hint,
            "kind": "tool",
            "group": "control",
        }
    if pipeline.admission:
        nodes["no_input"] = {
            "title": "Нет входа",
            "description": "Остановка до первого вызова модели: нет материала для обработки.",
            "kind": "system",
            "color": "warning",
        }
    if pipeline.prelude is not None:
        # Добыча материала: стоит до первой роли и денег не тратит.
        nodes[pipeline.prelude.key] = {
            "title": pipeline.prelude.title,
            "description": pipeline.prelude.summary,
            "kind": "system",
            "group": "input",
        }
    if pipeline.postlude is not None:
        # Внешнее действие по готовому документу — такой же результат прогона,
        # как документ, поэтому `task`, а не `system`.
        nodes[pipeline.postlude.key] = {
            "title": pipeline.postlude.title,
            "description": pipeline.postlude.summary,
            "kind": "task",
            "group": "finalization",
            "color": "success",
        }
    return nodes


def base_manifest(pipeline: Pipeline) -> dict:
    """
    Манифест конвейера целиком: всё, что выводится из его описания.

    Конвейеру остаётся дополнить его тем, что описанием не выводится, —
    своим полем ввода, своим виджетом результата, своей остановкой. Трём
    конвейерам из четырёх дополнять нечего.
    """
    return {
        "schema_version": "1.0",
        "manifest_version": "2026.09.03.1",
        "graph_id": pipeline.key,
        "title": {"ru": pipeline.title, "en": pipeline.key},
        "description": {"ru": pipeline.summary, "en": pipeline.summary},
        "tags": ["orbita", "langgraph", "pipeline"],
        "capabilities": {
            "new_thread": True,
            "stop_run": True,
            "resume_interrupt": True,
            "history": False,
            "retry_node": False,
        },
        "input": [
            {
                "id": "question",
                "target": "messages",
                "widget": "chat-input",
                "title": "Задача",
                "required": True,
            },
            {
                "id": "task",
                "target": "configurable.input_dir",
                "widget": "task-picker",
                "title": "Папка задачи",
                "source": {"resource_id": "orbita.tasks", "operation": "list"},
                # Клик по файлу внутри папки выбирает источник, а не открывает
                # его на просмотр. Куда уезжает выбор, что этому полю годится и
                # сколько файлов в него влезает, сказано здесь: виджет не знает
                # про `document` по имени, как не знает и про `task`.
                "options": {
                    "document_input": "document",
                    "document_kind": "text",
                    "document_multiple": True,
                },
            },
            {
                "id": "document",
                "target": "configurable.input_file",
                "widget": "file-picker",
                # Панель показывает выбранное, а не всё содержимое папки: тот же
                # список уже стоит деревом выше, и второй такой же — это не
                # выбор, а шум, в котором выбранный файл ничем не выделен.
                "title": "Выбранные документы",
                "source": {"resource_id": "orbita.tasks", "operation": "list"},
                # Комплект документации это несколько файлов: требования без
                # контракта API раскладываются в задачи, которых нет.
                "options": {"kind": "text", "depends_on": "task", "multiple": True},
            },
        ],
        "nodes": pipeline_nodes(pipeline),
        "state": [
            {
                "id": "messages",
                "path": "messages",
                "title": "Прогон",
                "widget": "messages",
                "surface": "main",
                "order": 20,
                "empty": "show",
            },
            {
                "id": "artifacts",
                "path": "artifacts",
                "title": "Документы этапов",
                "widget": "artifact-list",
                "surface": "left",
                "order": 30,
                "empty": "placeholder",
            },
            {
                "id": "published",
                "path": "publication",
                "title": "Опубликованные документы",
                "widget": "published-list",
                "surface": "left",
                "order": 40,
                "empty": "show",
            },
            {
                "id": "cost",
                "path": "cost",
                "title": "Стоимость",
                "widget": "cost-summary",
                "surface": "right",
                "order": 20,
                "empty": "placeholder",
            },
            {
                "id": "publication",
                "path": "publication",
                "title": "Публикация",
                "widget": "publication",
                "surface": "right",
                "order": 30,
                "empty": "placeholder",
            },
        ],
        "interrupts": [
            {
                "id": "stage-approval",
                "priority": 20,
                "match": {"path": "action", "equals": "stage"},
                "widget": "approval",
                "resume_schema": {
                    "type": "object",
                    "required": ["decision"],
                    "additionalProperties": False,
                    "properties": {
                        "decision": {"enum": ["approved", "rejected"]},
                        "reason": {"type": "string", "maxLength": 4000},
                    },
                },
            },
            {
                "id": "publish-approval",
                "priority": 10,
                "match": {"path": "action", "equals": "publish"},
                "widget": "approval",
                "resume_schema": {
                    "type": "object",
                    "required": ["decision"],
                    "additionalProperties": False,
                    "properties": {
                        "decision": {"enum": ["approved", "rejected", "drafts"]},
                        "reason": {"type": "string", "maxLength": 4000},
                    },
                },
            },
        ],
        "surfaces": [
            {"id": "header", "order": 0, "widgets": []},
            {
                "id": "left",
                "order": 10,
                "widgets": ["task", "document", "artifacts", "published"],
            },
            {"id": "main", "order": 20, "widgets": ["messages"]},
            {"id": "right", "order": 30, "widgets": ["cost", "publication"]},
            {"id": "bottom", "order": 40, "widgets": ["timeline"], "collapsible": True},
            {"id": "modal", "order": 50, "widgets": ["interrupt"]},
        ],
        "actions": [
            {"id": "new-thread", "kind": "thread.create", "label": "Новый тред"},
            {"id": "start-run", "kind": "run.start", "label": "Запустить"},
            {"id": "stop-run", "kind": "run.stop", "label": "Остановить", "confirm": True},
            {
                "id": "resume-interrupt",
                "kind": "interrupt.resume",
                "label": "Продолжить",
            },
            {"id": "open-document", "kind": "publication.open", "label": "Открыть документ"},
            {"id": "refresh", "kind": "resource.refresh", "label": "Обновить"},
        ],
        "redaction": [
            {"path": "messages.*.response_metadata.raw_prompt", "mode": "remove"},
            {"path": "messages.*.additional_kwargs.authorization", "mode": "remove"},
            {"path": "provider_request", "mode": "metadata_only"},
        ],
        "theme": {"canvas": "terminal", "accent": "cyan"},
    }

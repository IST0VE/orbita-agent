"""
Инструменты, которые граф отдаёт модели, и наборы, в которых они ездят.

Раньше их было два, и они жили в `graph.py`. Конвейеров стало четыре, и у
одного из них свой источник данных — Jira и Confluence вместо папки задачи, —
поэтому набор инструментов перестал быть свойством графа и стал свойством
конвейера (`pipeline.Pipeline.tools`).

Главное правило, ради которого модуль устроен именно так: JSON-схемы
инструментов уходят в тот же кешируемый префикс, что и системный промпт. Имена,
описания и сигнатуры менять между ходами треда нельзя — это ровно тот же
антипаттерн, что дата в начале промпта. Отсюда два следствия:

  * набор фиксирован для конвейера целиком и не зависит от того, что оператор
    выбрал в интерфейсе и что настроено в окружении. Инструмент, для которого
    нет настроек, объявлен и возвращает внятный отказ; исчезнуть он не может;
  * реализация источника данных отделена от контракта инструмента. Настоящий
    источник приносит настоящие отказы — сети нет, токен протух, страницы
    не существует, — и все они обязаны стать текстом ответа, а не исключением:
    ответ оператору важнее, чем строка из справочника.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from agent import confluence, inputs
from agent import jira as jira_api
from agent.runtime import options


def _unavailable(what: str, exc: Exception) -> str:
    """Текст для модели вместо данных. Он же уедет оператору в ответе."""
    return (
        f"{what} временно недоступен ({exc}). Данные не получены — сообщите "
        "оператору, что справка не открылась, и предложите повторить запрос."
    )


# --------------------------------------------------------------------------
# Файлы задачи
# --------------------------------------------------------------------------
@tool
def list_task_files(config: RunnableConfig) -> str:
    """Перечислить файлы, приложенные к текущей задаче."""
    chosen = options(config)
    task = str(chosen.get("input_dir") or "")
    if not task:
        return "к задаче не приложено файлов: папка не выбрана"
    try:
        files = next((t["files"] for t in inputs.list_tasks() if t["name"] == task), [])
    except OSError as exc:
        return _unavailable("папка задачи", exc)
    if not files:
        return f"в папке задачи {task!r} нет файлов"
    # Выбор оператора видно и здесь, а не только в списке, подставленном в конец
    # задачи: роль могла дойти до инструмента на втором круге, когда до начала
    # сообщения ей уже далеко, — и решить по списку без пометки, что выбора
    # не было. Тот же текст в двух местах дешевле такого решения.
    picked = inputs.picked_names(chosen.get("input_file"))
    names = {f["name"] for f in files}
    lines = [
        f"{f['name']} ({f['size']} байт)" + (" ← выбран оператором" if f["name"] in picked else "")
        for f in files
    ]
    lost = [name for name in picked if name not in names]
    if lost:
        lines.append(
            "ВНИМАНИЕ: выбранных оператором файлов в папке нет: " + ", ".join(lost)
            if len(lost) > 1
            else f"ВНИМАНИЕ: выбранного оператором файла {lost[0]} в папке нет"
        )
    return "\n".join(lines)


@tool
def read_task_file(name: str, config: RunnableConfig) -> str:
    """Прочитать текстовый файл, приложенный к текущей задаче, по его имени."""
    task = str(options(config).get("input_dir") or "")
    if not task:
        return "к задаче не приложено файлов: папка не выбрана"
    try:
        return inputs.read(task, name)
    except inputs.InputError as exc:
        return f"файл не прочитан: {exc}"
    except OSError as exc:
        return _unavailable(f"файл {name!r}", exc)


# --------------------------------------------------------------------------
# Jira и Confluence
#
# Все четыре — только на чтение. Инструмента, который заводит задачу или
# правит страницу, здесь нет и быть не должно: side effect в этом проекте
# проходит через ноду публикации с подтверждением оператора, а не через
# решение модели посреди этапа.
#
# Ненастроенная интеграция — это отказ с перечислением переменных, а не
# молчание. Иначе роль напишет план по пустому месту и не заметит.
# --------------------------------------------------------------------------
def _not_configured(system: str, absent: list[str]) -> str:
    return (
        f"{system} не настроена: не заданы {', '.join(absent)}. Данные не получены. "
        "Не выдумывай содержимое — отметь в документе, что источник недоступен, "
        "и перечисли, что осталось непроверенным."
    )


@tool
def jira_issue(key: str) -> str:
    """Прочитать задачу Jira по её ключу (например ORB-123): описание, поля, связи, комментарии."""
    absent = jira_api.missing_vars()
    if absent:
        return _not_configured("Jira", absent)
    try:
        return jira_api.format_issue(jira_api.fetch_issue(key))
    except jira_api.JiraError as exc:
        return f"задача {key!r} не прочитана: {exc}"


@tool
def jira_search(query: str) -> str:
    """Найти задачи Jira по тексту запроса: ключ, тип, статус и заголовок каждой."""
    absent = jira_api.missing_vars()
    if absent:
        return _not_configured("Jira", absent)
    try:
        found = jira_api.search(query)
    except jira_api.JiraError as exc:
        return f"поиск в Jira не выполнен: {exc}"
    return jira_api.format_results(found) or f"по запросу {query!r} задач не найдено"


@tool
def confluence_search(query: str) -> str:
    """Найти страницы Confluence по тексту запроса: идентификатор, заголовок и ссылку."""
    absent = confluence.missing_vars()
    if absent:
        return _not_configured("Confluence", absent)
    try:
        found = confluence.search(query)
    except confluence.ConfluenceError as exc:
        return f"поиск в Confluence не выполнен: {exc}"
    return confluence.format_results(found) or f"по запросу {query!r} страниц не найдено"


@tool
def confluence_page(page_id: str) -> str:
    """Прочитать страницу Confluence по её числовому идентификатору из результатов поиска."""
    absent = confluence.missing_vars()
    if absent:
        return _not_configured("Confluence", absent)
    try:
        return confluence.format_page(confluence.fetch_page(page_id))
    except confluence.ConfluenceError as exc:
        return f"страница {page_id!r} не прочитана: {exc}"


# --------------------------------------------------------------------------
# Наборы
#
# Набор привязывается ко ВСЕМ ролям конвейера, хотя в цикл с инструментами
# уходят не все (`pipeline.Role.reads_files`). Провайдеры сериализуют
# объявления инструментов вместе с началом запроса, поэтому роль без привязки
# посылала бы запрос другой формы — и общее начало префикса, ради которого всё
# затевалось, перестало бы совпадать именно там, где должно совпадать больше
# всего.
# --------------------------------------------------------------------------
FILE_TOOLS = [list_task_files, read_task_file]

# Файловые входят и сюда: `context_node` дописывает список файлов задачи в
# сообщение любого конвейера и обещает модели `read_task_file`. Набор без них
# сделал бы это обещание ложным.
RESEARCH_TOOLS = [*FILE_TOOLS, jira_issue, jira_search, confluence_search, confluence_page]

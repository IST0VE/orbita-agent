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

from agent import confluence, evidence, inputs
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
        files = inputs.files_of(task)
    except (inputs.InputError, OSError) as exc:
        return _unavailable("папка задачи", exc)
    if not files:
        return f"в папке «{inputs.title_for(task)}» нет файлов"
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


def _read_file(name: str, config: RunnableConfig) -> tuple[str | None, str, dict]:
    """Текст файла или отказ для модели, и след для реестра прочитанного."""
    # След для реестра прочитанного (`ledger.py`): модель его не видит.
    trace = {"kind": "read", "system": "file", "name": name}
    task = str(options(config).get("input_dir") or "")
    if not task:
        return None, "к задаче не приложено файлов: папка не выбрана", {**trace, "error": "нет папки"}
    try:
        return inputs.read(task, name), "", trace
    except inputs.InputError as exc:
        return None, f"файл не прочитан: {exc}", {**trace, "error": str(exc)}
    except OSError as exc:
        return None, _unavailable(f"файл {name!r}", exc), {**trace, "error": str(exc)}


def _answer(item: evidence.EvidenceItem, cited: bool) -> tuple[str, dict]:
    """
    Прочитанное и его запись Evidence.

    Запись в следе есть всегда: по ней код знает версию, хеш и чей токен
    читал, какой бы конвейер ни спрашивал. Шапку с id модель видит только у
    конвейера, чьи роли ссылаются на Evidence (`cited`): остальные пишут
    прежние теги, и чужой идентификатор модель переписывала бы в документы,
    где его никто не проверяет.
    """
    if cited:
        return evidence.attach(item)
    return item.text, {**item.to_dict(text=False), "skip": 0}


def _file_answer(name: str, config: RunnableConfig, cited: bool) -> tuple[str, dict]:
    text, refusal, trace = _read_file(name, config)
    if text is None:
        return refusal, trace
    truncated = "[…файл обрезан на " in text[-120:]
    content, meta = _answer(evidence.for_file(name, text, truncated=truncated), cited)
    return content, {**trace, "evidence": meta}


@tool(response_format="content_and_artifact")
def read_task_file(name: str, config: RunnableConfig) -> tuple[str, dict]:
    """Прочитать текстовый файл, приложенный к текущей задаче, по его имени."""
    return _file_answer(name, config, cited=False)


# Тот же инструмент для конвейера, чьи роли ссылаются на Evidence: имя,
# описание и аргументы совпадают побайтово, иначе поменялась бы схема в
# кешируемом префиксе. Отличается ответ — перед текстом стоит id источника.
@tool("read_task_file", response_format="content_and_artifact")
def read_task_file_cited(name: str, config: RunnableConfig) -> tuple[str, dict]:
    """Прочитать текстовый файл, приложенный к текущей задаче, по его имени."""
    return _file_answer(name, config, cited=True)


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


# Ответ инструмента — это текст для модели и след для кода. Текст модель
# пересказывает, и пересказ теряет идентификаторы и путает счёт: 27 сентября
# 2026 документ называл «12 непрочитанных страниц» при списке из шестнадцати.
# След (`ToolMessage.artifact`) модель не видит, а код собирает из него реестр
# прочитанного и найденного (`ledger.py`) — с точными ссылками и числами.
_EMPTY_HINT = (
    "Попробуй 1–2 слова или точное имя из материалов: утилиты, файла, модуля, "
    "таблицы. Формы слов подбираются автоматически."
)


def _issue_answer(key: str, cited: bool) -> tuple[str, dict]:
    trace = {"kind": "read", "system": "jira", "key": str(key or "").strip().upper()}
    absent = jira_api.missing_vars()
    if absent:
        return _not_configured("Jira", absent), {**trace, "error": "не настроена"}
    try:
        issue = jira_api.fetch_issue(key)
    except jira_api.JiraError as exc:
        return f"задача {key!r} не прочитана: {exc}", {**trace, "error": str(exc)}
    content, meta = _answer(evidence.for_issue(issue, jira_api.format_issue(issue)), cited)
    return content, {
        **trace,
        "key": str(issue.get("key") or trace["key"]),
        "summary": issue.get("summary") or "",
        "url": issue.get("url") or "",
        "evidence": meta,
    }


@tool(response_format="content_and_artifact")
def jira_issue(key: str) -> tuple[str, dict]:
    """Прочитать задачу Jira по её ключу (например ORB-123): описание, поля, связи, комментарии."""
    return _issue_answer(key, cited=False)


@tool("jira_issue", response_format="content_and_artifact")
def jira_issue_cited(key: str) -> tuple[str, dict]:
    """Прочитать задачу Jira по её ключу (например ORB-123): описание, поля, связи, комментарии."""
    return _issue_answer(key, cited=True)


@tool(response_format="content_and_artifact")
def jira_search(query: str, project: str = "") -> tuple[str, dict]:
    """
    Найти задачи Jira по 1–3 словам запроса: ключ, тип, статус и заголовок каждой.

    project — ключ проекта или несколько через запятую (например «ORB, PAY»):
    искать только в них, а не во всём трекере. Содержание задачи — через jira_issue.
    """
    trace = {"kind": "search", "system": "jira", "query": query, "scope": "все проекты"}
    absent = jira_api.missing_vars()
    if absent:
        return _not_configured("Jira", absent), {**trace, "error": "не настроена"}
    try:
        keys = jira_api.project_keys(project)
        scope = ", ".join(keys) or "все проекты"
        found = jira_api.search(query, broad=True, projects=keys)
    except jira_api.JiraError as exc:
        return f"поиск в Jira не выполнен: {exc}", {**trace, "error": str(exc)}
    trace = {
        **trace,
        "scope": scope,
        "found": [{"key": item.get("key", ""), "summary": item.get("summary", ""),
                   "url": item.get("url", "")} for item in found],
    }
    if not found:
        return f"По запросу {query!r} (проекты: {scope}) задач не найдено. {_EMPTY_HINT}", trace
    return f"Проекты поиска: {scope}.\n" + jira_api.format_results(found), trace


@tool(response_format="content_and_artifact")
def confluence_search(query: str) -> tuple[str, dict]:
    """
    Найти страницы Confluence по 1–3 словам: имя системы, утилиты, файла, термин из задачи.

    Возвращает идентификатор, заголовок, ссылку и фрагмент совпадения каждой
    страницы. Текст страницы — через confluence_page.
    """
    trace = {"kind": "search", "system": "confluence", "query": query, "scope": ""}
    absent = confluence.missing_vars()
    if absent:
        return _not_configured("Confluence", absent), {**trace, "error": "не настроена"}
    try:
        found = confluence.search(query, broad=True)
    except confluence.ConfluenceError as exc:
        return f"поиск в Confluence не выполнен: {exc}", {**trace, "error": str(exc)}
    try:
        scope = confluence.scope_label()
    except confluence.ConfluenceError:
        scope = ""  # поиск прошёл, а подпись области — не повод его потерять
    trace = {
        **trace,
        "scope": scope,
        "found": [{"id": str(item.get("id", "")), "title": item.get("title", ""),
                   "url": item.get("url", "")} for item in found],
    }
    where = f" ({scope})" if scope else ""
    if not found:
        return f"По запросу {query!r}{where} страниц не найдено. {_EMPTY_HINT}", trace
    head = f"Область поиска: {scope}.\n" if scope else ""
    return head + confluence.format_results(found), trace


def _page_answer(page_id: str, cited: bool) -> tuple[str, dict]:
    trace = {"kind": "read", "system": "confluence", "id": str(page_id or "").strip()}
    absent = confluence.missing_vars()
    if absent:
        return _not_configured("Confluence", absent), {**trace, "error": "не настроена"}
    try:
        page = confluence.fetch_page(page_id)
    except confluence.ConfluenceError as exc:
        return f"страница {page_id!r} не прочитана: {exc}", {**trace, "error": str(exc)}
    item = evidence.for_page(
        {**page, "id": page.get("id") or trace["id"]}, confluence.format_page(page)
    )
    content, meta = _answer(item, cited)
    return content, {
        **trace,
        "id": str(page.get("id") or trace["id"]),
        "title": page.get("title") or "",
        "url": page.get("url") or "",
        "truncated": bool(page.get("truncated")),
        # Страница, собранная самой Orbita: реестр и критик называют её отдельно.
        "own": item.own,
        "evidence": meta,
    }


@tool(response_format="content_and_artifact")
def confluence_page(page_id: str) -> tuple[str, dict]:
    """Прочитать страницу Confluence по её числовому идентификатору из результатов поиска."""
    return _page_answer(page_id, cited=False)


@tool("confluence_page", response_format="content_and_artifact")
def confluence_page_cited(page_id: str) -> tuple[str, dict]:
    """Прочитать страницу Confluence по её числовому идентификатору из результатов поиска."""
    return _page_answer(page_id, cited=True)


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

# Тот же набор для конвейера, чьи роли ссылаются на Evidence по id
# (`prep_prompts.COMMON`): имена, описания и схемы те же, ответ на чтение
# начинается с id источника. Сравнение схем — в тестах.
CITED_TOOLS = [
    list_task_files,
    read_task_file_cited,
    jira_issue_cited,
    jira_search,
    confluence_search,
    confluence_page_cited,
]

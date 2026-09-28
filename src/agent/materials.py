"""
Прелюдия конвейера аналитики: что оператор назвал сам, читает код.

Оператор называет материал двумя способами: отмечает файлы в чате и вставляет
в запрос ссылки на страницы Confluence и задачи Jira. Аргумент у такого чтения
известен до первой роли, и просить модель позвать инструмент с ним — это
оплаченный вызов, который ничего не выбирает. Тот же довод записан в
`sources.py`, и так же устроены прелюдии остальных конвейеров: подготовка
задачи читает тикет и материалы оператора (`prep_graph.ticket_node`),
декомпозиция и сверка — свой источник.

Раньше ссылки читала нода контекста и дописывала в сообщение оператора, а
выбранные файлы аналитик открывал инструментом сам. У этого было три следствия.
Прочитанное по ссылкам видел один аналитик и только на первом ходе треда: на
следующем в сообщении стояло указание «поправь раздел», и материалов в нём не
было. Выбранный файл оставался непрочитанным, если модель отвечала анонсом
вместо вызова. И ревьюер, сверяя финальную версию, не видел первоисточника
вовсе. Теперь прочитанное лежит в `artifacts` (`roles.LINKS`, `roles.FILES`) и
доходит брифом до каждой роли, которой нужно, на каждом ходе.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from agent import ledger as registry
from agent import roles, sources
from agent.state import State


def _summary(items: list[dict]) -> list[dict]:
    """Сводка прочитанного без текста: для страницы и реестра, не для модели."""
    return [{key: value for key, value in item.items() if key != "text"} for item in items]


def _note(links: list[dict], files: list[str], found: dict | None, others: list[str]) -> str:
    """Что прочитано до первой роли — оператору в тред. Ход по графу должен быть виден."""
    parts = []
    read = [
        registry.tag(item["system"], item["id"]) for item in links if not item.get("error")
    ]
    if read:
        parts.append("По ссылкам из запроса прочитаны: " + ", ".join(read) + ".")
    failed = [
        f"{registry.tag(item['system'], item['id'])} ({item['error']})"
        for item in links
        if item.get("error")
    ]
    if failed:
        parts.append("Не прочитаны: " + ", ".join(failed) + ".")
    if files:
        parts.append("Материалы оператора прочитаны: " + ", ".join(files) + ".")
    elif found and found.get("error"):
        parts.append(f"Материалы оператора не прочитаны: {found['error']}.")
    if not parts:
        return (
            "Выбранных файлов и ссылок в запросе нет: какие файлы чата относятся к "
            "задаче, решает аналитик."
            if others
            else "Файлов в чате и ссылок в запросе нет: материал — сам запрос оператора."
        )
    return " ".join(parts)


def _names(item: dict) -> set[tuple[str, str]]:
    """Под какими ключами ссылка известна: как её вернула система и как назвали."""
    return {(item["system"], item["id"]), (item["system"], item.get("asked") or item["id"])}


def _merge(known: list[dict], fresh: list[dict], block: str) -> tuple[list[dict], str]:
    """
    Сводка ссылок треда и блок для брифа после этого хода.

    Новая ссылка встаёт в конец. Ссылка, которую на прошлом ходе прочитать не
    удалось, встаёт на своё место: её прежняя причина уступает новой попытке —
    удачной или нет. Иначе в брифе висела бы старая ошибка при уже прочитанной
    странице. Текст раздела ошибки собирается из сводки теми же словами, что и
    в первый раз (`roles.links_block`), поэтому его можно найти и заменить.
    """
    links = list(known)
    where = {name: index for index, item in enumerate(links) for name in _names(item)}
    for item in fresh:
        section = roles.links_block([item])
        index = next((where[name] for name in _names(item) if name in where), None)
        stale = roles.links_block([links[index]]) if index is not None else ""
        if stale and stale in block:
            block = block.replace(stale, section, 1)
        else:
            block = f"{block}\n\n{section}" if block else section
        if index is None:
            where.update(dict.fromkeys(_names(item), len(links)))
            links.append(_summary([item])[0])
        else:
            links[index] = _summary([item])[0]
    return links, block


def materials_node(state: State, config: RunnableConfig) -> dict:
    """
    Прочитать выбранные файлы и ссылки из запроса и положить их в `artifacts`.

    Прочитанная ссылка читается один раз на тред: на следующем ходе — только
    новые и те, что в прошлый раз не открылись. Материалы у треда одни, и
    ходить за ними в Confluence на каждое «перепиши раздел» значит платить
    задержкой за данные, которые не менялись; за свежестью следит оператор,
    начиная новый тред. Непрочитанную ссылку, названную снова, стоит открыть
    ещё раз: временный сбой не должен навсегда оставлять тред без источника.

    Файлы чата читаются заново на каждом ходе: содержимое и выбор могут
    смениться при прежнем имени файла. Если в ходе ничего не выбрано и не
    названо, читаются файлы прошлого хода: указание «поправь раздел» не
    называет файлов, а материалы треда остаются прежними (`sources.materials`).

    Непрочитанное не останавливает конвейер: причина уходит в блок и в
    сообщение оператору, а аналитик обязан начать с неё документ.
    """
    question = sources.question_of(state)
    known = state.get("materials") or {}
    artifacts = state.get("artifacts") or {}
    # Тред, начатый до этой прелюдии, сводки не имеет, а его ссылки и имена
    # файлов живут в задаче треда, не в указании «поправь раздел». Без неё
    # первый же ход после обновления оставил бы аналитика без материалов.
    task = (state.get("task") or "").strip()
    if not known and task and task != question:
        question = f"{task}\n\n{question}"

    found, others = sources.materials(question, config, remembered=known.get("files") or ())
    files = list((found or {}).get("names") or [])
    files_block = roles.files_block(found, others)

    known_links = list(known.get("links") or [])
    read = {name for item in known_links if not item.get("error") for name in _names(item)}
    fresh = sources.linked(question, skip=read)
    previous = (artifacts.get(roles.LINKS) or "").strip()
    links, links_block = _merge(known_links, fresh, previous)

    update_artifacts: dict[str, str] = {}
    if files_block != (artifacts.get(roles.FILES) or ""):
        update_artifacts[roles.FILES] = files_block
    if links_block != previous:
        update_artifacts[roles.LINKS] = links_block

    summary = {"links": links, "files": files}
    first = not known
    if not first and not update_artifacts and summary == known:
        return {}

    update: dict = {"materials": summary, "stage": "materials"}
    if update_artifacts:
        update["artifacts"] = update_artifacts
    if first or fresh or files != (known.get("files") or []):
        update["messages"] = [AIMessage(content=_note(fresh, files, found, others))]
    return update


__all__ = ["materials_node"]

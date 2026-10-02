"""
Запросы оператора в чате и их версии: правка отправленного запроса и ветки.

Правка запроса — это не перезапись истории, а развилка. Интерфейс запускает
прогон заново с чекпоинта, на котором этот запрос вошёл в тред (`fork`), и
LangGraph заводит от него новую ветку; прежняя остаётся в истории треда целиком
— с ответами, документами этапов и стоимостью. Так её и можно вернуть.

Здесь — то, чего LangGraph не отдаёт готовым: какие версии были у каждого
запроса и какая из них показана сейчас. SDK умеет строить ветки сам, но из
полной истории треда, а у конвейера Orbita это десятки чекпоинтов на прогон,
каждый с документами всех этапов. Поэтому история читается на сервере и не
целиком: только чекпоинты ввода (`source=input`) — по одному на запрос.

Версии одного запроса — это вводы с одинаковым началом разговора: те же
сообщения до него. Показанная версия — та, чьё сообщение стоит в голове треда
на этом месте. Переключение версии копирует голову её ветки (`__copy__`): копия
становится последним чекпоинтом, и тред открывается уже на ней, а следующий
запрос продолжает именно эту ветку.

Владельца треда здесь не проверяют: это делает роут (`api._own_chat`), а
читают и копируют тред внутрипроцессным клиентом с правами админ-токена.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.documents import operator_question

#: Сколько чекпоинтов ввода читается за раз и всего. Запросов в одном чате
#: больше тысячи не бывает, а потолок не даёт истории с ошибкой съесть память.
_PAGE = 100
_MAX_INPUTS = 1000
#: Голову ветки ищем по истории целиком, страницами: у конвейера десятки
#: чекпоинтов на прогон, и чужая ветка может быть свежее нужной.
_SCAN_PAGE = 50
_MAX_SCAN = 5000


class TurnError(ValueError):
    """Версию не переключить: такого запроса или версии нет, тред занят."""


@dataclass
class Version:
    checkpoint_id: str
    run_id: str
    #: Id сообщения оператора. У запросов, отправленных до того, как интерфейс
    #: стал давать сообщениям id, его во вводе нет — тогда None.
    message_id: str | None
    text: str
    created_at: str


@dataclass
class Turn:
    prefix: tuple[str, ...]
    versions: list[Version] = field(default_factory=list)


def _messages(values: Any) -> list[dict]:
    if not isinstance(values, dict):
        return []
    found = values.get("messages")
    return [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []


def _text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) and part.get("type") == "text"
            else part if isinstance(part, str) else ""
            for part in content
        )
    return ""


def _human(snapshot: dict) -> dict | None:
    """Сообщение оператора, с которым начался прогон: из задачи `__start__` ввода."""
    for task in snapshot.get("tasks") or []:
        if not isinstance(task, dict) or task.get("name") != "__start__":
            continue
        result = task.get("result")
        for message in _messages(result):
            if message.get("type") == "human":
                return message
    return None


def _checkpoint_id(snapshot: dict) -> str:
    return str(((snapshot.get("checkpoint") or {}).get("checkpoint_id")) or "")


async def _inputs(client: Any, thread_id: str) -> list[dict]:
    """Чекпоинты ввода треда, старые первыми."""
    found: list[dict] = []
    before = None
    while len(found) < _MAX_INPUTS:
        page = await client.threads.get_history(
            thread_id, limit=_PAGE, before=before, metadata={"source": "input"}
        )
        if not page:
            break
        found.extend(page)
        if len(page) < _PAGE:
            break
        # Строкой, а не словарём чекпоинта: словарь сервер передал бы в историю
        # графа как есть, а та ждёт конфиг с `configurable`.
        before = _checkpoint_id(page[-1]) or None
        if before is None:
            break
    found.reverse()
    return found


def group(inputs: list[dict]) -> dict[tuple[str, ...], Turn]:
    """Вводы с одинаковым началом разговора — версии одного запроса."""
    turns: dict[tuple[str, ...], Turn] = {}
    for snapshot in inputs:
        human = _human(snapshot)
        if human is None:
            # Ввод без сообщения оператора — ответ на остановку или служебный
            # запуск: это не запрос, и версий у него нет.
            continue
        prefix = tuple(str(item.get("id") or "") for item in _messages(snapshot.get("values")))
        metadata = snapshot.get("metadata") or {}
        turns.setdefault(prefix, Turn(prefix)).versions.append(
            Version(
                checkpoint_id=_checkpoint_id(snapshot),
                run_id=str(metadata.get("run_id") or ""),
                message_id=str(human["id"]) if human.get("id") else None,
                text=_text(human),
                created_at=str(snapshot.get("created_at") or ""),
            )
        )
    return turns


def _current(turn: Turn, message: dict) -> int:
    """Какая версия показана: по id сообщения, а у старых запросов — по тексту."""
    message_id = str(message.get("id") or "")
    for index, version in enumerate(turn.versions):
        if version.message_id and version.message_id == message_id:
            return index
    text = _text(message)
    # Нода контекста дописывает к запросу справку после `---`: сравнивается
    # вопрос, а не то, что из него сделала нода.
    for index in range(len(turn.versions) - 1, -1, -1):
        version = turn.versions[index]
        if not version.message_id and operator_question(version.text) == operator_question(text):
            return index
    return -1


def describe_turns(head: dict, inputs: list[dict]) -> list[dict]:
    """Запросы оператора в голове треда: их версии и откуда переписывать каждый."""
    turns = group(inputs)
    messages = _messages(head.get("values"))
    ids = [str(item.get("id") or "") for item in messages]
    described = []
    for index, message in enumerate(messages):
        if message.get("type") != "human" or not message.get("id"):
            continue
        turn = turns.get(tuple(ids[:index]))
        position = _current(turn, message) if turn else -1
        if turn is None or position < 0:
            continue
        version = turn.versions[position]
        described.append(
            {
                "message_id": ids[index],
                # Текст для правки: вопрос оператора без дописанного кодом.
                "question": operator_question(_text(message)),
                "version": position + 1,
                "versions": len(turn.versions),
                # С этого чекпоинта прогон начнётся заново: он и есть место,
                # где запрос вошёл в тред.
                "fork": version.checkpoint_id,
            }
        )
    return described


async def describe(client: Any, thread_id: str) -> dict:
    head = await client.threads.get_state(thread_id)
    inputs = await _inputs(client, thread_id)
    return {
        "thread_id": thread_id,
        "head": _checkpoint_id(head),
        "turns": describe_turns(head, inputs),
    }


async def _branch_head(client: Any, thread_id: str, position: int, version: Version) -> str | None:
    """
    Самый свежий чекпоинт ветки этой версии: на месте `position` стоит её запрос.

    Не «последний чекпоинт её прогона»: ветка живёт и после него — ответ на
    остановку (подтверждение публикации) идёт отдельным прогоном со своим
    run_id, следующий запрос — тоже. 2 октября 2026 переключение на первую
    версию открыло чат на вопросе о публикации, на который уже ответили:
    голова бралась из прогона самого запроса, а ответ лежал в соседнем.

    История идёт от новых к старым, поэтому первый подходящий — и есть голова.
    У запросов без id (до того, как интерфейс стал их давать) сверяется текст.
    """
    before = None
    seen = 0
    while seen < _MAX_SCAN:
        page = await client.threads.get_history(thread_id, limit=_SCAN_PAGE, before=before)
        if not page:
            return None
        for snapshot in page:
            messages = _messages(snapshot.get("values"))
            if len(messages) <= position:
                continue
            message = messages[position]
            if version.message_id:
                if str(message.get("id") or "") == version.message_id:
                    return _checkpoint_id(snapshot)
            elif message.get("type") == "human" and operator_question(_text(message)) == operator_question(version.text):
                return _checkpoint_id(snapshot)
        seen += len(page)
        if len(page) < _SCAN_PAGE:
            return None
        before = _checkpoint_id(page[-1]) or None
        if before is None:
            return None
    return None


async def switch(client: Any, thread_id: str, message_id: str, version: int) -> dict:
    """
    Показать другую версию запроса: скопировать голову её ветки в конец треда.

    Голова ветки — самый свежий чекпоинт, в котором стоит эта версия запроса
    (`_branch_head`): ветка могла продолжиться, и вернуться надо туда, где в
    ней остановились, а не к ответу на сам запрос.
    """
    thread = await client.threads.get(thread_id)
    if (thread or {}).get("status") == "busy":
        raise TurnError("в чате идёт прогон — переключить версию можно после него")
    head = await client.threads.get_state(thread_id)
    inputs = await _inputs(client, thread_id)
    turns = group(inputs)
    messages = _messages(head.get("values"))
    ids = [str(item.get("id") or "") for item in messages]
    if message_id not in ids:
        raise TurnError("такого запроса в чате нет")
    position = ids.index(message_id)
    turn = turns.get(tuple(ids[:position]))
    if turn is None or not 1 <= version <= len(turn.versions):
        raise TurnError("такой версии запроса нет")
    chosen = turn.versions[version - 1]
    if chosen.message_id and chosen.message_id == message_id:
        return await describe(client, thread_id)
    # Прогон версии мог упасть раньше, чем запрос лёг в состояние: тогда
    # возвращаемся к месту, где он вошёл в чат.
    target = await _branch_head(client, thread_id, position, chosen) or chosen.checkpoint_id
    if target == _checkpoint_id(head):
        return await describe(client, thread_id)
    await client.threads.update_state(thread_id, None, as_node="__copy__", checkpoint_id=target)
    return await describe(client, thread_id)

"""
Долгая память: что известно об аккаунте поверх отдельного треда.

Чекпоинтер помнит один тред и ничего не знает о соседних. Store живёт поверх
тредов — сервер LangGraph поднимает его сам (`store.pckl` в `.langgraph_api/`),
а своему рантайму его передают в `compile(store=...)`. Это ровно то место, где
хранится «по этому аккаунту уже спрашивали вот что».

Как это устроено здесь:

  * ключ — идентификатор аккаунта, вынутый из текста по MEMORY_ACCOUNT_PATTERN.
    Память привязана к клиенту, а не к оператору и не к треду: следующий тред
    может вести другой человек, а аккаунт тот же;
  * запись — одна строка на ход: дата, о чём спрашивали, что ответили. Никакого
    дополнительного вызова модели: он стоил бы денег на каждом ходе, а текст
    уже написан;
  * чтение — в КОНЕЦ вопроса оператора, тем же каналом, что и справка из базы
    знаний (knowledge.py). Префикс не двигается, кеш не страдает.

Память необязательна. Store не подключён, сломан или пуст — агент работает
как раньше: ответ оператору не должен зависеть от вспомогательного хранилища.
"""

from __future__ import annotations

import re
from datetime import date

from agent import config as cfg

# Заголовок блока и признак того, что память в сообщение уже подставлена.
BLOCK_TITLE = "Из прошлых обращений"

# Длина фрагментов в одной записи: вопрос и первая строка ответа. Память
# уходит в промпт на каждом ходе, поэтому её объём — это деньги.
_QUESTION_CHARS = 120
_ANSWER_CHARS = 220


def store():
    """
    Store текущего прогона или None.

    `get_store()` работает только внутри ноды графа и только если store вообще
    подключён. Вне прогона (тесты, импорт, вызов из скрипта) это нормальная
    ситуация, а не ошибка.
    """
    try:
        from langgraph.config import get_store

        return get_store()
    except Exception:  # store не подключён или мы вне прогона графа
        return None


def namespace() -> tuple[str, ...]:
    """Пространство имён в store: разные стенды не смешиваются."""
    return (cfg.memory_namespace(), "accounts")


def accounts_in(text: str) -> list[str]:
    """
    Идентификаторы аккаунтов в тексте, по одному разу и в порядке появления.

    Шаблон приходит из окружения: в демо-данных это `acc-1024`, у вас —
    свой формат. Нерабочее регулярное выражение — ошибка конфигурации, а не
    повод молча ничего не найти.
    """
    pattern = cfg.memory_account_pattern()
    try:
        found = re.findall(pattern, text or "", flags=re.IGNORECASE)
    except re.error as exc:
        raise cfg.ConfigError(
            f"MEMORY_ACCOUNT_PATTERN: не компилируется {pattern!r} ({exc})"
        ) from exc

    seen: list[str] = []
    for raw in found:
        value = (raw if isinstance(raw, str) else raw[0]).lower()
        if value not in seen:
            seen.append(value)
    return seen


def _shorten(text: str, limit: int) -> str:
    """Одна строка не длиннее лимита, с честным многоточием."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def fact_from(question: str, answer: str) -> str:
    """Одна запись памяти: дата, о чём спрашивали, чем ответили."""
    first_line = next((line for line in (answer or "").splitlines() if line.strip()), "")
    return (
        f"{date.today().isoformat()}: спрашивали «{_shorten(question, _QUESTION_CHARS)}»"
        f" — ответ: {_shorten(first_line, _ANSWER_CHARS)}"
    )


def facts(account: str) -> list[str]:
    """Что помним про аккаунт. Пусто — store не подключён или записей нет."""
    active = store()
    if active is None:
        return []
    try:
        item = active.get(namespace(), account)
    except Exception:  # хранилище недоступно — работаем без памяти
        return []
    if item is None:
        return []
    value = getattr(item, "value", None) or {}
    return [str(fact) for fact in value.get("facts", []) if str(fact).strip()]


def remember(account: str, fact: str) -> bool:
    """
    Дописать факт про аккаунт. Хранится последнее: MEMORY_MAX_FACTS записей.

    Ошибка записи не должна стоить оператору ответа — ответ к этому моменту
    уже готов, а память вспомогательна. Поэтому False вместо исключения.
    """
    active = store()
    if active is None or not fact.strip():
        return False

    kept = facts(account)
    if fact in kept:  # тот же вопрос дважды за день — не плодим строки
        return False

    limit = max(cfg.memory_max_facts(), 0)
    kept = (kept + [fact])[-limit:] if limit else []
    try:
        active.put(namespace(), account, {"facts": kept})
    except Exception:  # хранилище недоступно — молча живём дальше
        return False
    return True


def recall(text: str) -> list[tuple[str, list[str]]]:
    """Память по всем аккаунтам, упомянутым в тексте. Пустые — не возвращаются."""
    found: list[tuple[str, list[str]]] = []
    for account in accounts_in(text):
        known = facts(account)
        if known:
            found.append((account, known))
    return found


def as_block(recalled: list[tuple[str, list[str]]]) -> str:
    """Память — в текст, который дописывается в конец вопроса оператора."""
    if not recalled:
        return ""
    parts = []
    for account, known in recalled:
        lines = "\n".join(f"- {fact}" for fact in known)
        parts.append(f"### {BLOCK_TITLE} по аккаунту {account}\n{lines}")
    return "\n\n---\n" + "\n\n".join(parts)


def block_for(text: str) -> str:
    """Готовый блок памяти под вопрос. Пустая строка — вспоминать нечего."""
    return as_block(recall(text))

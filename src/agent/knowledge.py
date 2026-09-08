"""
База знаний и поиск по ней — retrieval, который не ломает кеш.

Задача не в том, чтобы найти документ (лексического поиска для базы из десятка
разделов хватает с запасом), а в том, КУДА его положить. Кеш провайдера
работает от нулевого токена: любой найденный кусок, вставленный в системный
промпт, сдвигает префикс и обнуляет совпадение для всего запроса. Поэтому
справка подставляется в КОНЕЦ сообщения оператора — там она ничего не двигает,
а сам префикс остаётся побайтово тем же, что и на прошлом ходе.

Откуда берутся документы:

  * KNOWLEDGE_DIR задан — каждый `*.md` в папке считается одним документом,
    заголовок берётся из первой строки вида `# ...`, иначе из имени файла;
  * не задан — справочные разделы вынимаются из системного промпта
    (`prompts.policy_documents()`), и база знаний не расходится с ним по
    содержанию просто потому, что источник один.

Поиск намеренно без эмбеддингов и без зависимостей: словарь терминов, IDF и
нормированная сумма весов. Для десятка документов на одном языке это работает
не хуже, а объяснить результат можно построчно.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agent import config as cfg
from agent import prompts

# Заголовок блока со справкой. Он же — признак того, что справка в сообщение
# уже подставлена: нода контекста не должна дублировать её при повторном входе.
BLOCK_TITLE = "Справка из базы знаний"

_WORD = re.compile(r"[0-9a-zа-яё]{3,}")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)

# Длина псевдоосновы. Русский язык словоизменительный, а морфологического
# анализатора здесь нет и не будет: обрезка до пяти символов склеивает
# «тариф/тарифы/тарифом» и «оплата/оплаты», чего для отбора раздела достаточно.
_STEM = 5


@dataclass(frozen=True)
class Document:
    """Один документ базы знаний: заголовок и тело."""

    title: str
    text: str

    def as_markdown(self) -> str:
        return f"### {self.title}\n{self.text}"


# --------------------------------------------------------------------------
# Источник документов
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def builtin_documents() -> tuple[Document, ...]:
    """Справочные разделы системного промпта. Не меняются в течение процесса."""
    return tuple(Document(title, body) for title, body in prompts.policy_documents())


def documents() -> tuple[Document, ...]:
    """
    Текущая база знаний.

    Файлы читаются на каждом обращении, без кеша: правку документа в работающем
    `langgraph dev` разумно подхватывать сразу, а стоимость чтения нескольких
    килобайт рядом со стоимостью вызова модели неразличима.
    """
    directory = cfg.knowledge_dir()
    if not directory:
        return builtin_documents()

    root = Path(directory).expanduser()
    if not root.is_dir():
        raise cfg.ConfigError(f"KNOWLEDGE_DIR={directory!r}: папки не существует")

    found: list[Document] = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        heading = _HEADING.search(text)
        title = heading.group(1).strip() if heading else path.stem
        body = text[heading.end() :].strip() if heading else text
        found.append(Document(title, body))
    return tuple(found)


# --------------------------------------------------------------------------
# Поиск
# --------------------------------------------------------------------------
def terms(text: str) -> list[str]:
    """Слова текста, приведённые к псевдооснове."""
    return [word[:_STEM] for word in _WORD.findall(text.lower())]


def _idf(corpus: tuple[Document, ...]) -> dict[str, float]:
    """
    Вес термина: чем в большем числе документов он встречается, тем меньше.

    Без этого «тариф» и «клиент» перевешивали бы редкие слова, по которым
    документ и опознаётся.
    """
    total = len(corpus) or 1
    seen: dict[str, int] = {}
    for document in corpus:
        for term in set(terms(f"{document.title} {document.text}")):
            seen[term] = seen.get(term, 0) + 1
    return {term: math.log(1 + total / count) for term, count in seen.items()}


def score(query: str, document: Document, weights: dict[str, float]) -> float:
    """
    Доля веса запроса, покрытая документом: 0 — ничего общего, 1 — все слова.

    Нормировка нужна, чтобы порог KNOWLEDGE_MIN_SCORE значил одно и то же для
    короткого и длинного вопроса.
    """
    wanted = set(terms(query))
    if not wanted:
        return 0.0
    have = set(terms(f"{document.title} {document.text}"))
    total = sum(weights.get(term, 1.0) for term in wanted)
    hit = sum(weights.get(term, 1.0) for term in wanted & have)
    return hit / total if total else 0.0


def search(
    query: str, top_k: int | None = None, min_score: float | None = None
) -> list[Document]:
    """Документы под вопрос оператора, от самого подходящего к менее подходящему."""
    corpus = documents()
    if not corpus or not (query or "").strip():
        return []

    limit = cfg.knowledge_top_k() if top_k is None else top_k
    threshold = cfg.knowledge_min_score() if min_score is None else min_score
    weights = _idf(corpus)

    ranked = sorted(
        ((score(query, document, weights), document) for document in corpus),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return [document for value, document in ranked[: max(limit, 0)] if value >= threshold]


def as_block(found: list[Document]) -> str:
    """
    Найденное — в текст, который дописывается в конец вопроса оператора.

    Формат стабильный, но содержимое каждый раз своё: это и есть переменная
    часть промпта, и место ей строго после всего постоянного.
    """
    if not found:
        return ""
    body = "\n\n".join(document.as_markdown() for document in found)
    return f"\n\n---\n{BLOCK_TITLE} (подставлена автоматически):\n\n{body}"


def block_for(query: str) -> str:
    """Готовый блок справки под вопрос. Пустая строка — ничего не нашлось."""
    return as_block(search(query))

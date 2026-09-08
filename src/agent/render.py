"""
Разметка документа треда: storage format Confluence или Markdown.

Формат — свойство места назначения, а не графа. На wiki уезжает storage format
с макросами Atlassian, в файл на диске — Markdown, и обе разметки собираются из
одних и тех же кусков: заголовок, абзац, текст ответа модели, таблица расходов,
свёрнутый блок ранних ходов.

Поэтому здесь протокол из шести операций и две его реализации, а `graph.py`
собирает документ, ничего не зная о том, куда он поедет. Третий формат (HTML,
AsciiDoc, что угодно ещё) — это ещё один класс, а не правка сборки документа.

Маскирование чувствительных данных живёт отдельно (`confluence.mask_text`) и
применяется ДО разметки: маска должна видеть исходные символы, а не
экранированные сущности.
"""

from __future__ import annotations

import html
from typing import Protocol

from agent import confluence


class Renderer(Protocol):
    """Минимум, которого хватает, чтобы собрать документ треда."""

    name: str
    extension: str

    def heading(self, text: str) -> str:
        """Заголовок раздела внутри документа."""

    def paragraph(self, text: str) -> str:
        """Служебный абзац: шапка, пояснение. Разметки в тексте нет."""

    def body(self, text: str) -> str:
        """Текст человека или модели: списки и заголовки внутри учитываются."""

    def table(self, rows: list[tuple[str, object]]) -> str:
        """Таблица «метрика — значение»."""

    def bullets(self, items: list[str]) -> str:
        """Маркированный список."""

    def collapsed(self, title: str, body: str) -> str:
        """Свёрнутый блок: заголовок виден всегда, содержимое — по клику."""

    def join(self, parts: list[str]) -> str:
        """Склейка кусков в готовый документ."""


class StorageRenderer:
    """
    Storage format Confluence — историческая и основная разметка проекта.

    Вся конвертация уже была написана для публикации на wiki и осталась там же,
    в `confluence.py`: здесь только переходник к общему протоколу.
    """

    name = "storage"
    extension = ".html"

    def heading(self, text: str) -> str:
        return f"<h2>{html.escape(text, quote=False)}</h2>"

    def paragraph(self, text: str) -> str:
        return f"<p>{html.escape(text, quote=False)}</p>"

    def body(self, text: str) -> str:
        return confluence.text_to_storage(text)

    def table(self, rows: list[tuple[str, object]]) -> str:
        cells = "".join(
            f"<tr><td>{html.escape(str(name), quote=False)}</td>"
            f"<td>{html.escape(str(value), quote=False)}</td></tr>"
            for name, value in rows
        )
        return (
            "<table><thead><tr><th>Метрика</th><th>Значение</th></tr></thead>"
            f"<tbody>{cells}</tbody></table>"
        )

    def bullets(self, items: list[str]) -> str:
        body = "".join(f"<li>{html.escape(item, quote=False)}</li>" for item in items)
        return f"<ul>{body}</ul>"

    def collapsed(self, title: str, body: str) -> str:
        return confluence.expand_macro(title, body)

    def join(self, parts: list[str]) -> str:
        return "\n".join(parts)


def _cell(value: object) -> str:
    """Вертикальная черта делит колонки таблицы, внутри ячейки её экранируют."""
    return str(value).replace("|", r"\|")


class MarkdownRenderer:
    """
    Markdown для документа, который кладётся на диск.

    Текст модели уже написан почти в Markdown (нумерованные списки, дефисы),
    поэтому тело проходит насквозь: конвертировать нечего. Свёрнутый блок —
    `<details>`, он работает и на GitHub, и в большинстве просмотрщиков, а
    в обычном редакторе виден как текст.
    """

    name = "markdown"
    extension = ".md"

    def heading(self, text: str) -> str:
        return f"## {text}"

    def paragraph(self, text: str) -> str:
        return text

    def body(self, text: str) -> str:
        return (text or "").strip()

    def table(self, rows: list[tuple[str, object]]) -> str:
        lines = ["| Метрика | Значение |", "| --- | --- |"]
        lines += [f"| {_cell(name)} | {_cell(value)} |" for name, value in rows]
        return "\n".join(lines)

    def bullets(self, items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items)

    def collapsed(self, title: str, body: str) -> str:
        return f"<details>\n<summary>{title}</summary>\n\n{body}\n\n</details>"

    def join(self, parts: list[str]) -> str:
        return "\n\n".join(part for part in parts if part)


STORAGE = StorageRenderer()
MARKDOWN = MarkdownRenderer()

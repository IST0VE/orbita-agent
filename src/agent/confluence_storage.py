"""
Текст ответа модели -> storage format Confluence.

Отделено от `confluence.py` по ответственности: там транспорт, реквизиты и
операции над страницей, здесь — разметка. Разница видна по зависимостям:
этому файлу не нужны ни `requests`, ни настройки инстанса, и он не делает
ни одного запроса.

Конвертер сознательно неполный. Страница собирается из ответов модели, а не
из произвольного Markdown: заголовки, списки, таблицы, код и минимум
подчёркиваний. Всё остальное уезжает абзацем — и это лучше, чем частично
разобранная разметка, которая на wiki выглядит сломанной.
"""

from __future__ import annotations

import html
import re

from agent import outgoing

_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
#: Оставлено ради читателей и старых ссылок: сами шаблоны теперь в `outgoing.py`,
#: вместе с теми, которые маскировать нельзя, и с проверкой готового payload.
_BUILTIN_SECRET_PATTERNS = tuple(pattern for _, pattern in outgoing.MASKED)


def _inline(text: str) -> str:
    """Экранирование плюс минимум разметки: **жирный** и `код`."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def mask_text(text: str) -> str:
    """
    Замаскировать в тексте всё, что попадает под CONFLUENCE_MASK_PATTERNS.

    Применяется к тексту до конвертации в storage format — то есть маска видит
    исходные символы, а не экранированные сущности.

    Само правило живёт в `outgoing.py` и одно на всех, кто пишет наружу: пока
    оно лежало здесь, публикация файла и заведение задачи в Jira ходили мимо.
    Имя оставлено прежним — на него ссылается половина модулей и документация.
    """
    return outgoing.mask_text(text)


def expand_macro(title: str, body_html: str) -> str:
    """Свёрнутый блок: заголовок виден всегда, содержимое — по клику."""
    label = html.escape(title, quote=True)
    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{label}</ac:parameter>'
        f"<ac:rich-text-body>{body_html}</ac:rich-text-body>"
        "</ac:structured-macro>"
    )


def _code_block(lines: list[str]) -> str:
    text = "\n".join(lines).replace("]]>", "]]]]><![CDATA[>")
    return (
        '<ac:structured-macro ac:name="code"><ac:plain-text-body>'
        f"<![CDATA[{text}]]></ac:plain-text-body></ac:structured-macro>"
    )


def _table_cells(line: str) -> list[str]:
    """Markdown row; escaped pipes belong to the cell, not the table grid."""
    cells = re.split(r"(?<!\\)\|", line.strip())
    if not cells[0].strip():
        cells.pop(0)
    if cells and not cells[-1].strip():
        cells.pop()
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def text_to_storage(text: str) -> str:
    """Абзацы, заголовки, списки, Markdown-таблицы и блоки кода в XHTML."""
    out: list[str] = []
    list_tag: str | None = None
    fence: list[str] | None = None

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    def open_list(tag: str) -> None:
        nonlocal list_tag
        if list_tag != tag:
            close_list()
            out.append(f"<{tag}>")
            list_tag = tag

    lines = (text or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if _FENCE.match(line):
            if fence is None:
                close_list()
                fence = []
            else:
                out.append(_code_block(fence))
                fence = None
            continue
        if fence is not None:
            fence.append(line)
            continue

        if not line.strip():
            close_list()
            continue

        if "|" in line and index < len(lines):
            headers = _table_cells(line)
            separator = _table_cells(lines[index])
            if (headers and len(headers) == len(separator)
                    and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)):
                close_list()
                index += 1
                out.append("<table><tbody><tr>" + "".join(
                    f"<th>{_inline(cell)}</th>" for cell in headers
                ) + "</tr>")
                while index < len(lines) and "|" in lines[index] and not _FENCE.match(lines[index]):
                    cells = _table_cells(lines[index])
                    if len(cells) > len(headers):
                        break  # preserve malformed rows as text instead of dropping cells
                    cells += [""] * (len(headers) - len(cells))
                    out.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>")
                    index += 1
                out.append("</tbody></table>")
                continue

        heading = _HEADING.match(line)
        if heading:
            close_list()
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        ordered = _ORDERED.match(line)
        if ordered:
            open_list("ol")
            out.append(f"<li>{_inline(ordered.group(1))}</li>")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            open_list("ul")
            out.append(f"<li>{_inline(bullet.group(1))}</li>")
            continue

        close_list()
        out.append(f"<p>{_inline(line.strip())}</p>")

    if fence is not None:  # незакрытый ``` в ответе модели
        out.append(_code_block(fence))
    close_list()
    return "\n".join(out)

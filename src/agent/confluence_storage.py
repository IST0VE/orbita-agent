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


# Разбираем исходный Markdown, а не HTML после предыдущей замены: иначе
# **адрес** вставляет <strong> внутрь href, а <адрес> повреждает &gt;.
_INLINE = re.compile(
    r"`(?P<code>[^`\n]+)`"
    r"|\[(?P<label>[^\]\n]+)\]\((?P<href>https?://[^\s)<>\"'`]+)\)"
    r"|<(?P<auto>https?://[^\s<>\"'`]+)>"
    r"|\*\*(?P<strong>.+?)\*\*"
    r"|(?P<url>https?://[^\s<>\"'`]+)"
)
# Знаки, которыми предложение кончается сразу за адресом: частью адреса они
# почти никогда не бывают, а ссылка с точкой на конце ведёт на 404.
_URL_TAIL = ".,;:!?)]»”"


def _link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{label}</a>'


def _bare_link(url: str) -> str:
    tail = ""
    while url and url[-1] in _URL_TAIL:
        opening = {")": "(", "]": "["}.get(url[-1])
        if opening and url.count(url[-1]) <= url.count(opening):
            break  # парная скобка принадлежит адресу
        url, tail = url[:-1], url[-1] + tail
    return _link(url, html.escape(url, quote=False)) + html.escape(tail, quote=False)


def _inline(text: str, *, links: bool = True) -> str:
    """
    Экранирование плюс минимум разметки: **жирный**, `код` и ссылки.

    Ссылки — и `[текст](адрес)`, и голый адрес. Документы конвейеров ссылаются
    на страницы и задачи десятками, а storage format не делает ссылкой адрес
    в тексте сам: на странице они оставались строками, которые приходилось
    копировать руками. Ссылками становятся только http(s)-адреса.
    """
    out: list[str] = []
    end = 0
    for match in _INLINE.finditer(text):
        out.append(html.escape(text[end:match.start()], quote=False))
        if match.group("code") is not None:
            out.append("<code>" + html.escape(match.group("code"), quote=False) + "</code>")
        elif match.group("strong") is not None:
            out.append("<strong>" + _inline(match.group("strong"), links=links) + "</strong>")
        elif not links:
            # Текст ссылки может содержать URL, но вложенный <a> недопустим.
            out.append(html.escape(match.group(0), quote=False))
        elif match.group("href") is not None:
            out.append(_link(match.group("href"), _inline(match.group("label"), links=False)))
        elif match.group("auto") is not None:
            url = match.group("auto")
            out.append(_link(url, html.escape(url, quote=False)))
        else:
            out.append(_bare_link(match.group("url")))
        end = match.end()
    out.append(html.escape(text[end:], quote=False))
    return "".join(out)


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
            if (headers and separator and "|" in lines[index]
                    and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)):
                close_list()
                index += 1
                rows = []
                while index < len(lines) and "|" in lines[index] and not _FENCE.match(lines[index]):
                    rows.append(_table_cells(lines[index]))
                    index += 1
                # Ширина — по самой длинной строке. Модель дописывает строкам
                # колонку и забывает про шапку: 27 сентября 2026 таблица пробелов
                # вышла с шапкой в пять колонок и строками в шесть, и прежний
                # конвертер оставлял все строки текстом с «|». Лишняя ячейка не
                # теряется и не выталкивает строку из таблицы — шапка добивается
                # пустыми.
                width = max(len(headers), len(separator), *(len(cells) for cells in rows))
                headers += [""] * (width - len(headers))
                out.append("<table><tbody><tr>" + "".join(
                    f"<th>{_inline(cell)}</th>" for cell in headers
                ) + "</tr>")
                for cells in rows:
                    cells += [""] * (width - len(cells))
                    out.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>")
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

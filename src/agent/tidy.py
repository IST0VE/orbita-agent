"""
Форма документа этапа: таблицы и чужой алфавит.

Две поломки видны читателю сразу, а модель их не замечает. Обе нашлись
27 сентября 2026 в черновике конвейера подготовки задачи.

Первая — строки таблицы длиннее шапки. Модель дописала строкам таблицы
пробелов шестую ячейку с тегами источников, а шапку оставила в пять.
Конвертер storage format в таком случае выходит из таблицы и оставляет строки
текстом с «|», а remark-gfm в интерфейсе молча отбрасывает лишние ячейки —
вместе с данными. Чинит это код: шапка и разделитель добиваются пустыми
колонками, и ни одна ячейка не теряется.

Вторая — слова на другом языке посреди русского текста: «Сделать服务端-часть».
Причину такой генерации этот модуль не определяет. Словаря для обратного
перевода нет, поэтому переписывает модель, но только затронутые
строки: одним коротким запросом, а не новым документом этапа ценой в тысячи
токенов.
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

_FENCE = re.compile(r"^\s*```")
_SEPARATOR_CELL = re.compile(r":?-{3,}:?")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")

#: Иероглифы, кана, хангыль и полноширинные формы — в русском документе всегда
#: сбой. И слово, в котором кириллица стоит вплотную к латинице: «aутентификация»
#: с латинской «a», «стенограммеRequirements». Имя с дефисом или апострофом
#: (`nginx-пользователь`, `tab'а`) под это не попадает — там буквы разделены.
FOREIGN = re.compile(
    "[　-〿぀-ヿ㐀-䶿一-鿿가-힯＀-￯]"
    "|[A-Za-z][А-Яа-яЁё]|[А-Яа-яЁё][A-Za-z]"
)

# Теги источников, испорченные сэмплированием: «[ФАЙл имя]», «[Wiki 123]» и
# «[ФАЙЛ speech_to_text (9).doc.txt)» — скобка в имени файла сбивает модель, и
# тег закрывается круглой. Чинится кодом: форма тега известна заранее.
_TAG_CASE = re.compile(r"\[(файл|wiki|jira)(?=\s)", re.IGNORECASE)
# Id источника (`evidence.py`) с чужим тире, пробелом или в верхнем регистре:
# «[EV 3F9A2C]», «EV–3f9a2c». Критик ищет ровно `EV-3f9a2c` и счёл бы такую
# ссылку битой, хотя источник назван верно.
_EV_ID = re.compile(r"\bEV(?:\s*[-‐‑‒–—]\s*|\s+)([0-9A-Fa-f]{6})\b")
_FILE_TAG = re.compile(r"\[ФАЙЛ ")
_TAG_TAIL = re.compile(r"[^\[\]\n]*")
_EXT_PAREN = re.compile(r"\.[A-Za-z0-9]{1,6}\)")


def _cells(line: str) -> list[str]:
    """Ячейки строки таблицы как есть: экранированная `\\|` остаётся в ячейке."""
    cells = _CELL_SPLIT.split(line.strip())
    if cells and not cells[0].strip():
        cells.pop(0)
    if cells and not cells[-1].strip():
        cells.pop()
    return [cell.strip() for cell in cells]


def _is_separator(line: str) -> bool:
    if "|" not in line:
        return False
    cells = _cells(line)
    return bool(cells) and all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _pad(block: list[str]) -> list[str]:
    """Таблица с одинаковым числом ячеек во всех строках; ровная — как была."""
    rows = [_cells(line) for line in block]
    width = max(len(cells) for cells in rows)
    if all(len(cells) == width for cells in rows):
        return block
    header, separator, *body = rows
    return [
        _row(header + [""] * (width - len(header))),
        _row(separator + ["---"] * (width - len(separator))),
        *(_row(cells + [""] * (width - len(cells))) for cells in body),
    ]


def normalize_tables(text: str) -> str:
    """
    Выровнять Markdown-таблицы по самой длинной строке.

    Таблица — строка с `|`, за ней разделитель из дефисов, дальше строки с
    `|` до пустой строки. Код в ``` не трогается: там `|` бывает чем угодно.
    """
    lines = (text or "").split("\n")
    out: list[str] = []
    fence = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if _FENCE.match(line):
            fence = not fence
        elif not fence and "|" in line and index + 1 < len(lines) and _is_separator(lines[index + 1]):
            end = index + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            out.extend(_pad(lines[index:end]))
            index = end
            continue
        out.append(line)
        index += 1
    return "\n".join(out)


def _close_file_tags(text: str) -> str:
    """
    Незакрытый тег файла, закрытый круглой скобкой, — закрыть квадратной.

    Тег считается незакрытым, если до конца строки или до следующего `[` в
    нём нет `]`: «[ФАЙЛ протокол (v1.2).md]» закрыт и не трогается. Скобка,
    которую модель поставила вместо `]`, — первая после расширения файла, на
    которой скобки в имени сбалансированы: у «[ФАЙЛ запись (ч.1).txt)» это
    последняя, а не та, что закрывает «(ч.1». Раньше хватало первой
    попавшейся, и корректные теги с точкой в скобках ломались посередине имени.
    """
    out: list[str] = []
    position = 0
    for match in _FILE_TAG.finditer(text):
        if match.start() < position:
            continue
        start = match.end()
        tail = _TAG_TAIL.match(text, start)
        end = tail.end() if tail else start
        if end < len(text) and text[end] == "]":
            continue
        for ext in _EXT_PAREN.finditer(text, start, end):
            name = text[start : ext.end() - 1]
            if name.count("(") == name.count(")"):
                out.append(text[position : ext.end() - 1] + "]")
                position = ext.end()
                break
    out.append(text[position:])
    return "".join(out)


def fix_tags(text: str) -> str:
    """Теги источников в каноническую форму: `[ФАЙЛ …]`, `[WIKI …]`, `[JIRA …]`, `[EV-…]`."""
    text = _TAG_CASE.sub(lambda match: "[" + match.group(1).upper(), text or "")
    text = _EV_ID.sub(lambda match: "EV-" + match.group(1).lower(), text)
    return _close_file_tags(text)


# Код в строке: `имя`, `путь`, `команда`. Внутри — имя как в источнике, и чужой
# алфавит там не сбой генерации: подпись элемента схемы бывает латиницей
# вперемешку с кириллицей, а переписать её «по-русски» значит сломать поиск по
# имени (`diagram_prompts`: имена — как на схеме, без перевода).
_CODE_SPAN = re.compile(r"`[^`\n]*`")
# Цитата в «ёлочках» — слова источника, и язык у неё его: отчёт о китайском
# пакете цитирует китайский текст, и перевести цитату значит подменить её.
_QUOTE = re.compile(r"«[^«»\n]*»")


def _prose(line: str) -> str:
    """Строка без кода и цитат: чужой алфавит ищется только в собственном тексте."""
    return _QUOTE.sub("", _CODE_SPAN.sub("", line))


def foreign_lines(text: str) -> list[int]:
    """Номера строк с чужим алфавитом вне блоков кода."""
    found: list[int] = []
    fence = False
    for number, line in enumerate((text or "").split("\n")):
        if _FENCE.match(line):
            fence = not fence
        elif not fence and FOREIGN.search(_prose(line)):
            found.append(number)
    return found


REPAIR_PROMPT = """\
Ты правишь отдельные строки документа на русском языке. В них по ошибке попали
символы другого языка: китайские иероглифы, латинская буква внутри русского
слова («aутентификация» с латинской a) или английское слово, слипшееся с
русским («стенограммеRequirements»). Перепиши каждую строку по-русски: сохрани
смысл, Markdown-разметку, имена, ссылки и теги источников вроде [EV-3f9a2c],
[JIRA ORB-1] или [ВЫВОД]; испорченное замени правильными русскими словами. Остальной текст
строки не меняй. Текст в обратных кавычках (`nginx-user`) — имя как в источнике,
текст в «ёлочках» — цитата: их не трогай.

Ответь только JSON-массивом строк: столько же элементов, сколько прислано, и
в том же порядке. Без пояснений и без обёртки ```.
"""


def repair_request(lines: list[str]) -> list:
    """Запрос на починку строк. Префикс свой: кеш ролей он не трогает и не использует."""
    return [
        SystemMessage(content=REPAIR_PROMPT),
        HumanMessage(content=json.dumps(lines, ensure_ascii=False)),
    ]


def parse_repair(answer: str, expected: int) -> list[str] | None:
    """
    Исправленные строки или None, если ответу нельзя верить.

    Верить нельзя ответу не той длины, не массиву строк и строкам, в которых
    чужой алфавит остался: подставлять такое — значит заменить одну поломку
    другой, а исходная строка хотя бы честно показывает, что сломалось.
    """
    text = re.sub(r"^\s*<think>.*?</think>\s*", "", answer or "", count=1, flags=re.DOTALL)
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if (
        not isinstance(value, list)
        or len(value) != expected
        or not all(isinstance(item, str) for item in value)
        or any(FOREIGN.search(_prose(item)) or "\n" in item for item in value)
    ):
        return None
    return value


def apply_repair(text: str, numbers: list[int], lines: list[str]) -> str:
    """Подставить исправленные строки на их места."""
    out = text.split("\n")
    for number, line in zip(numbers, lines, strict=True):
        out[number] = line
    return "\n".join(out)

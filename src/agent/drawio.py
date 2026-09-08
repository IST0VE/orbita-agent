"""
Разбор .drawio: из картинки — проверяемые данные.

Схема в draw.io — это XML, в котором нарисованное и написанное лежат вперемешку:
координаты, стили, HTML в подписях, вложенные контейнеры, оборванные стрелки.
Отдать этот XML модели целиком нельзя по двум причинам сразу. Он дорогой —
геометрия и стили занимают больше половины файла и не значат ничего. И он
разваливает главное: по сырому XML модель не отличает стрелку, у которой есть
и начало и конец, от стрелки, нарисованной «примерно рядом» — а именно на этом
различии держится вся достоверность будущего документа.

Поэтому здесь ровно две вещи:

  parse()    XML → страницы, узлы и связи с разрешёнными текстами концов;
  compact()  проекция для промпта, в которой узлы и связи РАЗДЕЛЕНЫ на те,
             по которым можно утверждать факты, и те, по которым нельзя.

Второе важнее первого. Полная стрелка (`source` и `target` указывают на
существующие элементы) — это факт: «А отправляет в Б». Оборванная — это повод
для вопроса, и она уезжает модели в отдельном разделе, а не в таблице
интеграций. Разделение сделано здесь, кодом, а не просьбой в промпте: просьбу
модель выполняет с некоторой вероятностью, а разложенные по разным ключам
данные — всегда.

Третье, что даёт разбор, — идентификаторы. У каждого узла и каждой связи есть
`id` из файла, и роль обязана ссылаться на них в каждом утверждении
(`[id: ...]`). По этим ссылкам `unknown_ids()` проверяет документ против схемы
без всякой модели: сославшийся на несуществующий элемент — это выдуманный факт,
и его видно арифметикой.

Зависимостей нет, только stdlib: разбор XML — не то место, где нужен пакет.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import re
import urllib.parse
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

# Что считаем схемой. `.xml` тоже попадает сюда: draw.io сохраняет так же часто,
# как в `.drawio`, а внутри лежит тот же mxfile.
SUFFIXES = (".drawio", ".xml", ".drawio.xml")

# Потолок на распакованную страницу. Сжатый `<diagram>` разжимается в память
# до того, как его кто-нибудь посмотрит, и zip-бомба в папке задачи не должна
# укладывать сервер вместе со всеми чужими тредами.
MAX_INFLATED_BYTES = 64 * 1024 * 1024
# Сырой файл тоже читается до разбора. Сжатая страница ограничена отдельно
# выше, а 16 MiB достаточно даже для крупных несжатых схем.
MAX_SOURCE_BYTES = 16 * 1024 * 1024
_FORBIDDEN_XML = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

# Формат ссылки на элемент схемы. Ровно этот же формат требует префикс роли:
# разбирать свободный текст «как получилось» — значит проверять не документ,
# а свою же регулярку.
CITATION = re.compile(r"\[id:\s*([^\]\n]{1,400})\]")
_SPLIT_IDS = re.compile(r"[,;\s]+")


class DiagramError(ValueError):
    """Файл не открылся, не распаковался или не похож на схему draw.io."""


# --------------------------------------------------------------------------
# Чтение файла
#
# Четыре формата хранения — исторические слои самого draw.io. Порядок попыток
# от самого частого к самому редкому; ошибка каждой попытки нормальна и молча
# ведёт к следующей, а не наверх.
# --------------------------------------------------------------------------
def _looks_like_diagram(text: str) -> bool:
    return "<mxfile" in text or "<mxGraphModel" in text


def _read_source(path: Path) -> bytes:
    size = path.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise DiagramError(f"{path.name}: файл больше {MAX_SOURCE_BYTES} байт — не читается")
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:  # файл мог вырасти между stat() и read_bytes()
        raise DiagramError(f"{path.name}: файл больше {MAX_SOURCE_BYTES} байт — не читается")
    return raw


def _bounded_decompress(data: bytes) -> bytes:
    """Raw DEFLATE без неограниченного промежуточного выделения памяти."""
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    inflated = decompressor.decompress(data, MAX_INFLATED_BYTES + 1)
    if len(inflated) > MAX_INFLATED_BYTES or decompressor.unconsumed_tail:
        raise DiagramError(
            f"распакованные данные больше {MAX_INFLATED_BYTES} байт — файл не читается"
        )
    inflated += decompressor.flush(MAX_INFLATED_BYTES - len(inflated) + 1)
    if len(inflated) > MAX_INFLATED_BYTES or not decompressor.eof:
        raise DiagramError(f"распакованные данные больше {MAX_INFLATED_BYTES} байт или оборваны")
    return inflated


def read_xml(path: Path, raw: bytes | None = None) -> str:
    """XML схемы: обычный, base64, base64+deflate или голый deflate."""
    raw = _read_source(path) if raw is None else raw

    try:
        text = raw.decode("utf-8")
        if _looks_like_diagram(text):
            return text
    except UnicodeDecodeError:
        pass

    for decode in (
        lambda data: base64.b64decode(data).decode("utf-8"),
        lambda data: _bounded_decompress(base64.b64decode(data)).decode("utf-8"),
        lambda data: _bounded_decompress(data).decode("utf-8"),
    ):
        try:
            text = decode(raw)
        except (UnicodeDecodeError, ValueError, binascii.Error, zlib.error, DiagramError):
            continue
        if _looks_like_diagram(text):
            return text

    raise DiagramError(f"{path.name}: не разбирается как файл draw.io")


def _inflate(encoded: str) -> str:
    """Содержимое сжатого `<diagram>`: base64 → deflate → percent-encoding."""
    encoded = encoded.strip()
    if not encoded:
        raise DiagramError("страница схемы пуста")

    unescaped = html.unescape(encoded)
    if "<mxGraphModel" in unescaped:
        return unescaped

    try:
        inflated = _bounded_decompress(base64.b64decode(encoded, validate=True))
    except (ValueError, binascii.Error, zlib.error, DiagramError) as exc:
        raise DiagramError(f"страница схемы не распаковывается: {exc}") from exc
    return urllib.parse.unquote(inflated.decode("utf-8", errors="replace"))


# --------------------------------------------------------------------------
# Подписи
#
# В подписи draw.io лежит HTML: переносы, жирный шрифт, цвета, иногда таблица
# целиком. Модели нужен текст, а не разметка, но текст без потерь: имена полей,
# пути и команды внутри подписи — это и есть содержание схемы.
# --------------------------------------------------------------------------
_STYLE_BLOCK = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_STYLE_ATTR = re.compile(r"\s+style=\"[^\"]*\"|\s+style='[^']*'", re.IGNORECASE)
_BREAKS = re.compile(r"<\s*/?\s*(?:br|div|p|li|ul|ol|tr|td|th|h[1-6])[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t ]+")


def clean_text(raw: str) -> str:
    """Подпись элемента как текст: без тегов, стилей и лишних пробелов."""
    if not raw:
        return ""
    text = _STYLE_BLOCK.sub(" ", raw)
    text = _STYLE_ATTR.sub("", text)
    text = _BREAKS.sub(" ", text)
    text = _TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    return _SPACES.sub(" ", text).strip()


def _style_keys(style: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for part in (style or "").split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition("=")
        keys[name.strip()] = value.strip()
    return keys


_SHAPES = {
    "ellipse": "ellipse",
    "rhombus": "rhombus",
    "cylinder": "cylinder",
    "swimlane": "swimlane",
    "hexagon": "hexagon",
    "cloud": "cloud",
    "actor": "actor",
    "note": "note",
}


def _shape(keys: dict[str, str]) -> str:
    """
    Форма элемента словом. Не украшение: цилиндр — это база, ромб — ветвление,
    дорожка — зона ответственности, и модель обязана видеть разницу.
    """
    named = keys.get("shape", "")
    if named in _SHAPES:
        return _SHAPES[named]
    for key, name in _SHAPES.items():
        if key in keys:
            return name
    if keys.get("container") == "1":
        return "container"
    return "rectangle"


# --------------------------------------------------------------------------
# Разбор
# --------------------------------------------------------------------------
def _cell(element: ET.Element) -> dict:
    attrib = element.attrib
    if attrib.get("edge") == "1":
        kind = "edge"
    elif attrib.get("vertex") == "1":
        kind = "vertex"
    else:
        kind = ""
    return {
        "id": attrib.get("id", ""),
        "kind": kind,
        "text": clean_text(attrib.get("value", "")),
        "shape": _shape(_style_keys(attrib.get("style", ""))),
        "parent": attrib.get("parent", ""),
        "source": attrib.get("source", ""),
        "target": attrib.get("target", ""),
    }


def _cells(model: ET.Element) -> list[dict]:
    """
    Все элементы страницы одним списком.

    `<object>` — это обёртка draw.io вокруг ячейки: идентификатор и подпись
    лежат на обёртке, геометрия и связи — на вложенной `mxCell`. Их надо
    сшивать, иначе половина именованных элементов схемы теряет имя, а вторая
    половина — идентификатор, по которому на неё потом ссылаются.
    """
    objects = model.findall(".//object")
    wrapped = {id(inner) for obj in objects for inner in obj.findall("./mxCell")}

    cells = [_cell(c) for c in model.findall(".//mxCell") if id(c) not in wrapped]

    for obj in objects:
        label = clean_text(obj.attrib.get("label", ""))
        for inner in obj.findall("./mxCell"):
            record = _cell(inner)
            record["id"] = record["id"] or obj.attrib.get("id", "")
            if label:
                record["text"] = label
            cells.append(record)

    return [cell for cell in cells if cell["id"] and cell["kind"]]


def _page(name: str, model: ET.Element) -> dict:
    cells = _cells(model)
    index = {cell["id"]: cell for cell in cells}

    incoming: dict[str, int] = {}
    outgoing: dict[str, int] = {}
    for cell in cells:
        if cell["kind"] != "edge":
            continue
        if cell["source"]:
            outgoing[cell["source"]] = outgoing.get(cell["source"], 0) + 1
        if cell["target"]:
            incoming[cell["target"]] = incoming.get(cell["target"], 0) + 1

    nodes = []
    edges = []
    for cell in cells:
        if cell["kind"] == "edge":
            source = index.get(cell["source"])
            target = index.get(cell["target"])
            edges.append(
                {
                    "id": cell["id"],
                    "source_id": cell["source"],
                    "source_text": source["text"] if source else "",
                    "target_id": cell["target"],
                    "target_text": target["text"] if target else "",
                    "label": cell["text"],
                    # Полная связь — обе стороны указаны И обе существуют.
                    # Стрелка, нарисованная в пустоту, полной не считается,
                    # даже если на картинке она смотрит ровно на прямоугольник.
                    "is_complete": source is not None and target is not None,
                }
            )
            continue

        connections = (incoming.get(cell["id"], 0), outgoing.get(cell["id"], 0))
        if not cell["text"] and not any(connections):
            # Безымянный элемент без единой связи: слой, фон, рамка. Сказать
            # о нём нечего, а место в промпте он занимает.
            continue
        parent = index.get(cell["parent"])
        nodes.append(
            {
                "id": cell["id"],
                "text": cell["text"],
                "parent_text": parent["text"] if parent else "",
                "shape": cell["shape"],
                "incoming_count": connections[0],
                "outgoing_count": connections[1],
            }
        )

    return {"name": name, "nodes": nodes, "edges": edges}


def _parse_xml(xml_text: str) -> ET.Element:
    """XML без DTD/entities: схемам draw.io они не нужны, а parser bomb — нужен атакующему."""
    if _FORBIDDEN_XML.search(xml_text):
        raise DiagramError("XML с DOCTYPE/ENTITY запрещён")
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise DiagramError(f"XML схемы не разбирается: {exc}") from exc


def _models(xml_text: str) -> list[tuple[str, ET.Element]]:
    root = _parse_xml(xml_text)

    diagrams = root.findall(".//diagram") or [root]
    found: list[tuple[str, ET.Element]] = []

    for number, diagram in enumerate(diagrams, start=1):
        name = diagram.attrib.get("name") or f"Страница {number}"
        model = diagram.find("./mxGraphModel")
        if model is None and diagram.tag == "mxGraphModel":
            model = diagram
        if model is None and (diagram.text or "").strip():
            try:
                model = _parse_xml(_inflate(diagram.text or ""))
            except DiagramError as exc:
                raise DiagramError(f"страница «{name}» не разбирается: {exc}") from exc
        if model is None:
            model = diagram.find(".//mxGraphModel")
        if model is None or model.tag != "mxGraphModel":
            raise DiagramError(f"на странице «{name}» нет mxGraphModel")
        found.append((name, model))

    return found


def parse(path: Path, name: str = "") -> dict:
    """
    Схема как данные: страницы, узлы и связи.

    `name` — как файл называть в документе. По умолчанию имя на диске; граф
    подставляет путь относительно папки задачи, потому что документ читают
    люди, у которых этой папки нет.
    """
    raw = _read_source(path)
    pages = [_page(page_name, model) for page_name, model in _models(read_xml(path, raw))]
    return {
        "source": {
            "name": name or path.name,
            # Хеш файла — это версия схемы. Документ, собранный по вчерашней
            # версии, отличается от сегодняшнего одним этим полем, и по нему
            # видно, что перечитывать, а что нет.
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "pages": len(pages),
        },
        "pages": pages,
    }


# --------------------------------------------------------------------------
# Проекция для промпта
# --------------------------------------------------------------------------
def compact(data: dict) -> dict:
    """
    Те же данные, разложенные по назначению.

    Ключи длинные и говорящие намеренно: они читаются моделью как инструкция
    и стоят четырёх токенов на страницу. Связи, по которым можно утверждать
    факты, и связи, по которым нельзя, обязаны лежать в РАЗНЫХ списках —
    иначе разделение держится на внимательности модели, а не на данных.
    """
    pages = []
    for page in data.get("pages", []):
        nodes = page.get("nodes", [])
        edges = page.get("edges", [])
        pages.append(
            {
                "name": page.get("name", ""),
                "connected_nodes": [
                    node for node in nodes if node["incoming_count"] or node["outgoing_count"]
                ],
                "complete_edges_are_facts": [
                    {key: value for key, value in edge.items() if key != "is_complete"}
                    for edge in edges
                    if edge["is_complete"]
                ],
                "unconnected_nodes_and_notes": [
                    node
                    for node in nodes
                    if not node["incoming_count"] and not node["outgoing_count"]
                ],
                "incomplete_edges_are_questions_only": [
                    {key: value for key, value in edge.items() if key != "is_complete"}
                    for edge in edges
                    if not edge["is_complete"]
                ],
            }
        )
    return {"source": data.get("source", {}), "pages": pages}


def as_json(data: dict, max_chars: int = 0) -> str:
    """
    Проекция в JSON для сообщения роли.

    Обрезка — по символам и с честной отметкой в конце. Схема на тысячу
    элементов не влезет ни в какой бюджет, и молча укоротить её нельзя: модель,
    не знающая, что данные кончились раньше, напишет уверенный документ по
    половине схемы.
    """
    text = json.dumps(compact(data), ensure_ascii=False, indent=1)
    if max_chars and len(text) > max_chars:
        return (
            text[:max_chars]
            + f"\n\n[…данные схемы обрезаны на {max_chars} символах из {len(text)}. "
            "Опиши только то, что видишь выше, и укажи в документе, что схема "
            "прочитана не полностью.]"
        )
    return text


# --------------------------------------------------------------------------
# Проверка достоверности
#
# Здесь проект перестаёт верить модели на слово. Каждое утверждение в документе
# помечено ссылками на элементы схемы; ссылки сверяются с разобранным файлом
# арифметикой, без второго вызова LLM. Ссылка на несуществующий элемент — это
# выдуманный факт, и стоит такая проверка ноль.
# --------------------------------------------------------------------------
def ids(data: dict) -> set[str]:
    """Все идентификаторы схемы: узлы и связи."""
    found: set[str] = set()
    for page in data.get("pages", []):
        found.update(node["id"] for node in page.get("nodes", []))
        found.update(edge["id"] for edge in page.get("edges", []))
    return found


def cited_ids(text: str) -> list[str]:
    """Идентификаторы, на которые ссылается документ, в порядке появления."""
    seen: list[str] = []
    for block in CITATION.findall(text or ""):
        for token in _SPLIT_IDS.split(block):
            token = token.strip("`\"'.,").strip()
            if token and token not in seen:
                seen.append(token)
    return seen


def unknown_ids(text: str, data: dict) -> list[str]:
    """Ссылки документа, которых нет на схеме. Пусто — все ссылки настоящие."""
    known = ids(data)
    return [cited for cited in cited_ids(text) if cited not in known]


# --------------------------------------------------------------------------
# Файл в папке задачи
# --------------------------------------------------------------------------
def is_diagram(path: Path) -> bool:
    return path.name.lower().endswith(SUFFIXES)


def find(folder: Path) -> list[Path]:
    """Схемы в папке задачи, вместе с вложенными. Отсортированы по пути."""
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.rglob("*") if p.is_file() and is_diagram(p)), key=str)


def stats(data: dict) -> dict:
    """Сводка по схеме для оператора и для состояния треда."""
    nodes = sum(len(page.get("nodes", [])) for page in data.get("pages", []))
    edges = [edge for page in data.get("pages", []) for edge in page.get("edges", [])]
    complete = sum(1 for edge in edges if edge["is_complete"])
    return {
        "name": data.get("source", {}).get("name", ""),
        "sha256": data.get("source", {}).get("sha256", ""),
        "pages": len(data.get("pages", [])),
        "nodes": nodes,
        "edges": len(edges),
        "complete_edges": complete,
        "incomplete_edges": len(edges) - complete,
    }

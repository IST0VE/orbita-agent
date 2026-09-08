"""
Разбор .drawio: что именно считается фактом, а что — вопросом.

Тесты здесь не про XML. XML разбирает стандартная библиотека, и проверять её
незачем. Проверяется граница, ради которой весь модуль и написан: стрелка
с двумя настоящими концами попадает в один список, стрелка в пустоту — в другой,
и никакая подпись, форма или соседство на холсте эту границу не двигают.

Вторая проверяемая вещь — идентификаторы. По ним документ сверяется со схемой
арифметикой, без модели, поэтому ссылка на несуществующий элемент обязана
находиться всегда, а не «обычно».
"""

from __future__ import annotations

import base64
import urllib.parse
import zlib

import pytest

from agent import drawio

MODEL = """\
<mxGraphModel dx="800" dy="600">
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane" value="Асинхронный контур" style="swimlane;" vertex="1" parent="1" />
    <mxCell id="worker" value="&lt;b&gt;Export&lt;/b&gt;&lt;br&gt;Worker" style="rounded=1;" vertex="1" parent="lane" />
    <mxCell id="db" value="PostgreSQL" style="shape=cylinder;" vertex="1" parent="1" />
    <mxCell id="note" value="TODO: уточнить срок хранения" style="shape=note;" vertex="1" parent="1" />
    <mxCell id="frame" value="" style="" vertex="1" parent="1" />
    <mxCell id="e-write" value="INSERT export_jobs" style="" edge="1" parent="1" source="worker" target="db" />
    <mxCell id="e-lost" value="метрики?" style="dashed=1;" edge="1" parent="1" source="worker" />
    <object id="notify" label="Notifier" type="service">
      <mxCell style="rounded=1;" vertex="1" parent="1" />
    </object>
    <mxCell id="e-notify" value="webhook" style="" edge="1" parent="1" source="db" target="notify" />
  </root>
</mxGraphModel>"""

PLAIN = f'<mxfile host="test"><diagram id="d1" name="Поток">{MODEL}</diagram></mxfile>'


def packed(model: str) -> str:
    """Страница в том виде, в каком её пишет сам draw.io: percent + deflate + base64."""
    quoted = urllib.parse.quote(model, safe="~()*!.'")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    raw = compressor.compress(quoted.encode("utf-8")) + compressor.flush()
    return base64.b64encode(raw).decode("ascii")


COMPRESSED = f'<mxfile host="test"><diagram id="d1" name="Поток">{packed(MODEL)}</diagram></mxfile>'


@pytest.fixture
def diagram(tmp_path):
    path = tmp_path / "поток.drawio"
    path.write_text(PLAIN, encoding="utf-8")
    return drawio.parse(path)


# --------------------------------------------------------------------------
# Чтение файла
# --------------------------------------------------------------------------
def test_compressed_page_reads_the_same_as_plain(tmp_path, diagram):
    """
    Один и тот же файл, сохранённый двумя способами, обязан дать один результат.
    Иначе достоверность документа зависит от галочки в настройках редактора.
    """
    path = tmp_path / "сжатая.drawio"
    path.write_text(COMPRESSED, encoding="utf-8")

    assert drawio.parse(path)["pages"] == diagram["pages"]


def test_file_that_is_not_a_diagram_is_refused_loudly(tmp_path):
    path = tmp_path / "не-схема.drawio"
    path.write_bytes(bytes([0, 1, 2]) + b"binary junk, not a diagram")

    with pytest.raises(drawio.DiagramError, match="не разбирается"):
        drawio.parse(path)


def test_source_file_size_is_limited_before_read(monkeypatch, tmp_path):
    monkeypatch.setattr(drawio, "MAX_SOURCE_BYTES", 32)
    path = tmp_path / "слишком-большая.drawio"
    path.write_text(PLAIN, encoding="utf-8")

    with pytest.raises(drawio.DiagramError, match="файл больше"):
        drawio.parse(path)


def test_deflate_bomb_is_stopped_while_decompressing(monkeypatch):
    monkeypatch.setattr(drawio, "MAX_INFLATED_BYTES", 64)
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    compressed = compressor.compress(b"x" * 1000) + compressor.flush()
    encoded = base64.b64encode(compressed).decode("ascii")

    with pytest.raises(drawio.DiagramError, match="распаковы"):
        drawio._inflate(encoded)


def test_xml_entities_are_rejected(tmp_path):
    path = tmp_path / "entity.drawio"
    path.write_text(
        '<!DOCTYPE mxGraphModel [<!ENTITY x "payload">]>'
        '<mxGraphModel><root><mxCell id="0" value="&x;" /></root></mxGraphModel>',
        encoding="utf-8",
    )

    with pytest.raises(drawio.DiagramError, match="DOCTYPE/ENTITY"):
        drawio.parse(path)


def test_hash_is_the_version_of_the_schema(tmp_path, diagram):
    """Хеш файла — единственное, по чему видно, что схему перерисовали."""
    other = tmp_path / "правленая.drawio"
    other.write_text(PLAIN.replace("PostgreSQL", "ClickHouse"), encoding="utf-8")

    assert drawio.parse(other)["source"]["sha256"] != diagram["source"]["sha256"]


# --------------------------------------------------------------------------
# Элементы
# --------------------------------------------------------------------------
def test_html_in_a_label_becomes_plain_text(diagram):
    worker = next(n for n in diagram["pages"][0]["nodes"] if n["id"] == "worker")

    assert worker["text"] == "Export Worker"


def test_container_is_remembered_as_the_place_of_the_element(diagram):
    """
    Дорожка — это зона ответственности, и элемент внутри неё значит не то же,
    что такой же элемент снаружи. Подпись контейнера едет вместе с элементом.
    """
    worker = next(n for n in diagram["pages"][0]["nodes"] if n["id"] == "worker")

    assert worker["parent_text"] == "Асинхронный контур"


def test_shape_is_kept_because_a_cylinder_is_a_database(diagram):
    shapes = {n["id"]: n["shape"] for n in diagram["pages"][0]["nodes"]}

    assert shapes["db"] == "cylinder"
    assert shapes["note"] == "note"
    assert shapes["lane"] == "swimlane"


def test_object_wrapper_keeps_its_label_and_its_id(diagram):
    """
    `<object>` — обёртка draw.io: id и подпись на ней, связи на вложенной
    ячейке. Не сшить их — потерять имя у половины именованных элементов схемы.
    """
    notify = next(n for n in diagram["pages"][0]["nodes"] if n["id"] == "notify")

    assert notify["text"] == "Notifier"
    assert notify["incoming_count"] == 1


def test_unnamed_element_without_links_is_dropped(diagram):
    """Рамка без подписи и без связей: сказать о ней нечего, а токены она ест."""
    assert "frame" not in {n["id"] for n in diagram["pages"][0]["nodes"]}


# --------------------------------------------------------------------------
# Граница факта
# --------------------------------------------------------------------------
def test_complete_edge_carries_the_texts_of_both_ends(diagram):
    edge = next(e for e in diagram["pages"][0]["edges"] if e["id"] == "e-write")

    assert edge["is_complete"]
    assert edge["source_text"] == "Export Worker"
    assert edge["target_text"] == "PostgreSQL"
    assert edge["label"] == "INSERT export_jobs"


def test_dangling_edge_is_not_a_fact(diagram):
    """
    Стрелка с одним концом нарисована и что-то значила для автора. Но кто на
    другом конце — неизвестно, а значит утверждать по ней нельзя ничего.
    """
    edge = next(e for e in diagram["pages"][0]["edges"] if e["id"] == "e-lost")

    assert not edge["is_complete"]


def test_compact_splits_facts_from_questions(diagram):
    page = drawio.compact(diagram)["pages"][0]

    assert [e["id"] for e in page["complete_edges_are_facts"]] == ["e-write", "e-notify"]
    assert [e["id"] for e in page["incomplete_edges_are_questions_only"]] == ["e-lost"]
    assert "note" in {n["id"] for n in page["unconnected_nodes_and_notes"]}
    assert "worker" in {n["id"] for n in page["connected_nodes"]}


def test_projection_does_not_leak_the_completeness_flag(diagram):
    """
    В проекции признак полноты не нужен: списки уже разные. Оставить его —
    значит дать модели повод «пересмотреть» разделение, сделанное кодом.
    """
    page = drawio.compact(diagram)["pages"][0]

    assert all("is_complete" not in e for e in page["complete_edges_are_facts"])


def test_cut_data_says_that_it_was_cut(diagram):
    text = drawio.as_json(diagram, max_chars=200)

    assert len(text) < len(drawio.as_json(diagram))
    assert "обрезаны" in text


# --------------------------------------------------------------------------
# Сверка ссылок
# --------------------------------------------------------------------------
def test_cited_ids_reads_the_documented_format():
    text = "Worker пишет в базу [id: worker, e-write]. Notifier зовут по webhook [id: e-notify]."

    assert drawio.cited_ids(text) == ["worker", "e-write", "e-notify"]


def test_invented_reference_is_found(diagram):
    """
    Ровно то, ради чего в схему заглядывают по id: утверждение выглядит
    убедительно, ссылка оформлена правильно, а такого элемента на схеме нет.
    """
    text = "Worker шлёт события в Kafka [id: kafka-topic]."

    assert drawio.unknown_ids(text, diagram) == ["kafka-topic"]


def test_real_references_pass(diagram):
    text = "Worker пишет в PostgreSQL [id: e-write]."

    assert drawio.unknown_ids(text, diagram) == []


# --------------------------------------------------------------------------
# Поиск файла
# --------------------------------------------------------------------------
def test_find_takes_diagrams_and_ignores_the_rest(tmp_path):
    (tmp_path / "вложенная").mkdir()
    (tmp_path / "схема.drawio").write_text(PLAIN, encoding="utf-8")
    (tmp_path / "вложенная" / "вторая.drawio").write_text(PLAIN, encoding="utf-8")
    (tmp_path / "записи.md").write_text("не схема", encoding="utf-8")

    found = [p.name for p in drawio.find(tmp_path)]

    assert found == ["вторая.drawio", "схема.drawio"]


def test_find_on_a_missing_folder_is_empty_not_an_error(tmp_path):
    assert drawio.find(tmp_path / "нет-такой") == []

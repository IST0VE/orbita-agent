"""
Инструменты конвейера: файлы задачи.

Единственные инструменты, которые остались у графа после переезда с домена
поддержки на проектную аналитику. Читает их одна роль — аналитик, — и именно
через них в конвейер попадает всё, ради чего он существует: требования
заказчика, записи встреч, выгрузки.

Две заботы, кроме собственно чтения.

Первая — граница. Инструмент принимает имя файла от модели, то есть в конечном
счёте из текста, который писал человек снаружи. Без проверки это чтение любого
файла на машине по строке из чата, поэтому выход за корень проверяется отдельно
и на нормализованном пути, а не на исходной строке.

Вторая — кеш. JSON-схемы инструментов входят в кешируемый префикс наравне с
системным промптом: изменилось описание — сдвинулся префикс — обнулился кеш на
всём треде, причём сразу у всех пяти ролей. Поэтому имена, описания и аргументы
зафиксированы тестом, а не только договорённостью.
"""

from __future__ import annotations

import pytest

from agent import config as cfg
from agent import inputs, publishers
from agent.graph import list_task_files, read_task_file


def call(tool, config: dict | None = None, **kwargs) -> str:
    """Инструмент с папкой задачи в `configurable`, как его зовёт граф."""
    return tool.invoke(kwargs, config=config or {})


def with_task(name: str) -> dict:
    return {"configurable": {"input_dir": name}}


@pytest.fixture
def task(tmp_path):
    """Папка задачи с материалами, как её собрал бы оператор."""
    folder = inputs.ensure_root() / "export-feature"
    folder.mkdir(parents=True)
    (folder / "встреча.md").write_text(
        "# Встреча 12.03\n\nДоговорились: выгрузка асинхронная.",
        encoding="utf-8",
    )
    (folder / "limits.csv").write_text("plan,rows\nstart,1000\n", encoding="utf-8")
    (folder / "схема.png").write_bytes(b"\x89PNG\r\n\x1a\n binary")
    return folder


# --------------------------------------------------------------------------
# Список файлов
# --------------------------------------------------------------------------
def test_missing_folder_is_a_plain_answer_not_an_error():
    """
    Инструменты объявлены всегда, даже когда оператор не выбрал папку: набор
    инструментов входит в префикс и не может зависеть от выбора в интерфейсе.
    Значит, ответ на «папки нет» обязан быть текстом, а не исключением.
    """
    assert "папка не выбрана" in call(list_task_files)
    assert "папка не выбрана" in call(read_task_file, name="встреча.md")


def test_listing_names_every_file_with_its_size(task):
    answer = call(list_task_files, config=with_task("export-feature"))

    assert "встреча.md" in answer
    assert "limits.csv" in answer
    assert "схема.png" in answer
    assert "байт" in answer


def test_empty_folder_says_so(tmp_path):
    (inputs.ensure_root() / "пусто").mkdir(parents=True)

    assert "нет файлов" in call(list_task_files, config=with_task("пусто"))


# --------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------
def test_text_file_comes_back_whole(task):
    answer = call(read_task_file, name="встреча.md", config=with_task("export-feature"))

    assert "выгрузка асинхронная" in answer


def test_missing_file_is_a_plain_answer(task):
    answer = call(read_task_file, name="нет.md", config=with_task("export-feature"))

    assert "файл не прочитан" in answer


def test_binary_file_is_refused_before_it_reaches_the_prompt(task):
    """Бинарник в промпте — это деньги за мусор, и модель по нему ничего не скажет."""
    answer = call(read_task_file, name="схема.png", config=with_task("export-feature"))

    assert "не текстовый файл" in answer


def test_long_file_is_truncated_and_says_so(monkeypatch: pytest.MonkeyPatch, task):
    """
    Обрезка честно помечается: модель, которая не знает, что файл кончился
    раньше времени, сделает вывод по половине данных и не скажет об этом.
    """
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", "40")
    (task / "длинный.txt").write_text("а" * 500, encoding="utf-8")

    answer = call(read_task_file, name="длинный.txt", config=with_task("export-feature"))

    assert "файл обрезан на 40 символах из 500" in answer
    assert cfg.input_max_chars() == 40


# --------------------------------------------------------------------------
# Граница папки задачи
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "../секрет.md",
        "../../секрет.md",
        "подпапка/../../секрет.md",
    ],
)
def test_path_cannot_leave_the_task_folder(task, name: str):
    """
    Проверка стоит после раскрытия `..`, а не на исходной строке: сравнивать
    строки до нормализации бессмысленно.
    """
    (inputs.root().parent / "секрет.md").write_text("ключи", encoding="utf-8")

    answer = call(read_task_file, name=name, config=with_task("export-feature"))

    assert "ключи" not in answer
    assert "файл не прочитан" in answer


def test_sibling_task_folder_is_out_of_reach(task):
    """Соседняя задача — тоже чужие данные, хотя и лежит внутри корня."""
    other = inputs.ensure_root() / "другая-задача"
    other.mkdir(parents=True)
    (other / "чужое.md").write_text("не для этого треда", encoding="utf-8")

    answer = call(
        read_task_file, name="../другая-задача/чужое.md", config=with_task("export-feature")
    )

    assert "не для этого треда" not in answer


def test_context_block_cannot_list_files_outside_input_root(tmp_path):
    """configurable.input_dir тоже недоверен: даже список имён не должен утекать."""
    secret = inputs.root().parent / "секретная-папка"
    secret.mkdir()
    (secret / "credentials.env").write_text("TOKEN=secret", encoding="utf-8")

    assert inputs.block_for("../секретная-папка") == ""


# --------------------------------------------------------------------------
# Выбранный источник
#
# Папка задачи — не всегда однородный материал: в ней лежит и аналитика, и
# протокол встречи, и выгрузка. Сказать «работай вот по этому документу» можно
# было только словами в запросе; теперь выбор делается в интерфейсе и приезжает
# в `configurable.input_file`.
#
# Здесь выбор — подсказка, а не фильтр: файлы читает роль инструментом, и
# запретить ей соседний протокол значило бы получить требования без него и без
# единого слова об этом. Строже он трактуется там, где документ и есть весь
# вход, — см. `test_jira_graph.py`.
# --------------------------------------------------------------------------
def test_chosen_file_is_marked_in_the_list_and_others_stay(task):
    block = inputs.block_for("export-feature", "встреча.md")

    assert "встреча.md" in block and "ВЫБРАН ОПЕРАТОРОМ" in block
    # Остальные файлы никуда не делись: выбор не сокращает список.
    assert "limits.csv" in block
    assert "Начни с него" in block


def test_several_chosen_files_are_all_marked(task):
    """Комплект документации это несколько файлов, и помечены они все."""
    block = inputs.block_for("export-feature", ["встреча.md", "limits.csv"])

    assert block.count("ВЫБРАН ОПЕРАТОРОМ") == 2
    assert "Начни с них" in block


def test_a_lost_file_of_a_set_is_named_and_the_rest_keep_their_marks(task):
    """
    Пропасть может один файл из комплекта. Сказать надо про него, а не про
    выбор целиком: остальные на месте, и работать по ним роль обязана.
    """
    block = inputs.block_for("export-feature", ["встреча.md", "удалённый.md"])

    assert block.count("ВЫБРАН ОПЕРАТОРОМ") == 1
    assert "удалённый.md" in block and "ВНИМАНИЕ" in block


def test_no_choice_leaves_the_list_exactly_as_it_was(task):
    assert "ВЫБРАН" not in inputs.block_for("export-feature")
    assert "ВЫБРАН" not in inputs.block_for("export-feature", "")


def test_choice_that_is_no_longer_in_the_folder_is_said_out_loud(task):
    """
    Папку сменили, файл удалили, имя набрали руками. Промолчать нельзя: роль
    решит, что выбора не было, а оператор будет уверен, что он был.
    """
    block = inputs.block_for("export-feature", "удалённый.md")

    assert "удалённый.md" in block
    assert "в папке" in block and "нет" in block
    assert "ВЫБРАН ОПЕРАТОРОМ" not in block


def test_the_tool_repeats_the_choice_to_the_role(task):
    """
    Роль могла дойти до инструмента на втором круге, когда до начала сообщения
    ей уже далеко. Список без пометки она прочитает как «выбора не было».
    """
    answer = call(
        list_task_files,
        config={"configurable": {"input_dir": "export-feature", "input_file": "limits.csv"}},
    )

    assert "limits.csv (" in answer and "выбран оператором" in answer
    assert "встреча.md" in answer


def test_the_tool_repeats_a_choice_of_several_files(task):
    answer = call(
        list_task_files,
        config={
            "configurable": {
                "input_dir": "export-feature",
                "input_file": ["limits.csv", "встреча.md"],
            }
        },
    )

    assert answer.count("выбран оператором") == 2


def test_the_tool_warns_about_a_choice_it_cannot_find(task):
    answer = call(
        list_task_files,
        config={"configurable": {"input_dir": "export-feature", "input_file": "нет.md"}},
    )

    assert "нет.md" in answer and "ВНИМАНИЕ" in answer


# --------------------------------------------------------------------------
# Кеш: схемы инструментов входят в префикс
# --------------------------------------------------------------------------
def test_tool_descriptions_are_frozen():
    """
    Описание инструмента — часть кешируемого префикса. Переписать его «чуть
    понятнее» стоит ровно столько же, сколько поставить дату в system prompt,
    и теперь вдобавок бьёт по всем пяти ролям сразу: схемы привязаны ко всем.
    """
    assert list_task_files.name == "list_task_files"
    assert list_task_files.description == "Перечислить файлы, приложенные к текущей задаче."
    assert list(list_task_files.args) == []

    assert read_task_file.name == "read_task_file"
    assert (
        read_task_file.description
        == "Прочитать текстовый файл, приложенный к текущей задаче, по его имени."
    )
    assert list(read_task_file.args) == ["name"]


# --------------------------------------------------------------------------
# Готовые документы как папка задачи
#
# Конвейер работает не только по сырью оператора: Jira-декомпозиция и ревью
# раскладывают уже написанную аналитику, а она лежит в папке публикации.
# Поэтому папка публикации выбирается тем же способом, что и задача, — под
# виртуальным именем и с той же проверкой границы, только от своего корня.
# --------------------------------------------------------------------------
@pytest.fixture
def published(tmp_path):
    """Папка публикации с готовым документом прошлого прогона."""
    folder = publishers.directory()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "требования.md").write_text(
        "# Orbita — 01 Системные требования\n\nВыгрузка асинхронная.",
        encoding="utf-8",
    )
    return folder


def test_published_folder_is_offered_as_a_task(published, task):
    names = [t["name"] for t in inputs.list_tasks()]

    assert inputs.OUTPUT_TASK in names
    assert "export-feature" in names


def test_empty_published_folder_is_not_offered(task):
    """Строка без файлов рядом с задачами читалась бы как поломка."""
    assert inputs.OUTPUT_TASK not in [t["name"] for t in inputs.list_tasks()]


def test_role_reads_ready_documentation_from_the_published_folder(published):
    answer = call(read_task_file, name="требования.md", config=with_task(inputs.OUTPUT_TASK))

    assert "Выгрузка асинхронная" in answer


def test_published_folder_is_listed_to_the_role(published):
    assert "требования.md" in call(list_task_files, config=with_task(inputs.OUTPUT_TASK))


def test_ready_output_subfolder_is_offered_and_isolated(published):
    first = published / "проект-alpha"
    second = published / "проект-beta"
    first.mkdir()
    second.mkdir()
    (first / "требования.md").write_text("Только Alpha", encoding="utf-8")
    (second / "решение.md").write_text("Только Beta", encoding="utf-8")

    task_name = f"{inputs.OUTPUT_TASK}/проект-alpha"
    tasks = {task["name"]: task for task in inputs.list_tasks()}

    assert task_name in tasks
    assert tasks[task_name]["title"] == "готовые документы / проект-alpha"
    assert [item["name"] for item in tasks[task_name]["files"]] == ["требования.md"]
    assert "Только Alpha" in call(read_task_file, name="требования.md", config=with_task(task_name))
    assert "файл не прочитан" in call(
        read_task_file, name="решение.md", config=with_task(task_name)
    )


def test_empty_output_subfolder_is_not_offered(published):
    (published / "пустой-комплект").mkdir()

    assert f"{inputs.OUTPUT_TASK}/пустой-комплект" not in {
        task["name"] for task in inputs.list_tasks()
    }


@pytest.mark.parametrize(
    "name",
    [
        "../секрет.md",
        "../../секрет.md",
        "подпапка/../../секрет.md",
    ],
)
def test_path_cannot_leave_the_published_folder(published, name: str):
    (published.parent / "секрет.md").write_text("ключи", encoding="utf-8")

    answer = call(read_task_file, name=name, config=with_task(inputs.OUTPUT_TASK))

    assert "ключи" not in answer
    assert "файл не прочитан" in answer


def test_published_root_cannot_be_left_by_the_folder_name(published):
    """`..` подставляется и в имя папки, не только в имя файла."""
    with pytest.raises(inputs.InputError):
        inputs.folder(inputs.OUTPUT_TASK + "/../..")


# --------------------------------------------------------------------------
# Схема .drawio
#
# Роли схема приезжает разобранной (`drawio.py`), а не сырым XML, поэтому в
# `TEXT_SUFFIXES` её нет и инструмент чтения её не отдаёт. Оператору в списке
# файлов она при этом обязана читаться как материал: показать её содержимое
# ему можно, и по нему он проверяет, ту ли схему выбрал.
# --------------------------------------------------------------------------
@pytest.fixture
def diagram(tmp_path):
    folder = inputs.ensure_root() / "схемы"
    folder.mkdir(parents=True)
    (folder / "orders.drawio").write_text(
        "<mxfile><diagram>рисунок</diagram></mxfile>", encoding="utf-8"
    )
    return folder


def test_diagram_is_marked_as_a_diagram_not_as_a_binary(diagram):
    files = next(t["files"] for t in inputs.list_tasks() if t["name"] == "схемы")

    assert files[0]["diagram"] is True
    assert files[0]["text"] is False


def test_diagram_opens_in_the_interface_but_not_in_the_prompt(diagram):
    assert "рисунок" in inputs.preview("схемы", "orders.drawio")

    with pytest.raises(inputs.InputError):
        inputs.read("схемы", "orders.drawio")


def test_binary_is_refused_by_the_preview_too(task):
    with pytest.raises(inputs.InputError):
        inputs.preview("export-feature", "схема.png")

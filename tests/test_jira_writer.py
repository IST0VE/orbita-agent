"""
Запись в Jira на библиотеке `responses`.

Направление, которого у проекта не было: до сих пор он в трекер только ходил
читать. Поэтому и проверок здесь больше, чем у чтения, и все они про цену
ошибки — заведённая задача не отменяется перезапуском прогона.

Проверяется четыре вещи. Первая — что в трекер уезжает то и в том виде, что
он принимает: Cloud ждёт описание документом ADF, Data Center — строкой, и
перепутать их значит получить 400 на середине пачки. Вторая — что эпик
заводится раньше своих детей и ребёнок уезжает с настоящим ключом родителя.
Третья — что отказ на одной карточке не отменяет остальные. Четвёртая — что
`responses` роняет любой незарегистрированный запрос: тест, который случайно
уйдёт в сеть, упадёт, а не заведёт задач на чьём-то боевом трекере.
"""

from __future__ import annotations

import json

import pytest
import responses

from agent import jira, jira_plan, jira_writer

BASE = "https://jira.example.com"
API = "/rest/api/3"

CLOUD = jira.Settings(base_url=BASE, token="tok", email="me@org.com", api_path=API, timeout_s=5.0)
SERVER = jira.Settings(base_url=BASE, token="pat", api_path="/rest/api/2", timeout_s=5.0)


def plan_of(*issues: dict) -> jira_plan.Plan:
    return jira_plan.parse(json.dumps({"issues": list(issues)}, ensure_ascii=False))


def register_types(names: list[str] | None = None, api: str = API) -> None:
    """Схема проекта. Спрашивается перед пачкой один раз — на весь прогон."""
    names = names or ["Epic", "Task", "Sub-task"]
    if api == API:
        responses.add(
            responses.GET,
            f"{BASE}{api}/issue/createmeta/ORB/issuetypes",
            json={"values": [{"name": name} for name in names]},
            status=200,
        )
    else:
        responses.add(
            responses.GET,
            f"{BASE}{api}/issue/createmeta",
            json={"projects": [{"issuetypes": [{"name": name} for name in names]}]},
            status=200,
        )


def register_created(*keys: str, api: str = API) -> None:
    for key in keys:
        responses.add(responses.POST, f"{BASE}{api}/issue", json={"key": key}, status=201)


def sent(index: int) -> dict:
    """Поля, уехавшие в трекер n-м запросом на создание."""
    creates = [call for call in responses.calls if call.request.url.endswith("/issue")]
    return json.loads(creates[index].request.body)["fields"]


# --------------------------------------------------------------------------
# Формат описания
# --------------------------------------------------------------------------
@responses.activate
def test_cloud_receives_the_description_as_an_adf_document():
    register_types()
    register_created("ORB-1")

    jira_writer.create_issues(
        plan_of({"id": "T-1", "type": "Task", "summary": "Раз", "description": "Абзац"}),
        "ORB",
        settings=CLOUD,
    )

    description = sent(0)["description"]
    assert description["type"] == "doc"
    assert description["content"][0]["content"][0]["text"] == "Абзац"


@responses.activate
def test_data_center_receives_the_description_as_a_string():
    register_types(api="/rest/api/2")
    register_created("ORB-1", api="/rest/api/2")

    jira_writer.create_issues(
        plan_of({"id": "T-1", "type": "Task", "summary": "Раз", "description": "Абзац"}),
        "ORB",
        settings=SERVER,
    )

    assert sent(0)["description"].startswith("Абзац")


def test_bullets_become_a_list_in_adf():
    """Критерии приёмки — список, и списком они должны выглядеть в трекере."""
    document = jira_writer.text_to_adf("Цель\n\n- раз\n- два")

    kinds = [node["type"] for node in document["content"]]
    assert kinds == ["paragraph", "bulletList"]
    assert len(document["content"][1]["content"]) == 2


def test_an_empty_description_is_still_a_valid_document():
    """Пустой `content` Jira не принимает, а пустое описание — обычное дело."""
    assert jira_writer.text_to_adf("")["content"]


# --------------------------------------------------------------------------
# Порядок и родители
# --------------------------------------------------------------------------
@responses.activate
def test_a_child_is_created_with_the_real_key_of_its_epic():
    register_types()
    register_created("ORB-1", "ORB-2")

    result = jira_writer.create_issues(
        plan_of(
            {"id": "T-1", "type": "Task", "summary": "Задача", "parent": "EPIC-1"},
            {"id": "EPIC-1", "type": "Epic", "summary": "Эпик"},
        ),
        "ORB",
        settings=CLOUD,
    )

    assert sent(0)["summary"] == "Эпик"  # эпик заводится первым
    assert sent(1)["parent"] == {"key": "ORB-1"}
    assert [issue["key"] for issue in result["created"]] == ["ORB-1", "ORB-2"]


@responses.activate
def test_data_center_links_the_epic_through_the_configured_field(
    monkeypatch: pytest.MonkeyPatch,
):
    """В Data Center до нового интерфейса связь с эпиком — кастомное поле."""
    monkeypatch.setenv("JIRA_EPIC_LINK_FIELD", "customfield_10014")
    register_types(api="/rest/api/2")
    register_created("ORB-1", "ORB-2", api="/rest/api/2")

    jira_writer.create_issues(
        plan_of(
            {"id": "EPIC-1", "type": "Epic", "summary": "Эпик"},
            {"id": "T-1", "type": "Task", "summary": "Задача", "parent": "EPIC-1"},
        ),
        "ORB",
        settings=SERVER,
    )

    assert sent(1)["customfield_10014"] == "ORB-1"
    assert "parent" not in sent(1)


@responses.activate
def test_dependencies_become_links_between_created_issues():
    register_types()
    register_created("ORB-1", "ORB-2")
    responses.add(responses.POST, f"{BASE}{API}/issueLink", json={}, status=201)

    jira_writer.create_issues(
        plan_of(
            {"id": "T-1", "type": "Task", "summary": "Первая"},
            {"id": "T-2", "type": "Task", "summary": "Вторая", "depends_on": ["T-1"]},
        ),
        "ORB",
        settings=CLOUD,
    )

    link = json.loads(responses.calls[-1].request.body)
    assert link["outwardIssue"] == {"key": "ORB-1"}
    assert link["inwardIssue"] == {"key": "ORB-2"}


@responses.activate
def test_a_failed_link_does_not_spoil_created_issues():
    """Имя типа связи зависит от схемы, а задачи к этому моменту уже заведены."""
    register_types()
    register_created("ORB-1", "ORB-2")
    responses.add(responses.POST, f"{BASE}{API}/issueLink", json={}, status=400)

    result = jira_writer.create_issues(
        plan_of(
            {"id": "T-1", "type": "Task", "summary": "Первая"},
            {"id": "T-2", "type": "Task", "summary": "Вторая", "depends_on": ["T-1"]},
        ),
        "ORB",
        settings=CLOUD,
    )

    assert result["status"] == "created"
    assert len(result["created"]) == 2
    assert any("связь не создана" in warning for warning in result["warnings"])


# --------------------------------------------------------------------------
# Схема проекта
# --------------------------------------------------------------------------
@responses.activate
def test_a_type_missing_from_the_project_is_replaced_not_rejected():
    """`Story` есть не в каждой схеме, и терять из-за неё всю пачку нельзя."""
    register_types(["Epic", "Task"])
    register_created("ORB-1")

    result = jira_writer.create_issues(
        plan_of({"id": "T-1", "type": "Story", "summary": "История"}),
        "ORB",
        settings=CLOUD,
    )

    assert sent(0)["issuetype"] == {"name": "Task"}
    assert any("Story" in warning for warning in result["warnings"])


@responses.activate
def test_a_localised_epic_type_is_found_by_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_EPIC_TYPE", "Эпик")
    register_types(["Эпик", "Задача"])
    register_created("ORB-1")

    jira_writer.create_issues(
        plan_of({"id": "EPIC-1", "type": "Epic", "summary": "Платежи"}),
        "ORB",
        settings=CLOUD,
    )

    assert sent(0)["issuetype"] == {"name": "Эпик"}


@responses.activate
def test_unavailable_metadata_does_not_stop_the_batch():
    """Прав на createmeta может не быть; решать про тип будет сам трекер."""
    responses.add(responses.GET, f"{BASE}{API}/issue/createmeta/ORB/issuetypes", status=403)
    register_created("ORB-1")

    result = jira_writer.create_issues(
        plan_of({"id": "T-1", "type": "Task", "summary": "Раз"}), "ORB", settings=CLOUD
    )

    assert result["status"] == "created"
    assert sent(0)["issuetype"] == {"name": "Task"}


# --------------------------------------------------------------------------
# Отказы
# --------------------------------------------------------------------------
@responses.activate
def test_one_rejected_card_does_not_cancel_the_rest():
    """Прогон, заведший две задачи из трёх, полезнее прогона без единой."""
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-1"}, status=201)
    responses.add(
        responses.POST,
        f"{BASE}{API}/issue",
        json={"errorMessages": ["field 'labels' is not on the screen"]},
        status=400,
    )
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-3"}, status=201)

    result = jira_writer.create_issues(
        plan_of(
            {"id": "T-1", "type": "Task", "summary": "Раз"},
            {"id": "T-2", "type": "Task", "summary": "Два"},
            {"id": "T-3", "type": "Task", "summary": "Три"},
        ),
        "ORB",
        settings=CLOUD,
    )

    assert result["status"] == "partial"
    assert [issue["key"] for issue in result["created"]] == ["ORB-1", "ORB-3"]
    assert result["failed"][0]["local"] == "T-2"
    assert "labels" in result["failed"][0]["reason"]


@responses.activate
def test_a_child_of_a_failed_epic_is_created_without_a_parent():
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", status=400, json={"errorMessages": ["нет"]})
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-2"}, status=201)

    result = jira_writer.create_issues(
        plan_of(
            {"id": "EPIC-1", "type": "Epic", "summary": "Эпик"},
            {"id": "T-1", "type": "Task", "summary": "Задача", "parent": "EPIC-1"},
        ),
        "ORB",
        settings=CLOUD,
    )

    assert "parent" not in sent(1)
    assert len(result["created"]) == 1
    assert any("без родителя" in warning for warning in result["warnings"])


def test_creating_without_a_project_is_refused_before_the_network():
    with pytest.raises(jira_writer.JiraError):
        jira_writer.create_issues(
            plan_of({"id": "T-1", "type": "Task", "summary": "Раз"}), "", settings=CLOUD
        )


# --------------------------------------------------------------------------
# Справочник проектов
# --------------------------------------------------------------------------
@responses.activate
def test_cloud_projects_come_from_the_paged_endpoint():
    responses.add(
        responses.GET,
        f"{BASE}{API}/project/search",
        json={"values": [{"key": "PAY", "name": "Платежи"}, {"key": "ORB", "name": "Орбита"}]},
        status=200,
    )

    assert jira_writer.projects(CLOUD) == [
        {"key": "ORB", "name": "Орбита"},
        {"key": "PAY", "name": "Платежи"},
    ]


@responses.activate
def test_data_center_projects_come_as_a_plain_list():
    responses.add(
        responses.GET,
        f"{BASE}/rest/api/2/project",
        json=[{"key": "ORB", "name": "Орбита"}],
        status=200,
    )

    assert jira_writer.projects(SERVER) == [{"key": "ORB", "name": "Орбита"}]


# --------------------------------------------------------------------------
# Схема чужого проекта
#
# Русифицированная Jira Server 10 — не экзотика, а рабочий контур: общий
# `createmeta` там отвечает 404, типы называются по-русски, а поля эпика
# лежат в кастомных. Каждая из трёх мелочей по отдельности превращает пачку
# в список отказов, и увидеть это можно только на живом трекере — поэтому
# они проверяются здесь.
# --------------------------------------------------------------------------
@responses.activate
def test_types_fall_back_to_the_project_when_createmeta_is_gone():
    """Server 10 убрал общий createmeta; выбирать путь по версии API нельзя."""
    api = "/rest/api/2"
    responses.add(
        responses.GET, f"{BASE}{api}/issue/createmeta/ORB/issuetypes", json={}, status=404
    )
    responses.add(responses.GET, f"{BASE}{api}/issue/createmeta", json={}, status=404)
    responses.add(
        responses.GET,
        f"{BASE}{api}/project/ORB",
        json={"id": "1", "issueTypes": [{"id": "2", "name": "Задача"}]},
        status=200,
    )

    assert jira_writer.issue_types("ORB", SERVER) == ["Задача"]


@responses.activate
def test_project_endpoint_is_asked_first_and_alone():
    """Живой Server 10 отвечает на эндпоинт проекта — остальные не спрашиваются."""
    api = "/rest/api/2"
    responses.add(
        responses.GET,
        f"{BASE}{api}/issue/createmeta/ORB/issuetypes",
        json={"values": [{"id": "2", "name": "Задача"}]},
        status=200,
    )

    assert jira_writer.issue_types("ORB", SERVER) == ["Задача"]
    assert len(responses.calls) == 1


def test_russian_scheme_maps_by_translation_not_by_order():
    """
    Запасной вариант «первый тип списка» ставил истории эпиками.

    В схеме этого проекта первым идёт `Epic`, и до перевода `Story` и `Task`
    сваливались именно в него: доска на двенадцать эпиков выглядит как работа
    конвейера, а не как отказ.
    """
    plan = plan_of(
        {"id": "E-1", "type": "Epic", "summary": "Э"},
        {"id": "S-1", "type": "Story", "summary": "И"},
        {"id": "T-1", "type": "Task", "summary": "З"},
        {"id": "B-1", "type": "Sub-task", "summary": "П", "parent": "T-1"},
    )
    mapping, warnings = jira_writer.resolve_types(
        plan, ["Epic", "История", "Задача", "Подзадача", "Ошибка"]
    )

    assert mapping == {
        "Epic": "Epic",
        "Story": "История",
        "Task": "Задача",
        "Sub-task": "Подзадача",
    }
    assert len(warnings) == 3


def test_unknown_scheme_avoids_epics_and_subtasks_as_the_last_resort():
    """Своих имён типов у компании не угадать; промахнуться можно только мимо."""
    plan = plan_of({"id": "T-1", "type": "Task", "summary": "З"})
    mapping, _ = jira_writer.resolve_types(plan, ["Эпик", "Подзадача", "Инцидент"])

    assert mapping["Task"] == "Инцидент"


def register_fields(*, api: str = "/rest/api/2") -> None:
    """Справочник полей инстанса: номера кастомных полей эпика у каждого свои."""
    responses.add(
        responses.GET,
        f"{BASE}{api}/field",
        json=[
            {"id": "summary", "name": "Тема"},
            {"id": "customfield_10103", "name": "Epic Name"},
            {"id": "customfield_10101", "name": "Epic Link"},
        ],
        status=200,
    )


@responses.activate
def test_server_epic_gets_its_name_and_children_get_the_epic_link():
    api = "/rest/api/2"
    register_types(["Epic", "Задача"], api=api)
    register_fields()
    register_created("ORB-1", "ORB-2", api=api)

    jira_writer.create_issues(
        plan_of(
            {"id": "E-1", "type": "Epic", "summary": "Эпик"},
            {"id": "T-1", "type": "Task", "summary": "Задача", "parent": "E-1"},
        ),
        "ORB",
        settings=SERVER,
    )

    # Без имени эпика Server отвечает 400 на каждый эпик, без связи — на
    # каждого ребёнка: поля `parent` у обычной задачи там нет.
    assert sent(0)["customfield_10103"] == "Эпик"
    assert sent(1)["customfield_10101"] == "ORB-1"
    assert "parent" not in sent(1)


@responses.activate
def test_field_catalogue_is_asked_once_per_batch():
    """Справочник в 400 полей — не то, за чем ходят на каждую задачу."""
    api = "/rest/api/2"
    register_types(["Epic", "Задача"], api=api)
    register_fields()
    register_created("ORB-1", "ORB-2", "ORB-3", api=api)

    jira_writer.create_issues(
        plan_of(
            {"id": "E-1", "type": "Epic", "summary": "Эпик"},
            {"id": "T-1", "type": "Task", "summary": "Раз", "parent": "E-1"},
            {"id": "T-2", "type": "Task", "summary": "Два", "parent": "E-1"},
        ),
        "ORB",
        settings=SERVER,
    )

    assert len([call for call in responses.calls if call.request.url.endswith("/field")]) == 1


@responses.activate
def test_cloud_keeps_the_parent_field_and_never_asks_for_custom_fields():
    """В Cloud эпик — обычный родитель: справочник полей там не нужен вовсе."""
    register_types(["Epic", "Task"])
    register_created("ORB-1", "ORB-2")

    jira_writer.create_issues(
        plan_of(
            {"id": "E-1", "type": "Epic", "summary": "Эпик"},
            {"id": "T-1", "type": "Task", "summary": "Задача", "parent": "E-1"},
        ),
        "ORB",
        settings=CLOUD,
    )

    assert sent(1)["parent"] == {"key": "ORB-1"}
    assert not [call for call in responses.calls if call.request.url.endswith("/field")]


# --------------------------------------------------------------------------
# Экран создания
#
# Обязательным на нём бывает поле, которого нет ни в плане, ни в справочнике
# типов: на живом контуре таким оказался «Автор» — без значения по умолчанию,
# то есть отклоняющий каждую задачу пачки. Это единственное обязательное поле,
# которое конвейер может заполнить сам, и единственный случай, когда он
# добавляет в запрос то, чего не было в плане.
# --------------------------------------------------------------------------
def register_scheme(types: dict, screen: list, *, api: str = "/rest/api/2") -> None:
    """Типы проекта с номерами и экран создания у каждого из них."""
    responses.add(
        responses.GET,
        f"{BASE}{api}/issue/createmeta/ORB/issuetypes",
        json={"values": [{"id": number, "name": name} for name, number in types.items()]},
        status=200,
    )
    for number in types.values():
        responses.add(
            responses.GET,
            f"{BASE}{api}/issue/createmeta/ORB/issuetypes/{number}",
            json={"values": screen},
            status=200,
        )


REPORTER = {"fieldId": "reporter", "name": "Автор", "required": True, "hasDefaultValue": False}


@responses.activate
def test_required_reporter_is_filled_with_the_account_the_token_belongs_to():
    api = "/rest/api/2"
    register_scheme({"Задача": "10002"}, [REPORTER])
    register_fields()
    responses.add(responses.GET, f"{BASE}{api}/myself", json={"name": "ivanov"}, status=200)
    register_created("ORB-1", api=api)

    result = jira_writer.create_issues(
        plan_of({"id": "T-1", "type": "Task", "summary": "Раз"}), "ORB", settings=SERVER
    )

    assert sent(0)["reporter"] == {"name": "ivanov"}
    assert not [warning for warning in result["warnings"] if "экран создания" in warning]


@responses.activate
def test_reporter_is_not_sent_when_the_screen_does_not_demand_it():
    """Поля не на экране Jira отклоняет так же, как пропущенные обязательные."""
    api = "/rest/api/2"
    register_scheme({"Задача": "10002"}, [])
    register_fields()
    register_created("ORB-1", api=api)

    jira_writer.create_issues(
        plan_of({"id": "T-1", "type": "Task", "summary": "Раз"}), "ORB", settings=SERVER
    )

    assert "reporter" not in sent(0)
    assert not [call for call in responses.calls if call.request.url.endswith("/myself")]


@responses.activate
def test_unfillable_required_field_is_named_before_the_batch_starts():
    """Отказ по всей пачке должен читаться до неё, а не двенадцатью 400 подряд."""
    api = "/rest/api/2"
    register_scheme(
        {"Задача": "10002"},
        [{"fieldId": "customfield_12", "name": "Заказчик", "required": True}],
    )
    register_fields()
    register_created("ORB-1", api=api)

    result = jira_writer.create_issues(
        plan_of({"id": "T-1", "type": "Task", "summary": "Раз"}), "ORB", settings=SERVER
    )

    assert any("Заказчик (customfield_12)" in warning for warning in result["warnings"])


@responses.activate
def test_epic_fields_are_not_reported_as_unfillable():
    """Имя эпика конвейер заполняет — по номеру из справочника, а не по имени."""
    api = "/rest/api/2"
    register_scheme(
        {"Epic": "10000"},
        [{"fieldId": "customfield_10103", "name": "Epic Name", "required": True}],
    )
    register_fields()
    register_created("ORB-1", api=api)

    result = jira_writer.create_issues(
        plan_of({"id": "E-1", "type": "Epic", "summary": "Эпик"}), "ORB", settings=SERVER
    )

    assert sent(0)["customfield_10103"] == "Эпик"
    assert not [warning for warning in result["warnings"] if "экран создания" in warning]

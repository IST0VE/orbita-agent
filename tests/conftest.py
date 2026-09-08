"""
Общие фикстуры.

Главная забота — изоляция от окружения. `agent.config` на импорте делает
`load_dotenv()`, поэтому личный `.env` разработчика оказывается в `os.environ`
и начинает влиять на результаты: цены, имя агента, реквизиты Confluence,
провайдер. На машине без `.env` (в CI) те же тесты вели бы себя иначе.

Вторая забота — файлы: без изоляции тесты публикации писали бы `published/*.md`
прямо в рабочую копию. Путь публикации уводится в `tmp_path`.

Фикстуры ниже autouse: перед каждым тестом окружение чистится от всех
переменных проекта и от вендорных ключей, поэтому код видит ровно свои
значения по умолчанию. Тесту, которому нужна конкретная переменная, её
ставит `monkeypatch.setenv`.
"""

from __future__ import annotations

import os
import socket
import threading

import pytest

# Prevent collection-time model constructors from loading personal credentials.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for _name in list(os.environ):
    if _name.startswith(("LANGSMITH_", "LANGCHAIN_")):
        os.environ.pop(_name, None)
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

# Префиксы всего, что читает config.py, плюс "родные" переменные провайдеров:
# их читают конструкторы LangChain мимо нашего конфига.
_PREFIXES = (
    "API_",
    "LLM_",
    "PRICE_",
    "CONFLUENCE_",
    "DEMO_",
    "AGENT_",
    "BUDGET_",
    "KNOWLEDGE_",
    "MEMORY_",
    "PUBLISH_",
    "CHECKPOINT_",
    "DIAGRAM_",
    "POSTGRES_",
    "JIRA_",
    "ATLASSIAN_",
)
_NAMES = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    """Mocks and in-process ASGI work; accidental TCP/UDP traffic must fail."""
    def denied(*args, **kwargs):
        raise AssertionError("Real network access is forbidden in tests")

    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    local = threading.local()

    def connect(sock, address):
        # Windows implements asyncio's private wakeup socketpair over loopback.
        if getattr(local, "socketpair", False):
            return original_connect(sock, address)
        return denied()

    def socketpair(*args, **kwargs):
        local.socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            local.socketpair = False

    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket.socket, "sendto", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Убрать из окружения всё, что проект читает из .env."""
    for name in list(os.environ):
        if name.startswith(_PREFIXES) or name in _NAMES:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def virtual_request_clock(monkeypatch: pytest.MonkeyPatch):
    """Очередь запросов работает и в тестах, но пять секунд проходят виртуально."""
    from agent import request_pacing

    now = [0.0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(
        request_pacing, "_pacer",
        request_pacing.RequestPacer(clock=lambda: now[0], sleep=advance),
    )


@pytest.fixture(autouse=True)
def forget_instance_quirks():
    """
    Память о том, чего нет на инстансе, живёт процесс целиком.

    В бою это правильно — одна и та же wiki на весь прогон, — а между тестами
    такая память протекала бы: соседний тест начинал бы с чужим знанием.
    """
    from agent import confluence

    confluence._NO_SHARE_ID.clear()
    yield
    confluence._NO_SHARE_ID.clear()


@pytest.fixture(autouse=True)
def isolated_paths(clean_env, monkeypatch: pytest.MonkeyPatch, tmp_path):
    """
    Свои папки публикации и входных файлов на каждый тест.
    """
    monkeypatch.setenv("PUBLISH_DIR", str(tmp_path / "published"))
    # Папки задач тоже уводятся в tmp_path: `inputs.ensure_root()` создаёт
    # корень при первом обращении, и без изоляции тест завёл бы `input/`
    # прямо в рабочей копии — а заодно увидел бы чужие файлы, лежащие там.
    monkeypatch.setenv("AGENT_INPUT_DIR", str(tmp_path / "input"))
    # Подтверждение публикации включено по умолчанию — это решение про
    # интерактивный прогон, где по ту сторону `interrupt()` есть человек.
    # Тесты зовут ноды напрямую и человека не изображают: без этого каждая
    # публикация в них отклонялась бы как неподтверждённая. Кто проверяет
    # саму остановку и её умолчание, ставит переменную сам.
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "0")
    yield

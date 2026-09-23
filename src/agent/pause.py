"""
Пауза оператора: вклиниться в идущий конвейер и продолжить его с добавкой.

Это то, ради чего конвейер вообще собран графом, а не скриптом из пяти
вызовов подряд. Скрипт можно только убить и запустить заново — с первого
этапа и за те же деньги. Граф останавливается между шагами: состояние лежит
в чекпоинте, а не в памяти процесса, поэтому в него можно дописать то, чего
не хватило, и продолжить с того же места.

Остановок в проекте теперь три, и путать их не надо:

  ворота этапа    `nodes.make_gate_node`, PIPELINE_REQUIRE_APPROVAL. Остановка
                  запланирована заранее и случается на каждом этапе;
  подтверждение   `publish_nodes.approve_node`. Остановка перед внешним
                  действием — записью в чужую систему;
  пауза           этот модуль. Остановка незапланированная: оператор передумал
                  уже во время прогона. Её никто не объявлял заранее, и за
                  прогон её может не случиться ни разу.

Как это устроено. Кнопка «Пауза» графа не трогает — она оставляет заявку на
доске (`board`). Узел роли перед каждым обращением к модели смотрит на доску
и, увидев заявку, зовёт `interrupt()`: LangGraph замораживает тред на
чекпоинте и отдаёт вопрос наружу. Дальше всё как у любой другой остановки —
`Command(resume=...)` продолжает ход с того же места, а не с начала.

Почему заявка, а не поле состояния. Состояние треда во время прогона
принадлежит графу: писать в него снаружи, пока идут узлы, — это гонка с
чекпоинтером. Заявка живёт рядом с графом и читается узлом ровно в тот
момент, когда узел и так решает, тратить ли деньги. Доска процессная, как
журнал событий интерфейса и ключи идемпотентности рядом с ним: сервер у
проекта один, и второй ставился бы вместе со вторым чекпоинтером.

Почему перед вызовом модели, а не «в любой момент». Узел в LangGraph
атомарен: остановить его на середине нельзя, а если бы было можно —
получился бы наполовину сделанный шаг, который нечем продолжить. Поэтому
пауза берётся на ближайшей границе, и граница выбрана там, где она дороже
всего стоит: перед обращением к модели. Всё, что оператор допишет, попадёт
в следующий запрос, а не в следующий прогон.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from agent.pipeline import Pipeline

#: Сколько текста принимается одним указанием. Столько же разрешает схема
#: ответа в манифесте интерфейса: два потолка на одно поле разошлись бы.
MAX_NOTE = 4000

#: Сколько заявок помнится и как долго. Заявка живёт до ближайшего вызова
#: модели в своём треде, но тред могли и бросить, не дождавшись паузы, —
#: тогда она провисела бы в памяти процесса до перезапуска сервера.
_LIMIT = 1000
_TTL_SECONDS = 24 * 60 * 60


def _now() -> datetime:
    return datetime.now(UTC)


def thread_of(config: RunnableConfig | None) -> str:
    """
    Идентификатор треда из конфигурации хода.

    Пауза адресуется треду, а не прогону: прогонов у треда бывает несколько,
    и оператор останавливает работу, а не конкретный HTTP-стрим. Треда нет —
    значит, граф запущен без чекпоинтера, и останавливать нечего: продолжить
    такой ход всё равно было бы неоткуда.
    """
    return str(((config or {}).get("configurable") or {}).get("thread_id") or "")


class PauseBoard:
    """
    Заявки на паузу: треды, в которых оператор попросил остановиться.

    Заявка — это не состояние графа и не событие. Она живёт от нажатия кнопки
    до ближайшей границы шага и после неё исчезает: снимает её тот же узел,
    который её увидел. Поэтому здесь нет ни истории, ни счётчиков — только
    множество тредов и время, когда о каждом попросили.
    """

    def __init__(self, limit: int = _LIMIT, ttl_seconds: int = _TTL_SECONDS) -> None:
        self._limit = limit
        self._ttl = ttl_seconds
        self._items: OrderedDict[str, datetime] = OrderedDict()
        self._lock = Lock()

    def request(self, thread_id: str) -> dict:
        """Попросить паузу. Повторная просьба ничего не меняет."""
        if not thread_id:
            raise ValueError("пауза адресуется треду: thread_id пуст")
        if len(thread_id) > 200:
            raise ValueError("некорректный thread_id")
        now = _now()
        with self._lock:
            self._prune(now)
            self._items.setdefault(thread_id, now)
            self._items.move_to_end(thread_id)
            return self._status(thread_id)

    def cancel(self, thread_id: str) -> dict:
        """Снять заявку: передумали ждать или пауза уже взята."""
        with self._lock:
            self._items.pop(thread_id, None)
            return self._status(thread_id)

    def pending(self, thread_id: str) -> bool:
        """Ждёт ли этот тред паузы. Вопрос узла перед вызовом модели."""
        if not thread_id:
            return False
        with self._lock:
            self._prune(_now())
            return thread_id in self._items

    def status(self, thread_id: str) -> dict:
        """То же для интерфейса: заявка и время, когда её оставили."""
        with self._lock:
            self._prune(_now())
            return self._status(thread_id)

    def _prune(self, now: datetime) -> None:
        # Сравнение нестрогое: часы Windows идут шагами по 15 мс, и «прошло
        # ровно ноль» здесь обычное значение, а не вырожденный случай.
        stale = [
            thread
            for thread, at in self._items.items()
            if (now - at).total_seconds() >= self._ttl
        ]
        for thread in stale:
            self._items.pop(thread, None)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def _status(self, thread_id: str) -> dict:
        at = self._items.get(thread_id)
        return {
            "thread_id": thread_id,
            "pending": at is not None,
            "requested_at": at.isoformat(timespec="seconds") if at else "",
        }


board = PauseBoard()


def notes_block(notes: list | None) -> str:
    """
    Указания оператора одним блоком — для КОНЦА сообщения роли.

    Именно для конца. Кешируемый префикс роли обязан остаться побайтово
    неподвижным, иначе первая же пауза обнулила бы кеш всем оставшимся
    этапам: экономия, ради которой собран весь этот проект, стоит дороже
    удобного места для абзаца.
    """
    items = [
        text
        for text in (str((note or {}).get("text") or "").strip() for note in notes or [])
        if text
    ]
    if not items:
        return ""
    lines = "\n".join(f"- {text}" for text in items)
    # Источников у указаний два: пауза посреди прогона и следующее сообщение
    # в треде, где документы уже выпущены (`nodes.context_node`). Для роли
    # разницы между ними нет — это одно и то же уточнение задачи.
    return (
        "\n\nУказания оператора к задаче — даны на паузе или следующим сообщением "
        "в треде, по порядку. Учти их наравне с задачей; при расхождении с ней "
        "побеждает указание, а из двух указаний — более позднее.\n" + lines + "\n"
    )


def decision_of(answer: Any) -> dict:
    """
    Ответ оператора в одном виде: `{"decision": ..., "note": ...}`.

    Форм у ответа три, потому что на паузу отвечают не только кнопкой в
    интерфейсе: из Studio и из теста продолжают голым `Command(resume=...)`.
    Словарь — ответ формы; строка — просто приписка, с которой продолжают;
    `False` — отказ, то есть остановка конвейера. Всё остальное, включая
    `True` и `None`, означает «продолжай как шёл».
    """
    if isinstance(answer, dict):
        raw = str(answer.get("decision") or "continue").strip().lower()
        note = str(answer.get("note") or answer.get("reason") or "").strip()
    elif isinstance(answer, str):
        raw, note = "continue", answer.strip()
    else:
        raw, note = ("stop" if answer is False else "continue"), ""
    decision = "stop" if raw in {"stop", "rejected", "halt"} else "continue"
    return {"decision": decision, "note": note[:MAX_NOTE]}


def _payload(state: dict, *, stage: str, title: str, pipeline: Pipeline | None) -> dict:
    """Что оператор видит в карточке паузы: где конвейер встал и что уже готово."""
    artifacts = state.get("artifacts") or {}
    known = [str((note or {}).get("text") or "").strip() for note in state.get("notes") or []]
    return {
        "action": "pause",
        "title": f"Пауза перед этапом «{title}»",
        "stage": stage,
        "stage_title": title,
        # Список этапов есть только у конвейера, собранного из ролей. У
        # графа со своей топологией его нет, и рисовать вместо него
        # выдуманную последовательность хуже, чем не рисовать ничего.
        "done": [item.title for item in pipeline.done(artifacts)] if pipeline else [],
        "pending": [item.title for item in pipeline.pending(artifacts)] if pipeline else [],
        "notes": [text for text in known if text],
        "hint": (
            "Напишите, что добавить, и продолжите: указание уедет в конец "
            "сообщения этому и следующим этапам. Пустой ответ просто продолжит "
            "конвейер с того же места."
        ),
    }


def _update(answer: Any, *, stage: str) -> dict:
    decision = decision_of(answer)
    if decision["decision"] == "stop":
        return {
            "halt": {
                "stage": stage,
                # Ворота останавливают ПОСЛЕ этапа, документ которого показали;
                # пауза — ПЕРЕД этапом, который ещё не выполнялся. Различие
                # уезжает в сообщение об остановке (`routes.halted_node`):
                # без него оно назвало бы этап, которого как раз и не было.
                "point": "before",
                "reason": decision["note"] or "оператор остановил конвейер на паузе",
            }
        }
    if not decision["note"]:
        return {}
    return {"notes": [note(stage, decision["note"])]}


def note(stage: str, text: str) -> dict:
    """
    Одно указание оператора в том виде, в каком оно лежит в `notes`.

    Пишут их двое — пауза и нода контекста, когда следующее сообщение в треде
    правит уже выпущенные документы, — и форма у них обязана быть одна:
    читает их один и тот же `notes_block`.
    """
    return {"stage": stage, "text": text, "at": _now().isoformat(timespec="seconds")}


def checkpoint(
    state: dict,
    config: RunnableConfig | None,
    *,
    stage: str,
    title: str,
    pipeline: Pipeline | None = None,
) -> dict:
    """
    Граница шага, на которой конвейер отдаёт ход оператору, — если тот просил.

    Возвращает обновление состояния: приписку оператора, остановку конвейера
    или ничего. Ничего — обычный случай: заявки нет, и узел идёт дальше, не
    заметив, что здесь вообще есть остановка.

    `stage` и `title` — имя шага и его название на экране; конвейеру из ролей
    это ключ и заголовок роли. Именно они, а не сама роль: остановиться можно
    и в графе, у которого ролей нет вовсе (правка документа, планирование НТ),
    а просить у такого графа `Role` ради двух строк значило бы выдумать ему
    конвейер, которого у него нет. `pipeline` необязателен по той же причине —
    он нужен только для списка готовых и оставшихся этапов в карточке.

    Остановку («stop») вызывающий обязан разобрать сам: в состояние она
    приезжает полем `halt`, но увести по ней ход умеет только топология,
    а она у каждого графа своя.
    """
    thread = thread_of(config)
    if not board.pending(thread):
        return {}
    answer = interrupt(_payload(state, stage=stage, title=title, pipeline=pipeline))
    # Заявка снимается ПОСЛЕ ответа, а не до вопроса. Узел после остановки
    # выполняется заново с начала, и снятая заранее заявка увела бы его мимо
    # `interrupt()` — то есть мимо ответа оператора, который в этот момент
    # уже лежит в чекпоинте и ждёт, когда его заберут.
    board.cancel(thread)
    return _update(answer, stage=stage)

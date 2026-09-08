"""
Демо: три задачи в одном треде, после каждой — статистика кеша.

Один ход — это полный прогон конвейера: пять ролей подряд, от системных
требований до ревью. Кеш поэтому работает в двух измерениях сразу. Внутри
хода вторая и следующие роли попадают в общее начало префикса (`prompts.COMMON`)
уже на первом своём вызове; от хода к ходу каждая роль видит свой префикс
побайтово тем же. Строка "cache hit rate" считает и то и другое.

    python run_demo.py                  # правильный вариант: стабильный префикс
    python run_demo.py --bad            # антипример: в промпт подставляется время
    python run_demo.py --json           # машинный вывод: одна JSON-строка на ход
    python run_demo.py --log calls.jsonl  # лог CostMeter: строка на вызов модели

Провайдер, модель, цены и thread_id берутся из `.env` — см. `agent/config.py`.

Смотрите на строку "cache hit rate". В правильном варианте она высока уже на
первом ходе — за счёт общего начала пяти префиксов — и растёт дальше. В --bad
остаётся около нуля на каждом ходе, потому что префикс меняется и DeepSeek
не может переиспользовать ничего.

Учёт идёт двумя путями сразу: редьюсер в состоянии графа копит счётчики по
треду, а `CostMeter` ловит те же ответы как callback handler. Оба обязаны
показать одно и то же — при расхождении демо скажет об этом в конце.

Чекпоинтер и store берутся из `agent/checkpointer.py`: по умолчанию оба живут
в процессе, а с CHECKPOINT_BACKEND=postgres переживают перезапуск — и тогда
долгая память по проекту работает между запусками, а не только между тредами
одного прогона.

Материалы задачи демо не подкладывает: файлы читает аналитик через
AGENT_INPUT_DIR, и для замера кеша это лишняя переменная. Как это выглядит
с настоящей папкой — см. `input/example` и панель файлов в интерфейсе.
"""

import json
import os
import sys

from langchain_core.messages import HumanMessage

from agent import config as cfg
from agent import graph as g
from agent import publishers
from agent.checkpointer import open_checkpointer, open_store
from costmeter import CostMeter

# Остановка на верификацию черновиков включена по умолчанию, и это правильно
# везде, где по ту сторону есть человек. Здесь его нет: демо считает кеш и
# ходит тремя вызовами подряд, а `interrupt()` заморозил бы тред на первом же.
# `setdefault` после импорта config: тот уже перенёс `.env` в окружение, и
# явное значение оттуда сильнее — кому нужно подтверждение и в демо, тот его
# получит.
os.environ.setdefault("PUBLISH_REQUIRE_APPROVAL", "0")

BAD = "--bad" in sys.argv
AS_JSON = "--json" in sys.argv
LOG_PATH = None
if "--log" in sys.argv:
    LOG_PATH = sys.argv[sys.argv.index("--log") + 1]

# Три задачи одного проекта: вторая и третья уточняют первую. Так и работает
# аналитик, и так же выгоднее всего для кеша — префикс не двигается, меняется
# только хвост сообщения.
QUESTIONS = [
    "Спроектировать асинхронную выгрузку заказов через REST: период, фильтры, "
    "формат CSV или XLSX, статус выгрузки и ссылка на готовый файл.",
    "Добавить требование: повторный запрос после сетевой ошибки не должен "
    "создавать дубликат выгрузки.",
    "Учесть падение worker во время формирования файла и повторную доставку "
    "сообщения из брокера.",
]


def answer_for_console(text: str) -> str:
    """Ответ модели в пределах DEMO_ANSWER_CHARS, с честной пометкой об обрезке."""
    text = text.strip()
    limit = cfg.demo_answer_chars()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [обрезано, ещё {len(text) - limit} симв.; DEMO_ANSWER_CHARS]"


def counters(usage: dict) -> dict:
    """Только числовые поля: в usage может лежать и строка от провайдера."""
    return {
        key: value
        for key, value in usage.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def header() -> str:
    """Строка с режимом прогона: что именно сейчас проверяется."""
    mode = "АНТИПРИМЕР (ломаем кеш)" if BAD else "стабильный префикс"
    knowledge = "база знаний" if cfg.knowledge_enabled() else "политика в префиксе"
    return (
        f"{g.PROVIDER} / {g.MODEL} | режим: {mode} | {knowledge} | "
        f"публикация: {publishers.resolve()} | чекпоинты: {cfg.checkpoint_backend()}"
    )


def main() -> None:
    meter = CostMeter(
        provider=g.PROVIDER,
        model=g.MODEL,
        log_path=LOG_PATH,
        budget_usd=cfg.budget_usd_per_thread() or None,
    )

    # Store живёт рядом с чекпоинтером: тред помнит себя, store помнит аккаунт
    # поверх тредов. На memory-бэкенде оба умирают вместе с процессом.
    with open_checkpointer() as saver, open_store() as store:
        app = g.build_graph(unstable_prefix=BAD).compile(checkpointer=saver, store=store)
        # Один thread_id на все три хода: история дописывается в конец, префикс
        # растёт, кеш работает. Меняется через DEMO_THREAD_ID — например, чтобы
        # прогнать демо заново с чистой страницей в Confluence.
        config = {
            "configurable": {"thread_id": cfg.demo_thread_id()},
            "callbacks": [meter],
        }

        if not AS_JSON:
            print(header())
            print("=" * 78)

        previous: dict = {}
        for i, question in enumerate(QUESTIONS, start=1):
            result = app.invoke({"messages": [HumanMessage(content=question)]}, config=config)

            total = counters(result["usage"])
            delta = {key: value - previous.get(key, 0) for key, value in total.items()}
            previous = dict(total)
            answer = result["messages"][-1].content

            if AS_JSON:
                # Одна строка на ход: ложится в таблицу, в график или в CI без
                # ручной обработки. Полный текст ответа сюда не идёт — для него
                # есть обычный вывод.
                print(json.dumps({
                    "turn": i,
                    "question": question,
                    "answer_chars": len(answer),
                    "turn_usage": delta,
                    "turn_cost_usd": round(g.estimate_cost(delta), 8),
                    "total_usage": total,
                    "total_cost_usd": round(g.estimate_cost(total), 8),
                    "hit_rate": round(g.hit_rate(total), 1),
                    "publication": (result.get("publication") or {}).get("status"),
                }, ensure_ascii=False), flush=True)
                continue

            print(f"\n[ход {i}] {question}")
            print("-" * 78)
            print(answer_for_console(answer))
            print("-" * 78)
            print("за этот ход:   " + g.format_usage(delta))
            print("накопительно:  " + g.format_usage(total))
            print("               " + g.format_publication(result.get("publication")))

    if not AS_JSON:
        print("\n" + "=" * 78)
        print("Итог по треду: " + g.format_usage(previous))
        print("\nТо же самое глазами CostMeter (callback handler, про граф не знает):")
        print(meter.report())

        # Два независимых пути учёта обязаны сойтись. Расхождение означает, что
        # один из них перестал видеть часть вызовов, — молча это оставлять нельзя.
        if meter.usage.as_dict() != previous:
            print("\nВНИМАНИЕ: счётчики разошлись")
            print(f"  состояние графа: {previous}")
            print(f"  CostMeter:       {meter.usage.as_dict()}")

    if LOG_PATH:
        print(f"\nЛог вызовов: {LOG_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()

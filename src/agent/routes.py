"""
Маршруты: решения о том, куда пойдёт ход, и два узла, которыми он кончается.

Функция маршрута возвращает имя ветки и не меняет состояние — это её главное
свойство. Решение о деньгах, о пустом входе и о том, дочитала ли роль файлы,
принимается без побочных эффектов, и потому проверяется вызовом с готовым
словарём, а не прогоном графа.

Ворота бюджета стоят перед каждым обращением к модели. Кончились деньги на
третьем этапе — конвейер не падает: `over_budget_node` пишет причину, ход
уходит в память и публикацию, и то, что успели выпустить, уезжает с честной
отметкой о недоделанном. `halted_node` — то же самое для остановки
оператором. Оба узла денег не тратят: они пишут текст, а не спрашивают
модель.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from agent import config as cfg
from agent import roles
from agent.cost import estimate_cost
from agent.pipeline import Pipeline
from agent.state import State


def make_role_router(
    role: roles.Role,
    pipeline: Pipeline = roles.PIPELINE,
    last_target: str = "remember",
):
    """
    Куда после роли: в инструменты или на следующий этап.

    Условный переход нужен только роли с инструментами. Остальные соединены
    обычным ребром — и на картинке графа сразу видно, кто может уйти в цикл,
    а кто ходит к модели ровно один раз.

    `last_target` — куда ведёт ПОСЛЕДНЯЯ роль конвейера. По умолчанию это
    запись в память, но у конвейера с постлюдией между ними стоит ещё узел,
    и знать об этом должны оба места, где считается адресат: и это, и сборка.
    """
    following = pipeline.after(role)
    target = f"gate_{following.key}" if following else last_target

    def role_router(state: State) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else target

    return role_router


# --------------------------------------------------------------------------
# Бюджет на тред
#
# Учёт, который только отчитывается постфактум, — половина ценности. Вторая
# половина здесь: ворота стоят перед каждым обращением к модели, и на длинном
# или зациклившемся треде граф уходит дальше с честным сообщением вместо
# следующего вызова. Проверяются и вход в тред, и цикл с инструментами, и
# каждый этап конвейера: у пяти ролей пять поводов потратить деньги.
# --------------------------------------------------------------------------
def make_tools_router(pipeline: Pipeline):
    """
    Куда вернуться после ответа инструмента: в роль, которая его позвала.

    Роль записывает свой ключ в `state["stage"]` тем же обновлением, в котором
    просит инструмент, поэтому спрашивающий известен и искать его в истории
    сообщений не нужно. Ворота бюджета стоят и здесь: переписка с инструментами
    — это вызовы модели, и оборваться она обязана там же, где всё остальное.
    """
    readers = {role.key for role in pipeline.roles if role.reads_files}
    fallback = next((role.key for role in pipeline.roles if role.reads_files), None)

    def tools_router(state: State) -> str:
        if budget_gate(state) == "over_budget":
            return "over_budget"
        stage = str(state.get("stage") or "")
        return stage if stage in readers else (fallback or "over_budget")

    return tools_router


def budget_gate(state: State) -> str:
    limit = cfg.budget_usd_per_thread()
    if limit <= 0:
        return "agent"
    spent = estimate_cost(state.get("usage") or {})
    return "over_budget" if spent >= limit else "agent"


# --------------------------------------------------------------------------
# Проверка входа
#
# Ворота бюджета отвечают на вопрос «есть ли на что говорить», проверка входа —
# на вопрос «есть ли о чём». Конвейеру, который принимает готовый материал со
# стороны (аналитика, написанная другим человеком; схема из чужого репозитория),
# пустой вход — обычный исход, а не сбой: выбрали не ту папку, забыли приложить
# файл, вставили одну строчку вместо документа. Решать это должен код и до
# первого платного вызова, иначе конвейер честно сходит в модель столько раз,
# сколько у него ролей, и выпустит документ из одних `TBD`.
#
# Что считать пустым входом, знает только сам конвейер: у аналитики материалы
# бывают и в сообщении, и в папке, у разбора схем вход — один файл. Поэтому
# проверка приходит функцией и отвечает строкой: пустая — вход годится,
# непустая — причина отказа, которую увидит оператор.
# --------------------------------------------------------------------------
Admission = Callable[["State", RunnableConfig], str]


def make_no_input_node(admission: Admission):
    """Отказ вместо прогона. Модель не вызывается, деньги не тратятся."""

    def no_input_node(state: State, config: RunnableConfig) -> dict:
        return {"messages": [AIMessage(content=admission(state, config))]}

    return no_input_node


def make_entry_router(admission: Admission):
    """
    Вход в тред: сначала есть ли материал, потом хватает ли денег.

    Порядок именно такой. Обе проверки бесплатны, но причина «нечего
    раскладывать» точнее причины «кончился бюджет»: первая говорит оператору,
    что делать, вторая — только то, что прогон не состоялся.
    """

    def entry_router(state: State, config: RunnableConfig) -> str:
        return "no_input" if admission(state, config) else budget_gate(state)

    return entry_router


def make_gate_router(role: roles.Role):
    """Пускать ли конвейер на следующий этап: решение оператора, потом деньги."""

    def gate_router(state: State) -> str:
        if state.get("halt"):
            return "halted"
        return budget_gate(state)

    return gate_router


def over_budget_node(state: State, pipeline: Pipeline = roles.PIPELINE) -> dict:
    """Сообщение вместо вызова модели. Денег не тратит."""
    limit = cfg.budget_usd_per_thread()
    spent = estimate_cost(state.get("usage") or {})
    left = [role.title for role in pipeline.pending(state.get("artifacts"))]
    remaining = f" Не выполнены этапы: {', '.join(left)}." if left else ""
    return {
        "messages": [
            AIMessage(
                content=(
                    f"Бюджет треда исчерпан: потрачено ${spent:.6f} при лимите "
                    f"${limit:.6f} (BUDGET_USD_PER_THREAD). Обращение к модели "
                    f"не выполнено.{remaining} Поднимите лимит или начните новый тред."
                )
            )
        ]
    }


def halted_node(state: State, pipeline: Pipeline = roles.PIPELINE) -> dict:
    """Конвейер остановлен оператором на воротах этапа. Денег не тратит."""
    halt = state.get("halt") or {}
    stage = halt.get("stage") or ""
    title = pipeline.by_key(stage).title if stage in pipeline.keys else "предыдущего этапа"
    left = [role.title for role in pipeline.pending(state.get("artifacts"))]
    remaining = f" Не выполнены этапы: {', '.join(left)}." if left else ""
    return {
        "messages": [
            AIMessage(
                content=(
                    f"Конвейер остановлен оператором после этапа «{title}». "
                    f"Причина: {halt.get('reason') or 'не указана'}.{remaining} "
                    f"Готовые документы публикуются, остальные не выполнялись."
                )
            )
        ]
    }


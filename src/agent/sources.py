"""
Откуда конвейер берёт материал: четыре способа и ни одного лишнего.

Материал у конвейеров разный — страница Confluence, файлы папки задачи, тикет
Jira, сам текст сообщения, — но способов его назвать всего несколько, и до
этого модуля каждый граф решал это заново. «Вопрос оператора без
подставленного контекста» существовал в четырёх экземплярах, два из них
побайтово совпадали, а два были вписаны прямо в проверку входа; папка задачи
доставалась из `configurable` семью одинаковыми строками в шести файлах.
Такое дублирование не ломается громко: оно расходится по одной правке за раз,
и первым это замечает тот, у кого один граф читает выбранный файл, а соседний
молча читает папку целиком.

Здесь только добыча: что прочитано и откуда, в виде данных. Как об этом
сказать ролям — дело конвейера: у декомпозиции это «раскладывай выбранное»,
у разбора схем «схема не прочитана», и общих слов у них нет. Поэтому
`from_confluence` и `from_files` возвращают словарь, а не готовый текст, и
подача остаётся там, где живёт словарь конвейера.

Ни одна функция отсюда не вызывает модель. Адрес страницы, имя файла и ключ
задачи уже написаны в запросе оператора; просить модель позвать инструмент
с известным аргументом — это оплаченный вызов, который ничего не выбирает.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from urllib.parse import urlsplit

from agent import config as cfg
from agent import confluence, inputs, jira
from agent.documents import operator_question, task_of, text_of
from agent.runtime import options


class Budget:
    """
    Остаток общего потолка на набор материалов.

    Отдельный тип понадобился из-за нуля. `AGENT_INPUT_MAX_CHARS=0` документирован
    как «читать целиком», а в коде оставался обычным числом, и проверка «место
    кончилось» (`size >= limit`, `remaining <= 0`) при нуле была истинной с самого
    начала: не читалось ничего, и папка с материалами превращалась в ложное
    «ни один файл папки задачи не прочитан».

    Потолок применяется к набору, а не к файлу: комплект из требований и
    контракта API уезжает в промпт вместе и оплачивается вместе. Чтение одного
    файла — инструментом, предпросмотром в интерфейсе — ограничивается тем же
    числом, но на файл: см. `inputs.read` и `config.input_max_chars`.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(0, int(limit))
        self.used = 0

    @property
    def unlimited(self) -> bool:
        return self.limit == 0

    @property
    def exhausted(self) -> bool:
        """Место кончилось. Без потолка не кончается никогда."""
        return not self.unlimited and self.used >= self.limit

    @property
    def left(self) -> int:
        """Сколько ещё можно прочитать; 0 здесь означает «сколько угодно»."""
        return 0 if self.unlimited else max(0, self.limit - self.used)

    def head(self, text: str) -> str:
        """Голова текста по остатку; целиком, если потолка нет."""
        return text if self.unlimited else text[: self.left]

    def spend(self, text: str) -> str:
        self.used += len(text)
        return text

    @property
    def overflowed(self) -> bool:
        """Материал упёрся в потолок. Без потолка — никогда."""
        return self.exhausted


#: Сколько источников по ссылкам из запроса читается до первой роли. Остальные
#: остаются в списке с причиной: открыть их может роль с инструментами.
LINKED_MAX = 8

_LINK = re.compile(r"https?://[^\s<>]+")
_NAMES = {"confluence": "Confluence", "jira": "Jira"}


def references(question: str) -> list[dict]:
    """
    Явные ссылки запроса: страницы Confluence и задачи Jira, по порядку и без повторов.

    Ссылка на чужой хост остаётся в списке с причиной и не читается: ключ
    ORB-12 и страница 12345 есть в любом трекере и в любой вики, и по чужой
    ссылке конвейер прочитал бы из своей совсем другой документ. Голый ключ
    задачи — тоже явная ссылка; ключи внутри адресов разобраны вместе с адресом.

    Повтор ключа не читается дважды, но отклонённая ссылка не занимает места
    разрешённой: чужая ORB-12 перед своей ORB-12 уступает ей своё место в
    списке, иначе своя задача не читалась бы вовсе.
    """
    found: dict[tuple[str, str], dict] = {}

    def keep(item: dict) -> None:
        key = (item["system"], item["id"])
        old = found.get(key)
        if old is None or (old.get("error") and not item.get("error")):
            found[key] = item
    for raw in _LINK.findall(question or ""):
        url = raw.rstrip(".,;)]}")
        host = urlsplit(url).hostname
        jira_host = urlsplit(cfg.jira_base_url()).hostname if cfg.jira_base_url() else None
        for system, ids, base in (
            ("confluence", confluence.find_page_ids(url), cfg.confluence_base_url()),
            (
                "jira",
                jira.find_keys(url)
                if "/browse/" in url or "selectedIssue=" in url or (jira_host and host == jira_host)
                else [],
                cfg.jira_base_url(),
            ),
        ):
            ours = bool(base) and host == urlsplit(base).hostname
            for key in ids:
                item = {"system": system, "id": key, "url": url}
                if not ours:
                    item["error"] = f"домен не совпадает с настройками {_NAMES[system]}"
                keep(item)
    for key in jira.find_keys(_LINK.sub("", question or "")):
        keep({"system": "jira", "id": key, "url": ""})
    return list(found.values())


def linked(question: str, skip: Collection[tuple[str, str]] = ()) -> list[dict]:
    """
    Прочитать явные ссылки запроса — без модели, до первой роли.

    По элементу на ссылку: `system`, `id`, `url`, а у прочитанной ещё `title`,
    `text` и `truncated`; у непрочитанной — `error` с причиной. Причина остаётся
    в списке, а не пропадает: «страница не открылась» и «ссылки не было» для
    читателя документа разные вещи.

    skip — ссылки, прочитанные на прошлых ходах треда: пары `(system, id)`
    в том виде, в каком их назвал оператор.

    У прочитанной `id` — то, что вернула система, а `asked` — то, что было
    в запросе. Они расходятся у перенесённой задачи: ORB-12 отвечает как
    NEW-5, и без `asked` следующий запрос с ORB-12 не узнал бы прочитанное.

    `LINKED_MAX` ограничивает обращения в сеть, а не удачные чтения: двадцать
    недоступных задач иначе давали двадцать запросов, и при таймаутах прогон
    стоял минутами до первой роли. Потолок объёма общий на все ссылки, и ноль
    в нём означает «без потолка», а не «ничего не читать»: со старым
    `remaining <= 0` каждый источник объявлялся непрочитанным по
    несуществующему лимиту.
    """
    budget = Budget(cfg.input_max_chars())
    items: list[dict] = []
    attempts = 0
    for ref in references(question):
        system, key = ref["system"], ref["id"]
        if (system, key) in skip:
            continue
        if ref.get("error"):
            items.append(ref)
            continue
        if attempts >= LINKED_MAX or budget.exhausted:
            items.append({**ref, "error": "достигнут лимит контекста"})
            continue
        api = confluence if system == "confluence" else jira
        absent = api.missing_vars()
        if absent:
            items.append(
                {**ref, "error": f"{_NAMES[system]} не настроена ({', '.join(absent)})"}
            )
            continue
        attempts += 1
        try:
            if system == "confluence":
                page = confluence.fetch_page(key)
                body = confluence.format_page(page)
                found = {
                    "id": str(page.get("id") or key),
                    "title": page.get("title") or "",
                    "url": page.get("url") or ref["url"],
                    "truncated": bool(page.get("truncated")),
                }
            else:
                issue = jira.fetch_issue(key)
                body = jira.format_issue(issue)
                found = {
                    "id": str(issue.get("key") or key),
                    "title": issue.get("summary") or "",
                    "url": issue.get("url") or ref["url"],
                    "truncated": False,
                }
        except (confluence.ConfluenceError, jira.JiraError) as exc:
            items.append({**ref, "error": str(exc)})
            continue
        excerpt = budget.spend(budget.head(body))
        found["truncated"] = found["truncated"] or len(excerpt) < len(body)
        items.append({**ref, **found, "asked": key, "text": excerpt})
    return items


def materials(
    question: str, config: object | None = None, remembered: Sequence[str] = ()
) -> tuple[dict | None, list[str]]:
    """
    Файлы, которые оператор дал к задаче: прочитанные и остальные по именам.

    Читаются выбранные в интерфейсе или названные в запросе — по тому же
    доводу, что и ссылки: какие это файлы, оператор уже сказал, и модели здесь
    выбирать нечего. Выбирать ЗА оператора код тоже не должен: невыбранные
    файлы только перечисляются, и прочитать их может роль с инструментами.

    remembered — файлы, прочитанные на прошлом ходе треда. Они читаются снова,
    если в этом ходе ничего не выбрано и не названо: указание «поправь раздел»
    не называет файлов, а задача треда и её материалы остаются прежними. Без
    этого файл, названный в первом запросе, на следующем ходе пропадал из
    брифа и из реестра источников. Это выбор оператора в этом же треде, а не
    выбор за него; другой выбор или другое имя в запросе его заменяют.

    Возвращает результат `from_files` (None — читать нечего) и имена
    остальных текстовых файлов чата.
    """
    task = task_dir(config)
    names = inputs.readable_files(task) if task else []
    wanted = picked_files(config)
    if not wanted:
        lowered = (question or "").lower()
        wanted = [name for name in names if name.lower() in lowered]
    if not wanted:
        wanted = [name for name in remembered if name in names]
    found = from_files(question, task, wanted) if task and wanted else None
    read = list((found or {}).get("names") or [])
    return found, [name for name in names if name not in read]


def question_of(state: dict) -> str:
    """
    Запрос оператора без того, что мы к нему дописали.

    К последнему сообщению человека нода контекста подставляет справку из базы
    знаний, память по аккаунту и список файлов. Искать ключ задачи или ссылку
    на страницу надо в том, что написал человек: список файлов папки способен
    подсунуть имя, которого оператор не называл.

    Последнее сообщение не человеческое — идёт следующий ход уже начатого
    треда, и вопрос берётся из состояния, куда его положила та же нода.
    """
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if last is not None and getattr(last, "type", "") == "human":
        return operator_question(text_of(last))
    return task_of(state)


def task_dir(config: object | None = None) -> str:
    """Папка задачи текущего хода. Пустая строка — папка не выбрана."""
    return str(options(config).get("input_dir") or "")


def picked_files(config: object | None = None) -> list[str]:
    """Файлы, выбранные оператором в интерфейсе. Пустой список — выбора нет."""
    return inputs.picked_names(options(config).get("input_file"))


def from_confluence(question: str) -> dict | None:
    """Страница по ссылке из запроса. None — ссылки нет или читать её нечем."""
    ids = confluence.find_page_ids(question)
    if not ids or confluence.missing_vars():
        return None

    try:
        page = confluence.fetch_page(ids[0])
    except confluence.ConfluenceError as exc:
        return {"kind": "confluence", "id": ids[0], "error": str(exc)}

    return {
        "kind": "confluence",
        "id": page["id"],
        "title": page["title"],
        "url": page["url"],
        "text": page["text"],
        "truncated": page["truncated"],
        "extra": ids[1:],
        "foreign": confluence.foreign_hosts(question),
    }


def from_files(question: str, task: str, picked: str | Sequence[str] = ()) -> dict | None:
    """
    Текстовые файлы папки задачи. None — читать нечего.

    Источник выбирается тремя способами, и они перечислены по убыванию
    определённости.

    Выбранное в интерфейсе (`picked`) читается ровно как выбрано и не
    обсуждается: оператор ткнул в конкретные документы, и подмешать к ним
    соседний протокол встречи — значит разложить на задачи не то, что он
    показал. Выбрать можно и комплект: аналитика редко живёт одним файлом, а
    требования без контракта API раскладываются в задачи, которых нет. Файла с
    таким именем в папке может не оказаться — папку сменили, файл удалили, — и
    это отказ с причиной, а не молчаливый возврат к чтению всей папки: тихо
    прочитать вместо выбранного документа что-то другое хуже, чем не прочитать
    ничего.

    Названный в запросе словами — то же самое, но мягче: имя ищется подстрокой,
    и ошибиться тут легко, поэтому не найденное имя просто не срабатывает.

    Ничего из этого — читаются все текстовые до общего потолка: выбирать за
    оператора нечем, а материал нужен целиком.
    """
    names = inputs.readable_files(task)
    if not names:
        return None

    wanted = inputs.picked_names(picked)
    if wanted:
        lost = [name for name in wanted if name not in names]
        if lost:
            many = len(lost) > 1
            return {
                "kind": "files",
                "error": (
                    f"{'выбраны файлы' if many else 'выбран файл'} "
                    + ", ".join(lost)
                    + f", но в файлах чата {'их' if many else 'его'} нет "
                    "(текстовых файлов там: " + (", ".join(names) or "нет") + ")"
                ),
            }
        chosen, named = wanted, True
    else:
        lowered = question.lower()
        found = [name for name in names if name.lower() in lowered]
        chosen, named = (found or names), bool(found)

    budget = Budget(cfg.input_max_chars())
    parts: list[str] = []
    read: list[str] = []
    skipped: list[str] = []
    # Тексты по именам — рядом со склеенным документом, а не вместо него.
    # Конвейеру, у которого вход это один документ, нужна склейка; тому, кто
    # сверяет документы между собой, нужно знать, в каком из них что написано,
    # а восстанавливать это разбором собственной же склейки значило бы держать
    # формат заголовка «## Файл» в двух местах.
    each: dict[str, str] = {}
    for name in chosen:
        if budget.exhausted:
            skipped.append(name)
            continue
        try:
            text = inputs.read(task, name, max_chars=budget.left)
        except (inputs.InputError, OSError) as exc:
            skipped.append(f"{name} ({exc})")
            continue
        parts.append(f"## Файл {name}\n\n{text}" if len(chosen) > 1 else text)
        each[name] = text
        read.append(name)
        budget.spend(text)

    if not read:
        return {"kind": "files", "error": "ни один файл чата не прочитан"}
    return {
        "kind": "files",
        "names": read,
        "skipped": skipped,
        "named": bool(named),
        "chosen": bool(picked),
        "each": each,
        "text": "\n\n".join(parts),
        "truncated": budget.overflowed,
    }

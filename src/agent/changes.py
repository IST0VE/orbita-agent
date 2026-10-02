"""
Изменение: единица работы, которая живёт дольше треда, и его требования с устойчивыми номерами.

Тред — это разговор, а изменение — то, о чём разговаривают неделями: «переход
на новый алгоритм паролей» собирает десятки прогонов, правок и людей. До этого
модуля каждый прогон подготовки задачи начинал с нуля и нумеровал требования
заново. Модель честно писала «R-1, R-2, R-3», а на следующем прогоне то же
требование становилось R-2, потому что выше появилось новое. Трассировка
«R-17 → задача → проверка» на таких номерах невозможна: номер ничего не
называет.

Поэтому номер требованию даёт код, а не модель:

  * модель видит требования изменения с их номерами и пишет знакомые под
    прежним номером, а новые — с меткой `R-?1`, по которой на них ссылаются
    задачи плана;
  * код сверяет написанное с сохранённым (`reconcile`). Тот же текст — тот же
    номер, даже если модель поставила другой. Прежний номер с похожей
    формулировкой — то же требование в новой редакции. Новое получает
    следующий номер, а номер выбывшего требования не занимается никогда;
  * документ переписывается с окончательными номерами (`rewrite`), и на
    страницу уезжают они, а не те, что предложила модель;
  * в базу тред пишет только то, что изменил относительно прочитанного из
    неё (`merge`): номер, выданный сверкой в треде, — ещё не номер в базе, а
    нетронутое требование могло за это время поменяться в соседнем треде.

Изменение принадлежит тому, кто его ведёт (`owner`), и связано с задачей Jira,
из которой выросло. Общим на команду оно пока не бывает намеренно: требования
пересказывают источники, прочитанные личным токеном, и общий список стал бы
каналом, по которому чужое прочитанное видит тот, кому Jira его не показала бы.

Хранение — Postgres (`db.py`, миграция 2). Сервер без базы работает дальше:
номера тогда устойчивы в пределах треда, а документ говорит, что изменение
не сохранено.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

import psycopg

from agent import config as cfg
from agent import db
from agent.evidence import ID as EVIDENCE_ID

#: Номер в строке требования: прежний `R-3`, новый `R-?` или новый с меткой
#: `R-?1`. По метке задачи плана ссылаются на новое требование, пока номера у
#: него нет: две ссылки `R-?` на два разных новых требования не различить.
_NUMBER = r"(\d+|\?\d*|new|NEW|новое)"
#: Строка требования: `- R-3: текст`, `**R-?1** — текст`, `1. R-new. текст`.
_LINE = re.compile(
    rf"^\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*)?R-{_NUMBER}(?:\*\*)?\s*[:.—–-]\s*(?:\*\*)?\s*(.+?)\s*$"
)
#: Строка таблицы: `| R-3 | текст | … |`.
_ROW = re.compile(rf"^\s*\|\s*(?:\*\*)?R-{_NUMBER}(?:\*\*)?\s*\|(.+)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TOKEN = re.compile(r"\bR-(\d+)\b")
#: Ссылка в остальном тексте: на номер `R-3` или на метку нового `R-?1`, `R-?`.
_REFERENCE = re.compile(r"\bR-(?:(\d+)\b|(\?\d*)(?!\d))")
_TAGS = re.compile(
    r"\[(?:EV-[^\]]*|JIRA[^\]]*|WIKI[^\]]*|ФАЙЛ[^\]]*|ВЫВОД|TBD)\]|\bEV-\w+", re.IGNORECASE
)

#: Тот же номер и хоть какая-то общая основа слов — та же мысль в новой
#: редакции. Модель, сохранившая номер, заявляет, что требование то же; код
#: не верит только номеру, отданному под совсем другой текст.
SAME = 0.25
#: Другой номер или без номера, но почти те же слова — то же требование.
MOVED = 0.6

ACTIVE = "active"
DROPPED = "dropped"


# --------------------------------------------------------------------------
# Разбор документа
# --------------------------------------------------------------------------
def parse(document: str) -> list[dict] | None:
    """
    Требования из раздела «Требования»: номер, текст и ссылки на Evidence.

    None — раздела нет. Это не то же, что пустой раздел: документ без раздела
    ничего не говорит о требованиях, и снимать по нему прежние требования
    нельзя. Строки вне раздела не читаются: «покрывает R-2» в критериях задачи
    — ссылка на требование, а не оно само.
    """
    lines = (document or "").split("\n")
    found: list[dict] | None = None
    level = 0
    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading:
            if found is not None and len(heading.group(1)) <= level:
                break
            if found is None and heading.group(2).strip().casefold().startswith("требован"):
                found, level = [], len(heading.group(1))
            continue
        if found is None:
            continue
        match = _LINE.match(line) or _ROW.match(line)
        if not match:
            continue
        text = match.group(2)
        if match.re is _ROW:
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", text) if cell.strip()]
            text = " — ".join(cells)
        text = text.strip().strip("*").strip()
        if not text:
            continue
        asked = match.group(1)
        found.append(
            {
                "asked": asked if asked.isdigit() else "?",
                # Метка нового требования, как её написала модель: `?1`, а
                # без метки — `?`. По ней переписываются ссылки (`rewrite`).
                "label": "" if asked.isdigit() else asked if asked.startswith("?") else "?",
                "text": text,
                "evidence": list(dict.fromkeys(m.group(0) for m in EVIDENCE_ID.finditer(text))),
                "line": index,
            }
        )
    return found


def _stems(text: str) -> set[str]:
    clean = _TAGS.sub(" ", (text or "").casefold().replace("ё", "е"))
    words = re.findall(r"\w+", clean)
    return {word[:5] for word in words if len(word) >= 3}


def fingerprint(text: str) -> str:
    """Отпечаток формулировки без ссылок, регистра и знаков: по нему «тот же текст»."""
    clean = _TAGS.sub(" ", (text or "").casefold().replace("ё", "е"))
    words = re.findall(r"\w+", clean)
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()[:16]


def similarity(first: str, second: str) -> float:
    """Доля общих основ слов: грубо, но одинаково на каждом прогоне и без модели."""
    a, b = _stems(first), _stems(second)
    return len(a & b) / len(a | b) if a | b else 0.0


# --------------------------------------------------------------------------
# Сверка номеров
# --------------------------------------------------------------------------
def reconcile(
    existing: Sequence[Mapping],
    proposed: Sequence[Mapping],
    *,
    seen: Iterable[int] | None = None,
) -> dict:
    """
    Окончательные номера для предложенного и новое состояние всех требований.

    existing — требования изменения: `number`, `text`, `fingerprint`, `status`,
    `revision`, `evidence`. proposed — разобранные `parse`: `asked`, `text`,
    `evidence`.

    seen — номера, о которых автор предложения высказывается. Требование, которого
    нет в предложении, выбывает, только если оно из них: требование,
    сохранённое соседним тредом после того, как этот прочитал изменение, не
    выбывает из-за того, что о нём здесь не знали (`untouched`). Похожая
    формулировка тоже не отнимает у соседа его требование: по сходству текст
    сверяется только с требованиями из seen, а с остальными — лишь по тому же
    тексту. None — видел всё.

    Порядок проходов — от надёжного к догадке: сначала тот же текст, потом тот
    же номер с похожим текстом, потом похожий текст под любым номером, и
    только потом новый номер. Каждый номер достаётся одному предложению.

    Возвращает `requirements` (все, со статусом и пометкой `change`: same,
    cited — тот же текст с другими ссылками на источники, changed, returned,
    new, dropped, absent, untouched), `assigned` (номер каждого предложения),
    `remap` (номера, которые модель назвала иначе, — для ссылок в остальном
    тексте) и `notes` — что код поправил и почему.
    """
    known = None if seen is None else {int(number) for number in seen}
    by_number = {int(item["number"]): item for item in existing}
    by_print: dict[str, Mapping] = {}
    for item in existing:
        by_print.setdefault(str(item.get("fingerprint") or fingerprint(item["text"])), item)
    prints = [fingerprint(item["text"]) for item in proposed]
    assigned: list[int | None] = [None] * len(proposed)
    kinds = [""] * len(proposed)
    taken: dict[int, int] = {}
    notes: list[str] = []

    def take(index: int, number: int, kind: str) -> None:
        assigned[index], kinds[index] = number, kind
        taken[number] = index

    def asked(index: int) -> int | None:
        value = str(proposed[index].get("asked") or "")
        return int(value) if value.isdigit() else None

    def open_(number: int) -> bool:
        """Номер свободен и его можно отдать по сходству формулировок."""
        return number not in taken and (known is None or number in known)

    for index in range(len(proposed)):
        item = by_print.get(prints[index])
        if item is None:
            continue
        number = int(item["number"])
        if number in taken:
            assigned[index], kinds[index] = number, "duplicate"
        else:
            take(index, number, "same")
    for index in range(len(proposed)):
        number = asked(index)
        if (
            assigned[index] is None
            and number in by_number
            and open_(number)
            and similarity(proposed[index]["text"], by_number[number]["text"]) >= SAME
        ):
            take(index, number, "changed")
    for index in range(len(proposed)):
        if assigned[index] is not None:
            continue
        scored = [
            (similarity(proposed[index]["text"], item["text"]), int(item["number"]))
            for item in existing
            if open_(int(item["number"]))
        ]
        best = max(scored, default=(0.0, 0))
        if best[0] >= MOVED:
            take(index, best[1], "changed")
    following = max([int(item["number"]) for item in existing] + [0]) + 1
    for index in range(len(proposed)):
        if assigned[index] is None:
            take(index, following, "new")
            following += 1

    counts: dict[int, int] = {}
    for index in range(len(proposed)):
        number = asked(index)
        if number is not None:
            counts[number] = counts.get(number, 0) + 1
    remap: dict[int, int] = {}
    for index in range(len(proposed)):
        number, final = asked(index), assigned[index]
        if kinds[index] == "duplicate":
            notes.append(f"R-{final} написано дважды: повтор оставлен под тем же номером")
        elif number is not None and number != final:
            reason = (
                "тот же текст уже был под этим номером"
                if kinds[index] == "same"
                else f"R-{number} у изменения — другое требование"
                if number in by_number
                else f"номера R-{number} у изменения не было"
            )
            notes.append(f"R-{number} → R-{final}: {reason}")
            if counts.get(number) == 1:
                remap[number] = final

    result: list[dict] = []
    for item in sorted(existing, key=lambda value: int(value["number"])):
        number = int(item["number"])
        base = {
            "number": number,
            "text": item["text"],
            "fingerprint": str(item.get("fingerprint") or fingerprint(item["text"])),
            "status": item.get("status") or ACTIVE,
            "revision": int(item.get("revision") or 1),
            "evidence": list(item.get("evidence") or []),
        }
        if number not in taken and known is not None and number not in known:
            result.append({**base, "change": "untouched"})
            continue
        if number not in taken:
            was = base["status"]
            result.append({**base, "status": DROPPED, "change": "dropped" if was == ACTIVE else "absent"})
            continue
        index = taken[number]
        edited = prints[index] != base["fingerprint"]
        returned = base["status"] != ACTIVE
        cited = list(proposed[index].get("evidence") or [])
        # Отпечаток не видит ссылок на источники: `[EV-a]` → `[EV-b]` при той
        # же формулировке — не новая редакция, но и не «то же»: запись в базе
        # должна узнать новый источник.
        recited = set(cited) != set(base["evidence"])
        result.append(
            {
                **base,
                "text": proposed[index]["text"],
                "fingerprint": prints[index],
                "status": ACTIVE,
                "revision": base["revision"] + (1 if edited else 0),
                "evidence": cited,
                "change": "changed" if edited
                else "returned" if returned
                else "cited" if recited
                else "same",
            }
        )
    for index, kind in enumerate(kinds):
        if kind == "new":
            result.append(
                {
                    "number": assigned[index],
                    "text": proposed[index]["text"],
                    "fingerprint": prints[index],
                    "status": ACTIVE,
                    "revision": 1,
                    "evidence": list(proposed[index].get("evidence") or []),
                    "change": "new",
                }
            )
    result.sort(key=lambda value: value["number"])
    return {"requirements": result, "assigned": assigned, "remap": remap, "notes": notes}


def rewrite(document: str, parsed: Sequence[Mapping], assigned: Sequence[int | None],
            remap: Mapping[int, int]) -> str:
    """
    Документ с окончательными номерами.

    Строка требования получает свой номер. В остальных строках ссылка
    переписывается, только если она однозначна: `R-5` — если модель назвала
    этим номером ровно одно требование, метка `R-?1` — если ею названо ровно
    одно новое, голое `R-?` — если новое без метки одно. При двух «R-5» или
    двух «R-?» неизвестно, на какое из них ссылка, и угадывать за читателя
    код не должен: ссылка остаётся как есть и видна на странице.
    """
    lines = (document or "").split("\n")
    own = {int(item["line"]): number for item, number in zip(parsed, assigned, strict=True)}
    for index, number in own.items():
        if number is None:
            continue
        lines[index] = re.sub(rf"R-{_NUMBER}", f"R-{number}", lines[index], count=1)
    written: dict[str, int] = {}
    for item in parsed:
        if item.get("label"):
            written[item["label"]] = written.get(item["label"], 0) + 1
    labels = {
        item["label"]: number
        for item, number in zip(parsed, assigned, strict=True)
        if item.get("label") and written[item["label"]] == 1 and number is not None
    }
    if not remap and not labels:
        return "\n".join(lines)

    def final(match: re.Match) -> str:
        if match.group(1):
            number = int(match.group(1))
            return f"R-{remap.get(number, number)}"
        label = match.group(2)
        return f"R-{labels[label]}" if label in labels else match.group(0)

    for index, line in enumerate(lines):
        if index not in own:
            lines[index] = _REFERENCE.sub(final, line)
    return "\n".join(lines)


def renumber(document: str, remap: Mapping[int, int]) -> str:
    """Ссылки `R-n` во всём документе по карте номеров: после сверки с базой."""
    if not remap:
        return document
    return _TOKEN.sub(
        lambda match: f"R-{remap.get(int(match.group(1)), int(match.group(1)))}", document or ""
    )


def _unchanged(item: Mapping, was: Mapping) -> bool:
    """Та же формулировка, тот же статус и те же источники, что в снимке базы."""
    return (
        str(item.get("fingerprint") or fingerprint(item["text"]))
        == str(was.get("fingerprint") or fingerprint(was["text"]))
        and (item.get("status") or ACTIVE) == (was.get("status") or ACTIVE)
        and set(item.get("evidence") or []) == set(was.get("evidence") or [])
    )


def merge(
    existing: Sequence[Mapping],
    requirements: Iterable[Mapping],
    committed: Iterable[Mapping] = (),
) -> dict:
    """
    Итог прогона против того, что лежит в базе сейчас: номера, окончательные для базы.

    existing — требования изменения в базе, прочитанные под блокировкой.
    requirements — итог прогона из состояния треда. committed — снимок базы,
    от которого шёл тред: что он прочитал из неё или записал в неё последним.

    В базу уходит только то, что тред изменил относительно снимка. Требование,
    которого он не трогал, не отправляется вовсе: пока тред думал, соседний
    мог его переформулировать или снять, и прежний текст из этого треда
    откатил бы чужую правку.

    Номер, которого в снимке нет, дан сверкой в треде, а не базой, и соседний
    тред мог за это время отдать тот же номер своему требованию. Такой номер —
    не заявка на номер в базе: требование сверяется как новое (`R-?`), а его
    номер в документах треда переписывается на выданный базой (`remap`).
    Результат — тот же, что у `reconcile`.
    """
    before = {int(item["number"]): item for item in committed}
    proposed: list[dict] = []
    local: list[int | None] = []
    touched: list[int] = []
    for item in requirements:
        number = int(item["number"])
        active = (item.get("status") or ACTIVE) == ACTIVE
        was = before.get(number)
        if was is not None:
            if _unchanged(item, was):
                continue
            touched.append(number)
        if active:
            proposed.append(
                {"asked": "?" if was is None else str(number), "text": item["text"],
                 "evidence": list(item.get("evidence") or [])}
            )
            local.append(number if was is None else None)
    settled = reconcile(existing, proposed, seen=touched)
    for number, final in zip(local, settled["assigned"], strict=True):
        if number is not None and final != number:
            settled["remap"][number] = final
            settled["notes"].append(
                f"R-{number} → R-{final}: номер был дан в этом чате, база выдала свой"
            )
    return settled


# --------------------------------------------------------------------------
# Что видит роль
# --------------------------------------------------------------------------
#: Как роль пишет новое требование. Метка, а не общий `R-?`: задачи плана
#: ссылаются на требования, и по двум одинаковым `R-?` код не поймёт, какой
#: номер подставить в ссылку (`rewrite`).
NEW_RULE = (
    "Новое требование пиши с меткой `R-?1`, `R-?2` и так далее и в задачах "
    "ссылайся на него той же меткой — номер вместо метки поставит код."
)


def block(change: Mapping | None) -> str:
    """Требования изменения для брифа роли, которая их пишет."""
    change = change or {}
    items = list(change.get("requirements") or [])
    active = [item for item in items if item.get("status") == ACTIVE]
    dropped = [item for item in items if item.get("status") != ACTIVE]
    if change.get("id"):
        head = f"Изменение {change['id']}" + (f" по задаче {change['key']}" if change.get("key") else "")
    elif change.get("key"):
        head = f"Изменение по задаче {change['key']} (ещё не сохранено)"
    else:
        head = "Изменение не заведено: задача не прочитана"
    if not active and not dropped:
        return f"{head}. Требований у него ещё нет. {NEW_RULE}"
    parts = [f"{head}. Требования с прошлых прогонов — номера сохраняй, даже если меняешь "
             f"формулировку. {NEW_RULE}"]
    parts += [f"- R-{item['number']}: {item['text']}" for item in active]
    if dropped:
        parts.append(
            "Выбыли из последней версии (их номера не занимай): "
            + ", ".join(f"R-{item['number']}" for item in dropped)
            + "."
        )
    return "\n".join(parts)


def describe(change: Mapping | None) -> str:
    """Одна строка об изменении для страницы: номер, требования и сохранено ли."""
    change = change or {}
    if not change.get("key"):
        return ""
    items = list(change.get("requirements") or [])
    active = [item for item in items if item.get("status") == ACTIVE]
    new = [item for item in items if item.get("change") == "new"]
    edited = [item for item in items if item.get("change") in ("changed", "returned")]
    cited = [item for item in items if item.get("change") == "cited"]
    gone = [item for item in items if item.get("change") == "dropped"]
    name = f"Изменение {change['id']}" if change.get("id") else f"Изменение по {change['key']}"
    parts = [f"требований {len(active)}"]
    if new:
        parts.append("новые " + ", ".join(f"R-{item['number']}" for item in new))
    if edited:
        parts.append("переформулированы " + ", ".join(f"R-{item['number']}" for item in edited))
    if cited:
        parts.append("сменились источники " + ", ".join(f"R-{item['number']}" for item in cited))
    if gone:
        parts.append("выбыли " + ", ".join(f"R-{item['number']}" for item in gone))
    if change.get("stored"):
        state = "сохранено"
    elif change.get("error"):
        state = f"не сохранено: {change['error']}"
    else:
        state = "номера устойчивы в пределах этого чата"
    return f"{name}: " + "; ".join(parts) + f" — {state}."


# --------------------------------------------------------------------------
# Хранение
# --------------------------------------------------------------------------
def owner() -> str:
    """Кто ведёт изменение: пользователь прогона, а без входа — админ-токен."""
    from agent import credentials, security

    return credentials.current_subject() or security.SERVICE_SUBJECT


#: Сколько секунд не стучаться в базу после отказа. Прелюдия и постлюдия
#: спрашивают её на каждом ходе, и лежащая база стоила бы каждому прогону
#: двух таймаутов соединения.
_RETRY_S = 60.0
_down: dict[str, float] = {}


def available() -> None:
    """
    Можно ли сейчас идти в базу. Нельзя — `db.DatabaseUnavailable` с причиной.

    База не заведена (`POSTGRES_URI` не задан) — изменение живёт в треде, и
    это не ошибка, а обычный режим разработки и тестов.
    """
    if not cfg.postgres_configured():
        raise db.DatabaseUnavailable("база не настроена (POSTGRES_URI не задан)")
    reason = _down.get("reason")
    if reason and time.monotonic() < _down.get("until", 0.0):
        raise db.DatabaseUnavailable(str(reason))


def failed(exc: Exception) -> None:
    """Запомнить отказ базы на `_RETRY_S` секунд."""
    _down.update({"reason": str(exc), "until": time.monotonic() + _RETRY_S})


def _call(what: str, action):
    """
    Обращение к хранилищу с отказом одного вида.

    Прогон не должен падать из-за базы: изменение — приложение к документу,
    а не его условие. Поэтому любая ошибка psycopg становится
    `db.DatabaseUnavailable` с человеческой причиной, а недоступность ещё и
    запоминается, чтобы следующий ход не ждал таймаута заново.
    """
    available()
    try:
        return action()
    except db.DatabaseUnavailable as exc:
        failed(exc)
        raise
    except psycopg.Error as exc:
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        raise db.DatabaseUnavailable(f"{what}: {first}") from exc


def load(owner_: str, key: str) -> dict | None:
    """Изменение этого человека по задаче и его требования. None — его ещё нет."""
    return _call("изменение не прочитано", lambda: store.find(owner_, key))


def save(**values) -> dict:
    """Итог прогона в базу (`PostgresChanges.save`)."""
    return _call("изменение не сохранено", lambda: store.save(**values))


def listing(owner_: str) -> list[dict]:
    """Изменения человека, свежие первыми: для страницы и API."""
    return _call("изменения не прочитаны", lambda: store.list(owner_))


def one(owner_: str, change_id: str) -> dict | None:
    """Изменение целиком: требования, их история, Evidence и треды. Чужое — None."""
    return _call("изменение не прочитано", lambda: store.get(owner_, change_id))


def _new_id() -> str:
    return "CH-" + secrets.token_hex(4)


_REQUIREMENT_COLUMNS = "number, text, fingerprint, status, revision, evidence"


def _requirements(conn, change_id: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_REQUIREMENT_COLUMNS} FROM requirements WHERE change_id = %s ORDER BY number",
        (change_id,),
    ).fetchall()
    return [
        {
            "number": number,
            "text": text,
            "fingerprint": print_,
            "status": status,
            "revision": revision,
            "evidence": list(evidence or []),
        }
        for number, text, print_, status, revision, evidence in rows
    ]


class PostgresChanges:
    """Строки `changes`, `requirements` и их спутников. Тесты подменяют `store`."""

    def find(self, owner: str, key: str) -> dict | None:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT id, key, title FROM changes WHERE owner = %s AND key = %s", (owner, key)
            ).fetchone()
            if row is None:
                return None
            return {"id": row[0], "key": row[1], "title": row[2],
                    "requirements": _requirements(conn, row[0])}

    def save(
        self,
        *,
        owner: str,
        key: str,
        title: str,
        thread_id: str,
        graph: str,
        requirements: Iterable[Mapping] | None,
        evidence: Iterable[Mapping],
        committed: Iterable[Mapping] = (),
    ) -> dict:
        """
        Записать итог прогона одной транзакцией: изменение, требования, Evidence, тред.

        Требования сверяются заново, уже с тем, что лежит в базе под блокировкой
        строки изменения: соседний тред мог сохранить своё между чтением и
        записью, и тогда номер, выданный этим прогоном, мог оказаться занят.
        requirements=None — документ не говорил о требованиях, и они не трогаются.
        committed — снимок базы, от которого шёл тред: пишется только то, что
        он изменил относительно снимка (`merge`).
        """
        with db.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO changes (id, owner, key, title) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (owner, key) DO UPDATE SET title = EXCLUDED.title, updated_at = now()",
                (_new_id(), owner, key, title),
            )
            change_id = conn.execute(
                "SELECT id FROM changes WHERE owner = %s AND key = %s FOR UPDATE", (owner, key)
            ).fetchone()[0]
            existing = _requirements(conn, change_id)
            settled = {"requirements": existing, "remap": {}, "notes": []}
            if requirements is not None:
                settled = merge(existing, requirements, committed)
                for item in settled["requirements"]:
                    if item["change"] in ("same", "absent", "untouched"):
                        continue
                    conn.execute(
                        "INSERT INTO requirements (change_id, number, text, fingerprint, status,"
                        " revision, evidence, thread_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (change_id, number) DO UPDATE SET text = EXCLUDED.text,"
                        " fingerprint = EXCLUDED.fingerprint, status = EXCLUDED.status,"
                        " revision = EXCLUDED.revision, evidence = EXCLUDED.evidence,"
                        " thread_id = EXCLUDED.thread_id, updated_at = now()",
                        (change_id, item["number"], item["text"], item["fingerprint"],
                         item["status"], item["revision"], item["evidence"], thread_id),
                    )
                    if item["change"] == "cited":
                        # Ссылки сменились, редакция — нет: история ведёт
                        # формулировки, и строка с той же редакцией в ней лишняя.
                        continue
                    conn.execute(
                        "INSERT INTO requirement_history (change_id, number, revision, text,"
                        " status, thread_id) VALUES (%s, %s, %s, %s, %s, %s)",
                        (change_id, item["number"], item["revision"], item["text"],
                         item["status"], thread_id),
                    )
            for item in evidence:
                conn.execute(
                    "INSERT INTO evidence (change_id, id, system, source_id, title, url, version,"
                    " location, content_hash, reader, own, fetched_at, thread_id)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (change_id, id) DO UPDATE SET location = EXCLUDED.location,"
                    " content_hash = EXCLUDED.content_hash, reader = EXCLUDED.reader,"
                    " fetched_at = EXCLUDED.fetched_at, thread_id = EXCLUDED.thread_id",
                    (change_id, item["id"], item["system"], item["source_id"], item["title"],
                     item["url"], item["version"], item["location"], item["hash"],
                     item["reader"], bool(item.get("own")),
                     datetime.fromisoformat(item["fetched_at"]), thread_id),
                )
            conn.execute(
                "INSERT INTO change_threads (change_id, thread_id, graph) VALUES (%s, %s, %s)"
                " ON CONFLICT (change_id, thread_id) DO UPDATE SET last_at = now()",
                (change_id, thread_id, graph),
            )
        return {"id": change_id, **settled}

    def list(self, owner: str) -> list[dict]:
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT c.id, c.key, c.title, c.created_at, c.updated_at,"
                " (SELECT count(*) FROM requirements r WHERE r.change_id = c.id AND r.status = 'active'),"
                " (SELECT count(*) FROM change_threads t WHERE t.change_id = c.id),"
                " (SELECT count(*) FROM evidence e WHERE e.change_id = c.id)"
                " FROM changes c WHERE c.owner = %s ORDER BY c.updated_at DESC",
                (owner,),
            ).fetchall()
        return [
            {"id": id_, "key": key, "title": title, "created_at": created.isoformat(),
             "updated_at": updated.isoformat(), "requirements": reqs, "threads": threads,
             "evidence": evidence}
            for id_, key, title, created, updated, reqs, threads, evidence in rows
        ]

    def get(self, owner: str, change_id: str) -> dict | None:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT id, key, title, created_at, updated_at FROM changes"
                " WHERE owner = %s AND id = %s",
                (owner, change_id),
            ).fetchone()
            if row is None:
                return None
            history = conn.execute(
                "SELECT number, revision, text, status, thread_id, saved_at FROM requirement_history"
                " WHERE change_id = %s ORDER BY number, saved_at",
                (change_id,),
            ).fetchall()
            evidence = conn.execute(
                "SELECT id, system, source_id, title, url, version, location, content_hash,"
                " reader, own, fetched_at, thread_id FROM evidence WHERE change_id = %s"
                " ORDER BY fetched_at",
                (change_id,),
            ).fetchall()
            threads = conn.execute(
                "SELECT thread_id, graph, first_at, last_at FROM change_threads"
                " WHERE change_id = %s ORDER BY first_at",
                (change_id,),
            ).fetchall()
            requirements = _requirements(conn, change_id)
        return {
            "id": row[0],
            "key": row[1],
            "title": row[2],
            "created_at": row[3].isoformat(),
            "updated_at": row[4].isoformat(),
            "requirements": requirements,
            "history": [
                {"number": n, "revision": r, "text": t, "status": s, "thread_id": th,
                 "saved_at": saved.isoformat()}
                for n, r, t, s, th, saved in history
            ],
            "evidence": [
                {"id": e[0], "system": e[1], "source_id": e[2], "title": e[3], "url": e[4],
                 "version": e[5], "location": e[6], "hash": e[7], "reader": e[8],
                 "own": e[9], "fetched_at": e[10].isoformat(), "thread_id": e[11]}
                for e in evidence
            ],
            "threads": [
                {"thread_id": t, "graph": g, "first_at": f.isoformat(), "last_at": last.isoformat()}
                for t, g, f, last in threads
            ],
        }


store = PostgresChanges()

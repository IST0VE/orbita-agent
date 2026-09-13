"""
Поиск секретов в изменениях, в дереве и в истории репозитория.

Правила распознавания не заводятся здесь заново: они живут в `agent.outgoing`
и уже применяются к исходящим документам, страницам и задачам Jira. Второй
набор шаблонов разошёлся бы с первым в первую же правку, и разошёлся бы тихо —
проверка публикации ловила бы одно, проверка репозитория другое.

Три режима, и они отвечают на разные вопросы:

    changed    что добавляется этим изменением. Быстро, на каждый push и PR;
    tree       что лежит в рабочем дереве сейчас. Ловит файл, добавленный
               мимо проверки изменений — например, слитый из другой ветки;
    history    что когда-либо попадало в объекты репозитория. Медленно,
               поэтому по расписанию, а не на каждый коммит: обезличивание
               файла не удаляет его прежние версии.

Значения находок не печатаются никогда. Сообщение об утечке, в котором лежит
утёкший ключ, — это второй экземпляр того же секрета, теперь в логах CI,
доступных шире, чем исходный файл.

Исключения перечислены поимённо и с причиной. «Исключить каталог тестов»
означало бы, что тесты — место, куда секрет можно положить незаметно.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent import outgoing  # noqa: E402

#: Файлы, которым разрешено содержать распознаваемые синтетические секреты,
#: и причина для каждого. Каталогов здесь нет намеренно.
ALLOWED: dict[str, str] = {
    "tests/test_outgoing.py": "проверки самой маскировки: синтетические ключи — их предмет",
    "tests/fixtures/planted_secret.txt": "контрольная закладка: на ней проверяется сам сканер",
    "scripts/scan_secrets.py": "перечень исключений и пояснения к ним",
}

#: Что не читаем вовсе: двоичное, сборки и чужие зависимости.
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "dist", "build", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".tmp", ".langgraph_api", ".nt-runs", ".nt-tools",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff",
    ".woff2", ".ttf", ".eot", ".mp4", ".sqlite3", ".db", ".lock",
}

#: Закрытые корпоративные домены и идентификаторы. Пусто: публичного списка нет.
#: Появится — правила добавляются сюда, и проверка начинает их искать наравне
#: с ключами. Формат — регулярное выражение и имя находки.
PROJECT_RULES: tuple[tuple[str, str], ...] = ()

#: Хосты, зарезервированные для документации и локальной работы (RFC 2606 и
#: петля), плюс имена сервисов docker compose этого репозитория. Логин с
#: паролем в таком адресе — это пример в документации или значение по умолчанию
#: локальной сборки, а не утёкшие реквизиты. Сюда же словесные заглушки
#: (`host`, `hostname`) из примеров в документации. Список именной: «любое имя
#: без точки» означало бы, что реквизиты к внутреннему хосту проверять не нужно.
DOC_HOSTS = re.compile(
    r"(?i)@(?:localhost|127\.0\.0\.1|\[::1\]"
    r"|(?:[a-z0-9-]+\.)*(?:example\.(?:com|org|net)|example|invalid|test|localhost)"
    r"|postgres|host|hostname)"
    r"(?![a-z0-9.-])"
)

#: Значение, которое само объявляет себя тестовым. Применяется только к самому
#: широкому правилу — `bearer-token`: оно срабатывает на любой длинной строке
#: после слова Bearer, и без этого фильтра проверка тонет в собственных тестах.
#: Узкие правила (ключи с опознаваемым префиксом, блок приватного ключа) этот
#: фильтр не проходят никогда: у них ложных срабатываний и так нет.
SYNTHETIC = re.compile(r"(?i)(?:test|example|placeholder|dummy|changeme|sample|fake)|-")
BROAD_KINDS = {"bearer-token"}

MAX_BYTES = 2_000_000


class Finding:
    """Где нашли и что именно. Значения нет — оно и не печатается."""

    def __init__(self, where: str, line: int, kind: str) -> None:
        self.where, self.line, self.kind = where, line, kind

    def __str__(self) -> str:
        return f"{self.where}:{self.line}: {self.kind}"


def _rules():
    extra = tuple((name, re.compile(pattern)) for name, pattern in PROJECT_RULES)
    return (*outgoing.MASKED, *outgoing.BLOCKED, *extra)


def _real(kind: str, value: str, line: str) -> bool:
    """
    Находка ли это или заведомо безобидное значение.

    Адрес с логином проверяется по строке целиком: само правило заканчивается
    на «@» и хоста в совпадении не содержит, а решает именно хост.
    """
    if kind == "url-credentials":
        return not DOC_HOSTS.search(line)
    if kind in BROAD_KINDS:
        return not SYNTHETIC.search(value.split(None, 1)[-1])
    return True


def scan_text(text: str, where: str) -> list[Finding]:
    """Находки в одном тексте, с номерами строк и без значений."""
    found: list[Finding] = []
    rules = _rules()
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in rules:
            match = pattern.search(line)
            if match and _real(kind, match.group(0), line):
                found.append(Finding(where, number, kind))
    # Блок приватного ключа занимает много строк и по одной не находится.
    for kind, pattern in rules:
        if pattern.flags & re.DOTALL and pattern.search(text):
            if not any(item.kind == kind for item in found):
                found.append(Finding(where, 1, kind))
    return found


def _readable(path: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    try:
        return path.stat().st_size <= MAX_BYTES
    except OSError:
        return False


def _text_of(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def scan_paths(paths: list[str]) -> list[Finding]:
    found: list[Finding] = []
    for name in paths:
        if name in ALLOWED:
            continue
        path = ROOT / name
        if not path.is_file() or not _readable(path):
            continue
        found += scan_text(_text_of(path), name)
    return found


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    return [
        name
        for name in out.stdout.splitlines()
        if name and not any(part in SKIP_DIRS for part in Path(name).parts)
    ]


def changed_files(base: str) -> list[str]:
    """Файлы, изменившиеся относительно базы. Удалённые не читаем."""
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", base, "--"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if out.returncode:
        # Базы нет (мелкий клон, первый коммит) — проверяем дерево целиком.
        return tracked_files()
    return [name for name in out.stdout.splitlines() if name]


def history_blobs() -> list[tuple[str, str]]:
    """Все объекты репозитория: (идентификатор, путь). Прежние версии тоже."""
    listing = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    rows = []
    for line in listing.stdout.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1]:
            rows.append((parts[0], parts[1]))
    return rows


def scan_history() -> list[Finding]:
    found: list[Finding] = []
    for object_id, name in history_blobs():
        if name in ALLOWED or Path(name).suffix.lower() in SKIP_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in Path(name).parts):
            continue
        blob = subprocess.run(
            ["git", "cat-file", "-p", object_id],
            cwd=ROOT, capture_output=True, encoding=None,
        )
        if blob.returncode or len(blob.stdout) > MAX_BYTES:
            continue
        text = blob.stdout.decode("utf-8", "replace")
        found += scan_text(text, f"{name}@{object_id[:10]}")
    return found


def self_test() -> int:
    """Сканер обязан находить контрольную закладку. Иначе он ничего не проверяет."""
    planted = ROOT / "tests" / "fixtures" / "planted_secret.txt"
    if not planted.is_file():
        print("самопроверка: нет контрольной закладки tests/fixtures/planted_secret.txt")
        return 1
    found = scan_text(_text_of(planted), "tests/fixtures/planted_secret.txt")
    kinds = sorted({item.kind for item in found})
    if not kinds:
        print("самопроверка: закладка не найдена — правила распознавания не работают")
        return 1
    print("самопроверка пройдена, распознано: " + ", ".join(kinds))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("changed", "tree", "history"), default="changed")
    parser.add_argument("--base", default="origin/master", help="база для режима changed")
    parser.add_argument("--self-test", action="store_true", help="проверить сам сканер")
    args = parser.parse_args()

    if args.self_test and self_test():
        return 1

    if args.mode == "changed":
        found = scan_paths(changed_files(args.base))
    elif args.mode == "tree":
        found = scan_paths(tracked_files())
    else:
        found = scan_history()

    if not found:
        print(f"секретов не найдено ({args.mode})")
        return 0

    print(f"найдено возможных секретов: {len(found)} ({args.mode})")
    for item in sorted({str(item) for item in found}):
        print("  " + item)
    print(
        "\nЗначения не выводятся намеренно. Если находка — намеренная тестовая "
        "фикстура, добавьте её путь и причину в ALLOWED в scripts/scan_secrets.py."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

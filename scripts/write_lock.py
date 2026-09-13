"""
Пересобрать `requirements.lock` из отчёта pip.

Обновление зависимостей должно быть явным и проверяемым, поэтому шага два и
оба видны в истории: сначала разрешение, потом запись.

    python -m pip install --dry-run --ignore-installed --report report.json ./packages/costmeter ".[server,postgres]"
    python scripts/write_lock.py report.json

Локальные пакеты репозитория в файл не попадают: они ставятся из исходников
рядом, и версия у них одна — та, что лежит в рабочей копии.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

HEADER = """# Зафиксированный набор runtime-зависимостей приложения.
#
# Зачем он есть. `pyproject.toml` описывает совместимые диапазоны, и это верно
# для библиотеки: она должна уживаться с чужими наборами. Приложению нужно
# обратное — чтобы установка сегодня и через месяц дала одно и то же. Без этого
# файла разрешение зависимостей приводило к уязвимой версии httpx2 у каждого,
# кто ставил проект заново, и увидеть это можно было только аудитом.
#
# Что здесь закреплено: версии, а не файлы колёс. Отпечатки колёс зависят от
# платформы и версии Python, а проект собирается и на Linux в образе, и на
# машине разработчика; набор версий одинаков везде, набор файлов — нет.
#
# Чем пользуются: Dockerfile и работа CI `install from the lock`. Разработка
# по-прежнему ставится редактируемой (`requirements.txt`), а этот файл проверяет
# CI на чистой установке.
#
# Как обновлять — явно и проверяемо, одной командой:
#
#     python -m pip install --dry-run --ignore-installed --report report.json ./packages/costmeter ".[server,postgres]"
#     python scripts/write_lock.py report.json
#
# и затем `pip-audit`. Локальные пакеты репозитория (orbita-agent,
# orbita-costmeter) сюда не попадают: они ставятся из исходников рядом.
#
# Снято: {captured}. Набор: {extras}.
"""


def pinned(report: dict) -> list[tuple[str, str]]:
    rows = []
    for item in report.get("install") or []:
        meta = item.get("metadata") or {}
        url = (item.get("download_info") or {}).get("url", "")
        if url.startswith("file:"):
            continue  # локальный пакет репозитория
        rows.append((str(meta["name"]).lower().replace("_", "-"), str(meta["version"])))
    return sorted(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="файл отчёта pip install --report")
    parser.add_argument("--out", type=Path, default=Path("requirements.lock"))
    parser.add_argument("--extras", default=".[server,postgres]")
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    rows = pinned(report)
    if not rows:
        raise SystemExit("отчёт не содержит устанавливаемых пакетов")
    header = HEADER.format(captured=datetime.date.today().isoformat(), extras=args.extras)
    body = "\n".join(f"{name}=={version}" for name, version in rows)
    args.out.write_text(header + "\n" + body + "\n", encoding="utf-8")
    print(f"{args.out}: зафиксировано пакетов {len(rows)}")


if __name__ == "__main__":
    main()

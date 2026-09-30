"""
Собрать доски Jira в метрики потока — из командной строки или из cron.

Тот же сбор, что делает сервер в фоне по FLOW_BOARDS и граф «Метрики потока»
по запросу (`flow_sync.sync`): история задач доски читается токеном из `.env`
и пишется в базу Orbita (POSTGRES_URI). Нужен там, где сервер не держат
запущенным постоянно, и для первого сбора за длинное окно, который удобнее
запустить руками и дождаться.

    python scripts/flow_sync.py 42              # доска 42: первый раз — окно FLOW_HISTORY_DAYS
    python scripts/flow_sync.py 42 57           # несколько досок подряд
    python scripts/flow_sync.py 42 --full       # перечитать окно целиком
    python scripts/flow_sync.py 42 --days 365   # окно глубже, чем FLOW_HISTORY_DAYS
    python scripts/flow_sync.py --scheduled     # доски из FLOW_BOARDS
    python scripts/flow_sync.py 42 --report 90  # и показатели за 90 дней после сбора

Каждый запрос к Jira ждёт паузу ATLASSIAN_REQUEST_INTERVAL_S, поэтому первый
сбор доски на тысячу задач идёт минуты: это защита шлюза перед трекером, а
не медлительность скрипта.
"""

from __future__ import annotations

import argparse
import json
import sys

from agent import config as cfg
from agent import db, flow, flow_sync, jira


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сбор досок Jira в метрики потока Orbita")
    parser.add_argument("boards", nargs="*", type=int, help="номера досок")
    parser.add_argument("--scheduled", action="store_true", help="доски из FLOW_BOARDS")
    parser.add_argument("--full", action="store_true", help="перечитать окно целиком")
    parser.add_argument("--days", type=int, help="глубина окна, дней")
    parser.add_argument("--report", type=int, metavar="DAYS",
                        help="после сбора напечатать показатели за столько дней")
    args = parser.parse_args(argv)

    boards = list(args.boards) + (list(cfg.flow_boards()) if args.scheduled else [])
    if not boards:
        parser.error("назовите доску или --scheduled (FLOW_BOARDS в .env)")

    failed = 0
    for board_id in dict.fromkeys(boards):
        try:
            result = flow_sync.sync(board_id, who="script", days=args.days, full=args.full)
        except (flow_sync.FlowBusy, jira.JiraError, db.DatabaseUnavailable) as exc:
            print(f"доска {board_id}: не собрана — {exc}", file=sys.stderr)
            failed += 1
            continue
        print(json.dumps(result, ensure_ascii=False))
        if args.report:
            measured = flow_sync.measure(board_id, args.report, refresh=False)
            print(flow.render(measured.board, measured.current, measured.previous,
                              notes=measured.notes))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

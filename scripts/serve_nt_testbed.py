"""Backend Orbita на координатах стенда nt-testbed.

Портал работает как обычно — те же графы, тот же UI engine, тот же
`langgraph dev`, — но Jira, Confluence, Kubernetes, система НТ и источники
метрик подменены моками стенда:

    python scripts/serve_nt_testbed.py
    python scripts/serve_nt_testbed.py --port 2024 --host 127.0.0.1

Значения `.env.nt-testbed` кладутся поверх `.env` в памяти процесса: файла со
смешанными секретами на диске не появляется, боевые Jira и Confluence в прогоне
не участвуют. Фронт поднимается отдельно: `npm --prefix web run dev`.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = ROOT / ".env"
TESTBED_ENV = ROOT / ".env.nt-testbed"
CONFIG = ROOT / "langgraph.json"


# Весь стенд живёт на localhost, а на рабочей машине может быть настроен
# системный HTTP-прокси. `requests` берёт его из реестра Windows и переменных
# окружения, и тогда запрос к моку уходит в прокси: тот отвечает 502 или
# молчит до таймаута. Для графа это выглядит как недоступный источник —
# precheck не собирает параметры и прогон уходит сразу в отчёт. Список
# исключений наследуется, локальные адреса дописываются к нему.
LOOPBACK = ("localhost", "127.0.0.1", "::1")


def values(path: Path) -> dict[str, str]:
    if not path.exists():
        sys.exit(f"нет файла {path.relative_to(ROOT)}")
    return {name: value for name, value in dotenv_values(path).items() if value is not None}


def no_proxy(env: dict[str, str]) -> dict[str, str]:
    """Список хостов в обход прокси: унаследованный плюс адреса стенда."""
    inherited = env.get("NO_PROXY") or env.get("no_proxy") or os.environ.get("NO_PROXY") or ""
    hosts = [host.strip() for host in inherited.split(",") if host.strip()]
    hosts += [host for host in LOOPBACK if host not in hosts]
    value = ",".join(hosts)
    # requests читает переменную в обоих регистрах, и выигрывает первая найденная.
    return {"NO_PROXY": value, "no_proxy": value}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2024)
    args = parser.parse_args()

    # Провайдер модели, учёт стоимости и публикация берутся из .env, координаты
    # источников — из .env.nt-testbed. Порядок слияния и решает, что победит.
    env = values(BASE_ENV) | values(TESTBED_ENV)
    env |= no_proxy(env)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))

    # Пути графов в langgraph.json относительные, их резолвит рабочий каталог.
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    from langgraph_api.cli import run_server

    print(f"стенд nt-testbed: Prometheus {env.get('NT_PROMETHEUS_URL')}, "
          f"НТ {env.get('NT_LOAD_TESTING_URL')}, Jira {env.get('JIRA_BASE_URL')}")
    print(f"в обход прокси: {env['NO_PROXY']}")
    run_server(
        host=args.host,
        port=args.port,
        reload=False,
        graphs=config["graphs"],
        env=env,
        http=config.get("http"),
        allow_blocking=True,
        open_browser=False,
    )


if __name__ == "__main__":
    main()

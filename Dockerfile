# Образ с агентом и сервером LangGraph на порту 2024.
#
# README обещал requirements.txt «для CI и Docker» — вот вторая половина
# обещания. Смысл образа не в развёртывании на проде, а в том, чтобы вопрос
# «как это запустить не на моей машине» закрывался одной командой:
#
#     docker compose up
#
# База инструментов и опубликованные документы лежат на томе (/data), поэтому
# пересоздание контейнера не стирает ни аккаунты, ни собранные страницы.
# k6 для runner нагрузочного тестирования (сервис `runner` в Compose). Версия —
# та же, что проверена с генератором сценариев; бинарник статический и
# переносится в образ на Debian как есть.
FROM grafana/k6:1.6.1 AS k6

FROM python:3.12-slim

# PYTHONUNBUFFERED — чтобы логи сервера появлялись в `docker compose logs`
# сразу, а не когда буфер сочтёт нужным.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала зафиксированный набор, потом сами пакеты без повторного разрешения:
# иначе установка образа сегодня и через месяц даёт разные версии, и уязвимая
# транзитивная зависимость приезжает молча. Обновляется файл явно —
# scripts/write_lock.py, см. комментарий в самом requirements.lock.
#
# Слой с зависимостями зависит только от requirements.lock и переживает
# правки кода: пересборка после изменения src/ или README занимает секунды,
# а не две минуты переустановки всего набора. Поэтому lock копируется и
# ставится отдельно, до остального кода.
COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY pyproject.toml README.md LICENSE ./
COPY packages ./packages
COPY src ./src
RUN pip install --no-deps ./packages/costmeter \
    && pip install --no-deps "."

COPY langgraph.json run_demo.py ./
COPY .env.example ./
# Runner НТ — тот же код агента с отдельной точкой входа; k6 он запускает сам.
COPY scripts/serve_nt_runner.py ./scripts/
COPY --from=k6 /usr/bin/k6 /usr/local/bin/k6

# Данные — на томах. Пути в образе остаются значением по умолчанию, но решает
# их не образ: `env_file` в Compose перекрывает ENV, поэтому в docker-compose.yml
# те же пути заданы через `environment`, где их не перебьёт личный .env.
ENV PUBLISH_DIR=/data/published \
    AGENT_INPUT_DIR=/data/input \
    JIRA_JOURNAL_PATH=/data/jira-operations.sqlite3
# `.langgraph_api` — собственное хранилище тредов сервера разработки,
# `/data/nt-runs` — журнал и файлы прогонов runner. Каталоги создаются здесь,
# чтобы тома монтировались на готовое место с нужным владельцем.
RUN mkdir -p /data/published /data/input /data/nt-runs /app/.langgraph_api

# Даже учебный сервер не должен выполнять разбор пользовательских файлов и
# HTTP-запросы от root. /app остаётся доступен на запись из-за runtime-файлов
# `langgraph dev`; постоянные данные живут отдельно в /data.
RUN addgroup --system orbita \
    && adduser --system --ingroup orbita --home /app orbita \
    && chown -R orbita:orbita /app /data
USER orbita

EXPOSE 2024

# --allow-blocking: ноды агента ходят в SQLite и на диск синхронно, и сервер
# разработки по умолчанию считает это ошибкой. Работа идёт в пуле потоков, цикл
# событий это не блокирует — запрет здесь мешает, а не помогает.
CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "2024", \
     "--no-browser", "--no-reload", "--allow-blocking"]

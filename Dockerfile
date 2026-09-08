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
FROM python:3.12-slim

# PYTHONUNBUFFERED — чтобы логи сервера появлялись в `docker compose logs`
# сразу, а не когда буфер сочтёт нужным.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости ставятся до копирования остального кода: слой с ними меняется
# редко и переиспользуется между сборками.
COPY pyproject.toml README.md LICENSE ./
COPY packages ./packages
COPY src ./src
RUN pip install ./packages/costmeter && pip install ".[server,postgres]"

COPY langgraph.json run_demo.py ./
COPY .env.example ./

# Данные — на том. Пути переопределяют значения по умолчанию из config.py:
# в контейнере рабочая папка пересоздаётся вместе с ним.
ENV PUBLISH_DIR=/data/published
RUN mkdir -p /data

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

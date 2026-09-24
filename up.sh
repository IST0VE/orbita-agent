#!/usr/bin/env bash
# Orbita одной командой: проверяет .env, дописывает недостающие секреты,
# собирает и поднимает Compose (Postgres, агент, веб-интерфейс), ждёт, пока всё
# ответит, и открывает интерфейс в браузере.
#
#     ./up.sh                из корня репозитория
#     ./up.sh --no-browser   не открывать браузер
#
# Windows-версия того же — up.cmd и scripts/up.ps1; проверки в них одинаковые.
set -euo pipefail

cd "$(dirname "$0")"

open_browser=1
case "${1:-}" in
  --no-browser) open_browser=0 ;;
  "") ;;
  *) echo "Неизвестный аргумент: $1 (есть только --no-browser)" >&2; exit 2 ;;
esac

fail() {
  printf '\n\033[31m%s\033[0m\n' "$1" >&2
  exit 1
}

# Последнее значение ключа в .env — как у Compose и python-dotenv.
env_get() {
  local line value
  line=$(grep -E "^[[:space:]]*$1[[:space:]]*=" .env | tail -n 1 | tr -d '\r') || true
  [ -n "$line" ] || return 0
  value=${line#*=}
  value=$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  case $value in
    \"*\") value=${value#\"}; value=${value%\"} ;;
    \'*\') value=${value#\'}; value=${value%\'} ;;
    *) value=$(printf '%s' "$value" | sed -e 's/[[:space:]]#.*$//') ;;
  esac
  printf '%s' "$value"
}

# Пустая строка ключа заполняется на месте, отсутствующая дописывается в конец.
# Запись через `cat >`, а не `mv`: у .env остаются его права доступа.
env_set() {
  local tmp
  tmp=$(mktemp)
  awk -v BINMODE=3 -v k="$1" -v v="$2" '
    { line = $0; cr = ""; if (sub(/\r$/, "", line)) cr = "\r" }
    !done && line ~ ("^[ \t]*" k "[ \t]*=[ \t]*$") { print k "=" v cr; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' .env > "$tmp"
  cat "$tmp" > .env
  rm -f "$tmp"
}

# 43 символа латиницы и цифр: без знаков, которые пришлось бы экранировать
# в POSTGRES_URI.
new_secret() {
  LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c 43 || true
}

# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Создан .env из .env.example."
fi

for name in API_ADMIN_TOKEN POSTGRES_PASSWORD; do
  if [ -z "$(env_get "$name")" ]; then
    secret=$(new_secret)
    [ "${#secret}" -ge 32 ] || fail "Не удалось получить случайные байты из /dev/urandom для $name."
    env_set "$name" "$secret"
    echo "В .env записан случайный $name."
  fi
done

provider=$(env_get LLM_PROVIDER | tr '[:upper:]' '[:lower:]')
provider=${provider:-deepseek}
# Штатный образ ставит адаптеры deepseek и openai; anthropic в нём нет,
# и прогон упал бы уже в интерфейсе, на первом обращении к модели.
if [ "$provider" = anthropic ]; then
  fail "LLM_PROVIDER=anthropic не поддержан штатным образом: в нём нет адаптера langchain-anthropic. Используйте deepseek или openai либо локальный запуск (docs/GETTING_STARTED.md)."
fi
key=$(env_get LLM_API_KEY)
key=${key:-${LLM_API_KEY:-}}
[ -n "$key" ] || key=$(env_get "$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')_API_KEY")
[ -n "$key" ] || fail "В .env не задан LLM_API_KEY — ключ модели ($provider). Впишите его и запустите снова."

token=$(env_get API_ADMIN_TOKEN)
if [ "${#token}" -lt 32 ] || [ "${#token}" -gt 256 ] || ! printf '%s' "$token" | LC_ALL=C grep -qE '^[!-~]+$'; then
  fail "API_ADMIN_TOKEN в .env должен состоять из 32–256 ASCII-символов без пробелов. Очистите значение — скрипт сгенерирует новое."
fi
if ! env_get POSTGRES_PASSWORD | LC_ALL=C grep -qE '^[A-Za-z0-9._~-]+$'; then
  fail "POSTGRES_PASSWORD в .env должен состоять из латиницы, цифр и знаков . _ ~ - : он встраивается в адрес базы. Очистите значение — скрипт сгенерирует новое."
fi

# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "Не найден Docker. Установите Docker Engine или Docker Desktop: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || fail "Не найден Docker Compose v2 (docker compose). Установите плагин compose или обновите Docker Desktop."
if ! docker info >/dev/null 2>&1; then
  echo "Docker не отвечает — пробую запустить Docker Desktop..."
  docker desktop start >/dev/null 2>&1 || true
  docker info >/dev/null 2>&1 || fail "Docker не запущен или недоступен этому пользователю. Запустите Docker Desktop (Linux: sudo systemctl start docker; пользователь должен входить в группу docker) и повторите."
fi

# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------
echo
echo "Собираю образы и поднимаю контейнеры. Первая сборка занимает несколько минут..."
# Docker Desktop прикладывает к каждой сборке provenance-аттестацию со своим
# digest: даже целиком закешированный образ получает новый ID, и Compose
# пересоздаёт контейнеры — повторный запуск обрывал бы идущие прогоны.
export BUILDX_NO_DEFAULT_ATTESTATIONS=1
if ! docker compose up -d --build --wait --wait-timeout 600; then
  echo
  echo "Последние строки журнала агента:"
  docker compose logs --tail 40 agent || true
  fail "Запуск не удался. Состояние контейнеров: docker compose ps"
fi

port=${ORBITA_WEB_PORT:-$(env_get ORBITA_WEB_PORT)}
url="http://localhost:${port:-8080}"

printf '\n\033[32mOrbita запущена: %s\033[0m\n' "$url"
echo "При первом входе браузер спросит токен API — это значение API_ADMIN_TOKEN из .env."
echo
echo "Журнал агента:  docker compose logs -f agent"
echo "Остановить:     docker compose down    (документы и треды остаются на томах)"
echo "После правки .env запустите ./up.sh снова: контейнеры пересоздадутся."

if [ "$open_browser" = 1 ]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  fi
fi

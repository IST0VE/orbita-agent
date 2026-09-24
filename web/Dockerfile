# Веб-интерфейс Orbita: собранный Vite и nginx, который отдаёт сборку и
# проксирует API к backend. Фронт и API оказываются на одном origin — так же,
# как в dev-сервере Vite, — поэтому VITE_API_URL при сборке не задаётся, а
# политика документа разрешает только свой адрес.
#
# Отдельно этот образ не нужен: его собирает и запускает `docker compose up`
# (сервис `web`), см. docker-compose.yml и up.cmd / up.sh в корне.
FROM node:22-slim AS build

WORKDIR /web

# Зависимости до исходников: слой переживает правки интерфейса.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

# Непривилегированный вариант nginx слушает 8080 и работает не от root —
# поэтому в Compose у сервиса можно отобрать все capabilities, как у backend.
FROM nginxinc/nginx-unprivileged:stable-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html

EXPOSE 8080

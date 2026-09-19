/**
 * Content-Security-Policy собранного фронтенда.
 *
 * Политика лежала в `index.html` неизменяемой строкой, а `connect-src`
 * разрешал только текущий origin и loopback. При этом `VITE_API_URL` обещает
 * отдельный backend, а `authorizedFetch` — удалённый HTTPS. Обещание не
 * работало: при `VITE_API_URL=https://api.example.test` браузер блокировал
 * REST и SSE ещё до отправки, при любых корректных CORS и токене. Разрешающий
 * заголовок это не лечит — несколько политик применяются пересечением, и
 * meta-политика из документа остаётся в силе.
 *
 * Поэтому `connect-src` собирается при сборке из того же адреса, который
 * встраивается в код. Не `https:` целиком: политика должна называть ровно тот
 * адрес, которым пользуются, иначе она перестаёт что-либо ограничивать.
 */

/** Адреса, которые разрешены всегда: свой origin и локальный backend. */
const BASE_SOURCES = [
  "'self'",
  "http://localhost:*",
  "http://127.0.0.1:*",
  "ws://localhost:*",
  "ws://127.0.0.1:*",
];

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

export class ApiOriginError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiOriginError";
  }
}

/**
 * Источники `connect-src` для заданного адреса API.
 *
 * Пустой адрес — совместное размещение: фронтенд и API на одном origin.
 */
export function connectSources(apiUrl: string | undefined): string[] {
  const target = (apiUrl ?? "").trim();
  if (!target) return [...BASE_SOURCES];

  let url: URL;
  try {
    url = new URL(target);
  } catch {
    throw new ApiOriginError(`VITE_API_URL не адрес: ${target}`);
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new ApiOriginError(`VITE_API_URL: поддерживаются только http и https, получено ${url.protocol}`);
  }
  // Тот же запрет, что в `authorizedFetch`: токен не уходит по открытому HTTP
  // никуда, кроме локальной машины. Политика, разрешающая такой адрес, лишь
  // отложила бы отказ до запроса.
  if (url.protocol === "http:" && !LOOPBACK.has(url.hostname)) {
    throw new ApiOriginError(
      `VITE_API_URL: удалённый API допускается только по HTTPS, получено ${url.origin}`,
    );
  }

  const sources = [...BASE_SOURCES];
  const socket = `${url.protocol === "https:" ? "wss:" : "ws:"}//${url.host}`;
  for (const source of [url.origin, socket]) {
    if (!sources.includes(source)) sources.push(source);
  }
  return sources;
}

/** Готовая строка политики для `<meta http-equiv="Content-Security-Policy">`. */
export function contentSecurityPolicy(apiUrl: string | undefined): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    `connect-src ${connectSources(apiUrl).join(" ")}`,
    "img-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
  ].join("; ");
}

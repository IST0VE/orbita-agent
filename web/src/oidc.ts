/**
 * Вход пользователей через OIDC (Keycloak): Authorization Code + PKCE.
 *
 * Включает его сервер: `/api/auth/config` отвечает без токена и говорит, задан
 * ли OIDC_ISSUER. Не задан — интерфейс работает как раньше, с API_ADMIN_TOKEN.
 * Задан — до первого кадра браузер уходит на страницу входа Keycloak, а
 * `authorizedFetch` подставляет в каждый запрос access-токен пользователя.
 *
 * Без библиотеки: публичному клиенту хватает двух запросов за токенами, за
 * кодом и за обновлением. keycloak-js тянет ещё iframe проверки сессии, и ради
 * него пришлось бы открывать `frame-src` в политике документа.
 *
 * Оба запроса идут в `/api/auth/token`, а в Keycloak их передаёт сервер.
 * `connect-src` называет только API, адрес которого известен при сборке
 * (web/build/csp.ts), а Keycloak живёт на своём домене и задаётся на сервере:
 * прямой запрос браузера политика заблокировала бы уже после ввода пароля.
 * Переходы на вход и выход — навигация, их `connect-src` не касается.
 */
import { API_URL } from "./api";
import { setUserTokens } from "./auth";

type Config = {
  enabled: true;
  issuer: string;
  client_id: string;
  authorization_endpoint: string;
  end_session_endpoint: string;
};

type Tokens = {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  /** Миллисекунды эпохи, после которых access-токен уже не примут. */
  expires_at: number;
  obtained_at: number;
};

type Flow = { state: string; verifier: string; returnTo: string };

export type User = { name: string; username: string; subject: string };

const TOKENS_KEY = "orbita.oidc.tokens";
const FLOW_KEY = "orbita.oidc.flow";
// Обновлять заранее: запрос, ушедший за секунду до истечения, дойдёт до
// сервера уже с просроченным токеном.
const REFRESH_MARGIN_MS = 30_000;
// Сервер отверг токен, полученный только что. Новый вход дал бы такой же, и
// страница уходила бы в Keycloak и обратно без конца.
const FRESH_LOGIN_MS = 15_000;

let config: Config | null = null;
let refreshing: Promise<Tokens | null> | null = null;

function base64url(bytes: Uint8Array): string {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(): string {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

function redirectUri(): string {
  return `${window.location.origin}/`;
}

function read<T>(key: string): T | null {
  try {
    return JSON.parse(sessionStorage.getItem(key) ?? "null") as T | null;
  } catch {
    return null;
  }
}

function save(tokens: Tokens | null): void {
  if (tokens) sessionStorage.setItem(TOKENS_KEY, JSON.stringify(tokens));
  else sessionStorage.removeItem(TOKENS_KEY);
}

async function tokenRequest(body: Record<string, string>): Promise<Tokens> {
  // Клиента realm подставляет сервер: он же решает, чьим токенам верить.
  const response = await fetch(`${API_URL}/api/auth/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(body),
    cache: "no-store",
    credentials: "omit",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || typeof payload.access_token !== "string") {
    throw new Error(payload.error_description ?? payload.error ?? `вход не завершён: сервер ответил ${response.status}`);
  }
  const now = Date.now();
  return {
    access_token: payload.access_token,
    refresh_token: payload.refresh_token,
    id_token: payload.id_token,
    expires_at: now + Number(payload.expires_in ?? 60) * 1000,
    obtained_at: now,
  };
}

async function login(): Promise<never> {
  const active = config as Config;
  const verifier = randomString();
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  const state = randomString();
  const { pathname, search, hash } = window.location;
  const flow: Flow = { state, verifier, returnTo: pathname + search + hash };
  sessionStorage.setItem(FLOW_KEY, JSON.stringify(flow));
  const url = new URL(active.authorization_endpoint);
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: active.client_id,
    redirect_uri: redirectUri(),
    scope: "openid",
    state,
    code_challenge: base64url(new Uint8Array(digest)),
    code_challenge_method: "S256",
  }).toString();
  window.location.assign(url.toString());
  // Страница уходит; рисовать до этого нечего.
  return new Promise<never>(() => {});
}

async function finishLogin(params: URLSearchParams): Promise<void> {
  const flow = read<Flow>(FLOW_KEY);
  sessionStorage.removeItem(FLOW_KEY);
  if (!flow || flow.state !== params.get("state")) {
    throw new Error("Ответ Keycloak не относится к этому входу. Откройте страницу заново.");
  }
  const error = params.get("error");
  if (error) throw new Error(params.get("error_description") ?? error);
  save(await tokenRequest({
    grant_type: "authorization_code",
    code: params.get("code") ?? "",
    redirect_uri: redirectUri(),
    code_verifier: flow.verifier,
  }));
  // Код одноразовый: в адресной строке и в истории ему не место.
  window.history.replaceState(null, "", flow.returnTo);
}

async function currentTokens(): Promise<Tokens | null> {
  const tokens = read<Tokens>(TOKENS_KEY);
  if (!tokens) return null;
  if (tokens.expires_at - REFRESH_MARGIN_MS > Date.now()) return tokens;
  if (!tokens.refresh_token) return null;
  // Одно обновление на все запросы, которые заметили истечение одновременно.
  refreshing ??= tokenRequest({ grant_type: "refresh_token", refresh_token: tokens.refresh_token })
    .then(
      (fresh) => {
        const next = { ...fresh, id_token: fresh.id_token ?? tokens.id_token };
        save(next);
        return next;
      },
      () => {
        save(null);
        return null;
      },
    )
    .finally(() => { refreshing = null; });
  return refreshing;
}

/**
 * До первого кадра: узнать у сервера, нужен ли вход, и если нужен — войти.
 *
 * Сервер недоступен или старый, без `/api/auth/config`, — работаем как
 * раньше: шапка сама скажет «нет сервера» или спросит админ-токен.
 */
export async function initAuth(): Promise<void> {
  let body: Partial<Config> & { enabled?: boolean } | null = null;
  try {
    const response = await fetch(`${API_URL}/api/auth/config`, { cache: "no-store" });
    if (response.ok) body = await response.json();
  } catch {
    return;
  }
  if (!body?.enabled) return;
  config = body as Config;
  setUserTokens({
    token: async () => (await currentTokens())?.access_token ?? null,
    unauthorized: () => {
      const tokens = read<Tokens>(TOKENS_KEY);
      if (tokens && Date.now() - tokens.obtained_at < FRESH_LOGIN_MS) return;
      save(null);
      void login();
    },
  });
  const params = new URLSearchParams(window.location.search);
  if (params.has("state") && (params.has("code") || params.has("error")) && sessionStorage.getItem(FLOW_KEY)) {
    await finishLogin(params);
    return;
  }
  if (!(await currentTokens())) await login();
}

/** Кто вошёл — из access-токена. Подпись проверяет сервер; здесь только подпись в шапке. */
export function currentUser(): User | null {
  const tokens = read<Tokens>(TOKENS_KEY);
  if (!config || !tokens) return null;
  try {
    const part = tokens.access_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const claims = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(part), (c) => c.charCodeAt(0))));
    const username = String(claims.preferred_username ?? claims.sub ?? "");
    return { name: String(claims.name ?? username), username, subject: String(claims.sub ?? "") };
  } catch {
    return null;
  }
}

/** Выход из Keycloak, а не только из вкладки: иначе следующий вход прошёл бы молча. */
export function logout(): void {
  const active = config;
  if (!active) return;
  const idToken = read<Tokens>(TOKENS_KEY)?.id_token;
  save(null);
  const url = new URL(active.end_session_endpoint);
  url.search = new URLSearchParams({
    client_id: active.client_id,
    post_logout_redirect_uri: redirectUri(),
    ...(idToken ? { id_token_hint: idToken } : {}),
  }).toString();
  window.location.assign(url.toString());
}

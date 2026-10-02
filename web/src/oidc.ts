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
 *
 * Сессия держится сама, пока открыта вкладка. Access-токен живёт минуты, и
 * раньше он обновлялся только тогда, когда его просил запрос: человек уходил
 * в другую вкладку, возвращался, отправлял запрос — а обновлять было уже
 * нечем, и страница уходила на вход вместе с набранным текстом. Теперь токен
 * обновляется заранее, по таймеру, и сразу, как только вкладка снова видна.
 * Каждое обновление продлевает и сессию Keycloak, так что открытая вкладка
 * не выходит из системы, пока сессия не упрётся в свой предельный срок.
 *
 * Сбой обновления — не всегда конец сессии. Keycloak ответил `invalid_grant`
 * — сессии больше нет, нужен вход. Не ответил вовсе (сеть, перезапуск) — токены
 * остаются, обновление повторится через `RETRY_MS`. Раньше любой сбой стирал
 * токены, и мигнувшая сеть выкидывала на страницу входа.
 *
 * Если вход всё-таки нужен, страница уходит в Keycloak, только когда её видно:
 * спрятанная вкладка дождётся возвращения человека. Набранный текст не
 * теряется — черновики поля задачи лежат в `localStorage` (`lib/drafts.ts`), а
 * после возвращения шапка говорит, что сессия истекала и текст на месте.
 */
import { API_URL } from "./api.ts";
import { setUserTokens } from "./auth.ts";

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
/** Вход повторный: сессия кончилась посреди работы. Читает его шапка после возвращения. */
const RELOGIN_KEY = "orbita.oidc.relogin";
// Обновлять заранее: запрос, ушедший за секунду до истечения, дойдёт до
// сервера уже с просроченным токеном.
const REFRESH_MARGIN_MS = 60_000;
// Keycloak не ответил на обновление: повторить через столько.
const RETRY_MS = 15_000;
// Сервер отверг токен, полученный только что. Новый вход дал бы такой же, и
// страница уходила бы в Keycloak и обратно без конца.
const FRESH_LOGIN_MS = 15_000;

let config: Config | null = null;
let refreshing: Promise<Tokens | null> | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
/** Вход нужен, но вкладка спрятана: уйдём в Keycloak, когда её откроют. */
let loginWhenVisible = false;
let leaving = false;

/** Keycloak отказал в токене. `rejected` — отказ по существу: сессии больше нет. */
class TokenError extends Error {
  readonly rejected: boolean;

  constructor(message: string, rejected: boolean) {
    super(message);
    this.rejected = rejected;
  }
}

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
    // 400 и 401 от token endpoint — это ответ Keycloak: код или refresh-токен
    // больше не годятся. 5xx и обрыв — сбой по дороге, о сессии он не говорит.
    throw new TokenError(
      payload.error_description ?? payload.error ?? `вход не завершён: сервер ответил ${response.status}`,
      response.status === 400 || response.status === 401,
    );
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

async function login(reason: "start" | "expired" = "start"): Promise<never> {
  const active = config as Config;
  // Второй вызов, пока страница уже уходит, начал бы второй вход с другим state.
  if (leaving) return new Promise<never>(() => {});
  leaving = true;
  clearTimeout(timer);
  if (reason === "expired") sessionStorage.setItem(RELOGIN_KEY, String(Date.now()));
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
  if (!tokens.refresh_token) return tokens.expires_at > Date.now() ? tokens : null;
  // Одно обновление на все запросы, которые заметили истечение одновременно.
  refreshing ??= tokenRequest({ grant_type: "refresh_token", refresh_token: tokens.refresh_token })
    .then(
      (fresh) => {
        const next = {
          ...fresh,
          // Keycloak может не прислать новый refresh-токен — старый тогда жив.
          refresh_token: fresh.refresh_token ?? tokens.refresh_token,
          id_token: fresh.id_token ?? tokens.id_token,
        };
        save(next);
        return next;
      },
      (error: unknown) => {
        if (error instanceof TokenError && error.rejected) {
          save(null);
          return null;
        }
        // Keycloak не ответил: сессия, скорее всего, жива. Отдаём что есть —
        // ещё не истёкший токен сервер примет, — и пробуем снова чуть позже.
        return tokens;
      },
    )
    .finally(() => { refreshing = null; });
  return refreshing;
}

/** Обновить токен заранее и назначить следующее обновление. */
async function keepAlive(): Promise<void> {
  if (!config || leaving) return;
  const before = read<Tokens>(TOKENS_KEY);
  const tokens = await currentTokens();
  if (!tokens) {
    // Сессия была и кончилась — нужен вход. Без токенов с самого начала сюда
    // не попадают: такой вход начинает `initAuth`.
    if (before) sessionLost();
    return;
  }
  schedule(tokens);
}

function schedule(tokens: Tokens): void {
  clearTimeout(timer);
  if (!tokens.refresh_token) return;
  const due = tokens.expires_at - REFRESH_MARGIN_MS - Date.now();
  // Неудачное обновление оставило старый срок — повтор не чаще RETRY_MS.
  timer = setTimeout(() => { void keepAlive(); }, Math.max(RETRY_MS, due));
}

/** Сессии нет. Уходим на вход сразу, если вкладку видно, иначе — когда откроют. */
function sessionLost(): void {
  clearTimeout(timer);
  if (document.visibilityState === "hidden") {
    loginWhenVisible = true;
    return;
  }
  void login("expired");
}

function watchSession(): void {
  // Вкладка снова видна, окно в фокусе, сеть вернулась — самое время проверить
  // токен: таймеры спрятанной вкладки браузер замедляет, а сон ноутбука их
  // останавливает вовсе.
  const wake = () => {
    if (document.visibilityState === "hidden") return;
    if (loginWhenVisible) {
      loginWhenVisible = false;
      void login("expired");
      return;
    }
    void keepAlive();
  };
  document.addEventListener("visibilitychange", wake);
  window.addEventListener("focus", wake);
  window.addEventListener("online", wake);
}

/**
 * Вход был повторным: сессия истекла посреди работы. Читается один раз —
 * шапка говорит об этом и больше не повторяет.
 */
export function takeRelogin(): boolean {
  const marked = sessionStorage.getItem(RELOGIN_KEY);
  if (marked === null) return false;
  sessionStorage.removeItem(RELOGIN_KEY);
  return Date.now() - Number(marked) < 10 * 60_000;
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
      sessionLost();
    },
  });
  watchSession();
  const params = new URLSearchParams(window.location.search);
  if (params.has("state") && (params.has("code") || params.has("error")) && sessionStorage.getItem(FLOW_KEY)) {
    await finishLogin(params);
    const fresh = read<Tokens>(TOKENS_KEY);
    if (fresh) schedule(fresh);
    return;
  }
  const tokens = await currentTokens();
  if (!tokens) await login();
  else schedule(tokens);
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
  clearTimeout(timer);
  leaving = true;
  save(null);
  const url = new URL(active.end_session_endpoint);
  url.search = new URLSearchParams({
    client_id: active.client_id,
    post_logout_redirect_uri: redirectUri(),
    ...(idToken ? { id_token_hint: idToken } : {}),
  }).toString();
  window.location.assign(url.toString());
}

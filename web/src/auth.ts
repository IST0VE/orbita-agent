/** One authenticated transport for settings, graph requests and SDK streams. */
import { reportClient } from "./lib/clientLog.ts";

const ADMIN_TOKEN_KEY = "orbita.adminApiToken";
type AuthState = { revision: number; prompt: Promise<void> | null };
const sessions = new WeakMap<Storage, AuthState>();

function requestLine(input: RequestInfo | URL, init: RequestInit): string {
  const method = init.method ?? (input instanceof Request ? input.method : "GET");
  try {
    return `${method.toUpperCase()} ${new URL(input instanceof Request ? input.url : String(input), window.location.href).pathname}`;
  } catch {
    return `${method.toUpperCase()} ${String(input)}`;
  }
}

/**
 * Every request the operator started goes through here, so failures the UI
 * only shows as one banner line are also kept in the client journal with
 * the method, path, status and response body. Background health checks are
 * not recorded: the header already shows "no server", and a journal that
 * repeats the same line every few seconds is not read.
 */
export async function authorizedFetch(input: RequestInfo | URL, init: RequestInit = {}, promptOnUnauthorized = true): Promise<Response> {
  if (!promptOnUnauthorized) return exchange(input, init, false);
  try {
    const response = await exchange(input, init, true);
    if (response.status >= 500) {
      const line = `${requestLine(input, init)} → ${response.status}`;
      response.clone().text().then(
        (body) => reportClient("error", "запрос", line, body.slice(0, 2000) || undefined),
        () => reportClient("error", "запрос", line),
      );
    }
    return response;
  } catch (error) {
    // A stopped run aborts its stream on purpose; that is not a failure.
    if ((error as Error)?.name !== "AbortError") {
      reportClient("warning", "запрос", `${requestLine(input, init)}: ${(error as Error)?.message ?? String(error)}`);
    }
    throw error;
  }
}

async function exchange(input: RequestInfo | URL, init: RequestInit, promptOnUnauthorized: boolean): Promise<Response> {
  const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.username || url.password || !(url.protocol === "https:" || (url.protocol === "http:" && loopback))) {
    throw new Error("Для удалённого API требуется HTTPS без учётных данных в URL");
  }
  const storage = window.sessionStorage;
  let state = sessions.get(storage);
  if (!state) {
    state = { revision: 0, prompt: null };
    sessions.set(storage, state);
  }
  const auth = state;
  const revision = auth.revision;
  const initialToken = storage.getItem(ADMIN_TOKEN_KEY);
  const requestHeaders = (token: string | null) => {
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    // Always use the current token, including after rotation or a failed attempt.
    headers.delete("authorization");
    if (token) headers.set("authorization", `Bearer ${token}`);
    return headers;
  };
  const send = (token: string | null) => fetch(input instanceof Request ? input.clone() : input, {
    ...init, headers: requestHeaders(token), redirect: "error", cache: "no-store",
  });
  let response = await send(initialToken);
  // Background health checks must never interrupt the user with token prompts.
  if (!promptOnUnauthorized) return response;
  if (response.status !== 401 || response.headers.get("www-authenticate") !== "Bearer") return response;
  // A late 401 belongs to the credentials sent with that request. A completed
  // prompt (even cancellation) must also cover responses arriving on later ticks.
  if (auth.revision === revision && storage.getItem(ADMIN_TOKEN_KEY) === initialToken) {
    auth.prompt ??= Promise.resolve().then(() => {
      const token = window.prompt("Bearer-токен API (API_ADMIN_TOKEN)")?.trim();
      if (token) storage.setItem(ADMIN_TOKEN_KEY, token);
      else storage.removeItem(ADMIN_TOKEN_KEY);
      auth.revision += 1;
    }).finally(() => { auth.prompt = null; });
  }
  await auth.prompt;
  const token = storage.getItem(ADMIN_TOKEN_KEY);
  if (!token) return response;
  response = await send(token);
  // An older failed retry cannot erase credentials entered by a newer request.
  if (response.status === 401 && storage.getItem(ADMIN_TOKEN_KEY) === token) {
    storage.removeItem(ADMIN_TOKEN_KEY);
  }
  return response;
}

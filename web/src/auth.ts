/** One authenticated transport for settings, graph requests and SDK streams. */
const ADMIN_TOKEN_KEY = "orbita.adminApiToken";
type AuthState = { revision: number; prompt: Promise<void> | null };
const sessions = new WeakMap<Storage, AuthState>();

export async function authorizedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
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

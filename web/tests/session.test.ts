import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";

import { authorizedFetch } from "../src/auth.ts";
import { draftKey, loadDraft, quoteBlock, saveDraft } from "../src/lib/drafts.ts";

const KEYCLOAK = "https://kc.example.test/realms/orbita/protocol/openid-connect";
let instance = 0;

type Browser = {
  session: Map<string, string>;
  assigned: string[];
  tokenCalls: URLSearchParams[];
  setVisible: (visible: boolean) => void;
  fire: (type: string) => void;
};

function storage(map: Map<string, string>) {
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => { map.set(key, String(value)); },
    removeItem: (key: string) => { map.delete(key); },
    key: (index: number) => [...map.keys()][index] ?? null,
    get length() { return map.size; },
  };
}

/** Вкладка браузера ровно настолько, насколько её трогает `oidc.ts`. */
function browser(t: TestContext, options: { tokens?: object; token: () => Response }): Browser {
  const session = new Map<string, string>();
  if (options.tokens) session.set("orbita.oidc.tokens", JSON.stringify(options.tokens));
  const listeners = new Map<string, Array<() => void>>();
  const listen = (type: string, handler: () => void) => listeners.set(type, [...(listeners.get(type) ?? []), handler]);
  const assigned: string[] = [];
  const tokenCalls: URLSearchParams[] = [];
  const doc = { visibilityState: "visible", addEventListener: listen };
  const win = {
    location: {
      origin: "http://localhost:5173", href: "http://localhost:5173/", pathname: "/", search: "", hash: "",
      assign: (url: string) => { assigned.push(url); },
    },
    history: { replaceState: () => undefined },
    addEventListener: listen,
    sessionStorage: storage(session),
    prompt: () => assert.fail("OIDC must not prompt for an admin token"),
  };
  for (const [name, value] of [["window", win], ["document", doc], ["sessionStorage", storage(session)]] as const) {
    Object.defineProperty(globalThis, name, { configurable: true, value });
  }
  t.after(() => {
    for (const name of ["window", "document", "sessionStorage"]) Reflect.deleteProperty(globalThis, name);
  });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  t.mock.method(globalThis, "fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/auth/config")) {
      return Response.json({
        enabled: true, issuer: "https://kc.example.test/realms/orbita", client_id: "orbita-web",
        authorization_endpoint: `${KEYCLOAK}/auth`, end_session_endpoint: `${KEYCLOAK}/logout`,
      });
    }
    if (url.endsWith("/api/auth/token")) {
      tokenCalls.push(new URLSearchParams(String(init?.body)));
      return options.token();
    }
    const authorized = new Headers(init?.headers).get("authorization");
    return authorized
      ? Response.json({ ok: true })
      : new Response("{}", { status: 401, headers: { "www-authenticate": "Bearer" } });
  });
  return {
    session,
    assigned,
    tokenCalls,
    setVisible: (visible) => { doc.visibilityState = visible ? "visible" : "hidden"; },
    fire: (type) => { for (const handler of listeners.get(type) ?? []) handler(); },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const fresh = (lifetime: number) => ({
  access_token: "access", refresh_token: "refresh",
  expires_at: Date.now() + lifetime, obtained_at: Date.now() - 600_000,
});
const load = () => import(`../src/oidc.ts?case=${++instance}`) as Promise<typeof import("../src/oidc.ts")>;

test("a refresh that did not reach Keycloak keeps the session instead of logging out", async (t) => {
  const tab = browser(t, { tokens: fresh(-1_000), token: () => new Response("upstream down", { status: 503 }) });
  const oidc = await load();

  await oidc.initAuth();

  assert.equal(tab.tokenCalls.length, 1);
  assert.deepEqual(tab.assigned, []);
  assert.ok(tab.session.has("orbita.oidc.tokens"), "transient failure must not erase tokens");
});

test("coming back to the tab refreshes the token before the user sends anything", async (t) => {
  const tab = browser(t, {
    tokens: fresh(10 * 60_000),
    token: () => Response.json({ access_token: "renewed", expires_in: 300 }),
  });
  const oidc = await load();
  await oidc.initAuth();
  assert.equal(tab.tokenCalls.length, 0);

  // Пять минут в другой вкладке: срок токена подошёл к концу.
  tab.session.set("orbita.oidc.tokens", JSON.stringify(fresh(20_000)));
  tab.fire("visibilitychange");
  await settle();

  assert.equal(tab.tokenCalls.length, 1);
  assert.equal(tab.tokenCalls[0].get("grant_type"), "refresh_token");
  const stored = JSON.parse(tab.session.get("orbita.oidc.tokens")!);
  assert.equal(stored.access_token, "renewed");
  // Keycloak не прислал новый refresh-токен — старый остаётся.
  assert.equal(stored.refresh_token, "refresh");
  assert.deepEqual(tab.assigned, []);
});

test("an ended session waits for a hidden tab and then logs in again, marking the return", async (t) => {
  const tab = browser(t, {
    tokens: fresh(10 * 60_000),
    token: () => Response.json({ error: "invalid_grant" }, { status: 400 }),
  });
  const oidc = await load();
  await oidc.initAuth();

  tab.session.set("orbita.oidc.tokens", JSON.stringify(fresh(-1_000)));
  tab.setVisible(false);
  const response = await authorizedFetch("/threads/search");
  await settle();

  assert.equal(response.status, 401);
  assert.deepEqual(tab.assigned, [], "a hidden tab must not jump to the login page");

  tab.setVisible(true);
  tab.fire("visibilitychange");
  await settle();
  await settle();

  assert.equal(tab.assigned.length, 1);
  assert.ok(tab.assigned[0].startsWith(`${KEYCLOAK}/auth?`), tab.assigned[0]);
  assert.equal(oidc.takeRelogin(), true);
  assert.equal(oidc.takeRelogin(), false);
});

test("drafts are per user and chat, survive reloads and become quotes", (t) => {
  const local = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage(local) });
  t.after(() => { Reflect.deleteProperty(globalThis, "localStorage"); });

  const anna = draftKey("anna", "agent", "thread-1");
  saveDraft(anna, "Опишите API возвратов");
  assert.equal(loadDraft(anna), "Опишите API возвратов");
  assert.equal(loadDraft(draftKey("tim", "agent", "thread-1")), "");
  assert.equal(loadDraft(draftKey("anna", "agent", null)), "");

  saveDraft(anna, "   ");
  assert.equal(local.size, 0, "an empty draft is removed, not stored");

  local.set(anna, JSON.stringify({ text: "старый", at: Date.now() - 8 * 24 * 3600_000 }));
  assert.equal(loadDraft(anna), "");

  assert.equal(quoteBlock("первая строка\r\n\r\nвторая"), "> первая строка\n>\n> вторая");
});

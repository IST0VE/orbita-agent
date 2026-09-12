import assert from "node:assert/strict";
import test from "node:test";
import { authorizedFetch } from "../src/auth.ts";

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

const unauthorized = () => new Response("{}", {
  status: 401, headers: { "www-authenticate": "Bearer" },
});

test("background health requests return 401 without prompting or erasing credentials", async (t) => {
  const stored = new Map([["orbita.adminApiToken", "current-token"]]);
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (_input, init) => {
    calls += 1;
    assert.equal(new Headers(init?.headers).get("authorization"), "Bearer current-token");
    return unauthorized();
  });
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    location: { href: "http://localhost:5173/" },
    prompt: () => { assert.fail("Background request opened a prompt"); },
    sessionStorage: {
      getItem: (key: string) => stored.get(key) ?? null,
      removeItem: (key: string) => stored.delete(key),
    },
  } });
  t.after(() => { Reflect.deleteProperty(globalThis, "window"); });
  assert.equal((await authorizedFetch("/info", {}, false)).status, 401);
  assert.equal(calls, 1);
  assert.equal(stored.get("orbita.adminApiToken"), "current-token");
});

for (const answer of ["  new-token  ", null, "   ", "wrong-token"]) {
  test(`ten staggered 401 responses share one prompt (${JSON.stringify(answer)})`, async (t) => {
    const stored = new Map<string, string>();
    let prompts = 0;
    let retries = 0;
    const pending = Array.from({ length: 10 }, deferredResponse);
    t.mock.method(globalThis, "fetch", async (input, init) => {
      const token = new Headers(init?.headers).get("authorization");
      if (!token) return pending[Number(String(input).slice(1))].promise;
      retries += 1;
      return token === "Bearer new-token" ? new Response("{}") : unauthorized();
    });
    Object.defineProperty(globalThis, "window", { configurable: true, value: {
      location: { href: "http://localhost:5173/" },
      prompt: () => { prompts += 1; return answer; },
      sessionStorage: {
        getItem: (key: string) => stored.get(key) ?? null,
        setItem: (key: string, value: string) => stored.set(key, value),
        removeItem: (key: string) => stored.delete(key),
      },
    } });
    t.after(() => { Reflect.deleteProperty(globalThis, "window"); });
    const requests = pending.map((_, i) => authorizedFetch(`/${i}`));
    for (let i = 0; i < pending.length; i += 1) {
      pending[i].resolve(unauthorized());
      assert.equal((await requests[i]).status, answer?.trim() === "new-token" ? 200 : 401);
    }
    assert.equal(prompts, 1);
    assert.equal(retries, answer?.trim() === "new-token" ? 10 : answer === "wrong-token" ? 1 : 0);
  });
}

test("a late rejected retry does not remove a newer token", async (t) => {
  const stored = new Map<string, string>();
  const oldRetry = deferredResponse();
  const retryStarted = deferredResponse();
  let prompts = 0;
  t.mock.method(globalThis, "fetch", async (input, init) => {
    const token = new Headers(init?.headers).get("authorization");
    if (String(input) === "/old" && token === "Bearer first-token") {
      retryStarted.resolve(new Response());
      return oldRetry.promise;
    }
    return token === "Bearer second-token" ? new Response("{}") : unauthorized();
  });
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    location: { href: "http://localhost:5173/" },
    prompt: () => ++prompts === 1 ? "first-token" : "second-token",
    sessionStorage: {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
    },
  } });
  t.after(() => { Reflect.deleteProperty(globalThis, "window"); });
  const old = authorizedFetch("/old");
  await retryStarted.promise;
  assert.equal((await authorizedFetch("/new")).status, 200);
  oldRetry.resolve(unauthorized());
  assert.equal((await old).status, 401);
  assert.equal(stored.get("orbita.adminApiToken"), "second-token");
});

test("API transport replaces stale authorization and preserves Headers instances", async (t) => {
  const stored = new Map([["orbita.adminApiToken", "current-token"]]);
  t.mock.method(globalThis, "fetch", async (_input, init) => {
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("authorization"), "Bearer current-token");
    assert.equal(headers.get("if-none-match"), "etag-value");
    assert.equal(init?.redirect, "error");
    assert.equal(init?.cache, "no-store");
    return new Response("{}", { status: 200 });
  });
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    location: { href: "http://localhost:5173/" },
    sessionStorage: { getItem: (key: string) => stored.get(key) },
  } });
  t.after(() => { Reflect.deleteProperty(globalThis, "window"); });
  await authorizedFetch("/threads", { headers: new Headers({
    authorization: "Bearer stale-token", "if-none-match": "etag-value",
  }) });
});

test("a 401 retries with the new token, including SDK requests", async (t) => {
  const stored = new Map([["orbita.adminApiToken", "old-token"]]);
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (_input, init) => {
    calls += 1;
    assert.equal(new Headers(init?.headers).get("authorization"),
      calls === 1 ? "Bearer old-token" : "Bearer new-token");
    return new Response("{}", calls === 1
      ? { status: 401, headers: { "www-authenticate": "Bearer" } }
      : { status: 200 });
  });
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    location: { href: "http://localhost:5173/" }, prompt: () => "new-token",
    sessionStorage: {
      getItem: (key: string) => stored.get(key),
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
    },
  } });
  t.after(() => { Reflect.deleteProperty(globalThis, "window"); });
  const request = new Request("http://localhost:5173/threads", {
    method: "POST", body: JSON.stringify({ input: "test" }),
  });
  assert.equal((await authorizedFetch(request)).status, 200);
  assert.equal(calls, 2);
  assert.equal(request.bodyUsed, false);
});

test("remote HTTP cannot receive the API token", async (t) => {
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    location: { href: "http://localhost:5173/" },
  } });
  t.after(() => { Reflect.deleteProperty(globalThis, "window"); });
  t.mock.method(globalThis, "fetch", async () => { throw new Error("must not fetch"); });
  await assert.rejects(authorizedFetch("http://remote.example/threads"), /HTTPS/);
});

import assert from "node:assert/strict";
import test from "node:test";

import { ApiOriginError, connectSources, contentSecurityPolicy } from "../build/csp.ts";

test("collocated deployment keeps the same-origin policy", () => {
  const sources = connectSources(undefined);
  assert.ok(sources.includes("'self'"));
  assert.ok(sources.includes("http://127.0.0.1:*"));
  assert.equal(sources.some((source) => source.startsWith("https://")), false);
});

test("a configured remote API is allowed by the policy it was built with", () => {
  // Настроенный адрес и разрешённый адрес обязаны совпадать: иначе браузер
  // блокирует запрос до отправки, и ошибка видна только в консоли.
  const policy = contentSecurityPolicy("https://api.example.test");
  assert.match(policy, /connect-src [^;]*https:\/\/api\.example\.test/);
  assert.match(policy, /connect-src [^;]*wss:\/\/api\.example\.test/);
  // Путь в адресе не расширяет разрешение: политика называет origin.
  assert.match(
    contentSecurityPolicy("https://api.example.test/base/"),
    /connect-src [^;]*https:\/\/api\.example\.test /,
  );
  // Разрешение остаётся точным, а не `https:` целиком.
  assert.equal(policy.includes("connect-src https:;"), false);
  assert.equal(policy.includes(" https: "), false);
});

test("an unusable API address stops the build instead of the request", () => {
  assert.throws(() => connectSources("http://api.example.test"), ApiOriginError);
  assert.throws(() => connectSources("не адрес"), ApiOriginError);
  assert.throws(() => connectSources("ftp://api.example.test"), ApiOriginError);
  // Локальный backend по http остаётся штатным сценарием разработки.
  assert.ok(connectSources("http://127.0.0.1:2025").includes("http://127.0.0.1:2025"));
});

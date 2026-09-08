import assert from "node:assert/strict";
import test from "node:test";

import { conditionMatches, configurableOf, inputSurfaceOf, matchInterrupt, resolveBinding } from "../src/engine/manifest/bindings.ts";
import { fallbackManifest, ManifestValidationError, validateManifest } from "../src/engine/manifest/validate.ts";
import { layeredLayout } from "../src/engine/canvas/layout.ts";
import { createRuntimeSnapshot, runtimeReducer } from "../src/engine/runtime/reducer.ts";
import { executionPath, visitCount } from "../src/engine/runtime/selectors.ts";
import { reconcileSnapshot } from "../src/engine/runtime/reconciliation.ts";
import { validateForm } from "../src/engine/widgets/formValidation.ts";
import { LiveEventFactory, runErrorMessage } from "../src/engine/api/langgraphAdapter.ts";
import type { RuntimeEvent } from "../src/engine/runtime/types.ts";
import type { UiManifest } from "../src/engine/manifest/types.ts";

function event(sequence: number, type: string, data: unknown = {}, eventId = `event-${sequence}`): RuntimeEvent {
  return {
    eventId,
    sequence,
    timestamp: `2026-08-30T10:00:0${Math.min(sequence, 9)}.000Z`,
    graphId: "demo",
    assistantId: "assistant",
    threadId: "thread",
    runId: "run",
    type,
    data,
    schemaVersion: "1.0",
  };
}

test("model authentication failures retain actionable guidance and their error class", () => {
  const error = new Error("An internal error occurred");
  error.name = "AuthenticationError";
  const factory = new LiveEventFactory("assistant", "prep", () => "thread");
  let state = createRuntimeSnapshot("assistant", "prep");
  state = runtimeReducer(state, { type: "event", event: factory.startRun() });
  state = runtimeReducer(state, { type: "event", event: factory.failed(error) });
  assert.equal(state.error?.code, "AuthenticationError");
  assert.match(state.error!.message, /LLM_MODEL/);
  assert.equal(runErrorMessage({ error: error.name, message: error.message }), state.error?.message);
  assert.equal(runErrorMessage(state.error), state.error?.message);
  assert.doesNotMatch(state.error!.message, /API_ADMIN_TOKEN|An internal error/);
});

test("unclassified errors keep useful messages or their class when details are hidden", () => {
  assert.equal(runErrorMessage(new Error("Specific failure")), "Specific failure");
  assert.match(runErrorMessage({ error: "ValueError", message: "An internal error occurred" }), /ValueError/);
  assert.match(runErrorMessage({ error: "RateLimitError" }), /квоту/);
  assert.match(runErrorMessage({ error: "APIConnectionError" }), /соединение/);
});

test("a late completion does not turn a cancelled or failed run into success", () => {
  for (const terminal of ["run.cancelled", "run.failed"]) {
    let state = createRuntimeSnapshot("assistant", "demo");
    state = runtimeReducer(state, { type: "event", event: event(1, "run.started") });
    state = runtimeReducer(state, { type: "event", event: event(2, terminal) });
    state = runtimeReducer(state, { type: "event", event: event(3, "run.completed") });
    assert.equal(state.runStatus, terminal === "run.cancelled" ? "cancelled" : "failed");
    state = runtimeReducer(state, { type: "event", event: event(1, "run.started", {}, "next-run-1") });
    state = runtimeReducer(state, { type: "event", event: event(2, "run.completed", {}, "next-run-2") });
    assert.equal(state.runStatus, "completed");
  }
});

test("server run metadata does not move an already running stream back to queued", () => {
  let state = createRuntimeSnapshot("assistant", "demo");
  state = runtimeReducer(state, { type: "event", event: event(1, "run.started") });
  state = runtimeReducer(state, { type: "event", event: event(2, "run.created") });
  assert.equal(state.runStatus, "running");
});

test("runtime reducer applies ordered lifecycle and ignores duplicates", () => {
  let state = createRuntimeSnapshot("assistant", "demo", "1.0");
  state = runtimeReducer(state, { type: "event", event: event(1, "run.started") });
  state = runtimeReducer(state, {
    type: "event",
    event: event(2, "node.completed", {
      nodeId: "prepare",
      executionId: "prepare:1",
      attempt: 1,
      update: { summary: "hello" },
    }),
  });
  const duplicate = runtimeReducer(state, {
    type: "event",
    event: event(2, "node.completed", {}, "event-2"),
  });

  assert.equal(state.runStatus, "running");
  assert.equal(state.executions["prepare:1"].status, "completed");
  assert.equal(state.executionOrder.length, 1);
  assert.strictEqual(duplicate, state);
});

test("sequence gaps enter reconnecting and buffered events drain in order", () => {
  let state = createRuntimeSnapshot("assistant", "demo");
  state = runtimeReducer(state, { type: "event", event: event(1, "run.started") });
  state = runtimeReducer(state, { type: "event", event: event(3, "run.completed") });
  assert.equal(state.connection, "reconnecting");
  assert.equal(state.lastSequence, 1);

  state = runtimeReducer(state, { type: "event", event: event(2, "state.snapshot", { state: { ok: true } }) });
  assert.equal(state.lastSequence, 3);
  assert.equal(state.runStatus, "completed");
  assert.deepEqual(state.state, { ok: true });
  assert.equal(Object.keys(state.bufferedEvents).length, 0);
});

test("reconciliation makes server snapshot authoritative", () => {
  const local = {
    ...createRuntimeSnapshot("assistant", "demo"),
    state: { optimistic: true },
    connection: "reconnecting" as const,
  };
  const reconciled = reconcileSnapshot(local, { server: true }, { runStatus: "interrupted" });
  assert.deepEqual(reconciled.state, { server: true });
  assert.equal(reconciled.connection, "live");
  assert.equal(reconciled.runStatus, "interrupted");
});

test("bindings distinguish missing from null and reject prototype paths", () => {
  assert.deepEqual(resolveBinding({ value: null }, "value"), { found: true, value: null });
  assert.deepEqual(resolveBinding({}, "value"), { found: false, value: undefined });
  assert.throws(() => resolveBinding({}, "__proto__.polluted"), /unsafe/);
  assert.equal(conditionMatches({ path: "status", in: ["ready", "done"] }, { status: "ready" }), true);
  assert.equal(conditionMatches({ not: { path: "hidden", equals: true } }, { hidden: false }), true);
});

test("interrupt matcher respects priority", () => {
  const chosen = matchInterrupt(
    [
      { id: "low", priority: 1, widget: "json", match: { path: "action", equals: "stage" }, resume_schema: {} },
      { id: "high", priority: 10, widget: "approval", match: { path: "action", equals: "stage" }, resume_schema: {} },
    ],
    { action: "stage" },
  );
  assert.equal(chosen?.id, "high");
});

test("manifest validation handles compatibility and safe fallback", () => {
  const fallback = fallbackManifest("unknown");
  assert.equal(validateManifest(fallback, "unknown").graph_id, "unknown");
  assert.throws(
    () => validateManifest({ ...fallback, schema_version: "2.0" }),
    ManifestValidationError,
  );
  const polluted = JSON.parse('{"schema_version":"1.0","manifest_version":"1.0","graph_id":"x","title":"x","theme":{"__proto__":{}}}');
  assert.throws(() => validateManifest(polluted), /опасный ключ/);
});

test("form subset reports enum, required, length and unknown fields", () => {
  const errors = validateForm(
    { decision: "later", extra: true },
    {
      type: "object",
      required: ["decision", "reason"],
      additionalProperties: false,
      properties: {
        decision: { enum: ["approved", "rejected"] },
        reason: { type: "string", maxLength: 4 },
      },
    },
  );
  assert.equal(errors["$.decision"], "Выберите значение из списка");
  assert.equal(errors["$.reason"], "Обязательное поле");
  assert.equal(errors["$.extra"], "Неизвестное поле");
});

test("layered layout is deterministic and honors rank/order hints", () => {
  const topology = {
    nodes: [{ id: "a" }, { id: "b" }, { id: "c" }],
    edges: [
      { source: "a", target: "c" },
      { source: "b", target: "c" },
    ],
  };
  const hints = { a: { rank: 0, order: 2 }, b: { rank: 0, order: 1 }, c: { rank: 2 } };
  const first = layeredLayout(topology, hints);
  const second = layeredLayout(topology, hints);
  assert.deepEqual(first, second);
  assert.ok((first.find((item) => item.id === "b")?.y ?? 99) < (first.find((item) => item.id === "a")?.y ?? 0));
  assert.ok((first.find((item) => item.id === "c")?.x ?? 0) > (first.find((item) => item.id === "a")?.x ?? 99));
});

test("a second run in the same thread reopens the sequence window", () => {
  let state = createRuntimeSnapshot("assistant", "demo", "1.0");
  for (const item of [
    event(1, "run.started"),
    event(2, "node.started", { nodeId: "prepare", executionId: "first" }),
    event(3, "node.completed", { nodeId: "prepare", executionId: "first" }),
    event(4, "run.completed"),
  ]) {
    state = runtimeReducer(state, { type: "event", event: item });
  }
  assert.equal(state.lastSequence, 4);
  assert.equal(state.runStatus, "completed");

  // Второй прогон нумеруется снова от единицы: без открытия окна все его
  // события отсеклись бы как опоздавшие.
  const second = [
    event(1, "run.started", {}, "run-2:1"),
    event(2, "node.started", { nodeId: "prepare", executionId: "second" }, "run-2:2"),
  ];
  for (const item of second) state = runtimeReducer(state, { type: "event", event: item });

  assert.equal(state.lastSequence, 2);
  assert.equal(state.runStatus, "running");
  assert.equal(state.events.length, 6);
  assert.deepEqual(Object.keys(state.executions), ["second"]);
});

test("a new run drops the stale interrupt of the previous one", () => {
  let state = createRuntimeSnapshot("assistant", "demo", "1.0");
  state = runtimeReducer(state, { type: "event", event: event(1, "run.started") });
  state = runtimeReducer(state, {
    type: "event",
    event: event(2, "interrupt.created", { interruptId: "old", value: { action: "publish" } }),
  });
  assert.equal(state.interrupts.length, 1);

  state = runtimeReducer(state, { type: "event", event: event(1, "run.started", {}, "run-2:1") });
  assert.deepEqual(state.interrupts, []);
  assert.equal(state.runStatus, "running");
});

test("reconcile with an equal snapshot keeps the previous state object", () => {
  const state = createRuntimeSnapshot("assistant", "demo", "1.0");
  // `useStream` при пустом треде отдаёт новый {} и новый [] на каждый рендер.
  const first = runtimeReducer(state, { type: "reconcile", snapshot: { state: { messages: [] } } });
  const second = runtimeReducer(first, { type: "reconcile", snapshot: { state: { messages: [] } } });
  assert.notEqual(first, state);
  assert.equal(second, first, "равный снимок обязан вернуть тот же объект");

  const changed = runtimeReducer(second, {
    type: "reconcile",
    snapshot: { state: { messages: [{ id: "m1" }] } },
  });
  assert.notEqual(changed, second);
});

test("reconcile ignores a deeply equal LangGraph snapshot with fresh object references", () => {
  const state = createRuntimeSnapshot("assistant", "agent", "1.0");
  const first = runtimeReducer(state, {
    type: "reconcile",
    snapshot: {
      state: {
        messages: [{ id: "m1", content: "готово", response_metadata: { tokens: 42 } }],
        artifacts: { requirements: "требования", api: "контракт" },
        cost: { usd: 0.12, counters: { input: 1000, output: 200 } },
      },
    },
  });

  // Так выглядит повторный getter useStream: содержание прежнее, но каждый
  // вложенный контейнер создан заново.
  const second = runtimeReducer(first, {
    type: "reconcile",
    snapshot: {
      state: {
        messages: [{ id: "m1", content: "готово", response_metadata: { tokens: 42 } }],
        artifacts: { requirements: "требования", api: "контракт" },
        cost: { usd: 0.12, counters: { input: 1000, output: 200 } },
      },
    },
  });

  assert.equal(second, first, "глубоко равный снимок не должен запускать новый render");
});

test("live adapter marks a task result as completed, not failed", () => {
  const factory = new LiveEventFactory("assistant", "demo", () => "thread");
  factory.startRun();
  factory.nodeStarted("prepare", "task-1", {});
  // Сервер кладёт в событие результата оба поля сразу — так выглядит успех.
  const payload = { id: "task-1", name: "prepare", error: null, result: { summary: "ok" } };
  const failed = payload.error !== null && payload.error !== undefined;
  assert.equal(failed, false);

  let state = createRuntimeSnapshot("assistant", "demo", "1.0");
  for (const item of [
    factory.startRun(),
    factory.nodeStarted("prepare", "task-1", {}),
    factory.nodeCompleted("prepare", "task-1", payload.result),
  ]) {
    state = runtimeReducer(state, { type: "event", event: item });
  }
  assert.equal(state.executions["task-1"].status, "completed");
  assert.equal(executionPath(state)[0], "prepare");
  assert.equal(visitCount(state, "prepare"), 1);
});

test("an input binding missing from surfaces still lands on a surface", () => {
  const manifest = {
    schema_version: "1.0",
    manifest_version: "1.0",
    graph_id: "demo",
    title: "demo",
    input: [
      { id: "question", target: "messages", widget: "chat-input" },
      { id: "task", target: "configurable.input_dir", widget: "task-picker" },
      { id: "extra", target: "configurable.extra", widget: "task-picker" },
    ],
    surfaces: [
      { id: "main", widgets: ["messages"] },
      { id: "right", widgets: ["extra"] },
    ],
  } as unknown as UiManifest;
  const inputs = manifest.input ?? [];
  // Не назван в surfaces — встаёт по типу виджета, а не исчезает.
  assert.equal(inputSurfaceOf(manifest, inputs[0]), "main");
  assert.equal(inputSurfaceOf(manifest, inputs[1]), "left");
  // Назван — манифест главнее умолчания.
  assert.equal(inputSurfaceOf(manifest, inputs[2]), "right");
});

test("input fields reach configurable by the target the manifest declares", () => {
  const manifest = {
    schema_version: "1.0",
    manifest_version: "1.0",
    graph_id: "demo",
    title: "demo",
    input: [
      { id: "question", target: "messages", widget: "chat-input" },
      { id: "task", target: "configurable.input_dir", widget: "task-picker" },
      { id: "document", target: "configurable.input_file", widget: "file-picker" },
      { id: "diagram", target: "configurable.diagram", widget: "file-picker" },
      { id: "__proto__", target: "configurable.__proto__", widget: "file-picker" },
    ],
  } as unknown as UiManifest;

  // Выбранный документ уезжает в прогон под тем именем, которое назвал
  // манифест: фронтенд про `input_file` ничего не знает.
  assert.deepEqual(
    configurableOf(manifest, { question: "разложи", task: "экспорт", document: "аналитика.md" }),
    { input_dir: "экспорт", input_file: "аналитика.md" },
  );

  // Снятый выбор — это не пустое значение, а отсутствие ключа: граф прочитает
  // папку целиком, а не файл с пустым именем. Пустым бывает и список:
  // у поля множественного выбора «не выбрано» выглядит как `[]`.
  assert.deepEqual(configurableOf(manifest, { task: "экспорт", document: "" }), {
    input_dir: "экспорт",
  });
  assert.deepEqual(configurableOf(manifest, { task: "экспорт", document: [] }), {
    input_dir: "экспорт",
  });

  // Комплект документов уезжает списком: выбрать можно и требования, и
  // контракт API, и раскладывать их надо вместе.
  assert.deepEqual(
    configurableOf(manifest, { task: "экспорт", document: ["требования.md", "контракт.md"] }),
    { input_dir: "экспорт", input_file: ["требования.md", "контракт.md"] },
  );

  // Поля разных графов не мешают друг другу и не подделывают прототип.
  assert.deepEqual(configurableOf(manifest, { diagram: "поток.drawio", __proto__: "x" }), {
    diagram: "поток.drawio",
  });
});

test("fallback manifest allows the actions it promises", () => {
  const manifest = fallbackManifest("unknown");
  const kinds = (manifest.actions ?? []).map((action) => action.kind);
  assert.deepEqual(kinds, ["thread.create", "run.start", "run.stop", "interrupt.resume"]);
  assert.equal(manifest.capabilities?.resume_interrupt, true);
});

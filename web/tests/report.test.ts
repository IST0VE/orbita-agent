import assert from "node:assert/strict";
import test from "node:test";

import type { ServerLogRecord } from "../src/api";
import { clearClient, clientEntries, describeError, reportClient } from "../src/lib/clientLog.ts";
import { buildReport, pickServerRecords, recordText } from "../src/lib/report.ts";

function record(id: number, overrides: Partial<ServerLogRecord> = {}): ServerLogRecord {
  return {
    id,
    seq: id,
    time: "2026-09-24T12:00:00.000Z",
    last_time: "2026-09-24T12:00:00.000Z",
    repeats: 1,
    level: "ERROR",
    logger: "langgraph_api.worker",
    message: `record ${id}`,
    exception: "",
    thread_id: "",
    run_id: "",
    graph_id: "",
    node: "",
    fields: {},
    ...overrides,
  };
}

const base = {
  now: new Date("2026-09-24T12:05:00.000Z"),
  page: "http://localhost:5173/",
  userAgent: "test-agent",
  client: [],
};

test("short report keeps the current thread's records before others", () => {
  const others = Array.from({ length: 40 }, (_, index) => record(index + 1));
  const own = record(3, { thread_id: "thread-1", message: "own failure" });
  const records = others.map((item) => (item.id === 3 ? own : item));

  const chosen = pickServerRecords(records, "thread-1", 5);

  assert.equal(chosen.length, 5);
  assert.ok(chosen.includes(own), "own-thread record was dropped for fresher unrelated ones");
  assert.deepEqual(chosen.map((item) => item.id), [3, 37, 38, 39, 40]);
});

test("short report lists only warnings and errors; the full one keeps everything", () => {
  const records = [
    record(1, { level: "INFO", message: "run started" }),
    record(2, { level: "WARNING", message: "gateway 429" }),
    record(3, { level: "ERROR", message: "node crashed", exception: "Traceback\nValueError: boom" }),
  ];
  const context = { ...base, server: { records } };

  const short = buildReport(context);
  const full = buildReport(context, { full: true });

  assert.ok(!short.includes("run started"));
  assert.ok(short.includes("gateway 429"));
  assert.ok(short.includes("ValueError: boom"));
  assert.ok(full.includes("run started"));
});

test("report carries the run context a developer needs to find the failure", () => {
  const text = buildReport({
    ...base,
    scenario: "Аналитика",
    graphId: "agent",
    threadId: "thread-42",
    runId: "run-7",
    runStatus: "failed",
    runError: "API модели отказал в доступе.",
    failures: [{ node: "analyst", message: "boom" }],
    server: {
      info: {
        app: "0.1.0",
        langgraph: "1.0",
        langgraph_api: "0.12.4",
        python: "3.12.7",
        platform: "Linux",
        provider: "openai",
        model: "deepseek",
      },
      records: [],
    },
  });

  for (const expected of ["thread-42", "run-7 — ошибка", "Аналитика (agent)", "analyst: boom", "orbita-agent 0.1.0", "openai / deepseek"]) {
    assert.ok(text.includes(expected), `report lacks ${expected}`);
  }
});

test("unavailable server log is stated instead of silently omitted", () => {
  const text = buildReport({ ...base, serverError: "HTTP 503" });

  assert.ok(text.includes("не получен: HTTP 503"));
});

test("long tracebacks keep their last lines in the short report", () => {
  const trace = Array.from({ length: 100 }, (_, index) => `line ${index}`).join("\n");

  const text = recordText(record(1, { exception: trace }), base.now, 40);

  assert.ok(text.includes("line 99"));
  assert.ok(!text.includes("line 10\n"));
  assert.ok(text.includes("пропущено строк: 60"));
});

test("client journal folds repeats and survives non-Error throws", () => {
  clearClient();
  reportClient("error", "запрос", "GET /info → 502");
  reportClient("error", "запрос", "GET /info → 502");
  reportClient("warning", "интерфейс", describeError({ code: 7 }).message);

  const entries = clientEntries();
  assert.equal(entries.length, 2);
  assert.equal(entries[0].repeats, 2);
  assert.equal(entries[1].message, '{"code":7}');
  clearClient();
});

test("the same traceback is printed once and an empty failures section is omitted", () => {
  // Так пишет сервер LangGraph: у второй записи сверху лишние кадры воркера,
  // а хвост, который попадает в короткий отчёт, тот же.
  const inner = Array.from({ length: 40 }, (_, index) => `  frame ${index}`).join("\n");
  const trace = `Traceback (most recent call last):\n${inner}\nopenai.AuthenticationError: 401`;
  const outer = `Traceback (most recent call last):\n  worker frame\n  worker frame\n${inner}\nopenai.AuthenticationError: 401`;
  const text = buildReport({
    ...base,
    server: {
      records: [
        record(1, { message: "Run encountered an error in graph", exception: trace }),
        record(2, { message: "Background run failed", exception: outer }),
      ],
    },
  });

  assert.equal(text.split("openai.AuthenticationError: 401").length - 1, 1);
  assert.ok(text.includes("трассировка та же"));
  assert.ok(!text.includes("Сбои узлов"));
});

"""A bounded scenario language compiled into k6 code; never execute model-supplied JS."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Step(StrictModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[\w -]+$")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str = Field(min_length=1, max_length=1000)
    body: dict | list | None = None
    expected_status: int = Field(default=200, ge=200, le=499)
    extract: dict[str, str] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def relative_path(self):
        if (not self.path.startswith("/") or self.path.startswith("//")
                or "\\" in self.path or "#" in self.path
                or any(ord(c) < 32 for c in self.path)):
            raise ValueError("path must be a relative HTTP path beginning with a single /")
        for variable, path in self.extract.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", variable):
                raise ValueError("invalid extraction variable")
            if not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", path):
                raise ValueError("extraction uses dotted JSON property paths")
        return self


class Plan(StrictModel):
    objective: str = Field(min_length=3, max_length=2000)
    target: str = Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")
    target_rps: int = Field(ge=1, le=10000)
    duration_seconds: int = Field(ge=1, le=3600)
    ramp_up_seconds: int = Field(default=0, ge=0, le=600)
    virtual_users: int = Field(default=5, ge=1, le=1000)
    sla_p95_ms: float = Field(gt=0, le=60000)
    sla_error_rate: float = Field(ge=0, lt=1)
    stop_p95_ms: float = Field(gt=0, le=60000)
    stop_error_rate: float = Field(gt=0, lt=1)
    steps: list[Step] = Field(min_length=1, max_length=10)
    dataset: list[dict] = Field(default_factory=lambda: [{}], min_length=1, max_length=1000)

    @model_validator(mode="after")
    def consistent(self):
        if self.stop_p95_ms < self.sla_p95_ms or self.stop_error_rate < self.sla_error_rate:
            raise ValueError("stop thresholds must not be stricter than SLA")
        if len({s.name for s in self.steps}) != len(self.steps):
            raise ValueError("step names must be unique")
        known = set.intersection(*(set(row) for row in self.dataset))
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", key) for key in known):
            raise ValueError("invalid dataset variable")
        for step in self.steps:
            used = set(re.findall(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}",
                                  json.dumps([step.path, step.body])))
            if used - known:
                raise ValueError("undefined scenario variables: " + ", ".join(sorted(used - known)))
            known.update(step.extract)
        if len(canonical(self.model_dump())) > 64000:
            raise ValueError("scenario and dataset exceed 64 KB")
        return self


def canonical(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(data: dict) -> str:
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def js_literal(text: str) -> str:
    """U+2028 and U+2029 are legal inside a JSON string but end a line for a JS parser.

    Applied only where JSON is embedded into generated code: `canonical` itself must keep
    producing the bytes that fingerprints and the saved scenario.json are built from.
    """
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def validate_plan(data: dict, capabilities: dict) -> tuple[Plan, dict]:
    plan = Plan.model_validate(data)
    target = capabilities.get("targets", {}).get(plan.target)
    if not isinstance(target, dict):
        raise ValueError("target is not configured on the runner")
    parsed = urlsplit(target["url"])
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("target URL must be an HTTP origin without credentials")
    for field in ("target_service", "environment", "namespace"):
        if not isinstance(target.get(field), str) or not target[field]:
            raise ValueError("target is missing " + field)
    limits = capabilities["limits"]
    if (plan.target_rps > limits["max_rps"] or plan.virtual_users > limits["max_vus"]
            or plan.duration_seconds + plan.ramp_up_seconds > limits["max_duration_seconds"]):
        raise ValueError("plan exceeds runner load limits")
    return plan, target


#: Что входит в одобряемый набор помимо самого сценария. Ключ цели («checkout»)
#: — это логическое имя, и одобрять его бессмысленно: адрес за ним меняется
#: правкой конфигурации runner, а нагрузка уезжает по новому.
COMMITTED_TARGET_FIELDS = ("url", "target_service", "environment", "namespace")
COMMITTED_LIMIT_FIELDS = ("max_rps", "max_vus", "max_duration_seconds")


def commitment(plan: Plan | dict, target: dict, capabilities: dict) -> dict:
    """
    Одобряемый набор: нормализованный сценарий, разрешённая цель и лимиты.

    Секретов здесь нет и быть не может: `target` приходит из `capabilities`,
    где остаются только адрес и координаты стенда, а имя переменной с токеном
    и тем более её значение туда не попадают.
    """
    data = plan.model_dump() if isinstance(plan, Plan) else dict(plan)
    limits = capabilities.get("limits") or {}
    return {
        "plan": json.loads(canonical(data)),
        "target": {field: target.get(field) for field in COMMITTED_TARGET_FIELDS},
        "limits": {field: limits.get(field) for field in COMMITTED_LIMIT_FIELDS},
        # Версия существенной конфигурации runner: engine и обещания вроде lease
        # и watchdog меняют смысл того, на что оператор согласился.
        "runner": {
            field: capabilities.get(field)
            for field in ("engine", "idempotent_start", "watchdog", "lease")
        },
    }


def commitment_of(data: dict, capabilities: dict) -> dict:
    """Набор по плану и текущим возможностям runner — одной проверкой."""
    plan, target = validate_plan(data, capabilities)
    return commitment(plan, target, capabilities)


def check_commitment(data: dict, capabilities: dict, approved: dict | None) -> dict:
    """
    Сверить одобренный набор с тем, что получается сейчас.

    Вызывается и в графе, и в runner: между предпросмотром и prepare меняется
    конфигурация runner, а между prepare и start — что угодно ещё. Проверка
    только в графе означала бы, что достаточно обратиться к runner мимо графа.
    """
    fresh = commitment_of(data, capabilities)
    if approved is None:
        return fresh
    if not isinstance(approved, dict) or fingerprint(approved) != fingerprint(fresh):
        raise ValueError("approved run parameters no longer match the runner configuration")
    return fresh


def compile_script(plan: Plan, target: dict) -> str:
    """Only JSON data is interpolated. Hosts, redirects, timeouts and code are controlled."""
    spec = js_literal(canonical(plan.model_dump()))
    origin = js_literal(json.dumps(target["url"].rstrip("/")))
    return f'''import http from 'k6/http';
import {{ check }} from 'k6';
import {{ Rate }} from 'k6/metrics';
const spec = {spec};
const origin = {origin};
const errors = new Rate('nt_errors');
const smoke = __ENV.NT_SMOKE === '1';
const stages = [];
if (spec.ramp_up_seconds) stages.push({{duration: spec.ramp_up_seconds + 's', target: spec.target_rps}});
stages.push({{duration: spec.duration_seconds + 's', target: spec.target_rps}});
export const options = {{
  discardResponseBodies: false,
  maxRedirects: 0,
  summaryTrendStats: ['avg', 'min', 'max', 'p(95)', 'p(99)'],
  scenarios: {{load: smoke ? {{executor: 'shared-iterations', vus: 1, iterations: 1, maxDuration: '30s'}} : {{
    executor: 'ramping-arrival-rate', startRate: spec.ramp_up_seconds ? 1 : spec.target_rps,
    timeUnit: spec.steps.length + 's', stages,
    preAllocatedVUs: spec.virtual_users, maxVUs: spec.virtual_users, gracefulStop: '5s'
  }}}},
  thresholds: smoke ? {{nt_errors: ['rate==0']}} : {{
    nt_errors: [{{threshold: 'rate<=' + spec.stop_error_rate, abortOnFail: true, delayAbortEval: '5s'}}],
    http_req_duration: [{{threshold: 'p(95)<=' + spec.stop_p95_ms, abortOnFail: true, delayAbortEval: '5s'}}]
  }}
}};
function fill(value, vars, encode = false) {{
  if (typeof value === 'string') return value.replace(/\\{{\\{{([A-Za-z][A-Za-z0-9_]*)\\}}\\}}/g,
    (_, key) => {{if (!(key in vars)) throw new Error('Missing variable');
      return encode ? encodeURIComponent(String(vars[key])) : String(vars[key]);}});
  if (Array.isArray(value)) return value.map(v => fill(v, vars));
  if (value !== null && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).map(([k,v]) => [k, fill(v, vars)]));
  return value;
}}
export default function () {{
  const vars = Object.assign({{}}, spec.dataset[(__VU + __ITER - 1) % spec.dataset.length]);
  for (const step of spec.steps) {{
    try {{
      const headers = {{'Content-Type': 'application/json'}};
      if (__ENV.NT_TARGET_TOKEN) headers.Authorization = 'Bearer ' + __ENV.NT_TARGET_TOKEN;
      const response = http.request(step.method, origin + fill(step.path, vars, true),
        step.body === null ? null : JSON.stringify(fill(step.body, vars)),
        {{headers, redirects: 0, timeout: '10s', tags: {{name: step.name}}}});
      let ok = response.status === step.expected_status;
      if (ok && Object.keys(step.extract).length) {{
        const body = response.json();
        for (const [key, path] of Object.entries(step.extract)) {{
          const value = path.split('.').reduce((v, p) => v == null ? undefined : v[p], body);
          if (value === undefined || value === null || typeof value === 'object') ok = false;
          else vars[key] = value;
        }}
      }}
      check(response, {{[step.name]: () => ok}});
      errors.add(!ok);
      if (!ok) return;
    }} catch (_) {{ errors.add(true); return; }}
  }}
}}
export function handleSummary(data) {{ return {{'summary.json': JSON.stringify(data)}}; }}
'''

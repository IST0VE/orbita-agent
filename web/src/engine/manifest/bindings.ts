import type { Condition, InputBinding, InterruptBinding, SurfaceId, UiManifest } from "./types";

const UNSAFE = new Set(["__proto__", "prototype", "constructor"]);

function tokens(path: string): string[] {
  if (!path) return [];
  const parts = path.startsWith("/")
    ? path
        .slice(1)
        .split("/")
        .map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"))
    : path.split(".");
  if (parts.some((part) => UNSAFE.has(part))) throw new Error("unsafe binding path");
  return parts.filter(Boolean);
}

export function resolveBinding(root: unknown, path: string): { found: boolean; value: unknown } {
  let current = root;
  for (const token of tokens(path)) {
    if (current === null || typeof current !== "object") return { found: false, value: undefined };
    if (!Object.prototype.hasOwnProperty.call(current, token)) return { found: false, value: undefined };
    current = (current as Record<string, unknown>)[token];
  }
  return { found: true, value: current };
}

function same(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

export function conditionMatches(condition: Condition | undefined, root: unknown): boolean {
  if (!condition) return true;
  if (condition.not) return !conditionMatches(condition.not, root);
  if (condition.all) return condition.all.every((item) => conditionMatches(item, root));
  if (condition.any) return condition.any.some((item) => conditionMatches(item, root));
  const resolved = resolveBinding(root, condition.path ?? "");
  if (condition.exists !== undefined && resolved.found !== condition.exists) return false;
  if (Object.prototype.hasOwnProperty.call(condition, "equals") && !same(resolved.value, condition.equals)) {
    return false;
  }
  if (condition.in && !condition.in.some((item) => same(item, resolved.value))) return false;
  return true;
}

export function matchInterrupt(rules: InterruptBinding[] = [], value: unknown): InterruptBinding | undefined {
  return [...rules]
    .sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0))
    .find((rule) => conditionMatches(rule.match, value));
}

/** Поверхности по умолчанию для встроенных input-виджетов. */
const INPUT_SURFACE: Record<string, SurfaceId> = { "task-picker": "left", "chat-input": "main" };

/**
 * Куда встаёт input-биндинг.
 *
 * Если манифест сам назвал его в `surfaces[].widgets` — верим манифесту.
 * Если нет, поверхность выбирается по типу виджета: список `widgets` служит
 * раскладкой, а не пропуском, и забытый в нём id не должен молча убирать из
 * интерфейса единственное поле ввода.
 */
export function inputSurfaceOf(manifest: UiManifest, input: InputBinding): SurfaceId {
  const declared = manifest.surfaces?.find((item) => item.widgets?.includes(input.id));
  return declared?.id ?? INPUT_SURFACE[input.widget] ?? "main";
}

/**
 * Значения `configurable` прогона из полей ввода манифеста.
 *
 * Соответствие «поле — ключ конфигурации» объявлено в самом манифесте
 * (`input[].target`), поэтому берётся оттуда: граф декомпозиции добавляет
 * `configurable.jira_project`, граф разбора схем — `configurable.diagram`,
 * и фронтенд об этом не знает. Раньше здесь стояло одно зашитое соответствие
 * `task -> input_dir`, и любое второе поле требовало правки кода.
 *
 * Пустой выбор не уезжает: у полей выбора он значит «не выбрано», а не
 * «выбрано ничто», и разницу видит уже граф — сняли выбор документа, читается
 * вся папка. Пустым бывает и список: поле множественного выбора хранит массив,
 * и `[]` в нём — то же самое «не выбрано», что и пустая строка у одиночного.
 */
export function configurableOf(
  manifest: UiManifest,
  inputs: Record<string, unknown>,
): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  for (const input of manifest.input ?? []) {
    const [scope, name] = String(input.target ?? "").split(".");
    const value = inputs[input.id];
    if (scope !== "configurable" || !name || value === undefined || value === "") continue;
    if (Array.isArray(value) && value.length === 0) continue;
    if (UNSAFE.has(name)) continue;
    config[name] = value;
  }
  return config;
}

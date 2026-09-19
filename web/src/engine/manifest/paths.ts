/**
 * Пути биндингов: один разбор на валидацию и на разрешение значения.
 *
 * Раньше правил было два. `validate.ts` проверял, что `state[].path` — строка,
 * и на этом останавливался; `bindings.ts` разбирал ту же строку на токены и
 * бросал на `__proto__`. Манифест с путём `__proto__.x` проходил проверку
 * целиком, а падал уже во время сборки поверхности — вне границы ошибок
 * виджета, то есть пустым экраном вместо предупреждения.
 *
 * Поэтому разбор здесь один, и обе стороны зовут его: что валидатор принял,
 * то resolver обязан разобрать.
 */

const UNSAFE = new Set(["__proto__", "prototype", "constructor"]);

export class BindingPathError extends Error {
  constructor(path: string) {
    super(`unsafe binding path: ${path}`);
    this.name = "BindingPathError";
  }
}

/**
 * Токены пути.
 *
 * Две записи: JSON Pointer (`/messages/0/text`, с экранированием `~1` и `~0`)
 * и точечная (`messages.0.text`). Обе приходят из манифеста, и разбираются
 * они одинаково для валидатора и для чтения значения.
 */
export function bindingTokens(path: string): string[] {
  if (!path) return [];
  const parts = path.startsWith("/")
    ? path
        .slice(1)
        .split("/")
        .map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"))
    : path.split(".");
  if (parts.some((part) => UNSAFE.has(part))) throw new BindingPathError(path);
  return parts.filter(Boolean);
}

/** Путь разбирается без ошибки: проверка для валидатора манифеста. */
export function isSafeBindingPath(path: unknown): path is string {
  if (typeof path !== "string") return false;
  try {
    bindingTokens(path);
    return true;
  } catch {
    return false;
  }
}

/** Опасное имя ключа: `configurable.__proto__` не уезжает в конфигурацию прогона. */
export function isUnsafeToken(token: string): boolean {
  return UNSAFE.has(token);
}

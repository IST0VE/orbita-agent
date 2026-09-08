import type { JsonSchema, JsonValue } from "../manifest/types";

export type FormErrors = Record<string, string>;

export function validateForm(value: unknown, schema: JsonSchema, path = "$", depth = 0): FormErrors {
  const errors: FormErrors = {};
  if (depth > 10) return { [path]: "Вложенность больше 10 уровней" };
  if (schema.const !== undefined && value !== schema.const) errors[path] = "Значение не совпадает с const";
  if (schema.enum && !schema.enum.some((item) => JSON.stringify(item) === JSON.stringify(value))) {
    errors[path] = "Выберите значение из списка";
  }
  const typeOk: Record<string, boolean> = {
    string: typeof value === "string",
    number: typeof value === "number" && Number.isFinite(value),
    integer: typeof value === "number" && Number.isInteger(value),
    boolean: typeof value === "boolean",
    object: !!value && typeof value === "object" && !Array.isArray(value),
    array: Array.isArray(value),
    null: value === null,
  };
  if (schema.type && !typeOk[schema.type]) return { [path]: `Ожидался тип ${schema.type}` };
  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) errors[path] = "Строка слишком короткая";
    if (schema.maxLength !== undefined && value.length > schema.maxLength) errors[path] = "Строка слишком длинная";
    if (schema.pattern) {
      try {
        if (!new RegExp(schema.pattern).test(value)) errors[path] = "Строка не соответствует формату";
      } catch {
        errors[path] = "Манифест содержит некорректный pattern";
      }
    }
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) errors[path] = `Минимум: ${schema.minimum}`;
    if (schema.maximum !== undefined && value > schema.maximum) errors[path] = `Максимум: ${schema.maximum}`;
  }
  if (Array.isArray(value)) {
    if (value.length > Math.min(schema.maxItems ?? 1000, 1000)) errors[path] = "Слишком много элементов";
    value.forEach((item, index) => Object.assign(errors, validateForm(item, schema.items ?? {}, `${path}[${index}]`, depth + 1)));
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    for (const key of schema.required ?? []) if (!(key in record)) errors[`${path}.${key}`] = "Обязательное поле";
    for (const [key, child] of Object.entries(record)) {
      const childSchema = schema.properties?.[key];
      if (childSchema) Object.assign(errors, validateForm(child, childSchema, `${path}.${key}`, depth + 1));
      else if (schema.additionalProperties === false) errors[`${path}.${key}`] = "Неизвестное поле";
    }
  }
  return errors;
}

export function schemaDefaults(schema: JsonSchema): JsonValue {
  if (schema.default !== undefined) return schema.default;
  if (schema.const !== undefined) return schema.const;
  if (schema.enum?.length) return schema.enum[0];
  if (schema.type === "object" || schema.properties) {
    return Object.fromEntries(Object.entries(schema.properties ?? {}).map(([key, child]) => [key, schemaDefaults(child)]));
  }
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  if (schema.type === "number" || schema.type === "integer") return 0;
  return "";
}

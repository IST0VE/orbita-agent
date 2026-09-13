/**
 * Мелочи, общие всем виджетам: приведение к строке и разбор сообщения.
 *
 * Отдельным файлом, потому что нужны всем группам сразу. Оставить их в любой
 * из групп значило бы, что три остальные импортируют, например, «виджеты
 * подтверждения» ради функции `text`.
 */

export function text(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return String(value);
}

/**
 * Значение поля выбора списком имён.
 *
 * Поле хранит либо строку (выбор один — схема draw.io), либо массив (выбор
 * множественный — комплект документации). Разбирать это в каждом виджете
 * значит написать одно и то же дважды и один раз ошибиться.
 */
export function names(value: unknown): string[] {
  if (typeof value === "string") return value ? [value] : [];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item !== "");
}

export function messageText(message: unknown): string {
  if (!message || typeof message !== "object") return text(message);
  const content = (message as { content?: unknown }).content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) =>
      typeof part === "string"
        ? part
        : part && typeof part === "object" && (part as { type?: string }).type === "text"
          ? text((part as { text?: unknown }).text)
          : "",
    )
    .join("");
}

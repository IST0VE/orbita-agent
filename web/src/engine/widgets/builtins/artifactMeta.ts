/**
 * Как показывать и отдавать файлом документ из `artifacts`.
 *
 * Ключ состояния — имя для кода (`survey`, `diagram_ids`), человеку нужно
 * название этапа. Названия и формат объявляет манифест
 * (`options.documents`): описание конвейера знает сервер, и повторять его
 * здесь значило бы завести второй список ролей, который разойдётся с первым.
 * Не объявлено — остаётся ключ и Markdown.
 *
 * Отдельно от виджета, чтобы правило проверялось тестом движка без JSX.
 */
import type { WidgetBinding } from "../../manifest/types";

export type ArtifactFormat = "markdown" | "json" | "text";

export const ARTIFACT_FILES: Record<ArtifactFormat, { extension: string; type: string }> = {
  markdown: { extension: "md", type: "text/markdown;charset=utf-8" },
  json: { extension: "json", type: "application/json;charset=utf-8" },
  text: { extension: "txt", type: "text/plain;charset=utf-8" },
};

export function artifactMeta(
  binding: WidgetBinding,
  key: string,
  body: string,
): { title: string; format: ArtifactFormat } {
  const declared = (binding.options?.documents ?? {}) as Record<string, { title?: unknown; format?: unknown }>;
  const entry = declared[key] ?? {};
  let format: ArtifactFormat = entry.format === "json" || entry.format === "text" ? entry.format : "markdown";
  // Данные схемы сверх потолка приезжают обрезанными и с отметкой в конце
  // (`drawio.as_json`). Файл .json, который ничем не открывается, хуже
  // честного .txt с той же отметкой.
  if (format === "json") {
    try {
      JSON.parse(body);
    } catch {
      format = "text";
    }
  }
  return { title: typeof entry.title === "string" && entry.title ? entry.title : key, format };
}

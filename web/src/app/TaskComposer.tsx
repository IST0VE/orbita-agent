/**
 * Поле задачи внизу рабочей области.
 *
 * Раньше это была полоса во всю ширину экрана с собственным заголовком,
 * плашкой проекта и лентой сообщений внутри — треть высоты окна под одно
 * текстовое поле, и так до первого запуска, и после него тоже.
 *
 * Теперь это композер: строка контекста, поле и главное действие. Лента
 * прогона уехала в консоль выполнения, которая открывается сама, когда
 * появляется, что показывать.
 */

import { Fragment, type ReactNode } from "react";

import { surfaceItems } from "../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { Paperclip } from "../ui/icons";

/**
 * Что уедет в прогон вместе с вопросом.
 *
 * Собирается из полей ввода манифеста: имя поля и то, что в нём выбрано.
 * Индикатор нужен ровно потому, что материалы выбирают в левой колонке, а
 * запускают отсюда, — и между этими двумя действиями легко забыть, что
 * выбрано было в прошлый раз.
 */
function contextLine(manifest: UiManifest, inputs: Record<string, unknown>): string[] {
  const parts: string[] = [];
  for (const input of manifest.input ?? []) {
    if (input.widget === "chat-input") continue;
    const value = inputs[input.id];
    if (Array.isArray(value)) {
      const names = value.filter((item): item is string => typeof item === "string" && item !== "");
      if (names.length === 1) parts.push(names[0]);
      else if (names.length) parts.push(`${names.length} файла`);
    } else if (typeof value === "string" && value) {
      parts.push(value);
    }
  }
  return parts;
}

export function TaskComposer({
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  onPickContext,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  onPickContext: () => void;
}) {
  const field = surfaceItems({ surface: "main", manifest, runtime, context, inputs, onInput, onAction })
    .find((item) => item.widget === "chat-input");
  if (!field) return null;
  const parts = contextLine(manifest, inputs);

  return (
    <section className="task-composer" aria-label="Задача для ORBITA">
      <button
        type="button"
        className="composer-context"
        title="Материалы, которые уедут в прогон. Открыть левую колонку"
        onClick={onPickContext}
      >
        <Paperclip size={14} aria-hidden="true" />
        {parts.length ? (
          <span className="truncate">Контекст: {parts.join(" · ")}</span>
        ) : (
          <span className="truncate">Контекст не выбран — конвейер прочитает материалы целиком</span>
        )}
      </button>
      <Fragment>{field.node as ReactNode}</Fragment>
    </section>
  );
}

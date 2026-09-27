/**
 * Поле задачи внизу рабочей области.
 *
 * Раньше это была полоса во всю ширину экрана с собственным заголовком,
 * плашкой проекта и лентой сообщений внутри — треть высоты окна под одно
 * текстовое поле, и так до первого запуска, и после него тоже.
 *
 * Теперь это композер: строка контекста, поле и главное действие. Лента
 * прогона уехала в консоль выполнения, которая открывается сама, когда
 * появляется, что показывать. Скрепка загружает файлы в открытый чат — те же,
 * что видны справа во вкладке «Чат».
 */

import { Fragment, useRef, useState, type ReactNode } from "react";

import { surfaceItems } from "../engine/surfaces/SurfaceRenderer";
import type { ChatFilesContext, SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { Paperclip, Upload } from "../ui/icons";

/**
 * Что уедет в прогон вместе с вопросом.
 *
 * Собирается из полей ввода манифеста: имя поля и то, что в нём выбрано.
 * Индикатор нужен ровно потому, что файлы отмечают в правой колонке, а
 * запускают отсюда, — и между этими двумя действиями легко забыть, что
 * отмечено было в прошлый раз. Поле файлов чата значения не хранит: его
 * значение постоянное (`@chat`), и показывать его незачем.
 */
function contextLine(manifest: UiManifest, inputs: Record<string, unknown>): string[] {
  const parts: string[] = [];
  for (const input of manifest.input ?? []) {
    if (input.widget === "chat-input" || input.widget === "chat-files") continue;
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

/** Что сказать о файлах чата одной строкой. */
function filesLine(chat: ChatFilesContext | undefined, picked: string[]): string {
  const count = chat?.files?.length ?? 0;
  if (!count) return "Файлов в чате нет — прикрепите их скрепкой или перетащите в правую колонку";
  const files = `Файлов в чате: ${count}`;
  return picked.length ? `${files} · отмечено: ${picked.join(" · ")}` : `${files} · прочитаются все`;
}

export function TaskComposer({
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  onPickContext,
  chat,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  onPickContext: () => void;
  chat?: ChatFilesContext;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const field = surfaceItems({ surface: "main", manifest, runtime, context, inputs, onInput, onAction })
    .find((item) => item.widget === "chat-input");
  if (!field) return null;
  // Файлы чата — только у сценариев, которые их читают: у НТ поле файлов не
  // объявлено, и скрепка там обещала бы то, чего граф не сделает.
  const takesFiles = (manifest.input ?? []).some((input) => input.widget === "chat-files");
  const parts = contextLine(manifest, inputs);
  const running = runtime.runStatus === "running" || runtime.runStatus === "queued";

  const upload = (files: File[]) => {
    if (!chat || !files.length) return;
    setUploading(true);
    setError("");
    chat.upload(files)
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setUploading(false));
  };

  return (
    <section className="task-composer" aria-label="Задача для ORBITA">
      <div className="composer-context-row">
        {takesFiles && chat ? (
          <button
            type="button"
            className="btn-ghost btn-icon btn-sm composer-attach"
            disabled={running || uploading}
            aria-label="Прикрепить файлы к чату"
            title="Прикрепить файлы к этому чату"
            onClick={() => picker.current?.click()}
          >
            <Upload size={15} aria-hidden="true" />
          </button>
        ) : null}
        <button
          type="button"
          className="composer-context"
          title="Файлы и параметры, которые уедут в прогон. Открыть файлы чата"
          onClick={onPickContext}
        >
          <Paperclip size={14} aria-hidden="true" />
          <span className="truncate">
            {uploading
              ? "Загрузка файлов…"
              : takesFiles ? filesLine(chat, parts) : parts.length ? `Контекст: ${parts.join(" · ")}` : "Задача ставится текстом"}
          </span>
        </button>
        {takesFiles && chat ? (
          <input
            ref={picker}
            type="file"
            multiple
            hidden
            accept={chat.limits?.suffixes.join(",")}
            onChange={(event) => {
              const list = Array.from(event.currentTarget.files ?? []);
              event.currentTarget.value = "";
              upload(list);
            }}
          />
        ) : null}
      </div>
      {error ? <span className="error composer-error" role="alert">{error}</span> : null}
      <Fragment>{field.node as ReactNode}</Fragment>
    </section>
  );
}

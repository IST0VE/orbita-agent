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
 *
 * Высоту поля тянут за верхнюю кромку, как у консоли: уголок поля, которым
 * это делалось раньше, мелкий, и тянуть его надо вниз — прочь от схемы, хотя
 * поле растёт вверх. Выбранная высота переживает перезагрузку, двойной щелчок
 * по кромке возвращает две строки.
 *
 * Выбранная высота — пожелание, а не приказ: место под поле меняется и без
 * ручки — открылась консоль, уменьшилось окно. Показывается она не выше того,
 * что оставляет схеме её минимум, и пересчитывается при каждом таком
 * изменении. Иначе схема сжималась в полоску, а кнопка отправки уезжала за
 * край экрана, и перезагрузка возвращала ту же высоту.
 */

import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
} from "react";

import { surfaceItems } from "../engine/surfaces/SurfaceRenderer";
import type { ChatFilesContext, SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { Paperclip, Upload } from "../ui/icons";

const HEIGHT_KEY = "orbita.composer.height";
/** Две строки текста: ниже поле перестаёт быть полем. */
const MIN_HEIGHT = 52;
/** Столько схеме оставляют всегда — тот же минимум, что у `.app-body`. */
const SCHEMA_MIN = 220;

/**
 * Самое высокое поле, при котором схеме над ним остаётся её минимум.
 *
 * Считается от рабочей области целиком, а не от схемы: поле, которое уже
 * выше допустимого, сжало схему, и по ней предел вышел бы ещё меньше.
 */
function roomFor(composer: HTMLElement): number {
  const main = composer.parentElement;
  const textarea = composer.querySelector("textarea");
  if (!main || !textarea) return Infinity;
  const gap = parseFloat(getComputedStyle(main).rowGap) || 0;
  // Всё в композере, кроме самого поля: строка файлов, кнопки, ошибка.
  const chrome = composer.getBoundingClientRect().height - textarea.getBoundingClientRect().height;
  return Math.floor(main.clientHeight - gap - chrome - SCHEMA_MIN);
}

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
  const [height, setHeight] = useState(() => Number(localStorage.getItem(HEIGHT_KEY)) || 0);
  const [box, setBox] = useState<HTMLElement | null>(null);
  const [room, setRoom] = useState(Infinity);

  useEffect(() => {
    if (height) localStorage.setItem(HEIGHT_KEY, String(height));
  }, [height]);

  // Рабочая область меняется с окном и консолью, композер — со строкой
  // ошибки. Пересчёт от собственной высоты поля не зацикливается: предел от
  // неё не зависит, и повторное значение React не рисует.
  useLayoutEffect(() => {
    const main = box?.parentElement;
    if (!box || !main) return;
    const measure = () => setRoom(roomFor(box));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(main);
    observer.observe(box);
    return () => observer.disconnect();
  }, [box]);

  const shown = height ? Math.max(MIN_HEIGHT, Math.min(height, room)) : 0;

  const startResize = useCallback((event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const composer = event.currentTarget.parentElement;
    const textarea = composer?.querySelector("textarea");
    if (!composer || !textarea) return;
    const start = textarea.getBoundingClientRect().height;
    const origin = event.clientY;
    // Поле растёт за счёт схемы над ним; уже минимума её не сжимаем.
    const limit = Math.max(MIN_HEIGHT, roomFor(composer));
    const move = (moving: globalThis.PointerEvent) => {
      setHeight(Math.round(Math.min(limit, Math.max(MIN_HEIGHT, start + origin - moving.clientY))));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }, []);

  const resetHeight = useCallback(() => {
    setHeight(0);
    localStorage.removeItem(HEIGHT_KEY);
  }, []);

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
    <section
      ref={setBox}
      className="task-composer"
      aria-label="Задача для ORBITA"
      data-sized={shown ? "true" : undefined}
      style={shown ? ({ "--composer-height": `${shown}px` } as CSSProperties) : undefined}
    >
      <div
        className="composer-resizer"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Высота поля задачи"
        title="Потяните, чтобы изменить высоту. Двойной щелчок — сбросить."
        onPointerDown={startResize}
        onDoubleClick={resetHeight}
      />
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

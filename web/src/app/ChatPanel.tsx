/**
 * Вкладка «Чат» правой колонки: весь открытый чат в одном месте.
 *
 * Раньше чат был разрезан надвое. Файлы, параметры и результаты стояли
 * здесь, а сам разговор — задача оператора и ответы ролей — в консоли
 * «Выполнение» внизу экрана. Во время прогона файлы не нужны, а разговор
 * нужен, и консоль приходилось то открывать, то закрывать, отнимая высоту
 * у схемы. Теперь это одна лента, как в любом чате:
 *
 * - сверху материалы — файлы чата и параметры прогона. Пока разговора нет,
 *   они и есть содержание чата; с началом прогона сворачиваются в строку и
 *   раскрываются по щелчку;
 * - ниже разговор: задача, ответы ролей, вызовы инструментов — вживую;
 * - в конце то, что прогон создал: публикации, документы этапов, архив.
 *
 * Лента держится низа (`useStickyScroll`): новое появляется снизу, и
 * догонять его колесом не нужно, пока не отлистал назад сам.
 *
 * Состав по-прежнему задаёт манифест. Поверхность `left` — материалы и
 * результаты (имя осталось от прежней раскладки), `main` — разговор. Файлы
 * чата — материалы, остальные поля ввода — параметры, а всё, что приезжает
 * из состояния, — результаты.
 */

import { Fragment, useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";

import { surfaceItems, type SurfaceItem } from "../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { useStickyScroll } from "../hooks/useStickyScroll";
import { ChevronDown, ChevronRight, MessageSquare, Paperclip } from "../ui/icons";

type SectionId = "input" | "workspace" | "output";

/** Виджеты материалов: файлы чата и старое дерево папок, если манифест его назовёт. */
const MATERIAL_WIDGETS = new Set(["chat-files", "task-picker"]);

/** Файлы — материалы, прочий ввод — параметры, состояние — результаты. */
function sectionOf(item: SurfaceItem): SectionId {
  if (item.kind === "input") return MATERIAL_WIDGETS.has(item.widget) ? "input" : "workspace";
  return "output";
}

/** Сколько отмечено в полях выбора файлов: строка или список имён. */
function pickedCount(manifest: UiManifest, inputs: Record<string, unknown>): number {
  return (manifest.input ?? [])
    .filter((input) => input.widget === "file-picker")
    .reduce((total, input) => {
      const value = inputs[input.id];
      return total + (Array.isArray(value) ? value.length : typeof value === "string" && value ? 1 : 0);
    }, 0);
}

const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes("Files");

export type ChatScroll = { target: "input" | "output"; nonce: number } | null;

export function ChatPanel({
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  surfaceKey,
  scrollRequest,
  ready,
  materialsOpen,
  onMaterialsOpen,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  surfaceKey: string;
  /** Запрос навигации: к какому разделу подвести взгляд и когда. */
  scrollRequest: ChatScroll;
  ready: boolean;
  /**
   * Раскрыты ли материалы. `null` — решает сам чат: раскрыты, пока разговора
   * нет, и свёрнуты, когда он есть. Щелчок оператора — уже его решение.
   */
  materialsOpen: boolean | null;
  onMaterialsOpen: (open: boolean) => void;
}) {
  const feed = useStickyScroll<HTMLDivElement>();
  const materialsBox = useRef<HTMLElement>(null);
  const [dropError, setDropError] = useState("");

  const left = ready
    ? [
        ...surfaceItems({ surface: "left", manifest, runtime, context, inputs, onInput, onAction }),
        // Поле ввода, которому манифест не назначил поверхность, попадает в
        // «main» — туда же, где стоит поле задачи. Задача — это вопрос, а
        // остальные поля — параметры, и место им среди параметров.
        ...surfaceItems({ surface: "main", manifest, runtime, context, inputs, onInput, onAction })
          .filter((item) => item.kind === "input" && item.widget !== "chat-input"),
      ]
    : [];
  const conversation = ready
    ? surfaceItems({ surface: "main", manifest, runtime, context, inputs, onInput, onAction })
        .filter((item) => item.kind === "state")
    : [];
  const files = left.filter((item) => sectionOf(item) === "input");
  const params = left.filter((item) => sectionOf(item) === "workspace");
  const results = left.filter((item) => sectionOf(item) === "output");
  const materials = files.length + params.length > 0;

  // Разговор есть, если в ленте есть хоть одно сообщение: пустой тред — это
  // ещё подготовка, и главное в нём — материалы.
  const talking = conversation.some((item) => !item.empty);
  const open = materialsOpen ?? !talking;

  const nonce = scrollRequest?.nonce ?? 0;
  const target = scrollRequest?.target;
  useEffect(() => {
    if (!nonce) return;
    if (target === "output") {
      feed.current
        ?.querySelector<HTMLElement>('[data-section="output"]')
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    materialsBox.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    // `feed` — ссылка из хука, она не меняется между отрисовками.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, target]);

  // `null` — список ещё едет, `undefined` — файлов чата в этом окне нет вовсе.
  const chatFiles = context.chat?.files;
  const picked = pickedCount(manifest, inputs);
  const summary = [
    chatFiles === undefined ? "" : chatFiles === null ? "…" : String(chatFiles.length),
    picked ? `отмечено ${picked}` : "",
  ].filter(Boolean).join(" · ");

  /*
   * Файл можно бросить на чат и тогда, когда материалы свёрнуты: список
   * раскрывается, как только над колонкой появился файл. Отпущенный мимо
   * списка файл загружает сама колонка — иначе он открылся бы в браузере
   * вместо того, чтобы попасть в чат.
   */
  const upload = context.chat?.upload;
  const dropProps = upload && files.length
    ? {
        onDragEnter: (event: DragEvent<HTMLDivElement>) => {
          if (hasFiles(event) && !open) onMaterialsOpen(true);
        },
        onDragOver: (event: DragEvent<HTMLDivElement>) => {
          if (hasFiles(event)) event.preventDefault();
        },
        onDrop: (event: DragEvent<HTMLDivElement>) => {
          // Список файлов уже принял файл сам — второй загрузки не нужно.
          if (!hasFiles(event) || event.defaultPrevented) return;
          event.preventDefault();
          setDropError("");
          upload(Array.from(event.dataTransfer.files)).catch((reason: Error) => setDropError(reason.message));
        },
      }
    : {};

  const node = (item: SurfaceItem) => (
    // Ключ включает сценарий: при смене конвейера виджеты пересоздаются, а
    // не донашивают чужой черновик и чужой список.
    <Fragment key={`${surfaceKey}:${item.key}`}>{item.node as ReactNode}</Fragment>
  );

  return (
    <div className="chat-panel" data-talking={talking ? "true" : "false"} {...dropProps}>
      {!ready ? (
        <div className="engine-widget">
          <span className="skeleton" style={{ width: "60%" }} />
          <span className="skeleton" style={{ width: "85%" }} />
          <span className="skeleton" style={{ width: "45%" }} />
        </div>
      ) : null}

      {materials ? (
        <section
          className="chat-panel-materials"
          data-open={open ? "true" : "false"}
          data-section="input"
          ref={materialsBox}
        >
          <button
            type="button"
            className="chat-panel-toggle"
            aria-expanded={open}
            title={open ? "Свернуть файлы и параметры" : "Показать файлы и параметры чата"}
            onClick={() => onMaterialsOpen(!open)}
          >
            {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
            <Paperclip size={14} aria-hidden="true" />
            <span className="chat-panel-toggle-label">
              {files.length ? (params.length ? "Файлы и параметры" : "Файлы чата") : "Параметры прогона"}
            </span>
            {summary ? <span className="hint">{summary}</span> : null}
          </button>
          {open ? (
            <div className="chat-panel-materials-body">
              {files.map(node)}
              {params.length ? (
                <section className="sidebar-section" data-section="workspace">
                  <h2 className="eyebrow sidebar-section-title" title="С чем именно работает ORBITA в этом прогоне">
                    Параметры прогона
                  </h2>
                  {params.map(node)}
                </section>
              ) : null}
            </div>
          ) : null}
          {dropError ? <span className="error" role="alert">{dropError}</span> : null}
        </section>
      ) : null}

      <div className="chat-panel-feed" ref={feed}>
        {ready && !talking && !results.some((item) => !item.empty) ? (
          <p className="hint chat-panel-empty">
            <MessageSquare size={14} aria-hidden="true" />
            Напишите задачу внизу: ход прогона и ответы ролей появятся здесь.
          </p>
        ) : null}
        {talking ? (
          <div className="chat-panel-conversation" aria-label="Разговор">
            {conversation.map(node)}
          </div>
        ) : null}
        {results.length ? (
          <section className="sidebar-section" data-section="output">
            <h2 className="eyebrow sidebar-section-title" title="Документы, задачи и публикации, созданные ORBITA">
              Результаты
            </h2>
            {results.map(node)}
          </section>
        ) : null}
      </div>
    </div>
  );
}

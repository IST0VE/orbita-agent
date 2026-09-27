/**
 * Вкладка «Чат» правой колонки: файлы, параметры и результаты открытого чата.
 *
 * Раньше это была левая колонка, и первым в ней стояло дерево общих папок
 * задач. Папки видел каждый вошедший, поэтому колонку заняли чаты
 * (`ChatSidebar`), а всё, что относится к одному чату, переехало сюда:
 * загруженные в него файлы, параметры прогона и то, что прогон создал.
 *
 * Состав по-прежнему задаёт манифест — поверхность `left`: имя осталось от
 * прежней раскладки, а раздача по разделам не требует ни новых полей, ни
 * знания фронтом конкретных графов. Файлы чата — материалы, остальные поля
 * ввода — параметры прогона, а всё, что приезжает из состояния, — результаты.
 */

import { Fragment, useEffect, useRef, type ReactNode } from "react";

import { surfaceItems, type SurfaceItem } from "../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { Inbox } from "../ui/icons";
import { EmptyState } from "../ui";

type SectionId = "input" | "workspace" | "output";

const SECTIONS: Array<{ id: SectionId; title: string; hint: string }> = [
  // Заголовок у файлов свой, внутри виджета: рядом с ним кнопки загрузки.
  { id: "input", title: "", hint: "Файлы, которые вы загрузили в этот чат" },
  { id: "workspace", title: "Параметры прогона", hint: "С чем именно работает ORBITA в этом прогоне" },
  { id: "output", title: "Результаты", hint: "Документы, задачи и публикации, созданные ORBITA" },
];

/** Виджеты материалов: файлы чата и старое дерево папок, если манифест его назовёт. */
const MATERIAL_WIDGETS = new Set(["chat-files", "task-picker"]);

/** Файлы — материалы, прочий ввод — параметры, состояние — результаты. */
function sectionOf(item: SurfaceItem): SectionId {
  if (item.kind === "input") return MATERIAL_WIDGETS.has(item.widget) ? "input" : "workspace";
  return "output";
}

export type SidebarScroll = { target: SectionId; nonce: number } | null;

export function ChatMaterials({
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  surfaceKey,
  scrollRequest,
  ready,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  surfaceKey: string;
  /** Запрос навигации: к какому разделу подвести взгляд и когда. */
  scrollRequest: SidebarScroll;
  ready: boolean;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const nonce = scrollRequest?.nonce ?? 0;
  const target = scrollRequest?.target;

  useEffect(() => {
    const node = scroll.current;
    if (!node || !nonce) return;
    const section = node.querySelector<HTMLElement>(`[data-section="${target}"]`);
    if (section) section.scrollIntoView({ behavior: "smooth", block: "nearest" });
    else node.scrollTo({ top: 0, behavior: "smooth" });
  }, [nonce, target]);

  const items = ready
    ? [
        ...surfaceItems({ surface: "left", manifest, runtime, context, inputs, onInput, onAction }),
        // Поле ввода, которому манифест не назначил поверхность, попадает в
        // «main» — туда же, где стоит поле задачи. Задача — это вопрос, а
        // остальные поля — параметры, и место им среди параметров.
        ...surfaceItems({ surface: "main", manifest, runtime, context, inputs, onInput, onAction })
          .filter((item) => item.kind === "input" && item.widget !== "chat-input"),
      ]
    : [];

  const grouped = SECTIONS.map((section) => ({
    ...section,
    items: items.filter((item) => sectionOf(item) === section.id),
  })).filter((section) => section.items.length);

  return (
    <div className="sidebar-scroll chat-materials" ref={scroll}>
      {!ready ? (
        <div className="engine-widget">
          <span className="skeleton" style={{ width: "60%" }} />
          <span className="skeleton" style={{ width: "85%" }} />
          <span className="skeleton" style={{ width: "45%" }} />
        </div>
      ) : null}

      {grouped.map((section) => (
        <section className="sidebar-section" data-section={section.id} key={section.id}>
          {section.title ? (
            <h2 className="eyebrow sidebar-section-title" title={section.hint}>{section.title}</h2>
          ) : null}
          {/* Ключ включает сценарий: при смене конвейера виджеты
              пересоздаются, а не донашивают чужой черновик и чужой список. */}
          {section.items.map((item) => (
            <Fragment key={`${surfaceKey}:${item.key}`}>{item.node as ReactNode}</Fragment>
          ))}
        </section>
      ))}

      {ready && !grouped.length ? (
        <EmptyState
          icon={Inbox}
          title="Файлов нет"
          hint="У этого сценария нет входных файлов: задача ставится текстом внизу экрана."
        />
      ) : null}
    </div>
  );
}

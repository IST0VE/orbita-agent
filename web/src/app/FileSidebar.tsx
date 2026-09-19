/**
 * Левая колонка: материалы, параметры и результаты.
 *
 * Состав колонки по-прежнему задаёт манифест, но раскладывается он теперь по
 * трём разделам, а не одним столбцом. Раньше в этом столбце подряд стояли
 * папки задач, выбранные документы, документы этапов и опубликованные
 * страницы — четыре разных вещи, отличавшиеся только заголовком, и понять,
 * что из этого принёс оператор, а что создала ORBITA, можно было только по
 * названиям файлов.
 *
 * Правило раздачи простое и не требует ни новых полей в манифесте, ни знания
 * фронтом конкретных графов: дерево папок — это материалы, остальные поля
 * ввода — параметры прогона, а всё, что приезжает из состояния, — результаты.
 */

import { Fragment, useEffect, useRef, type PointerEvent, type ReactNode } from "react";

import { surfaceItems, type SurfaceItem } from "../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { Inbox, PanelLeftClose } from "../ui/icons";
import { EmptyState } from "../ui";

type SectionId = "input" | "workspace" | "output";

const SECTIONS: Array<{ id: SectionId; title: string; hint: string }> = [
  { id: "input", title: "Материалы", hint: "Папки задач и файлы, которые загрузили вы" },
  { id: "workspace", title: "Параметры прогона", hint: "С чем именно работает ORBITA в этом прогоне" },
  { id: "output", title: "Результаты", hint: "Документы, задачи и публикации, созданные ORBITA" },
];

/** Дерево папок — материалы, прочий ввод — параметры, состояние — результаты. */
function sectionOf(item: SurfaceItem): SectionId {
  if (item.kind === "input") return item.widget === "task-picker" ? "input" : "workspace";
  return "output";
}

export type SidebarScroll = { target: SectionId; nonce: number } | null;

export function FileSidebar({
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  surfaceKey,
  startResize,
  resetWidth,
  scrollRequest,
  ready,
  drawer,
  onClose,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  surfaceKey: string;
  startResize: (event: PointerEvent<HTMLDivElement>) => void;
  resetWidth: () => void;
  /** Запрос навигации: к какому разделу колонки подвести взгляд и когда. */
  scrollRequest: SidebarScroll;
  ready: boolean;
  /** В узком окне колонка приезжает поверх рабочей области и закрывается. */
  drawer: boolean;
  onClose: () => void;
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
    <aside className="sidebar" aria-label="Материалы и результаты задачи">
      <div className="sidebar-head">
        <span className="sidebar-title">Файлы</span>
        <button
          className="btn-ghost btn-icon btn-sm"
          aria-label="Скрыть левую колонку"
          title="Скрыть левую колонку"
          onClick={onClose}
        >
          <PanelLeftClose size={16} aria-hidden="true" />
        </button>
      </div>

      {drawer ? null : (
        <div
          className="col-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="Ширина левой колонки"
          title="Потяните, чтобы изменить ширину. Двойной щелчок — сбросить."
          onPointerDown={startResize}
          onDoubleClick={resetWidth}
        />
      )}

      <div className="sidebar-scroll" ref={scroll}>
        {!ready ? (
          <div className="engine-widget">
            <span className="skeleton" style={{ width: "60%" }} />
            <span className="skeleton" style={{ width: "85%" }} />
            <span className="skeleton" style={{ width: "45%" }} />
          </div>
        ) : null}

        {grouped.map((section) => (
          <section className="sidebar-section" data-section={section.id} key={section.id}>
            <h2 className="eyebrow sidebar-section-title" title={section.hint}>{section.title}</h2>
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
            title="Материалов нет"
            hint="У этого сценария нет входных папок: задача ставится текстом внизу экрана."
          />
        ) : null}
      </div>
    </aside>
  );
}

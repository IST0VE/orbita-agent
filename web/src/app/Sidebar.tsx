/**
 * Левая колонка: материалы задачи и её результаты.
 *
 * Состав колонки задаёт манифест (`surfaces[].widgets`), а не этот файл: здесь
 * только оправа — ярлык раздела, ручка ширины и подпись продукта внизу.
 * Поэтому граф, у которого нет папок задач, не получит пустую секцию «Папка
 * задачи»: её просто не будет в манифесте.
 */

import { useEffect, useRef, type PointerEvent } from "react";

import { Inbox } from "../ui/icons";
import { BrandMark } from "../ui/icons";
import { SurfaceRenderer } from "../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { EmptyState } from "../ui";

export function Sidebar({
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
  /** Запрос навигации из шапки: куда пролистать колонку и когда. */
  scrollRequest: { target: "top" | "published"; nonce: number } | null;
  ready: boolean;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const nonce = scrollRequest?.nonce ?? 0;
  const target = scrollRequest?.target;

  useEffect(() => {
    const node = scroll.current;
    if (!node || !nonce) return;
    if (target === "published") {
      const section = node.querySelector<HTMLElement>('[data-widget="published-list"]');
      if (section) {
        section.scrollIntoView({ behavior: "smooth", block: "nearest" });
        return;
      }
    }
    node.scrollTo({ top: 0, behavior: "smooth" });
  }, [nonce, target]);

  return (
    <aside className="sidebar" aria-label="Материалы задачи">
      <div
        className="col-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Ширина левой колонки"
        title="Потяните, чтобы изменить ширину. Двойной щелчок — сбросить."
        onPointerDown={startResize}
        onDoubleClick={resetWidth}
      />
      <div className="sidebar-scroll" ref={scroll}>
        {ready ? (
          <SurfaceRenderer
            key={surfaceKey}
            surface="left"
            manifest={manifest}
            runtime={runtime}
            context={context}
            inputs={inputs}
            onInput={onInput}
            onAction={onAction}
          />
        ) : (
          <div className="engine-widget">
            <span className="skeleton" style={{ width: "60%" }} />
            <span className="skeleton" style={{ width: "85%" }} />
            <span className="skeleton" style={{ width: "45%" }} />
          </div>
        )}

        {ready && !(manifest.state ?? []).some((item) => item.surface === "left")
          && !(manifest.input ?? []).length ? (
          <EmptyState
            icon={Inbox}
            title="Материалов нет"
            hint="У этого конвейера нет входных папок: задача ставится текстом внизу экрана."
          />
        ) : null}

        <div className="sidebar-footer">
          <span className="sidebar-footer-brand">
            <BrandMark size={18} />
            ORBITA
          </span>
          <p>Анализирует. Структурирует. Помогает принимать решения.</p>
        </div>
      </div>
    </aside>
  );
}

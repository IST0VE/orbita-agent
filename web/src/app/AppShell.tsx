/**
 * Каркас приложения: шапка, строка контекста, рабочая область, консоль.
 *
 * Только раскладка и ничего больше — содержимое приезжает готовыми частями.
 * Раньше эту роль исполнял `App.tsx`: он же держал состояние прогона, он же
 * решал, где стоит правая колонка, и любая правка вёрстки требовала читать
 * шестьсот строк логики стрима.
 *
 * Состояние колонок живёт атрибутами на корне, а не классами: по ним же
 * работают медиазапросы, и в инспекторе браузера видно, почему колонка
 * скрыта, без чтения таблицы стилей.
 */

import type { ReactNode } from "react";

import type { AppSection } from "./sections";

export function AppShell({
  section,
  sidebarOpen,
  inspectorOpen,
  narrow,
  animated,
  leftWidth,
  header,
  context,
  alerts,
  sidebar,
  main,
  inspector,
  composer,
  console: bottom,
  onDismissDrawer,
  children,
}: {
  section: AppSection;
  sidebarOpen: boolean;
  inspectorOpen: boolean;
  /** Колонки показаны поверх рабочей области: окно слишком узкое. */
  narrow: boolean;
  animated: boolean;
  leftWidth: number;
  header: ReactNode;
  context?: ReactNode;
  alerts?: ReactNode;
  sidebar?: ReactNode;
  main?: ReactNode;
  inspector?: ReactNode;
  composer?: ReactNode;
  console?: ReactNode;
  onDismissDrawer: () => void;
  /** Слои поверх всего: настройки, окно подтверждения. Смонтированы всегда. */
  children?: ReactNode;
}) {
  const workspace = section === "workspace";
  return (
    <div
      className="app"
      data-section={section}
      data-sidebar={sidebarOpen ? "open" : "closed"}
      data-inspector={inspectorOpen ? "open" : "closed"}
      data-narrow={narrow ? "true" : "false"}
      data-animated={animated ? "true" : "false"}
      style={leftWidth ? ({ "--col-left": `${leftWidth}px` } as React.CSSProperties) : undefined}
    >
      {header}
      {workspace ? context : null}
      {alerts}

      {/* Рабочая область не размонтируется, когда поверх неё открыты
          настройки: у схемы есть камера и ручные позиции узлов, и
          пересчитывать раскладку ELK ради взгляда на настройки незачем. */}
      <div className="app-body" hidden={!workspace}>
        {sidebarOpen ? sidebar : null}
        <div className="app-main">
          {main}
          {composer}
        </div>
        {inspectorOpen ? inspector : null}
        {/* В узком окне выдвинутая панель закрывается щелчком по рабочей
            области: искать крестик в панели, занявшей экран, незачем. */}
        {narrow && (sidebarOpen || inspectorOpen) ? (
          <button className="drawer-scrim" aria-label="Закрыть панель" onClick={onDismissDrawer} />
        ) : null}
      </div>

      {workspace ? bottom : null}

      {children}
    </div>
  );
}

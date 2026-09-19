/**
 * Поверхности манифеста: что именно стоит в колонке, в главном окне и в доке.
 *
 * Состав поверхности объявляет сервер (`surfaces[].widgets`), а раскладку —
 * каркас приложения. Поэтому здесь не разметка колонки, а список готовых
 * элементов с их приметами: тип виджета, его id, заголовок и признак «данных
 * нет». По этим приметам каркас раскладывает одну и ту же поверхность
 * по разным местам экрана — материалы отдельно, параметры отдельно,
 * результаты отдельно, — не спрашивая сервер, куда что положить.
 *
 * Разделение появилось вместе с новой архитектурой экрана. Раньше поверхность
 * умела одно: вывалить всё подряд одним столбцом. Столбец из девяти карточек
 * и был главной причиной, по которой правая колонка показывала четыре пустые
 * плашки, а левая мешала исходные файлы с готовыми документами.
 */

import { Fragment, type ReactNode } from "react";
import { Modal } from "../../Modal";

import { conditionMatches, inputSurfaceOf, matchInterrupt, resolveBinding } from "../manifest/bindings";
import type { InputBinding, SafeWidgetContext, SurfaceId, UiManifest, WidgetAction, WidgetBinding } from "../manifest/types";
import type { RuntimeSnapshot } from "../runtime/types";
import { WidgetErrorBoundary } from "../widgets/ErrorBoundary";
import { widgetRegistry, type WidgetRegistry } from "../widgets/registry";

/**
 * Значения нет.
 *
 * Не то же самое, что «значение ложно»: ноль вызовов и пустая строка статуса
 * — это данные, а `undefined`, пустой список и пустой объект — их отсутствие.
 * Различие нужно там, где пустой блок не рисуется вовсе, а вместо него
 * остаётся строка «—».
 */
export function isEmptyValue(value: unknown): boolean {
  if (value === undefined || value === null || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value as object).length === 0;
  return false;
}

export type SurfaceItem = {
  key: string;
  /** id биндинга из манифеста: по нему поверхность называет свой порядок. */
  id: string;
  widget: string;
  kind: "state" | "input";
  title?: string;
  /** Данных за биндингом нет: место под него занимать незачем. */
  empty: boolean;
  node: ReactNode;
};

export type SurfaceProps = {
  surface: SurfaceId;
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  registry?: WidgetRegistry;
};

/**
 * Готовые элементы поверхности в порядке, который назвал манифест.
 *
 * Не хук и не компонент — обычная функция, которую зовут во время отрисовки.
 * Поэтому её результат можно разложить по секциям, отфильтровать или вовсе
 * пересчитать: каркас работает со списком, а не с непрозрачным поддеревом.
 */
export function surfaceItems({
  surface,
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  registry = widgetRegistry,
}: SurfaceProps): SurfaceItem[] {
  const configured = manifest.surfaces?.find((item) => item.id === surface)?.widgets;
  const running = runtime.runStatus === "running" || runtime.runStatus === "queued";
  const allows = (id: string) => !configured?.length || configured.includes(id);
  // Порядок колонки задаёт список surfaces[].widgets: файлы задачи стоят в нём
  // первыми и должны рисоваться первыми, хотя приходят из input, а не из state.
  // Что в списке не названо — уходит в хвост в прежнем порядке.
  const place = (id?: string) => {
    const at = id && configured ? configured.indexOf(id) : -1;
    return at === -1 ? Number.MAX_SAFE_INTEGER : at;
  };
  /*
   * Непрочитываемый путь в манифесте — ошибка конфигурации, и стоить она
   * должна ровно один блок поверхности. Валидатор такие пути отклоняет и
   * уводит сценарий в fallback, но разрешение биндинга идёт до границы ошибок
   * виджета: исключение отсюда раньше поднималось до корня и оставляло пустой
   * экран вместо интерфейса с одной красной карточкой.
   */
  const visible = (binding: WidgetBinding) => {
    try {
      return conditionMatches(binding.visible_when, runtime.state);
    } catch {
      return true;
    }
  };
  const stateBindings = (manifest.state ?? [])
    .filter((binding) => binding.surface === surface && allows(binding.id))
    .filter(visible)
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const inputBindings = (manifest.input ?? []).filter(
    (binding) => inputSurfaceOf(manifest, binding) === surface,
  );

  const render = (binding: WidgetBinding, value: unknown, mode: "view" | "input") => {
    const definition = registry.resolve(binding.widget);
    const supported = definition.modes.includes(mode);
    const actual = supported ? binding : { ...binding, widget: "unknown" };
    const ActualComponent = supported ? definition.component : registry.resolve("unknown").component;
    return <WidgetErrorBoundary key={binding.id ?? binding.path} widget={actual.widget} binding={actual.path}>
      <section className="engine-widget" data-widget={actual.widget}>
        {actual.title && !definition.ownHeader ? <h3>{actual.title}</h3> : null}
        <ActualComponent value={value} binding={actual} mode={mode} readonly={running && mode === "input"} context={context} onAction={onAction} />
      </section>
    </WidgetErrorBoundary>;
  };

  const items: Array<SurfaceItem & { sort: [number, number] }> = [];
  stateBindings.forEach((binding) => {
    let resolved: { found: boolean; value: unknown };
    try {
      resolved = resolveBinding(runtime.state, binding.path);
    } catch (error) {
      items.push({
        key: `state:${binding.id ?? binding.path}`,
        id: binding.id ?? binding.path,
        widget: binding.widget,
        kind: "state",
        title: binding.title,
        empty: false,
        sort: [place(binding.id), binding.order ?? 0],
        node: <div className="widget-error" role="alert">
          <b>Биндинг {binding.id ?? binding.widget} не прочитан</b>
          <div className="hint">{error instanceof Error ? error.message : String(error)}</div>
        </div>,
      });
      return;
    }
    const empty = !resolved.found || isEmptyValue(resolved.value);
    if (empty && binding.empty === "hide") return;
    items.push({
      key: `state:${binding.id ?? binding.path}`,
      id: binding.id ?? binding.path,
      widget: binding.widget,
      kind: "state",
      title: binding.title,
      empty,
      sort: [place(binding.id), binding.order ?? 0],
      node: render(binding, resolved.value, "view"),
    });
  });
  inputBindings.forEach((input: InputBinding) => {
    const binding: WidgetBinding = {
      id: input.id,
      path: input.id,
      widget: input.widget,
      title: input.title,
      options: { ...(input.options ?? {}), ...(input.source ?? {}) },
    };
    const definition = registry.resolve(binding.widget);
    const Component = definition.modes.includes("input") ? definition.component : registry.resolve("unknown").component;
    items.push({
      key: `input:${input.id}`,
      id: input.id,
      widget: input.widget,
      kind: "input",
      title: input.title,
      // Поле ввода пустым не бывает: его показывают ради того, чтобы заполнить.
      empty: false,
      sort: [place(input.id), 0],
      node: <WidgetErrorBoundary key={input.id} widget={binding.widget} binding={input.target}>
        <section className="engine-widget" data-widget={binding.widget}>
          {binding.title && !definition.ownHeader ? <h3>{binding.title}</h3> : null}
          <Component value={inputs[input.id]} binding={binding} mode="input" readonly={running} context={context} onChange={(value) => onInput(input.id, value)} onAction={onAction} />
        </section>
      </WidgetErrorBoundary>,
    });
  });
  items.sort((a, b) => a.sort[0] - b.sort[0] || a.sort[1] - b.sort[1]);
  return items;
}

/** Поверхность одним столбцом: то, что нужно доку и любому простому месту. */
export function SurfaceRenderer(props: SurfaceProps) {
  return <>{surfaceItems(props).map((item) => <Fragment key={item.key}>{item.node}</Fragment>)}</>;
}

export function InterruptSurface({
  manifest,
  runtime,
  context,
  onAction,
  busy = false,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  onAction: (action: WidgetAction) => void;
  busy?: boolean;
}) {
  const interrupt = runtime.interrupts.find((item) => item.status === "pending");
  if (!interrupt) return null;
  const rule = matchInterrupt(manifest.interrupts, interrupt.value);
  const binding: WidgetBinding = {
    id: rule?.id ?? interrupt.interruptId,
    path: "interrupt",
    widget: rule?.widget ?? "form",
    title: "Требуется решение оператора",
    options: {
      interruptId: interrupt.interruptId,
      // Правило и остановка — разные идентификаторы: по первому сервер берёт
      // resume_schema, по второму LangGraph адресует ответ остановке.
      ruleId: rule?.id ?? "",
      schema: rule?.resume_schema ?? { type: "object" },
    },
  };
  const definition = widgetRegistry.resolve(binding.widget);
  const Component = definition.modes.includes("interrupt")
    ? definition.component
    : widgetRegistry.resolve("unknown").component;
  return (
    <Modal key={interrupt.interruptId} className="engine-interrupt-overlay" label="Требуется решение оператора">
      <WidgetErrorBoundary widget={binding.widget} binding={binding.path}>
        <Component
          value={interrupt.value}
          binding={binding}
          mode="interrupt"
          readonly={busy || runtime.runStatus === "running" || runtime.runStatus === "queued"}
          context={context}
          onAction={onAction}
        />
      </WidgetErrorBoundary>
    </Modal>
  );
}

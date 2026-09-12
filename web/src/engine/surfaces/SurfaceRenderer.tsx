import { Fragment, type ReactNode } from "react";
import { Modal } from "../../Modal";

import { conditionMatches, inputSurfaceOf, matchInterrupt, resolveBinding } from "../manifest/bindings";
import type { InputBinding, SafeWidgetContext, SurfaceId, UiManifest, WidgetAction, WidgetBinding } from "../manifest/types";
import type { RuntimeSnapshot } from "../runtime/types";
import { WidgetErrorBoundary } from "../widgets/ErrorBoundary";
import { widgetRegistry, type WidgetRegistry } from "../widgets/registry";

export function SurfaceRenderer({
  surface,
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  registry = widgetRegistry,
}: {
  surface: SurfaceId;
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  registry?: WidgetRegistry;
}) {
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
  const stateBindings = (manifest.state ?? [])
    .filter((binding) => binding.surface === surface && allows(binding.id))
    .filter((binding) => conditionMatches(binding.visible_when, runtime.state))
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const inputBindings = (manifest.input ?? []).filter(
    (binding) => inputSurfaceOf(manifest, binding) === surface,
  );

  const render = (binding: WidgetBinding, value: unknown, mode: "view" | "input") => {
    const definition = registry.resolve(binding.widget);
    const Component = definition.component;
    const supported = definition.modes.includes(mode);
    const actual = supported ? binding : { ...binding, widget: "unknown" };
    const ActualComponent = supported ? Component : registry.resolve("unknown").component;
    return <WidgetErrorBoundary key={binding.id ?? binding.path} widget={actual.widget} binding={actual.path}>
      <section className="engine-widget" data-widget={actual.widget}>
        {actual.title && !definition.ownHeader ? <h3>{actual.title}</h3> : null}
        <ActualComponent value={value} binding={actual} mode={mode} readonly={running && mode === "input"} context={context} onAction={onAction} />
      </section>
    </WidgetErrorBoundary>;
  };

  const items: Array<{ key: string; sort: [number, number]; node: ReactNode }> = [];
  stateBindings.forEach((binding) => {
    const resolved = resolveBinding(runtime.state, binding.path);
    if ((!resolved.found || resolved.value === undefined || resolved.value === null) && binding.empty === "hide") return;
    items.push({
      key: `state:${binding.id ?? binding.path}`,
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

  return <>{items.map((item) => <Fragment key={item.key}>{item.node}</Fragment>)}</>;
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

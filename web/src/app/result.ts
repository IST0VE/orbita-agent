/**
 * Разделение итога прогона: что читают и что сверяют.
 *
 * Манифест кладёт на правую поверхность и то и другое: итог, вердикт SLA,
 * таблицы аномалий — рядом со стоимостью и публикацией. Пока всё это стояло
 * одной колонкой в триста пикселей, таблица на восемь колонок читалась
 * прокруткой вбок, а «Стоимость —» занимала столько же места, сколько
 * заключение.
 *
 * Поэтому итог уезжает в главную область, где под него есть ширина, а в
 * инспекторе остаются показатели прогона: сколько это стоило и уехало ли
 * куда-нибудь. Список короткий и закрытый — всё, что не названо здесь,
 * считается содержанием результата.
 */

import { resolveBinding } from "../engine/manifest/bindings";
import { isEmptyValue } from "../engine/surfaces/SurfaceRenderer";
import type { UiManifest } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";

/** Показатели прогона: их место — инспектор, а не главная область. */
export const INSPECTOR_WIDGETS = new Set(["cost-summary", "publication"]);

export function isInspectorWidget(widget: string): boolean {
  return INSPECTOR_WIDGETS.has(widget);
}

/**
 * Есть ли что показать во вкладке результата.
 *
 * По этому же признаку вкладка подсвечивается после прогона: обещать
 * результат, которого нет, хуже, чем не обещать ничего.
 */
export function hasResultContent(manifest: UiManifest, runtime: RuntimeSnapshot): boolean {
  return (manifest.state ?? []).some((binding) => {
    if (binding.surface !== "right" || isInspectorWidget(binding.widget)) return false;
    const resolved = resolveBinding(runtime.state, binding.path);
    return resolved.found && !isEmptyValue(resolved.value);
  });
}

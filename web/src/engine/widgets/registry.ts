import type { WidgetDefinition } from "../manifest/types";
import { BUILTIN_WIDGETS } from "./builtins";

function major(version: string): string { return version.split(".")[0]; }

export class WidgetRegistry {
  private definitions = new Map<string, WidgetDefinition>();

  register(definition: WidgetDefinition): void {
    const key = `${definition.type}@${major(definition.version)}`;
    if (this.definitions.has(key)) throw new Error(`duplicate widget registration: ${key}`);
    if (!definition.errorBoundary) throw new Error(`widget ${key} must declare an Error Boundary`);
    this.definitions.set(key, definition);
  }

  resolve(type: string, version = "1"): WidgetDefinition {
    return this.definitions.get(`${type}@${major(version)}`) ?? (this.definitions.get("unknown@1") as WidgetDefinition);
  }

  list(): string[] { return [...this.definitions.keys()].sort(); }
}

export const widgetRegistry = new WidgetRegistry();
BUILTIN_WIDGETS.forEach((definition) => widgetRegistry.register(definition));

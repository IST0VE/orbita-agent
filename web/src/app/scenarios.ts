/**
 * Каталог сценариев: что предлагает продукт и как это разложено.
 *
 * Названия и описания берутся из манифеста графа — их пишет сервер, и второй
 * их копии здесь нет. Отсюда только то, чего у сервера нет и быть не должно:
 * к какому разделу каталога сценарий относится и каким значком он узнаётся.
 * Незнакомый граф (дописали в `langgraph.json`) попадает в общий раздел и
 * получает значок по умолчанию — появиться он появится, просто без места в
 * структуре, которую ещё никто не назначил.
 */

import type { Assistant } from "../api";
import {
  ClipboardList,
  FileSearch,
  Gauge,
  Network,
  Rocket,
  SquareKanban,
  SquarePen,
  Waypoints,
  type LucideIcon,
} from "../ui/icons";

export type ScenarioCategory = "tasks" | "docs" | "testing" | "other";

export const CATEGORY_TITLES: Record<ScenarioCategory, string> = {
  tasks: "Работа с задачами",
  docs: "Документация",
  testing: "Тестирование",
  other: "Другие сценарии",
};

/** Порядок разделов каталога: от самого частого к самому редкому. */
export const CATEGORY_ORDER: ScenarioCategory[] = ["tasks", "docs", "testing", "other"];

const CATEGORY: Record<string, ScenarioCategory> = {
  prep: "tasks",
  jira: "tasks",
  agent: "tasks",
  drawio: "docs",
  update: "docs",
  audit: "docs",
  nt: "testing",
  nt_run: "testing",
};

const ICON: Record<string, LucideIcon> = {
  prep: ClipboardList,
  jira: SquareKanban,
  agent: Waypoints,
  drawio: Network,
  update: SquarePen,
  audit: FileSearch,
  nt: Gauge,
  nt_run: Rocket,
};

export function scenarioCategory(graphId: string): ScenarioCategory {
  return CATEGORY[graphId] ?? "other";
}

export function scenarioIcon(graphId: string): LucideIcon {
  return ICON[graphId] ?? Waypoints;
}

export function sortAssistants(list: Assistant[]): Assistant[] {
  return [...list].sort((a, b) => a.graph_id.localeCompare(b.graph_id));
}

/** Название и однострочное описание сценария: из манифеста, иначе из реестра. */
export function graphInfo(
  assistant: Assistant | undefined,
  fallback = "",
  info: Record<string, { label: string; hint: string }> = {},
) {
  if (!assistant) return { label: fallback, hint: "" };
  return info[assistant.graph_id] ?? {
    label: assistant.name || assistant.graph_id,
    hint: assistant.description || `граф ${assistant.graph_id}`,
  };
}

/* ------------------------------------------------------------------ */
/* Недавние                                                            */
/* ------------------------------------------------------------------ */

const RECENT_KEY = "orbita.scenarios.recent";
const RECENT_LIMIT = 3;

/**
 * Чем пользовались в последний раз.
 *
 * Каталог из восьми сценариев читают целиком ровно один раз; дальше ходят по
 * двум-трём. Список недавних — это не украшение, а то, ради чего каталог не
 * приходится перечитывать.
 */
export function recentScenarios(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as unknown;
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

export function rememberScenario(graphId: string): void {
  if (!graphId) return;
  const next = [graphId, ...recentScenarios().filter((item) => item !== graphId)].slice(0, RECENT_LIMIT);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // Приватный режим браузера запрещает запись: список недавних — удобство,
    // и падать из-за него нельзя.
  }
}

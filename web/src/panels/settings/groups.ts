/**
 * Разделы настроек и человеческие имена переменных.
 *
 * Окно настроек было графическим редактором `.env`: восемьдесят строк вида
 * `LLM_API_BASE` подряд, отсортированных так, как они лежат в файле. Такой
 * список читает тот, кто уже знает, что ищет, — то есть тот, кому проще
 * открыть сам файл.
 *
 * Здесь появляется второй слой: к какому разделу продукта переменная
 * относится и как она называется по-русски. Имя `LLM_API_BASE` никуда не
 * девается — оно остаётся второй строкой, потому что в документации,
 * сообщениях об ошибках и в самом файле переменная называется именно так.
 *
 * Раздача по разделам — по префиксу имени, а не по разделу файла: в файле
 * адреса Prometheus лежат вместе с настройками анализа НТ, потому что так
 * удобнее читать файл, а не потому, что это одно и то же.
 */

import type { Setting } from "../../api";
import type { SettingsGroupId } from "../../app/sections";
import {
  Blocks,
  Cpu,
  Palette,
  Server,
  SlidersHorizontal,
  Wrench,
  type LucideIcon,
} from "../../ui/icons";

export type SettingsGroup = {
  id: SettingsGroupId;
  title: string;
  hint: string;
  icon: LucideIcon;
};

export const SETTINGS_GROUPS: SettingsGroup[] = [
  { id: "ai", title: "Модель", hint: "Провайдер, модель и подключение к ней", icon: Cpu },
  { id: "connections", title: "Подключения", hint: "Служебный API, хранилище и источники метрик", icon: Server },
  { id: "integrations", title: "Интеграции", hint: "Confluence, Jira и публикация", icon: Blocks },
  { id: "execution", title: "Выполнение", hint: "Материалы, бюджет, память и этапы", icon: Wrench },
  { id: "appearance", title: "Оформление", hint: "Как ведёт себя интерфейс", icon: Palette },
  { id: "advanced", title: "Продвинутые", hint: "Все переменные окружения целиком", icon: SlidersHorizontal },
];

const PREFIX: Array<[RegExp, SettingsGroupId]> = [
  [/^(LLM|PRICE)_/, "ai"],
  [/^(CONFLUENCE|JIRA|ATLASSIAN|PUBLISH)_/, "integrations"],
  [/^(API_|POSTGRES|CHECKPOINT_)/, "connections"],
  [/^NT_(PROMETHEUS|INFLUX|KUBERNETES|LOAD_TESTING|RUNNER)/, "connections"],
];

/** Раздел, в котором переменная показывается. Неизвестная — про выполнение. */
export function groupOf(name: string): SettingsGroupId {
  for (const [pattern, group] of PREFIX) if (pattern.test(name)) return group;
  return "execution";
}

/**
 * Имена, которые стоит написать самим.
 *
 * Только те, что стоят на виду в разделе «Модель»: их читают все, и
 * «Адрес API» здесь честнее первой фразы комментария из `.env.example`.
 * Остальные семьдесят переменных берут имя из описания — оно там уже
 * написано и уже поддерживается.
 */
const LABELS: Record<string, string> = {
  LLM_PROVIDER: "Провайдер",
  LLM_MODEL: "Модель",
  LLM_API_BASE: "Адрес API",
  LLM_API_KEY: "Ключ API",
};

/** Ключевые поля раздела «Модель» — в том порядке, в каком их заполняют. */
export const AI_PROVIDER_FIELDS = ["LLM_PROVIDER", "LLM_MODEL"];
export const AI_CONNECTION_FIELDS = ["LLM_API_BASE", "LLM_API_KEY"];

/** Первая мысль описания: до точки, точки с запятой или тире-пояснения. */
function firstSentence(value: string): string {
  const text = value.trim().split(/\r?\n/)[0] ?? "";
  const cut = text.split(/[.;]|\s—\s/)[0]?.trim() ?? "";
  if (!cut || cut.length > 72) return "";
  return cut.charAt(0).toUpperCase() + cut.slice(1);
}

/**
 * Как переменная называется на экране.
 *
 * Имя из окружения остаётся второй строкой, а не заголовком: `AGENT_INPUT_DIR`
 * отвечает на вопрос «как это записано в файле», а «Корень папок задач» — на
 * вопрос «что это». Оператору нужен второй ответ, а инженеру — оба.
 */
export function settingLabel(field: Setting): string {
  return LABELS[field.name] ?? (firstSentence(field.description) || field.name);
}

/** Длинный заголовок раздела из `.env.example`: первая фраза — в заголовок. */
export function sectionTitle(title: string): { title: string; note: string } {
  const at = title.indexOf(". ");
  if (at === -1 || at > 64) return { title, note: "" };
  return { title: title.slice(0, at), note: title.slice(at + 2) };
}

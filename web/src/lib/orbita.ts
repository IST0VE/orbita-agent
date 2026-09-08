/**
 * Состояние графа Orbita на стороне браузера.
 *
 * Поля приезжают из `src/agent/graph.py` как есть. Деньги там уже переведены
 * в доллары по тарифу из `.env`: цены остаются на сервере, иначе у них
 * появилось бы второе место хранения. Поэтому здесь только типы и
 * форматирование, без единой операции над тарифом.
 */

/** То, что кладёт в состояние `cost_summary()`. */
export type CostSummary = {
  /** Реально потрачено на тред с учётом кеша. */
  usd: number;
  /** Во что обошёлся бы тот же тред, если бы кеш не срабатывал. */
  naive_usd: number;
  /** Доля входа, приехавшая из кеша, в процентах. */
  hit_rate: number;
  /** BUDGET_USD_PER_THREAD; 0 означает «без лимита». */
  limit_usd: number;
  input: number;
  output: number;
  cache_hit: number;
  calls: number;
};

/** Одна опубликованная страница: свой этап, свой адрес, свой исход. */
export type PublishedPage = {
  /** Ключ роли из `roles.py`; пусто у страницы треда без этапов. */
  role: string;
  status: string;
  title: string;
  url?: string;
  version?: number;
  reason?: string;
};

/**
 * Итог этапа публикации: `publish_node` пишет его после каждого хода.
 *
 * Страниц столько, сколько состоялось этапов конвейера, поэтому адрес живёт
 * у страницы, а не у публикации целиком. Наверху остаётся сводный статус —
 * в том числе `partial`, когда уехало не всё.
 */
export type Publication = {
  status: string;
  title?: string;
  reason?: string;
  pages?: PublishedPage[];
};

/** Значение `interrupt()` из ноды `approve`. */
export type PublishInterrupt = {
  action: "publish";
  /** Имя цели публикации: confluence, file, none. */
  target: string;
  /** Разметка документа: storage (XHTML Confluence) или markdown. */
  format?: string;
  title: string;
  /** Заголовки страниц, которые уедут, — по одной на состоявшийся этап. */
  pages?: string[];
  document: string;
  hint?: string;
};

/**
 * Значение `interrupt()` из ворот этапа (PIPELINE_REQUIRE_APPROVAL).
 *
 * Приходит между ролями и показывает документ предыдущей: продолжить конвейер
 * или остановить его, не оплачивая оставшиеся этапы.
 */
export type StageInterrupt = {
  action: "stage";
  /** Ключ роли, чей документ показывают. */
  stage: string;
  /** Ключ роли, которая пойдёт следующей, если подтвердить. */
  next: string;
  title: string;
  format?: string;
  document: string;
  hint?: string;
};

/** Поля состояния Orbita поверх стандартного `messages`. */
export type OrbitaState = {
  cost?: CostSummary;
  publication?: Publication;
  approval?: { decision: "approved" | "rejected"; reason?: string };
  /** Задача конвейера: текст оператора без подставленного контекста. */
  task?: string;
  /** Готовые документы этапов: ключ роли — текст. */
  artifacts?: Record<string, string>;
  /** Ключ роли, отработавшей последней. */
  stage?: string;
};

/** Остановку на подтверждение узнаём по `action`, а не по форме объекта. */
export function isPublishInterrupt(value: unknown): value is PublishInterrupt {
  return (
    !!value &&
    typeof value === "object" &&
    (value as PublishInterrupt).action === "publish" &&
    typeof (value as PublishInterrupt).document === "string"
  );
}

/** Ворота этапа отличаются от подтверждения публикации тем же способом. */
export function isStageInterrupt(value: unknown): value is StageInterrupt {
  return (
    !!value &&
    typeof value === "object" &&
    (value as StageInterrupt).action === "stage" &&
    typeof (value as StageInterrupt).document === "string"
  );
}

/**
 * Деньги в строку.
 *
 * Тред стоит доли цента, и округление до копеек показало бы везде $0.00 —
 * ровно ту цифру, ради которой всё и затевалось. Поэтому шесть знаков,
 * пока сумма меньше доллара, и обычные два, когда счёт пошёл на доллары.
 */
export function formatUsd(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `$${value.toFixed(value < 1 ? 6 : 2)}`;
}

/** Во сколько раз кеш срезал счёт. Без потраченного делить не на что. */
export function savingRatio(cost: CostSummary): number | null {
  if (!(cost.usd > 0) || !(cost.naive_usd > 0)) return null;
  const ratio = cost.naive_usd / cost.usd;
  return ratio >= 1.05 ? ratio : null;
}

export function formatTokens(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("ru-RU").format(Math.round(value));
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} КБ`;
  return `${(size / 1024 / 1024).toFixed(1)} МБ`;
}

/** Статусы публикации из `publish_node` — в человеческий текст и цвет. */
export type Tone = "ok" | "muted" | "warn" | "bad";

const PUBLICATION_LABELS: Record<string, { text: string; tone: Tone }> = {
  created: { text: "страницы созданы", tone: "ok" },
  updated: { text: "страницы обновлены", tone: "ok" },
  partial: { text: "уехало не всё", tone: "warn" },
  unchanged: { text: "документ не изменился", tone: "muted" },
  disabled: { text: "публикация выключена", tone: "muted" },
  postponed: { text: "публикация отложена", tone: "muted" },
  skipped: { text: "не хватает настроек", tone: "warn" },
  rejected: { text: "оператор отклонил", tone: "warn" },
  failed: { text: "ошибка публикации", tone: "bad" },
};

export function publicationLabel(publication: Publication): { text: string; tone: Tone } {
  return (
    PUBLICATION_LABELS[publication.status] ?? { text: publication.status, tone: "muted" }
  );
}

export const TONE_VAR: Record<Tone, string> = {
  ok: "var(--green)",
  muted: "var(--fg-dim)",
  warn: "var(--amber)",
  bad: "var(--red)",
};

/**
 * Верхняя строка продукта.
 *
 * Здесь стоит только то, что относится ко всему приложению: марка,
 * переключатель сценария, состояние связи, уведомления и профиль. Действий
 * текущего сценария здесь нет ни одного — они уехали в строку контекста и к
 * тем объектам, к которым относятся.
 *
 * Навигации по разделам здесь нет, потому что разделов не осталось.
 * «Проекты» вели туда же, куда ведёт марка, и обещали сущность, которой в
 * продукте нет. «База знаний» показывала тот же список публикаций, что и
 * секция в колонке результатов, а читались документы всё равно в главной
 * области. Рабочая поверхность одна, и уходить с неё некуда: настройки
 * открываются слоем из меню профиля.
 *
 * Точка входа в настройки одна — меню профиля. Раньше их было две, пункт и
 * шестерёнка, и обе вели в одно место: два одинаковых входа читаются как два
 * разных, и половина операторов ищет в шестерёнке то, чего там нет.
 */

import type { Assistant, ServerStatus } from "../api";
import { Bell, BrandMark, Palette, Settings } from "../ui/icons";
import { Menu } from "../ui/Menu";
import { StatusDot } from "../ui";
import { ScenarioSwitcher } from "./ScenarioSwitcher";
import type { AppSection, SettingsGroupId } from "./sections";

/**
 * Состояние связи с сервером агента.
 *
 * Это единственный глобальный статус продукта: он говорит, жив ли сервер, и
 * ничего не говорит о прогоне. Состояние прогона живёт в строке контекста —
 * один смысл, одно место.
 */
const CONNECTION: Record<string, { label: string; tone: "ok" | "warn" | "bad" }> = {
  ok: { label: "На связи", tone: "ok" },
  unauthorized: { label: "Нет доступа", tone: "warn" },
  offline: { label: "Нет сервера", tone: "bad" },
};

export function GlobalHeader({
  assistants,
  assistantId,
  manifestInfo,
  onSelectAssistant,
  locked,
  online,
  onSection,
  onOpenSettings,
  alerts,
  onOpenAlerts,
}: {
  assistants: Assistant[];
  assistantId: string;
  manifestInfo: Record<string, { label: string; hint: string }>;
  onSelectAssistant: (id: string) => void;
  /** Во время прогона сценарий не переключается. */
  locked: boolean;
  online: ServerStatus | null;
  /** Возврат на рабочую область по марке: из настроек и из открытого документа. */
  onSection: (section: AppSection) => void;
  onOpenSettings: (group?: SettingsGroupId) => void;
  /** Сколько в текущем прогоне того, о чём стоит сказать: отказы и остановки. */
  alerts: number;
  onOpenAlerts: () => void;
}) {
  const connection = online === null
    ? { label: "Связь…", tone: "idle" as const }
    : CONNECTION[online] ?? CONNECTION.offline;

  return (
    <header className="app-header">
      <button
        type="button"
        className="brand"
        aria-label="ORBITA: к рабочей области"
        onClick={() => onSection("workspace")}
      >
        <span className="brand-mark"><BrandMark size={24} /></span>
        <span className="brand-name">ORBITA</span>
      </button>

      <ScenarioSwitcher
        assistants={assistants}
        selected={assistantId}
        onSelect={onSelectAssistant}
        disabled={locked}
        info={manifestInfo}
      />

      <span className="header-spacer" />

      <span className="system-status" title={`Сервер агента: ${connection.label.toLowerCase()}`}>
        <StatusDot tone={connection.tone} />
        <span className="system-status-label">{connection.label}</span>
      </span>

      {/*
        Колокольчик сообщает о том, что уже произошло в этом прогоне: отказах
        и остановках. Пока их нет, он выключен — кнопка, которая ничего не
        делает, но выглядит нажимаемой, хуже её отсутствия.
      */}
      <button
        className="btn-ghost btn-icon header-alerts"
        aria-label={alerts ? `Событий, требующих внимания: ${alerts}` : "Уведомлений нет"}
        title={alerts ? `Событий, требующих внимания: ${alerts}` : "Уведомлений нет"}
        disabled={!alerts}
        onClick={onOpenAlerts}
      >
        <Bell size={17} aria-hidden="true" />
        {alerts ? <span className="dot dot-bad bell-dot" aria-hidden="true" /> : null}
      </button>

      <Menu
        className="menu-avatar"
        label="Оператор и настройки"
        trigger={<span className="avatar" aria-hidden="true">ОП</span>}
        items={[
          {
            id: "app-settings",
            label: "Настройки приложения",
            icon: Settings,
            hint: "Модель, подключения, интеграции, выполнение",
            onSelect: () => onOpenSettings("ai"),
          },
          {
            id: "appearance",
            label: "Оформление",
            icon: Palette,
            hint: "Движение фона и плотность интерфейса",
            onSelect: () => onOpenSettings("appearance"),
          },
        ]}
      />
    </header>
  );
}

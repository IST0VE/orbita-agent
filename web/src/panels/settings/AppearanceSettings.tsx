/**
 * Раздел «Оформление»: настройки самого интерфейса.
 *
 * Единственный раздел, который не имеет отношения к `.env`: это предпочтения
 * браузера, и живут они в `localStorage`. Раньше движение фона переключалось
 * значком в шапке — рядом с уведомлениями и настройками, как будто это
 * действие того же порядка. Это не действие, это настройка, и место ей здесь.
 */

import { Check, X } from "../../ui/icons";

export function AppearanceSettings({
  animated,
  onToggleAnimation,
  onResetLayout,
}: {
  animated: boolean;
  onToggleAnimation: () => void;
  onResetLayout: () => void;
}) {
  return (
    <div className="set-section">
      <h3>Интерфейс</h3>

      <div className="set-row">
        <div className="set-field">
          <span className="set-label">
            <span className="set-name">Движение фона</span>
          </span>
          <span className="set-control">
            <button
              className={`toggle ${animated ? "on" : ""}`.trim()}
              aria-label="Движение фона"
              aria-pressed={animated}
              onClick={onToggleAnimation}
            >
              {animated ? <Check size={14} aria-hidden="true" /> : <X size={14} aria-hidden="true" />}
              {animated ? "включено" : "выключено"}
            </button>
          </span>
        </div>
        <div className="set-desc">
          Орбиты за схемой медленно вращаются. Системная настройка «меньше
          движения» отключает их независимо от этого переключателя.
        </div>
      </div>

      <div className="set-row">
        <div className="set-field">
          <span className="set-label">
            <span className="set-name">Ширина левой колонки</span>
          </span>
          <span className="set-control">
            <button className="btn-sm" onClick={onResetLayout}>Сбросить</button>
          </span>
        </div>
        <div className="set-desc">
          Колонку материалов растягивают за правую кромку. Сброс возвращает
          ширину по умолчанию — ту же, что двойной щелчок по кромке.
        </div>
      </div>
    </div>
  );
}

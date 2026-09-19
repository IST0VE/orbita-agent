/**
 * Последняя граница ошибок приложения.
 *
 * Границы виджетов ловят отрисовку виджета, но не всё, что происходит до неё:
 * разрешение биндингов, сборку модели поверхности, раскладку каркаса. Любое
 * исключение там оставляло `#root` пустым — белый экран без единой строки о
 * том, что случилось, и без способа вернуться.
 *
 * Здесь не «красивая заглушка», а три вещи, которых не хватало: сказать, что
 * сломалось, дать перезагрузить страницу и дать способ выйти из состояния,
 * которое в неё и привело, — сохранённый выбор сценария и тред.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

export class RootBoundary extends Component<
  { children: ReactNode },
  { error?: Error; correlationId?: string }
> {
  state: { error?: Error; correlationId?: string } = {};

  static getDerivedStateFromError(error: Error) {
    return { error, correlationId: crypto.randomUUID?.() ?? String(Date.now()) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("app render failed", {
      correlationId: this.state.correlationId,
      error: error.message,
      componentStack: info.componentStack,
    });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="root-error" role="alert">
        <h1>Интерфейс не отрисован</h1>
        <p>
          Приложение остановилось до того, как появился экран. Чаще всего так
          заканчивается несогласованная конфигурация сценария, а не отказ сервера.
        </p>
        <pre className="root-error-message">{this.state.error.message}</pre>
        <p className="hint">correlation: {this.state.correlationId}</p>
        <div className="root-error-actions">
          <button className="btn-primary" onClick={() => window.location.reload()}>
            Перезагрузить
          </button>
          <button
            className="btn-ghost"
            title="Забыть сохранённый сценарий и тред и открыть приложение заново"
            onClick={() => {
              for (const key of Object.keys(localStorage)) {
                if (key.startsWith("orbita.")) localStorage.removeItem(key);
              }
              window.location.reload();
            }}
          >
            Сбросить сохранённый выбор
          </button>
        </div>
      </div>
    );
  }
}

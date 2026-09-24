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
 *
 * И четвёртая — отчёт. Страница «Журнал» отсюда недоступна: упало то, что её
 * рисует. Поэтому отчёт собирается прямо здесь, из журнала интерфейса и того,
 * что успеет отдать сервер, и уходит в буфер обмена или файлом.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import { loadServerLog } from "../api";
import { clientEntries, reportClient } from "../lib/clientLog.ts";
import { buildReport, type ReportContext } from "../lib/report.ts";
import { copyText, downloadText, stampedName } from "../lib/share.ts";

async function crashReport(full: boolean): Promise<string> {
  let server: ReportContext["server"];
  let serverError: string | undefined;
  try {
    const log = await loadServerLog({ level: full ? "info" : "warning", limit: full ? 2000 : 200 });
    server = { info: log.server, startedAt: log.started_at, records: log.records };
  } catch (error) {
    serverError = (error as Error).message;
  }
  return buildReport(
    {
      now: new Date(),
      page: window.location.href,
      userAgent: navigator.userAgent,
      client: clientEntries(),
      server,
      serverError,
    },
    { full },
  );
}

type BoundaryState = { error?: Error; correlationId?: string; shared?: string };

export class RootBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = {};

  static getDerivedStateFromError(error: Error) {
    return { error, correlationId: crypto.randomUUID?.() ?? String(Date.now()) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("app render failed", {
      correlationId: this.state.correlationId,
      error: error.message,
      componentStack: info.componentStack,
    });
    reportClient(
      "error",
      "отрисовка",
      `${error.name}: ${error.message}`,
      [`correlation: ${this.state.correlationId}`, error.stack, info.componentStack].filter(Boolean).join("\n"),
    );
  }

  private copyReport = async () => {
    const ok = await copyText(await crashReport(false));
    this.setState({
      shared: ok
        ? "Отчёт скопирован — вставьте его в сообщение разработчику."
        : "Буфер обмена недоступен — скачайте отчёт файлом.",
    });
  };

  private saveReport = async () => {
    downloadText(stampedName("orbita-crash", "txt"), await crashReport(true));
    this.setState({ shared: "Отчёт сохранён файлом — приложите его к сообщению." });
  };

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
          <button onClick={this.copyReport} title="Ошибка, журнал интерфейса и записи сервера — одним текстом">
            Скопировать отчёт
          </button>
          <button className="btn-ghost" onClick={this.saveReport}>
            Скачать отчёт
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
        {this.state.shared ? <p className="hint" role="status">{this.state.shared}</p> : null}
      </div>
    );
  }
}

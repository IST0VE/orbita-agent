/**
 * Журнал: ошибки сервера и интерфейса в одном месте и отчёт для разработчика.
 *
 * Страница нужна тому, кто работает в интерфейсе, а не тому, кто его
 * поддерживает. У оператора нет ни `docker compose logs`, ни консоли
 * браузера, и до журнала ошибка для него кончалась красной плашкой с одной
 * строкой. Теперь у плашки есть продолжение: что именно записал сервер,
 * какие ошибки видел браузер, и кнопка, которая складывает это в текст для
 * сообщения разработчику.
 *
 * Два списка, а не один общий. Записи сервера и браузера отвечают на разные
 * вопросы — «что сломалось там» и «что увидел я» — и смешанные по времени
 * они читаются хуже, чем рядом.
 *
 * Журнал сервера опрашивается, пока страница открыта: ошибку воспроизводят,
 * глядя на журнал, и перезагружать страницу ради новой записи незачем.
 */

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { loadServerLog, type ServerInfo, type ServerLogRecord } from "../../api";
import { clearClient, clientEntries, subscribeClient, type ClientEntry } from "../../lib/clientLog.ts";
import { buildReport, clientText, clock, levelRank, recordText, type ReportContext } from "../../lib/report.ts";
import { copyText, downloadText, stampedName } from "../../lib/share.ts";
import { ChevronRight, Copy, Download, Search, Trash2, X } from "../../ui/icons";

/** Что известно о текущей работе: всё, кроме самих журналов. */
export type JournalContext = Omit<ReportContext, "now" | "page" | "userAgent" | "client" | "server" | "serverError">;

type Tab = "server" | "client";
type LevelFilter = "error" | "warning" | "all";

const POLL_MS = 5000;
const KEEP = 2000;

const LEVEL_FILTERS: { id: LevelFilter; label: string; rank: number }[] = [
  { id: "warning", label: "Предупреждения и ошибки", rank: 30 },
  { id: "error", label: "Только ошибки", rank: 40 },
  { id: "all", label: "Все записи", rank: 0 },
];

const tone = (level: string) => (levelRank(level) >= 40 ? "bad" : levelRank(level) >= 30 ? "warn" : "idle");
const short = (id: string) => (id.length > 12 ? `…${id.slice(-8)}` : id);

export function JournalPage({
  open,
  onClose,
  context,
}: {
  open: boolean;
  onClose: () => void;
  context: JournalContext;
}) {
  const [tab, setTab] = useState<Tab>("server");
  const [level, setLevel] = useState<LevelFilter>("warning");
  const [ownThread, setOwnThread] = useState(false);
  const [query, setQuery] = useState("");
  const [records, setRecords] = useState<ServerLogRecord[]>([]);
  const [info, setInfo] = useState<ServerInfo | undefined>();
  const [startedAt, setStartedAt] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [status, setStatus] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [preview, setPreview] = useState(false);
  const client = useSyncExternalStore(subscribeClient, clientEntries);
  const next = useRef(0);
  /** Какой процесс сервера мы читаем: после перезапуска номера записей начинаются заново. */
  const serverRun = useRef("");
  const page = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  // Опрос: первый запрос забирает всё, что есть, следующие — только новое.
  // Запись с тем же `id` приходит снова, когда у неё прибавился повтор.
  useEffect(() => {
    if (!open) return;
    let live = true;
    let timer = 0;
    const poll = async () => {
      try {
        let value = await loadServerLog({ after: next.current, level: "info", limit: KEEP });
        if (!live) return;
        const restarted = Boolean(serverRun.current) && value.started_at !== serverRun.current;
        if (restarted) {
          // Сервер перезапустился: номера его записей начались с единицы и
          // пересеклись бы с прежними. Прежние снимаются, новые берутся целиком.
          value = await loadServerLog({ after: 0, level: "info", limit: KEEP });
          if (!live) return;
          setRecords([]);
        }
        serverRun.current = value.started_at;
        next.current = value.next;
        setInfo(value.server);
        setStartedAt(value.started_at);
        setLoadError(null);
        if (value.records.length) {
          setRecords((current) => {
            const byId = new Map(current.map((record) => [record.id, record]));
            for (const record of value.records) byId.set(record.id, record);
            return [...byId.values()].sort((a, b) => a.id - b.id).slice(-KEEP);
          });
        }
      } catch (error) {
        if (live) setLoadError((error as Error).message);
      }
      if (live) timer = window.setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [open]);

  // Фокус — как у настроек: на страницу и обратно туда, откуда открыли.
  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    page.current?.focus({ preventScroll: true });
    return () => {
      if (opener.current?.isConnected) opener.current.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open) setStatus(null);
  }, [open]);

  const threadId = context.threadId ?? "";
  const rank = LEVEL_FILTERS.find((item) => item.id === level)?.rank ?? 30;
  const needle = query.trim().toLowerCase();

  const shownServer = useMemo(
    () =>
      records
        .filter((record) => levelRank(record.level) >= rank)
        .filter((record) => !ownThread || !threadId || record.thread_id === threadId)
        .filter(
          (record) =>
            !needle
            || `${record.message}\n${record.logger}\n${record.exception}\n${record.node}\n${record.run_id}\n${record.thread_id}`
              .toLowerCase()
              .includes(needle),
        )
        .reverse(),
    [records, rank, ownThread, threadId, needle],
  );
  const shownClient = useMemo(
    () =>
      client
        .filter((entry) => level !== "error" || entry.level === "error")
        .filter(
          (entry) =>
            !needle || `${entry.message}\n${entry.source}\n${entry.detail ?? ""}`.toLowerCase().includes(needle),
        )
        .slice()
        .reverse(),
    [client, level, needle],
  );

  const serverProblems = records.filter((record) => levelRank(record.level) >= 30).length;
  const clientErrors = client.length;

  const report = (full: boolean) =>
    buildReport(
      {
        ...context,
        now: new Date(),
        page: window.location.href,
        userAgent: navigator.userAgent,
        client,
        server: { info, startedAt, records },
        serverError: loadError && !records.length ? loadError : undefined,
      },
      { full },
    );

  const copyReport = async () => {
    const ok = await copyText(report(false));
    setStatus(
      ok
        ? { tone: "ok", text: "Отчёт скопирован — вставьте его в сообщение разработчику." }
        : { tone: "error", text: "Буфер обмена недоступен — скачайте отчёт файлом." },
    );
  };

  const saveReport = () => {
    downloadText(stampedName("orbita-report", "txt"), report(true));
    setStatus({ tone: "ok", text: "Полный журнал сохранён файлом — приложите его к сообщению." });
  };

  if (!open) return null;

  return (
    <div
      className="journal"
      ref={page}
      tabIndex={-1}
      role="region"
      aria-label="Журнал ошибок"
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}
    >
      <div className="settings-bar journal-bar">
        <h1 className="settings-title">Журнал</h1>
        <div className="tabs" role="group" aria-label="Чей журнал">
          <button className="tab" aria-pressed={tab === "server"} onClick={() => setTab("server")}>
            Сервер
            <span className="console-count">{serverProblems}</span>
          </button>
          <button className="tab" aria-pressed={tab === "client"} onClick={() => setTab("client")}>
            Интерфейс
            <span className="console-count">{clientErrors}</span>
          </button>
        </div>
        <label className="canvas-search">
          <Search size={15} aria-hidden="true" />
          <input
            type="text"
            value={query}
            aria-label="Поиск в журнале"
            placeholder="Найти в журнале…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select aria-label="Важность записей" value={level} onChange={(event) => setLevel(event.target.value as LevelFilter)}>
          {LEVEL_FILTERS.map((item) => (
            <option key={item.id} value={item.id}>{item.label}</option>
          ))}
        </select>
        {tab === "server" ? (
          <label className="journal-check" title={threadId ? `тред ${threadId}` : "Тред ещё не создан"}>
            <input
              type="checkbox"
              checked={ownThread && Boolean(threadId)}
              disabled={!threadId}
              onChange={(event) => setOwnThread(event.target.checked)}
            />
            Только текущий тред
          </label>
        ) : null}
        <span className="settings-bar-spacer" />
        <button onClick={saveReport} title="Всё, что загружено, без сокращений — файлом .txt">
          <Download size={15} aria-hidden="true" />
          Скачать полный
        </button>
        <button className="btn-primary" onClick={copyReport} title="Короткий отчёт для сообщения: ошибки, предупреждения и сведения о сервере">
          <Copy size={15} aria-hidden="true" />
          Скопировать отчёт
        </button>
        <button className="btn-ghost btn-icon" aria-label="Закрыть журнал" title="Закрыть · esc" onClick={onClose}>
          <X size={17} aria-hidden="true" />
        </button>
      </div>

      <div className="journal-body">
        {/* Итог копирования — под полосой, а не в ней: длинная фраза в полосе
            переносила кнопки на вторую строку в момент нажатия. */}
        {status ? <p className={`${status.tone} journal-status`} role="status">{status.text}</p> : null}
        {loadError ? (
          <p className="error journal-load-error" role="alert">
            Журнал сервера не получен: {loadError}
            {records.length ? " Показаны записи, полученные раньше." : ""}
          </p>
        ) : null}

        <p className="hint journal-note">
          {tab === "server"
            ? `Записи сервера агента с ${startedAt ? clock(startedAt) : "его запуска"} — перезапуск сервера журнал очищает. Ключи и токены вычищены ещё при записи.`
            : "Ошибки, которые видела эта вкладка браузера. Журнал переживает перезагрузку страницы, но не закрытие вкладки."}
          {tab === "client" && client.length ? (
            <button className="btn-ghost btn-sm" onClick={clearClient}>
              <Trash2 size={14} aria-hidden="true" />
              Очистить
            </button>
          ) : null}
        </p>

        {tab === "server" ? (
          shownServer.length ? (
            <ol className="journal-list" aria-label="Записи сервера">
              {shownServer.map((record) => (
                <ServerRow key={record.id} record={record} />
              ))}
            </ol>
          ) : (
            <p className="inspector-empty">
              {records.length || needle
                ? "Под выбранные условия записей нет."
                : loadError
                  ? "Записей нет: журнал сервера не получен."
                  : "Сервер пока не записал ничего тревожного."}
            </p>
          )
        ) : shownClient.length ? (
          <ol className="journal-list" aria-label="Ошибки интерфейса">
            {shownClient.map((entry) => (
              <ClientRow key={entry.id} entry={entry} />
            ))}
          </ol>
        ) : (
          <p className="inspector-empty">
            {client.length ? "Под выбранные условия записей нет." : "Интерфейс ошибок не видел."}
          </p>
        )}

        <details className="journal-preview" onToggle={(event) => setPreview(event.currentTarget.open)}>
          <summary>
            <ChevronRight size={14} aria-hidden="true" />
            Что уйдёт в отчёт
          </summary>
          {preview ? <pre>{report(false)}</pre> : null}
        </details>
      </div>
    </div>
  );
}

function CopyRow({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn-ghost btn-icon btn-sm journal-copy"
      aria-label={label}
      title={done ? "Скопировано" : label}
      onClick={async () => {
        setDone(await copyText(text));
        window.setTimeout(() => setDone(false), 1500);
      }}
    >
      <Copy size={14} aria-hidden="true" />
    </button>
  );
}

function ServerRow({ record }: { record: ServerLogRecord }) {
  return (
    <li className="journal-row" data-tone={tone(record.level)}>
      <div className="journal-head">
        <time className="journal-time" dateTime={record.time}>{clock(record.time)}</time>
        <span className="journal-level">{record.level}</span>
        <span className="journal-source mono truncate" title={record.logger}>{record.logger}</span>
        {record.repeats > 1 ? (
          <span className="journal-tag" title={`последний раз ${clock(record.last_time)}`}>×{record.repeats}</span>
        ) : null}
        {record.node ? <span className="journal-tag">узел {record.node}</span> : null}
        {record.thread_id ? (
          <span className="journal-tag mono" title={`тред ${record.thread_id}`}>тред {short(record.thread_id)}</span>
        ) : null}
        <CopyRow text={recordText(record)} label="Скопировать запись" />
      </div>
      <p className="journal-message">{record.message}</p>
      {record.exception ? (
        <details className="journal-trace">
          <summary>
            <ChevronRight size={14} aria-hidden="true" />
            Трассировка
          </summary>
          <pre>{record.exception}</pre>
        </details>
      ) : null}
    </li>
  );
}

function ClientRow({ entry }: { entry: ClientEntry }) {
  return (
    <li className="journal-row" data-tone={entry.level === "error" ? "bad" : "warn"}>
      <div className="journal-head">
        <time className="journal-time" dateTime={entry.time}>{clock(entry.time)}</time>
        <span className="journal-level">{entry.level === "error" ? "ОШИБКА" : "ВНИМАНИЕ"}</span>
        <span className="journal-source truncate">{entry.source}</span>
        {entry.repeats > 1 ? (
          <span className="journal-tag" title={`последний раз ${clock(entry.lastTime)}`}>×{entry.repeats}</span>
        ) : null}
        <CopyRow text={clientText(entry)} label="Скопировать запись" />
      </div>
      <p className="journal-message">{entry.message}</p>
      {entry.detail ? (
        <details className="journal-trace">
          <summary>
            <ChevronRight size={14} aria-hidden="true" />
            Подробности
          </summary>
          <pre>{entry.detail}</pre>
        </details>
      ) : null}
    </li>
  );
}

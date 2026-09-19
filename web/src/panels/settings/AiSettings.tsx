/**
 * Раздел «Модель»: четыре поля, которые заполняют все.
 *
 * Провайдер, модель, адрес и ключ — этого достаточно, чтобы ORBITA заработала.
 * Остальные восемь переменных модели (температура, повторы, таймаут, лимиты
 * токенов) лежат в «Продвинутых»: их трогают тогда, когда знают зачем, и
 * держать их рядом с ключом значит требовать читать десять строк вместо
 * четырёх.
 *
 * Состояние подключения показывается здесь же. Отказ сервера — это то, что
 * чинят в этом разделе, и сообщать о нём в другом месте экрана значит
 * заставлять искать причину.
 */

import type { ServerStatus, Setting, SettingsDoc } from "../../api";
import type { SettingsGroupId } from "../../app/sections";
import { StatusDot } from "../../ui";
import { AI_CONNECTION_FIELDS, AI_PROVIDER_FIELDS } from "./groups";
import { SettingsField } from "./SettingsField";

const CONNECTION: Record<string, { label: string; tone: "ok" | "warn" | "bad" }> = {
  ok: { label: "Подключено", tone: "ok" },
  unauthorized: { label: "Сервер агента отвергает токен", tone: "warn" },
  offline: { label: "Сервер агента недоступен", tone: "bad" },
};

export function AiSettings({
  doc,
  drafts,
  notes,
  onChange,
  onNote,
  online,
  onAdvanced,
}: {
  doc: SettingsDoc;
  drafts: Record<string, string>;
  notes: Record<string, string>;
  onChange: (name: string, value: string | undefined) => void;
  onNote: (name: string, value: string | undefined) => void;
  online: ServerStatus | null;
  onAdvanced: (group: SettingsGroupId, filter: string) => void;
}) {
  const all = doc.sections.flatMap((section) => section.fields);
  const pick = (names: string[]): Setting[] =>
    names.map((name) => all.find((field) => field.name === name)).filter((field): field is Setting => Boolean(field));
  const provider = pick(AI_PROVIDER_FIELDS);
  const connection = pick(AI_CONNECTION_FIELDS);
  const rest = all.filter(
    (field) => /^LLM_/.test(field.name)
      && !AI_PROVIDER_FIELDS.includes(field.name)
      && !AI_CONNECTION_FIELDS.includes(field.name),
  );
  const status = online === null ? { label: "Проверяем связь…", tone: "idle" as const } : CONNECTION[online] ?? CONNECTION.offline;

  const field = (item: Setting) => (
    <SettingsField
      key={item.name}
      field={item}
      draft={drafts[item.name]}
      onChange={onChange}
      note={notes[item.name]}
      onNote={onNote}
    />
  );

  return (
    <>
      {provider.length ? (
        <div className="set-section">
          <h3>Модель</h3>
          {provider.map(field)}
        </div>
      ) : null}

      {connection.length ? (
        <div className="set-section">
          <h3>Подключение</h3>
          {connection.map(field)}
          <div className="set-row">
            <div className="set-field">
              <span className="set-label">
                <span className="set-name">Состояние подключения</span>
              </span>
              <span className="set-control set-connection">
                <StatusDot tone={status.tone} />
                {status.label}
              </span>
            </div>
            <div className="set-desc">
              Проверяется фоном каждые пятнадцать секунд. Правка адреса и ключа
              применяется после перезапуска сервера агента.
            </div>
          </div>
        </div>
      ) : null}

      {!provider.length && !connection.length ? (
        <p className="inspector-empty">
          Сервер не отдал ни одной переменной модели: проверьте, что
          в `.env.example` объявлены LLM_PROVIDER, LLM_MODEL и LLM_API_BASE.
        </p>
      ) : null}

      {rest.length ? (
        <p className="settings-more">
          Остальные параметры модели — температура, повторы, таймаут и лимиты
          токенов, всего {rest.length} — лежат в разделе «Продвинутые».
          <button className="btn-ghost btn-sm" onClick={() => onAdvanced("advanced", "LLM_")}>
            Показать
          </button>
        </p>
      ) : null}
    </>
  );
}

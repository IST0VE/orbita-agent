/**
 * Инспектор до первого прогона: что это за сценарий и чего ему не хватает.
 *
 * Показывать здесь пустую стоимость и пустую публикацию бессмысленно — до
 * запуска их не существует. А вот ответ на вопрос «почему кнопка ничего не
 * даст» до запуска нужен ровно один раз и именно здесь: перечислены поля
 * сценария и то, что в них сейчас выбрано.
 */

import type { UiManifest } from "../../engine/manifest/types";
import { localized } from "../../engine/manifest/validate";
import { Play } from "../../ui/icons";

/** Значение поля ввода одной строкой: имя файла, список имён или прочерк. */
function chosen(value: unknown): string {
  if (Array.isArray(value)) {
    return value.filter((item) => typeof item === "string" && item).join(", ") || "не выбрано";
  }
  return typeof value === "string" && value ? value : "не выбрано";
}

export function ScenarioInspector({
  manifest,
  inputs,
  title,
}: {
  manifest: UiManifest;
  inputs: Record<string, unknown>;
  title: string;
}) {
  // Поле задачи в список не попадает: это не параметр, а сам вопрос, и стоит
  // он в поле ввода внизу экрана, где его и заполняют.
  const fields = (manifest.input ?? []).filter((input) => input.widget !== "chat-input");
  const description = manifest.description ? localized(manifest.description) : "";

  return (
    <div className="inspector-body">
      <div className="inspector-section">
        <h3 className="inspector-heading">{title}</h3>
        {description ? <p className="inspector-note">{description}</p> : null}
      </div>

      {fields.length ? (
        <div className="inspector-section">
          <span className="eyebrow">Материалы прогона</span>
          <dl className="key-value">
            {fields.map((field) => (
              <div key={field.id}>
                <dt>{field.title ?? field.id}</dt>
                <dd className="truncate" title={chosen(inputs[field.id])}>{chosen(inputs[field.id])}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}

      <div className="inspector-section">
        <p className="inspector-hint">
          <Play size={14} aria-hidden="true" />
          Опишите задачу внизу экрана и запустите сценарий — здесь появятся показатели прогона.
        </p>
      </div>
    </div>
  );
}

/**
 * Выбор материалов: папка задачи и файлы в ней.
 *
 * Единственные виджеты, которые сами ходят в служебный API за списком.
 * Отсюда их размер и отсюда же — отдельный файл.
 */
import { useCallback, useEffect, useState } from "react";
import { formatBytes } from "../../../lib/orbita";
import { names, text } from "./shared";
import type { WidgetProps } from "../../manifest/types";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Eye,
  File,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  X,
} from "../../../ui/icons";

/**
 * Выбор папки задачи, а внутри неё — файла-источника.
 *
 * Папки вывода показывают содержимое всегда, а не после выбора: это готовые
 * документы, за ними приходят читать, а не только чтобы пустить по ним прогон.
 * Папки задач раскрываются по выбору — их файлы относятся к будущему ходу.
 *
 * Клик по файлу задачи ВЫБИРАЕТ его источником, а не открывает. Раньше он
 * открывал: файл показывался на главном экране, и это было единственное, что
 * с ним можно было сделать. Выбор при этом существовал в графах и раньше —
 * но задать его можно было только словами в запросе, и оператор, ткнувший в
 * нужный документ, получал прогон по всей папке. Так что клик значит теперь
 * то, ради чего в него и целятся, а просмотр уехал на соседнюю кнопку.
 *
 * Куда уезжает выбор, говорит манифест: `options.document_input` — id поля,
 * `options.document_kind` — что этому полю годится (схема или текст). Не
 * названо поле — файлы открываются по клику: выбирать их некуда.
 *
 * Файлы папки вывода выбираются наравне с файлами задачи. Раньше их можно было
 * только открыть: считалось, что готовый документ — конец пути. Но конец пути
 * одного конвейера это вход другого — написанную аналитику раскладывает
 * на задачи граф Jira-декомпозиции, — и выбор источника там тот же самый
 * `configurable.input_file`. Запрет стоял только в этом виджете: соседнее поле
 * «Документ» те же файлы предлагало, а граф их читал. Оператор, ткнувший в
 * готовый документ, получал его просмотр вместо выбора и прогон по всей папке.
 */
export function TaskPickerWidget({ binding, context, value, onChange, onAction, readonly }: WidgetProps) {
  const [items, setItems] = useState<
    Array<{
      name: string;
      title?: string;
      kind?: "input" | "output";
      files?: Array<{ name: string; size: number; text?: boolean; diagram?: boolean }>;
    }>
  >([]);
  const [error, setError] = useState("");
  const [newTask, setNewTask] = useState("");
  /** Что оператор свернул или раскрыл руками; остальное — по умолчанию. */
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const resourceId = text(binding.options?.resource_id);
  const operation = text(binding.options?.operation) || "list";
  const documentInput = text(binding.options?.document_input);
  const documentKind = text(binding.options?.document_kind) || "text";
  /** Сколько файлов влезает в поле-приёмник; сказано манифестом, не виджетом. */
  const documentMultiple = binding.options?.document_multiple === true;
  const resource = context.resource;
  const mutateResource = context.mutateResource;
  const setInput = context.setInput;
  const chosen = documentInput ? names(context.inputs[documentInput]) : [];
  /** Пустое значение поля-приёмника: у списка это список, а не пустая строка. */
  const cleared = documentMultiple ? [] : "";
  const reload = useCallback(() => resource(resourceId, operation).then((response) => {
    const tasks = (
      response as {
        tasks?: Array<{
          name: string;
          title?: string;
          kind?: "input" | "output";
          files?: Array<{ name: string; size: number; text?: boolean; diagram?: boolean }>;
        }>;
      }
    )?.tasks;
    setItems(Array.isArray(tasks) ? tasks : []);
    setError("");
  }), [operation, resource, resourceId]);
  useEffect(() => {
    let live = true;
    reload().catch((reason: Error) => live && setError(reason.message));
    return () => { live = false; };
  }, [reload]);
  const selected = items.find((item) => item.name === text(value));
  // Сменили папку — снимаем выбранный в прежней файл: он в новой не лежит, а
  // оставленное имя уехало бы в прогон и обернулось отказом «файла нет».
  const choose = (name: string) => {
    const next = text(value) === name ? "" : name;
    if (documentInput && next !== text(value)) setInput(documentInput, cleared);
    onChange?.(next);
  };
  const pick = (task: string, name: string) => {
    // Файл выбирают в папке, которая для этого хода и выбрана. Клик по файлу
    // чужой папки сначала переносит выбор туда — иначе он молча ничего бы
    // не значил, — и начинает выбор заново: имена прежней папки в новой
    // не лежат.
    if (text(value) !== task) {
      onChange?.(task);
      setInput(documentInput, documentMultiple ? [name] : name);
      return;
    }
    if (!documentMultiple) {
      setInput(documentInput, chosen.includes(name) ? "" : name);
      return;
    }
    setInput(
      documentInput,
      chosen.includes(name) ? chosen.filter((item) => item !== name) : [...chosen, name],
    );
  };
  const fold = (name: string, open: boolean) =>
    setFolded((previous) => ({ ...previous, [name]: !open }));
  // Документ открывается на главном экране, а не в углу панели: читать его
  // в колонке шириной с список файлов невозможно.
  const open = (task: string, name: string) =>
    resource(resourceId, "read", { task, name })
      .then((response) => {
        const document = response as { name: string; text: string };
        onAction?.({
          kind: "publication.open",
          payload: { title: document.name, text: document.text },
        });
        setError("");
      })
      .catch((reason: Error) => setError(reason.message));
  const create = () => {
    const name = newTask.trim();
    if (!name) return;
    mutateResource(resourceId, "create", { name })
      .then(() => reload())
      .then(() => {
        onChange?.(name);
        setNewTask("");
      })
      .catch((reason: Error) => setError(reason.message));
  };
  return (
    <div className="task-picker">
      <div className="task-picker-label">
        Папка задачи
        <span className={selected ? "task-picker-current" : "hint"}>
          {selected ? selected.title || selected.name : "не выбрана"}
        </span>
      </div>
      <div className="task-picker-folders" aria-label="Папка задачи">
        {items.map((item) => {
          const active = item.name === text(value);
          const output = item.kind === "output";
          const files = item.files?.length ?? 0;
          // По умолчанию раскрыта папка вывода и выбранная задача; решение
          // оператора важнее умолчания и живёт до перезагрузки.
          const expanded = folded[item.name] === undefined ? output || active : !folded[item.name];
          return (
            <div className="task-picker-folder" key={item.name}>
              <div className="task-picker-row">
                {files ? (
                  <button
                    type="button"
                    className="task-picker-toggle"
                    aria-expanded={expanded}
                    aria-label={`${expanded ? "свернуть" : "развернуть"} ${item.title || item.name}`}
                    title={expanded ? "свернуть" : "развернуть"}
                    onClick={() => fold(item.name, !expanded)}
                  >
                    {expanded
                      ? <ChevronDown size={14} aria-hidden="true" />
                      : <ChevronRight size={14} aria-hidden="true" />}
                  </button>
                ) : (
                  <span className="task-picker-toggle" />
                )}
                <button
                  type="button"
                  aria-pressed={active}
                  disabled={readonly}
                  className={`${active ? "selected" : ""} ${output ? "output" : ""}`}
                  onClick={() => choose(item.name)}
                  title={output ? "готовая документация из папки outputs" : "выбрать папку для прогона"}
                >
                  <span className="mark">
                    {expanded
                      ? <FolderOpen size={15} aria-hidden="true" />
                      : <Folder size={15} aria-hidden="true" />}
                  </span>
                  <span className="name">{item.title || item.name}</span>
                  {output ? <span className="badge">out</span> : null}
                  <span className="count">{files}</span>
                </button>
              </div>
              {expanded && item.files?.length ? (
                <ul className="resource-list task-picker-files">
                  {item.files.map((file) => {
                    const readable = file.text !== false || Boolean(file.diagram);
                    // Выбирать можно только то, что выбранному полю годится:
                    // .drawio в поле схемы, текст — в поле документа.
                    const selectable = Boolean(documentInput) && fileMatches(file, documentKind);
                    const picked =
                      selectable && item.name === text(value) && chosen.includes(file.name);
                    return (
                      <li
                        key={file.name}
                        className={`${file.diagram ? "diagram" : file.text === false ? "binary" : "text"}${picked ? " picked" : ""}`}
                      >
                        <button
                          disabled={readonly ? selectable : !readable}
                          aria-pressed={selectable ? picked : undefined}
                          title={
                            selectable
                              ? picked
                                ? documentMultiple && chosen.length > 1
                                  ? `${file.name}: убрать из выбранных`
                                  : `${file.name}: снять выбор — конвейер прочитает папку целиком`
                                : documentMultiple && chosen.length
                                  ? `${file.name}: добавить к выбранным`
                                  : `${file.name}: работать по этому файлу`
                              : file.name
                          }
                          onClick={() =>
                            selectable ? pick(item.name, file.name) : open(item.name, file.name)
                          }
                        >
                          <span className="mark">
                            {selectable && picked
                              ? <Check size={14} aria-hidden="true" />
                              : file.diagram
                                ? <FileText size={14} aria-hidden="true" />
                                : <File size={14} aria-hidden="true" />}
                          </span>
                          <span className="name">{file.name}</span>
                        </button>
                        {selectable && readable ? (
                          <button
                            className="task-picker-open"
                            title={`${file.name}: показать содержимое`}
                            aria-label={`показать ${file.name}`}
                            onClick={() => open(item.name, file.name)}
                          >
                            <Eye size={14} aria-hidden="true" />
                          </button>
                        ) : null}
                        <span className="hint">{formatBytes(file.size)}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="resource-create">
        <input
          value={newTask}
          disabled={readonly}
          aria-label="Имя новой папки задачи"
          placeholder="Новая папка"
          onChange={(event) => setNewTask(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") create(); }}
        />
        <button disabled={readonly || !newTask.trim()} onClick={create}>
          <FolderPlus size={15} aria-hidden="true" />
          Создать
        </button>
      </div>
      {error ? <span className="error">{error}</span> : null}
    </div>
  );
}


/** Годится ли файл в источник для поля с таким `kind`. */
export function fileMatches(file: { text?: boolean; diagram?: boolean }, kind: string): boolean {
  return kind === "diagram" ? Boolean(file.diagram) : file.text !== false;
}


/**
 * Что выбрано источником — и ничего больше.
 *
 * Панель показывала весь каталог папки: тот же список, что стоит деревом
 * выше, только плоский. Второй такой же список — это не выбор, а шум, в
 * котором выбранный файл ничем не выделен; выбирают в дереве, где файлы
 * лежат по папкам. Здесь остаётся ответ на единственный вопрос, который
 * задают перед запуском: что именно уедет в прогон.
 *
 * Два поля, один виджет, разница в `options.kind`: `diagram` — схема draw.io
 * для графа разбора схем, `text` — документы остальных конвейеров.
 * `options.multiple` говорит, сколько имён поле держит: схема одна, комплект
 * документации — сколько выбрали.
 *
 * Список папки всё-таки читается, но не рисуется: по нему видно, что
 * выбранного файла в папке больше нет — папку сменили, файл удалили. Молчать
 * об этом нельзя, а узнать это без списка неоткуда.
 *
 * Пустой выбор — законное значение, и у полей оно значит разное: для схемы —
 * «возьми первую и скажи, какую», для документа — «читай папку целиком».
 */
export function FilePickerWidget({ binding, context, value, onChange, readonly }: WidgetProps) {
  const [available, setAvailable] = useState<string[] | null>(null);
  const [hint, setHint] = useState("");
  const resourceId = text(binding.options?.resource_id);
  const operation = text(binding.options?.operation) || "list";
  const kind = text(binding.options?.kind) || "text";
  const multiple = binding.options?.multiple === true;
  const dependsOn = text(binding.options?.depends_on);
  const task = text(dependsOn ? context.inputs[dependsOn] : "");
  const resource = context.resource;
  useEffect(() => {
    let live = true;
    if (!task) {
      setAvailable(null);
      setHint("сначала выберите папку задачи");
      return;
    }
    resource(resourceId, operation)
      .then((response) => {
        if (!live) return;
        const tasks = (response as {
          tasks?: Array<{ name: string; files?: Array<{ name: string; diagram?: boolean }> }>;
        })?.tasks;
        const folder = (Array.isArray(tasks) ? tasks : []).find((item) => item.name === task);
        setAvailable((folder?.files ?? []).filter((file) => fileMatches(file, kind)).map((file) => file.name));
        setHint("");
      })
      .catch((reason: Error) => live && setHint(reason.message));
    return () => { live = false; };
  }, [kind, operation, resource, resourceId, task]);
  const chosen = names(value);
  // Выбранное имя не сбрасывается автоматически при смене папки: молча стереть
  // выбор оператора хуже, чем показать, что в новой папке такого файла нет.
  const missing = available ? chosen.filter((name) => !available.includes(name)) : [];
  const drop = (name: string) => {
    const rest = chosen.filter((item) => item !== name);
    onChange?.(multiple ? rest : "");
  };
  return <div className="file-picker">
    {chosen.length ? (
      <ul className="resource-list">
        {chosen.map((name) => (
          <li key={name} className={missing.includes(name) ? "missing" : undefined}>
            <span className="name" title={name}>
              <span className="mark"><FileText size={14} aria-hidden="true" /></span>
              <span className="text">{name}</span>
            </span>
            <button
              type="button"
              className="file-picker-drop"
              disabled={readonly}
              title={`убрать ${name} из выбранных`}
              aria-label={`убрать ${name} из выбранных`}
              onClick={() => drop(name)}
            >
              <X size={14} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
    ) : null}
    {!chosen.length && task ? (
      <span className="hint">
        {text(binding.options?.empty_hint) || (kind === "diagram"
          ? "не выбрана: граф возьмёт первую и скажет, какую"
          : "не выбран: конвейер прочитает папку целиком")}
      </span>
    ) : null}
    {chosen.length ? (
      <span className="hint">
        {multiple ? "выбрано в дереве файлов; клик по файлу добавляет и убирает" : "выбрано в дереве файлов"}
      </span>
    ) : null}
    {missing.map((name) => (
      <span className="error" key={name}>{name}: в папке {task} такого файла нет</span>
    ))}
    {hint ? <span className="hint">{hint}</span> : null}
  </div>;
}

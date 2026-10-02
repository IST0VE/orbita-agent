/**
 * Файлы чата: что пользователь загрузил в этот чат и что из этого уедет в прогон.
 *
 * Раньше здесь стояло дерево общих папок задач: его видел каждый вошедший, и
 * материалы одного человека лежали на виду у всех. Теперь файл загружается в
 * чат и живёт только в нём — это папка треда на сервере, а владельца треда
 * проверяет сервер LangGraph. Какую папку читать, интерфейс не выбирает: поле
 * шлёт постоянное `@chat` (`options.fixed`), а тред сервер знает сам.
 *
 * Отметка у файла — это выбор, куда он уедет. Куда именно, говорит манифест
 * (`options.pick`): у обычных конвейеров одна отметка «в прогон», у графа
 * схем — «эта схема», у обновления документа две — «основной» и «материал».
 * Виджет не знает имён этих полей: он пишет в то поле, которое назвали.
 */
import { useEffect, useRef, useState, type DragEvent } from "react";

import { formatBytes } from "../../../lib/orbita";
import { Modal } from "../../../Modal";
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  Plus,
  Trash2,
  Upload,
  X,
} from "../../../ui/icons";
import type { ChatFileEntry, ChatFilesContext, ChatLibrary, JsonValue, WidgetProps } from "../../manifest/types";
import { names, text } from "./shared";
import { fileMatches } from "./pickers";

/** Куда уезжает отметка файла: id поля ввода, что ему годится и сколько файлов. */
export type FilePick = { input: string; kind: string; multiple: boolean; label: string };

/** Отметки из манифеста. Кривой элемент пропускается: он не повод ронять список. */
export function picksOf(options: Record<string, JsonValue> | undefined): FilePick[] {
  const raw = options?.pick;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const input = text(item.input);
    if (!input) return [];
    return [{
      input,
      kind: text(item.kind) || "text",
      multiple: item.multiple === true,
      label: text(item.label),
    }];
  });
}

/** Что сказать под списком: как поведёт себя прогон с таким выбором. */
function selectionHint(picks: FilePick[], files: ChatFileEntry[], inputs: Record<string, unknown>): string {
  if (!files.length) return "";
  if (picks.length > 1) {
    return "Отметьте основной документ и новые материалы: без отметок обновлять нечего.";
  }
  const pick = picks[0];
  if (!pick) return "";
  const fitting = files.filter((file) => fileMatches(file, pick.kind));
  const chosen = names(inputs[pick.input]).filter((name) => fitting.some((file) => file.name === name));
  if (pick.kind === "diagram") {
    if (chosen.length) return "";
    if (!fitting.length) return "Схемы .drawio в чате нет: загрузите её.";
    return fitting.length > 1
      ? "Схем несколько — отметьте одну: без отметки граф откажет, а не возьмёт первую."
      : "Схема одна — граф возьмёт её и сам. Отметка нужна, когда схем станет больше.";
  }
  if (!chosen.length) return "Ничего не отмечено — прогон прочитает все файлы чата.";
  return chosen.length === fitting.length
    ? "Отмечены все файлы чата."
    : `Отмечено ${chosen.length} из ${fitting.length}: прогон начнёт с них.`;
}

export function ChatFilesWidget({ binding, context, onAction, readonly }: WidgetProps) {
  const chat = context.chat;
  const picks = picksOf(binding.options);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState<string[]>([]);
  const [error, setError] = useState("");
  /** Файл, для которого первое нажатие «удалить» уже было: второе удаляет. */
  const [armed, setArmed] = useState<string | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const depth = useRef(0);

  // Подтверждение удаления живёт несколько секунд: промахнувшийся по корзине
  // не должен вернуться к файлу, который удалится от случайного клика.
  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(null), 4000);
    return () => window.clearTimeout(timer);
  }, [armed]);

  if (!chat) return <span className="hint">Файлы чата в этом окне недоступны.</span>;
  const files = chat.files ?? [];
  const locked = readonly || uploading.length > 0;

  const upload = async (list: File[]) => {
    if (!list.length || locked) return;
    setError("");
    setUploading(list.map((file) => file.name));
    try {
      await chat.upload(list);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setUploading([]);
    }
  };

  const toggle = (pick: FilePick, name: string) => {
    const chosen = names(context.inputs[pick.input]);
    if (!pick.multiple) {
      context.setInput(pick.input, chosen.includes(name) ? "" : name);
      return;
    }
    context.setInput(
      pick.input,
      chosen.includes(name) ? chosen.filter((item) => item !== name) : [...chosen, name],
    );
  };

  const remove = (name: string) => {
    if (armed !== name) {
      setArmed(name);
      return;
    }
    setArmed(null);
    chat.remove(name)
      .then(() => {
        // Удалённый файл не должен уехать в прогон отметкой: граф откажет
        // «файла нет», и причина будет не видна.
        for (const pick of picks) {
          const chosen = names(context.inputs[pick.input]);
          if (!chosen.includes(name)) continue;
          context.setInput(pick.input, pick.multiple ? chosen.filter((item) => item !== name) : "");
        }
      })
      .catch((reason: Error) => setError(reason.message));
  };

  const open = (name: string) => {
    chat.read(name)
      .then((document) => onAction?.({
        kind: "publication.open",
        payload: { title: document.name, text: document.text },
      }))
      .catch((reason: Error) => setError(reason.message));
  };

  const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes("Files");
  const single = picks.length === 1 ? picks[0] : null;
  const hint = selectionHint(picks, files, context.inputs);

  return (
    <div
      className={`chat-files${dragging ? " dragging" : ""}`}
      onDragEnter={(event) => {
        if (!hasFiles(event) || locked) return;
        event.preventDefault();
        depth.current += 1;
        setDragging(true);
      }}
      onDragOver={(event) => {
        if (!hasFiles(event) || locked) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (!depth.current) setDragging(false);
      }}
      onDrop={(event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        depth.current = 0;
        setDragging(false);
        void upload(Array.from(event.dataTransfer.files));
      }}
    >
      <div className="chat-files-head">
        <span className="chat-files-title">
          {binding.title ?? "Файлы чата"}
          <span className="hint">{chat.files === null ? "…" : files.length}</span>
        </span>
        <button
          type="button"
          className="btn-sm"
          disabled={locked}
          title="Загрузить файлы с компьютера в этот чат"
          onClick={() => picker.current?.click()}
        >
          <Upload size={14} aria-hidden="true" />
          Загрузить
        </button>
        <button
          type="button"
          className="btn-ghost btn-icon btn-sm"
          disabled={locked}
          aria-label="Добавить из библиотеки"
          title="Добавить копию примера или своего опубликованного документа"
          onClick={() => setLibraryOpen(true)}
        >
          <BookOpen size={15} aria-hidden="true" />
        </button>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          accept={chat.limits?.suffixes.join(",")}
          onChange={(event) => {
            const list = Array.from(event.currentTarget.files ?? []);
            event.currentTarget.value = "";
            void upload(list);
          }}
        />
      </div>

      {files.length ? (
        <ul className="resource-list chat-files-list" aria-label="Файлы чата">
          {files.map((file) => {
            const pickedSingle = Boolean(single && names(context.inputs[single.input]).includes(file.name));
            const fits = Boolean(single && fileMatches(file, single.kind));
            return (
              <li
                key={file.name}
                className={`${file.diagram ? "diagram" : "text"}${pickedSingle ? " picked" : ""}`}
              >
                {single ? (
                  <button
                    type="button"
                    className="chat-file-pick"
                    role="checkbox"
                    aria-checked={pickedSingle}
                    disabled={readonly || !fits}
                    aria-label={`${pickedSingle ? "снять отметку" : "отметить"} ${file.name}`}
                    title={
                      !fits
                        ? single.kind === "diagram" ? "Это не схема draw.io" : "Этот файл роль не прочитает"
                        : pickedSingle ? "Снять отметку" : single.multiple ? "Отметить для прогона" : "Разбирать этот файл"
                    }
                    onClick={() => toggle(single, file.name)}
                  >
                    {pickedSingle ? <Check size={13} aria-hidden="true" /> : null}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="chat-file-name"
                  title={`${file.name}: показать содержимое`}
                  onClick={() => open(file.name)}
                >
                  <span className="name">
                    <span className="mark"><FileText size={14} aria-hidden="true" /></span>
                    <span className="text">{file.name}</span>
                  </span>
                </button>
                {picks.length > 1
                  ? picks.map((pick) => {
                    if (!fileMatches(file, pick.kind)) return null;
                    const on = names(context.inputs[pick.input]).includes(file.name);
                    return (
                      <button
                        key={pick.input}
                        type="button"
                        className="chat-file-role"
                        aria-pressed={on}
                        disabled={readonly}
                        title={on ? `Убрать из «${pick.label || pick.input}»` : `Отметить как «${pick.label || pick.input}»`}
                        onClick={() => toggle(pick, file.name)}
                      >
                        {pick.label || pick.input}
                      </button>
                    );
                  })
                  : null}
                <span className="hint">{formatBytes(file.size)}</span>
                <button
                  type="button"
                  className={`chat-file-remove${armed === file.name ? " armed" : ""}`}
                  disabled={locked}
                  aria-label={armed === file.name ? `подтвердить удаление ${file.name}` : `удалить ${file.name}`}
                  title={armed === file.name ? "Нажмите ещё раз, чтобы удалить" : "Удалить файл из чата"}
                  onClick={() => remove(file.name)}
                >
                  {armed === file.name ? "Удалить?" : <Trash2 size={14} aria-hidden="true" />}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}

      {!files.length && chat.files !== null ? (
        <button
          type="button"
          className="chat-files-drop"
          disabled={locked}
          onClick={() => picker.current?.click()}
        >
          <Upload size={18} aria-hidden="true" />
          <span>Перетащите файлы сюда или нажмите, чтобы выбрать</span>
          <span className="hint">Файлы видны только в этом чате</span>
        </button>
      ) : null}

      {uploading.length ? <span className="hint" role="status">Загрузка: {uploading.join(", ")}…</span> : null}
      {hint ? <span className="hint">{hint}</span> : null}
      {chat.error ? <span className="error">{chat.error}</span> : null}
      {error ? <span className="error" role="alert">{error}</span> : null}
      {dragging ? <div className="chat-files-overlay" aria-hidden="true">Отпустите, чтобы добавить в чат</div> : null}

      {libraryOpen ? (
        <ChatLibraryDialog chat={chat} onClose={() => setLibraryOpen(false)} />
      ) : null}
    </div>
  );
}

/**
 * Библиотека: что можно добавить в чат копией.
 *
 * Примеры — общие папки задач, их заводит администратор. Свои документы —
 * опубликованное этим пользователем. В чат уезжает копия: чат читает только
 * свою папку, и граница у него одна.
 */
function ChatLibraryDialog({ chat, onClose }: { chat: ChatFilesContext; onClose: () => void }) {
  const [library, setLibrary] = useState<ChatLibrary | null>(null);
  const [error, setError] = useState("");
  const [added, setAdded] = useState<string[]>([]);
  const [pending, setPending] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let live = true;
    chat.library()
      .then((value) => live && setLibrary(value))
      .catch((reason: Error) => live && setError(reason.message));
    return () => { live = false; };
  }, [chat]);

  const add = (key: string, source: { source: "examples" | "published"; name: string; example?: string }) => {
    setPending(key);
    setError("");
    chat.attach(source)
      .then(() => setAdded((previous) => [...previous, key]))
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setPending(""));
  };

  const row = (key: string, label: string, size: number, source: Parameters<typeof add>[1]) => (
    <li key={key}>
      <span className="name" title={label}>
        <span className="mark"><FileText size={14} aria-hidden="true" /></span>
        <span className="text">{label}</span>
      </span>
      <span className="hint">{formatBytes(size)}</span>
      <button
        type="button"
        className="btn-sm"
        disabled={Boolean(pending) || added.includes(key)}
        onClick={() => add(key, source)}
      >
        {added.includes(key) ? <Check size={14} aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
        {added.includes(key) ? "В чате" : pending === key ? "Копирую…" : "В чат"}
      </button>
    </li>
  );

  return (
    <Modal label="Добавить в чат" className="chat-library" onClose={onClose}>
      <div className="chat-library-head">
        <h2>Добавить в чат</h2>
        <button className="btn-ghost btn-icon btn-sm" aria-label="Закрыть" onClick={onClose}>
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <p className="hint">
        В чат уезжает копия: исходный документ не меняется, а копия видна только в этом чате.
      </p>
      {!library && !error ? <span className="hint">Загрузка…</span> : null}
      {error ? <span className="error" role="alert">{error}</span> : null}
      {library ? (
        <div className="chat-library-body">
          <section>
            <h3 className="eyebrow">Мои документы</h3>
            {library.published.length ? (
              <ul className="resource-list chat-library-list">
                {library.published.map((item) =>
                  row(`published:${item.name}`, item.title || item.name, item.size, { source: "published", name: item.name }),
                )}
              </ul>
            ) : (
              <span className="hint">Опубликованных документов пока нет.</span>
            )}
          </section>
          <section>
            <h3 className="eyebrow">Примеры</h3>
            {library.examples.length ? library.examples.map((example) => {
              const expanded = Boolean(open[example.name]);
              return (
                <div className="chat-library-folder" key={example.name}>
                  <button
                    type="button"
                    className="btn-ghost chat-library-toggle"
                    aria-expanded={expanded}
                    onClick={() => setOpen((previous) => ({ ...previous, [example.name]: !expanded }))}
                  >
                    {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                    <Folder size={15} aria-hidden="true" />
                    <span className="truncate">{example.title}</span>
                    <span className="hint">{example.files.length}</span>
                  </button>
                  {expanded ? (
                    <ul className="resource-list chat-library-list">
                      {example.files.map((file) =>
                        row(`examples:${example.name}/${file.name}`, file.name, file.size, {
                          source: "examples",
                          example: example.name,
                          name: file.name,
                        }),
                      )}
                    </ul>
                  ) : null}
                </div>
              );
            }) : <span className="hint">Администратор ещё не положил примеров.</span>}
          </section>
        </div>
      ) : null}
      <div className="chat-library-foot">
        <button className="btn-primary btn-sm" onClick={onClose}>Готово</button>
      </div>
    </Modal>
  );
}

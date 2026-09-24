/**
 * Отдать текст оператору: в буфер обмена или файлом.
 *
 * Буфер — основной путь: отчёт вставляют в сообщение. Но Clipboard API
 * бывает недоступен (запрет браузера, фокус ушёл из вкладки), и тогда
 * пробуется старый способ через выделение. Не вышло и он — остаётся файл,
 * поэтому `copyText` сообщает об успехе, а не молчит.
 */

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      area.remove();
    }
  }
}

export function downloadText(name: string, text: string, type = "text/plain;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  // Отзыв ссылки на следующем тике: синхронный revoke успевает отменить
  // скачивание, которое click() только что начал.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** Имя файла с меткой времени: несколько отчётов подряд не затирают друг друга. */
export function stampedName(prefix: string, extension: string, now = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${prefix}-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
    + `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.${extension}`;
}

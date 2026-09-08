/**
 * Символьное полотно: сетка знаков, на которой рисуется граф.
 *
 * Ни SVG, ни canvas: интерфейс собран из моноширинных знаков, и схема должна
 * быть из них же — её отдают в тикет текстом, а не картинкой (`toText()`).
 * Полотно хранит два слоя одинакового размера: сам символ и класс раскраски.
 * Раскраска отдельно от символа, потому что состояние узла меняется на каждом
 * событии стрима, а рисунок при этом не двигается.
 *
 * Соседние знаки одного класса собираются в один <span> — иначе на схеме из
 * трёх тысяч ячеек браузер получил бы три тысячи элементов на каждый кадр.
 */

export type Cell = { ch: string; cls: string };

export class Canvas {
  readonly width: number;
  readonly height: number;
  private readonly chars: string[];
  private readonly classes: string[];

  constructor(width: number, height: number) {
    this.width = Math.max(0, width);
    this.height = Math.max(0, height);
    const size = this.width * this.height;
    this.chars = new Array<string>(size).fill(" ");
    this.classes = new Array<string>(size).fill("");
  }

  put(x: number, y: number, ch: string, cls = ""): void {
    if (x < 0 || y < 0 || x >= this.width || y >= this.height) return;
    const i = y * this.width + x;
    this.chars[i] = ch;
    this.classes[i] = cls;
  }

  text(x: number, y: number, value: string, cls = ""): void {
    for (let i = 0; i < value.length; i += 1) this.put(x + i, y, value[i], cls);
  }

  /** Горизонтальный отрезок. Знак повторяется, концы дорисовывает вызывающий. */
  hline(x1: number, x2: number, y: number, ch: string, cls = ""): void {
    for (let x = Math.min(x1, x2); x <= Math.max(x1, x2); x += 1) this.put(x, y, ch, cls);
  }

  vline(x: number, y1: number, y2: number, ch: string, cls = ""): void {
    for (let y = Math.min(y1, y2); y <= Math.max(y1, y2); y += 1) this.put(x, y, ch, cls);
  }

  /** Рамка одинарной линией. Внутренность не затирается. */
  box(x: number, y: number, w: number, h: number, cls = ""): void {
    if (w < 2 || h < 2) return;
    const right = x + w - 1;
    const bottom = y + h - 1;
    this.hline(x + 1, right - 1, y, "─", cls);
    this.hline(x + 1, right - 1, bottom, "─", cls);
    this.vline(x, y + 1, bottom - 1, "│", cls);
    this.vline(right, y + 1, bottom - 1, "│", cls);
    // Внутренность затирается: сквозь рамку не должно просвечивать ребро,
    // которое прошло через это место раньше.
    for (let yy = y + 1; yy <= bottom - 1; yy += 1) {
      this.hline(x + 1, right - 1, yy, " ", cls);
    }
    this.put(x, y, "┌", cls);
    this.put(right, y, "┐", cls);
    this.put(x, bottom, "└", cls);
    this.put(right, bottom, "┘", cls);
  }

  /**
   * Полотно одной строкой — тем же текстом, каким оно выглядит на экране.
   *
   * Нужен ради буфера обмена: выделять схему мышью на экране больше нельзя
   * (выделение красило половину рисунка в сплошной прямоугольник и цепляло
   * к нему всплывающий переводчик), а копировать её в тикет — по-прежнему да.
   */
  toText(): string {
    const lines: string[] = [];
    for (let y = 0; y < this.height; y += 1) {
      const from = y * this.width;
      lines.push(this.chars.slice(from, from + this.width).join("").replace(/\s+$/, ""));
    }
    return lines.join("\n");
  }

  /**
   * Строки полотна, где подряд идущие знаки одного класса слиты в один кусок.
   * Хвостовые пробелы срезаются: они ничего не показывают, но растягивают
   * блок по ширине и включают горизонтальную прокрутку на пустом месте.
   */
  rows(): Cell[][] {
    const out: Cell[][] = [];
    for (let y = 0; y < this.height; y += 1) {
      const row: Cell[] = [];
      let current: Cell | null = null;
      for (let x = 0; x < this.width; x += 1) {
        const i = y * this.width + x;
        const cls = this.classes[i];
        if (current && current.cls === cls) current.ch += this.chars[i];
        else {
          current = { ch: this.chars[i], cls };
          row.push(current);
        }
      }
      while (row.length && !row[row.length - 1].ch.trim()) row.pop();
      out.push(row);
    }
    return out;
  }
}

/** Рамка вокруг подписи по центру: ширина считается по самой подписи. */
export function boxWidth(label: string, pad = 1): number {
  return label.length + pad * 2 + 2;
}

/**
 * Рамка вокруг готовых строк текста.
 *
 * Ширина считается по самой длинной строке, а не набирается пробелами в
 * литерале: набранная руками рамка расходится с текстом на первой же правке
 * подписи, и разъехавшийся угол на ASCII-экране виден сразу.
 */
export function frame(lines: string[], pad = 2): string {
  if (!lines.length) return "";
  const len = (line: string) => [...line].length;
  const inner = Math.max(...lines.map(len)) + pad * 2;
  const bar = "─".repeat(inner);
  const gap = " ".repeat(pad);
  const body = lines.map(
    (line) => `│${gap}${line}${" ".repeat(inner - pad - len(line))}│`,
  );
  return [`┌${bar}┐`, ...body, `└${bar}┘`].join("\n");
}

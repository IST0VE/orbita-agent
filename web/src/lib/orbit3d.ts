/**
 * Орбита в трёх измерениях — теми же знаками, что и всё остальное.
 *
 * На экране уже есть символьный граф; здесь за ним крутится сама сцена, ради
 * которой проект называется Orbita: шар из параллелей и меридианов и три
 * наклонённых кольца со спутниками. Ни canvas, ни WebGL: точки модели живут в
 * трёх координатах, поворачиваются матрицей и проецируются перспективой в
 * клетку моноширинной сетки. Глубина показывается не цветом, а знаком —
 * дальняя точка это `.`, ближняя `@`, — потому что так глубину показывает
 * терминал, у которого не было ничего, кроме знаков.
 *
 * Кадр возвращается одной строкой, а не деревом узлов. Двадцать кадров в
 * секунду по две тысячи клеток — это сорок тысяч элементов в секунду для
 * React; текстом это одно присваивание `textContent`, и оно ничего не стоит.
 */

/** Знаки по глубине: от дальнего к ближнему. Пробел — пусто. */
const RAMP = " .·:+*oO@";

/** Удаление камеры от центра сцены в единицах модели. */
const DIST = 5.2;

export type Point = { x: number; y: number; z: number; w: number };

export type Camera = {
  /** Поворот вокруг вертикали, радианы. */
  yaw: number;
  /** Наклон камеры, радианы. */
  pitch: number;
};

/**
 * Шар: пять параллелей и четыре меридиана.
 *
 * Одних параллелей мало — при повороте они читаются как стопка колец, а не
 * как шар. Меридианы стоят ровно для этого и намеренно тусклее: они держат
 * форму, но не спорят с кольцами орбит.
 *
 * Число точек зависит от размера сцены на экране. Постоянное число разваливает
 * картинку в обе стороны сразу: на маленькой панели точки ложатся друг на
 * друга и шар слипается в пятно, на большой — расходятся, и от шара остаётся
 * горсть крапин.
 */
function globe(density: number): Point[] {
  const points: Point[] = [];
  for (let lat = -60; lat <= 60; lat += 30) {
    const rad = (lat * Math.PI) / 180;
    const r = Math.cos(rad);
    const y = Math.sin(rad);
    const count = Math.max(10, Math.round(density * 5 * r));
    for (let i = 0; i < count; i += 1) {
      const a = (i / count) * Math.PI * 2;
      points.push({ x: r * Math.cos(a), y, z: r * Math.sin(a), w: 0.55 });
    }
  }
  const along = Math.max(12, Math.round(density * 6));
  for (let m = 0; m < 4; m += 1) {
    const lon = (m / 4) * Math.PI;
    for (let i = 0; i < along; i += 1) {
      const a = (i / along) * Math.PI * 2;
      points.push({
        x: Math.cos(a) * Math.cos(lon),
        y: Math.sin(a),
        z: Math.cos(a) * Math.sin(lon),
        w: 0.42,
      });
    }
  }
  return points;
}

/** Шар пересчитывается только при смене размера панели, а не каждый кадр. */
let cached: { density: number; points: Point[] } | null = null;

function globeFor(density: number): Point[] {
  const step = Math.round(density);
  if (!cached || cached.density !== step) cached = { density: step, points: globe(step) };
  return cached.points;
}

/** Кольца орбит: свой радиус, свой наклон и своя скорость обхода. */
const RINGS = [
  { r: 1.5, tilt: 0.42, speed: 0.55 },
  { r: 1.95, tilt: -0.72, speed: -0.34 },
  { r: 2.4, tilt: 1.05, speed: 0.21 },
];

/**
 * Точки колец на момент `t`.
 *
 * Считаются каждый кадр, а не один раз: кольцо не просто повёрнуто вместе со
 * сценой — оно ещё и вращается само, и спутник на нём обгоняет собственное
 * кольцо. Без этого сцена читается как неподвижный чертёж, который крутят.
 */
function orbits(t: number, density: number): Point[] {
  const points: Point[] = [];
  for (const ring of RINGS) {
    const cos = Math.cos(ring.tilt);
    const sin = Math.sin(ring.tilt);
    // Шаг по кольцу — примерно половина клетки: реже линия рассыпается в
    // пунктир, чаще точки садятся в одну и ту же клетку впустую.
    const dots = Math.max(24, Math.round(ring.r * density * 2.6));
    for (let i = 0; i < dots; i += 1) {
      const a = (i / dots) * Math.PI * 2 + t * ring.speed * 0.25;
      const x = Math.cos(a) * ring.r;
      const z = Math.sin(a) * ring.r;
      // Наклон кольца — поворот вокруг оси X: плоская окружность встаёт ребром.
      points.push({ x, y: -z * sin, z: z * cos, w: 0.62 });
    }
    // Спутник: та же орбита, но втрое быстрее и единственный во всей сцене,
    // кому достаются самые тяжёлые знаки набора — за ним и следит глаз.
    const a = t * ring.speed;
    const x = Math.cos(a) * ring.r;
    const z = Math.sin(a) * ring.r;
    points.push({ x, y: -z * sin, z: z * cos, w: 1.6 });
  }
  return points;
}

/**
 * Кадр сцены как текст.
 *
 * `aspect` — отношение ширины знака к высоте строки. Без него окружность
 * выезжает овалом: клетка терминала вдвое выше своей ширины, и одинаковый
 * шаг по осям даёт разное расстояние на экране.
 */
export function frame(
  cols: number,
  rows: number,
  camera: Camera,
  t: number,
  aspect: number,
): string {
  if (cols < 8 || rows < 4) return "";

  const cells = new Array<string>(cols * rows).fill(" ");
  // Буфер глубины: ближняя точка обязана закрыть дальнюю, а порядок обхода
  // модели про глубину ничего не знает.
  const depth = new Array<number>(cols * rows).fill(-Infinity);

  const cx = cols / 2;
  const cy = rows / 2;
  // Сцена считается по ширине полотна, а не по высоте: панель схемы низкая и
  // широкая, и орбита, вписанная в высоту, съёживается в пятно посередине.
  // Верх и низ колец при этом уезжают за края — так и задумано: за схемой
  // видно дугу орбиты, а не всю окружность целиком.
  const scale = Math.min(cols / 6.4, rows / (2.6 * aspect));

  const cosYaw = Math.cos(camera.yaw);
  const sinYaw = Math.sin(camera.yaw);
  const cosPitch = Math.cos(camera.pitch);
  const sinPitch = Math.sin(camera.pitch);

  const plot = (p: Point) => {
    // Поворот вокруг вертикали, затем наклон камеры.
    const x1 = p.x * cosYaw + p.z * sinYaw;
    const z1 = p.z * cosYaw - p.x * sinYaw;
    const y2 = p.y * cosPitch - z1 * sinPitch;
    const z2 = z1 * cosPitch + p.y * sinPitch;

    const k = DIST / (DIST + z2);
    const sx = Math.round(cx + x1 * k * scale);
    const sy = Math.round(cy - y2 * k * scale * aspect);
    if (sx < 0 || sy < 0 || sx >= cols || sy >= rows) return;

    const i = sy * cols + sx;
    if (k <= depth[i]) return;
    depth[i] = k;

    // Яркость: своя у точки и от глубины. Знаменатель растянут на весь
    // диапазон глубины, а не на его половину: иначе `O` достаётся всей
    // ближней половине сцены разом, и кольцо читается не линией, а кляксой.
    const lit = Math.max(0, Math.min(1, (k - 0.62) / 0.85)) * p.w;
    const step = Math.max(1, Math.min(RAMP.length - 1, Math.round(lit * (RAMP.length - 1))));
    cells[i] = RAMP[step];
  };

  for (const p of globeFor(scale)) plot(p);
  for (const p of orbits(t, scale)) plot(p);

  const lines: string[] = [];
  for (let y = 0; y < rows; y += 1) {
    lines.push(cells.slice(y * cols, (y + 1) * cols).join("").replace(/\s+$/, ""));
  }
  return lines.join("\n");
}

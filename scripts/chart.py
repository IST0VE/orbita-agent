"""
График стоимости по ходам — из логов, которые пишет сам проект.

    python run_demo.py --json > good.jsonl
    python run_demo.py --bad --json > bad.jsonl
    python scripts/chart.py good.jsonl bad.jsonl -o docs/cost-per-turn.svg

Понимает два формата, оба свои:

  * построчный вывод `run_demo.py --json` — одна строка на ход
    (`turn`, `turn_cost_usd`, `turn_usage`);
  * лог `CostMeter` из `--log calls.jsonl` — одна строка на вызов модели
    (`cost_usd`, `cache_hit`, `cache_miss`); столбцы тогда считаются по вызовам,
    потому что ходов лог не знает.

Рисуется голый SVG без единой зависимости. Это не принципиальная позиция, а
арифметика: matplotlib тянет за собой numpy и pillow ради двух десятков
прямоугольников, а проект, который меряет чужую расточительность, выглядел бы
странно с такой сборкой.

Цвета заданы явно поверх белой подложки: файл открывают и в тёмной теме
GitHub, и в светлой, а прозрачный фон там читается по-разному.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

# Палитра README: синий DeepSeek для исправного прогона, красный — для сломанного.
COLORS = ("#4D6BFE", "#9B3B36", "#1C3C3C", "#7A5AF8")

WIDTH, HEIGHT = 760, 380
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 78, 24, 64, 76


@dataclass
class Series:
    """Один прогон: подпись, стоимость по столбцам и доля кеша в них."""

    name: str
    costs: list[float] = field(default_factory=list)
    hit_rates: list[float] = field(default_factory=list)
    unit: str = "ход"

    @property
    def total(self) -> float:
        return sum(self.costs)


def _hit_rate(usage: dict) -> float:
    hit = usage.get("cache_hit", 0)
    total = hit + usage.get("cache_miss", 0) + usage.get("cache_write", 0)
    return hit / total * 100 if total else 0.0


def read_series(path: Path) -> Series:
    """Прочитать JSONL любого из двух своих форматов."""
    series = Series(name=path.stem)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if "turn" in record:  # построчный вывод run_demo.py --json
            series.costs.append(float(record["turn_cost_usd"]))
            series.hit_rates.append(_hit_rate(record.get("turn_usage") or {}))
        else:  # лог CostMeter: одна строка на вызов модели
            series.unit = "вызов"
            series.costs.append(float(record["cost_usd"]))
            series.hit_rates.append(float(record.get("hit_rate", _hit_rate(record))))
    if not series.costs:
        raise SystemExit(f"{path}: ни одной строки с данными")
    return series


def money(value: float) -> str:
    """Доллары с точностью, на которой ещё видна разница между столбцами."""
    return f"${value:.6f}".rstrip("0").rstrip(".") if value else "$0"


def render(series: list[Series], title: str) -> str:
    columns = max(len(item.costs) for item in series)
    ceiling = max(max(item.costs) for item in series) * 1.15 or 1.0

    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    group_w = plot_w / columns
    bar_w = min(group_w / (len(series) + 0.6), 64)

    def y_of(value: float) -> float:
        return PAD_TOP + plot_h - value / ceiling * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#FFFFFF"/>',
        f'<text x="{PAD_LEFT}" y="30" font-size="17" font-weight="600" fill="#141E27">'
        f"{escape(title)}</text>",
    ]

    # Горизонтальная сетка и подписи оси денег.
    for step in range(5):
        value = ceiling * step / 4
        y = y_of(value)
        parts.append(
            f'<line x1="{PAD_LEFT}" y1="{y:.1f}" x2="{WIDTH - PAD_RIGHT}" y2="{y:.1f}" '
            f'stroke="#D2D9E0" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD_LEFT - 10}" y="{y + 4:.1f}" font-size="11" fill="#5A6875" '
            f'text-anchor="end">{money(value)}</text>'
        )

    # Столбцы: группа на ход, внутри группы по прогону.
    for column in range(columns):
        base = PAD_LEFT + group_w * column
        offset = (group_w - bar_w * len(series)) / 2
        for index, item in enumerate(series):
            if column >= len(item.costs):
                continue
            value = item.costs[column]
            x = base + offset + bar_w * index
            y = y_of(value)
            height = PAD_TOP + plot_h - y
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w - 4:.1f}" '
                f'height="{height:.1f}" fill="{COLORS[index % len(COLORS)]}" rx="2"/>'
            )
            parts.append(
                f'<text x="{x + (bar_w - 4) / 2:.1f}" y="{y - 16:.1f}" font-size="10" '
                f'fill="#141E27" text-anchor="middle">{money(value)}</text>'
            )
            parts.append(
                f'<text x="{x + (bar_w - 4) / 2:.1f}" y="{y - 5:.1f}" font-size="10" '
                f'fill="#5A6875" text-anchor="middle">'
                f"{item.hit_rates[column]:.0f}% из кеша</text>"
            )
        parts.append(
            f'<text x="{base + group_w / 2:.1f}" y="{PAD_TOP + plot_h + 20:.1f}" '
            f'font-size="12" fill="#141E27" text-anchor="middle">'
            f"{series[0].unit} {column + 1}</text>"
        )

    parts.append(
        f'<line x1="{PAD_LEFT}" y1="{PAD_TOP + plot_h:.1f}" x2="{WIDTH - PAD_RIGHT}" '
        f'y2="{PAD_TOP + plot_h:.1f}" stroke="#B6C1CA" stroke-width="1.5"/>'
    )

    # Легенда с итогом по треду: без неё график показывает форму, но не счёт.
    legend_y = HEIGHT - 28
    x = PAD_LEFT
    for index, item in enumerate(series):
        parts.append(
            f'<rect x="{x}" y="{legend_y - 10}" width="12" height="12" '
            f'fill="{COLORS[index % len(COLORS)]}" rx="2"/>'
        )
        label = f"{item.name} — весь тред {money(item.total)}"
        parts.append(
            f'<text x="{x + 18}" y="{legend_y}" font-size="12" fill="#141E27">'
            f"{escape(label)}</text>"
        )
        x += 20 + len(label) * 6.6
    parts.append("</svg>")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path, help="JSONL-логи прогонов")
    parser.add_argument("-o", "--out", type=Path, help="куда писать SVG (иначе stdout)")
    parser.add_argument("--name", action="append", default=[], help="подпись прогона")
    parser.add_argument(
        "--title",
        default="Стоимость хода: стабильный префикс против сломанного",
        help="заголовок графика",
    )
    args = parser.parse_args(argv)

    series = [read_series(path) for path in args.logs]
    for item, name in zip(series, args.name, strict=False):
        item.name = name

    svg = render(series, args.title)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(svg, encoding="utf-8")
        print(f"{args.out}: {len(svg)} байт", file=sys.stderr)
    else:
        print(svg)


if __name__ == "__main__":
    main()

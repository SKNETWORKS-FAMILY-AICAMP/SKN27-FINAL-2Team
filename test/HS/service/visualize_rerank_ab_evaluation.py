from __future__ import annotations

import argparse
import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_SVG = Path(__file__).with_name("rerank_ab_evaluation.svg")
SUMMARY_CSV = Path(__file__).with_name("rerank_ab_evaluation_summary.csv")

QUALITY = (
    ("RAGAS Context Precision", "Precision"),
    ("RAGAS Context Recall", "Recall"),
    ("RAGAS Faithfulness", "Faithfulness"),
    ("RAGAS Answer Relevance", "Relevance"),
)
SPEED = (
    ("검색 속도", "검색"),
    ("LLM 답변 생성 속도", "LLM"),
    ("전체 응답 속도", "전체"),
)


def load_results(path: Path) -> dict[str, float]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = csv.DictReader(file)
        result: dict[str, float] = {}
        for row in rows:
            raw = (row.get("측정 결과") or "").strip().replace("%", "").replace("s", "")
            try:
                result[row["평가 항목"]] = float(raw) / (100 if "%" in (row.get("측정 결과") or "") else 1)
            except ValueError:
                continue
    return result


def grouped_bars(
    metrics: tuple[tuple[str, str], ...],
    before: dict[str, float],
    after: dict[str, float],
    left: int,
    top: int,
    width: int,
    height: int,
    upper: float,
    threshold: float | None,
) -> str:
    group_width = width / len(metrics)
    bar_width = min(42, group_width * 0.28)
    bars: list[str] = []
    for index, (key, label) in enumerate(metrics):
        group_left = left + index * group_width
        for offset, value, css in ((group_width * 0.18, before.get(key), "before"), (group_width * 0.54, after.get(key), "after")):
            if value is None:
                continue
            bar_height = value / upper * height
            bars.append(
                f'<rect x="{group_left+offset:.1f}" y="{top+height-bar_height:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" class="{css}"/>'
                f'<text x="{group_left+offset+bar_width/2:.1f}" y="{top+height-bar_height-7:.1f}" text-anchor="middle" class="value">{value:.2f}</text>'
            )
        bars.append(f'<text x="{group_left+group_width/2:.1f}" y="{top+height+25}" text-anchor="middle" class="axis">{label}</text>')
    guide = ""
    if threshold is not None:
        y = top + height - threshold / upper * height
        guide = f'<line x1="{left}" y1="{y:.1f}" x2="{left+width}" y2="{y:.1f}" class="threshold"/><text x="{left+width}" y="{y-5:.1f}" text-anchor="end" class="axis">기준 {threshold:.1f}</text>'
    return f'<line x1="{left}" y1="{top+height}" x2="{left+width}" y2="{top+height}" class="axis-line"/>{guide}{"".join(bars)}'


def build_svg(before: dict[str, float], after: dict[str, float], before_label: str, after_label: str) -> str:
    speed_values = [value for key, _ in SPEED for value in (before.get(key), after.get(key)) if value is not None]
    speed_upper = max(speed_values, default=1) * 1.15
    quality_chart = grouped_bars(QUALITY, before, after, 70, 110, 490, 320, 1.0, 0.8)
    speed_chart = grouped_bars(SPEED, before, after, 650, 110, 400, 320, speed_upper, None)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="550" viewBox="0 0 1120 550" role="img" aria-label="Rerank A/B evaluation comparison">
<style>
  text {{ font-family: Arial, sans-serif; fill: #202124; }} .title {{ font-size: 20px; font-weight: bold; }} .subtitle,.axis {{ font-size: 12px; fill: #5f6368; }} .value {{ font-size: 12px; }}
  .axis-line {{ stroke: #697277; stroke-width: 1.2; }} .threshold {{ stroke: #c04b30; stroke-width: 1.5; stroke-dasharray: 5 4; }} .before {{ fill: #73818a; }} .after {{ fill: #166b8f; }}
</style>
<text x="70" y="35" class="title">Rerank A/B evaluation</text>
<rect x="70" y="58" width="12" height="12" class="before"/><text x="88" y="68" class="axis">{before_label}</text>
<rect x="190" y="58" width="12" height="12" class="after"/><text x="208" y="68" class="axis">{after_label}</text>
<text x="70" y="95" class="title">RAGAS quality</text>{quality_chart}
<text x="650" y="95" class="title">Latency (seconds)</text>{speed_chart}
</svg>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a rerank before/after evaluation SVG from two service metric CSV files.")
    parser.add_argument("--before", type=Path, required=True, help="Rerank-disabled service_eval_results CSV")
    parser.add_argument("--after", type=Path, required=True, help="Rerank-enabled service_eval_results CSV")
    parser.add_argument("--before-label", default="리랭크 전")
    parser.add_argument("--after-label", default="리랭크 후")
    parser.add_argument("--output", type=Path, default=OUTPUT_SVG)
    args = parser.parse_args()
    before, after = load_results(args.before), load_results(args.after)
    required = {key for key, _ in QUALITY + SPEED}
    if not required.issubset(before) or not required.issubset(after):
        raise SystemExit("두 파일 모두 service_eval_results.csv 형식이어야 합니다.")
    args.output.write_text(build_svg(before, after, args.before_label, args.after_label), encoding="utf-8")
    rows = []
    for group, metrics in (("quality", QUALITY), ("speed_sec", SPEED)):
        for key, label in metrics:
            if key in before and key in after:
                rows.append({"group": group, "metric": key, "label": label, "before": before[key], "after": after[key], "delta": round(after[key] - before[key], 6)})
    with SUMMARY_CSV.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"svg: {args.output}")
    print(f"summary_csv: {SUMMARY_CSV}")


if __name__ == "__main__":
    main()

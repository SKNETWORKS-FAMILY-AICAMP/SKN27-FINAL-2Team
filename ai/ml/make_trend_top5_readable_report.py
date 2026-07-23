from __future__ import annotations

from pathlib import Path

import pandas as pd


REPORT_DIR = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports")
SOURCE_CSV = REPORT_DIR / "trend_top5_for_db_2026-07-18.csv"
OUTPUT_MD = REPORT_DIR / "trend_top5_for_team_보기쉬운정리_2026-07-18.md"
OUTPUT_XLSX = REPORT_DIR / "trend_top5_for_team_보기쉬운정리_2026-07-18.xlsx"


SOURCE_TITLES = {
    "recent5_actual": "최근 5회차 실제 TOP5",
    "predicted": "모델분류 TOP5",
    "actual": "해당 회차 실제 TOP5",
}

TREND_TITLES = {
    "era_topic_train": "시대 + 통합 주제",
    "era": "시대",
    "topic_train": "통합 주제",
    "topic": "세부 주제",
}

TREND_ORDER = ["era_topic_train", "era", "topic_train", "topic"]
SOURCE_ORDER = ["recent5_actual", "predicted", "actual"]


def format_label(row: pd.Series) -> str:
    trend_type = row["trend_type"]
    count = int(row["count"])
    ratio = float(row["ratio_percent"])

    if trend_type == "era_topic_train":
        label = str(row["combo_label_with_topic"])
    else:
        label = str(row["label"])

    return f"{int(row['rank'])}. {label} ({count}개, {ratio:.1f}%)"


def make_block(df: pd.DataFrame, trend_type: str, source: str) -> str:
    part = df[(df["trend_type"] == trend_type) & (df["source"] == source)].copy()
    if part.empty:
        return "-"
    part = part.sort_values("rank")
    return "<br>".join(format_label(row) for _, row in part.iterrows())


def to_md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def main() -> None:
    if not SOURCE_CSV.exists():
        raise FileNotFoundError(SOURCE_CSV)

    df = pd.read_csv(SOURCE_CSV)

    md_parts = [
        "# 최근 트렌드 TOP5 팀원 공유용 정리 - 2026-07-18",
        "",
        "## 이 파일을 보는 방법",
        "",
        "- `최근 5회차 실제 TOP5`: 직전 5회차 실제 라벨을 기준으로 계산한 최근 트렌드입니다. 학습 계획 팀원이 주로 참고하면 됩니다.",
        "- `모델분류 TOP5`: 해당 회차 문제 text를 ML 모델로 분류한 결과입니다. 모델 분류 결과 검증용입니다.",
        "- `해당 회차 실제 TOP5`: 해당 회차의 실제 라벨 기준 분포입니다. 비교 기준입니다.",
        "",
        "## 추천 사용 기준",
        "",
        "학습 계획/문제 생성 우선순위에는 우선 `최근 5회차 실제 TOP5`를 사용하고, 모델이 생성 문제를 라벨링한 결과는 DB에 저장해서 개인 학습 계획에 연결합니다.",
        "",
    ]

    excel_sheets: dict[str, pd.DataFrame] = {}

    for round_no in sorted(df["target_round"].unique()):
        round_df = df[df["target_round"] == round_no].copy()
        recent5_rounds = str(round_df["recent5_rounds"].iloc[0])
        md_parts.extend(
            [
                f"## {int(round_no)}회차 기준",
                "",
                f"- 최근 5회차 기준: `{recent5_rounds}`",
                "",
            ]
        )

        for trend_type in TREND_ORDER:
            title = TREND_TITLES[trend_type]
            rows = [
                [
                    SOURCE_TITLES[source],
                    make_block(round_df, trend_type, source),
                ]
                for source in SOURCE_ORDER
            ]
            md_parts.extend(
                [
                    f"### {title}",
                    "",
                    to_md_table(["구분", "TOP5"], rows),
                    "",
                ]
            )

        sheet_rows = []
        for trend_type in TREND_ORDER:
            for source in SOURCE_ORDER:
                part = round_df[(round_df["trend_type"] == trend_type) & (round_df["source"] == source)].copy()
                for _, row in part.sort_values("rank").iterrows():
                    sheet_rows.append(
                        {
                            "회차": int(round_no),
                            "최근5회차": recent5_rounds,
                            "분류": TREND_TITLES[trend_type],
                            "구분": SOURCE_TITLES[source],
                            "순위": int(row["rank"]),
                            "라벨": row["combo_label_with_topic"] if trend_type == "era_topic_train" else row["label"],
                            "개수": int(row["count"]),
                            "비율(%)": float(row["ratio_percent"]),
                        }
                    )
        excel_sheets[f"{int(round_no)}회차"] = pd.DataFrame(sheet_rows)

    OUTPUT_MD.write_text("\n".join(md_parts) + "\n", encoding="utf-8")

    try:
        with pd.ExcelWriter(OUTPUT_XLSX) as writer:
            for sheet_name, sheet_df in excel_sheets.items():
                sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)
    except Exception as exc:
        print(f"skip xlsx export: {exc}")

    print(OUTPUT_MD)
    print(OUTPUT_XLSX)


if __name__ == "__main__":
    main()

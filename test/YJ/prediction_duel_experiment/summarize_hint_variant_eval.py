from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
BASELINE_DIR = BASE_DIR / "rubric_eval_results"
HINT_DIR = BASE_DIR / "hint_rubric_eval_results"
OUT_PATH = HINT_DIR / "gpt_hint_variant_comparison_report.md"

PROFILES = [
    {
        "key": "sllm",
        "label": "SLLM 기준선",
        "dir": BASELINE_DIR,
        "ids": range(101, 111),
        "hint": "SLLM 출력",
    },
    {
        "key": "gpt0",
        "label": "GPT-0 원본",
        "dir": BASELINE_DIR,
        "ids": range(201, 211),
        "hint": "원본 prompt만 제공",
    },
    {
        "key": "gpt1",
        "label": "GPT-1 체크리스트",
        "dir": HINT_DIR / "p1_checklist",
        "ids": range(201, 211),
        "hint": "3번: 출력 전 자기검수 체크리스트",
    },
    {
        "key": "gpt2",
        "label": "GPT-2 체크리스트+예시",
        "dir": HINT_DIR / "p2_checklist_examples",
        "ids": range(201, 211),
        "hint": "3번 + 2번: 자기검수 체크리스트 + 좋은/나쁜 예시",
    },
    {
        "key": "gpt3",
        "label": "GPT-3 체크리스트+예시+Gate",
        "dir": HINT_DIR / "p3_checklist_examples_gate",
        "ids": range(201, 211),
        "hint": "3번 + 2번 + 1번: 자기검수 체크리스트 + 예시 + Gate 요약",
    },
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def latest_results(path: Path, ids: range) -> dict[int, dict[str, Any]]:
    wanted = set(ids)
    latest: dict[int, tuple[float, dict[str, Any]]] = {}
    for file_path in path.glob("eval_run_*.jsonl"):
        mtime = file_path.stat().st_mtime
        for row in read_jsonl(file_path):
            qid = int(row["question_id"])
            if qid not in wanted:
                continue
            if qid not in latest or mtime >= latest[qid][0]:
                latest[qid] = (mtime, row)
    return {qid: row for qid, (_, row) in latest.items()}


def problem_score(parsed: dict[str, Any]) -> int:
    score = parsed.get("problem_score")
    return int(score) if isinstance(score, int | float) else 0


def gate_pass(parsed: dict[str, Any]) -> bool:
    return str(parsed.get("gate_result") or "").lower() == "pass" and isinstance(
        parsed.get("problem_score"), int | float
    )


def effective_score(parsed: dict[str, Any]) -> int:
    return problem_score(parsed) if gate_pass(parsed) else 0


def failed_gates(parsed: dict[str, Any]) -> str:
    values = parsed.get("failed_gates")
    if isinstance(values, list):
        return ",".join(str(item) for item in values)
    return ""


def summarize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    results = latest_results(profile["dir"], profile["ids"])
    rows = []
    for offset, qid in enumerate(profile["ids"], start=1):
        parsed = results.get(qid, {}).get("parsed")
        if not isinstance(parsed, dict):
            parsed = {}
        rows.append(
            {
                "group": offset,
                "qid": qid,
                "gate": (
                    "PASS"
                    if gate_pass(parsed)
                    else "INVALID"
                    if str(parsed.get("gate_result") or "").lower() == "pass"
                    else str(parsed.get("gate_result") or "MISSING").upper()
                ),
                "problem_score": problem_score(parsed) if gate_pass(parsed) else None,
                "effective_score": effective_score(parsed),
                "failed_gates": failed_gates(parsed),
                "decision": parsed.get("final_decision") or "",
            }
        )
    pass_rows = [row for row in rows if row["gate"] == "PASS"]
    return {
        **profile,
        "rows": rows,
        "gate_pass": len(pass_rows),
        "gate_fail": len(rows) - len(pass_rows),
        "avg_effective": sum(row["effective_score"] for row in rows) / len(rows),
        "avg_pass": (sum(row["problem_score"] or 0 for row in pass_rows) / len(pass_rows)) if pass_rows else 0,
        "fail_gates": Counter(
            gate
            for row in rows
            for gate in str(row["failed_gates"]).split(",")
            if gate
        ),
    }


def rel_lift(a: float, b: float) -> str:
    if b == 0:
        return "비교 불가"
    return f"{((a - b) / b) * 100:.1f}%"


def pp(a: float, b: float) -> str:
    return f"{(a - b) * 100:.1f}%p"


def main() -> int:
    summaries = [summarize_profile(profile) for profile in PROFILES]
    by_key = {item["key"]: item for item in summaries}
    sllm = by_key["sllm"]
    gpt0 = by_key["gpt0"]

    lines = [
        "# GPT 추가 정보량별 성능 변화 보고서",
        "",
        "## 1. 실험 설정",
        "",
        "- 문제 수: 어제와 동일하게 처음 10개 문항 묶음",
        "- GPT 생성 모델: `gpt-4.1-mini`",
        "- 평가 모델: `gpt-4.1-mini` LLM judge",
        "- 평가 기준: 우리 `Gate + 문제 10점` 루브릭",
        "- 해설은 생성 대상이 아니므로 제외",
        "- Gate FAIL은 실효 점수 0점 처리",
        "",
        "## 2. 제공 정보 단계",
        "",
        "| 단계 | 제공 정보 |",
        "|---|---|",
    ]
    for item in summaries:
        lines.append(f"| {item['label']} | {item['hint']} |")

    lines.extend(
        [
            "",
            "## 3. 종합 점수",
            "",
            "| 모델/조건 | Gate PASS | Gate FAIL | 평균 실효 점수 | PASS 문항 평균 | 원본 GPT 대비 평균 향상 | SLLM 대비 평균 차이 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in summaries:
        lines.append(
            f"| {item['label']} | {item['gate_pass']}/10 | {item['gate_fail']}/10 | "
            f"{item['avg_effective']:.1f} | {item['avg_pass']:.1f} | "
            f"{rel_lift(item['avg_effective'], gpt0['avg_effective']) if item['key'] != 'gpt0' else '-'} | "
            f"{item['avg_effective'] - sllm['avg_effective']:+.1f}점 |"
        )

    lines.extend(
        [
            "",
            "## 4. Gate PASS율 변화",
            "",
            "| 모델/조건 | Gate PASS율 | 원본 GPT 대비 차이 | SLLM 대비 차이 |",
            "|---|---:|---:|---:|",
        ]
    )
    for item in summaries:
        pass_rate = item["gate_pass"] / 10
        gpt0_rate = gpt0["gate_pass"] / 10
        sllm_rate = sllm["gate_pass"] / 10
        lines.append(
            f"| {item['label']} | {pass_rate * 100:.1f}% | "
            f"{pp(pass_rate, gpt0_rate) if item['key'] != 'gpt0' else '-'} | {pp(pass_rate, sllm_rate)} |"
        )

    lines.extend(
        [
            "",
            "## 5. 문항별 실효 점수",
            "",
            "| 문항 | SLLM | GPT-0 | GPT-1 | GPT-2 | GPT-3 |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for index in range(10):
        lines.append(
            f"| {index + 1} | "
            + " | ".join(str(item["rows"][index]["effective_score"]) for item in summaries)
            + " |"
        )

    lines.extend(
        [
            "",
            "## 6. 문항별 Gate",
            "",
            "| 문항 | SLLM | GPT-0 | GPT-1 | GPT-2 | GPT-3 |",
            "|---:|---|---|---|---|---|",
        ]
    )
    for index in range(10):
        cells = []
        for item in summaries:
            row = item["rows"][index]
            cell = row["gate"]
            if row["failed_gates"]:
                cell += f" ({row['failed_gates']})"
            cells.append(cell)
        lines.append(f"| {index + 1} | " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "## 7. 해석",
            "",
        ]
    )
    best_gpt = max([item for item in summaries if item["key"].startswith("gpt")], key=lambda item: item["avg_effective"])
    lines.append(
        f"- GPT 조건 중 최고 평균은 **{best_gpt['label']}**의 {best_gpt['avg_effective']:.1f}점입니다."
    )
    lines.append(
        f"- 원본 GPT 평균 {gpt0['avg_effective']:.1f}점 대비 최고 GPT 조건은 **{rel_lift(best_gpt['avg_effective'], gpt0['avg_effective'])}** 변화했습니다."
    )
    lines.append(
        f"- SLLM 평균 {sllm['avg_effective']:.1f}점과 비교하면 최고 GPT 조건도 **{best_gpt['avg_effective'] - sllm['avg_effective']:+.1f}점** 차이입니다."
    )
    lines.append(
        "- 추가 정보가 항상 선형적으로 성능을 올리지는 않았습니다. 특히 예시나 Gate를 많이 넣으면 형식 준수는 좋아질 수 있지만, 특정 예시 패턴을 과하게 따라 하거나 문장이 길어져 G6/G3 위험이 생길 수 있습니다."
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

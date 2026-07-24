from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
BASELINE_DIR = BASE_DIR / "rubric_eval_results"
OUT_PATH = BASE_DIR / "hint_multi_model_comparison_report.md"

QUESTION_COUNT = 10

PROFILES = {
    "h1": {
        "label": "H1",
        "dir_name": "p1_checklist",
        "hint": "3번만: 출력 전 자기검수 체크리스트",
    },
    "h2": {
        "label": "H2",
        "dir_name": "p2_checklist_examples",
        "hint": "3번+2번: 자기검수 체크리스트 + 좋은/나쁜 예시",
    },
    "h3": {
        "label": "H3",
        "dir_name": "p3_checklist_examples_gate",
        "hint": "3번+2번+1번: 자기검수 체크리스트 + 예시 + Gate 요약",
    },
}

TARGETS = [
    {
        "key": "sllm",
        "family": "baseline",
        "model": "SLLM",
        "condition": "SLLM",
        "hint": "SLLM 출력",
        "dir": BASELINE_DIR,
        "ids": range(101, 111),
    },
    {
        "key": "gpt41mini_original",
        "family": "original",
        "model": "gpt-4.1-mini",
        "condition": "원본",
        "hint": "원본 prompt만 제공",
        "dir": BASELINE_DIR,
        "ids": range(201, 211),
    },
]

MODEL_DIRS = [
    ("gpt-4.1-mini", BASE_DIR / "hint_rubric_eval_results"),
    ("gpt-4.1", BASE_DIR / "hint_rubric_eval_results_gpt41"),
    ("gpt-5", BASE_DIR / "hint_rubric_eval_results_gpt5"),
]

for model_name, model_dir in MODEL_DIRS:
    for profile_key, profile in PROFILES.items():
        TARGETS.append(
            {
                "key": f"{model_name}_{profile_key}".replace(".", ""),
                "family": "hint",
                "model": model_name,
                "condition": profile["label"],
                "hint": profile["hint"],
                "dir": model_dir / profile["dir_name"],
                "ids": range(201, 211),
            }
        )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def gate_pass(parsed: dict[str, Any]) -> bool:
    return str(parsed.get("gate_result") or "").lower() == "pass" and is_number(parsed.get("problem_score"))


def raw_gate(parsed: dict[str, Any]) -> str:
    if gate_pass(parsed):
        return "PASS"
    if str(parsed.get("gate_result") or "").lower() == "pass":
        return "INVALID"
    return str(parsed.get("gate_result") or "MISSING").upper()


def problem_score(parsed: dict[str, Any]) -> int | None:
    if not gate_pass(parsed):
        return None
    return int(parsed["problem_score"])


def effective_score(parsed: dict[str, Any]) -> int:
    return int(parsed["problem_score"]) if gate_pass(parsed) else 0


def failed_gates(parsed: dict[str, Any]) -> str:
    values = parsed.get("failed_gates")
    if isinstance(values, list):
        return ",".join(str(item) for item in values)
    return ""


def summarize_target(target: dict[str, Any]) -> dict[str, Any]:
    results = latest_results(target["dir"], target["ids"])
    rows = []
    for offset, qid in enumerate(target["ids"], start=1):
        parsed = results.get(qid, {}).get("parsed")
        if not isinstance(parsed, dict):
            parsed = {}
        rows.append(
            {
                "index": offset,
                "qid": qid,
                "gate": raw_gate(parsed),
                "problem_score": problem_score(parsed),
                "effective_score": effective_score(parsed),
                "failed_gates": failed_gates(parsed),
                "decision": str(parsed.get("final_decision") or ""),
            }
        )

    pass_rows = [row for row in rows if row["gate"] == "PASS"]
    fail_gate_counter = Counter(
        gate
        for row in rows
        for gate in str(row["failed_gates"]).split(",")
        if gate
    )
    decision_counter = Counter(row["decision"] or "missing" for row in rows)
    return {
        **target,
        "rows": rows,
        "gate_pass": len(pass_rows),
        "gate_fail": len(rows) - len(pass_rows),
        "avg_effective": sum(row["effective_score"] for row in rows) / len(rows),
        "avg_pass": (sum(row["problem_score"] or 0 for row in pass_rows) / len(pass_rows)) if pass_rows else 0,
        "fail_gates": fail_gate_counter,
        "decisions": decision_counter,
    }


def rel_lift(value: float, base: float) -> str:
    if base == 0:
        return "비교 불가"
    return f"{((value - base) / base) * 100:.1f}%"


def diff(value: float, base: float) -> str:
    return f"{value - base:+.1f}"


def pass_rate(item: dict[str, Any]) -> float:
    return item["gate_pass"] / QUESTION_COUNT


def gate_cell(row: dict[str, Any]) -> str:
    cell = row["gate"]
    if row["failed_gates"]:
        cell += f" ({row['failed_gates']})"
    return cell


def compact_counter(counter: Counter[str], limit: int = 4) -> str:
    if not counter:
        return "-"
    return ", ".join(f"{key} {count}" for key, count in counter.most_common(limit))


def target_label(item: dict[str, Any]) -> str:
    if item["model"] == "SLLM":
        return "SLLM"
    return f"{item['model']} {item['condition']}"


def diff_sentence(value: float, base: float) -> str:
    delta = value - base
    if delta >= 0:
        return f"{delta:+.1f}점 높습니다"
    return f"{delta:+.1f}점 낮습니다"


def main() -> int:
    summaries = [summarize_target(target) for target in TARGETS]
    by_key = {item["key"]: item for item in summaries}
    sllm = by_key["sllm"]
    original = by_key["gpt41mini_original"]

    hint_summaries = [item for item in summaries if item["family"] == "hint"]
    best_overall = max(hint_summaries, key=lambda item: item["avg_effective"])

    best_by_model: dict[str, dict[str, Any]] = {}
    for model_name, _ in MODEL_DIRS:
        model_items = [item for item in hint_summaries if item["model"] == model_name]
        best_by_model[model_name] = max(model_items, key=lambda item: item["avg_effective"])

    lines = [
        "# GPT 모델 업그레이드별 정보량 조건 평가 보고서",
        "",
        "## 1. 실험 설정",
        "",
        f"- 문제 수: 어제와 동일하게 처음 {QUESTION_COUNT}개 문항 묶음",
        "- 비교 축: `gpt-4.1-mini`, `gpt-4.1`, `gpt-5`",
        "- 정보량 조건: H1, H2, H3 세 단계",
        "- 평가 모델: 기존과 동일하게 `gpt-4.1-mini` LLM judge",
        "- 평가 기준: 우리 `Gate + 문제 10점` 루브릭",
        "- 해설은 생성 대상이 아니므로 제외",
        "- Gate PASS가 아니거나 problem_score가 숫자가 아니면 실효 점수 0점",
        "",
        "## 2. 정보량 조건",
        "",
        "| 조건 | 제공 정보 |",
        "|---|---|",
    ]
    for profile in PROFILES.values():
        lines.append(f"| {profile['label']} | {profile['hint']} |")

    lines.extend(
        [
            "",
            "## 3. 전체 요약",
            "",
            "| 대상 | 생성 모델 | 조건 | Gate PASS | 평균 실효 점수 | PASS 문항 평균 | SLLM 대비 | GPT 원본 대비 | 주요 탈락 Gate |",
            "|---|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in summaries:
        lines.append(
            f"| {target_label(item)} | {item['model']} | {item['condition']} | "
            f"{item['gate_pass']}/10 | {item['avg_effective']:.1f} | {item['avg_pass']:.1f} | "
            f"{diff(item['avg_effective'], sllm['avg_effective'])}점 | "
            f"{rel_lift(item['avg_effective'], original['avg_effective']) if item['key'] != original['key'] else '-'} | "
            f"{compact_counter(item['fail_gates'])} |"
        )

    lines.extend(
        [
            "",
            "## 4. 모델별 최고 조건",
            "",
            "| 생성 모델 | 최고 조건 | Gate PASS | 평균 실효 점수 | SLLM 대비 | GPT 원본 대비 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for model_name, best in best_by_model.items():
        lines.append(
            f"| {model_name} | {best['condition']} | {best['gate_pass']}/10 | "
            f"{best['avg_effective']:.1f} | {diff(best['avg_effective'], sllm['avg_effective'])}점 | "
            f"{rel_lift(best['avg_effective'], original['avg_effective'])} |"
        )
    lines.append(
        f"| 전체 GPT 최고 | {best_overall['model']} {best_overall['condition']} | "
        f"{best_overall['gate_pass']}/10 | {best_overall['avg_effective']:.1f} | "
        f"{diff(best_overall['avg_effective'], sllm['avg_effective'])}점 | "
        f"{rel_lift(best_overall['avg_effective'], original['avg_effective'])} |"
    )

    lines.extend(
        [
            "",
            "## 5. 모델 x 정보량 매트릭스",
            "",
            "| 생성 모델 | H1 평균/PASS | H2 평균/PASS | H3 평균/PASS | 모델 내 최고 |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for model_name, _ in MODEL_DIRS:
        cells = []
        model_items = [item for item in hint_summaries if item["model"] == model_name]
        by_condition = {item["condition"]: item for item in model_items}
        for condition in ("H1", "H2", "H3"):
            item = by_condition[condition]
            cells.append(f"{item['avg_effective']:.1f} / {item['gate_pass']}/10")
        best = best_by_model[model_name]
        lines.append(f"| {model_name} | " + " | ".join(cells) + f" | {best['condition']} |")

    lines.extend(
        [
            "",
            "## 6. Gate PASS율",
            "",
            "| 대상 | PASS율 | SLLM 대비 | GPT 원본 대비 |",
            "|---|---:|---:|---:|",
        ]
    )
    for item in summaries:
        lines.append(
            f"| {target_label(item)} | {pass_rate(item) * 100:.1f}% | "
            f"{(pass_rate(item) - pass_rate(sllm)) * 100:+.1f}%p | "
            f"{(pass_rate(item) - pass_rate(original)) * 100:+.1f}%p |"
        )

    lines.extend(
        [
            "",
            "## 7. 문항별 실효 점수",
            "",
            "| 문항 | SLLM | GPT 원본 | mini H1 | mini H2 | mini H3 | 4.1 H1 | 4.1 H2 | 4.1 H3 | 5 H1 | 5 H2 | 5 H3 |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    display_order = [
        by_key["sllm"],
        by_key["gpt41mini_original"],
        *[item for item in hint_summaries if item["model"] == "gpt-4.1-mini"],
        *[item for item in hint_summaries if item["model"] == "gpt-4.1"],
        *[item for item in hint_summaries if item["model"] == "gpt-5"],
    ]
    for index in range(QUESTION_COUNT):
        cells = [str(item["rows"][index]["effective_score"]) for item in display_order]
        lines.append(f"| {index + 1} | " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "## 8. 문항별 Gate",
            "",
            "| 문항 | SLLM | GPT 원본 | mini H1 | mini H2 | mini H3 | 4.1 H1 | 4.1 H2 | 4.1 H3 | 5 H1 | 5 H2 | 5 H3 |",
            "|---:|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for index in range(QUESTION_COUNT):
        cells = [gate_cell(item["rows"][index]) for item in display_order]
        lines.append(f"| {index + 1} | " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "## 9. 결론",
            "",
            f"- 전체 GPT 조건 중 최고는 **{best_overall['model']} {best_overall['condition']}**이며 평균 실효 점수는 **{best_overall['avg_effective']:.1f}점**입니다.",
            f"- SLLM 기준선은 **{sllm['avg_effective']:.1f}점 / Gate PASS {sllm['gate_pass']}/10**입니다.",
            f"- 이번 실험에서 최고 GPT 조건은 SLLM보다 **{diff_sentence(best_overall['avg_effective'], sllm['avg_effective'])}**.",
            "- 모델을 올려도 정보 제공 방식과 Gate 탈락 문제가 함께 개선되지 않으면 점수가 자동으로 오르지는 않았습니다.",
        ]
    )

    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

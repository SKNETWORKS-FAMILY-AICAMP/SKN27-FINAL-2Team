from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
ASSEMBLED_PATH = BASE_DIR / "data" / "assembled_first10_for_rubric_eval.jsonl"
RESULT_DIR = BASE_DIR / "rubric_eval_results"
OUT_PATH = RESULT_DIR / "rubric_duel_first10_summary.md"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def status(parsed: dict[str, Any]) -> str:
    return str(parsed.get("gate_result") or "").upper()


def decision(parsed: dict[str, Any]) -> str:
    return str(parsed.get("final_decision") or "")


def failed(parsed: dict[str, Any]) -> str:
    values = parsed.get("failed_gates")
    if isinstance(values, list):
        return ", ".join(str(item) for item in values)
    return ""


def effective_score(parsed: dict[str, Any]) -> int:
    if str(parsed.get("gate_result") or "").lower() != "pass":
        return 0
    score = parsed.get("problem_score")
    return int(score) if isinstance(score, int | float) else 0


def reason(parsed: dict[str, Any]) -> str:
    gates = parsed.get("gate") if isinstance(parsed.get("gate"), dict) else {}
    failed_gates = parsed.get("failed_gates") if isinstance(parsed.get("failed_gates"), list) else []
    if failed_gates:
        parts = []
        for gate_id in failed_gates:
            gate = gates.get(gate_id)
            if isinstance(gate, dict):
                parts.append(f"{gate_id}: {gate.get('reason', '')}")
        return " / ".join(parts)
    detail = parsed.get("problem_score_detail") if isinstance(parsed.get("problem_score_detail"), dict) else {}
    reasons = []
    for key in ("target_difficulty_fit", "choice_quality"):
        item = detail.get(key)
        if isinstance(item, dict):
            reasons.append(f"{key} {item.get('score')}: {item.get('reason', '')}")
    return " / ".join(reasons)


def compact(text: str, limit: int = 170) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def main() -> int:
    records = {int(row["question_id"]): row for row in read_jsonl(ASSEMBLED_PATH)}
    latest: dict[int, tuple[float, dict[str, Any]]] = {}
    for path in RESULT_DIR.glob("eval_run_*.jsonl"):
        mtime = path.stat().st_mtime
        for row in read_jsonl(path):
            qid = int(row["question_id"])
            if qid not in records:
                continue
            if qid not in latest or mtime >= latest[qid][0]:
                latest[qid] = (mtime, row)

    rows = []
    for qid in sorted(records):
        record = records[qid]
        result = latest.get(qid, (0, {}))[1]
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        row = {
            "qid": qid,
            "group": record["source_group"],
            "generator": record["generator"],
            "topic": record.get("topic") or "",
            "target_score": record["target_score"],
            "gate": status(parsed),
            "decision": decision(parsed),
            "failed": failed(parsed),
            "problem_score": parsed.get("problem_score") if status(parsed) == "PASS" else None,
            "effective_score": effective_score(parsed),
            "reason": reason(parsed),
        }
        rows.append(row)

    by_generator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_generator[row["generator"]].append(row)

    lines = [
        "# Rubric Duel First 10",
        "",
        "해설은 생성 대상에 없으므로 해설 5점은 제외하고, 우리 평가표의 Gate와 문제 10점 기준만 적용했다.",
        "Gate FAIL은 평가표 원칙상 점수 채점을 중단하므로, 집계용 실효 점수에서는 0점으로 계산했다.",
        "",
        "## Aggregate",
        "",
        "| 생성자 | 문항 | Gate PASS | Gate FAIL | 평균 실효 점수(10) | PASS 문항 평균(10) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for generator in ("SLLM", "GPT"):
        items = by_generator.get(generator, [])
        pass_items = [item for item in items if item["gate"] == "PASS"]
        avg_effective = sum(item["effective_score"] for item in items) / len(items) if items else 0
        avg_pass = sum(int(item["problem_score"] or 0) for item in pass_items) / len(pass_items) if pass_items else 0
        lines.append(
            f"| {generator} | {len(items)} | {len(pass_items)} | {len(items) - len(pass_items)} | "
            f"{avg_effective:.1f} | {avg_pass:.1f} |"
        )

    lines.extend(
        [
            "",
            "## Pairwise",
            "",
            "| 묶음 | 주제 | target | SLLM Gate/점수 | GPT Gate/점수 | 승자 | 핵심 사유 |",
            "|---:|---|---:|---|---|---|---|",
        ]
    )
    group_winners = Counter()
    for group in range(1, 11):
        sllm = next(item for item in rows if item["group"] == group and item["generator"] == "SLLM")
        gpt = next(item for item in rows if item["group"] == group and item["generator"] == "GPT")
        if sllm["effective_score"] > gpt["effective_score"]:
            winner = "SLLM"
            why = sllm["reason"] if sllm["gate"] != "PASS" else gpt["reason"]
        elif gpt["effective_score"] > sllm["effective_score"]:
            winner = "GPT"
            why = gpt["reason"] if gpt["gate"] != "PASS" else sllm["reason"]
        else:
            winner = "tie"
            why = sllm["reason"] or gpt["reason"]
        group_winners[winner] += 1
        sllm_cell = f"{sllm['gate']} / {sllm['effective_score']}"
        if sllm["failed"]:
            sllm_cell += f" ({sllm['failed']})"
        gpt_cell = f"{gpt['gate']} / {gpt['effective_score']}"
        if gpt["failed"]:
            gpt_cell += f" ({gpt['failed']})"
        lines.append(
            f"| {group} | {sllm['topic']} | {sllm['target_score']} | {sllm_cell} | {gpt_cell} | "
            f"**{winner}** | {compact(why).replace('|', '\\|')} |"
        )

    lines.extend(
        [
            "",
            f"Pairwise result: SLLM {group_winners['SLLM']} / GPT {group_winners['GPT']} / tie {group_winners['tie']}",
            "",
            "## Detail",
            "",
            "| QID | 묶음 | 생성자 | Gate | 문제점수 | 실효점수 | 실패 Gate | 사유 |",
            "|---:|---:|---|---|---:|---:|---|---|",
        ]
    )
    for row in rows:
        problem = "" if row["problem_score"] is None else str(row["problem_score"])
        lines.append(
            f"| {row['qid']} | {row['group']} | {row['generator']} | {row['gate']} | "
            f"{problem} | {row['effective_score']} | {row['failed']} | {compact(row['reason']).replace('|', '\\|')} |"
        )

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
ASSEMBLED_PATH = BASE_DIR / "data" / "assembled_first10_for_rubric_eval.jsonl"
RESULT_DIR = BASE_DIR / "rubric_eval_results"
DUEL_PATH = BASE_DIR / "results" / "duel_first50_20260630_143539.json"
OUT_PATH = RESULT_DIR / "rubric_duel_first10_report.md"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def compact(text: Any, limit: int = 260) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def gate_status(parsed: dict[str, Any]) -> str:
    return str(parsed.get("gate_result") or "").upper()


def failed_gates(parsed: dict[str, Any]) -> list[str]:
    values = parsed.get("failed_gates")
    return [str(item) for item in values] if isinstance(values, list) else []


def problem_score(parsed: dict[str, Any]) -> int | None:
    if gate_status(parsed) != "PASS":
        return None
    score = parsed.get("problem_score")
    return int(score) if isinstance(score, int | float) else None


def effective_score(parsed: dict[str, Any]) -> int:
    return problem_score(parsed) or 0


def gate_reasons(parsed: dict[str, Any]) -> list[str]:
    gates = parsed.get("gate") if isinstance(parsed.get("gate"), dict) else {}
    reasons: list[str] = []
    for gate_id in failed_gates(parsed):
        gate = gates.get(gate_id)
        if isinstance(gate, dict):
            reasons.append(f"{gate_id}: {gate.get('reason', '')}")
    return reasons


def score_reasons(parsed: dict[str, Any]) -> list[str]:
    detail = parsed.get("problem_score_detail") if isinstance(parsed.get("problem_score_detail"), dict) else {}
    reasons: list[str] = []
    labels = {
        "target_difficulty_fit": "난이도 적합성",
        "choice_quality": "선택지 품질",
    }
    for key in ("target_difficulty_fit", "choice_quality"):
        item = detail.get(key)
        if isinstance(item, dict):
            reasons.append(f"{labels[key]} {item.get('score')}점: {item.get('reason', '')}")
    return reasons


def choice_text(record: dict[str, Any]) -> str:
    return "\n".join(f"{item['label']} {item['text']}" for item in record.get("choices", []))


def get_latest_results() -> dict[int, dict[str, Any]]:
    latest: dict[int, tuple[float, dict[str, Any]]] = {}
    for path in RESULT_DIR.glob("eval_run_*.jsonl"):
        mtime = path.stat().st_mtime
        for row in read_jsonl(path):
            qid = int(row["question_id"])
            if qid not in latest or mtime >= latest[qid][0]:
                latest[qid] = (mtime, row)
    return {qid: row for qid, (_, row) in latest.items()}


def winner_label(sllm_parsed: dict[str, Any], gpt_parsed: dict[str, Any]) -> str:
    s_score = effective_score(sllm_parsed)
    g_score = effective_score(gpt_parsed)
    if s_score > g_score:
        return "SLLM"
    if g_score > s_score:
        return "GPT"
    return "동점"


def verdict_sentence(winner: str, sllm_parsed: dict[str, Any], gpt_parsed: dict[str, Any]) -> str:
    if winner == "SLLM":
        return "SLLM이 Gate 또는 문제 점수에서 우세했습니다."
    if winner == "GPT":
        return "GPT가 Gate 또는 문제 점수에서 우세했습니다."
    if gate_status(sllm_parsed) != "PASS" and gate_status(gpt_parsed) != "PASS":
        return "두 출력 모두 Gate를 통과하지 못했습니다."
    return "두 출력의 실효 점수가 같습니다."


def main() -> int:
    records = {int(row["question_id"]): row for row in read_jsonl(ASSEMBLED_PATH)}
    latest = get_latest_results()
    duel_items = json.loads(DUEL_PATH.read_text(encoding="utf-8")) if DUEL_PATH.exists() else []
    duel_by_group = {index + 1: duel_items[index * 5 : index * 5 + 5] for index in range(10)}

    grouped: dict[int, dict[str, tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(dict)
    for qid, record in records.items():
        result = latest.get(qid)
        parsed = result.get("parsed") if result and isinstance(result.get("parsed"), dict) else {}
        grouped[int(record["source_group"])][str(record["generator"])] = (record, parsed)

    rows = []
    for group in sorted(grouped):
        sllm_record, sllm_parsed = grouped[group]["SLLM"]
        gpt_record, gpt_parsed = grouped[group]["GPT"]
        rows.append(
            {
                "group": group,
                "topic": sllm_record.get("topic") or "",
                "target_score": sllm_record.get("target_score"),
                "winner": winner_label(sllm_parsed, gpt_parsed),
                "sllm_gate": gate_status(sllm_parsed),
                "gpt_gate": gate_status(gpt_parsed),
                "sllm_score": effective_score(sllm_parsed),
                "gpt_score": effective_score(gpt_parsed),
            }
        )

    aggregate = Counter(row["winner"] for row in rows)
    pass_count = {
        "SLLM": sum(1 for group in grouped.values() if gate_status(group["SLLM"][1]) == "PASS"),
        "GPT": sum(1 for group in grouped.values() if gate_status(group["GPT"][1]) == "PASS"),
    }
    avg_effective = {
        "SLLM": sum(effective_score(group["SLLM"][1]) for group in grouped.values()) / len(grouped),
        "GPT": sum(effective_score(group["GPT"][1]) for group in grouped.values()) / len(grouped),
    }
    total_count = len(grouped)
    pass_rate = {
        "SLLM": pass_count["SLLM"] / total_count if total_count else 0,
        "GPT": pass_count["GPT"] / total_count if total_count else 0,
    }
    win_rate = {
        "SLLM": aggregate["SLLM"] / total_count if total_count else 0,
        "GPT": aggregate["GPT"] / total_count if total_count else 0,
    }

    def relative_lift(a: float, b: float) -> str:
        if b == 0:
            return "비교 불가"
        return f"{((a - b) / b) * 100:.1f}%"

    def point_gap(a: float, b: float) -> str:
        return f"{(a - b) * 100:.1f}%p"

    lines = [
        "# SLLM vs GPT 문제별 평가 결과 보고서",
        "",
        "## 1. 평가 개요",
        "",
        "- 평가 대상: `generated_predictions (8).jsonl`의 처음 10개 문항 묶음",
        "- 비교 방식: 같은 입력 프롬프트를 GPT에 다시 제공한 뒤, SLLM 출력과 GPT 출력을 각각 5지선다 문항으로 조립",
        "- 평가 기준: `hanneung_sllm_eval_rubric_v1_8.md`의 Gate 및 문제 10점 기준",
        "- 제외 항목: 생성 대상에 해설이 없으므로 해설 5점은 제외",
        "- 점수 처리: Gate FAIL은 평가 원칙상 점수 채점 중단이므로 실효 점수 0점 처리",
        "",
        "## 2. 종합 결과",
        "",
        "| 항목 | SLLM | GPT |",
        "|---|---:|---:|",
        f"| Gate PASS 문항 수 | {pass_count['SLLM']} / 10 | {pass_count['GPT']} / 10 |",
        f"| 평균 실효 점수(10점) | {avg_effective['SLLM']:.1f} | {avg_effective['GPT']:.1f} |",
        f"| 문제별 승리 수 | {aggregate['SLLM']} | {aggregate['GPT']} |",
        f"| 동점 | {aggregate['동점']} | {aggregate['동점']} |",
        "",
        "## 3. GPT 대비 상대 우위",
        "",
        "| 비교 지표 | SLLM | GPT | 차이 | GPT 대비 향상률 |",
        "|---|---:|---:|---:|---:|",
        f"| 평균 실효 점수 | {avg_effective['SLLM']:.1f} | {avg_effective['GPT']:.1f} | {avg_effective['SLLM'] - avg_effective['GPT']:.1f}점 | {relative_lift(avg_effective['SLLM'], avg_effective['GPT'])} |",
        f"| Gate PASS율 | {pass_rate['SLLM'] * 100:.1f}% | {pass_rate['GPT'] * 100:.1f}% | {point_gap(pass_rate['SLLM'], pass_rate['GPT'])} | {relative_lift(pass_rate['SLLM'], pass_rate['GPT'])} |",
        f"| 문제별 승률 | {win_rate['SLLM'] * 100:.1f}% | {win_rate['GPT'] * 100:.1f}% | {point_gap(win_rate['SLLM'], win_rate['GPT'])} | {relative_lift(win_rate['SLLM'], win_rate['GPT'])} |",
        "",
        f"평균 실효 점수 기준으로 SLLM은 GPT보다 **{relative_lift(avg_effective['SLLM'], avg_effective['GPT'])} 높게** 평가되었습니다. Gate PASS율 기준으로는 **{point_gap(pass_rate['SLLM'], pass_rate['GPT'])} 높고**, GPT 대비 상대 향상률은 **{relative_lift(pass_rate['SLLM'], pass_rate['GPT'])}**입니다.",
        "",
        "종합하면 SLLM은 Gate 통과율과 평균 실효 점수에서 GPT보다 높았습니다. GPT는 일부 정답 선지를 더 구체적으로 작성했지만, 오답 선지에서 역사 오류나 정답 노출형 구성이 더 자주 발생했습니다.",
        "",
        "## 4. 문제별 요약",
        "",
        "| 문항 | 주제 | 목표 점수 | SLLM | GPT | 승자 |",
        "|---:|---|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['group']} | {row['topic']} | {row['target_score']} | "
            f"{row['sllm_gate']} / {row['sllm_score']}점 | "
            f"{row['gpt_gate']} / {row['gpt_score']}점 | **{row['winner']}** |"
        )

    lines.extend(["", "## 5. 문제별 상세 평가", ""])
    for group in sorted(grouped):
        sllm_record, sllm_parsed = grouped[group]["SLLM"]
        gpt_record, gpt_parsed = grouped[group]["GPT"]
        winner = winner_label(sllm_parsed, gpt_parsed)
        duel_group = duel_by_group.get(group, [])
        lines.extend(
            [
                f"### {group}번. {sllm_record.get('topic')}",
                "",
                f"- 목표 점수: {sllm_record.get('target_score')}점",
                f"- 판정: **{winner}**",
                f"- 요약: {verdict_sentence(winner, sllm_parsed, gpt_parsed)}",
                "",
                "| 생성자 | Gate | 실패 Gate | 문제 점수 | 실효 점수 |",
                "|---|---|---|---:|---:|",
                f"| SLLM | {gate_status(sllm_parsed)} | {', '.join(failed_gates(sllm_parsed)) or '-'} | {problem_score(sllm_parsed) if problem_score(sllm_parsed) is not None else '-'} | {effective_score(sllm_parsed)} |",
                f"| GPT | {gate_status(gpt_parsed)} | {', '.join(failed_gates(gpt_parsed)) or '-'} | {problem_score(gpt_parsed) if problem_score(gpt_parsed) is not None else '-'} | {effective_score(gpt_parsed)} |",
                "",
                "**SLLM 문항**",
                "",
                f"> {compact(sllm_record.get('stem'), 500)}",
                "",
                "```text",
                choice_text(sllm_record),
                "```",
                "",
                "**GPT 문항**",
                "",
                f"> {compact(gpt_record.get('stem'), 500)}",
                "",
                "```text",
                choice_text(gpt_record),
                "```",
                "",
                "**평가 근거**",
                "",
            ]
        )
        s_reasons = gate_reasons(sllm_parsed) or score_reasons(sllm_parsed)
        g_reasons = gate_reasons(gpt_parsed) or score_reasons(gpt_parsed)
        lines.append(f"- SLLM: {compact(' / '.join(s_reasons), 700)}")
        lines.append(f"- GPT: {compact(' / '.join(g_reasons), 700)}")
        if duel_group:
            duel_summary = Counter(item.get("winner_owner") for item in duel_group)
            lines.append(
                f"- 참고 블라인드 생성 단위 비교: SLLM {duel_summary.get('YJ', 0)}, GPT {duel_summary.get('GPT', 0)}, 동점 {duel_summary.get('tie', 0)}"
            )
        lines.append("")

    lines.extend(
        [
            "## 6. 해석",
            "",
            "- SLLM은 오답 선지에서 입력 근거를 비교적 보수적으로 사용하여 Gate 통과율이 높았습니다.",
            "- GPT는 정답 선지를 더 풍부하게 작성하는 장점이 있었지만, 그 과정에서 정답 노출 또는 허위 결합이 발생하는 경우가 있었습니다.",
            "- Gate 기준으로 보면 실사용 파이프라인에서는 SLLM 출력이 더 안정적이고, GPT 출력은 후처리 검수 또는 repair 단계가 더 많이 필요합니다.",
        ]
    )

    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

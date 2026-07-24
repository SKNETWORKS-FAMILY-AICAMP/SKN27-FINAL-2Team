"""최종 평가에서 탈락한 모의고사 문항만 다른 승인 pack으로 교체한다.

이미 통과한 문항은 보존하며 실패 문항별 replacement를 생성·평가한 뒤 전체 모의고사와
사람용 파일을 다시 쓴다. 신규 생성 파이프라인이 아니라 평가 후 교체 단계다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from question_generation.legacy.workflows.mock_exam import render_markdown, run_pack, safe_id, validate_questions
from question_generation.legacy.workflows.batch import eligible_packs
from question_generation.evaluation.v18 import DEFAULT_MIN_ACCEPT_SCORE, LABELS
from question_generation.workflows.question_pipeline import invalidate


def parse_args() -> argparse.Namespace:
    """교체할 모의고사 run 폴더와 pack 재시도 횟수를 읽는다."""
    parser = argparse.ArgumentParser(description="Replace final-evaluation failures in an existing mock exam.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--pack-retries", type=int, default=1)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    """UTF-8 BOM을 허용해 JSON 객체를 읽는다."""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """평가 JSONL을 행별 객체 목록으로 읽는다."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluation_accepted(row: dict[str, Any]) -> bool:
    """Gate PASS이면서 문제 품질 총점도 운영 기준 이상인지 확인한다."""
    parsed = row.get("parsed") or {}
    score = parsed.get("problem_score") or {}
    return parsed.get("gate_result") == "PASS" and int(score.get("total_score") or 0) >= DEFAULT_MIN_ACCEPT_SCORE


def evaluation_repair_targets(question: dict[str, Any], row: dict[str, Any]) -> list[str]:
    """평가기의 최종 선지 번호를 원래 생성 컴포넌트 슬롯으로 바꾼다."""
    parsed = row.get("parsed") or {}
    requested = [
        *(parsed.get("repair_targets") or []),
        *((parsed.get("problem_score") or {}).get("revision_targets") or []),
    ]
    targets: list[str] = []
    for target in requested:
        if target in {"material", "correct"}:
            targets.append(target)
            continue
        if not str(target).startswith("choice:"):
            continue
        label = str(target).split(":", 1)[1]
        number = LABELS.index(label) + 1 if label in LABELS else int(label) if label.isdigit() else 0
        choice = next((item for item in question.get("choices", []) if int(item.get("number") or 0) == number), None)
        if not choice:
            continue
        if choice.get("is_answer"):
            targets.append("correct")
        else:
            slot = (choice.get("source") or {}).get("slot") or choice.get("distractor_index")
            if slot:
                targets.append(f"distractor:{slot}")
    return list(dict.fromkeys(targets))


def repair_feedback(row: dict[str, Any]) -> str:
    """오류 원문을 재노출하지 않고 근거 준수만 다시 지시한다."""
    del row
    return (
        "이전 출력은 최종 평가를 통과하지 못했다. 제공된 입력 근거의 인명·용어·주체·행위·시기를 "
        "정확히 사용해 새로 작성하고 이전 출력의 표현은 재사용하지 않는다."
    )


def repair_checkpoint(question: dict[str, Any], row: dict[str, Any], path: Path, retries: int) -> dict[str, Any] | None:
    """평가에서 지목한 컴포넌트만 비우고 기존 체크포인트를 한 번 재개한다."""
    targets = evaluation_repair_targets(question, row)
    if not targets or not path.exists():
        return None
    state = read_json(path)
    if state.get("status") != "complete":
        return None
    invalidate(state, targets, repair_feedback(row))
    state["status"] = "prepared"
    state["assembly_attempts"] = 0
    state.pop("error", None)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_pack(str(state["pack_id"]), path, retries)


def evaluate_one(question: dict[str, Any], run_dir: Path, index: int) -> dict[str, Any]:
    """교체 후보 한 문항을 임시 평가 입력으로 만들고 v1.8 평가 결과를 반환한다."""
    evaluation_dir = run_dir / "evaluation" / "replenish"
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    input_path = evaluation_dir / f"{index:04d}_input.json"
    prefix = evaluation_dir / f"{index:04d}_result"
    input_path.write_text(
        json.dumps({"schema_version": "question_bank_evaluation_batch_v1", "questions": [question]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "question_generation.evaluation.v18",
            "--input",
            str(input_path),
            "--output-prefix",
            str(prefix),
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"evaluation failed for {question.get('seed_id')}: {result.returncode}")
    rows = read_jsonl(prefix.with_suffix(".jsonl"))
    if len(rows) != 1:
        raise RuntimeError(f"evaluation output count mismatch for {question.get('seed_id')}")
    return rows[0]


def main() -> int:
    """실패 위치별 대체 문항을 찾아 최종 모의고사를 갱신한다."""
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    exam_path = args.run_dir / "mock_exam.json"
    md_path = args.run_dir / "mock_exam.md"
    evaluation_path = args.run_dir / "evaluation" / "v18_mock_exam.jsonl"
    exam = read_json(exam_path)
    evaluation_rows = read_jsonl(evaluation_path)
    evaluation_by_id = {str(row["question_id"]): row for row in evaluation_rows}
    quotas = {str(key): int(value) for key, value in exam["quotas"].items()}
    initial_manifest = read_json(args.run_dir / "run_manifest.json")
    attempt_paths = {
        str(item.get("pack_id") or ""): Path(item["output"])
        for item in initial_manifest.get("attempts") or []
        if item.get("output")
    }
    evaluation_index = 0
    repair_attempts: list[dict[str, Any]] = []

    accepted: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {difficulty: [] for difficulty in quotas}
    rejected_ids: set[str] = set()
    for question in exam["questions"]:
        question_id = str(question.get("seed_id") or "")
        row = evaluation_by_id.get(question_id)
        if row and not evaluation_accepted(row) and question_id in attempt_paths:
            targets = evaluation_repair_targets(question, row)
            repaired = repair_checkpoint(question, row, attempt_paths[question_id], args.pack_retries)
            if repaired and repaired.get("status") == "complete" and isinstance(repaired.get("question"), dict):
                evaluation_index += 1
                question = repaired["question"]
                row = evaluate_one(question, args.run_dir, evaluation_index)
                repair_attempts.append(
                    {
                        "pack_id": question_id,
                        "targets": targets,
                        "status": "complete" if evaluation_accepted(row) else "evaluation_failed",
                    }
                )
        if row and evaluation_accepted(row):
            accepted[question["difficulty_label"]].append((question, row))
        else:
            rejected_ids.add(question_id)

    attempted_ids = {str(row.get("pack_id") or "") for row in initial_manifest.get("attempts") or []}
    replenish_attempts: list[dict[str, Any]] = []

    for difficulty, quota in quotas.items():
        if len(accepted[difficulty]) >= quota:
            continue
        candidates = eligible_packs([difficulty], [], 0)
        for candidate in candidates:
            if len(accepted[difficulty]) >= quota:
                break
            pack_id = candidate["pack_id"]
            if pack_id in attempted_ids:
                continue
            attempted_ids.add(pack_id)
            attempt_index = len(replenish_attempts) + 1
            output = args.run_dir / "replenish_attempts" / difficulty / f"{attempt_index:04d}_{safe_id(pack_id)}.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            payload = run_pack(pack_id, output, args.pack_retries)
            status = str(payload.get("status") or "missing_output")
            row: dict[str, Any] | None = None
            if status == "complete" and isinstance(payload.get("question"), dict):
                evaluation_index += 1
                row = evaluate_one(payload["question"], args.run_dir, evaluation_index)
                if not evaluation_accepted(row):
                    targets = evaluation_repair_targets(payload["question"], row)
                    repaired = repair_checkpoint(payload["question"], row, output, args.pack_retries)
                    if repaired and repaired.get("status") == "complete" and isinstance(repaired.get("question"), dict):
                        evaluation_index += 1
                        payload = repaired
                        row = evaluate_one(payload["question"], args.run_dir, evaluation_index)
                        repair_attempts.append(
                            {
                                "pack_id": pack_id,
                                "targets": targets,
                                "status": "complete" if evaluation_accepted(row) else "evaluation_failed",
                            }
                        )
                status = "complete" if evaluation_accepted(row) else "evaluation_failed"
                if status == "complete":
                    accepted[difficulty].append((payload["question"], row))
            replenish_attempts.append(
                {
                    "difficulty": difficulty,
                    "pack_id": pack_id,
                    "topic": candidate["topic"],
                    "status": status,
                    "output": str(output),
                    "error": str(payload.get("error") or ""),
                    "evaluation_gate": (row or {}).get("parsed", {}).get("gate_result"),
                }
            )
            (args.run_dir / "finalization_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "question_bank_mock_exam_finalization_v1",
                        "quotas": quotas,
                        "accepted": {key: len(value) for key, value in accepted.items()},
                        "initial_rejected_ids": sorted(rejected_ids),
                        "repair_attempts": repair_attempts,
                        "status_counts": dict(Counter(item["status"] for item in replenish_attempts)),
                        "attempts": replenish_attempts,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        if len(accepted[difficulty]) != quota:
            raise RuntimeError(f"insufficient final-evaluation PASS questions for {difficulty}: {len(accepted[difficulty])}/{quota}")

    final_pairs = [pair for difficulty in quotas for pair in accepted[difficulty][: quotas[difficulty]]]
    final_questions = [pair[0] for pair in final_pairs]
    final_evaluations = [pair[1] for pair in final_pairs]
    validate_questions(final_questions, quotas)
    if any(not evaluation_accepted(row) for row in final_evaluations):
        raise RuntimeError("final mock exam contains evaluation failure")

    initial_json = args.run_dir / "mock_exam_initial_local_gate.json"
    initial_md = args.run_dir / "mock_exam_initial_local_gate.md"
    if not initial_json.exists():
        shutil.copy2(exam_path, initial_json)
    if not initial_md.exists():
        shutil.copy2(md_path, initial_md)
    exam["questions"] = final_questions
    exam["question_count"] = len(final_questions)
    exam["final_evaluation_gate"] = "PASS"
    exam_path.write_text(json.dumps(exam, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(final_questions), encoding="utf-8")
    final_jsonl = args.run_dir / "evaluation" / "final_passed.jsonl"
    final_jsonl.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in final_evaluations), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "complete",
                "question_count": len(final_questions),
                "quotas": quotas,
                "replenish_attempts": len(replenish_attempts),
                "json": str(exam_path),
                "markdown": str(md_path),
                "evaluation": str(final_jsonl),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

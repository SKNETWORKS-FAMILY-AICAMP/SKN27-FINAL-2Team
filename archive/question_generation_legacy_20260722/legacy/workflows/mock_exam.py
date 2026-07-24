"""난이도별 정해진 수량으로 모의고사 한 회분을 생성한다.

DB의 승인 pack을 단일 파이프라인으로 생성하면서 로컬 Gate PASS 문항만 채택한다.
목표 수량을 만족하면 JSON과 사람용 Markdown을 만들고, 선택적으로 최종 평가를 실행한다.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from question_generation.legacy.workflows.batch import eligible_packs


def parse_args() -> argparse.Namespace:
    """난이도별 목표 수량, 재시도, 최종 평가 옵션을 읽는다."""
    parser = argparse.ArgumentParser(description="Generate an exact-quota mock exam from approved question-bank packs.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--easy", type=int, default=10)
    parser.add_argument("--medium", type=int, default=30)
    parser.add_argument("--hard", type=int, default=10)
    parser.add_argument("--pack-retries", type=int, default=1)
    parser.add_argument("--candidate-multiplier", type=int, default=3)
    parser.add_argument("--evaluate", action="store_true")
    return parser.parse_args()


def read_payload(path: Path) -> dict[str, Any]:
    """생성 체크포인트가 존재하면 JSON으로 읽고 없으면 빈 객체를 반환한다."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def safe_id(value: str) -> str:
    """pack ID를 Windows에서도 안전한 파일명 조각으로 변환한다."""
    return re.sub(r"[^0-9A-Za-z_-]+", "_", value).strip("_")


def write_progress(path: Path, attempts: list[dict[str, Any]], quotas: dict[str, int]) -> None:
    """현재 시도와 난이도별 채택 수를 progress JSON에 기록한다."""
    completed = Counter(row["difficulty"] for row in attempts if row.get("status") == "complete")
    path.write_text(
        json.dumps(
            {
                "schema_version": "question_bank_mock_exam_run_v1",
                "quotas": quotas,
                "completed": dict(completed),
                "status_counts": dict(Counter(row.get("status", "unknown") for row in attempts)),
                "attempts": attempts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def run_pack(pack_id: str, path: Path, retries: int) -> dict[str, Any]:
    """pack 하나를 단일 파이프라인으로 실행하고 실패 시 지정 횟수만 재시도한다."""
    for _ in range(retries + 1):
        payload = read_payload(path)
        if payload.get("status") == "complete":
            return payload
        command = [
            sys.executable,
            "-m",
            "question_generation.workflows.question_pipeline",
            "--pack-id",
            pack_id,
            "--output",
            str(path),
        ]
        if path.exists() and payload.get("status") not in {"generation_exhausted", "pack_rejected"}:
            command.append("--resume")
        result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        payload = read_payload(path)
        payload.setdefault("returncode", result.returncode)
        if payload.get("status") == "complete" or payload.get("status") in {"generation_exhausted", "pack_rejected"}:
            return payload
    return read_payload(path)


def validate_questions(questions: list[dict[str, Any]], quotas: dict[str, int]) -> None:
    """최종 문항 수, 난이도 쿼터, 5선지, 로컬 Gate PASS를 확인한다."""
    counts = Counter(str(question.get("difficulty_label")) for question in questions)
    if counts != Counter(quotas):
        raise RuntimeError(f"mock exam quota mismatch: {dict(counts)} != {quotas}")
    ids = [str(question.get("seed_id") or "") for question in questions]
    if not all(ids) or len(ids) != len(set(ids)):
        raise RuntimeError("mock exam contains missing or duplicate seed_id")
    for question in questions:
        choices = question.get("choices") or []
        if len(choices) != 5 or sum(bool(choice.get("is_answer")) for choice in choices) != 1:
            raise RuntimeError(f"invalid choices: {question.get('seed_id')}")
        if (question.get("validation") or {}).get("gate_result") != "PASS":
            raise RuntimeError(f"local Gate failed: {question.get('seed_id')}")


def render_markdown(questions: list[dict[str, Any]]) -> str:
    """완성 모의고사를 문제·선지·정답이 포함된 사람용 Markdown으로 만든다."""
    labels = "①②③④⑤"
    lines = ["# 한능검 심화 모의고사", ""]
    for index, question in enumerate(questions, start=1):
        image = question.get("image") or {}
        image_url = image.get("original_image_url") or image.get("thumbnail_url")
        lines.extend(
            [
                f"## {index}. [{question['target_score']}점]",
                "",
                *( [f"![문항 시각 자료]({image_url})", ""] if image_url else [] ),
                str(question.get("material") or ""),
                "",
                str(question.get("question") or ""),
                "",
            ]
        )
        for choice in sorted(question["choices"], key=lambda row: int(row["number"])):
            lines.append(f"{labels[int(choice['number']) - 1]} {choice['text']}")
        lines.append("")
    lines.extend(["# 정답", ""])
    lines.append(" | ".join(f"{index}. {question['answer_number']}" for index, question in enumerate(questions, start=1)))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """난이도 쿼터가 찰 때까지 후보 pack을 순차 생성하는 CLI 진입점."""
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    if min(args.easy, args.medium, args.hard, args.pack_retries, args.candidate_multiplier) < 0:
        raise ValueError("quota and retry values must be non-negative")
    if args.candidate_multiplier < 1:
        raise ValueError("candidate-multiplier must be at least 1")

    quotas = {"쉬움": args.easy, "보통": args.medium, "어려움": args.hard}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    attempts_dir = args.output_dir / "attempts"
    attempts_dir.mkdir(exist_ok=True)
    progress_path = args.output_dir / "run_manifest.json"
    attempts: list[dict[str, Any]] = []
    selected_questions: list[dict[str, Any]] = []

    for difficulty, quota in quotas.items():
        completed = 0
        candidates = eligible_packs([difficulty], [], max(quota * args.candidate_multiplier, quota))
        for candidate_index, pack in enumerate(candidates, start=1):
            if completed >= quota:
                break
            path = attempts_dir / difficulty / f"{candidate_index:04d}_{safe_id(pack['pack_id'])}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = run_pack(pack["pack_id"], path, args.pack_retries)
            status = str(payload.get("status") or "missing_output")
            attempts.append(
                {
                    "difficulty": difficulty,
                    "pack_id": pack["pack_id"],
                    "topic": pack["topic"],
                    "status": status,
                    "output": str(path),
                    "llm_calls": int(payload.get("total_llm_calls") or 0),
                    "error": str(payload.get("error") or ""),
                }
            )
            if status == "complete" and isinstance(payload.get("question"), dict):
                selected_questions.append(payload["question"])
                completed += 1
            write_progress(progress_path, attempts, quotas)
        if completed != quota:
            raise RuntimeError(f"insufficient completed questions for {difficulty}: {completed}/{quota}")

    validate_questions(selected_questions, quotas)
    exam = {
        "schema_version": "question_bank_mock_exam_v1",
        "quotas": quotas,
        "question_count": len(selected_questions),
        "questions": selected_questions,
    }
    json_path = args.output_dir / "mock_exam.json"
    md_path = args.output_dir / "mock_exam.md"
    json_path.write_text(json.dumps(exam, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(selected_questions), encoding="utf-8")

    evaluation_returncode = 0
    if args.evaluate:
        evaluation_prefix = args.output_dir / "evaluation" / "v18_mock_exam"
        evaluation_prefix.parent.mkdir(exist_ok=True)
        evaluation_returncode = subprocess.run(
            [
                sys.executable,
                "-m",
                "question_generation.evaluation.v18",
                "--input",
                str(json_path),
                "--output-prefix",
                str(evaluation_prefix),
            ],
            cwd=PROJECT_ROOT,
            check=False,
        ).returncode
        if not evaluation_returncode:
            evaluation_returncode = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "question_generation.legacy.workflows.finalize",
                    "--run-dir",
                    str(args.output_dir),
                    "--pack-retries",
                    str(args.pack_retries),
                ],
                cwd=PROJECT_ROOT,
                check=False,
            ).returncode

    print(
        json.dumps(
            {
                "status": "complete" if not evaluation_returncode else "evaluation_failed",
                "question_count": len(selected_questions),
                "json": str(json_path),
                "markdown": str(md_path),
                "evaluation_executed": args.evaluate,
            },
            ensure_ascii=False,
        )
    )
    return evaluation_returncode


if __name__ == "__main__":
    raise SystemExit(main())

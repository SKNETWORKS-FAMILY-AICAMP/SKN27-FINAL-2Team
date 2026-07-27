"""승인된 고정 basis pack 여러 개를 단일 문항 파이프라인으로 순차 실행한다.

각 pack은 ``items`` 폴더의 독립 체크포인트로 저장된다. ``--evaluate``를 사용하면
생성이 끝난 문항을 하나의 evaluation_input.json으로 합쳐 v1.8 평가를 한 번 실행한다.
ChoiceFact 풀을 새로 조립하는 배치가 아니라 ``qgen.basis_packs`` 전용 배치다.
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

from app.chatbot.rag.pgvector_retriever import connect_db
from question_generation.generation.material_rules import material_type_route_status
from question_generation.core.contracts import validate_pack
from question_generation.legacy.retrieval.pack_repository import read_pack


def parse_args() -> argparse.Namespace:
    """배치 출력 폴더, 필터, 실행·재개·평가 옵션을 읽는다."""
    parser = argparse.ArgumentParser(description="Run approved question-bank packs through the production E2E pipeline.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=1, help="Number of packs to run; use 0 for every eligible pack.")
    parser.add_argument("--difficulty", action="append", default=[])
    parser.add_argument("--question-task", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate the completed batch only after generation finishes.")
    return parser.parse_args()


def eligible_packs(difficulties: list[str], tasks: list[str], count: int) -> list[dict[str, str]]:
    """DB에서 의미 검증을 통과하고 5개 item이 모두 준비된 pack만 조회한다."""
    filters = ["p.status = 'rag_ready'", "p.semantic_status = 'pass'"]
    params: list[Any] = []
    if difficulties:
        filters.append("p.difficulty_label = ANY(%s)")
        params.append(difficulties)
    if tasks:
        filters.append("p.question_task = ANY(%s)")
        params.append(tasks)
    query = f"""
        SELECT p.pack_id, p.target_label, p.difficulty_label, p.question_task, p.material_type
        FROM qgen.basis_packs p
        JOIN qgen.basis_items i USING (pack_id)
        WHERE {' AND '.join(filters)}
        GROUP BY p.pack_id
        HAVING count(*) = 5
           AND bool_and(i.status = 'rag_ready' AND i.semantic_status = 'pass')
        ORDER BY p.difficulty_label, p.question_task, p.source_question_id
    """
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            packs = [
                {
                    "pack_id": row[0],
                    "topic": row[1],
                    "difficulty_label": row[2],
                    "question_task": row[3],
                    "material_type": row[4],
                }
                for row in cur.fetchall()
            ]
            validated = []
            for pack in packs:
                if material_type_route_status(pack)["status"] != "ok":
                    continue
                try:
                    validate_pack(read_pack(pack["pack_id"]))
                except (KeyError, RuntimeError, ValueError):
                    continue
                validated.append(pack)
                if count > 0 and len(validated) >= count:
                    break
            return validated
    finally:
        conn.close()


def output_path(output_dir: Path, index: int, pack_id: str) -> Path:
    """pack ID를 안전한 파일명으로 바꿔 문항 체크포인트 경로를 만든다."""
    safe_id = re.sub(r"[^0-9A-Za-z_-]+", "_", pack_id).strip("_")
    return output_dir / "items" / f"{index:04d}_{safe_id}.json"


def read_status(path: Path) -> str:
    """기존 체크포인트의 status를 읽고 없거나 손상된 상태를 구분한다."""
    if not path.exists():
        return "pending"
    try:
        return str(json.loads(path.read_text(encoding="utf-8-sig")).get("status") or "unknown")
    except (OSError, json.JSONDecodeError):
        return "invalid_output"


def write_manifest(path: Path, items: list[dict[str, Any]]) -> None:
    """배치 문항별 pending/complete/failed 상태를 재개 가능한 manifest로 저장한다."""
    path.write_text(
        json.dumps(
            {
                "schema_version": "question_bank_batch_v1",
                "status_counts": dict(Counter(item["status"] for item in items)),
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def write_evaluation_input(path: Path, items: list[dict[str, Any]]) -> int:
    """완료된 체크포인트에서 최종 question만 모아 평가기 입력 파일을 만든다."""
    questions = []
    for item in items:
        output = Path(item["output"])
        if item["status"] != "complete" or not output.exists():
            continue
        payload = json.loads(output.read_text(encoding="utf-8-sig"))
        question = payload.get("question")
        if isinstance(question, dict):
            questions.append(question)
    path.write_text(
        json.dumps({"schema_version": "question_bank_evaluation_batch_v1", "questions": questions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(questions)


def main() -> int:
    """pack별 하위 프로세스를 순차 실행하고 선택적으로 최종 평가를 호출한다."""
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "items").mkdir(exist_ok=True)

    manifest_path = args.output_dir / "manifest.json"
    packs = eligible_packs(args.difficulty, args.question_task, args.count)
    items = []
    for index, pack in enumerate(packs, start=1):
        path = output_path(args.output_dir, index, pack["pack_id"])
        items.append({**pack, "output": str(path), "status": read_status(path)})
    write_manifest(manifest_path, items)
    if not args.execute:
        print(json.dumps({"status": "prepared", "count": len(items), "manifest": str(manifest_path)}, ensure_ascii=False))
        return 0

    for item in items:
        path = Path(item["output"])
        if args.resume and item["status"] == "complete":
            continue
        command = [
            sys.executable,
            "-m",
            "question_generation.workflows.question_pipeline",
            "--pack-id",
            item["pack_id"],
            "--output",
            str(path),
        ]
        if args.resume and path.exists():
            command.append("--resume")
        result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        item["returncode"] = result.returncode
        item["status"] = read_status(path)
        write_manifest(manifest_path, items)
        if result.returncode and not args.continue_on_error:
            break

    evaluation_input = args.output_dir / "evaluation_input.json"
    evaluation_count = write_evaluation_input(evaluation_input, items)
    evaluation_returncode = 0
    if args.evaluate and evaluation_count:
        evaluation_dir = args.output_dir / "evaluation"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "question_generation.evaluation.v18",
                "--input",
                str(evaluation_input),
                "--output-prefix",
                str(evaluation_dir / "v18_batch"),
            ],
            cwd=PROJECT_ROOT,
            check=False,
        )
        evaluation_returncode = result.returncode

    counts = Counter(item["status"] for item in items)
    print(
        json.dumps(
            {
                "status": "finished",
                "counts": dict(counts),
                "manifest": str(manifest_path),
                "evaluation_input": str(evaluation_input),
                "evaluation_count": evaluation_count,
                "evaluation_executed": args.evaluate,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not any(item.get("returncode") for item in items) and not evaluation_returncode else 1


if __name__ == "__main__":
    main()

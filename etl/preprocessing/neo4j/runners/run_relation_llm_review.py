from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_arguments(neo4j_root: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Review relation candidates once with GPT-5.5. "
            "This runner never loads Neo4j."
        )
    )
    parser.add_argument(
        "--config",
        default=str(neo4j_root / "config" / "relation_llm_review.json"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root
            / "output"
            / "internal"
            / "model_review"
            / "relation_review"
        ),
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    neo4j_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(neo4j_root))
    from fact_retrieval.relation_llm_review import (
        build_relation_review_tasks,
        compile_final_decisions,
        execute_review_phase,
        read_json,
        write_final_outputs,
        write_jsonl,
    )

    args = parse_arguments(neo4j_root)
    if args.limit < 0:
        raise ValueError("--limit must be zero or positive")
    if args.workers < 0:
        raise ValueError("--workers must be zero or positive")

    config_path = Path(args.config)
    config = read_json(config_path)
    config_root = config_path.parent
    evaluation_config = config["evaluation"]
    prompt_path = config_root / evaluation_config["prompt_file"]
    schema_path = config_root / evaluation_config["schema_file"]
    output_root = neo4j_root / "output"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tasks = build_relation_review_tasks(
        output_root=output_root,
        minimum_type_signature_support=int(
            config["minimum_type_signature_support"]
        ),
    )
    if args.limit > 0:
        tasks = tasks[: args.limit]
    task_path = output_dir / "relation_review_tasks.jsonl"
    write_jsonl(task_path, tasks)

    origin_counts: dict[str, int] = {}
    for task in tasks:
        origin = task["review_origin"]
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
    plan = {
        "task_count": len(tasks),
        "origin_counts": origin_counts,
        "evaluation_model": evaluation_config["model"],
        "reasoning_effort": evaluation_config["reasoning_effort"],
        "neo4j_load": False,
        "task_path": str(task_path.resolve()),
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return

    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    execution_config = config["execution"]
    workers = args.workers
    if workers == 0:
        workers = int(execution_config["maximum_workers"])
    client = OpenAI(
        timeout=float(execution_config["timeout_seconds"]),
        max_retries=0,
    )

    def evaluation_payload(task: dict[str, Any]) -> dict[str, Any]:
        return {"task": task}

    evaluation_result = execute_review_phase(
        client=client,
        tasks=tasks,
        checkpoint_path=output_dir / "evaluation_checkpoint_v1_1.jsonl",
        failure_path=output_dir / "evaluation_failures_v1_1.csv",
        prompt_path=prompt_path,
        schema_path=schema_path,
        model_config=evaluation_config,
        execution_config=execution_config,
        policy_version=config["policy_version"],
        payload_builder=evaluation_payload,
        maximum_workers=workers,
    )
    final_records = compile_final_decisions(
        tasks=tasks,
        evaluation_records=evaluation_result["records"],
    )
    output_paths = write_final_outputs(
        output_dir=output_dir,
        tasks=tasks,
        evaluation_result=evaluation_result,
        final_records=final_records,
        policy=config,
        config_path=config_path,
    )
    print(
        json.dumps(
            {
                "final_decision_count": len(final_records),
                "output_paths": output_paths,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

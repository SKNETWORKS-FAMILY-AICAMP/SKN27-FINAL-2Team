from argparse import ArgumentParser
from datetime import datetime, timezone
from json import dumps, loads
from pathlib import Path
from typing import Callable
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import load_pipeline_policy
from entity_resolution.execute_term_review import (
    load_json_schema,
    load_text_file,
    validate_structured_output_schema,
)
from entity_resolution.problem_review import (
    validate_problem_decision_shape,
)
from entity_resolution.semantic_review import load_jsonl, write_jsonl
from goldset.build_gold_set import calculate_file_sha256


def apply_controlled_problem_fields(
    decision: dict,
    task: dict,
    policy: dict,
) -> dict:
    """모델이 변경하면 안 되는 problem 결정 필드를 task와 정책값으로 고정한다."""
    semantic_policy = policy["entity_resolution"]["semantic_review"]
    controlled = dict(decision)
    controlled["problem_review_task_id"] = task["problem_review_task_id"]
    controlled["problem_assignment_id"] = task["problem_assignment_id"]
    controlled["resolution_case_id"] = task["resolution_case_id"]
    controlled["decision_status"] = semantic_policy["decision_status_input"]
    controlled["review_model"] = semantic_policy["problem_model"]["model"]
    controlled["prompt_version"] = semantic_policy["problem_prompt_version"]
    return controlled


def validate_problem_executor_decision(
    decision: dict,
    task: dict,
) -> list[str]:
    """체크포인트 저장 전에 선택 ID와 selection mode를 검증한다."""
    errors = validate_problem_decision_shape(decision)
    selected_ids = decision.get("selected_canonical_alternative_ids")
    if not isinstance(selected_ids, list):
        return errors

    allowed_ids = {
        alternative["canonical_alternative_id"]
        for alternative in task["canonical_alternatives"]
    }
    selected_id_set = set(selected_ids)
    unknown_ids = selected_id_set.difference(allowed_ids)
    if len(selected_id_set) != len(selected_ids):
        errors.append("DUPLICATE_ALTERNATIVE_SELECTION")
    if unknown_ids:
        errors.append(
            "UNKNOWN_CANONICAL_ALTERNATIVE: "
            + dumps(sorted(unknown_ids), ensure_ascii=False)
        )

    selection_mode = str(decision.get("selection_mode") or "")
    allowed_modes = {"SINGLE", "MULTIPLE", "AMBIGUOUS", "NONE"}
    if selection_mode not in allowed_modes:
        errors.append(f"INVALID_SELECTION_MODE: {selection_mode}")
    elif selection_mode == "SINGLE" and len(selected_ids) != 1:
        errors.append("SELECTION_MODE_CARDINALITY_MISMATCH")
    elif selection_mode == "MULTIPLE" and len(selected_ids) < 2:
        errors.append("SELECTION_MODE_CARDINALITY_MISMATCH")
    elif selection_mode == "NONE" and selected_ids:
        errors.append("SELECTION_MODE_CARDINALITY_MISMATCH")
    return errors


def request_problem_decision(
    client: object,
    task: dict,
    prompt: str,
    schema: dict,
    policy: dict,
) -> tuple[dict, dict]:
    """OpenAI Responses API로 problem-level canonical 대안을 선택한다."""
    semantic_policy = policy["entity_resolution"]["semantic_review"]
    model_policy = semantic_policy["problem_model"]
    executor_policy = semantic_policy["problem_executor"]
    request_task = dict(task)
    request_task["review_model"] = model_policy["model"]
    request_task["prompt_version"] = semantic_policy[
        "problem_prompt_version"
    ]
    request_arguments: dict[str, object] = {
        "model": model_policy["model"],
        "instructions": prompt,
        "input": dumps(request_task, ensure_ascii=False),
        "max_output_tokens": int(executor_policy["maximum_output_tokens"]),
        "reasoning": {"effort": model_policy["reasoning_effort"]},
        "store": bool(executor_policy["store_response"]),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "problem_resolution_decision",
                "schema": schema,
                "strict": True,
            }
        },
    }
    if model_policy.get("send_temperature"):
        request_arguments["temperature"] = float(model_policy["temperature"])
    service_tier = str(executor_policy.get("service_tier") or "")
    if service_tier:
        request_arguments["service_tier"] = service_tier

    response = client.responses.create(**request_arguments)
    raw_output = str(response.output_text or "").strip()
    if not raw_output:
        raise ValueError("LLM problem decision 응답이 비어 있습니다.")
    parsed = loads(raw_output)
    if not isinstance(parsed, dict):
        raise ValueError("LLM problem decision은 JSON 객체여야 합니다.")
    decision = apply_controlled_problem_fields(parsed, task, policy)
    validation_errors = validate_problem_executor_decision(decision, task)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    usage: dict = {}
    if getattr(response, "usage", None) is not None:
        usage = response.usage.model_dump()
    return decision, {
        "response_id": str(getattr(response, "id", "")),
        "usage": usage,
    }


def select_problem_execution_tasks(
    tasks: list[dict],
    limit: int,
) -> list[dict]:
    """0이면 전체, 양수면 입력 순서의 앞쪽 problem task만 선택한다."""
    if limit < 0:
        raise ValueError("problem review 실행 limit은 0 이상이어야 합니다.")
    selected = tasks
    if limit > 0:
        selected = tasks[:limit]
    return selected


def load_compatible_problem_checkpoint(
    checkpoint_path: str,
    tasks_by_id: dict[str, dict],
    policy: dict,
) -> dict[str, dict]:
    """현재 problem 모델·prompt·정책과 같은 성공 checkpoint만 재사용한다."""
    checkpoint = Path(checkpoint_path)
    if not checkpoint.is_file():
        return {}
    semantic_policy = policy["entity_resolution"]["semantic_review"]
    compatible: dict[str, dict] = {}
    with checkpoint.open("r", encoding="utf-8") as checkpoint_file:
        for line_number, line in enumerate(checkpoint_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            record = loads(stripped)
            if not isinstance(record, dict):
                raise ValueError(
                    f"problem checkpoint {line_number}행은 객체여야 합니다."
                )
            task_id = str(record.get("problem_review_task_id") or "")
            task = tasks_by_id.get(task_id)
            if task is None:
                continue
            if (
                record.get("review_model")
                != semantic_policy["problem_model"]["model"]
            ):
                continue
            if (
                record.get("prompt_version")
                != semantic_policy["problem_prompt_version"]
            ):
                continue
            if record.get("resolution_policy_version") != policy[
                "policy_version"
            ]:
                continue
            decision = record.get("decision")
            if not isinstance(decision, dict):
                continue
            if validate_problem_executor_decision(decision, task):
                continue
            compatible[task_id] = record
    return compatible


def build_problem_execution_plan(
    tasks: list[dict],
    checkpoint_path: str,
    policy: dict,
    limit: int,
) -> dict[str, int]:
    """API 호출 없이 problem task의 재사용·호출 예정 수를 계산한다."""
    selected_tasks = select_problem_execution_tasks(tasks, limit)
    tasks_by_id = {
        task["problem_review_task_id"]: task for task in selected_tasks
    }
    checkpoint_records = load_compatible_problem_checkpoint(
        checkpoint_path,
        tasks_by_id,
        policy,
    )
    pending_count = sum(
        task["problem_review_task_id"] not in checkpoint_records
        for task in selected_tasks
    )
    return {
        "selected_task_count": len(selected_tasks),
        "reused_checkpoint_count": len(checkpoint_records),
        "pending_task_count": pending_count,
        "selected_input_character_count": sum(
            len(dumps(task, ensure_ascii=False)) for task in selected_tasks
        ),
    }


def execute_problem_review_tasks(
    tasks: list[dict],
    prompt: str,
    schema: dict,
    checkpoint_path: str,
    policy: dict,
    client: object | None,
    limit: int = 0,
    maximum_retries: int | None = None,
    requester: Callable[
        [object, dict, str, dict, dict],
        tuple[dict, dict],
    ] = request_problem_decision,
) -> dict[str, object]:
    """problem task를 실행하고 성공 응답을 즉시 checkpoint에 저장한다."""
    schema_errors = validate_structured_output_schema(schema, policy)
    if schema_errors:
        raise ValueError(
            "problem Structured Output schema 오류: "
            + ", ".join(schema_errors)
        )
    selected_tasks = select_problem_execution_tasks(tasks, limit)
    tasks_by_id = {
        task["problem_review_task_id"]: task for task in selected_tasks
    }
    checkpoint_records = load_compatible_problem_checkpoint(
        checkpoint_path,
        tasks_by_id,
        policy,
    )
    executor_policy = policy["entity_resolution"]["semantic_review"][
        "problem_executor"
    ]
    retry_count = int(executor_policy["maximum_retries"])
    if maximum_retries is not None:
        retry_count = maximum_retries
    if retry_count < 0:
        raise ValueError("problem review 재시도 횟수는 0 이상이어야 합니다.")

    checkpoint = Path(checkpoint_path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    failure_rows: list[dict] = []
    attempted_count = 0
    succeeded_count = 0
    with checkpoint.open("a", encoding="utf-8") as checkpoint_file:
        for task in selected_tasks:
            task_id = task["problem_review_task_id"]
            if task_id in checkpoint_records:
                continue
            attempted_count += 1
            decision: dict | None = None
            response_metadata: dict = {}
            last_error = ""
            for attempt in range(1, retry_count + 2):
                try:
                    decision, response_metadata = requester(
                        client,
                        task,
                        prompt,
                        schema,
                        policy,
                    )
                    break
                except Exception as error:
                    last_error = str(error)
                    print(
                        f"problem task 실패 {task_id} "
                        f"({attempt}/{retry_count + 1}): {last_error}"
                    )
            if decision is None:
                failure_rows.append(
                    {
                        "problem_review_task_id": task_id,
                        "problem_assignment_id": task[
                            "problem_assignment_id"
                        ],
                        "resolution_case_id": task["resolution_case_id"],
                        "attempt_count": retry_count + 1,
                        "error": last_error,
                        "review_model": policy["entity_resolution"][
                            "semantic_review"
                        ]["problem_model"]["model"],
                        "prompt_version": policy["entity_resolution"][
                            "semantic_review"
                        ]["problem_prompt_version"],
                        "resolution_policy_version": policy[
                            "policy_version"
                        ],
                    }
                )
                continue
            completed_at = datetime.now(timezone.utc).isoformat()
            checkpoint_record = {
                "problem_review_task_id": task_id,
                "problem_assignment_id": task["problem_assignment_id"],
                "resolution_case_id": task["resolution_case_id"],
                "review_model": decision["review_model"],
                "prompt_version": decision["prompt_version"],
                "resolution_policy_version": policy["policy_version"],
                "response_id": response_metadata.get("response_id", ""),
                "usage": response_metadata.get("usage", {}),
                "completed_at": completed_at,
                "decision": decision,
            }
            checkpoint_file.write(
                dumps(checkpoint_record, ensure_ascii=False) + "\n"
            )
            checkpoint_file.flush()
            checkpoint_records[task_id] = checkpoint_record
            succeeded_count += 1
            print(
                "problem review 진행: "
                f"{len(checkpoint_records)}/{len(selected_tasks)}"
            )

    decisions = [
        checkpoint_records[task["problem_review_task_id"]]["decision"]
        for task in selected_tasks
        if task["problem_review_task_id"] in checkpoint_records
    ]
    failure_columns = [
        "problem_review_task_id",
        "problem_assignment_id",
        "resolution_case_id",
        "attempt_count",
        "error",
        "review_model",
        "prompt_version",
        "resolution_policy_version",
    ]
    return {
        "decisions": decisions,
        "failures": pd.DataFrame(
            failure_rows,
            columns=failure_columns,
        ),
        "selected_task_count": len(selected_tasks),
        "reused_checkpoint_count": len(decisions) - succeeded_count,
        "attempted_count": attempted_count,
        "succeeded_count": succeeded_count,
        "failed_count": len(failure_rows),
    }


def write_problem_execution_outputs(
    execution_result: dict[str, object],
    tasks_path: str,
    prompt_path: str,
    schema_path: str,
    output_dir: str,
    checkpoint_path: str,
    policy: dict,
) -> dict[str, str]:
    """problem decision·실패·실행 manifest를 저장한다."""
    semantic_policy = policy["entity_resolution"]["semantic_review"]
    executor_policy = semantic_policy["problem_executor"]
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    decision_path = output_directory / executor_policy["decision_file"]
    failure_path = output_directory / executor_policy["failure_file"]
    manifest_path = output_directory / executor_policy["run_manifest_file"]
    write_jsonl(execution_result["decisions"], str(decision_path))
    execution_result["failures"].to_csv(
        failure_path,
        index=False,
        encoding="utf-8-sig",
    )
    manifest = {
        "review_model": semantic_policy["problem_model"]["model"],
        "reasoning_effort": semantic_policy["problem_model"][
            "reasoning_effort"
        ],
        "prompt_version": semantic_policy["problem_prompt_version"],
        "resolution_policy_version": policy["policy_version"],
        "tasks_path": str(Path(tasks_path).resolve()),
        "tasks_sha256": calculate_file_sha256(tasks_path),
        "prompt_path": str(Path(prompt_path).resolve()),
        "prompt_sha256": calculate_file_sha256(prompt_path),
        "schema_path": str(Path(schema_path).resolve()),
        "schema_sha256": calculate_file_sha256(schema_path),
        "checkpoint_path": str(Path(checkpoint_path).resolve()),
        "selected_task_count": execution_result["selected_task_count"],
        "reused_checkpoint_count": execution_result[
            "reused_checkpoint_count"
        ],
        "attempted_count": execution_result["attempted_count"],
        "succeeded_count": execution_result["succeeded_count"],
        "failed_count": execution_result["failed_count"],
        "decision_count": len(execution_result["decisions"]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(
        dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "decisions": str(decision_path),
        "failures": str(failure_path),
        "manifest": str(manifest_path),
        "checkpoint": str(checkpoint_path),
    }


def create_problem_openai_client(policy: dict) -> object:
    """problem executor timeout을 적용한 OpenAI client를 만든다."""
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    executor_policy = policy["entity_resolution"]["semantic_review"][
        "problem_executor"
    ]
    return OpenAI(
        timeout=float(executor_policy["timeout_seconds"]),
        max_retries=0,
    )


if __name__ == "__main__":
    neo4j_root = Path(__file__).resolve().parent.parent
    parser = ArgumentParser(
        description="Entity Resolution problem review task를 LLM으로 판정"
    )
    parser.add_argument("tasks", help="problem review task JSONL 경로")
    parser.add_argument("output_dir", help="decision·checkpoint 출력 폴더")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retries", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prompt",
        default=str(
            neo4j_root
            / "config"
            / "prompts"
            / "problem_resolution_review.md"
        ),
    )
    parser.add_argument(
        "--schema",
        default=str(
            neo4j_root
            / "config"
            / "schemas"
            / "problem_resolution_decision.schema.json"
        ),
    )
    parser.add_argument(
        "--policy",
        default=str(neo4j_root / "config" / "resolution_policy.json"),
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    problem_tasks = load_jsonl(cli_args.tasks)
    executor_policy = pipeline_policy["entity_resolution"][
        "semantic_review"
    ]["problem_executor"]
    checkpoint_file = (
        Path(cli_args.output_dir) / executor_policy["checkpoint_file"]
    )
    plan = build_problem_execution_plan(
        problem_tasks,
        str(checkpoint_file),
        pipeline_policy,
        cli_args.limit,
    )
    if cli_args.dry_run:
        print(dumps(plan, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    openai_client = None
    if plan["pending_task_count"]:
        openai_client = create_problem_openai_client(pipeline_policy)
    execution = execute_problem_review_tasks(
        problem_tasks,
        load_text_file(cli_args.prompt),
        load_json_schema(cli_args.schema),
        str(checkpoint_file),
        pipeline_policy,
        openai_client,
        limit=cli_args.limit,
        maximum_retries=cli_args.retries,
    )
    written_paths = write_problem_execution_outputs(
        execution,
        cli_args.tasks,
        cli_args.prompt,
        cli_args.schema,
        cli_args.output_dir,
        str(checkpoint_file),
        pipeline_policy,
    )
    print(dumps(written_paths, ensure_ascii=False, indent=2))
    if execution["failed_count"]:
        raise SystemExit(1)

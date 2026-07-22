from argparse import ArgumentParser
from datetime import datetime, timezone
from json import dumps, load, loads
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import load_pipeline_policy
from goldset.build_gold_set import calculate_file_sha256
from entity_resolution.semantic_review import (
    collect_classified_sources,
    load_jsonl,
    validate_decision_shape,
    write_jsonl,
)


def load_text_file(input_path: str) -> str:
    """UTF-8 prompt 파일을 비어 있지 않은 문자열로 읽는다."""
    text = Path(input_path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"prompt 파일이 비어 있습니다: {input_path}")
    return text


def load_json_schema(input_path: str) -> dict:
    """Structured Output용 JSON Schema를 읽는다."""
    with open(input_path, "r", encoding="utf-8") as input_file:
        schema = load(input_file)
    if not isinstance(schema, dict):
        raise ValueError("term decision schema는 JSON 객체여야 합니다.")
    return schema


def validate_structured_output_schema(
    schema: dict,
    policy: dict,
) -> list[str]:
    """Responses API strict 출력에서 금지된 schema 키워드의 위치를 찾는다."""
    schema_policy = policy["entity_resolution"]["semantic_review"][
        "structured_output_schema"
    ]
    unsupported_keywords = set(schema_policy["unsupported_keywords"])
    errors: list[str] = []

    def visit_schema(value, path: str) -> None:
        if isinstance(value, dict):
            for key, child_value in value.items():
                child_path = f"{path}.{key}"
                if key in unsupported_keywords:
                    errors.append(child_path)
                visit_schema(child_value, child_path)
        elif isinstance(value, list):
            for item_index, child_value in enumerate(value):
                visit_schema(child_value, f"{path}[{item_index}]")

    visit_schema(schema, "$")
    return errors


def apply_controlled_decision_fields(
    decision: dict,
    task: dict,
    policy: dict,
) -> dict:
    """LLM이 정할 수 없는 식별자·상태·버전 필드를 코드 값으로 고정한다."""
    semantic_policy = policy["entity_resolution"]["semantic_review"]
    controlled = dict(decision)
    controlled["term_review_task_id"] = task["term_review_task_id"]
    controlled["resolution_case_id"] = task["resolution_case_id"]
    controlled["decision_status"] = semantic_policy["decision_status_input"]
    controlled["review_model"] = semantic_policy["term_model"]["model"]
    controlled["prompt_version"] = semantic_policy["prompt_version"]
    return controlled


def validate_executor_decision(
    decision: dict,
    task: dict,
    policy: dict,
) -> list[str]:
    """체크포인트 저장 전에 schema와 후보 완전 분류를 검사한다."""
    errors = validate_decision_shape(decision)
    if errors:
        return errors
    classified, duplicate_ids = collect_classified_sources(decision)
    expected_ids = {
        candidate["source_candidate_id"]
        for candidate in task["source_candidates"]
    }
    classified_ids = set(classified)
    unknown_ids = classified_ids.difference(expected_ids)
    missing_ids = expected_ids.difference(classified_ids)
    if duplicate_ids:
        errors.append(
            "DUPLICATE_CANDIDATE_CLASSIFICATION: "
            + dumps(sorted(set(duplicate_ids)), ensure_ascii=False)
        )
    if unknown_ids:
        errors.append(
            "UNKNOWN_SOURCE_CANDIDATE: "
            + dumps(sorted(unknown_ids), ensure_ascii=False)
        )
    if missing_ids:
        errors.append(
            "MISSING_CANDIDATE_CLASSIFICATION: "
            + dumps(sorted(missing_ids), ensure_ascii=False)
        )
    allowed_entity_types = set(
        policy["entity_resolution"]["entity_type_mapping"].values()
    )
    for alternative in decision["proposed_alternatives"]:
        entity_type = alternative["entity_type"]
        if entity_type not in allowed_entity_types:
            errors.append(f"INVALID_ENTITY_TYPE: {entity_type}")
    return errors


def request_term_decision(
    client,
    task: dict,
    prompt: str,
    schema: dict,
    policy: dict,
) -> tuple[dict, dict]:
    """OpenAI Responses API의 strict JSON Schema 출력으로 한 task를 판정한다."""
    semantic_policy = policy["entity_resolution"]["semantic_review"]
    model_policy = semantic_policy["term_model"]
    executor_policy = semantic_policy["term_executor"]
    request_arguments: dict[str, object] = {
        "model": model_policy["model"],
        "instructions": prompt,
        "input": dumps(task, ensure_ascii=False),
        "max_output_tokens": int(executor_policy["maximum_output_tokens"]),
        "reasoning": {"effort": model_policy["reasoning_effort"]},
        "store": bool(executor_policy["store_response"]),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "term_resolution_decision",
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
        raise ValueError("LLM 응답 본문이 비어 있습니다.")
    parsed = loads(raw_output)
    if not isinstance(parsed, dict):
        raise ValueError("LLM term decision은 JSON 객체여야 합니다.")
    decision = apply_controlled_decision_fields(parsed, task, policy)
    validation_errors = validate_executor_decision(decision, task, policy)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))
    usage: dict = {}
    if getattr(response, "usage", None) is not None:
        usage = response.usage.model_dump()
    response_metadata = {
        "response_id": str(getattr(response, "id", "")),
        "usage": usage,
    }
    return decision, response_metadata


def load_compatible_checkpoint(
    checkpoint_path: str,
    tasks_by_id: dict[str, dict],
    policy: dict,
) -> dict[str, dict]:
    """현재 모델·prompt·정책과 같은 성공 checkpoint만 재사용한다."""
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
                raise ValueError(f"checkpoint {line_number}행은 객체여야 합니다.")
            task_id = str(record.get("term_review_task_id") or "")
            task = tasks_by_id.get(task_id)
            if task is None:
                continue
            same_model = (
                record.get("review_model")
                == semantic_policy["term_model"]["model"]
            )
            same_prompt = (
                record.get("prompt_version")
                == semantic_policy["prompt_version"]
            )
            same_policy = (
                record.get("resolution_policy_version")
                == policy["policy_version"]
            )
            if not same_model or not same_prompt or not same_policy:
                continue
            decision = record.get("decision")
            if not isinstance(decision, dict):
                continue
            validation_errors = validate_executor_decision(
                decision,
                task,
                policy,
            )
            if validation_errors:
                continue
            compatible[task_id] = record
    return compatible


def select_execution_tasks(tasks: list[dict], limit: int) -> list[dict]:
    """0이면 전체, 양수면 입력 순서의 앞쪽 task만 선택한다."""
    if limit < 0:
        raise ValueError("term review 실행 limit은 0 이상이어야 합니다.")
    selected = tasks
    if limit > 0:
        selected = tasks[:limit]
    return selected


def build_execution_plan(
    tasks: list[dict],
    checkpoint_path: str,
    policy: dict,
    limit: int,
) -> dict[str, object]:
    """API 호출 없이 선택·재사용·미처리 task 수를 계산한다."""
    selected_tasks = select_execution_tasks(tasks, limit)
    tasks_by_id = {
        task["term_review_task_id"]: task for task in selected_tasks
    }
    checkpoint_records = load_compatible_checkpoint(
        checkpoint_path,
        tasks_by_id,
        policy,
    )
    pending_tasks = [
        task
        for task in selected_tasks
        if task["term_review_task_id"] not in checkpoint_records
    ]
    return {
        "selected_task_count": len(selected_tasks),
        "reused_checkpoint_count": len(checkpoint_records),
        "pending_task_count": len(pending_tasks),
        "selected_input_character_count": sum(
            len(dumps(task, ensure_ascii=False)) for task in selected_tasks
        ),
    }


def execute_term_review_tasks(
    tasks: list[dict],
    prompt: str,
    schema: dict,
    checkpoint_path: str,
    policy: dict,
    client,
    limit: int = 0,
    maximum_retries: int | None = None,
    requester=request_term_decision,
) -> dict[str, object]:
    """성공 응답을 즉시 checkpoint에 기록하며 term task를 순차 실행한다."""
    schema_errors = validate_structured_output_schema(schema, policy)
    if schema_errors:
        raise ValueError(
            "OpenAI strict Structured Outputs에서 지원하지 않는 JSON Schema "
            "키워드가 있습니다: "
            + ", ".join(schema_errors)
        )
    selected_tasks = select_execution_tasks(tasks, limit)
    tasks_by_id = {
        task["term_review_task_id"]: task for task in selected_tasks
    }
    checkpoint_records = load_compatible_checkpoint(
        checkpoint_path,
        tasks_by_id,
        policy,
    )
    executor_policy = policy["entity_resolution"]["semantic_review"][
        "term_executor"
    ]
    retry_count = int(executor_policy["maximum_retries"])
    if maximum_retries is not None:
        retry_count = maximum_retries
    if retry_count < 0:
        raise ValueError("term review 재시도 횟수는 0 이상이어야 합니다.")
    checkpoint = Path(checkpoint_path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    failure_rows: list[dict] = []
    attempted_count = 0
    succeeded_count = 0
    with checkpoint.open("a", encoding="utf-8") as checkpoint_file:
        for task in selected_tasks:
            task_id = task["term_review_task_id"]
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
                        f"term task 실패 {task_id} "
                        f"({attempt}/{retry_count + 1}): {last_error}"
                    )
            if decision is None:
                failure_rows.append(
                    {
                        "term_review_task_id": task_id,
                        "resolution_case_id": task["resolution_case_id"],
                        "attempt_count": retry_count + 1,
                        "error": last_error,
                        "review_model": policy["entity_resolution"][
                            "semantic_review"
                        ]["term_model"]["model"],
                        "prompt_version": policy["entity_resolution"][
                            "semantic_review"
                        ]["prompt_version"],
                        "resolution_policy_version": policy["policy_version"],
                    }
                )
                continue
            completed_at = datetime.now(timezone.utc).isoformat()
            checkpoint_record = {
                "term_review_task_id": task_id,
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
                "term review 진행: "
                f"{len(checkpoint_records)}/{len(selected_tasks)}"
            )
    decisions = [
        checkpoint_records[task["term_review_task_id"]]["decision"]
        for task in selected_tasks
        if task["term_review_task_id"] in checkpoint_records
    ]
    failure_columns = [
        "term_review_task_id",
        "resolution_case_id",
        "attempt_count",
        "error",
        "review_model",
        "prompt_version",
        "resolution_policy_version",
    ]
    return {
        "decisions": decisions,
        "failures": pd.DataFrame(failure_rows, columns=failure_columns),
        "selected_task_count": len(selected_tasks),
        "reused_checkpoint_count": len(decisions) - succeeded_count,
        "attempted_count": attempted_count,
        "succeeded_count": succeeded_count,
        "failed_count": len(failure_rows),
    }


def write_execution_outputs(
    execution_result: dict[str, object],
    tasks_path: str,
    prompt_path: str,
    schema_path: str,
    output_dir: str,
    checkpoint_path: str,
    policy: dict,
) -> dict[str, str]:
    """결정·실패·run manifest를 정책의 파일명으로 저장한다."""
    semantic_policy = policy["entity_resolution"]["semantic_review"]
    executor_policy = semantic_policy["term_executor"]
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
        "review_model": semantic_policy["term_model"]["model"],
        "reasoning_effort": semantic_policy["term_model"][
            "reasoning_effort"
        ],
        "prompt_version": semantic_policy["prompt_version"],
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


def create_openai_client(policy: dict):
    """정책 timeout을 적용하고 SDK 내부 재시도는 끈 OpenAI client를 만든다."""
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    executor_policy = policy["entity_resolution"]["semantic_review"][
        "term_executor"
    ]
    return OpenAI(
        timeout=float(executor_policy["timeout_seconds"]),
        max_retries=0,
    )


if __name__ == "__main__":
    neo4j_root = Path(__file__).resolve().parent.parent
    parser = ArgumentParser(
        description="Entity Resolution term review task를 LLM으로 판정"
    )
    parser.add_argument("tasks", help="term review task JSONL 경로")
    parser.add_argument("output_dir", help="decision·checkpoint 출력 폴더")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="처리 task 수, 0이면 전체",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=None,
        help="task별 재시도 횟수",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API를 호출하지 않고 task·checkpoint 수만 확인",
    )
    parser.add_argument(
        "--prompt",
        default=str(
            neo4j_root / "config" / "prompts" / "term_resolution_review.md"
        ),
        help="term review prompt 경로",
    )
    parser.add_argument(
        "--schema",
        default=str(
            neo4j_root
            / "config"
            / "schemas"
            / "term_resolution_decision.schema.json"
        ),
        help="term decision JSON Schema 경로",
    )
    parser.add_argument(
        "--policy",
        default=str(neo4j_root / "config" / "resolution_policy.json"),
        help="Entity Resolution 정책 JSON 경로",
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    executor_policy = pipeline_policy["entity_resolution"]["semantic_review"][
        "term_executor"
    ]
    output_directory = Path(cli_args.output_dir)
    checkpoint_file = output_directory / executor_policy["checkpoint_file"]
    task_records = load_jsonl(cli_args.tasks)
    decision_schema = load_json_schema(cli_args.schema)
    schema_errors = validate_structured_output_schema(
        decision_schema,
        pipeline_policy,
    )
    if schema_errors:
        raise ValueError(
            "OpenAI strict Structured Outputs에서 지원하지 않는 JSON Schema "
            "키워드가 있습니다: "
            + ", ".join(schema_errors)
        )
    if cli_args.dry_run:
        plan = build_execution_plan(
            task_records,
            str(checkpoint_file),
            pipeline_policy,
            cli_args.limit,
        )
        print(dumps(plan, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    review_prompt = load_text_file(cli_args.prompt)
    openai_client = create_openai_client(pipeline_policy)
    result = execute_term_review_tasks(
        task_records,
        review_prompt,
        decision_schema,
        str(checkpoint_file),
        pipeline_policy,
        openai_client,
        limit=cli_args.limit,
        maximum_retries=cli_args.retries,
    )
    written = write_execution_outputs(
        result,
        cli_args.tasks,
        cli_args.prompt,
        cli_args.schema,
        cli_args.output_dir,
        str(checkpoint_file),
        pipeline_policy,
    )
    print(dumps(written, ensure_ascii=False, indent=2))
    if result["failed_count"]:
        raise SystemExit(1)

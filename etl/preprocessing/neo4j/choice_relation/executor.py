from __future__ import annotations

from datetime import datetime, timezone
from json import dumps, load, loads
from pathlib import Path
from typing import Callable

import pandas as pd

from choice_relation.analysis import (
    apply_controlled_fields,
    validate_choice_relation_decision,
)


def load_text_file(input_path: str) -> str:
    """UTF-8 prompt 파일을 읽는다."""
    text = Path(input_path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"prompt 파일이 비어 있습니다: {input_path}")
    return text


def load_json_schema(input_path: str) -> dict:
    """Structured Output용 JSON Schema를 읽는다."""
    with Path(input_path).open("r", encoding="utf-8") as schema_file:
        schema = load(schema_file)
    if not isinstance(schema, dict):
        raise ValueError("choice relation schema는 JSON 객체여야 합니다.")
    return schema


def load_jsonl(input_path: str) -> list[dict]:
    """JSONL 객체를 입력 순서대로 읽는다."""
    path = Path(input_path)
    if not path.is_file():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            record = loads(stripped)
            if not isinstance(record, dict):
                raise ValueError(
                    f"{path} {line_number}행은 JSON 객체여야 합니다."
                )
            records.append(record)
    return records


def write_jsonl(records: list[dict], output_path: str) -> str:
    """JSON 객체 목록을 UTF-8 JSONL로 저장한다."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(dumps(record, ensure_ascii=False) + "\n")
    return str(path)


def request_choice_relation_decision(
    client: object,
    task: dict,
    prompt: str,
    schema: dict,
    policy: dict,
) -> tuple[dict, dict]:
    """OpenAI Responses API로 한 문항의 정답–오답 관계를 분석한다."""
    model_policy = policy["generator_model"]
    executor_policy = policy["executor"]
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
                "name": "choice_relation_decision",
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
        raise ValueError("LLM choice relation 응답이 비어 있습니다.")
    parsed = loads(raw_output)
    if not isinstance(parsed, dict):
        raise ValueError("LLM choice relation 결과는 JSON 객체여야 합니다.")

    decision = apply_controlled_fields(parsed, task, policy)
    validation_errors = validate_choice_relation_decision(
        decision,
        task,
        policy,
    )
    if validation_errors:
        messages = [
            f"{error['error_code']}: {error['message']}"
            for error in validation_errors
        ]
        raise ValueError("; ".join(messages))

    usage: dict = {}
    if getattr(response, "usage", None) is not None:
        usage = response.usage.model_dump()
    return decision, {
        "response_id": str(getattr(response, "id", "")),
        "usage": usage,
    }


def select_execution_tasks(
    tasks: list[dict],
    limit: int,
) -> list[dict]:
    """0이면 전체, 양수면 입력 순서의 앞쪽 task만 선택한다."""
    if limit < 0:
        raise ValueError("choice relation 실행 limit은 0 이상이어야 합니다.")
    selected_tasks = tasks
    if limit > 0:
        selected_tasks = tasks[:limit]
    return selected_tasks


def load_compatible_checkpoint(
    checkpoint_path: str,
    tasks_by_id: dict[str, dict],
    policy: dict,
) -> dict[str, dict]:
    """현재 모델·prompt·정책과 같은 성공 checkpoint만 재사용한다."""
    compatible: dict[str, dict] = {}
    for record in load_jsonl(checkpoint_path):
        task_id = str(record.get("choice_relation_task_id") or "")
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        if record.get("review_model") != policy["generator_model"]["model"]:
            continue
        if record.get("prompt_version") != policy["prompt_version"]:
            continue
        if record.get("analysis_policy_version") != policy["policy_version"]:
            continue
        decision = record.get("decision")
        if not isinstance(decision, dict):
            continue
        if validate_choice_relation_decision(decision, task, policy):
            continue
        compatible[task_id] = record
    return compatible


def build_execution_plan(
    tasks: list[dict],
    checkpoint_path: str,
    policy: dict,
    limit: int,
) -> dict[str, int]:
    """API 호출 없이 선택·재사용·미처리 task 수를 계산한다."""
    selected_tasks = select_execution_tasks(tasks, limit)
    tasks_by_id = {
        task["choice_relation_task_id"]: task for task in selected_tasks
    }
    checkpoint_records = load_compatible_checkpoint(
        checkpoint_path,
        tasks_by_id,
        policy,
    )
    pending_tasks = [
        task
        for task in selected_tasks
        if task["choice_relation_task_id"] not in checkpoint_records
    ]
    return {
        "selected_task_count": len(selected_tasks),
        "reused_checkpoint_count": len(checkpoint_records),
        "pending_task_count": len(pending_tasks),
        "selected_input_character_count": sum(
            len(dumps(task, ensure_ascii=False)) for task in selected_tasks
        ),
    }


def load_checkpoint_decisions(
    tasks: list[dict],
    checkpoint_path: str,
    policy: dict,
    limit: int = 0,
) -> list[dict]:
    """현재 생성 설정과 호환되는 완료 결과를 task 순서대로 불러온다."""
    selected_tasks = select_execution_tasks(tasks, limit)
    tasks_by_id = {
        task["choice_relation_task_id"]: task for task in selected_tasks
    }
    checkpoint_records = load_compatible_checkpoint(
        checkpoint_path,
        tasks_by_id,
        policy,
    )
    return [
        checkpoint_records[task["choice_relation_task_id"]]["decision"]
        for task in selected_tasks
        if task["choice_relation_task_id"] in checkpoint_records
    ]


def execute_choice_relation_tasks(
    tasks: list[dict],
    prompt: str,
    schema: dict,
    checkpoint_path: str,
    policy: dict,
    client: object,
    limit: int = 0,
    requester: Callable[
        [object, dict, str, dict, dict],
        tuple[dict, dict],
    ] = request_choice_relation_decision,
) -> dict[str, object]:
    """미처리 task만 실행하고 성공 결과를 즉시 checkpoint에 기록한다."""
    selected_tasks = select_execution_tasks(tasks, limit)
    tasks_by_id = {
        task["choice_relation_task_id"]: task for task in selected_tasks
    }
    checkpoint_records = load_compatible_checkpoint(
        checkpoint_path,
        tasks_by_id,
        policy,
    )
    initial_checkpoint_count = len(checkpoint_records)
    retry_count = int(policy["executor"]["maximum_retries"])
    if retry_count < 0:
        raise ValueError("choice relation 재시도 횟수는 0 이상이어야 합니다.")

    checkpoint = Path(checkpoint_path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    failure_rows: list[dict] = []
    attempted_count = 0
    succeeded_count = 0
    with checkpoint.open("a", encoding="utf-8") as checkpoint_file:
        for task in selected_tasks:
            task_id = task["choice_relation_task_id"]
            if task_id in checkpoint_records:
                continue
            attempted_count += 1
            decision: dict | None = None
            response_metadata: dict = {}
            last_error = ""
            attempt_count = 0
            for attempt in range(1, retry_count + 2):
                attempt_count = attempt
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
                        f"choice relation task 실패 {task_id} "
                        f"({attempt}/{retry_count + 1}): {last_error}"
                    )
                    non_retryable_markers = policy["executor"][
                        "non_retryable_error_markers"
                    ]
                    if any(
                        marker in last_error
                        for marker in non_retryable_markers
                    ):
                        break
            if decision is None:
                failure_rows.append(
                    {
                        "choice_relation_task_id": task_id,
                        "problem_id": task["problem_id"],
                        "attempt_count": attempt_count,
                        "error": last_error,
                    }
                )
                continue

            checkpoint_record = {
                "choice_relation_task_id": task_id,
                "problem_id": task["problem_id"],
                "review_model": policy["generator_model"]["model"],
                "prompt_version": policy["prompt_version"],
                "analysis_policy_version": policy["policy_version"],
                "response_id": response_metadata.get("response_id", ""),
                "usage": response_metadata.get("usage", {}),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "decision": decision,
            }
            checkpoint_file.write(
                dumps(checkpoint_record, ensure_ascii=False) + "\n"
            )
            checkpoint_file.flush()
            checkpoint_records[task_id] = checkpoint_record
            succeeded_count += 1

    ordered_decisions = [
        checkpoint_records[task["choice_relation_task_id"]]["decision"]
        for task in selected_tasks
        if task["choice_relation_task_id"] in checkpoint_records
    ]
    return {
        "selected_task_count": len(selected_tasks),
        "reused_checkpoint_count": initial_checkpoint_count,
        "attempted_task_count": attempted_count,
        "succeeded_task_count": succeeded_count,
        "failed_task_count": len(failure_rows),
        "completed_task_count": len(ordered_decisions),
        "pending_task_count": len(selected_tasks) - len(ordered_decisions),
        "decisions": ordered_decisions,
        "failures": pd.DataFrame(failure_rows),
    }


def create_openai_client(policy: dict) -> object:
    """프로젝트 환경변수와 정책 timeout으로 OpenAI client를 만든다."""
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    return OpenAI(
        timeout=float(policy["executor"]["timeout_seconds"]),
        max_retries=0,
    )

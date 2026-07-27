from pathlib import Path

from entity_resolution.execute_term_review import (
    execute_term_review_tasks,
    load_json_schema,
    load_text_file,
    write_execution_outputs,
)


def execute_review_batch(
    tasks: list[dict],
    tasks_path: Path,
    output_directory: Path,
    prompt_path: Path,
    schema_path: Path,
    policy: dict,
    client: object | None,
    limit: int,
    maximum_retries: int | None,
) -> tuple[dict[str, object], dict[str, str]]:
    """한 종류의 term review task를 checkpoint 재사용 방식으로 실행한다."""
    executor_policy = policy["entity_resolution"]["semantic_review"][
        "term_executor"
    ]
    checkpoint_path = output_directory / executor_policy["checkpoint_file"]
    execution_result = execute_term_review_tasks(
        tasks,
        load_text_file(str(prompt_path)),
        load_json_schema(str(schema_path)),
        str(checkpoint_path),
        policy,
        client,
        limit=limit,
        maximum_retries=maximum_retries,
    )
    written_paths = write_execution_outputs(
        execution_result,
        str(tasks_path),
        str(prompt_path),
        str(schema_path),
        str(output_directory),
        str(checkpoint_path),
        policy,
    )
    if execution_result["failed_count"]:
        raise RuntimeError(
            "term review 실행 실패가 있습니다: "
            f"{execution_result['failed_count']}건, "
            f"{written_paths['failures']}"
        )
    return execution_result, written_paths

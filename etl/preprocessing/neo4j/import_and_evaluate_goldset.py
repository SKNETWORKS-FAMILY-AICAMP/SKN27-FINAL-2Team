from argparse import ArgumentParser
from datetime import datetime, timezone
from json import dumps
from pathlib import Path

from common import load_pipeline_policy
from entity_resolution.evaluate_term_review import (
    evaluate_term_decisions,
    write_evaluation_outputs,
)
from entity_resolution.execute_term_review import (
    build_execution_plan,
    create_openai_client,
    load_json_schema,
    validate_structured_output_schema,
)
from entity_resolution.import_gold_set import (
    import_gold_annotations,
    load_annotation_records,
    write_gold_import_outputs,
)
from entity_resolution.review_execution import execute_review_batch
from entity_resolution.role_conflict_review import (
    build_role_conflict_review_table,
    write_role_conflict_review_table,
)
from entity_resolution.semantic_review import (
    build_validation_tables_from_review_tasks,
    load_jsonl,
    validate_term_decisions,
)


def resolve_goldset_evaluation_paths(
    neo4j_root: Path,
    policy: dict,
    annotation_directory: str = "",
    gold_task_file: str = "",
) -> dict[str, Path]:
    """골든셋 import·평가 단계에서 사용하는 절대 경로를 반환한다."""
    workflow_policy = policy["entity_resolution"]["semantic_review"][
        "gold_set"
    ]["workflow"]
    path_names = [
        "annotation_directory",
        "gold_task_file",
        "validation_directory",
        "model_prediction_directory",
        "evaluation_directory",
        "role_conflict_manual_review",
        "term_review_prompt",
        "term_review_schema",
    ]
    paths = {
        name: (neo4j_root / workflow_policy[name]).resolve()
        for name in path_names
    }
    if annotation_directory:
        paths["annotation_directory"] = Path(annotation_directory).resolve()
    if gold_task_file:
        paths["gold_task_file"] = Path(gold_task_file).resolve()
    paths["manifest"] = (
        paths["evaluation_directory"] / workflow_policy["manifest_file"]
    )
    return paths


def run_goldset_evaluation(
    neo4j_root: str,
    policy_path: str,
    annotation_directory: str = "",
    gold_task_file: str = "",
    review_limit: int = 0,
    maximum_retries: int | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Human Review import, 골든셋 LLM 판정, 평가까지만 실행한다."""
    root = Path(neo4j_root).resolve()
    policy = load_pipeline_policy(policy_path)
    paths = resolve_goldset_evaluation_paths(
        root,
        policy,
        annotation_directory=annotation_directory,
        gold_task_file=gold_task_file,
    )
    case_annotations, candidate_annotations = load_annotation_records(
        str(paths["annotation_directory"]),
        policy,
    )
    gold_tasks = load_jsonl(str(paths["gold_task_file"]))
    import_outputs = import_gold_annotations(
        case_annotations,
        candidate_annotations,
        gold_tasks,
        policy,
    )
    validation_errors = import_outputs["validation_errors"]
    executor_policy = policy["entity_resolution"]["semantic_review"][
        "term_executor"
    ]
    checkpoint_path = (
        paths["model_prediction_directory"]
        / executor_policy["checkpoint_file"]
    )
    execution_plan = build_execution_plan(
        gold_tasks,
        str(checkpoint_path),
        policy,
        review_limit,
    )
    schema_errors = validate_structured_output_schema(
        load_json_schema(str(paths["term_review_schema"])),
        policy,
    )
    if dry_run:
        status = "READY"
        if not validation_errors.empty:
            status = "BLOCKED_BY_GOLD_VALIDATION"
        if schema_errors:
            status = "BLOCKED_BY_SCHEMA_VALIDATION"
        validation_error_counts: dict[str, int] = {}
        validation_error_examples: list[dict] = []
        if not validation_errors.empty:
            validation_error_counts = {
                str(error_code): int(count)
                for error_code, count in validation_errors[
                    "error_code"
                ].value_counts().items()
            }
            validation_error_examples = validation_errors[
                ["gold_case_id", "error_code", "message"]
            ].head(10).to_dict("records")
        return {
            "status": status,
            "dry_run": True,
            "gold_validation_error_count": len(validation_errors),
            "gold_validation_error_counts": validation_error_counts,
            "gold_validation_error_examples": validation_error_examples,
            "structured_output_schema_errors": schema_errors,
            "related_entity_seed_task_count": len(
                import_outputs["related_entity_tasks"]
            ),
            "model_execution_plan": execution_plan,
            "paths": {name: str(path) for name, path in paths.items()},
        }

    import_paths = write_gold_import_outputs(
        import_outputs,
        str(paths["annotation_directory"]),
        str(paths["gold_task_file"]),
        str(paths["validation_directory"]),
        policy,
    )
    if not validation_errors.empty:
        raise ValueError(
            "골든셋 검증 오류를 먼저 수정해야 합니다: "
            f"{len(validation_errors)}건, "
            f"{import_paths['validation_errors']}"
        )
    if schema_errors:
        raise ValueError(
            "term review schema 오류를 먼저 수정해야 합니다: "
            + ", ".join(schema_errors)
        )

    client = None
    if execution_plan["pending_task_count"]:
        client = create_openai_client(policy)
    execution, model_paths = execute_review_batch(
        gold_tasks,
        paths["gold_task_file"],
        paths["model_prediction_directory"],
        paths["term_review_prompt"],
        paths["term_review_schema"],
        policy,
        client,
        review_limit,
        maximum_retries,
    )
    gate_input_tables = build_validation_tables_from_review_tasks(gold_tasks)
    verified_decision_tables = validate_term_decisions(
        execution["decisions"],
        gold_tasks,
        gate_input_tables,
        policy,
    )
    evaluation_outputs = evaluate_term_decisions(
        import_outputs["gold_decisions"],
        execution["decisions"],
        gold_tasks,
        import_outputs["gold_case_outcomes"],
        policy,
        verified_decision_tables=verified_decision_tables,
    )
    evaluation_paths = write_evaluation_outputs(
        evaluation_outputs,
        str(paths["evaluation_directory"]),
        policy,
    )
    role_conflict_review = build_role_conflict_review_table(
        import_outputs["gold_decisions"],
        execution["decisions"],
        gold_tasks,
        str(paths["role_conflict_manual_review"]),
        policy,
    )
    evaluation_paths["role_conflict_manual_review"] = (
        write_role_conflict_review_table(
            role_conflict_review,
            str(paths["role_conflict_manual_review"]),
        )
    )
    manifest = {
        "status": "COMPLETED",
        "stage": "GOLDSET_EVALUATION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resolution_policy_version": policy["policy_version"],
        "gold_case_count": len(import_outputs["gold_decisions"]),
        "gold_model_decision_count": len(execution["decisions"]),
        "related_entity_seed_task_count": len(
            import_outputs["related_entity_tasks"]
        ),
        "evaluation_metrics": evaluation_outputs["metrics"],
        "outputs": {
            "gold_import": import_paths,
            "gold_model": model_paths,
            "gold_evaluation": evaluation_paths,
        },
    }
    paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
    paths["manifest"].write_text(
        dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    neo4j_directory = Path(__file__).resolve().parent
    parser = ArgumentParser(
        description="Human Review import와 골든셋 LLM 평가만 실행"
    )
    parser.add_argument("--annotations", default="")
    parser.add_argument("--gold-tasks", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--policy",
        default=str(
            neo4j_directory / "config" / "resolution_policy.json"
        ),
    )
    cli_args = parser.parse_args()
    result = run_goldset_evaluation(
        neo4j_root=str(neo4j_directory),
        policy_path=cli_args.policy,
        annotation_directory=cli_args.annotations,
        gold_task_file=cli_args.gold_tasks,
        review_limit=cli_args.limit,
        maximum_retries=cli_args.retries,
        dry_run=cli_args.dry_run,
    )
    print(dumps(result, ensure_ascii=False, indent=2))
    if result["status"] not in {"READY", "COMPLETED"}:
        raise SystemExit(1)

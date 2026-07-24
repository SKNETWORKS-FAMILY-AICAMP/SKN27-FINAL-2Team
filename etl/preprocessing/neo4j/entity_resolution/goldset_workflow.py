from datetime import datetime, timezone
from json import dumps
from pathlib import Path

import pandas as pd

from common import load_pipeline_policy
from entity_resolution.evaluate_term_review import (
    evaluate_term_decisions,
    write_evaluation_outputs,
)
from entity_resolution.execute_term_review import (
    build_execution_plan,
    create_openai_client,
    execute_term_review_tasks,
    load_json_schema,
    load_text_file,
    validate_structured_output_schema,
    write_execution_outputs,
)
from entity_resolution.import_gold_set import (
    import_gold_annotations,
    load_annotation_records,
    write_gold_import_outputs,
)
from entity_resolution.finalize_entity_resolution import (
    finalize_entity_resolution,
    load_existing_registry,
    write_final_resolution_tables,
)
from entity_resolution.manual_term_review import (
    build_manual_review_table,
    prepare_manual_decisions,
    write_manual_review_table,
    write_manual_validation_errors,
)
from entity_resolution.semantic_review import (
    build_validation_tables_from_review_tasks,
    load_jsonl,
    load_resolution_package,
    validate_term_decisions,
    write_term_decision_tables,
)
from entity_resolution.related_entity_resolution import (
    select_seed_backed_alternatives,
)
from entity_resolution.role_conflict_review import (
    build_role_conflict_review_table,
    write_role_conflict_review_table,
)
from run_related_entity_resolution import (
    resolve_related_entity_paths,
    run_related_entity_resolution,
)


def resolve_goldset_workflow_paths(
    neo4j_root: Path,
    policy: dict,
    annotation_directory: str = "",
    gold_task_file: str = "",
) -> dict[str, Path]:
    """정책의 상대 경로를 골든셋 일괄 실행에 사용할 절대 경로로 바꾼다."""
    workflow_policy = policy["entity_resolution"]["semantic_review"][
        "gold_set"
    ]["workflow"]
    paths = {
        name: (neo4j_root / configured_path).resolve()
        for name, configured_path in workflow_policy.items()
        if name != "manifest_file"
    }
    paths["manifest"] = (
        paths["validation_directory"]
        / workflow_policy["manifest_file"]
    )
    if annotation_directory:
        paths["annotation_directory"] = Path(
            annotation_directory
        ).resolve()
    if gold_task_file:
        paths["gold_task_file"] = Path(gold_task_file).resolve()
    related_policy = policy["entity_resolution"][
        "related_entity_resolution"
    ]
    manual_review_policy = related_policy["manual_review"]
    final_identity_policy = related_policy["final_identity"]
    paths["related_output_directory"] = (
        neo4j_root / related_policy["default_output_directory"]
    ).resolve()
    paths["related_manual_review"] = (
        neo4j_root / manual_review_policy["input_file"]
    ).resolve()
    paths["related_manual_review_errors"] = (
        paths["related_model_prediction_directory"]
        / manual_review_policy["validation_error_file"]
    ).resolve()
    paths["related_final_identity_directory"] = (
        neo4j_root / final_identity_policy["default_output_directory"]
    ).resolve()
    paths["related_final_selection"] = (
        paths["related_final_identity_directory"]
        / final_identity_policy["selection_file"]
    ).resolve()
    return paths


def execute_review_batch(
    tasks: list[dict],
    tasks_path: Path,
    output_directory: Path,
    prompt_path: Path,
    schema_path: Path,
    policy: dict,
    client,
    limit: int,
    maximum_retries: int | None,
) -> tuple[dict[str, object], dict[str, str]]:
    """한 종류의 term review task를 checkpoint 재사용 방식으로 끝까지 실행한다."""
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


def run_goldset_workflow(
    neo4j_root: str,
    thesaurus_csv_path: str,
    encyclopedia_jsonl_path: str,
    itkc_people_csv_path: str,
    itkc_events_csv_path: str,
    policy_path: str,
    annotation_directory: str = "",
    gold_task_file: str = "",
    gold_review_limit: int = 0,
    related_review_limit: int = 0,
    maximum_retries: int | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """사람 골든셋 import부터 관련 엔티티 2차 판정까지 순서대로 실행한다."""
    root = Path(neo4j_root).resolve()
    policy = load_pipeline_policy(policy_path)
    paths = resolve_goldset_workflow_paths(
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
    related_seed_tasks = import_outputs["related_entity_tasks"]
    executor_policy = policy["entity_resolution"]["semantic_review"][
        "term_executor"
    ]
    gold_checkpoint = (
        paths["model_prediction_directory"]
        / executor_policy["checkpoint_file"]
    )
    gold_execution_plan = build_execution_plan(
        gold_tasks,
        str(gold_checkpoint),
        policy,
        gold_review_limit,
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
            "related_entity_seed_task_count": len(related_seed_tasks),
            "gold_model_execution_plan": gold_execution_plan,
            "paths": {name: str(path) for name, path in paths.items()},
        }

    gold_import_paths = write_gold_import_outputs(
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
            f"{gold_import_paths['validation_errors']}"
        )

    client = create_openai_client(policy)
    gold_execution, gold_model_paths = execute_review_batch(
        gold_tasks,
        paths["gold_task_file"],
        paths["model_prediction_directory"],
        paths["term_review_prompt"],
        paths["term_review_schema"],
        policy,
        client,
        gold_review_limit,
        maximum_retries,
    )
    gold_gate_input_tables = build_validation_tables_from_review_tasks(
        gold_tasks
    )
    gold_verified_decision_tables = validate_term_decisions(
        gold_execution["decisions"],
        gold_tasks,
        gold_gate_input_tables,
        policy,
    )
    evaluation_outputs = evaluate_term_decisions(
        import_outputs["gold_decisions"],
        gold_execution["decisions"],
        gold_tasks,
        import_outputs["gold_case_outcomes"],
        policy,
        verified_decision_tables=gold_verified_decision_tables,
    )
    evaluation_paths = write_evaluation_outputs(
        evaluation_outputs,
        str(paths["evaluation_directory"]),
        policy,
    )
    role_conflict_review = build_role_conflict_review_table(
        import_outputs["gold_decisions"],
        gold_execution["decisions"],
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

    related_manifest: dict[str, object] = {
        "related_entity_count": 0,
        "review_task_count": 0,
        "output_files": {},
    }
    related_model_paths: dict[str, str] = {}
    related_gate_paths: dict[str, str] = {}
    related_manual_review_paths: dict[str, str] = {}
    related_final_identity_paths: dict[str, str] = {}
    related_verification_counts: dict[str, int] = {}
    related_manual_applied_count = 0
    related_finalization_counts = {
        "selected_alternative_count": 0,
        "canonical_entity_count": 0,
        "acceptance_review_count": 0,
    }
    if related_seed_tasks:
        related_manifest = run_related_entity_resolution(
            queue_path=gold_import_paths["related_entity_tasks"],
            output_dir=str(paths["related_output_directory"]),
            thesaurus_csv_path=thesaurus_csv_path,
            encyclopedia_jsonl_path=encyclopedia_jsonl_path,
            itkc_people_csv_path=itkc_people_csv_path,
            itkc_events_csv_path=itkc_events_csv_path,
            policy_path=policy_path,
        )
        related_paths = resolve_related_entity_paths(
            gold_import_paths["related_entity_tasks"],
            str(paths["related_output_directory"]),
            policy,
        )
        related_review_tasks = load_jsonl(
            str(related_paths["term_review_tasks"])
        )
        related_execution, related_model_paths = execute_review_batch(
            related_review_tasks,
            related_paths["term_review_tasks"],
            paths["related_model_prediction_directory"],
            paths["term_review_prompt"],
            paths["term_review_schema"],
            policy,
            client,
            related_review_limit,
            maximum_retries,
        )
        related_resolution_tables = load_resolution_package(
            str(related_paths["resolution_package"]),
            policy,
        )
        automatic_decision_tables = validate_term_decisions(
            related_execution["decisions"],
            related_review_tasks,
            related_resolution_tables,
            policy,
        )
        manual_review_table = build_manual_review_table(
            related_execution["decisions"],
            related_review_tasks,
            automatic_decision_tables,
            str(paths["related_manual_review"]),
            policy,
        )
        write_manual_review_table(
            manual_review_table,
            str(paths["related_manual_review"]),
        )
        manual_result = prepare_manual_decisions(
            manual_review_table,
            related_execution["decisions"],
            related_review_tasks,
            automatic_decision_tables,
            policy,
        )
        related_decision_tables = validate_term_decisions(
            manual_result["decisions"],
            related_review_tasks,
            related_resolution_tables,
            policy,
            manual_verifications=manual_result["manual_verifications"],
        )
        manual_errors = manual_result["validation_errors"]
        if not manual_errors.empty:
            related_decision_tables[
                "term_decision_validation_errors"
            ] = pd.concat(
                [
                    related_decision_tables[
                        "term_decision_validation_errors"
                    ],
                    manual_errors,
                ],
                ignore_index=True,
            )
        write_manual_review_table(
            manual_result["manual_review_table"],
            str(paths["related_manual_review"]),
        )
        manual_error_path = write_manual_validation_errors(
            manual_errors,
            str(paths["related_manual_review_errors"]),
        )
        related_manual_review_paths = {
            "input": str(paths["related_manual_review"]),
            "validation_errors": manual_error_path,
        }
        related_manual_applied_count = len(
            manual_result["applied_case_ids"]
        )
        related_gate_paths = write_term_decision_tables(
            related_decision_tables,
            str(paths["related_model_prediction_directory"]),
            policy,
        )
        verification_series = related_decision_tables[
            "term_resolution_decisions"
        ]["verification_status"].value_counts()
        related_verification_counts = {
            str(status): int(count)
            for status, count in verification_series.items()
        }
        related_selections = select_seed_backed_alternatives(
            related_resolution_tables,
            related_decision_tables,
            policy,
        )
        paths["related_final_selection"].parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        related_selections.to_csv(
            paths["related_final_selection"],
            index=False,
            encoding="utf-8-sig",
        )
        verified_selections = related_selections.loc[
            related_selections["selection_status"] == "VERIFIED"
        ]
        preselected_alternative_methods = {
            str(row["canonical_alternative_id"]): str(
                row["selection_method"]
            )
            for row in verified_selections.to_dict("records")
        }
        empty_problem_assignments = pd.DataFrame(
            columns=[
                "problem_assignment_id",
                "problem_id",
                "resolution_case_id",
                "selected_canonical_alternative_ids_json",
                "selection_mode",
                "resolution_method",
                "verification_status",
            ]
        )
        registry_file = policy["entity_resolution"][
            "canonical_registry"
        ]["output_files"]["canonical_registry"]
        existing_registry = load_existing_registry(
            str(
                paths["related_final_identity_directory"]
                / registry_file
            )
        )
        final_identity_tables = finalize_entity_resolution(
            related_resolution_tables,
            related_decision_tables,
            empty_problem_assignments,
            existing_registry,
            policy,
            preselected_alternative_methods=(
                preselected_alternative_methods
            ),
            manually_approved_alternative_ids=set(
                preselected_alternative_methods
            ),
        )
        related_final_identity_paths = write_final_resolution_tables(
            final_identity_tables,
            str(paths["related_final_identity_directory"]),
            policy,
        )
        related_final_identity_paths["related_entity_selections"] = str(
            paths["related_final_selection"]
        )
        related_finalization_counts = {
            "selected_alternative_count": len(verified_selections),
            "canonical_entity_count": len(
                final_identity_tables["canonical_registry"]
            ),
            "acceptance_review_count": len(
                final_identity_tables[
                    "canonical_acceptance_review_queue"
                ]
            ),
        }

    manifest = {
        "status": "COMPLETED",
        "dry_run": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resolution_policy_version": policy["policy_version"],
        "gold_case_count": len(import_outputs["gold_decisions"]),
        "gold_validation_error_count": 0,
        "gold_model_decision_count": len(gold_execution["decisions"]),
        "gold_evaluation_metrics": evaluation_outputs["metrics"],
        "related_entity_seed_task_count": len(related_seed_tasks),
        "related_entity_review_task_count": related_manifest[
            "review_task_count"
        ],
        "related_manual_review_applied_count": (
            related_manual_applied_count
        ),
        "related_verification_counts": related_verification_counts,
        "related_finalization_counts": related_finalization_counts,
        "outputs": {
            "gold_import": gold_import_paths,
            "gold_model": gold_model_paths,
            "gold_evaluation": evaluation_paths,
            "related_entity_resolution": related_manifest["output_files"],
            "related_entity_model": related_model_paths,
            "related_entity_manual_review": related_manual_review_paths,
            "related_entity_gate": related_gate_paths,
            "related_entity_final_identity": (
                related_final_identity_paths
            ),
        },
    }
    paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
    paths["manifest"].write_text(
        dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest

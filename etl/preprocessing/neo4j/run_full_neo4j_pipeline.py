from argparse import ArgumentParser
from datetime import datetime, timezone
from json import dumps, load
from pathlib import Path

import pandas as pd

from common import load_pipeline_policy
from entity_resolution.deterministic_triage import (
    select_budgeted_tasks,
    triage_term_tasks,
    write_triage_outputs,
)
from entity_resolution.execute_problem_review import (
    build_problem_execution_plan,
    create_problem_openai_client,
    execute_problem_review_tasks,
    write_problem_execution_outputs,
)
from entity_resolution.execute_term_review import (
    build_execution_plan,
    create_openai_client,
    load_json_schema,
    load_text_file,
)
from entity_resolution.finalize_entity_resolution import (
    finalize_entity_resolution,
    load_existing_registry,
    write_final_resolution_tables,
)
from entity_resolution.load_final_identity import (
    build_final_identity_load_plan,
    load_final_identity_tables,
    load_final_identity_to_neo4j,
)
from entity_resolution.problem_review import (
    build_problem_review_inputs,
    resolve_problem_tasks_by_context,
    validate_problem_decisions,
    write_problem_decision_tables,
)
from entity_resolution.review_execution import execute_review_batch
from entity_resolution.semantic_review import (
    build_term_review_tasks,
    load_jsonl,
    load_resolution_package,
    validate_term_decisions,
    write_jsonl,
    write_term_decision_tables,
)
from run_neo4j_preprocessing import (
    resolve_pipeline_paths,
    resolve_stage_output_paths,
    run_preprocessing_pipeline,
)


def resolve_full_pipeline_paths(
    neo4j_root: Path,
    output_dir: str,
    policy: dict,
) -> dict[str, Path]:
    """통합 runner의 각 단계가 공유하는 입력·출력 경로를 결정한다."""
    output_directory = Path(output_dir).resolve()
    stage_paths = resolve_stage_output_paths(output_dir, policy)
    semantic_policy = policy["entity_resolution"]["semantic_review"]
    term_executor_policy = semantic_policy["term_executor"]
    problem_executor_policy = semantic_policy["problem_executor"]
    problem_output_files = semantic_policy["problem_decision_output_files"]
    final_output_files = policy["entity_resolution"]["canonical_registry"][
        "output_files"
    ]
    full_pipeline_policy = policy["full_pipeline"]
    gold_workflow = semantic_policy["gold_set"]["workflow"]
    review_directory = stage_paths["llm_review_directory"]
    final_directory = stage_paths["final_identity_directory"]
    return {
        **stage_paths,
        "output_directory": output_directory,
        "term_prompt": (
            neo4j_root / gold_workflow["term_review_prompt"]
        ).resolve(),
        "term_schema": (
            neo4j_root / gold_workflow["term_review_schema"]
        ).resolve(),
        "term_checkpoint": review_directory
        / term_executor_policy["checkpoint_file"],
        "term_decisions": review_directory
        / term_executor_policy["decision_file"],
        "problem_tasks": review_directory
        / semantic_policy["problem_task_file"],
        "problem_prompt": (
            neo4j_root / full_pipeline_policy["problem_review_prompt"]
        ).resolve(),
        "problem_schema": (
            neo4j_root / full_pipeline_policy["problem_review_schema"]
        ).resolve(),
        "problem_checkpoint": review_directory
        / problem_executor_policy["checkpoint_file"],
        "problem_decisions": review_directory
        / problem_executor_policy["decision_file"],
        "verified_problem_assignments": review_directory
        / problem_output_files["verified_problem_assignments"],
        "registry": final_directory
        / final_output_files["canonical_registry"],
        "goldset_manifest": (
            neo4j_root / full_pipeline_policy["goldset_manifest"]
        ).resolve(),
        "pipeline_manifest": output_directory
        / full_pipeline_policy["manifest_file"],
    }


def validate_goldset_safety_gate(
    manifest_path: Path,
    policy: dict,
    skip_gate: bool = False,
) -> dict[str, object]:
    """최신 골드셋 결과가 production 실행의 안전성 기준을 만족하는지 검사한다."""
    full_pipeline_policy = policy["full_pipeline"]
    if skip_gate or not bool(
        full_pipeline_policy["require_goldset_gate"]
    ):
        return {
            "status": "SKIPPED",
            "manifest_path": str(manifest_path),
            "errors": [],
        }
    if not manifest_path.is_file():
        return {
            "status": "BLOCKED",
            "manifest_path": str(manifest_path),
            "errors": ["골드셋 평가 manifest가 없습니다."],
        }
    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        manifest = load(manifest_file)
    metrics = manifest.get("evaluation_metrics", {})
    errors: list[str] = []
    if manifest.get("status") != "COMPLETED":
        errors.append("골드셋 평가가 COMPLETED 상태가 아닙니다.")
    if manifest.get("resolution_policy_version") != policy[
        "policy_version"
    ]:
        errors.append("골드셋 평가와 현재 resolution 정책 버전이 다릅니다.")

    precision = float(
        metrics.get("auto_accepted_identity_pair_precision", 0.0)
    )
    minimum_precision = float(
        full_pipeline_policy[
            "minimum_auto_accepted_identity_pair_precision"
        ]
    )
    if precision < minimum_precision:
        errors.append(
            "자동 승인 identity pair precision이 기준보다 낮습니다."
        )
    false_merge_count = int(
        metrics.get("verified_false_merge_pair_count", -1)
    )
    maximum_false_merges = int(
        full_pipeline_policy[
            "maximum_verified_false_merge_pair_count"
        ]
    )
    if false_merge_count < 0 or false_merge_count > maximum_false_merges:
        errors.append("검증 후 오병합 건수가 허용 기준을 넘었습니다.")
    status = "READY"
    if errors:
        status = "BLOCKED"
    return {
        "status": status,
        "manifest_path": str(manifest_path),
        "auto_accepted_identity_pair_precision": precision,
        "verified_false_merge_pair_count": false_merge_count,
        "errors": errors,
    }


def count_statuses(
    table: pd.DataFrame,
    column_name: str,
) -> dict[str, int]:
    """검증 테이블의 상태별 건수를 JSON 직렬화 가능한 dict로 만든다."""
    if column_name not in table.columns:
        return {}
    return {
        str(status): int(count)
        for status, count in table[column_name].value_counts().items()
    }


def build_full_pipeline_dry_run(
    neo4j_root: Path,
    pipeline_inputs: dict[str, str],
    paths: dict[str, Path],
    policy: dict,
    term_limit: int,
    problem_limit: int,
    skip_goldset_gate: bool,
) -> dict[str, object]:
    """파일을 생성하거나 API·DB를 호출하지 않고 현재 실행 계획을 반환한다."""
    if load_neo4j and rebuild_registry:
        raise ValueError(
            "registry 재생성과 Neo4j 적재는 같은 실행에서 허용하지 않습니다."
        )

    goldset_gate = validate_goldset_safety_gate(
        paths["goldset_manifest"],
        policy,
        skip_gate=skip_goldset_gate,
    )
    required_inputs = {
        name: path
        for name, path in pipeline_inputs.items()
        if name != "output_dir"
    }
    missing_inputs = [
        f"{name}: {path}"
        for name, path in required_inputs.items()
        if not path or not Path(path).is_file()
    ]
    term_plan: dict[str, object] = {
        "status": "WAITING_FOR_PREPROCESSING"
    }
    if paths["entity_resolution_directory"].is_dir():
        resolution_tables = load_resolution_package(
            str(paths["entity_resolution_directory"]),
            policy,
        )
        term_tasks = build_term_review_tasks(
            resolution_tables,
            policy,
        )
        triage_table, code_decisions, context_required_tasks = (
            triage_term_tasks(term_tasks, policy)
        )
        term_executor_policy = policy["entity_resolution"][
            "semantic_review"
        ]["term_executor"]
        selected_llm_tasks, effective_term_limit = select_budgeted_tasks(
            [],
            term_limit,
            term_executor_policy,
        )
        triage_dispositions = policy["entity_resolution"][
            "semantic_review"
        ]["deterministic_triage"]["dispositions"]
        term_plan = {
            "status": "READY",
            **build_execution_plan(
                selected_llm_tasks,
                str(paths["term_checkpoint"]),
                policy,
                0,
            ),
            "total_task_count": len(term_tasks),
            "deterministic_decision_count": len(code_decisions),
            "code_linkable_task_count": int(
                (
                    triage_table["disposition"]
                    == triage_dispositions["single_candidate"]
                ).sum()
            ),
            "context_required_task_count": len(context_required_tasks),
            "term_llm_candidate_task_count": 0,
            "term_only_task_count": int(
                (
                    triage_table["disposition"]
                    == triage_dispositions["term_only"]
                ).sum()
            ),
            "effective_llm_task_limit": effective_term_limit,
        }
    problem_plan: dict[str, object] = {
        "status": "WAITING_FOR_TERM_GATE"
    }
    if paths["problem_tasks"].is_file():
        problem_tasks = load_jsonl(str(paths["problem_tasks"]))
        problem_executor_policy = policy["entity_resolution"][
            "semantic_review"
        ]["problem_executor"]
        selected_problem_tasks, effective_problem_limit = (
            select_budgeted_tasks(
                problem_tasks,
                problem_limit,
                problem_executor_policy,
            )
        )
        problem_plan = {
            "status": "READY",
            **build_problem_execution_plan(
                selected_problem_tasks,
                str(paths["problem_checkpoint"]),
                policy,
                0,
            ),
            "llm_candidate_task_count": len(problem_tasks),
            "effective_llm_task_limit": effective_problem_limit,
        }
    load_plan: dict[str, object] = {
        "status": "WAITING_FOR_FINALIZATION"
    }
    try:
        final_tables = load_final_identity_tables(
            str(paths["final_identity_directory"]),
            policy,
        )
        load_plan = build_final_identity_load_plan(final_tables)
    except FileNotFoundError:
        pass

    status = "READY"
    if missing_inputs or goldset_gate["status"] == "BLOCKED":
        status = "BLOCKED"
    return {
        "status": status,
        "stage": "FULL_NEO4J_PIPELINE",
        "dry_run": True,
        "pipeline_version": policy["full_pipeline"]["pipeline_version"],
        "missing_inputs": missing_inputs,
        "goldset_gate": goldset_gate,
        "term_execution": term_plan,
        "problem_execution": problem_plan,
        "neo4j_load": load_plan,
        "paths": {name: str(path) for name, path in paths.items()},
    }


def run_full_neo4j_pipeline(
    neo4j_root: str,
    policy_path: str,
    pipeline_inputs: dict[str, str],
    batch_size: int = 20,
    exam_limit: int = 0,
    maximum_retries: int = 2,
    coverage_threshold: float = 90.0,
    display_limit: int = 20,
    term_limit: int = 0,
    problem_limit: int = 0,
    skip_preprocessing: bool = False,
    skip_goldset_gate: bool = False,
    load_neo4j: bool = False,
    neo4j_database: str = "",
    neo4j_batch_size: int | None = None,
    rebuild_registry: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """기출 용어 추출부터 검증된 identity의 Neo4j upsert까지 실행한다."""
    root = Path(neo4j_root).resolve()
    project_root = root.parents[2]
    policy = load_pipeline_policy(policy_path)
    paths = resolve_full_pipeline_paths(
        root,
        pipeline_inputs["output_dir"],
        policy,
    )
    if dry_run:
        return build_full_pipeline_dry_run(
            root,
            pipeline_inputs,
            paths,
            policy,
            term_limit,
            problem_limit,
            skip_goldset_gate,
        )
    if load_neo4j and any(
        limit_value > 0
        for limit_value in [exam_limit, term_limit, problem_limit]
    ):
        raise ValueError(
            "제한 실행 결과는 Neo4j에 적재할 수 없습니다. "
            "exam·term·problem limit을 모두 0으로 두세요."
        )

    goldset_gate = validate_goldset_safety_gate(
        paths["goldset_manifest"],
        policy,
        skip_gate=skip_goldset_gate,
    )
    if goldset_gate["status"] == "BLOCKED":
        raise ValueError(" ".join(goldset_gate["errors"]))

    stage_results: dict[str, object] = {
        "goldset_gate": goldset_gate,
    }
    if not skip_preprocessing:
        coverage_report = run_preprocessing_pipeline(
            exam_json_path=pipeline_inputs["exam_json_path"],
            thesaurus_csv_path=pipeline_inputs["thesaurus_csv_path"],
            output_dir=pipeline_inputs["output_dir"],
            encyclopedia_jsonl_path=pipeline_inputs[
                "encyclopedia_jsonl_path"
            ],
            itkc_people_csv_path=pipeline_inputs[
                "itkc_people_csv_path"
            ],
            itkc_events_csv_path=pipeline_inputs[
                "itkc_events_csv_path"
            ],
            batch_size=batch_size,
            limit=exam_limit,
            max_retries=maximum_retries,
            threshold=coverage_threshold,
            display_limit=display_limit,
            policy_path=policy_path,
        )
        stage_results["preprocessing"] = {
            "status": "COMPLETED",
            "coverage_percent": coverage_report.get(
                "coverage_percent",
                0.0,
            ),
            "meets_threshold": coverage_report.get(
                "meets_threshold",
                False,
            ),
        }
    elif skip_preprocessing:
        stage_results["preprocessing"] = {"status": "REUSED"}

    resolution_tables = load_resolution_package(
        str(paths["entity_resolution_directory"]),
        policy,
    )
    term_tasks = build_term_review_tasks(
        resolution_tables,
        policy,
    )
    write_jsonl(term_tasks, str(paths["term_review_tasks"]))
    triage_table, code_decisions, context_required_tasks = triage_term_tasks(
        term_tasks,
        policy,
    )
    triage_paths = write_triage_outputs(
        triage_table,
        context_required_tasks,
        str(paths["llm_review_directory"]),
        policy,
    )
    term_executor_policy = policy["entity_resolution"]["semantic_review"][
        "term_executor"
    ]
    selected_llm_tasks, effective_term_limit = select_budgeted_tasks(
        [],
        term_limit,
        term_executor_policy,
    )
    term_plan = build_execution_plan(
        selected_llm_tasks,
        str(paths["term_checkpoint"]),
        policy,
        0,
    )
    term_client = None
    if term_plan["pending_task_count"]:
        term_client = create_openai_client(policy)
    term_execution, term_model_paths = execute_review_batch(
        selected_llm_tasks,
        paths["term_review_tasks"],
        paths["llm_review_directory"],
        paths["term_prompt"],
        paths["term_schema"],
        policy,
        term_client,
        0,
        maximum_retries,
    )
    combined_term_decisions = (
        code_decisions + term_execution["decisions"]
    )
    term_decision_tables = validate_term_decisions(
        combined_term_decisions,
        term_tasks,
        resolution_tables,
        policy,
    )
    term_gate_paths = write_term_decision_tables(
        term_decision_tables,
        str(paths["llm_review_directory"]),
        policy,
    )
    stage_results["term_review"] = {
        "status": "COMPLETED",
        "execution_plan": {
            **term_plan,
            "total_task_count": len(term_tasks),
            "deterministic_decision_count": len(code_decisions),
            "code_linkable_task_count": int(
                (
                    triage_table["disposition"]
                    == policy["entity_resolution"]["semantic_review"][
                        "deterministic_triage"
                    ]["dispositions"]["single_candidate"]
                ).sum()
            ),
            "context_required_task_count": len(
                context_required_tasks
            ),
            "term_llm_candidate_task_count": 0,
            "term_only_task_count": int(
                (
                    triage_table["disposition"]
                    == policy["entity_resolution"]["semantic_review"][
                        "deterministic_triage"
                    ]["dispositions"]["term_only"]
                ).sum()
            ),
            "effective_llm_task_limit": effective_term_limit,
        },
        "triage_outputs": triage_paths,
        "verification_counts": count_statuses(
            term_decision_tables["term_resolution_decisions"],
            "verification_status",
        ),
        "model_outputs": term_model_paths,
        "gate_outputs": term_gate_paths,
    }

    problem_tasks, deterministic_assignments = build_problem_review_inputs(
        resolution_tables,
        term_decision_tables,
        policy,
    )
    initial_problem_task_count = len(problem_tasks)
    (
        problem_tasks,
        context_assignments,
        context_audit,
    ) = resolve_problem_tasks_by_context(problem_tasks, policy)
    context_resolved_status = policy["entity_resolution"][
        "semantic_review"
    ]["problem_context_rule"]["resolved_status"]
    code_context_resolved_count = int(
        (
            context_audit["resolution_status"]
            == context_resolved_status
        ).sum()
    )
    deterministic_assignments = pd.concat(
        [deterministic_assignments, context_assignments],
        ignore_index=True,
    )
    context_audit_path = (
        paths["llm_review_directory"]
        / policy["entity_resolution"]["semantic_review"][
            "problem_context_rule"
        ]["audit_file"]
    )
    context_audit.to_csv(
        context_audit_path,
        index=False,
        encoding="utf-8-sig",
    )
    write_jsonl(problem_tasks, str(paths["problem_tasks"]))
    problem_executor_policy = policy["entity_resolution"][
        "semantic_review"
    ]["problem_executor"]
    selected_problem_tasks, effective_problem_limit = select_budgeted_tasks(
        problem_tasks,
        problem_limit,
        problem_executor_policy,
    )
    problem_plan = build_problem_execution_plan(
        selected_problem_tasks,
        str(paths["problem_checkpoint"]),
        policy,
        0,
    )
    problem_client = None
    if problem_plan["pending_task_count"]:
        problem_client = create_problem_openai_client(policy)
    problem_execution = execute_problem_review_tasks(
        selected_problem_tasks,
        load_text_file(str(paths["problem_prompt"])),
        load_json_schema(str(paths["problem_schema"])),
        str(paths["problem_checkpoint"]),
        policy,
        problem_client,
        limit=0,
        maximum_retries=maximum_retries,
    )
    problem_model_paths = write_problem_execution_outputs(
        problem_execution,
        str(paths["problem_tasks"]),
        str(paths["problem_prompt"]),
        str(paths["problem_schema"]),
        str(paths["llm_review_directory"]),
        str(paths["problem_checkpoint"]),
        policy,
    )
    if problem_execution["failed_count"]:
        raise RuntimeError(
            "problem review 실행 실패가 있습니다: "
            f"{problem_execution['failed_count']}건"
        )
    problem_decision_tables = validate_problem_decisions(
        problem_execution["decisions"],
        problem_tasks,
        deterministic_assignments,
        policy,
    )
    problem_gate_paths = write_problem_decision_tables(
        problem_decision_tables,
        str(paths["llm_review_directory"]),
        policy,
    )
    stage_results["problem_review"] = {
        "status": "COMPLETED",
        "execution_plan": {
            **problem_plan,
            "initial_context_task_count": initial_problem_task_count,
            "code_context_resolved_count": code_context_resolved_count,
            "llm_candidate_task_count": len(problem_tasks),
            "effective_llm_task_limit": effective_problem_limit,
        },
        "context_audit": str(context_audit_path),
        "deterministic_assignment_count": len(
            deterministic_assignments
        ),
        "verification_counts": count_statuses(
            problem_decision_tables["problem_resolution_decisions"],
            "verification_status",
        ),
        "model_outputs": problem_model_paths,
        "gate_outputs": problem_gate_paths,
    }

    existing_registry_path = str(paths["registry"])
    if rebuild_registry:
        existing_registry_path = ""
    existing_registry = load_existing_registry(existing_registry_path)
    final_tables = finalize_entity_resolution(
        resolution_tables,
        term_decision_tables,
        problem_decision_tables["verified_problem_assignments"],
        existing_registry,
        policy,
        register_all_verified_candidates=True,
    )
    final_paths = write_final_resolution_tables(
        final_tables,
        str(paths["final_identity_directory"]),
        policy,
    )
    stage_results["finalization"] = {
        "status": "COMPLETED",
        "exam_term_count": len(final_tables["exam_term_nodes"]),
        "canonical_entity_count": len(
            final_tables["canonical_registry"]
        ),
        "single_source_review_count": len(
            final_tables["canonical_acceptance_review_queue"]
        ),
        "registry_mode": (
            "REBUILT" if rebuild_registry else "REUSED"
        ),
        "outputs": final_paths,
    }

    neo4j_load_result: dict[str, object] = {
        "status": "SKIPPED",
        "reason": "--load-neo4j 옵션이 지정되지 않았습니다.",
    }
    if load_neo4j:
        neo4j_load_result = load_final_identity_to_neo4j(
            str(paths["final_identity_directory"]),
            policy,
            str(project_root),
            database=neo4j_database,
            batch_size=neo4j_batch_size,
            dry_run=False,
        )
        if neo4j_load_result["status"] != "COMPLETED":
            raise ValueError(
                "Neo4j 적재 전 검증에 실패했습니다: "
                + ", ".join(
                    neo4j_load_result.get("validation_errors", [])
                )
            )
    stage_results["neo4j_load"] = neo4j_load_result

    manifest = {
        "status": "COMPLETED",
        "stage": "FULL_NEO4J_PIPELINE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": policy["full_pipeline"]["pipeline_version"],
        "resolution_policy_version": policy["policy_version"],
        "database_load_requested": load_neo4j,
        "stages": stage_results,
        "paths": {name: str(path) for name, path in paths.items()},
    }
    paths["pipeline_manifest"].parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    paths["pipeline_manifest"].write_text(
        dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    neo4j_directory = Path(__file__).resolve().parent
    parser = ArgumentParser(
        description=(
            "한국사 용어 추출부터 검증된 identity의 Neo4j 적재까지 실행"
        )
    )
    parser.add_argument("exam_json_path", nargs="?", default="")
    parser.add_argument("thesaurus_csv_path", nargs="?", default="")
    parser.add_argument("output_dir", nargs="?", default="")
    parser.add_argument("--encyclopedia-jsonl", default="")
    parser.add_argument("--itkc-people", default="")
    parser.add_argument("--itkc-events", default="")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--term-limit",
        type=int,
        default=0,
        help="term LLM 최대 호출 수. 0이면 정책의 안전 기본값(현재 0건)",
    )
    parser.add_argument(
        "--problem-limit",
        type=int,
        default=0,
        help="problem LLM 최대 호출 수. 0이면 정책의 안전 기본값(현재 0건)",
    )
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--threshold", type=float, default=90.0)
    parser.add_argument("--display-limit", type=int, default=20)
    parser.add_argument("--skip-preprocessing", action="store_true")
    parser.add_argument("--skip-goldset-gate", action="store_true")
    parser.add_argument("--load-neo4j", action="store_true")
    parser.add_argument("--neo4j-database", default="")
    parser.add_argument("--neo4j-batch-size", type=int, default=None)
    parser.add_argument(
        "--rebuild-registry",
        action="store_true",
        help="기존 파생 registry를 재사용하지 않고 새 유형 규칙으로 재생성",
    )
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument(
        "--execute",
        action="store_true",
        help="LLM·파일 생성 단계를 실제 실행",
    )
    execution_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="호환용 명시 옵션. 기본 동작도 dry-run",
    )
    parser.add_argument(
        "--policy",
        default=str(
            neo4j_directory / "config" / "resolution_policy.json"
        ),
    )
    cli_args = parser.parse_args()
    resolved_inputs = resolve_pipeline_paths(
        exam_json_path=cli_args.exam_json_path,
        thesaurus_csv_path=cli_args.thesaurus_csv_path,
        output_dir=cli_args.output_dir,
        encyclopedia_jsonl_path=cli_args.encyclopedia_jsonl,
        itkc_people_csv_path=cli_args.itkc_people,
        itkc_events_csv_path=cli_args.itkc_events,
    )
    result = run_full_neo4j_pipeline(
        neo4j_root=str(neo4j_directory),
        policy_path=cli_args.policy,
        pipeline_inputs=resolved_inputs,
        batch_size=cli_args.batch_size,
        exam_limit=cli_args.limit,
        maximum_retries=cli_args.retries,
        coverage_threshold=cli_args.threshold,
        display_limit=cli_args.display_limit,
        term_limit=cli_args.term_limit,
        problem_limit=cli_args.problem_limit,
        skip_preprocessing=cli_args.skip_preprocessing,
        skip_goldset_gate=cli_args.skip_goldset_gate,
        load_neo4j=cli_args.load_neo4j,
        neo4j_database=cli_args.neo4j_database,
        neo4j_batch_size=cli_args.neo4j_batch_size,
        rebuild_registry=cli_args.rebuild_registry,
        dry_run=not cli_args.execute,
    )
    print(dumps(result, ensure_ascii=False, indent=2))
    if result["status"] not in {"READY", "COMPLETED"}:
        raise SystemExit(1)

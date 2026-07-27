from __future__ import annotations

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from hashlib import new as new_hash
from json import dump
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from choice_relation.analysis import (
    build_choice_relation_tasks,
    load_choice_relation_policy,
    load_problem_records,
    validate_choice_relation_decisions,
)
from choice_relation.evaluation import (
    evaluate_relation_predictions,
    load_relation_goldset,
)
from choice_relation.evaluator import (
    apply_evaluator_relation_corrections,
    build_choice_relation_evaluation_tasks,
    build_evaluation_execution_plan,
    execute_choice_relation_evaluations,
    validate_choice_relation_evaluations,
)
from choice_relation.executor import (
    build_execution_plan,
    create_openai_client,
    execute_choice_relation_tasks,
    load_checkpoint_decisions,
    load_json_schema,
    load_jsonl,
    load_text_file,
    select_execution_tasks,
    write_jsonl,
)


def resolve_project_path(project_root: Path, path_value: str) -> Path:
    """상대 경로를 프로젝트 루트 기준의 절대 경로로 바꾼다."""
    path = Path(path_value)
    if path.is_absolute():
        return path
    return project_root / path


def calculate_file_digest(input_path: Path, policy: dict) -> str:
    """입력 파일의 내용 digest를 계산한다."""
    identifier_policy = policy["identifier"]
    hasher = new_hash(identifier_policy["hash_algorithm"])
    chunk_size = int(identifier_policy["chunk_size_bytes"])
    with input_path.open("rb") as input_file:
        chunk = input_file.read(chunk_size)
        while chunk:
            hasher.update(chunk)
            chunk = input_file.read(chunk_size)
    return hasher.hexdigest()


def write_json(data: dict, output_path: Path) -> None:
    """JSON 객체를 UTF-8 형식으로 저장한다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        dump(data, output_file, ensure_ascii=False, indent=2)


def write_dataframe(dataframe: pd.DataFrame, output_path: Path) -> None:
    """DataFrame을 UTF-8 BOM CSV로 저장한다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataframe.to_csv(output_path, index=False, encoding="utf-8-sig")


def parse_arguments() -> Namespace:
    """정답-오답 관계 분석 CLI 인자를 읽는다."""
    project_root = Path(__file__).resolve().parents[3]
    default_config = (
        project_root
        / "etl"
        / "preprocessing"
        / "neo4j"
        / "config"
        / "choice_relation.json"
    )
    parser = ArgumentParser(
        description="기출 정답-오답 관계의 생성과 평가를 분리해 실행합니다."
    )
    parser.add_argument("--config", default=str(default_config))
    parser.add_argument("--input", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="준비할 문항 수. 0이면 선택 조건을 만족하는 전체 문항",
    )
    parser.add_argument(
        "--problem-id",
        action="append",
        default=[],
        help="분석할 problem_id. 여러 문항은 옵션을 반복해서 지정",
    )
    parser.add_argument(
        "--decisions",
        default="",
        help="외부에서 생성한 choice relation 제안 JSONL",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="생성 단계와 평가 단계를 연속 실행",
    )
    parser.add_argument(
        "--execute-generator",
        action="store_true",
        help="Terra 생성 단계만 실행",
    )
    parser.add_argument(
        "--execute-evaluator",
        action="store_true",
        help="기존 생성 결과를 사용해 Sol 평가 단계만 실행",
    )
    parser.add_argument(
        "--execute-limit",
        type=int,
        default=0,
        help="실제 API 호출 대상으로 선택할 최대 task 수",
    )
    parser.add_argument(
        "--execute-all",
        action="store_true",
        help="준비된 task 전체를 실제 API로 호출",
    )
    return parser.parse_args()


def run_choice_relation_analysis(cli_args: Namespace) -> dict:
    """관계 제안 생성, 독립 평가, 최종 코드 게이트를 순서대로 처리한다."""
    project_root = Path(__file__).resolve().parents[3]
    policy = load_choice_relation_policy(cli_args.config)
    input_value = cli_args.input or policy["input"]["default_problem_json"]
    input_path = resolve_project_path(project_root, input_value)
    output_value = (
        cli_args.output_dir
        or policy["paths"]["default_output_directory"]
    )
    output_directory = resolve_project_path(project_root, output_value)
    output_directory.mkdir(parents=True, exist_ok=True)

    generate_requested = bool(
        getattr(cli_args, "execute", False)
        or getattr(cli_args, "execute_generator", False)
    )
    evaluate_requested = bool(
        getattr(cli_args, "execute", False)
        or getattr(cli_args, "execute_evaluator", False)
    )
    execution_limit = int(getattr(cli_args, "execute_limit", 0))
    execute_all = bool(getattr(cli_args, "execute_all", False))
    if execute_all:
        execution_limit = 0
    if (
        (generate_requested or evaluate_requested)
        and execution_limit == 0
        and not execute_all
    ):
        raise ValueError(
            "전체 API 호출을 방지했습니다. --execute-limit에 양수를 주거나 "
            "--execute-all을 함께 사용하세요."
        )

    problem_records = load_problem_records(str(input_path))
    selected_problem_ids: set[str] | None = None
    if cli_args.problem_id:
        selected_problem_ids = {
            str(problem_id).strip()
            for problem_id in cli_args.problem_id
            if str(problem_id).strip()
        }
    preparation = build_choice_relation_tasks(
        problem_records,
        policy,
        limit=cli_args.limit,
        selected_problem_ids=selected_problem_ids,
    )
    tasks = preparation["tasks"]
    path_keys = [
        "tasks",
        "source_choices",
        "preparation_exclusions",
        "input_integrity_issues",
        "checkpoint",
        "decisions",
        "decision_summary",
        "choice_claims",
        "distractor_relations",
        "validation_errors",
        "evaluator_checkpoint",
        "evaluator_decisions",
        "evaluator_summary",
        "evaluator_validation_errors",
        "evaluator_failures",
        "final_decision_summary",
        "final_choice_claims",
        "final_distractor_relations",
        "evaluation_comparison",
        "evaluation_metrics",
        "execution_failures",
        "manifest",
    ]
    paths = {
        key: output_directory / policy["paths"][key]
        for key in path_keys
    }
    write_jsonl(tasks, str(paths["tasks"]))
    write_dataframe(preparation["source_choices"], paths["source_choices"])
    write_dataframe(
        preparation["exclusions"],
        paths["preparation_exclusions"],
    )
    write_dataframe(
        preparation["input_integrity_issues"],
        paths["input_integrity_issues"],
    )

    generator_plan = build_execution_plan(
        tasks,
        str(paths["checkpoint"]),
        policy,
        execution_limit,
    )
    generator_execution: dict[str, object] = {
        "mode": "DRY_RUN",
        **generator_plan,
    }
    decisions: list[dict] = []
    validation_tasks = tasks
    api_client: object | None = None

    if cli_args.decisions:
        decisions_path = resolve_project_path(
            project_root,
            cli_args.decisions,
        )
        decisions = load_jsonl(str(decisions_path))
        decision_task_ids = {
            str(decision.get("choice_relation_task_id") or "")
            for decision in decisions
        }
        validation_tasks = [
            task
            for task in tasks
            if task["choice_relation_task_id"] in decision_task_ids
        ]
        generator_execution = {
            "mode": "EXTERNAL_DECISIONS",
            "decision_count": len(decisions),
            "matched_task_count": len(validation_tasks),
        }
    elif generate_requested:
        prompt_path = resolve_project_path(
            project_root,
            policy["paths"]["prompt"],
        )
        schema_path = resolve_project_path(
            project_root,
            policy["paths"]["schema"],
        )
        if int(generator_plan["pending_task_count"]) > 0:
            api_client = create_openai_client(policy)
        generator_client = api_client
        if generator_client is None:
            generator_client = object()
        generator_result = execute_choice_relation_tasks(
            tasks,
            load_text_file(str(prompt_path)),
            load_json_schema(str(schema_path)),
            str(paths["checkpoint"]),
            policy,
            generator_client,
            limit=execution_limit,
        )
        decisions = generator_result["decisions"]
        validation_tasks = select_execution_tasks(tasks, execution_limit)
        write_dataframe(
            generator_result["failures"],
            paths["execution_failures"],
        )
        generator_execution = {
            key: value
            for key, value in generator_result.items()
            if key not in {"decisions", "failures"}
        }
        generator_execution["mode"] = "EXECUTED"
    elif evaluate_requested:
        decisions = load_checkpoint_decisions(
            tasks,
            str(paths["checkpoint"]),
            policy,
            limit=execution_limit,
        )
        validation_tasks = select_execution_tasks(tasks, execution_limit)
        generator_execution = {
            "mode": "CHECKPOINT_REUSE",
            **generator_plan,
            "completed_task_count": len(decisions),
        }

    validated: dict[str, pd.DataFrame] | None = None
    generator_validation_summary: dict[str, int] = {}
    if decisions:
        write_jsonl(decisions, str(paths["decisions"]))
        validated = validate_choice_relation_decisions(
            decisions,
            validation_tasks,
            policy,
        )
        write_dataframe(validated["decisions"], paths["decision_summary"])
        write_dataframe(validated["choice_claims"], paths["choice_claims"])
        write_dataframe(
            validated["distractor_relations"],
            paths["distractor_relations"],
        )
        write_dataframe(
            validated["validation_errors"],
            paths["validation_errors"],
        )
        verification_counts: dict[str, int] = {}
        if not validated["decisions"].empty:
            verification_counts = {
                str(status): int(count)
                for status, count in validated["decisions"][
                    "verification_status"
                ].value_counts().items()
            }
        generator_validation_summary = {
            "decision_count": len(validated["decisions"]),
            "choice_claim_count": len(validated["choice_claims"]),
            "distractor_relation_count": len(
                validated["distractor_relations"]
            ),
            "validation_error_count": len(
                validated["validation_errors"]
            ),
            **{
                f"{status.lower()}_decision_count": count
                for status, count in verification_counts.items()
            },
        }

    eligible_proposals: list[dict] = []
    evaluation_tasks: list[dict] = []
    if validated is not None and not validated["decisions"].empty:
        invalid_status = policy["validation"]["invalid_status"]
        eligible_task_ids = set(
            validated["decisions"].loc[
                validated["decisions"]["verification_status"]
                != invalid_status,
                "choice_relation_task_id",
            ]
        )
        eligible_proposals = [
            decision
            for decision in decisions
            if decision["choice_relation_task_id"] in eligible_task_ids
        ]
        evaluation_tasks = build_choice_relation_evaluation_tasks(
            validation_tasks,
            eligible_proposals,
            policy,
        )

    evaluator_plan = build_evaluation_execution_plan(
        evaluation_tasks,
        str(paths["evaluator_checkpoint"]),
        policy,
    )
    evaluator_execution: dict[str, object] = {
        "mode": "NOT_REQUESTED",
        **evaluator_plan,
    }
    evaluations: list[dict] = []
    if eligible_proposals and not evaluate_requested:
        evaluator_execution["mode"] = "DRY_RUN"
    elif evaluate_requested and evaluation_tasks:
        if (
            api_client is None
            and int(evaluator_plan["pending_task_count"]) > 0
        ):
            api_client = create_openai_client(policy)
        evaluator_client = api_client
        if evaluator_client is None:
            evaluator_client = object()
        evaluator_prompt_path = resolve_project_path(
            project_root,
            policy["paths"]["evaluator_prompt"],
        )
        evaluator_schema_path = resolve_project_path(
            project_root,
            policy["paths"]["evaluator_schema"],
        )
        evaluator_result = execute_choice_relation_evaluations(
            evaluation_tasks,
            load_text_file(str(evaluator_prompt_path)),
            load_json_schema(str(evaluator_schema_path)),
            str(paths["evaluator_checkpoint"]),
            policy,
            evaluator_client,
        )
        evaluations = evaluator_result["evaluations"]
        write_dataframe(
            evaluator_result["failures"],
            paths["evaluator_failures"],
        )
        evaluator_execution = {
            key: value
            for key, value in evaluator_result.items()
            if key not in {"evaluations", "failures"}
        }
        evaluator_execution["mode"] = "EXECUTED"
    elif evaluate_requested:
        evaluator_execution["mode"] = "NO_ELIGIBLE_PROPOSALS"

    evaluator_validation_summary: dict[str, int] = {}
    final_summary: dict[str, int] = {}
    final_status_by_task_id: dict[str, str] = {}
    final_decisions = pd.DataFrame()
    final_claims = pd.DataFrame()
    final_relations = pd.DataFrame()
    if evaluations:
        write_jsonl(evaluations, str(paths["evaluator_decisions"]))
        evaluated = validate_choice_relation_evaluations(
            evaluations,
            evaluation_tasks,
            policy,
        )
        write_dataframe(evaluated["summary"], paths["evaluator_summary"])
        write_dataframe(
            evaluated["validation_errors"],
            paths["evaluator_validation_errors"],
        )
        final_status_by_task_id = evaluated["final_status_by_task_id"]
        final_status_counts: dict[str, int] = {}
        if not evaluated["summary"].empty:
            final_status_counts = {
                str(status): int(count)
                for status, count in evaluated["summary"][
                    "final_verification_status"
                ].value_counts().items()
            }
        evaluator_validation_summary = {
            "evaluation_count": len(evaluated["summary"]),
            "validation_error_count": len(
                evaluated["validation_errors"]
            ),
            **{
                f"{status.lower()}_count": count
                for status, count in final_status_counts.items()
            },
        }

    if validated is not None:
        final_decisions = validated["decisions"].copy()
        final_decisions["generator_verification_status"] = (
            final_decisions["verification_status"]
        )
        pending_status = policy["evaluator"]["pending_status"]
        final_decisions["final_verification_status"] = (
            final_decisions["choice_relation_task_id"]
            .map(final_status_by_task_id)
            .fillna(pending_status)
        )
        final_verified_status = policy["evaluator"][
            "final_verified_status"
        ]
        auto_corrected_status = policy["evaluator"][
            "auto_corrected_status"
        ]
        final_verified_task_ids = set(
            final_decisions.loc[
                final_decisions["final_verification_status"]
                == final_verified_status,
                "choice_relation_task_id",
            ]
        )
        auto_corrected_task_ids = set(
            final_decisions.loc[
                final_decisions["final_verification_status"]
                == auto_corrected_status,
                "choice_relation_task_id",
            ]
        )
        accepted_task_ids = (
            final_verified_task_ids | auto_corrected_task_ids
        )
        final_claims = validated["choice_claims"].copy()
        if not final_claims.empty:
            final_claims = final_claims.loc[
                final_claims["choice_relation_task_id"].isin(
                    accepted_task_ids
                )
            ].copy()
        final_relations = apply_evaluator_relation_corrections(
            validated["distractor_relations"],
            evaluations,
            accepted_task_ids,
            policy,
        )
        write_dataframe(
            final_decisions,
            paths["final_decision_summary"],
        )
        write_dataframe(final_claims, paths["final_choice_claims"])
        write_dataframe(
            final_relations,
            paths["final_distractor_relations"],
        )
        final_summary = {
            "evaluated_decision_count": len(final_status_by_task_id),
            "final_accepted_decision_count": len(accepted_task_ids),
            "final_verified_decision_count": len(final_verified_task_ids),
            "auto_corrected_decision_count": len(
                auto_corrected_task_ids
            ),
            "final_choice_claim_count": len(final_claims),
            "final_distractor_relation_count": len(final_relations),
        }

    goldset_evaluation: dict[str, object] = {}
    if not final_relations.empty:
        goldset_path = resolve_project_path(
            project_root,
            policy["goldset"]["path"],
        )
        goldset = load_relation_goldset(str(goldset_path), policy)
        evaluation = evaluate_relation_predictions(
            final_relations,
            goldset,
            policy,
        )
        write_dataframe(
            evaluation["comparison"],
            paths["evaluation_comparison"],
        )
        write_json(evaluation["metrics"], paths["evaluation_metrics"])
        goldset_evaluation = evaluation["metrics"]

    stage_status = "PREPARED"
    if generate_requested and not evaluate_requested:
        stage_status = "GENERATED"
        if int(generator_execution.get("failed_task_count", 0)) > 0:
            stage_status = "PARTIAL"
        if int(generator_execution.get("completed_task_count", 0)) == 0:
            stage_status = "FAILED"
    elif cli_args.decisions and not evaluate_requested:
        stage_status = "GENERATED"
    elif evaluate_requested:
        stage_status = "COMPLETED"
        completed_evaluations = int(
            evaluator_execution.get("completed_task_count", 0)
        )
        if evaluator_execution["mode"] == "NO_ELIGIBLE_PROPOSALS":
            stage_status = "FAILED"
        elif completed_evaluations == 0:
            stage_status = "FAILED"
        elif int(evaluator_execution.get("failed_task_count", 0)) > 0:
            stage_status = "PARTIAL"
        elif (
            evaluator_validation_summary.get(
                f"{policy['evaluator']['manual_review_status'].lower()}_count",
                0,
            )
            > 0
            or evaluator_validation_summary.get(
                f"{policy['evaluator']['invalid_status'].lower()}_count",
                0,
            )
            > 0
        ):
            stage_status = "REVIEW_REQUIRED"

    manifest = {
        "status": stage_status,
        "stage": "CHOICE_RELATION_ANALYSIS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_policy_version": policy["policy_version"],
        "generator_prompt_version": policy["prompt_version"],
        "evaluator_policy_version": policy["evaluator"]["policy_version"],
        "evaluator_prompt_version": policy["evaluator"]["prompt_version"],
        "final_gate_version": policy["evaluator"]["final_gate_version"],
        "validation_version": policy["validation"]["version"],
        "generator_model": policy["generator_model"],
        "evaluator_model": policy["evaluator_model"],
        "input_path": str(input_path),
        "input_digest": calculate_file_digest(input_path, policy),
        "preparation": preparation["summary"],
        "execution": generator_execution,
        "evaluator_execution": evaluator_execution,
        "validation": generator_validation_summary,
        "evaluator_validation": evaluator_validation_summary,
        "final": final_summary,
        "evaluation": goldset_evaluation,
        "output_directory": str(output_directory),
    }
    write_json(manifest, paths["manifest"])
    return manifest


def main() -> None:
    """CLI 진입점."""
    manifest = run_choice_relation_analysis(parse_arguments())
    print(
        "choice relation 준비 완료: "
        f"{manifest['preparation']['selected_task_count']} tasks"
    )
    print(
        "생성: "
        f"{manifest['execution']['mode']} / "
        f"pending={manifest['execution'].get('pending_task_count', 0)}"
    )
    print(
        "평가: "
        f"{manifest['evaluator_execution']['mode']} / "
        f"pending={manifest['evaluator_execution'].get('pending_task_count', 0)}"
    )
    print(f"단계 상태: {manifest['status']}")
    print(f"산출물: {manifest['output_directory']}")
    if manifest["status"] == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

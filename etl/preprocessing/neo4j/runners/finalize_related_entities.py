import _bootstrap

from argparse import ArgumentParser
from datetime import datetime, timezone
from json import dumps, load
from pathlib import Path

import pandas as pd

from common import load_pipeline_policy
from entity_resolution.finalize_entity_resolution import (
    finalize_entity_resolution,
    load_existing_registry,
    write_final_resolution_tables,
)
from entity_resolution.problem_review import load_term_decision_tables
from entity_resolution.related_entity_resolution import (
    select_seed_backed_alternatives,
)
from entity_resolution.semantic_review import load_resolution_package
from goldset.build_gold_set import calculate_file_sha256


def resolve_related_finalization_paths(
    neo4j_root: Path,
    policy: dict,
    related_output_dir: str = "",
    final_output_dir: str = "",
    registry_path: str = "",
) -> dict[str, Path]:
    """관련 엔티티 판정 결과와 최종 identity 출력 경로를 결정한다."""
    related_policy = policy["entity_resolution"][
        "related_entity_resolution"
    ]
    final_policy = related_policy["final_identity"]
    related_directory = Path(related_output_dir).resolve()
    if not related_output_dir:
        related_directory = (
            neo4j_root / related_policy["default_output_directory"]
        ).resolve()
    final_directory = Path(final_output_dir).resolve()
    if not final_output_dir:
        final_directory = (
            neo4j_root / final_policy["default_output_directory"]
        ).resolve()
    registry_file = policy["entity_resolution"]["canonical_registry"][
        "output_files"
    ]["canonical_registry"]
    related_output_files = related_policy["output_files"]
    registry = Path(registry_path).resolve()
    if not registry_path:
        registry = final_directory / registry_file
    return {
        "related_output_directory": related_directory,
        "resolution_package": related_directory,
        "review_tasks": related_directory
        / related_output_files["term_review_tasks"],
        "staging_manifest": related_directory
        / related_output_files["manifest"],
        "review_manifest": related_directory
        / related_output_files["review_manifest"],
        "final_output_directory": final_directory,
        "selection": final_directory / final_policy["selection_file"],
        "registry": registry,
        "manifest": final_directory / final_policy["manifest_file"],
    }


def run_related_entity_finalization(
    neo4j_root: str,
    policy_path: str,
    related_output_dir: str = "",
    final_output_dir: str = "",
    registry_path: str = "",
    dry_run: bool = False,
) -> dict[str, object]:
    """검증된 관련 엔티티를 canonical registry와 Neo4j CSV로 승격한다."""
    root = Path(neo4j_root).resolve()
    policy = load_pipeline_policy(policy_path)
    paths = resolve_related_finalization_paths(
        root,
        policy,
        related_output_dir=related_output_dir,
        final_output_dir=final_output_dir,
        registry_path=registry_path,
    )
    provenance_errors: list[str] = []
    staging_manifest: dict[str, object] = {}
    review_manifest: dict[str, object] = {}
    if not paths["staging_manifest"].is_file():
        provenance_errors.append("관련 엔티티 후보 생성 manifest가 없습니다.")
    if not paths["review_manifest"].is_file():
        provenance_errors.append("관련 엔티티 LLM 검증 manifest가 없습니다.")
    if not provenance_errors:
        with paths["staging_manifest"].open(
            "r",
            encoding="utf-8",
        ) as input_file:
            staging_manifest = load(input_file)
        with paths["review_manifest"].open(
            "r",
            encoding="utf-8",
        ) as input_file:
            review_manifest = load(input_file)
        if review_manifest.get("status") != "COMPLETED":
            provenance_errors.append(
                "관련 엔티티 LLM 검증 단계가 완료 상태가 아닙니다."
            )
        if (
            review_manifest.get("queue_sha256")
            != staging_manifest.get("queue_sha256")
        ):
            provenance_errors.append(
                "후보 생성과 LLM 검증의 queue 버전이 다릅니다."
            )
        if not paths["review_tasks"].is_file():
            provenance_errors.append("관련 엔티티 review task가 없습니다.")
        elif (
            review_manifest.get("term_review_tasks_sha256")
            != calculate_file_sha256(str(paths["review_tasks"]))
        ):
            provenance_errors.append(
                "LLM 검증 이후 related review task가 변경됐습니다."
            )
        gate_hashes = review_manifest.get("gate_output_sha256", {})
        output_files = policy["entity_resolution"]["semantic_review"][
            "term_decision_output_files"
        ]
        required_gate_names = [
            "term_resolution_decisions",
            "reviewed_canonical_alternatives",
            "reviewed_source_roles",
        ]
        for table_name in required_gate_names:
            output_path = (
                paths["related_output_directory"]
                / output_files[table_name]
            )
            if not output_path.is_file():
                provenance_errors.append(
                    f"검증 게이트 출력이 없습니다: {output_path}"
                )
            elif gate_hashes.get(table_name) != calculate_file_sha256(
                str(output_path)
            ):
                provenance_errors.append(
                    f"검증 게이트 출력 버전이 다릅니다: {output_path}"
                )
    if provenance_errors:
        if dry_run:
            return {
                "status": "BLOCKED_BY_STALE_RELATED_REVIEW",
                "stage": "RELATED_ENTITY_FINALIZATION",
                "dry_run": True,
                "provenance_errors": provenance_errors,
                "paths": {name: str(path) for name, path in paths.items()},
            }
        raise ValueError(" ".join(provenance_errors))

    resolution_tables = load_resolution_package(
        str(paths["resolution_package"]),
        policy,
    )
    decision_tables = load_term_decision_tables(
        str(paths["related_output_directory"]),
        policy,
    )
    reviewed_roles_path = (
        paths["related_output_directory"]
        / output_files["reviewed_source_roles"]
    )
    if not reviewed_roles_path.is_file():
        raise FileNotFoundError(
            f"검증된 candidate role CSV를 찾을 수 없습니다: {reviewed_roles_path}"
        )
    decision_tables["reviewed_source_roles"] = pd.read_csv(
        reviewed_roles_path,
        dtype=str,
    ).fillna("")
    selections = select_seed_backed_alternatives(
        resolution_tables,
        decision_tables,
        policy,
    )
    verified_selections = selections.loc[
        selections["selection_status"] == "VERIFIED"
    ]
    status_counts = {
        str(status): int(count)
        for status, count in selections["selection_status"]
        .value_counts()
        .items()
    }
    if dry_run:
        return {
            "status": "READY",
            "stage": "RELATED_ENTITY_FINALIZATION",
            "dry_run": True,
            "selection_counts": status_counts,
            "paths": {name: str(path) for name, path in paths.items()},
        }

    paths["selection"].parent.mkdir(parents=True, exist_ok=True)
    selections.to_csv(
        paths["selection"],
        index=False,
        encoding="utf-8-sig",
    )
    preselected_methods = {
        str(row["canonical_alternative_id"]): str(row["selection_method"])
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
    existing_registry = load_existing_registry(str(paths["registry"]))
    final_tables = finalize_entity_resolution(
        resolution_tables,
        decision_tables,
        empty_problem_assignments,
        existing_registry,
        policy,
        preselected_alternative_methods=preselected_methods,
        manually_approved_alternative_ids=set(preselected_methods),
    )
    final_paths = write_final_resolution_tables(
        final_tables,
        str(paths["final_output_directory"]),
        policy,
    )
    final_paths["related_entity_selections"] = str(paths["selection"])
    manifest = {
        "status": "COMPLETED",
        "stage": "RELATED_ENTITY_FINALIZATION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resolution_policy_version": policy["policy_version"],
        "selection_counts": status_counts,
        "canonical_entity_count": len(final_tables["canonical_registry"]),
        "acceptance_review_count": len(
            final_tables["canonical_acceptance_review_queue"]
        ),
        "outputs": final_paths,
    }
    paths["manifest"].write_text(
        dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    neo4j_directory = Path(__file__).resolve().parent.parent
    parser = ArgumentParser(
        description="검증된 관련 엔티티를 최종 identity CSV로 승격"
    )
    parser.add_argument("--related-output-dir", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--registry", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--policy",
        default=str(
            neo4j_directory / "config" / "resolution_policy.json"
        ),
    )
    cli_args = parser.parse_args()
    result = run_related_entity_finalization(
        neo4j_root=str(neo4j_directory),
        policy_path=cli_args.policy,
        related_output_dir=cli_args.related_output_dir,
        final_output_dir=cli_args.output_dir,
        registry_path=cli_args.registry,
        dry_run=cli_args.dry_run,
    )
    print(dumps(result, ensure_ascii=False, indent=2))
    if result["status"] not in {"READY", "COMPLETED"}:
        raise SystemExit(1)

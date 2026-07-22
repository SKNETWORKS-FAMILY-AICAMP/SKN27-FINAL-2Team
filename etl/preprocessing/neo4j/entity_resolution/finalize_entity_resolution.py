import sys
from argparse import ArgumentParser
from datetime import datetime, timezone
from json import dumps, loads
from pathlib import Path
from uuid import uuid4

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "terms"))

from common import load_pipeline_policy
from entity_resolution.identifiers import create_stable_id
from entity_resolution.problem_review import (
    load_term_decision_tables,
)
from entity_resolution.semantic_review import load_resolution_package
from prep_thesaurus import build_match_key


def build_dataframe(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    """빈 최종 결과에서도 import 계약 컬럼을 유지한다."""
    return pd.DataFrame(rows, columns=columns)


def create_canonical_registry_id(
    entity_type: str,
    registry_policy: dict,
    uuid_factory,
) -> tuple[str, str]:
    """원천 키와 무관한 UUID 기반 canonical ID를 발급한다."""
    canonical_uuid = str(uuid_factory())
    canonical_id = (
        f"{registry_policy['canonical_id_prefix']}"
        f"{entity_type.lower()}:{canonical_uuid}"
    )
    return canonical_id, canonical_uuid


def load_existing_registry(registry_path: str) -> pd.DataFrame:
    """기존 registry가 있으면 읽고, 최초 실행이면 빈 계약을 반환한다."""
    columns = [
        "canonical_id",
        "canonical_uuid",
        "entity_type",
        "display_name",
        "lifecycle_status",
        "identity_member_source_ids_json",
        "resolution_case_ids_json",
        "created_at",
        "updated_at",
        "registry_version",
    ]
    if not registry_path:
        return pd.DataFrame(columns=columns)
    path = Path(registry_path)
    if not path.is_file():
        return pd.DataFrame(columns=columns)
    registry = pd.read_csv(path, dtype=str).fillna("")
    missing_columns = set(columns).difference(registry.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"canonical registry 필수 컬럼이 없습니다: {missing_text}")
    return registry[columns]


def finalize_entity_resolution(
    resolution_tables: dict[str, pd.DataFrame],
    term_decision_tables: dict[str, pd.DataFrame],
    verified_problem_assignments: pd.DataFrame,
    existing_registry: pd.DataFrame,
    policy: dict,
    uuid_factory=uuid4,
    timestamp: str = "",
    preselected_alternative_methods: dict[str, str] | None = None,
    manually_approved_alternative_ids: set[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """검증된 identity만 registry와 Neo4j import용 테이블로 승격한다."""
    resolution_policy = policy["entity_resolution"]
    registry_policy = resolution_policy["canonical_registry"]
    identifier_policy = resolution_policy["identifier_policy"]
    current_timestamp = timestamp
    if not current_timestamp:
        current_timestamp = datetime.now(timezone.utc).isoformat()
    registry_columns = [
        "canonical_id",
        "canonical_uuid",
        "entity_type",
        "display_name",
        "lifecycle_status",
        "identity_member_source_ids_json",
        "resolution_case_ids_json",
        "created_at",
        "updated_at",
        "registry_version",
    ]
    missing_registry_columns = set(registry_columns).difference(
        existing_registry.columns
    )
    if missing_registry_columns:
        missing_text = ", ".join(sorted(missing_registry_columns))
        raise ValueError(f"canonical registry 필수 컬럼이 없습니다: {missing_text}")
    cases = resolution_tables["resolution_cases"]
    candidates = resolution_tables["source_record_candidates"]
    reviewed_alternatives = term_decision_tables[
        "reviewed_canonical_alternatives"
    ]
    reviewed_roles = term_decision_tables["reviewed_source_roles"]
    case_by_id = {
        str(row["resolution_case_id"]): row
        for row in cases.to_dict("records")
    }
    candidate_by_id = {
        str(row["source_candidate_id"]): row
        for row in candidates.to_dict("records")
    }
    alternative_by_id = {
        str(row["canonical_alternative_id"]): row
        for row in reviewed_alternatives.to_dict("records")
        if row["verification_status"] == "VERIFIED"
    }
    role_by_candidate = {
        str(row["source_candidate_id"]): row
        for row in reviewed_roles.to_dict("records")
        if row["verification_status"] == "VERIFIED"
    }
    selected_alternative_ids: set[str] = set()
    selection_method_by_alternative: dict[str, str] = {}
    for assignment in verified_problem_assignments.to_dict("records"):
        if assignment["verification_status"] != "VERIFIED":
            continue
        assignment_alternative_ids = loads(
            assignment["selected_canonical_alternative_ids_json"]
        )
        selected_alternative_ids.update(assignment_alternative_ids)
        for alternative_id in assignment_alternative_ids:
            selection_method_by_alternative[alternative_id] = (
                "verified_problem_assignment"
            )
    for alternative_id, selection_method in (
        preselected_alternative_methods or {}
    ).items():
        selected_alternative_ids.add(alternative_id)
        selection_method_by_alternative[alternative_id] = selection_method
    unknown_alternative_ids = selected_alternative_ids.difference(
        alternative_by_id
    )
    if unknown_alternative_ids:
        unknown_text = dumps(
            sorted(unknown_alternative_ids),
            ensure_ascii=False,
        )
        raise ValueError(f"검증되지 않은 canonical 대안이 선택됐습니다: {unknown_text}")

    registry_rows = existing_registry[registry_columns].to_dict("records")
    acceptance_rows: list[dict] = []
    alternative_to_canonical: dict[str, str] = {}
    minimum_members = int(
        registry_policy["minimum_automatic_identity_members"]
    )
    manually_approved_ids = manually_approved_alternative_ids or set()
    for alternative_id in sorted(selected_alternative_ids):
        alternative = alternative_by_id[alternative_id]
        member_ids = loads(alternative["source_candidate_ids_json"])
        source_record_ids = set(
            loads(alternative["identity_member_source_ids_json"])
        )
        all_roles_are_identity = all(
            role_by_candidate.get(candidate_id, {}).get("verified_role")
            == "IDENTITY_MEMBER"
            for candidate_id in member_ids
        )
        merge_gate_passed = (
            str(alternative["merge_gate_passed"]).lower() == "true"
        )
        below_automatic_member_minimum = (
            len(member_ids) < minimum_members
            and alternative_id not in manually_approved_ids
        )
        if (
            below_automatic_member_minimum
            or not merge_gate_passed
            or not all_roles_are_identity
        ):
            review_id = create_stable_id(
                identifier_policy["canonical_acceptance_review_prefix"],
                [alternative_id, policy["policy_version"]],
                identifier_policy,
            )
            acceptance_rows.append(
                {
                    "canonical_acceptance_review_id": review_id,
                    "canonical_alternative_id": alternative_id,
                    "resolution_case_id": alternative["resolution_case_id"],
                    "canonical_term": case_by_id[
                        alternative["resolution_case_id"]
                    ]["canonical_term"],
                    "display_name": alternative["display_name_proposal"],
                    "entity_type": alternative["entity_type_proposal"],
                    "identity_member_source_ids_json": dumps(
                        sorted(source_record_ids),
                        ensure_ascii=False,
                    ),
                    "review_reason": "AUTOMATIC_ACCEPTANCE_GATE_NOT_SATISFIED",
                    "member_count": len(member_ids),
                    "review_status": "PENDING",
                    "resolution_policy_version": policy["policy_version"],
                }
            )
            continue

        overlapping_registry_rows = [
            registry_row
            for registry_row in registry_rows
            if source_record_ids.intersection(
                loads(registry_row["identity_member_source_ids_json"])
            )
        ]
        if len(overlapping_registry_rows) > 1:
            review_id = create_stable_id(
                identifier_policy["canonical_acceptance_review_prefix"],
                [alternative_id, "multiple-registry-overlap"],
                identifier_policy,
            )
            acceptance_rows.append(
                {
                    "canonical_acceptance_review_id": review_id,
                    "canonical_alternative_id": alternative_id,
                    "resolution_case_id": alternative["resolution_case_id"],
                    "canonical_term": case_by_id[
                        alternative["resolution_case_id"]
                    ]["canonical_term"],
                    "display_name": alternative["display_name_proposal"],
                    "entity_type": alternative["entity_type_proposal"],
                    "identity_member_source_ids_json": dumps(
                        sorted(source_record_ids),
                        ensure_ascii=False,
                    ),
                    "review_reason": "MULTIPLE_CANONICAL_REGISTRY_OVERLAP",
                    "member_count": len(member_ids),
                    "review_status": "PENDING",
                    "resolution_policy_version": policy["policy_version"],
                }
            )
            continue

        entity_type = alternative["entity_type_proposal"]
        registry_row: dict
        if len(overlapping_registry_rows) == 1:
            registry_row = overlapping_registry_rows[0]
            if registry_row["entity_type"] != entity_type:
                review_id = create_stable_id(
                    identifier_policy["canonical_acceptance_review_prefix"],
                    [alternative_id, "entity-type-conflict"],
                    identifier_policy,
                )
                acceptance_rows.append(
                    {
                        "canonical_acceptance_review_id": review_id,
                        "canonical_alternative_id": alternative_id,
                        "resolution_case_id": alternative[
                            "resolution_case_id"
                        ],
                        "canonical_term": case_by_id[
                            alternative["resolution_case_id"]
                        ]["canonical_term"],
                        "display_name": alternative[
                            "display_name_proposal"
                        ],
                        "entity_type": alternative[
                            "entity_type_proposal"
                        ],
                        "identity_member_source_ids_json": dumps(
                            sorted(source_record_ids),
                            ensure_ascii=False,
                        ),
                        "review_reason": "CANONICAL_REGISTRY_ENTITY_TYPE_CONFLICT",
                        "member_count": len(member_ids),
                        "review_status": "PENDING",
                        "resolution_policy_version": policy["policy_version"],
                    }
                )
                continue
            existing_source_ids = set(
                loads(registry_row["identity_member_source_ids_json"])
            )
            existing_case_ids = set(
                loads(registry_row["resolution_case_ids_json"])
            )
            existing_source_ids.update(source_record_ids)
            existing_case_ids.add(alternative["resolution_case_id"])
            registry_row["identity_member_source_ids_json"] = dumps(
                sorted(existing_source_ids),
                ensure_ascii=False,
            )
            registry_row["resolution_case_ids_json"] = dumps(
                sorted(existing_case_ids),
                ensure_ascii=False,
            )
            registry_row["updated_at"] = current_timestamp
        elif len(overlapping_registry_rows) == 0:
            canonical_id, canonical_uuid = create_canonical_registry_id(
                entity_type,
                registry_policy,
                uuid_factory,
            )
            registry_row = {
                "canonical_id": canonical_id,
                "canonical_uuid": canonical_uuid,
                "entity_type": entity_type,
                "display_name": alternative["display_name_proposal"],
                "lifecycle_status": registry_policy["active_status"],
                "identity_member_source_ids_json": dumps(
                    sorted(source_record_ids),
                    ensure_ascii=False,
                ),
                "resolution_case_ids_json": dumps(
                    [alternative["resolution_case_id"]],
                    ensure_ascii=False,
                ),
                "created_at": current_timestamp,
                "updated_at": current_timestamp,
                "registry_version": registry_policy["registry_version"],
            }
            registry_rows.append(registry_row)
        alternative_to_canonical[alternative_id] = registry_row["canonical_id"]

    source_node_rows: dict[str, dict] = {}
    resolution_rows: dict[tuple[str, str], dict] = {}
    source_target_by_id: dict[str, str] = {}
    for alternative_id, canonical_id in alternative_to_canonical.items():
        alternative = alternative_by_id[alternative_id]
        member_ids = loads(alternative["source_candidate_ids_json"])
        for candidate_id in member_ids:
            candidate = candidate_by_id[candidate_id]
            source_record_id = candidate["source_record_id"]
            previous_target = source_target_by_id.get(source_record_id)
            if previous_target and previous_target != canonical_id:
                raise ValueError(
                    "하나의 SourceRecord가 여러 CanonicalEntity로 승인됐습니다: "
                    f"{source_record_id}"
                )
            source_target_by_id[source_record_id] = canonical_id
            source_node_rows[source_record_id] = {
                "source_record_id": source_record_id,
                "source": candidate["source"],
                "source_key": candidate["source_key"],
                "source_release": candidate["source_release"],
                "source_metadata_json": candidate["source_metadata_json"],
            }
            resolution_rows[(source_record_id, canonical_id)] = {
                "source_record_id": source_record_id,
                "canonical_id": canonical_id,
                "match_status": "ACCEPTED",
                "method": selection_method_by_alternative.get(
                    alternative_id,
                    registry_policy["accepted_resolution_method"],
                ),
                "version": policy["policy_version"],
                "term_decision_id": alternative["term_decision_id"],
            }

    entity_name_rows: dict[str, dict] = {}
    entity_name_reference_rows: dict[tuple[str, str], dict] = {}
    for alternative_id, canonical_id in alternative_to_canonical.items():
        alternative = alternative_by_id[alternative_id]
        case = case_by_id[alternative["resolution_case_id"]]
        term_variants = loads(case["term_variants_json"])
        for term_variant in term_variants:
            normalized_name = build_match_key(term_variant)
            entity_name_id = create_stable_id(
                identifier_policy["entity_name_prefix"],
                [
                    term_variant,
                    normalized_name,
                    policy["normalization_policy_version"],
                ],
                identifier_policy,
            )
            name_type = "ALIAS"
            if term_variant == case["canonical_term"]:
                name_type = "CANONICAL_TERM"
            entity_name_rows[entity_name_id] = {
                "entity_name_id": entity_name_id,
                "name": term_variant,
                "normalized_name": normalized_name,
                "name_type": name_type,
                "normalization_policy_version": policy[
                    "normalization_policy_version"
                ],
            }
            entity_name_reference_rows[(entity_name_id, canonical_id)] = {
                "entity_name_id": entity_name_id,
                "canonical_id": canonical_id,
                "match_status": "ACCEPTED",
                "method": selection_method_by_alternative[alternative_id],
                "version": policy["policy_version"],
            }

    final_assignment_rows: list[dict] = []
    for assignment in verified_problem_assignments.to_dict("records"):
        selected_ids = loads(
            assignment["selected_canonical_alternative_ids_json"]
        )
        canonical_ids = [
            alternative_to_canonical[alternative_id]
            for alternative_id in selected_ids
            if alternative_id in alternative_to_canonical
        ]
        link_status = "AMBIGUOUS"
        if not selected_ids:
            link_status = "UNRESOLVED"
        elif len(canonical_ids) == len(selected_ids):
            link_status = "ACCEPTED"
        final_assignment_rows.append(
            {
                "problem_assignment_id": assignment[
                    "problem_assignment_id"
                ],
                "problem_id": assignment["problem_id"],
                "resolution_case_id": assignment["resolution_case_id"],
                "selected_canonical_alternative_ids_json": dumps(
                    selected_ids,
                    ensure_ascii=False,
                ),
                "canonical_ids_json": dumps(
                    canonical_ids,
                    ensure_ascii=False,
                ),
                "link_status": link_status,
                "resolution_method": assignment["resolution_method"],
                "resolution_policy_version": policy["policy_version"],
            }
        )

    canonical_node_columns = [
        "canonical_id",
        "display_name",
        "entity_type",
        "lifecycle_status",
        "registry_version",
    ]
    source_node_columns = [
        "source_record_id",
        "source",
        "source_key",
        "source_release",
        "source_metadata_json",
    ]
    entity_name_columns = [
        "entity_name_id",
        "name",
        "normalized_name",
        "name_type",
        "normalization_policy_version",
    ]
    resolution_columns = [
        "source_record_id",
        "canonical_id",
        "match_status",
        "method",
        "version",
        "term_decision_id",
    ]
    reference_columns = [
        "entity_name_id",
        "canonical_id",
        "match_status",
        "method",
        "version",
    ]
    assignment_columns = [
        "problem_assignment_id",
        "problem_id",
        "resolution_case_id",
        "selected_canonical_alternative_ids_json",
        "canonical_ids_json",
        "link_status",
        "resolution_method",
        "resolution_policy_version",
    ]
    acceptance_columns = [
        "canonical_acceptance_review_id",
        "canonical_alternative_id",
        "resolution_case_id",
        "canonical_term",
        "display_name",
        "entity_type",
        "identity_member_source_ids_json",
        "review_reason",
        "member_count",
        "review_status",
        "resolution_policy_version",
    ]
    canonical_node_rows = [
        {
            "canonical_id": row["canonical_id"],
            "display_name": row["display_name"],
            "entity_type": row["entity_type"],
            "lifecycle_status": row["lifecycle_status"],
            "registry_version": row["registry_version"],
        }
        for row in registry_rows
    ]
    return {
        "canonical_registry": build_dataframe(
            registry_rows,
            registry_columns,
        ),
        "canonical_entity_nodes": build_dataframe(
            canonical_node_rows,
            canonical_node_columns,
        ),
        "source_record_nodes": build_dataframe(
            list(source_node_rows.values()),
            source_node_columns,
        ),
        "entity_name_nodes": build_dataframe(
            list(entity_name_rows.values()),
            entity_name_columns,
        ),
        "source_record_resolutions": build_dataframe(
            list(resolution_rows.values()),
            resolution_columns,
        ),
        "entity_name_references": build_dataframe(
            list(entity_name_reference_rows.values()),
            reference_columns,
        ),
        "final_problem_assignments": build_dataframe(
            final_assignment_rows,
            assignment_columns,
        ),
        "canonical_acceptance_review_queue": build_dataframe(
            acceptance_rows,
            acceptance_columns,
        ),
    }


def write_final_resolution_tables(
    tables: dict[str, pd.DataFrame],
    output_dir: str,
    policy: dict,
) -> dict[str, str]:
    """registry와 Neo4j identity import CSV를 정책 파일명으로 저장한다."""
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_files = policy["entity_resolution"]["canonical_registry"][
        "output_files"
    ]
    written: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = output_directory / output_files[table_name]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        written[table_name] = str(output_path)
    return written


if __name__ == "__main__":
    parser = ArgumentParser(
        description="검증된 Entity Resolution을 canonical registry와 Neo4j CSV로 승격"
    )
    parser.add_argument("resolution_dir", help="ER staging CSV 폴더")
    parser.add_argument("review_dir", help="term·problem review 결과 폴더")
    parser.add_argument("output_dir", help="최종 identity CSV 출력 폴더")
    parser.add_argument(
        "--registry",
        default="",
        help="기존 canonical entity registry CSV 경로",
    )
    parser.add_argument(
        "--policy",
        default=str(
            Path(__file__).resolve().parent.parent
            / "config"
            / "resolution_policy.json"
        ),
        help="Entity Resolution 정책 JSON 경로",
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    base_tables = load_resolution_package(
        cli_args.resolution_dir,
        pipeline_policy,
    )
    term_tables = load_term_decision_tables(
        cli_args.review_dir,
        pipeline_policy,
    )
    term_output_files = pipeline_policy["entity_resolution"][
        "semantic_review"
    ]["term_decision_output_files"]
    reviewed_roles_path = Path(cli_args.review_dir) / term_output_files[
        "reviewed_source_roles"
    ]
    term_tables["reviewed_source_roles"] = pd.read_csv(
        reviewed_roles_path,
        dtype=str,
    ).fillna("")
    problem_output_files = pipeline_policy["entity_resolution"][
        "semantic_review"
    ]["problem_decision_output_files"]
    verified_assignments_path = Path(cli_args.review_dir) / problem_output_files[
        "verified_problem_assignments"
    ]
    verified_assignments = pd.read_csv(
        verified_assignments_path,
        dtype=str,
    ).fillna("")
    registry = load_existing_registry(cli_args.registry)
    final_tables = finalize_entity_resolution(
        base_tables,
        term_tables,
        verified_assignments,
        registry,
        pipeline_policy,
    )
    output_paths = write_final_resolution_tables(
        final_tables,
        cli_args.output_dir,
        pipeline_policy,
    )
    print(dumps(output_paths, ensure_ascii=False, indent=2))

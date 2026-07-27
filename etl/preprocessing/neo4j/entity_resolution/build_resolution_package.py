import sys
from argparse import ArgumentParser
from json import dumps, load, loads
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "terms"))

from common import load_pipeline_policy
from entity_resolution.identifiers import create_stable_id
from entity_resolution.propose_canonical_alternatives import (
    build_source_candidate_proposal_tables,
)
from prep_json import prep_json
from prep_thesaurus import build_match_key


def index_enrichment_candidates(enrichment_results: list[dict]) -> dict:
    """AKS 보강 검색 결과를 용어와 category 복합 키로 인덱싱한다."""
    return {
        (item["canonical_term"], item["category"]): item.get("candidates", [])
        for item in enrichment_results
    }


def merge_normalized_match_items(
    match_results: list[dict],
    resolution_policy: dict,
) -> list[dict]:
    """정규화 이름과 category가 같은 기존 집계 행을 손실 없이 합친다."""
    merged_by_key: dict[tuple[str, ...], dict] = {}
    candidate_collections = resolution_policy["candidate_collections"]
    for item in match_results:
        term = item["canonical_term"]
        category = item["category"]
        entity_type_proposal = str(
            item.get("entity_type_proposal") or ""
        )
        input_resolution_case_id = str(
            item.get("input_resolution_case_id") or ""
        )
        normalized_key = (
            build_match_key(term),
            category,
            entity_type_proposal,
            input_resolution_case_id,
        )
        existing = merged_by_key.get(normalized_key)
        if existing is None:
            stored = dict(item)
            stored["term_variants"] = [term]
            stored["problem_ids"] = list(item.get("problem_ids", []))
            stored["representative_problem_count"] = int(
                item.get("problem_count") or 0
            )
            for collection in candidate_collections:
                stored[collection] = list(item.get(collection, []))
            merged_by_key[normalized_key] = stored
            continue

        if term not in existing["term_variants"]:
            existing["term_variants"].append(term)
        existing["problem_ids"] = sorted(
            set(existing["problem_ids"]).union(item.get("problem_ids", []))
        )
        existing["is_noise"] = bool(existing.get("is_noise")) and bool(
            item.get("is_noise")
        )
        for collection in candidate_collections:
            existing[collection].extend(item.get(collection, []))

        problem_count = int(item.get("problem_count") or 0)
        if problem_count > existing["representative_problem_count"]:
            existing["canonical_term"] = term
            existing["representative_problem_count"] = problem_count
        for metadata_field in [
            "extraction_model",
            "extraction_reasoning_effort",
            "extraction_policy_version",
        ]:
            if not existing.get(metadata_field) and item.get(metadata_field):
                existing[metadata_field] = item[metadata_field]

    merged_items = list(merged_by_key.values())
    for item in merged_items:
        item.pop("representative_problem_count", None)
        item["term_variants"].sort()
    return merged_items


def collect_case_candidates(
    match_item: dict,
    definition_index: dict,
    body_mention_index: dict,
    resolution_policy: dict,
) -> list[dict]:
    """이름·definition·body 후보를 SourceRecord 단위로 합친다."""
    candidates_by_source_record: dict[str, dict] = {}
    for collection, channel in resolution_policy["candidate_collections"].items():
        for candidate in match_item.get(collection, []):
            add_candidate(
                candidates_by_source_record,
                candidate,
                channel,
            )

    definition_channel = resolution_policy["definition_candidate_channel"]
    for term_variant in match_item["term_variants"]:
        definition_key = (term_variant, match_item["category"])
        for candidate in definition_index.get(definition_key, []):
            add_candidate(
                candidates_by_source_record,
                candidate,
                definition_channel,
            )

    body_mention_channel = resolution_policy[
        "body_mention_candidate_channel"
    ]
    for term_variant in match_item["term_variants"]:
        body_mention_key = (term_variant, match_item["category"])
        for candidate in body_mention_index.get(body_mention_key, []):
            add_candidate(
                candidates_by_source_record,
                candidate,
                body_mention_channel,
            )

    candidates = list(candidates_by_source_record.values())
    candidates.sort(
        key=lambda candidate: (
            candidate.get("category_mismatch") is True,
            candidate.get("retrieval_method") != "exact",
            -float(candidate.get("retrieval_score") or 0.0),
            candidate["source_record_id"],
        )
    )
    return candidates


def add_candidate(
    candidates_by_source_record: dict[str, dict],
    candidate: dict,
    channel: str,
) -> None:
    """같은 SourceRecord 후보를 합치고 회수 채널을 모두 보존한다."""
    source_record_id = str(candidate.get("source_record_id") or "").strip()
    if not source_record_id:
        return

    existing = candidates_by_source_record.get(source_record_id)
    if existing is None:
        stored = dict(candidate)
        stored["retrieval_channels"] = {channel}
        candidates_by_source_record[source_record_id] = stored
        return

    existing["retrieval_channels"].add(channel)
    candidate_score = float(candidate.get("retrieval_score") or 0.0)
    existing_score = float(existing.get("retrieval_score") or 0.0)
    if candidate_score > existing_score:
        channels = existing["retrieval_channels"]
        stored = dict(candidate)
        stored["retrieval_channels"] = channels
        candidates_by_source_record[source_record_id] = stored


def get_initial_resolution_state(
    is_noise: bool,
    is_category_valid: bool,
    candidate_count: int,
    resolution_policy: dict,
) -> tuple[str, str, str]:
    """검증 전 케이스의 상태·사유·다음 검토 방법을 결정한다."""
    initial_status = resolution_policy["initial_link_status"]
    reason = resolution_policy["review_reason"]
    method = resolution_policy["review_method"]
    if is_noise:
        return initial_status["noise"], reason["noise"], ""
    if not is_category_valid:
        return (
            initial_status["invalid_category"],
            reason["invalid_category"],
            method["invalid_category"],
        )
    if candidate_count == 0:
        return (
            initial_status["candidate_not_found"],
            reason["candidate_not_found"],
            method["candidate_not_found"],
        )
    if candidate_count == 1:
        return (
            initial_status["candidate_found"],
            reason["candidate_found"],
            method["single_candidate"],
        )
    return (
        initial_status["candidate_found"],
        reason["candidate_found"],
        method["multiple_candidates"],
    )


def build_candidate_row(
    case_id: str,
    candidate: dict,
    candidate_rank: int,
    resolution_policy: dict,
    policy_version: str,
) -> dict:
    """원천 후보를 검증 가능한 한 행으로 직렬화한다."""
    identifier_policy = resolution_policy["identifier_policy"]
    source_record_id = candidate["source_record_id"]
    candidate_id = create_stable_id(
        identifier_policy["source_candidate_prefix"],
        [case_id, source_record_id],
        identifier_policy,
    )

    compatibility_policy = resolution_policy["category_compatibility_status"]
    compatibility = compatibility_policy["unknown"]
    evidence_signals = list(candidate.get("retrieval_methods", []))
    conflict_signals: list[str] = []
    category_mismatch = candidate.get("category_mismatch")
    if category_mismatch is False:
        compatibility = compatibility_policy["compatible"]
        evidence_signals.append("category_compatible")
    elif category_mismatch is True:
        compatibility = compatibility_policy["conflict"]
        conflict_signals.append("category_type_conflict")

    metadata_exclusions = {
        "source",
        "source_id",
        "source_release",
        "source_record_id",
        "matched_name",
        "matched_field",
        "retrieval_method",
        "retrieval_methods",
        "retrieval_score",
        "score_components",
        "verification_status",
        "retrieval_policy_version",
        "category_mismatch",
        "retrieval_channels",
    }
    source_metadata = {
        key: value
        for key, value in candidate.items()
        if key not in metadata_exclusions
    }
    return {
        "source_candidate_id": candidate_id,
        "resolution_case_id": case_id,
        "source_record_id": source_record_id,
        "source": candidate.get("source", ""),
        "source_key": candidate.get("source_id", ""),
        "source_release": candidate.get("source_release", ""),
        "candidate_rank": candidate_rank,
        "matched_name": candidate.get("matched_name", ""),
        "matched_field": candidate.get("matched_field", ""),
        "retrieval_method": candidate.get("retrieval_method", ""),
        "retrieval_channels_json": dumps(
            sorted(candidate.get("retrieval_channels", set())),
            ensure_ascii=False,
        ),
        "retrieval_methods_json": dumps(
            candidate.get("retrieval_methods", []),
            ensure_ascii=False,
        ),
        "retrieval_score": candidate.get("retrieval_score", ""),
        "score_components_json": dumps(
            candidate.get("score_components", {}),
            ensure_ascii=False,
            sort_keys=True,
        ),
        "category_compatibility": compatibility,
        "proposed_evidence_signals_json": dumps(
            sorted(set(evidence_signals)),
            ensure_ascii=False,
        ),
        "conflict_signals_json": dumps(
            sorted(set(conflict_signals)),
            ensure_ascii=False,
        ),
        "source_metadata_json": dumps(
            source_metadata,
            ensure_ascii=False,
            sort_keys=True,
        ),
        "candidate_status": resolution_policy["proposal_status"],
        "resolution_policy_version": policy_version,
    }


def build_resolution_tables(
    match_results: list[dict],
    definition_results: list[dict],
    problem_context_df: pd.DataFrame,
    policy: dict,
    body_mention_results: list[dict] | None = None,
) -> dict[str, pd.DataFrame]:
    """문항별 검증기에 전달할 정규화된 ER staging 테이블을 만든다."""
    resolution_policy = policy["entity_resolution"]
    identifier_policy = resolution_policy["identifier_policy"]
    definition_index = index_enrichment_candidates(definition_results)
    body_mention_index = index_enrichment_candidates(
        body_mention_results or []
    )
    merged_match_results = merge_normalized_match_items(
        match_results,
        resolution_policy,
    )
    context_by_problem: dict[str, dict] = {}
    for context in problem_context_df.to_dict("records"):
        problem_id = str(context["problem_id"])
        extraction_text = str(
            context.get("extraction_text")
            or context.get("full_text")
            or ""
        )
        context_by_problem[problem_id] = {
            **context,
            "extraction_text": extraction_text,
        }

    case_rows: list[dict] = []
    candidate_rows: list[dict] = []
    assignment_rows: list[dict] = []
    review_rows: list[dict] = []

    for match_item in merged_match_results:
        term = match_item["canonical_term"]
        category = match_item["category"]
        normalized_term = build_match_key(term)
        entity_type = str(match_item.get("entity_type_proposal") or "")
        if not entity_type:
            entity_type = resolution_policy["entity_type_mapping"].get(
                category,
                "",
            )
        allowed_entity_types = set(
            resolution_policy["entity_type_mapping"].values()
        )
        is_category_valid = entity_type in allowed_entity_types

        case_id = str(match_item.get("input_resolution_case_id") or "")
        if not case_id:
            case_id = create_stable_id(
                identifier_policy["resolution_case_prefix"],
                [
                    normalized_term,
                    category,
                    policy["normalization_policy_version"],
                ],
                identifier_policy,
            )
        candidates = collect_case_candidates(
            match_item,
            definition_index,
            body_mention_index,
            resolution_policy,
        )
        is_noise = bool(match_item.get("is_noise"))
        link_status, review_reason, review_method = get_initial_resolution_state(
            is_noise,
            is_category_valid,
            len(candidates),
            resolution_policy,
        )
        entity_type_status = resolution_policy["proposal_status"]
        if not is_category_valid:
            entity_type_status = link_status
        problem_ids = [str(value) for value in match_item.get("problem_ids", [])]
        source_count = len(
            {candidate.get("source", "") for candidate in candidates}
        )
        case_rows.append(
            {
                "resolution_case_id": case_id,
                "canonical_term": term,
                "normalized_term": normalized_term,
                "term_variants_json": dumps(
                    match_item["term_variants"],
                    ensure_ascii=False,
                ),
                "category": category,
                "entity_type_proposal": entity_type,
                "entity_type_status": entity_type_status,
                "problem_count": len(problem_ids),
                "problem_ids_json": dumps(problem_ids, ensure_ascii=False),
                "source_record_candidate_count": len(candidates),
                "source_system_count": source_count,
                "is_noise": is_noise,
                "link_status": link_status,
                "canonical_id": "",
                "resolution_method": "",
                "review_reason": review_reason,
                "extraction_model": match_item.get("extraction_model", ""),
                "extraction_policy_version": match_item.get(
                    "extraction_policy_version",
                    "",
                ),
                "normalization_policy_version": policy[
                    "normalization_policy_version"
                ],
                "resolution_policy_version": policy["policy_version"],
                "related_entity_task_id": match_item.get(
                    "related_entity_task_id",
                    "",
                ),
                "related_entity_origin_json": match_item.get(
                    "related_entity_origin_json",
                    "",
                ),
            }
        )

        current_candidate_rows: list[dict] = []
        for candidate_rank, candidate in enumerate(candidates, start=1):
            candidate_row = build_candidate_row(
                case_id,
                candidate,
                candidate_rank,
                resolution_policy,
                policy["policy_version"],
            )
            candidate_rows.append(candidate_row)
            current_candidate_rows.append(candidate_row)

        candidate_ids = [
            row["source_candidate_id"]
            for row in current_candidate_rows
        ]
        source_record_ids = [
            row["source_record_id"]
            for row in current_candidate_rows
        ]
        for problem_id in problem_ids:
            assignment_id = create_stable_id(
                identifier_policy["problem_assignment_prefix"],
                [problem_id, case_id],
                identifier_policy,
            )
            assignment_rows.append(
                {
                    "problem_assignment_id": assignment_id,
                    "problem_id": problem_id,
                    "resolution_case_id": case_id,
                    "canonical_term": term,
                    "category": category,
                    "canonical_id": "",
                    "assignment_status": link_status,
                    "resolution_method": "",
                    "source_candidate_ids_json": dumps(
                        candidate_ids,
                        ensure_ascii=False,
                    ),
                    "canonical_alternative_ids_json": "[]",
                    "selected_canonical_alternative_id": "",
                    "resolution_policy_version": policy["policy_version"],
                }
            )
            if is_noise:
                continue
            review_queue_id = create_stable_id(
                identifier_policy["review_queue_prefix"],
                [problem_id, case_id],
                identifier_policy,
            )
            review_rows.append(
                {
                    "review_queue_id": review_queue_id,
                    "problem_assignment_id": assignment_id,
                    "problem_id": problem_id,
                    "resolution_case_id": case_id,
                    "canonical_term": term,
                    "category": category,
                    "entity_type_proposal": entity_type,
                    "source_candidate_ids_json": dumps(
                        candidate_ids,
                        ensure_ascii=False,
                    ),
                    "source_record_ids_json": dumps(
                        source_record_ids,
                        ensure_ascii=False,
                    ),
                    "canonical_alternative_ids_json": "[]",
                    "proposed_resolution_method": review_method,
                    "review_reason": review_reason,
                    "review_status": resolution_policy["review_status"],
                    "llm_decision_status": "",
                    "identity_member_source_ids_json": "[]",
                    "selected_canonical_alternative_id": "",
                    "proposed_canonical_id": "",
                    "reviewer_reason": "",
                    "resolution_policy_version": policy["policy_version"],
                }
            )

    context_rows: list[dict] = []
    exact_status = policy["text_preprocessing"][
        "input_text_match_status"
    ]["exact"]
    for problem_id in sorted(context_by_problem):
        context = context_by_problem[problem_id]
        extraction_text = context["extraction_text"]
        match_status = str(
            context.get("input_text_match_status") or exact_status
        )
        duplicate_group_id = str(
            context.get("duplicate_text_group_id") or ""
        )
        requires_detail = (
            match_status != exact_status or bool(duplicate_group_id)
        )
        input_text_original = ""
        reconstructed_stem = ""
        if requires_detail:
            input_text_original = str(
                context.get("input_text_original") or ""
            )
            reconstructed_stem = str(
                context.get("reconstructed_stem") or ""
            )
        context_rows.append(
            {
                "problem_id": problem_id,
                "extraction_text": extraction_text,
                "input_text_original": input_text_original,
                "reconstructed_stem": reconstructed_stem,
                "input_text_match_status": match_status,
                "duplicate_text_group_id": duplicate_group_id,
                "text_policy_version": str(
                    context.get("text_policy_version")
                    or policy["text_preprocessing"]["version"]
                ),
                "context_available": bool(extraction_text),
                "normalization_policy_version": policy[
                    "normalization_policy_version"
                ],
            }
        )

    tables = {
        "resolution_cases": pd.DataFrame(case_rows),
        "source_record_candidates": pd.DataFrame(candidate_rows),
        "problem_contexts": pd.DataFrame(
            context_rows,
            columns=[
                "problem_id",
                "extraction_text",
                "input_text_original",
                "reconstructed_stem",
                "input_text_match_status",
                "duplicate_text_group_id",
                "text_policy_version",
                "context_available",
                "normalization_policy_version",
            ],
        ),
        "problem_resolution_assignments": pd.DataFrame(
            assignment_rows,
            columns=[
                "problem_assignment_id",
                "problem_id",
                "resolution_case_id",
                "canonical_term",
                "category",
                "canonical_id",
                "assignment_status",
                "resolution_method",
                "source_candidate_ids_json",
                "canonical_alternative_ids_json",
                "selected_canonical_alternative_id",
                "resolution_policy_version",
            ],
        ),
        "review_queue": pd.DataFrame(
            review_rows,
            columns=[
                "review_queue_id",
                "problem_assignment_id",
                "problem_id",
                "resolution_case_id",
                "canonical_term",
                "category",
                "entity_type_proposal",
                "source_candidate_ids_json",
                "source_record_ids_json",
                "canonical_alternative_ids_json",
                "proposed_resolution_method",
                "review_reason",
                "review_status",
                "llm_decision_status",
                "identity_member_source_ids_json",
                "selected_canonical_alternative_id",
                "proposed_canonical_id",
                "reviewer_reason",
                "resolution_policy_version",
            ],
        ),
    }
    tables.update(build_source_candidate_proposal_tables(tables, policy))
    attach_canonical_alternative_references(tables)
    return tables


def attach_canonical_alternative_references(
    tables: dict[str, pd.DataFrame],
) -> None:
    """case·문항·검토 큐에 선택 가능한 canonical 대안 ID를 연결한다."""
    clusters = tables["canonical_alternative_clusters"]
    alternatives_by_case: dict[str, list[str]] = {}
    for row in clusters.itertuples():
        alternatives_by_case.setdefault(row.resolution_case_id, []).append(
            row.canonical_alternative_id
        )
    for alternative_ids in alternatives_by_case.values():
        alternative_ids.sort()

    cases = tables["resolution_cases"]
    cases["canonical_alternative_count"] = cases["resolution_case_id"].map(
        lambda case_id: len(alternatives_by_case.get(case_id, []))
    )
    for table_name in ["problem_resolution_assignments", "review_queue"]:
        table = tables[table_name]
        for row_index, row in table.iterrows():
            alternative_ids = alternatives_by_case.get(
                row["resolution_case_id"],
                [],
            )
            table.at[row_index, "canonical_alternative_ids_json"] = dumps(
                alternative_ids,
                ensure_ascii=False,
            )


def write_resolution_package(
    tables: dict[str, pd.DataFrame],
    output_dir: str,
    policy: dict,
    output_path_overrides: dict[str, str | Path] | None = None,
) -> dict[str, str]:
    """ER staging 테이블을 정책에 지정된 CSV 파일명으로 저장한다."""
    validate_resolution_tables(tables, policy)
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_files = policy["entity_resolution"]["output_files"]
    path_overrides = output_path_overrides or {}
    written_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        configured_path = Path(
            path_overrides.get(table_name, output_files[table_name])
        )
        output_path = configured_path
        if not configured_path.is_absolute():
            output_path = output_directory / configured_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        written_paths[table_name] = str(output_path)
    return written_paths


def validate_resolution_tables(
    tables: dict[str, pd.DataFrame],
    policy: dict,
) -> None:
    """ER staging 테이블의 PK·FK·상태·집계 무결성을 검사한다."""
    required_tables = {
        "resolution_cases",
        "source_record_candidates",
        "source_candidate_features",
        "source_candidate_pair_signals",
        "canonical_alternative_clusters",
        "canonical_cluster_members",
        "problem_contexts",
        "problem_resolution_assignments",
        "review_queue",
    }
    missing_tables = required_tables.difference(tables)
    if missing_tables:
        missing_text = ", ".join(sorted(missing_tables))
        raise ValueError(f"ER staging 필수 테이블이 없습니다: {missing_text}")

    unique_keys = {
        "resolution_cases": "resolution_case_id",
        "source_record_candidates": "source_candidate_id",
        "source_candidate_features": "source_candidate_id",
        "source_candidate_pair_signals": "source_candidate_pair_id",
        "canonical_alternative_clusters": "canonical_alternative_id",
        "problem_contexts": "problem_id",
        "problem_resolution_assignments": "problem_assignment_id",
        "review_queue": "review_queue_id",
    }
    for table_name, key_column in unique_keys.items():
        table = tables[table_name]
        if table[key_column].duplicated().any():
            raise ValueError(f"{table_name}.{key_column} 값이 중복됩니다.")

    cases = tables["resolution_cases"]
    candidates = tables["source_record_candidates"]
    features = tables["source_candidate_features"]
    pair_signals = tables["source_candidate_pair_signals"]
    clusters = tables["canonical_alternative_clusters"]
    cluster_members = tables["canonical_cluster_members"]
    contexts = tables["problem_contexts"]
    assignments = tables["problem_resolution_assignments"]
    review_queue = tables["review_queue"]
    case_ids = set(cases["resolution_case_id"])
    candidate_ids = set(candidates["source_candidate_id"])
    cluster_ids = set(clusters["canonical_alternative_id"])
    context_ids = set(contexts["problem_id"])
    assignment_ids = set(assignments["problem_assignment_id"])

    unknown_candidate_cases = set(candidates["resolution_case_id"]).difference(
        case_ids
    )
    if unknown_candidate_cases:
        raise ValueError("source_record_candidates에 존재하지 않는 case FK가 있습니다.")
    if set(features["source_candidate_id"]) != candidate_ids:
        raise ValueError("모든 SourceRecord 후보는 정확히 하나의 feature 행이 필요합니다.")
    pair_candidate_ids = set(pair_signals["left_source_candidate_id"]).union(
        pair_signals["right_source_candidate_id"]
    )
    if pair_candidate_ids.difference(candidate_ids):
        raise ValueError("후보 쌍 신호에 존재하지 않는 source candidate FK가 있습니다.")
    if set(clusters["resolution_case_id"]).difference(case_ids):
        raise ValueError("canonical 대안에 존재하지 않는 case FK가 있습니다.")
    if set(cluster_members["canonical_alternative_id"]).difference(cluster_ids):
        raise ValueError("cluster member에 존재하지 않는 대안 FK가 있습니다.")
    if set(cluster_members["source_candidate_id"]).difference(candidate_ids):
        raise ValueError("cluster member에 존재하지 않는 후보 FK가 있습니다.")
    for assignment in assignments.itertuples():
        alternative_ids = set(loads(assignment.canonical_alternative_ids_json))
        if alternative_ids.difference(cluster_ids):
            raise ValueError("문항 배정에 존재하지 않는 canonical 대안 FK가 있습니다.")
    unknown_assignment_cases = set(assignments["resolution_case_id"]).difference(
        case_ids
    )
    if unknown_assignment_cases:
        raise ValueError(
            "problem_resolution_assignments에 존재하지 않는 case FK가 있습니다."
        )
    unknown_assignment_contexts = set(assignments["problem_id"]).difference(
        context_ids
    )
    if unknown_assignment_contexts:
        raise ValueError(
            "problem_resolution_assignments에 존재하지 않는 problem FK가 있습니다."
        )
    unknown_review_assignments = set(
        review_queue["problem_assignment_id"]
    ).difference(assignment_ids)
    if unknown_review_assignments:
        raise ValueError("review_queue에 존재하지 않는 assignment FK가 있습니다.")

    allowed_statuses = set(
        policy["entity_resolution"]["link_status_vocabulary"]
    )
    observed_statuses = set(cases["link_status"]).union(
        assignments["assignment_status"]
    )
    invalid_statuses = observed_statuses.difference(allowed_statuses)
    if invalid_statuses:
        invalid_text = ", ".join(sorted(invalid_statuses))
        raise ValueError(f"고정 상태 어휘 밖의 값이 있습니다: {invalid_text}")

    expected_candidate_status = policy["entity_resolution"]["proposal_status"]
    invalid_candidate_status = candidates[
        candidates["candidate_status"] != expected_candidate_status
    ]
    if not invalid_candidate_status.empty:
        raise ValueError("검증 전 후보 상태는 모두 PROPOSED여야 합니다.")
    proposal_tables_and_columns = [
        (features, "feature_status"),
        (features, "role_status"),
        (pair_signals, "proposal_status"),
        (clusters, "cluster_status"),
        (cluster_members, "role_status"),
    ]
    for proposal_table, status_column in proposal_tables_and_columns:
        if not proposal_table.empty and set(proposal_table[status_column]) != {
            expected_candidate_status
        }:
            raise ValueError("검증 전 feature·cluster 상태는 모두 PROPOSED여야 합니다.")
    allowed_roles = set(
        policy["entity_resolution"]["source_candidate_role_vocabulary"]
    )
    observed_roles = set(features["proposed_role"])
    if observed_roles.difference(allowed_roles):
        raise ValueError("정책에 없는 SourceRecord 후보 역할이 있습니다.")
    if features["proposed_role"].eq("").any():
        raise ValueError("모든 SourceRecord 후보에 제안 역할이 필요합니다.")

    observed_candidate_counts = (
        candidates.groupby("resolution_case_id").size().to_dict()
    )
    for row in cases.itertuples():
        observed_count = observed_candidate_counts.get(row.resolution_case_id, 0)
        if int(row.source_record_candidate_count) != observed_count:
            raise ValueError(
                f"case 후보 수가 일치하지 않습니다: {row.resolution_case_id}"
            )

    assigned_case_counts = assignments.groupby("resolution_case_id").size().to_dict()
    for row in cases.itertuples():
        assigned_count = assigned_case_counts.get(row.resolution_case_id, 0)
        if int(row.problem_count) != assigned_count:
            raise ValueError(
                f"case 문제 수가 일치하지 않습니다: {row.resolution_case_id}"
            )


def summarize_resolution_tables(
    tables: dict[str, pd.DataFrame],
) -> dict[str, object]:
    """EDA 기록에 사용할 ER staging 분포를 계산한다."""
    case_df = tables["resolution_cases"]
    candidate_df = tables["source_record_candidates"]
    feature_df = tables["source_candidate_features"]
    pair_df = tables["source_candidate_pair_signals"]
    cluster_df = tables["canonical_alternative_clusters"]
    status_counts = case_df["link_status"].value_counts().to_dict()
    source_counts: dict[str, int] = {}
    if not candidate_df.empty:
        source_counts = candidate_df["source"].value_counts().to_dict()
    return {
        "resolution_case_count": len(case_df),
        "source_record_candidate_count": len(candidate_df),
        "source_candidate_pair_count": len(pair_df),
        "merge_eligible_pair_count": int(pair_df["merge_eligible"].sum()),
        "canonical_alternative_count": len(cluster_df),
        "multi_source_alternative_count": int(
            (cluster_df["source_system_count"] > 1).sum()
        ),
        "problem_context_count": len(tables["problem_contexts"]),
        "problem_assignment_count": len(
            tables["problem_resolution_assignments"]
        ),
        "review_queue_count": len(tables["review_queue"]),
        "link_status_counts": status_counts,
        "candidate_source_counts": source_counts,
        "candidate_role_counts": feature_df["proposed_role"]
        .value_counts()
        .to_dict(),
    }


def run_resolution_package(
    match_json: str,
    definition_json: str,
    body_mention_json: str,
    exam_json: str,
    output_dir: str,
    policy: dict,
) -> dict[str, object]:
    """후보 JSON과 문제 원문을 읽어 ER staging CSV 패키지를 생성한다."""
    with open(match_json, "r", encoding="utf-8") as match_file:
        match_results = load(match_file)
    definition_results: list[dict] = []
    definition_path = Path(definition_json)
    if definition_path.is_file():
        with definition_path.open("r", encoding="utf-8") as definition_file:
            definition_results = load(definition_file)
    body_mention_results: list[dict] = []
    body_mention_path = Path(body_mention_json)
    if body_mention_path.is_file():
        with body_mention_path.open("r", encoding="utf-8") as body_file:
            body_mention_results = load(body_file)

    problem_context_df = prep_json(
        exam_json,
        policy["text_preprocessing"],
    )
    tables = build_resolution_tables(
        match_results,
        definition_results,
        problem_context_df,
        policy,
        body_mention_results=body_mention_results,
    )
    written_paths = write_resolution_package(tables, output_dir, policy)
    summary = summarize_resolution_tables(tables)
    summary["output_paths"] = written_paths
    return summary


if __name__ == "__main__":
    parser = ArgumentParser(
        description="문항별 Entity Resolution staging CSV 패키지 생성"
    )
    parser.add_argument("match_json", help="term_name_matches.json 경로")
    parser.add_argument("exam_json", help="기출문제 원본 JSON 경로")
    parser.add_argument("output_dir", help="ER staging CSV 출력 폴더")
    parser.add_argument(
        "--definition-json",
        default="",
        help="definition_scan_matches.json 경로",
    )
    parser.add_argument(
        "--body-mention-json",
        default="",
        help="body_mention_candidates.json 경로",
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
    result_summary = run_resolution_package(
        cli_args.match_json,
        cli_args.definition_json,
        cli_args.body_mention_json,
        cli_args.exam_json,
        cli_args.output_dir,
        pipeline_policy,
    )
    print(dumps(result_summary, ensure_ascii=False, indent=2))

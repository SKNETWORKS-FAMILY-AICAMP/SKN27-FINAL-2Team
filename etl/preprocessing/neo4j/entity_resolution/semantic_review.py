from argparse import ArgumentParser
from difflib import SequenceMatcher
from itertools import combinations
from json import dumps, loads
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "terms"))

from common import load_pipeline_policy, normalize_history_term
from entity_resolution.deterministic_triage import (
    build_candidate_deduplication_keys,
)
from entity_resolution.identifiers import create_stable_id


def load_resolution_package(
    input_dir: str,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """정책에 등록된 Entity Resolution staging CSV를 읽는다."""
    input_directory = Path(input_dir)
    output_files = policy["entity_resolution"]["output_files"]
    required_tables = [
        "resolution_cases",
        "source_record_candidates",
        "source_candidate_features",
        "source_candidate_pair_signals",
        "canonical_alternative_clusters",
        "canonical_cluster_members",
        "problem_contexts",
        "problem_resolution_assignments",
    ]
    tables: dict[str, pd.DataFrame] = {}
    for table_name in required_tables:
        input_path = input_directory / output_files[table_name]
        if not input_path.is_file():
            raise FileNotFoundError(f"ER staging CSV를 찾을 수 없습니다: {input_path}")
        tables[table_name] = pd.read_csv(input_path, dtype=str).fillna("")
    return tables


def select_source_context(
    metadata: dict,
    source: str,
    semantic_policy: dict,
) -> dict:
    """원천별 정책에 지정된 필드만 LLM 검토 문맥으로 구성한다."""
    selected: dict[str, object] = {}
    maximum_characters = int(
        semantic_policy["maximum_source_context_characters"]
    )
    source_fields = semantic_policy["source_context_fields"].get(source, [])
    for field_name in source_fields:
        raw_value = metadata.get(field_name)
        if raw_value is None or raw_value == "" or raw_value == []:
            continue
        if isinstance(raw_value, list):
            selected[field_name] = raw_value
            continue
        value = str(raw_value)
        if len(value) > maximum_characters:
            value = value[:maximum_characters]
        selected[field_name] = value
    return selected


def build_candidate_task_item(
    candidate: dict,
    feature: dict,
    semantic_policy: dict,
) -> dict:
    """SourceRecord 후보와 표준 feature를 하나의 검토 항목으로 합친다."""
    metadata = loads(candidate["source_metadata_json"])
    return {
        "source_candidate_id": candidate["source_candidate_id"],
        "source_record_id": candidate["source_record_id"],
        "source": candidate["source"],
        "candidate_rank": int(candidate["candidate_rank"]),
        "matched_name": candidate["matched_name"],
        "matched_field": candidate["matched_field"],
        "retrieval_method": candidate["retrieval_method"],
        "retrieval_score": float(candidate["retrieval_score"]),
        "category_compatibility": candidate["category_compatibility"],
        "normalized_names": loads(feature["normalized_names_json"]),
        "hanja": loads(feature["hanja_json"]),
        "era_values": loads(feature["era_values_json"]),
        "birth_year": feature["birth_year"],
        "death_year": feature["death_year"],
        "bonkwan": loads(feature["bonkwan_json"]),
        "source_entity_type_proposal": feature[
            "source_entity_type_proposal"
        ],
        "code_proposed_role": feature["proposed_role"],
        "code_canonical_alternative_id": feature[
            "proposed_canonical_alternative_id"
        ],
        "source_context": select_source_context(
            metadata,
            candidate["source"],
            semantic_policy,
        ),
    }


def build_term_review_tasks(
    tables: dict[str, pd.DataFrame],
    policy: dict,
) -> list[dict]:
    """AMBIGUOUS case를 LLM term-level 의미 판정용 JSON 객체로 만든다."""
    resolution_policy = policy["entity_resolution"]
    semantic_policy = resolution_policy["semantic_review"]
    identifier_policy = resolution_policy["identifier_policy"]
    review_statuses = set(semantic_policy["term_task_link_statuses"])
    candidates = tables["source_record_candidates"]
    features = tables["source_candidate_features"]
    pairs = tables["source_candidate_pair_signals"]
    clusters = tables["canonical_alternative_clusters"]
    members = tables["canonical_cluster_members"]
    contexts = tables["problem_contexts"]
    context_column = ""
    if "extraction_text" in contexts.columns:
        context_column = "extraction_text"
    elif "full_text" in contexts.columns:
        context_column = "full_text"
    if not context_column:
        raise ValueError("problem_contexts에 extraction_text가 없습니다.")
    context_by_problem = {
        str(row["problem_id"]): str(row[context_column])
        for row in contexts.to_dict("records")
    }
    feature_by_candidate = {
        str(row["source_candidate_id"]): row
        for row in features.to_dict("records")
    }
    candidate_rows_by_case: dict[str, list[dict]] = {}
    for row in candidates.to_dict("records"):
        candidate_rows_by_case.setdefault(row["resolution_case_id"], []).append(
            row
        )
    pair_rows_by_case: dict[str, list[dict]] = {}
    for row in pairs.to_dict("records"):
        pair_rows_by_case.setdefault(row["resolution_case_id"], []).append(row)
    cluster_rows_by_case: dict[str, list[dict]] = {}
    for row in clusters.to_dict("records"):
        cluster_rows_by_case.setdefault(row["resolution_case_id"], []).append(
            row
        )
    member_ids_by_cluster: dict[str, list[str]] = {}
    for row in members.itertuples():
        member_ids_by_cluster.setdefault(
            row.canonical_alternative_id,
            [],
        ).append(row.source_candidate_id)

    tasks: list[dict] = []
    for case in tables["resolution_cases"].to_dict("records"):
        if case["link_status"] not in review_statuses:
            continue
        case_id = case["resolution_case_id"]
        case_candidates = sorted(
            candidate_rows_by_case.get(case_id, []),
            key=lambda row: int(row["candidate_rank"]),
        )
        if not case_candidates:
            continue
        candidate_items = [
            build_candidate_task_item(
                candidate,
                feature_by_candidate[candidate["source_candidate_id"]],
                semantic_policy,
            )
            for candidate in case_candidates
        ]
        code_alternatives = []
        for cluster in cluster_rows_by_case.get(case_id, []):
            code_alternatives.append(
                {
                    "canonical_alternative_id": cluster[
                        "canonical_alternative_id"
                    ],
                    "confidence_tier": cluster["confidence_tier"],
                    "merge_signals": loads(cluster["merge_signals_json"]),
                    "source_candidate_ids": sorted(
                        member_ids_by_cluster.get(
                            cluster["canonical_alternative_id"],
                            [],
                        )
                    ),
                }
            )
        relevant_pairs = []
        for pair in pair_rows_by_case.get(case_id, []):
            conflict_signals = loads(pair["conflict_signals_json"])
            merge_eligible = str(pair["merge_eligible"]).lower() == "true"
            if not merge_eligible and not conflict_signals:
                continue
            relevant_pairs.append(
                {
                    "left_source_candidate_id": pair[
                        "left_source_candidate_id"
                    ],
                    "right_source_candidate_id": pair[
                        "right_source_candidate_id"
                    ],
                    "signals": loads(pair["signal_dimensions_json"]),
                    "conflicts": conflict_signals,
                    "merge_eligible": merge_eligible,
                }
            )
        problem_ids = loads(case["problem_ids_json"])
        maximum_contexts = int(
            semantic_policy["maximum_problem_contexts_per_term_task"]
        )
        problem_contexts = [
            {
                "problem_id": problem_id,
                "full_text": context_by_problem.get(problem_id, ""),
            }
            for problem_id in problem_ids[:maximum_contexts]
        ]
        task_id = create_stable_id(
            identifier_policy["term_review_task_prefix"],
            [case_id, semantic_policy["prompt_version"]],
            identifier_policy,
        )
        task = {
            "term_review_task_id": task_id,
            "resolution_case_id": case_id,
            "canonical_term": case["canonical_term"],
            "term_variants": loads(case["term_variants_json"]),
            "category": case["category"],
            "entity_type_proposal": case["entity_type_proposal"],
            "problem_count": int(case["problem_count"]),
            "problem_context_samples": problem_contexts,
            "source_candidates": candidate_items,
            "code_canonical_alternatives": code_alternatives,
            "relevant_pair_signals": relevant_pairs,
            "required_decision_status": semantic_policy[
                "decision_status_input"
            ],
            "review_model": semantic_policy["term_model"]["model"],
            "prompt_version": semantic_policy["prompt_version"],
            "resolution_policy_version": policy["policy_version"],
        }
        related_entity_task_id = str(
            case.get("related_entity_task_id") or ""
        )
        related_entity_origin_json = str(
            case.get("related_entity_origin_json") or ""
        )
        if related_entity_task_id:
            task["related_entity_task_id"] = related_entity_task_id
        if related_entity_origin_json:
            task["related_entity_origin"] = loads(
                related_entity_origin_json
            )
        tasks.append(task)
    return tasks


def write_jsonl(records: list[dict], output_path: str) -> str:
    """중첩된 검토 task 또는 결정을 UTF-8 JSONL로 저장한다."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(dumps(record, ensure_ascii=False) + "\n")
    return str(destination)


def load_jsonl(input_path: str) -> list[dict]:
    """빈 줄을 제외하고 JSONL 객체 목록을 읽는다."""
    records: list[dict] = []
    with open(input_path, "r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            record = loads(stripped)
            if not isinstance(record, dict):
                raise ValueError(f"JSONL {line_number}행은 객체여야 합니다.")
            records.append(record)
    return records


def collect_classified_sources(
    decision: dict,
) -> tuple[dict[str, tuple[str, str, str]], list[str]]:
    """결정 객체를 candidate ID별 역할·대안·사유로 평탄화한다."""
    classified: dict[str, tuple[str, str, str]] = {}
    duplicate_ids: list[str] = []
    for alternative in decision.get("proposed_alternatives", []):
        member_ids = alternative.get(
            "identity_member_source_candidate_ids",
            [],
        )
        for candidate_id in member_ids:
            if candidate_id in classified:
                duplicate_ids.append(candidate_id)
                continue
            classified[candidate_id] = (
                "IDENTITY_MEMBER",
                "",
                str(alternative.get("reason") or ""),
            )
    role_fields = [
        ("evidence_only_sources", "EVIDENCE_ONLY"),
        ("rejected_sources", "REJECTED"),
        ("ambiguous_sources", "AMBIGUOUS"),
    ]
    for field_name, role in role_fields:
        for item in decision.get(field_name, []):
            candidate_id = str(item.get("source_candidate_id") or "")
            if candidate_id in classified:
                duplicate_ids.append(candidate_id)
                continue
            classified[candidate_id] = (
                role,
                "",
                str(item.get("reason") or ""),
            )
    return classified, duplicate_ids


def validate_decision_shape(decision: dict) -> list[str]:
    """외부 라이브러리 없이 핵심 JSON Schema 구조를 선검사한다."""
    messages: list[str] = []
    required_strings = [
        "term_review_task_id",
        "resolution_case_id",
        "decision_status",
        "review_model",
        "prompt_version",
        "decision_reason",
    ]
    required_arrays = [
        "proposed_alternatives",
        "evidence_only_sources",
        "rejected_sources",
        "ambiguous_sources",
    ]
    for field_name in required_strings:
        if not isinstance(decision.get(field_name), str) or not decision.get(
            field_name
        ):
            messages.append(f"{field_name}: 비어 있지 않은 문자열이 필요합니다.")
    for field_name in required_arrays:
        if not isinstance(decision.get(field_name), list):
            messages.append(f"{field_name}: 배열이 필요합니다.")
    if messages:
        return messages
    for alternative in decision["proposed_alternatives"]:
        if not isinstance(alternative, dict):
            messages.append("proposed_alternatives 항목은 객체여야 합니다.")
            continue
        member_ids = alternative.get("identity_member_source_candidate_ids")
        if not isinstance(member_ids, list) or not member_ids:
            messages.append("canonical 대안에는 비어 있지 않은 후보 ID 배열이 필요합니다.")
        if not isinstance(alternative.get("display_name"), str) or not alternative.get(
            "display_name"
        ):
            messages.append("canonical 대안 display_name이 필요합니다.")
        if not isinstance(alternative.get("entity_type"), str) or not alternative.get(
            "entity_type"
        ):
            messages.append("canonical 대안 entity_type이 필요합니다.")
        if not isinstance(alternative.get("reason"), str) or not alternative.get(
            "reason"
        ):
            messages.append("canonical 대안 reason이 필요합니다.")
    proposed_related_entities = decision.get("proposed_related_entities", [])
    if not isinstance(proposed_related_entities, list):
        messages.append("proposed_related_entities: 배열이 필요합니다.")
        proposed_related_entities = []
    for related_entity in proposed_related_entities:
        if not isinstance(related_entity, dict):
            messages.append("proposed_related_entities 항목은 객체여야 합니다.")
            continue
        evidence_ids = related_entity.get("evidence_source_candidate_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            messages.append(
                "관련 엔티티에는 비어 있지 않은 근거 후보 ID 배열이 필요합니다."
            )
        required_related_strings = [
            "related_entity_key",
            "display_name",
            "entity_type",
            "reason",
        ]
        for field_name in required_related_strings:
            if not isinstance(related_entity.get(field_name), str) or not (
                related_entity.get(field_name)
            ):
                messages.append(
                    f"관련 엔티티 {field_name}이 필요합니다."
                )
    for field_name in [
        "evidence_only_sources",
        "rejected_sources",
        "ambiguous_sources",
    ]:
        for item in decision[field_name]:
            if not isinstance(item, dict):
                messages.append(f"{field_name} 항목은 객체여야 합니다.")
                continue
            if not isinstance(item.get("source_candidate_id"), str) or not item.get(
                "source_candidate_id"
            ):
                messages.append(f"{field_name}.source_candidate_id가 필요합니다.")
            if not isinstance(item.get("reason"), str) or not item.get("reason"):
                messages.append(f"{field_name}.reason이 필요합니다.")
    return messages


def add_validation_error(
    errors: list[dict],
    decision_id: str,
    case_id: str,
    severity: str,
    error_code: str,
    message: str,
) -> None:
    """검증 오류를 감사 가능한 표준 행으로 추가한다."""
    errors.append(
        {
            "term_decision_id": decision_id,
            "resolution_case_id": case_id,
            "severity": severity,
            "error_code": error_code,
            "message": message,
        }
    )


def build_validation_tables_from_review_tasks(
    tasks: list[dict],
) -> dict[str, pd.DataFrame]:
    """review task에 포함된 후보·pair 신호로 검증 게이트 입력을 재구성한다."""
    candidate_by_id: dict[str, dict] = {}
    pair_rows: list[dict] = []
    for task in tasks:
        case_id = str(task["resolution_case_id"])
        task_candidates = task.get("source_candidates", [])
        for candidate in task_candidates:
            candidate_id = str(candidate["source_candidate_id"])
            candidate_by_id[candidate_id] = {
                "source_candidate_id": candidate_id,
                "source_record_id": str(candidate["source_record_id"]),
                "category_compatibility": str(
                    candidate.get("category_compatibility") or ""
                ),
            }
        relevant_pair_by_ids = {
            frozenset(
                [
                    str(pair["left_source_candidate_id"]),
                    str(pair["right_source_candidate_id"]),
                ]
            ): pair
            for pair in task.get("relevant_pair_signals", [])
        }
        candidate_ids = sorted(
            str(candidate["source_candidate_id"])
            for candidate in task_candidates
        )
        for left_id, right_id in combinations(candidate_ids, 2):
            pair = relevant_pair_by_ids.get(frozenset([left_id, right_id]))
            conflicts: list[str] = []
            merge_eligible = False
            if pair is not None:
                conflicts = list(pair.get("conflicts", []))
                merge_eligible = bool(pair.get("merge_eligible"))
            pair_rows.append(
                {
                    "resolution_case_id": case_id,
                    "left_source_candidate_id": left_id,
                    "right_source_candidate_id": right_id,
                    "conflict_signals_json": dumps(
                        conflicts,
                        ensure_ascii=False,
                    ),
                    "merge_eligible": merge_eligible,
                }
            )
    return {
        "source_record_candidates": pd.DataFrame(
            list(candidate_by_id.values()),
            columns=[
                "source_candidate_id",
                "source_record_id",
                "category_compatibility",
            ],
        ),
        "source_candidate_pair_signals": pd.DataFrame(
            pair_rows,
            columns=[
                "resolution_case_id",
                "left_source_candidate_id",
                "right_source_candidate_id",
                "conflict_signals_json",
                "merge_eligible",
            ],
        ),
    }


def collect_term_source_alignment_modes_by_member(
    task: dict,
    member_ids: list[str],
) -> dict[str, set[str]]:
    """입력 용어와 각 identity member 원천명 사이의 정합성을 계산한다."""
    term_values = [task.get("canonical_term", "")]
    term_values.extend(task.get("term_variants", []))
    normalized_terms: set[str] = set()
    for value in term_values:
        if not value:
            continue
        normalized_value = normalize_history_term(value)
        if normalized_value:
            normalized_terms.add(normalized_value)
    candidate_by_id = {
        str(candidate["source_candidate_id"]): candidate
        for candidate in task.get("source_candidates", [])
    }
    alignment_modes_by_member: dict[str, set[str]] = {}
    for member_id in member_ids:
        member_key = str(member_id)
        alignment_modes: set[str] = set()
        alignment_modes_by_member[member_key] = alignment_modes
        candidate = candidate_by_id.get(str(member_id))
        if candidate is None:
            continue
        candidate_values = [candidate.get("matched_name", "")]
        candidate_values.extend(candidate.get("normalized_names", []))
        normalized_candidate_names: set[str] = set()
        for value in candidate_values:
            if not value:
                continue
            normalized_value = normalize_history_term(value)
            if normalized_value:
                normalized_candidate_names.add(normalized_value)
        for term_name in normalized_terms:
            for candidate_name in normalized_candidate_names:
                if term_name == candidate_name:
                    alignment_modes.add("normalized_exact")
                    continue
                if term_name in candidate_name or candidate_name in term_name:
                    alignment_modes.add("bidirectional_containment")
                    continue
                if sorted(term_name) == sorted(candidate_name):
                    alignment_modes.add("character_multiset_match")
                    continue
                matched_character_count = 0
                for character in candidate_name:
                    if (
                        matched_character_count < len(term_name)
                        and character == term_name[matched_character_count]
                    ):
                        matched_character_count += 1
                if matched_character_count == len(term_name):
                    alignment_modes.add("ordered_subsequence_expansion")
    return alignment_modes_by_member


def collect_term_source_alignment_modes(
    task: dict,
    member_ids: list[str],
) -> set[str]:
    """하위 호환을 위해 identity member 전체의 정합성 모드를 합쳐 반환한다."""
    combined_modes: set[str] = set()
    modes_by_member = collect_term_source_alignment_modes_by_member(
        task,
        member_ids,
    )
    for alignment_modes in modes_by_member.values():
        combined_modes.update(alignment_modes)
    return combined_modes


def identity_members_have_connected_pair_evidence(
    member_ids: list[str],
    pair_by_ids: dict[frozenset[str], dict],
    anchor_member_ids: set[str] | None = None,
    supplemental_pair_ids: set[frozenset[str]] | None = None,
) -> bool:
    """강한 충돌 없는 양성 pair edge가 identity 멤버 전체를 연결하는지 확인한다."""
    normalized_member_ids = [str(member_id) for member_id in member_ids]
    normalized_anchor_ids: set[str] = set()
    if anchor_member_ids is None and normalized_member_ids:
        normalized_anchor_ids = {normalized_member_ids[0]}
    elif anchor_member_ids is not None:
        normalized_anchor_ids = {
            str(member_id)
            for member_id in anchor_member_ids
            if str(member_id) in normalized_member_ids
        }
        if not normalized_anchor_ids:
            return False
    if len(normalized_member_ids) < 2:
        return True
    adjacency = {
        member_id: set()
        for member_id in normalized_member_ids
    }
    for left_id, right_id in combinations(normalized_member_ids, 2):
        pair_ids = frozenset([left_id, right_id])
        if supplemental_pair_ids and pair_ids in supplemental_pair_ids:
            adjacency[left_id].add(right_id)
            adjacency[right_id].add(left_id)
            continue
        pair = pair_by_ids.get(pair_ids)
        if pair is None:
            continue
        conflicts = loads(pair["conflict_signals_json"])
        if conflicts:
            continue
        merge_eligible = str(pair["merge_eligible"]).lower() == "true"
        if not merge_eligible:
            continue
        adjacency[left_id].add(right_id)
        adjacency[right_id].add(left_id)

    visited: set[str] = set()
    pending = list(normalized_anchor_ids)
    while pending:
        member_id = pending.pop()
        if member_id in visited:
            continue
        visited.add(member_id)
        pending.extend(adjacency[member_id].difference(visited))
    return visited == set(normalized_member_ids)


def collect_exact_name_description_pair_evidence(
    task: dict,
    pair_by_ids: dict[frozenset[str], dict],
    identity_pair_gate_policy: dict,
) -> set[frozenset[str]]:
    """정확 이름과 거의 같은 정의가 있는 교차 원천 pair를 수집한다."""
    evidence_policy = identity_pair_gate_policy[
        "exact_name_description_evidence"
    ]
    if not bool(evidence_policy["enabled"]):
        return set()
    candidates = {
        str(candidate["source_candidate_id"]): candidate
        for candidate in task.get("source_candidates", [])
    }
    candidate_ids = sorted(candidates)
    alignment_modes_by_member = collect_term_source_alignment_modes_by_member(
        task,
        candidate_ids,
    )
    required_alignment_mode = str(
        evidence_policy["required_alignment_mode"]
    )
    blocked_compatibilities = set(
        evidence_policy["blocked_category_compatibilities"]
    )
    description_fields_by_source = evidence_policy[
        "description_fields_by_source"
    ]
    minimum_similarity = float(
        evidence_policy["minimum_description_similarity"]
    )
    evidence_pairs: set[frozenset[str]] = set()

    for left_id, right_id in combinations(candidate_ids, 2):
        pair_ids = frozenset([left_id, right_id])
        pair = pair_by_ids.get(pair_ids)
        if pair is None:
            continue
        if loads(pair["conflict_signals_json"]):
            continue
        left = candidates[left_id]
        right = candidates[right_id]
        if (
            bool(evidence_policy["require_distinct_sources"])
            and left.get("source") == right.get("source")
        ):
            continue
        if (
            str(left.get("category_compatibility") or "")
            in blocked_compatibilities
            or str(right.get("category_compatibility") or "")
            in blocked_compatibilities
        ):
            continue
        if (
            required_alignment_mode
            not in alignment_modes_by_member.get(left_id, set())
            or required_alignment_mode
            not in alignment_modes_by_member.get(right_id, set())
        ):
            continue
        left_names = {
            normalize_history_term(value)
            for value in left.get("normalized_names", [])
            if normalize_history_term(value)
        }
        right_names = {
            normalize_history_term(value)
            for value in right.get("normalized_names", [])
            if normalize_history_term(value)
        }
        if (
            bool(evidence_policy["require_shared_normalized_name"])
            and not left_names.intersection(right_names)
        ):
            continue
        descriptions: list[str] = []
        for candidate in [left, right]:
            source = str(candidate.get("source") or "")
            source_context = candidate.get("source_context", {})
            description = ""
            for field_name in description_fields_by_source.get(source, []):
                field_value = str(source_context.get(field_name) or "").strip()
                if field_value:
                    description = normalize_history_term(field_value)
                    break
            descriptions.append(description)
        if not all(descriptions):
            continue
        similarity = SequenceMatcher(
            None,
            descriptions[0],
            descriptions[1],
        ).ratio()
        if similarity < minimum_similarity:
            continue
        evidence_pairs.add(pair_ids)
    return evidence_pairs


def collect_strict_source_duplicate_pair_evidence(
    task: dict,
    pair_by_ids: dict[frozenset[str], dict],
    policy: dict,
) -> set[frozenset[str]]:
    """같은 출처의 메타데이터가 완전히 같은 레코드 pair를 수집한다."""
    candidate_groups: dict[str, list[str]] = {}
    for candidate in task.get("source_candidates", []):
        deduplication_keys = build_candidate_deduplication_keys(
            candidate,
            policy,
        )
        for deduplication_key in deduplication_keys:
            candidate_groups.setdefault(
                deduplication_key,
                [],
            ).append(str(candidate["source_candidate_id"]))

    evidence_pairs: set[frozenset[str]] = set()
    for candidate_ids in candidate_groups.values():
        for left_id, right_id in combinations(
            sorted(candidate_ids),
            2,
        ):
            pair_ids = frozenset([left_id, right_id])
            pair = pair_by_ids.get(pair_ids)
            if pair is None:
                continue
            if loads(pair["conflict_signals_json"]):
                continue
            evidence_pairs.add(pair_ids)
    return evidence_pairs


def collect_structured_identity_pair_evidence(
    task: dict,
    pair_by_ids: dict[frozenset[str], dict],
    identity_pair_gate_policy: dict,
) -> set[frozenset[str]]:
    """이름·한자·시대·정의가 일치하는 교차 원천 pair를 수집한다."""
    evidence_policy = identity_pair_gate_policy[
        "structured_identity_evidence"
    ]
    if not bool(evidence_policy["enabled"]):
        return set()
    candidates = {
        str(candidate["source_candidate_id"]): candidate
        for candidate in task.get("source_candidates", [])
    }
    candidate_ids = sorted(candidates)
    alignment_modes_by_member = collect_term_source_alignment_modes_by_member(
        task,
        candidate_ids,
    )
    required_alignment_mode = str(
        evidence_policy["required_alignment_mode"]
    )
    minimum_aligned_member_count = int(
        evidence_policy["minimum_aligned_member_count"]
    )
    blocked_compatibilities = set(
        evidence_policy["blocked_category_compatibilities"]
    )
    allowed_pair_conflicts = set(
        evidence_policy["allowed_pair_conflicts"]
    )
    description_fields_by_source = evidence_policy[
        "description_fields_by_source"
    ]
    minimum_similarity = float(
        evidence_policy["minimum_description_similarity"]
    )
    evidence_pairs: set[frozenset[str]] = set()

    for left_id, right_id in combinations(candidate_ids, 2):
        pair_ids = frozenset([left_id, right_id])
        pair = pair_by_ids.get(pair_ids)
        if pair is None:
            continue
        pair_conflicts = set(loads(pair["conflict_signals_json"]))
        if pair_conflicts.difference(allowed_pair_conflicts):
            continue
        left = candidates[left_id]
        right = candidates[right_id]
        if (
            bool(evidence_policy["require_distinct_sources"])
            and left.get("source") == right.get("source")
        ):
            continue
        if (
            str(left.get("category_compatibility") or "")
            in blocked_compatibilities
            or str(right.get("category_compatibility") or "")
            in blocked_compatibilities
        ):
            continue
        aligned_member_count = sum(
            required_alignment_mode
            in alignment_modes_by_member.get(candidate_id, set())
            for candidate_id in [left_id, right_id]
        )
        if aligned_member_count < minimum_aligned_member_count:
            continue
        left_names = {
            normalize_history_term(value)
            for value in left.get("normalized_names", [])
            if normalize_history_term(value)
        }
        right_names = {
            normalize_history_term(value)
            for value in right.get("normalized_names", [])
            if normalize_history_term(value)
        }
        if (
            bool(evidence_policy["require_shared_normalized_name"])
            and not left_names.intersection(right_names)
        ):
            continue
        left_hanja = {
            normalize_history_term(value)
            for value in left.get("hanja", [])
            if normalize_history_term(value)
        }
        right_hanja = {
            normalize_history_term(value)
            for value in right.get("hanja", [])
            if normalize_history_term(value)
        }
        if (
            bool(evidence_policy["require_hanja_match"])
            and not left_hanja.intersection(right_hanja)
        ):
            continue
        left_eras = {
            normalize_history_term(value)
            for value in left.get("era_values", [])
            if normalize_history_term(value)
        }
        right_eras = {
            normalize_history_term(value)
            for value in right.get("era_values", [])
            if normalize_history_term(value)
        }
        if (
            bool(evidence_policy["require_era_overlap"])
            and not left_eras.intersection(right_eras)
        ):
            continue
        descriptions: list[str] = []
        for candidate in [left, right]:
            source = str(candidate.get("source") or "")
            source_context = candidate.get("source_context", {})
            description = ""
            for field_name in description_fields_by_source.get(source, []):
                field_value = str(source_context.get(field_name) or "").strip()
                if field_value:
                    description = normalize_history_term(field_value)
                    break
            descriptions.append(description)
        if not all(descriptions):
            continue
        similarity = SequenceMatcher(
            None,
            descriptions[0],
            descriptions[1],
        ).ratio()
        if similarity < minimum_similarity:
            continue
        evidence_pairs.add(pair_ids)
    return evidence_pairs


def build_identity_pair_verification_rows(
    alternative_specs: list[tuple[dict, str]],
    task: dict | None,
    decision_id: str,
    decision_input_invalid: bool,
    manual_override: bool,
    candidate_rows: dict[str, dict],
    pair_by_ids: dict[frozenset[str], dict],
    exact_description_pair_ids: set[frozenset[str]],
    structured_identity_pair_ids: set[frozenset[str]],
    strict_duplicate_pair_ids: set[frozenset[str]],
    policy: dict,
) -> list[dict]:
    """case 상태와 분리해 모델이 제안한 identity pair를 검증한다."""
    if task is None:
        return []

    resolution_policy = policy["entity_resolution"]
    semantic_policy = resolution_policy["semantic_review"]
    gate_policy = semantic_policy["identity_pair_gate"]
    structured_policy = gate_policy["structured_identity_evidence"]
    automatic_alignment_modes = set(
        semantic_policy["term_source_alignment"][
            "automatic_acceptance_modes"
        ]
    )
    verification_methods = semantic_policy["verification_methods"]
    identifier_policy = resolution_policy["identifier_policy"]
    allowed_entity_types = set(
        resolution_policy["entity_type_mapping"].values()
    )
    allowed_pair_conflicts = set(
        structured_policy["allowed_pair_conflicts"]
    )
    allow_category_conflict = bool(
        structured_policy[
            "allow_category_conflict_when_entity_type_matches"
        ]
    )
    supplemental_pair_ids = (
        exact_description_pair_ids
        | structured_identity_pair_ids
        | strict_duplicate_pair_ids
    )
    rows: list[dict] = []

    for alternative, alternative_id in alternative_specs:
        member_ids = sorted(
            {
                str(candidate_id)
                for candidate_id in alternative[
                    "identity_member_source_candidate_ids"
                ]
            }
        )
        if len(member_ids) < 2:
            continue
        alignment_modes_by_member = (
            collect_term_source_alignment_modes_by_member(
                task,
                member_ids,
            )
        )
        aligned_member_ids = {
            member_id
            for member_id in member_ids
            if alignment_modes_by_member.get(member_id, set()).intersection(
                automatic_alignment_modes
            )
        }
        adjacency = {member_id: set() for member_id in member_ids}
        error_codes_by_pair: dict[frozenset[str], set[str]] = {}
        direct_evidence_pairs: set[frozenset[str]] = set()
        fatal_error_codes = {
            "CATEGORY_CONFLICT_IDENTITY_MEMBER",
            "INVALID_DECISION_INPUT",
            "INVALID_ENTITY_TYPE",
            "MISSING_PAIR_EVIDENCE",
            "STRONG_PAIR_CONFLICT",
            "UNKNOWN_SOURCE_CANDIDATE",
        }

        for left_id, right_id in combinations(member_ids, 2):
            pair_ids = frozenset([left_id, right_id])
            error_codes: set[str] = set()
            error_codes_by_pair[pair_ids] = error_codes
            left_candidate = candidate_rows.get(left_id)
            right_candidate = candidate_rows.get(right_id)
            pair = pair_by_ids.get(pair_ids)

            if decision_input_invalid:
                error_codes.add("INVALID_DECISION_INPUT")
            if alternative["entity_type"] not in allowed_entity_types:
                error_codes.add("INVALID_ENTITY_TYPE")
            if left_candidate is None or right_candidate is None:
                error_codes.add("UNKNOWN_SOURCE_CANDIDATE")
            if pair is None:
                error_codes.add("MISSING_PAIR_EVIDENCE")

            structured_pair = pair_ids in structured_identity_pair_ids
            category_override = (
                structured_pair
                and allow_category_conflict
                and alternative["entity_type"]
                == task["entity_type_proposal"]
            )
            if left_candidate is not None and (
                left_candidate["category_compatibility"] == "CONFLICT"
                and not category_override
            ):
                error_codes.add("CATEGORY_CONFLICT_IDENTITY_MEMBER")
            if right_candidate is not None and (
                right_candidate["category_compatibility"] == "CONFLICT"
                and not category_override
            ):
                error_codes.add("CATEGORY_CONFLICT_IDENTITY_MEMBER")

            merge_eligible = False
            if pair is not None:
                conflicts = set(loads(pair["conflict_signals_json"]))
                conflict_override = (
                    structured_pair
                    and conflicts.issubset(allowed_pair_conflicts)
                )
                if conflicts and not conflict_override:
                    error_codes.add("STRONG_PAIR_CONFLICT")
                merge_eligible = (
                    str(pair["merge_eligible"]).lower() == "true"
                )
            has_pair_evidence = (
                merge_eligible or pair_ids in supplemental_pair_ids
            )
            if not has_pair_evidence:
                error_codes.add("INSUFFICIENT_PAIR_EVIDENCE")
            if error_codes.intersection(fatal_error_codes):
                continue
            if not has_pair_evidence:
                continue
            adjacency[left_id].add(right_id)
            adjacency[right_id].add(left_id)
            direct_evidence_pairs.add(pair_ids)

        verified_pairs: set[frozenset[str]] = set()
        evidence_mode_by_pair: dict[frozenset[str], str] = {}
        unvisited = set(member_ids)
        while unvisited:
            pending = [next(iter(unvisited))]
            component: set[str] = set()
            while pending:
                member_id = pending.pop()
                if member_id in component:
                    continue
                component.add(member_id)
                unvisited.discard(member_id)
                pending.extend(adjacency[member_id].difference(component))
            if len(component) < 2:
                continue
            component_pairs = {
                frozenset([left_id, right_id])
                for left_id, right_id in combinations(
                    sorted(component),
                    2,
                )
            }
            component_has_fatal_conflict = any(
                error_codes_by_pair[pair_ids].intersection(
                    fatal_error_codes
                )
                for pair_ids in component_pairs
            )
            if not component_has_fatal_conflict and component.intersection(
                aligned_member_ids
            ):
                verified_pairs.update(component_pairs)
                for pair_ids in component_pairs:
                    evidence_mode_by_pair[pair_ids] = "CONNECTED_GRAPH"
                for pair_ids in component_pairs.intersection(
                    direct_evidence_pairs
                ):
                    evidence_mode_by_pair[pair_ids] = "DIRECT_PAIR"
                continue
            for pair_ids in component_pairs.intersection(
                direct_evidence_pairs
            ):
                if not pair_ids.intersection(aligned_member_ids):
                    continue
                verified_pairs.add(pair_ids)
                evidence_mode_by_pair[pair_ids] = "DIRECT_PAIR"

        for left_id, right_id in combinations(member_ids, 2):
            pair_ids = frozenset([left_id, right_id])
            error_codes = set(error_codes_by_pair[pair_ids])
            verification_status = "NEEDS_MANUAL_REVIEW"
            verification_method = verification_methods["pending"]
            if manual_override and not decision_input_invalid:
                verification_status = "VERIFIED"
                verification_method = verification_methods["human"]
                error_codes.clear()
                evidence_mode_by_pair[pair_ids] = "HUMAN_REVIEW"
            elif pair_ids in verified_pairs:
                verification_status = "VERIFIED"
                verification_method = verification_methods["automatic"]
                error_codes.clear()
            elif error_codes.intersection(fatal_error_codes):
                verification_status = "INVALID"
                verification_method = verification_methods["invalid"]
            if (
                verification_status != "VERIFIED"
                and not pair_ids.intersection(aligned_member_ids)
            ):
                error_codes.add("TERM_SOURCE_ALIGNMENT_REVIEW_REQUIRED")
            pair_candidate_ids = sorted(pair_ids)
            pair_id = create_stable_id(
                identifier_policy["source_candidate_pair_prefix"],
                [task["resolution_case_id"], *pair_candidate_ids],
                identifier_policy,
            )
            rows.append(
                {
                    "source_candidate_pair_id": pair_id,
                    "term_decision_id": decision_id,
                    "term_review_task_id": task["term_review_task_id"],
                    "resolution_case_id": task["resolution_case_id"],
                    "canonical_alternative_id": alternative_id,
                    "left_source_candidate_id": pair_candidate_ids[0],
                    "right_source_candidate_id": pair_candidate_ids[1],
                    "verification_status": verification_status,
                    "verification_method": verification_method,
                    "evidence_mode": evidence_mode_by_pair.get(
                        pair_ids,
                        "",
                    ),
                    "error_codes_json": dumps(
                        sorted(error_codes),
                        ensure_ascii=False,
                    ),
                    "identity_pair_gate_policy_version": gate_policy[
                        "policy_version"
                    ],
                    "resolution_policy_version": policy["policy_version"],
                }
            )
    return rows


def collect_verified_identity_components(
    alternative_specs: list[tuple[dict, str]],
    pair_verification_rows: list[dict],
    minimum_member_count: int,
) -> list[tuple[dict, list[str]]]:
    """자동 검증된 pair로 연결된 안전한 identity 하위 집합을 찾는다."""
    alternative_by_id = {
        alternative_id: alternative
        for alternative, alternative_id in alternative_specs
    }
    adjacency_by_alternative: dict[str, dict[str, set[str]]] = {}
    for pair_row in pair_verification_rows:
        if pair_row["verification_status"] != "VERIFIED":
            continue
        alternative_id = str(pair_row["canonical_alternative_id"])
        if alternative_id not in alternative_by_id:
            continue
        left_id = str(pair_row["left_source_candidate_id"])
        right_id = str(pair_row["right_source_candidate_id"])
        adjacency = adjacency_by_alternative.setdefault(
            alternative_id,
            {},
        )
        adjacency.setdefault(left_id, set()).add(right_id)
        adjacency.setdefault(right_id, set()).add(left_id)

    components: list[tuple[dict, list[str]]] = []
    for alternative_id, adjacency in adjacency_by_alternative.items():
        visited: set[str] = set()
        for start_id in sorted(adjacency):
            if start_id in visited:
                continue
            pending = [start_id]
            component: set[str] = set()
            while pending:
                candidate_id = pending.pop()
                if candidate_id in component:
                    continue
                component.add(candidate_id)
                pending.extend(adjacency.get(candidate_id, set()))
            visited.update(component)
            if len(component) >= minimum_member_count:
                components.append(
                    (
                        alternative_by_id[alternative_id],
                        sorted(component),
                    )
                )
    return components


def validate_term_decisions(
    decisions: list[dict],
    tasks: list[dict],
    tables: dict[str, pd.DataFrame],
    policy: dict,
    manual_verifications: dict[str, dict] | None = None,
) -> dict[str, pd.DataFrame]:
    """LLM의 PROPOSED term 결정을 검증하고 VERIFIED 결과만 평탄화한다."""
    resolution_policy = policy["entity_resolution"]
    semantic_policy = resolution_policy["semantic_review"]
    identifier_policy = resolution_policy["identifier_policy"]
    term_alignment_policy = semantic_policy["term_source_alignment"]
    identity_pair_gate_policy = semantic_policy["identity_pair_gate"]
    pair_evidence_modes = identity_pair_gate_policy["evidence_modes"]
    pair_evidence_mode = identity_pair_gate_policy[
        "active_evidence_mode"
    ]
    if pair_evidence_mode not in set(pair_evidence_modes.values()):
        raise ValueError(
            "지원하지 않는 identity pair evidence mode입니다: "
            f"{pair_evidence_mode}"
        )
    automatic_alignment_modes = set(
        term_alignment_policy["automatic_acceptance_modes"]
    )
    verification_methods = semantic_policy["verification_methods"]
    manual_verification_by_case = manual_verifications or {}
    task_by_id = {task["term_review_task_id"]: task for task in tasks}
    candidate_rows = {
        str(row["source_candidate_id"]): row
        for row in tables["source_record_candidates"].to_dict("records")
    }
    pair_by_ids = {
        frozenset(
            [row["left_source_candidate_id"], row["right_source_candidate_id"]]
        ): row
        for row in tables["source_candidate_pair_signals"].to_dict("records")
    }
    exact_description_pair_ids: set[frozenset[str]] = set()
    structured_identity_pair_ids: set[frozenset[str]] = set()
    strict_duplicate_pair_ids: set[frozenset[str]] = set()
    for review_task in tasks:
        exact_description_pair_ids.update(
            collect_exact_name_description_pair_evidence(
                review_task,
                pair_by_ids,
                identity_pair_gate_policy,
            )
        )
        structured_identity_pair_ids.update(
            collect_structured_identity_pair_evidence(
                review_task,
                pair_by_ids,
                identity_pair_gate_policy,
            )
        )
        strict_duplicate_pair_ids.update(
            collect_strict_source_duplicate_pair_evidence(
                review_task,
                pair_by_ids,
                policy,
            )
        )
    supplemental_pair_ids = (
        exact_description_pair_ids
        | structured_identity_pair_ids
        | strict_duplicate_pair_ids
    )
    decision_rows: list[dict] = []
    alternative_rows: list[dict] = []
    role_rows: list[dict] = []
    pair_verification_rows: list[dict] = []
    error_rows: list[dict] = []
    observed_task_ids: set[str] = set()

    for decision_sequence, decision in enumerate(decisions, start=1):
        task_id = str(decision.get("term_review_task_id") or "")
        case_id = str(decision.get("resolution_case_id") or "")
        manual_verification = manual_verification_by_case.get(case_id, {})
        manual_override = bool(manual_verification)
        decision_id = create_stable_id(
            identifier_policy["term_decision_prefix"],
            [
                task_id,
                semantic_policy["prompt_version"],
                str(decision_sequence),
            ],
            identifier_policy,
        )
        invalid = False
        manual_review = False
        shape_errors = validate_decision_shape(decision)
        for shape_error in shape_errors:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "DECISION_SCHEMA_ERROR",
                shape_error,
            )
        if shape_errors:
            invalid = True
        task = task_by_id.get(task_id)
        if task is None:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "UNKNOWN_TERM_REVIEW_TASK",
                "등록되지 않은 term review task입니다.",
            )
            invalid = True
        elif task_id in observed_task_ids:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "DUPLICATE_TERM_DECISION",
                "동일 task에 대한 결정이 중복되었습니다.",
            )
            invalid = True
        elif task_id not in observed_task_ids:
            observed_task_ids.add(task_id)

        expected_candidate_ids: set[str] = set()
        if task is not None:
            expected_candidate_ids = {
                item["source_candidate_id"]
                for item in task["source_candidates"]
            }
            if case_id != task["resolution_case_id"]:
                add_validation_error(
                    error_rows,
                    decision_id,
                    case_id,
                    "INVALID",
                    "CASE_ID_MISMATCH",
                    "task와 결정의 resolution_case_id가 다릅니다.",
                )
                invalid = True
        if decision.get("decision_status") != semantic_policy[
            "decision_status_input"
        ]:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "INVALID_DECISION_STATUS",
                "LLM 입력 결정 상태는 PROPOSED여야 합니다.",
            )
            invalid = True
        if decision.get("prompt_version") != semantic_policy["prompt_version"]:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "PROMPT_VERSION_MISMATCH",
                "task와 결정의 prompt version이 다릅니다.",
            )
            invalid = True
        allowed_review_models = {
            semantic_policy["term_model"]["model"],
            semantic_policy["deterministic_triage"]["review_model"],
        }
        if decision.get("review_model") not in allowed_review_models:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "REVIEW_MODEL_MISMATCH",
                "정책에 지정된 term review 방식이 아닙니다.",
            )
            invalid = True

        classified: dict[str, tuple[str, str, str]] = {}
        duplicate_ids: list[str] = []
        if not shape_errors:
            classified, duplicate_ids = collect_classified_sources(decision)
        if duplicate_ids:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "DUPLICATE_CANDIDATE_CLASSIFICATION",
                dumps(sorted(set(duplicate_ids)), ensure_ascii=False),
            )
            invalid = True
        classified_ids = set(classified)
        unknown_ids = classified_ids.difference(expected_candidate_ids)
        missing_ids = expected_candidate_ids.difference(classified_ids)
        if unknown_ids:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "UNKNOWN_SOURCE_CANDIDATE",
                dumps(sorted(unknown_ids), ensure_ascii=False),
            )
            invalid = True
        if missing_ids:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "MISSING_CANDIDATE_CLASSIFICATION",
                dumps(sorted(missing_ids), ensure_ascii=False),
            )
            invalid = True

        decision_input_invalid = invalid
        alternative_specs: list[tuple[dict, str]] = []
        proposed_alternatives = decision.get("proposed_alternatives", [])
        if not isinstance(proposed_alternatives, list):
            proposed_alternatives = []
        if shape_errors:
            proposed_alternatives = []
        for alternative in proposed_alternatives:
            if not isinstance(alternative, dict):
                continue
            member_ids = sorted(
                alternative.get("identity_member_source_candidate_ids", [])
            )
            if not member_ids:
                add_validation_error(
                    error_rows,
                    decision_id,
                    case_id,
                    "INVALID",
                    "EMPTY_CANONICAL_ALTERNATIVE",
                    "canonical 대안에는 SourceRecord가 한 건 이상 필요합니다.",
                )
                invalid = True
                continue
            alternative_id = create_stable_id(
                identifier_policy["canonical_alternative_prefix"],
                [case_id] + member_ids,
                identifier_policy,
            )
            alternative_specs.append((alternative, alternative_id))
            structured_evidence_connects_alternative = (
                len(member_ids) > 1
                and identity_members_have_connected_pair_evidence(
                    member_ids,
                    {},
                    supplemental_pair_ids=structured_identity_pair_ids,
                )
            )
            allow_category_conflict = bool(
                identity_pair_gate_policy[
                    "structured_identity_evidence"
                ]["allow_category_conflict_when_entity_type_matches"]
            )
            category_conflict_override = (
                structured_evidence_connects_alternative
                and allow_category_conflict
                and task is not None
                and alternative["entity_type"]
                == task["entity_type_proposal"]
            )
            alignment_modes_by_member: dict[str, set[str]] = {}
            if task is not None:
                alignment_modes_by_member = (
                    collect_term_source_alignment_modes_by_member(
                        task,
                        member_ids,
                    )
                )
            members_requiring_alignment_review: list[str] = []
            require_each_member = bool(
                term_alignment_policy["require_each_identity_member"]
            )
            if require_each_member:
                members_requiring_alignment_review = [
                    member_id
                    for member_id in member_ids
                    if not alignment_modes_by_member.get(
                        str(member_id),
                        set(),
                    ).intersection(automatic_alignment_modes)
                ]
            elif not any(
                alignment_modes.intersection(automatic_alignment_modes)
                for alignment_modes in alignment_modes_by_member.values()
            ):
                members_requiring_alignment_review = list(member_ids)
            allow_connected_members = bool(
                term_alignment_policy[
                    "allow_connected_members_from_automatic_anchor"
                ]
            )
            if (
                members_requiring_alignment_review
                and allow_connected_members
            ):
                automatically_aligned_member_ids = {
                    str(member_id)
                    for member_id in member_ids
                    if alignment_modes_by_member.get(
                        str(member_id),
                        set(),
                    ).intersection(automatic_alignment_modes)
                }
                if identity_members_have_connected_pair_evidence(
                    member_ids,
                    pair_by_ids,
                    anchor_member_ids=automatically_aligned_member_ids,
                    supplemental_pair_ids=supplemental_pair_ids,
                ):
                    members_requiring_alignment_review = []
            if (
                task is not None
                and members_requiring_alignment_review
                and not manual_override
            ):
                observed_modes = {
                    member_id: sorted(
                        alignment_modes_by_member.get(member_id, set())
                    )
                    for member_id in members_requiring_alignment_review
                }
                add_validation_error(
                    error_rows,
                    decision_id,
                    case_id,
                    "NEEDS_MANUAL_REVIEW",
                    "TERM_SOURCE_ALIGNMENT_REVIEW_REQUIRED",
                    dumps(
                        {
                            "canonical_term": task["canonical_term"],
                            "identity_member_source_candidate_ids": member_ids,
                            "members_requiring_review": (
                                members_requiring_alignment_review
                            ),
                            "observed_alignment_modes": observed_modes,
                        },
                        ensure_ascii=False,
                    ),
                )
                manual_review = True
            allowed_entity_types = set(
                resolution_policy["entity_type_mapping"].values()
            )
            alternative_entity_type = alternative["entity_type"]
            if alternative_entity_type not in allowed_entity_types:
                add_validation_error(
                    error_rows,
                    decision_id,
                    case_id,
                    "INVALID",
                    "INVALID_ENTITY_TYPE",
                    alternative_entity_type,
                )
                invalid = True
            if task is not None and task["entity_type_proposal"]:
                if (
                    alternative_entity_type != task["entity_type_proposal"]
                    and not manual_override
                ):
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "NEEDS_MANUAL_REVIEW",
                        "ENTITY_TYPE_REVIEW_REQUIRED",
                        alternative_entity_type,
                    )
                    manual_review = True
            for candidate_id in member_ids:
                candidate = candidate_rows.get(candidate_id)
                if candidate is None:
                    continue
                if (
                    candidate["category_compatibility"] == "CONFLICT"
                    and not category_conflict_override
                ):
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "INVALID",
                        "CATEGORY_CONFLICT_IDENTITY_MEMBER",
                        candidate_id,
                    )
                    invalid = True
            alternative_has_invalid_pair = False
            for left_id, right_id in combinations(member_ids, 2):
                pair = pair_by_ids.get(frozenset([left_id, right_id]))
                if pair is None:
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "INVALID",
                        "MISSING_PAIR_EVIDENCE",
                        f"{left_id},{right_id}",
                    )
                    invalid = True
                    alternative_has_invalid_pair = True
                    continue
                conflicts = loads(pair["conflict_signals_json"])
                allowed_pair_conflicts = set(
                    identity_pair_gate_policy[
                        "structured_identity_evidence"
                    ]["allowed_pair_conflicts"]
                )
                strong_evidence_override = (
                    structured_evidence_connects_alternative
                    and frozenset([left_id, right_id])
                    in structured_identity_pair_ids
                    and set(conflicts).issubset(allowed_pair_conflicts)
                )
                if conflicts and not strong_evidence_override:
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "INVALID",
                        "STRONG_PAIR_CONFLICT",
                        dumps(conflicts, ensure_ascii=False),
                    )
                    invalid = True
                    alternative_has_invalid_pair = True
                    continue
                if (
                    pair_evidence_mode == pair_evidence_modes["complete"]
                    and str(pair["merge_eligible"]).lower() != "true"
                    and frozenset([left_id, right_id])
                    not in strict_duplicate_pair_ids
                    and not manual_override
                ):
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "NEEDS_MANUAL_REVIEW",
                        "INSUFFICIENT_PAIR_EVIDENCE",
                        f"{left_id},{right_id}",
                    )
                    manual_review = True
            if (
                pair_evidence_mode == pair_evidence_modes["connected"]
                and not alternative_has_invalid_pair
                and not identity_members_have_connected_pair_evidence(
                    member_ids,
                    pair_by_ids,
                    supplemental_pair_ids=supplemental_pair_ids,
                )
                and not manual_override
            ):
                add_validation_error(
                    error_rows,
                    decision_id,
                    case_id,
                    "NEEDS_MANUAL_REVIEW",
                    "INSUFFICIENT_PAIR_EVIDENCE",
                    dumps(
                        {
                            "evidence_mode": pair_evidence_mode,
                            "identity_member_source_candidate_ids": (
                                member_ids
                            ),
                        },
                        ensure_ascii=False,
                    ),
                )
                manual_review = True

        decision_pair_rows = build_identity_pair_verification_rows(
            alternative_specs,
            task,
            decision_id,
            decision_input_invalid,
            manual_override,
            candidate_rows,
            pair_by_ids,
            exact_description_pair_ids,
            structured_identity_pair_ids,
            strict_duplicate_pair_ids,
            policy,
        )
        pair_verification_rows.extend(decision_pair_rows)
        if isinstance(decision.get("ambiguous_sources"), list) and decision.get(
            "ambiguous_sources"
        ):
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "NEEDS_MANUAL_REVIEW",
                "AMBIGUOUS_SOURCE_REMAINS",
                "AMBIGUOUS 후보가 남아 있습니다.",
            )
            manual_review = True
        verification_status = "VERIFIED"
        if manual_review:
            verification_status = "NEEDS_MANUAL_REVIEW"
        if invalid:
            verification_status = "INVALID"

        verification_method = verification_methods["automatic"]
        verified_by = ""
        verified_at = ""
        if verification_status == "VERIFIED" and manual_override:
            verification_method = verification_methods["human"]
            verified_by = str(manual_verification.get("reviewer") or "")
            verified_at = str(manual_verification.get("reviewed_at") or "")
        elif verification_status == "NEEDS_MANUAL_REVIEW":
            verification_method = verification_methods["pending"]
        elif verification_status == "INVALID":
            verification_method = verification_methods["invalid"]

        evidence_only_sources = decision.get("evidence_only_sources")
        rejected_sources = decision.get("rejected_sources")
        ambiguous_sources = decision.get("ambiguous_sources")
        evidence_only_count = 0
        rejected_count = 0
        ambiguous_count = 0
        if isinstance(evidence_only_sources, list):
            evidence_only_count = len(evidence_only_sources)
        if isinstance(rejected_sources, list):
            rejected_count = len(rejected_sources)
        if isinstance(ambiguous_sources, list):
            ambiguous_count = len(ambiguous_sources)
        decision_rows.append(
            {
                "term_decision_id": decision_id,
                "term_review_task_id": task_id,
                "resolution_case_id": case_id,
                "input_decision_status": decision.get("decision_status", ""),
                "verification_status": verification_status,
                "alternative_count": len(alternative_specs),
                "evidence_only_count": evidence_only_count,
                "rejected_count": rejected_count,
                "ambiguous_count": ambiguous_count,
                "decision_reason": decision.get("decision_reason", ""),
                "review_model": decision.get("review_model", ""),
                "prompt_version": decision.get("prompt_version", ""),
                "verification_method": verification_method,
                "verified_by": verified_by,
                "verified_at": verified_at,
                "resolution_policy_version": policy["policy_version"],
            }
        )
        if verification_status == "NEEDS_MANUAL_REVIEW":
            minimum_member_count = int(
                resolution_policy["canonical_registry"][
                    "minimum_automatic_identity_members"
                ]
            )
            verified_components = collect_verified_identity_components(
                alternative_specs,
                decision_pair_rows,
                minimum_member_count,
            )
            for alternative, member_ids in verified_components:
                component_alternative_id = create_stable_id(
                    identifier_policy["canonical_alternative_prefix"],
                    [case_id] + member_ids,
                    identifier_policy,
                )
                source_record_ids = [
                    candidate_rows[candidate_id]["source_record_id"]
                    for candidate_id in member_ids
                ]
                alternative_rows.append(
                    {
                        "canonical_alternative_id": (
                            component_alternative_id
                        ),
                        "resolution_case_id": case_id,
                        "canonical_id": "",
                        "display_name_proposal": alternative["display_name"],
                        "entity_type_proposal": alternative["entity_type"],
                        "source_candidate_ids_json": dumps(
                            member_ids,
                            ensure_ascii=False,
                        ),
                        "identity_member_source_ids_json": dumps(
                            source_record_ids,
                            ensure_ascii=False,
                        ),
                        "member_count": len(member_ids),
                        "merge_gate_passed": True,
                        "verification_status": "VERIFIED",
                        "verification_method": verification_methods[
                            "automatic"
                        ],
                        "verified_by": "",
                        "verified_at": "",
                        "term_decision_id": decision_id,
                        "decision_reason": (
                            f"{alternative['reason']} "
                            "전체 후보 판정은 보류했지만 자동 검증된 "
                            "identity pair 연결 성분만 먼저 승인했다."
                        ),
                        "resolution_policy_version": policy[
                            "policy_version"
                        ],
                    }
                )
                for candidate_id in member_ids:
                    role_rows.append(
                        {
                            "source_candidate_id": candidate_id,
                            "source_record_id": candidate_rows[
                                candidate_id
                            ]["source_record_id"],
                            "resolution_case_id": case_id,
                            "canonical_alternative_id": (
                                component_alternative_id
                            ),
                            "verified_role": "IDENTITY_MEMBER",
                            "verification_status": "VERIFIED",
                            "verification_method": verification_methods[
                                "automatic"
                            ],
                            "verified_by": "",
                            "verified_at": "",
                            "term_decision_id": decision_id,
                            "role_reason": classified[candidate_id][2],
                            "resolution_policy_version": policy[
                                "policy_version"
                            ],
                        }
                    )
        if verification_status != "VERIFIED":
            continue

        alternative_by_candidate: dict[str, str] = {}
        for alternative, alternative_id in alternative_specs:
            member_ids = sorted(
                alternative["identity_member_source_candidate_ids"]
            )
            source_record_ids = [
                candidate_rows[candidate_id]["source_record_id"]
                for candidate_id in member_ids
            ]
            alternative_rows.append(
                {
                    "canonical_alternative_id": alternative_id,
                    "resolution_case_id": case_id,
                    "canonical_id": "",
                    "display_name_proposal": alternative["display_name"],
                    "entity_type_proposal": alternative["entity_type"],
                    "source_candidate_ids_json": dumps(
                        member_ids,
                        ensure_ascii=False,
                    ),
                    "identity_member_source_ids_json": dumps(
                        source_record_ids,
                        ensure_ascii=False,
                    ),
                    "member_count": len(member_ids),
                    "merge_gate_passed": True,
                    "verification_status": "VERIFIED",
                    "verification_method": verification_method,
                    "verified_by": verified_by,
                    "verified_at": verified_at,
                    "term_decision_id": decision_id,
                    "decision_reason": alternative["reason"],
                    "resolution_policy_version": policy["policy_version"],
                }
            )
            for candidate_id in member_ids:
                alternative_by_candidate[candidate_id] = alternative_id
        for candidate_id in sorted(expected_candidate_ids):
            role, _, role_reason = classified[candidate_id]
            role_rows.append(
                {
                    "source_candidate_id": candidate_id,
                    "source_record_id": candidate_rows[candidate_id][
                        "source_record_id"
                    ],
                    "resolution_case_id": case_id,
                    "canonical_alternative_id": alternative_by_candidate.get(
                        candidate_id,
                        "",
                    ),
                    "verified_role": role,
                    "verification_status": "VERIFIED",
                    "verification_method": verification_method,
                    "verified_by": verified_by,
                    "verified_at": verified_at,
                    "term_decision_id": decision_id,
                    "role_reason": role_reason,
                    "resolution_policy_version": policy["policy_version"],
                }
            )

    output_columns = {
        "term_resolution_decisions": [
            "term_decision_id",
            "term_review_task_id",
            "resolution_case_id",
            "input_decision_status",
            "verification_status",
            "alternative_count",
            "evidence_only_count",
            "rejected_count",
            "ambiguous_count",
            "decision_reason",
            "review_model",
            "prompt_version",
            "verification_method",
            "verified_by",
            "verified_at",
            "resolution_policy_version",
        ],
        "reviewed_canonical_alternatives": [
            "canonical_alternative_id",
            "resolution_case_id",
            "canonical_id",
            "display_name_proposal",
            "entity_type_proposal",
            "source_candidate_ids_json",
            "identity_member_source_ids_json",
            "member_count",
            "merge_gate_passed",
            "verification_status",
            "verification_method",
            "verified_by",
            "verified_at",
            "term_decision_id",
            "decision_reason",
            "resolution_policy_version",
        ],
        "reviewed_source_roles": [
            "source_candidate_id",
            "source_record_id",
            "resolution_case_id",
            "canonical_alternative_id",
            "verified_role",
            "verification_status",
            "verification_method",
            "verified_by",
            "verified_at",
            "term_decision_id",
            "role_reason",
            "resolution_policy_version",
        ],
        "verified_identity_pairs": [
            "source_candidate_pair_id",
            "term_decision_id",
            "term_review_task_id",
            "resolution_case_id",
            "canonical_alternative_id",
            "left_source_candidate_id",
            "right_source_candidate_id",
            "verification_status",
            "verification_method",
            "evidence_mode",
            "error_codes_json",
            "identity_pair_gate_policy_version",
            "resolution_policy_version",
        ],
        "term_decision_validation_errors": [
            "term_decision_id",
            "resolution_case_id",
            "severity",
            "error_code",
            "message",
        ],
    }
    row_sets = {
        "term_resolution_decisions": decision_rows,
        "reviewed_canonical_alternatives": alternative_rows,
        "reviewed_source_roles": role_rows,
        "verified_identity_pairs": pair_verification_rows,
        "term_decision_validation_errors": error_rows,
    }
    return {
        table_name: pd.DataFrame(
            row_sets[table_name],
            columns=columns,
        )
        for table_name, columns in output_columns.items()
    }


def write_term_decision_tables(
    tables: dict[str, pd.DataFrame],
    output_dir: str,
    policy: dict,
) -> dict[str, str]:
    """term decision gate 결과를 정책 파일명으로 저장한다."""
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_files = policy["entity_resolution"]["semantic_review"][
        "term_decision_output_files"
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
        description="Entity Resolution term-level 의미 판정 task 생성·결정 검증"
    )
    parser.add_argument("input_dir", help="ER staging CSV 폴더")
    parser.add_argument("output_dir", help="review task·결정 출력 폴더")
    parser.add_argument(
        "--decisions",
        default="",
        help="검증할 term identity model decision JSONL 경로",
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
    resolution_tables = load_resolution_package(
        cli_args.input_dir,
        pipeline_policy,
    )
    review_tasks = build_term_review_tasks(
        resolution_tables,
        pipeline_policy,
    )
    semantic_policy = pipeline_policy["entity_resolution"]["semantic_review"]
    task_path = Path(cli_args.output_dir) / semantic_policy["term_task_file"]
    write_jsonl(review_tasks, str(task_path))
    print(f"term review task: {len(review_tasks)}건, {task_path}")
    if cli_args.decisions:
        proposed_decisions = load_jsonl(cli_args.decisions)
        decision_tables = validate_term_decisions(
            proposed_decisions,
            review_tasks,
            resolution_tables,
            pipeline_policy,
        )
        output_paths = write_term_decision_tables(
            decision_tables,
            cli_args.output_dir,
            pipeline_policy,
        )
        print(dumps(output_paths, ensure_ascii=False, indent=2))

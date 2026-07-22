import re
from itertools import combinations
from json import dumps, loads

import pandas as pd

from entity_resolution.identifiers import create_stable_id
from prep_thesaurus import build_match_key


def rows_to_dataframe(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    """빈 결과에서도 CSV 계약 컬럼을 유지한다."""
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=columns)


def parse_source_metadata(serialized: object) -> dict:
    """후보 행에 직렬화된 원천 메타데이터를 객체로 복원한다."""
    if isinstance(serialized, dict):
        return serialized
    if serialized is None or pd.isna(serialized):
        return {}
    text = str(serialized).strip()
    if not text:
        return {}
    parsed = loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("source_metadata_json은 JSON 객체여야 합니다.")
    return parsed


def collect_field_values(metadata: dict, field_names: list[str]) -> list[str]:
    """정책에 명시된 메타데이터 필드에서 중복 없는 문자열 값을 수집한다."""
    values: list[str] = []
    for field_name in field_names:
        raw_value = metadata.get(field_name)
        raw_values = [raw_value]
        if isinstance(raw_value, list):
            raw_values = raw_value
        for value in raw_values:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
    return values


def split_feature_values(values: list[str], separators: list[str]) -> list[str]:
    """시대처럼 다중값이 들어가는 필드를 정책 구분자로 분리한다."""
    if not values:
        return []
    separator_pattern = "|".join(re.escape(value) for value in separators)
    split_values: list[str] = []
    for value in values:
        parts = re.split(separator_pattern, value)
        for part in parts:
            stripped = part.strip()
            if stripped and stripped not in split_values:
                split_values.append(stripped)
    return split_values


def extract_hanja_values(values: list[str]) -> list[str]:
    """한자 필드와 괄호형 이칭에서 비교 가능한 한자열을 추출한다."""
    hanja_values: list[str] = []
    for value in values:
        for hanja in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", value):
            if hanja not in hanja_values:
                hanja_values.append(hanja)
    return hanja_values


def parse_year(value: object) -> int | None:
    """완전한 정수 연도만 비교 대상으로 사용하고 불완전 연도는 보류한다."""
    text = str(value or "").strip()
    if not re.fullmatch(r"[+-]?\d{1,4}", text):
        return None
    return int(text)


def resolve_source_entity_type(metadata: dict, source_policy: dict) -> str:
    """원천별 외부 정책으로 보조 EntityType을 제안한다."""
    default_entity_type = str(
        source_policy.get("default_entity_type") or ""
    ).strip()
    if default_entity_type:
        return default_entity_type
    type_field = str(source_policy.get("type_field") or "").strip()
    raw_type = str(metadata.get(type_field) or "").strip()
    if not raw_type:
        return ""
    return str(source_policy.get("type_mapping", {}).get(raw_type) or "")


def build_source_candidate_features(
    resolution_cases: pd.DataFrame,
    source_candidates: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """각 SourceRecord 후보에서 병합 판별용 표준 feature를 생성한다."""
    feature_policy = policy["entity_resolution"]["source_feature_policy"]
    source_policies = feature_policy["sources"]
    separators = feature_policy["value_separators"]
    proposal_status = policy["entity_resolution"]["proposal_status"]
    policy_version = policy["policy_version"]
    case_entity_types = {
        str(row.resolution_case_id): str(row.entity_type_proposal or "")
        for row in resolution_cases.itertuples()
    }
    rows: list[dict] = []
    for candidate in source_candidates.to_dict("records"):
        source = str(candidate.get("source") or "")
        source_policy = source_policies.get(source, {})
        metadata = parse_source_metadata(candidate.get("source_metadata_json"))
        names = collect_field_values(
            metadata,
            source_policy.get("name_fields", []),
        )
        normalized_names = sorted(
            {
                build_match_key(name)
                for name in names
                if build_match_key(name)
            }
        )
        hanja_source_values = collect_field_values(
            metadata,
            source_policy.get("hanja_fields", []),
        )
        hanja_values = extract_hanja_values(hanja_source_values)
        era_values = split_feature_values(
            collect_field_values(
                metadata,
                source_policy.get("era_fields", []),
            ),
            separators,
        )
        # 통시대·미상처럼 특정 시대를 가리키지 않는 원천 값은 빈값으로 취급한다
        excluded_era_values = set(feature_policy.get("era_excluded_values", []))
        era_values = [
            value
            for value in era_values
            if value.strip() not in excluded_era_values
        ]
        era_tokens = sorted(
            {
                build_match_key(value)
                for value in era_values
                if build_match_key(value)
            }
        )
        bonkwan_values = collect_field_values(
            metadata,
            source_policy.get("bonkwan_fields", []),
        )
        birth_year = parse_year(
            metadata.get(source_policy.get("birth_year_field", ""))
        )
        death_year = parse_year(
            metadata.get(source_policy.get("death_year_field", ""))
        )
        source_entity_type = resolve_source_entity_type(
            metadata,
            source_policy,
        )
        feature_evidence = []
        for feature_name, feature_value in [
            ("name", normalized_names),
            ("hanja", hanja_values),
            ("era", era_tokens),
            ("birth_year", birth_year),
            ("death_year", death_year),
            ("bonkwan", bonkwan_values),
            ("source_entity_type", source_entity_type),
        ]:
            if feature_value:
                feature_evidence.append(feature_name)
        birth_year_value: int | str = ""
        death_year_value: int | str = ""
        if birth_year is not None:
            birth_year_value = birth_year
        if death_year is not None:
            death_year_value = death_year
        rows.append(
            {
                "source_candidate_id": candidate["source_candidate_id"],
                "resolution_case_id": candidate["resolution_case_id"],
                "source_record_id": candidate["source_record_id"],
                "source": source,
                "names_json": dumps(names, ensure_ascii=False),
                "normalized_names_json": dumps(
                    normalized_names,
                    ensure_ascii=False,
                ),
                "hanja_json": dumps(hanja_values, ensure_ascii=False),
                "era_values_json": dumps(era_values, ensure_ascii=False),
                "era_tokens_json": dumps(era_tokens, ensure_ascii=False),
                "birth_year": birth_year_value,
                "death_year": death_year_value,
                "bonkwan_json": dumps(bonkwan_values, ensure_ascii=False),
                "source_entity_type_proposal": source_entity_type,
                "case_entity_type_proposal": case_entity_types.get(
                    candidate["resolution_case_id"],
                    "",
                ),
                "category_compatibility": candidate.get(
                    "category_compatibility",
                    "",
                ),
                "feature_evidence_json": dumps(
                    feature_evidence,
                    ensure_ascii=False,
                ),
                "proposed_role": "",
                "proposed_canonical_alternative_id": "",
                "feature_status": proposal_status,
                "role_status": proposal_status,
                "resolution_policy_version": policy_version,
            }
        )
    columns = [
        "source_candidate_id",
        "resolution_case_id",
        "source_record_id",
        "source",
        "names_json",
        "normalized_names_json",
        "hanja_json",
        "era_values_json",
        "era_tokens_json",
        "birth_year",
        "death_year",
        "bonkwan_json",
        "source_entity_type_proposal",
        "case_entity_type_proposal",
        "category_compatibility",
        "feature_evidence_json",
        "proposed_role",
        "proposed_canonical_alternative_id",
        "feature_status",
        "role_status",
        "resolution_policy_version",
    ]
    return rows_to_dataframe(rows, columns)


def compare_candidate_pair(
    left: dict,
    right: dict,
    pair_policy: dict,
    identifier_policy: dict,
    policy_version: str,
    proposal_status: str,
) -> dict:
    """후보 두 건의 독립 일치 신호와 강한 충돌을 계산한다."""
    left_names = set(loads(left["normalized_names_json"]))
    right_names = set(loads(right["normalized_names_json"]))
    left_hanja = set(loads(left["hanja_json"]))
    right_hanja = set(loads(right["hanja_json"]))
    left_eras = set(loads(left["era_tokens_json"]))
    right_eras = set(loads(right["era_tokens_json"]))
    left_bonkwan = set(loads(left["bonkwan_json"]))
    right_bonkwan = set(loads(right["bonkwan_json"]))
    signals: list[str] = []
    signal_details: dict[str, object] = {}
    conflicts: list[str] = []

    shared_names = sorted(left_names.intersection(right_names))
    if shared_names:
        signals.append("normalized_name_match")
        signal_details["normalized_name_match"] = shared_names
    shared_hanja = sorted(left_hanja.intersection(right_hanja))
    if shared_hanja:
        signals.append("hanja_match")
        signal_details["hanja_match"] = shared_hanja
    shared_eras = sorted(left_eras.intersection(right_eras))
    if shared_eras:
        signals.append("era_overlap")
        signal_details["era_overlap"] = shared_eras
    shared_bonkwan = sorted(left_bonkwan.intersection(right_bonkwan))
    if shared_bonkwan:
        signals.append("bonkwan_match")
        signal_details["bonkwan_match"] = shared_bonkwan

    year_tolerance = int(pair_policy["year_tolerance"])
    matched_year_fields: list[str] = []
    for field_name, conflict_name in [
        ("birth_year", "birth_year_conflict"),
        ("death_year", "death_year_conflict"),
    ]:
        left_year = parse_year(left.get(field_name))
        right_year = parse_year(right.get(field_name))
        if left_year is None or right_year is None:
            continue
        if abs(left_year - right_year) <= year_tolerance:
            matched_year_fields.append(field_name)
        elif abs(left_year - right_year) > year_tolerance:
            conflicts.append(conflict_name)
    if matched_year_fields:
        signals.append("lifespan_match")
        signal_details["lifespan_match"] = matched_year_fields

    left_entity_type = str(left.get("source_entity_type_proposal") or "")
    right_entity_type = str(right.get("source_entity_type_proposal") or "")
    if left_entity_type and right_entity_type:
        if left_entity_type == right_entity_type:
            signals.append("entity_type_match")
            signal_details["entity_type_match"] = left_entity_type
        elif left_entity_type != right_entity_type:
            conflicts.append("entity_type_conflict")

    independent_signal_count = len(signals)
    disambiguating_signal_count = len(
        set(signals).intersection(pair_policy["disambiguating_signals"])
    )
    required_signals_present = set(pair_policy["required_signals"]).issubset(
        signals
    )
    strong_conflict_count = len(
        set(conflicts).intersection(pair_policy["strong_conflicts"])
    )
    same_source_system = left["source"] == right["source"]
    source_merge_allowed = not same_source_system
    if pair_policy["allow_same_source_merge"]:
        source_merge_allowed = True
    merge_eligible = bool(
        required_signals_present
        and independent_signal_count
        >= int(pair_policy["minimum_independent_signals"])
        and disambiguating_signal_count
        >= int(pair_policy["minimum_disambiguating_signals"])
        and strong_conflict_count == 0
        and source_merge_allowed
        and left["category_compatibility"] != "CONFLICT"
        and right["category_compatibility"] != "CONFLICT"
    )
    left_candidate_id = str(left["source_candidate_id"])
    right_candidate_id = str(right["source_candidate_id"])
    pair_id = create_stable_id(
        identifier_policy["source_candidate_pair_prefix"],
        [left["resolution_case_id"], left_candidate_id, right_candidate_id],
        identifier_policy,
    )
    return {
        "source_candidate_pair_id": pair_id,
        "resolution_case_id": left["resolution_case_id"],
        "left_source_candidate_id": left_candidate_id,
        "right_source_candidate_id": right_candidate_id,
        "left_source_record_id": left["source_record_id"],
        "right_source_record_id": right["source_record_id"],
        "same_source_system": same_source_system,
        "signal_dimensions_json": dumps(signals, ensure_ascii=False),
        "signal_details_json": dumps(
            signal_details,
            ensure_ascii=False,
            sort_keys=True,
        ),
        "conflict_signals_json": dumps(conflicts, ensure_ascii=False),
        "independent_signal_count": independent_signal_count,
        "disambiguating_signal_count": disambiguating_signal_count,
        "merge_eligible": merge_eligible,
        "proposal_status": proposal_status,
        "resolution_policy_version": policy_version,
    }


def build_source_candidate_pair_signals(
    candidate_features: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """같은 용어 case 안의 모든 후보 쌍에 대해 병합 신호를 생성한다."""
    resolution_policy = policy["entity_resolution"]
    pair_policy = resolution_policy["pair_signal_policy"]
    identifier_policy = resolution_policy["identifier_policy"]
    proposal_status = resolution_policy["proposal_status"]
    rows: list[dict] = []
    for case_id, group in candidate_features.groupby("resolution_case_id"):
        candidates = sorted(
            group.to_dict("records"),
            key=lambda row: row["source_candidate_id"],
        )
        for left, right in combinations(candidates, 2):
            rows.append(
                compare_candidate_pair(
                    left,
                    right,
                    pair_policy,
                    identifier_policy,
                    policy["policy_version"],
                    proposal_status,
                )
            )
    columns = [
        "source_candidate_pair_id",
        "resolution_case_id",
        "left_source_candidate_id",
        "right_source_candidate_id",
        "left_source_record_id",
        "right_source_record_id",
        "same_source_system",
        "signal_dimensions_json",
        "signal_details_json",
        "conflict_signals_json",
        "independent_signal_count",
        "disambiguating_signal_count",
        "merge_eligible",
        "proposal_status",
        "resolution_policy_version",
    ]
    return rows_to_dataframe(rows, columns)


def form_strict_clusters(
    candidate_ids: list[str],
    pair_rows: list[dict],
) -> list[list[str]]:
    """모든 교차 후보 쌍이 적격일 때만 묶는 complete-link cluster를 만든다."""
    eligible_pairs = {
        frozenset(
            [row["left_source_candidate_id"], row["right_source_candidate_id"]]
        )
        for row in pair_rows
        if bool(row["merge_eligible"])
    }
    ranked_pairs = sorted(
        [row for row in pair_rows if bool(row["merge_eligible"])],
        key=lambda row: (
            -int(row["disambiguating_signal_count"]),
            -int(row["independent_signal_count"]),
            row["source_candidate_pair_id"],
        ),
    )
    clusters = [[candidate_id] for candidate_id in sorted(candidate_ids)]
    for pair_row in ranked_pairs:
        left_id = pair_row["left_source_candidate_id"]
        right_id = pair_row["right_source_candidate_id"]
        left_cluster = next(cluster for cluster in clusters if left_id in cluster)
        right_cluster = next(cluster for cluster in clusters if right_id in cluster)
        if left_cluster is right_cluster:
            continue
        all_cross_pairs_eligible = all(
            frozenset([left_member, right_member]) in eligible_pairs
            for left_member in left_cluster
            for right_member in right_cluster
        )
        if not all_cross_pairs_eligible:
            continue
        merged_cluster = sorted(left_cluster + right_cluster)
        clusters.remove(left_cluster)
        clusters.remove(right_cluster)
        clusters.append(merged_cluster)
    return sorted(clusters, key=lambda cluster: cluster[0])


def propose_singleton_role(candidate: dict) -> str:
    """강한 자동 병합 근거가 없는 단일 후보의 검토 역할을 제안한다."""
    if candidate["category_compatibility"] == "CONFLICT":
        return "REJECTED"
    if candidate["retrieval_method"] in {"exact", "affix"}:
        return "ALTERNATIVE_ENTITY"
    return "AMBIGUOUS"


def build_canonical_alternative_tables(
    resolution_cases: pd.DataFrame,
    source_candidates: pd.DataFrame,
    candidate_features: pd.DataFrame,
    pair_signals: pd.DataFrame,
    policy: dict,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """복수 원천 후보를 보존한 canonical 대안 cluster와 멤버 역할을 제안한다."""
    resolution_policy = policy["entity_resolution"]
    identifier_policy = resolution_policy["identifier_policy"]
    proposal_status = resolution_policy["proposal_status"]
    candidate_by_id = {
        str(row["source_candidate_id"]): row
        for row in source_candidates.to_dict("records")
    }
    feature_rows = {
        str(row["source_candidate_id"]): row
        for row in candidate_features.to_dict("records")
    }
    pair_rows_by_case: dict[str, list[dict]] = {}
    for row in pair_signals.to_dict("records"):
        pair_rows_by_case.setdefault(row["resolution_case_id"], []).append(row)
    case_by_id = {
        str(row["resolution_case_id"]): row
        for row in resolution_cases.to_dict("records")
    }
    cluster_rows: list[dict] = []
    member_rows: list[dict] = []

    for case_id, feature_group in candidate_features.groupby("resolution_case_id"):
        valid_candidate_ids = sorted(
            row["source_candidate_id"]
            for row in feature_group.to_dict("records")
            if row["category_compatibility"] != "CONFLICT"
        )
        clusters = form_strict_clusters(
            valid_candidate_ids,
            pair_rows_by_case.get(case_id, []),
        )
        for member_ids in clusters:
            alternative_id = create_stable_id(
                identifier_policy["canonical_alternative_prefix"],
                [case_id] + member_ids,
                identifier_policy,
            )
            member_candidates = [candidate_by_id[value] for value in member_ids]
            member_features = [feature_rows[value] for value in member_ids]
            source_systems = sorted(
                {str(row["source"]) for row in member_candidates}
            )
            entity_types = sorted(
                {
                    str(row["source_entity_type_proposal"])
                    for row in member_features
                    if str(row["source_entity_type_proposal"])
                }
            )
            proposed_entity_type = str(
                case_by_id[case_id].get("entity_type_proposal") or ""
            )
            if not proposed_entity_type and len(entity_types) == 1:
                proposed_entity_type = entity_types[0]
            internal_pairs = [
                row
                for row in pair_rows_by_case.get(case_id, [])
                if row["left_source_candidate_id"] in member_ids
                and row["right_source_candidate_id"] in member_ids
            ]
            merge_signals = sorted(
                {
                    signal
                    for row in internal_pairs
                    for signal in loads(row["signal_dimensions_json"])
                }
            )
            confidence_tier = "NEEDS_SEMANTIC_REVIEW"
            if len(member_ids) > 1:
                confidence_tier = "MULTI_SOURCE_SUPPORTED"
            elif len(member_ids) == 1:
                singleton_role = propose_singleton_role(
                    candidate_by_id[member_ids[0]]
                )
                if singleton_role == "ALTERNATIVE_ENTITY":
                    confidence_tier = "SINGLE_SOURCE_CANDIDATE"
            cluster_rows.append(
                {
                    "canonical_alternative_id": alternative_id,
                    "resolution_case_id": case_id,
                    "canonical_id": "",
                    "display_name_proposal": case_by_id[case_id][
                        "canonical_term"
                    ],
                    "entity_type_proposal": proposed_entity_type,
                    "member_count": len(member_ids),
                    "source_system_count": len(source_systems),
                    "source_systems_json": dumps(
                        source_systems,
                        ensure_ascii=False,
                    ),
                    "source_record_ids_json": dumps(
                        [row["source_record_id"] for row in member_candidates],
                        ensure_ascii=False,
                    ),
                    "merge_signals_json": dumps(
                        merge_signals,
                        ensure_ascii=False,
                    ),
                    "confidence_tier": confidence_tier,
                    "cluster_status": proposal_status,
                    "resolution_policy_version": policy["policy_version"],
                }
            )
            for member_id in member_ids:
                proposed_role = "IDENTITY_MEMBER"
                if len(member_ids) == 1:
                    proposed_role = propose_singleton_role(
                        candidate_by_id[member_id]
                    )
                feature_rows[member_id]["proposed_role"] = proposed_role
                feature_rows[member_id][
                    "proposed_canonical_alternative_id"
                ] = alternative_id
                member_rows.append(
                    {
                        "canonical_alternative_id": alternative_id,
                        "source_candidate_id": member_id,
                        "source_record_id": candidate_by_id[member_id][
                            "source_record_id"
                        ],
                        "proposed_case_role": proposed_role,
                        "role_status": proposal_status,
                        "resolution_policy_version": policy["policy_version"],
                    }
                )

    for feature_row in feature_rows.values():
        if feature_row["category_compatibility"] == "CONFLICT":
            feature_row["proposed_role"] = "REJECTED"
    updated_features = rows_to_dataframe(
        list(feature_rows.values()),
        list(candidate_features.columns),
    )
    cluster_columns = [
        "canonical_alternative_id",
        "resolution_case_id",
        "canonical_id",
        "display_name_proposal",
        "entity_type_proposal",
        "member_count",
        "source_system_count",
        "source_systems_json",
        "source_record_ids_json",
        "merge_signals_json",
        "confidence_tier",
        "cluster_status",
        "resolution_policy_version",
    ]
    member_columns = [
        "canonical_alternative_id",
        "source_candidate_id",
        "source_record_id",
        "proposed_case_role",
        "role_status",
        "resolution_policy_version",
    ]
    return (
        rows_to_dataframe(cluster_rows, cluster_columns),
        rows_to_dataframe(member_rows, member_columns),
        updated_features,
    )


def build_source_candidate_proposal_tables(
    base_tables: dict[str, pd.DataFrame],
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """원천 후보 feature·쌍 신호·canonical 대안 cluster를 일괄 생성한다."""
    candidate_features = build_source_candidate_features(
        base_tables["resolution_cases"],
        base_tables["source_record_candidates"],
        policy,
    )
    pair_signals = build_source_candidate_pair_signals(
        candidate_features,
        policy,
    )
    clusters, members, candidate_features = build_canonical_alternative_tables(
        base_tables["resolution_cases"],
        base_tables["source_record_candidates"],
        candidate_features,
        pair_signals,
        policy,
    )
    return {
        "source_candidate_features": candidate_features,
        "source_candidate_pair_signals": pair_signals,
        "canonical_alternative_clusters": clusters,
        "canonical_cluster_members": members,
    }

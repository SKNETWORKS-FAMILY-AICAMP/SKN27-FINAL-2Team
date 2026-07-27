from __future__ import annotations

from argparse import ArgumentParser, Namespace
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from json import JSONDecodeError, dump, load, loads
from pathlib import Path

import pandas as pd


def load_goldset_policy(policy_path: str) -> dict:
    """사실 관계 골드셋 정책을 읽는다."""
    path = Path(policy_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"사실 관계 골드셋 정책 파일이 없습니다: {path}"
        )
    with path.open("r", encoding="utf-8") as policy_file:
        policy = load(policy_file)
    required_sections = {
        "policy_version",
        "sample_size",
        "minimum_cases_per_relation_type",
        "deterministic_seed",
        "case_id_prefix",
        "excluded_relation_types",
        "review",
        "outputs",
    }
    missing_sections = required_sections.difference(policy)
    if missing_sections:
        missing_text = ", ".join(sorted(missing_sections))
        raise ValueError(
            f"사실 관계 골드셋 정책 구성이 없습니다: {missing_text}"
        )
    return policy


def create_stable_rank(seed: str, *values: object) -> str:
    """입력 순서와 무관한 표본 정렬 키를 만든다."""
    payload = "|".join([seed, *[str(value) for value in values]])
    return sha256(payload.encode("utf-8")).hexdigest()


def calculate_file_sha256(input_path: Path) -> str:
    """입력 사실 관계 CSV의 해시를 계산한다."""
    digest = sha256()
    with input_path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_json_list(value: object) -> list[str]:
    """CSV의 JSON 배열을 문자열 목록으로 읽는다."""
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = loads(text)
    except (JSONDecodeError, TypeError):
        return [text]
    if isinstance(parsed, list):
        return [
            str(item)
            for item in parsed
            if str(item).strip()
        ]
    return [str(parsed)]


def allocate_relation_type_quotas(
    population_counts: dict[str, int],
    sample_size: int,
    minimum_per_type: int,
    seed: str,
) -> dict[str, int]:
    """관계 유형별 최소 표본 후 남은 표본을 모집단에 비례 배분한다."""
    population_size = sum(population_counts.values())
    if sample_size > population_size:
        raise ValueError(
            f"표본 크기 {sample_size}가 모집단 {population_size}보다 큽니다."
        )
    relation_types = sorted(population_counts)
    quotas = {
        relation_type: min(
            minimum_per_type,
            population_counts[relation_type],
        )
        for relation_type in relation_types
    }
    base_total = sum(quotas.values())
    if base_total > sample_size:
        quotas = {relation_type: 0 for relation_type in relation_types}
        ordered_types = sorted(
            relation_types,
            key=lambda relation_type: create_stable_rank(
                seed,
                "minimum",
                relation_type,
            ),
        )
        for relation_type in ordered_types[:sample_size]:
            quotas[relation_type] = 1
        return quotas

    remaining = sample_size - base_total
    residual_counts = {
        relation_type: (
            population_counts[relation_type] - quotas[relation_type]
        )
        for relation_type in relation_types
    }
    residual_total = sum(residual_counts.values())
    if remaining == 0 or residual_total == 0:
        return quotas

    fractional_remainders: list[tuple[float, str]] = []
    assigned = 0
    for relation_type in relation_types:
        raw_quota = (
            remaining
            * residual_counts[relation_type]
            / residual_total
        )
        additional_quota = min(
            int(raw_quota),
            residual_counts[relation_type],
        )
        quotas[relation_type] += additional_quota
        assigned += additional_quota
        fractional_remainders.append(
            (raw_quota - int(raw_quota), relation_type)
        )

    unassigned = remaining - assigned
    ordered_remainders = sorted(
        fractional_remainders,
        key=lambda item: (
            -item[0],
            create_stable_rank(seed, "remainder", item[1]),
        ),
    )
    while unassigned > 0:
        progress = False
        for _, relation_type in ordered_remainders:
            if unassigned == 0:
                break
            if quotas[relation_type] >= population_counts[relation_type]:
                continue
            quotas[relation_type] += 1
            unassigned -= 1
            progress = True
        if not progress:
            raise ValueError("관계 유형별 표본 할당을 완료하지 못했습니다.")
    return quotas


def select_rows_within_relation_type(
    rows: list[dict],
    quota: int,
    seed: str,
) -> list[dict]:
    """검증 상태·추출 방식 층을 순환해 한 관계 유형의 표본을 고른다."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        extraction_methods = "|".join(
            sorted(parse_json_list(row.get("extraction_methods_json", "")))
        )
        stratum = (
            str(row.get("verification_status") or ""),
            extraction_methods,
        )
        grouped[stratum].append(row)
    ordered_strata = sorted(
        grouped,
        key=lambda stratum: create_stable_rank(
            seed,
            "stratum",
            *stratum,
        ),
    )
    for stratum in ordered_strata:
        grouped[stratum].sort(
            key=lambda row: create_stable_rank(
                seed,
                "fact",
                row["canonical_relationship_id"],
            )
        )

    selected: list[dict] = []
    positions = {stratum: 0 for stratum in ordered_strata}
    while len(selected) < quota:
        progress = False
        for stratum in ordered_strata:
            if len(selected) >= quota:
                break
            position = positions[stratum]
            stratum_rows = grouped[stratum]
            if position >= len(stratum_rows):
                continue
            selected.append(stratum_rows[position])
            positions[stratum] = position + 1
            progress = True
        if not progress:
            raise ValueError("관계 유형 내부 표본 추출을 완료하지 못했습니다.")
    return selected


def select_fact_gold_sample(
    facts: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """핵심 사실 관계를 유형별 층화 방식으로 결정적으로 추출한다."""
    required_columns = {
        "canonical_relationship_id",
        "start_canonical_id",
        "end_canonical_id",
        "relation_type",
        "verification_status",
    }
    missing_columns = required_columns.difference(facts.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(
            f"사실 관계 입력 필수 컬럼이 없습니다: {missing_text}"
        )
    excluded_types = {
        str(value) for value in policy["excluded_relation_types"]
    }
    population = facts[
        ~facts["relation_type"].isin(excluded_types)
    ].copy()
    if population.empty:
        raise ValueError("검증셋으로 추출할 핵심 사실 관계가 없습니다.")
    if population["canonical_relationship_id"].duplicated().any():
        raise ValueError("사실 관계 입력 ID가 중복됐습니다.")

    population_counts = {
        str(relation_type): int(count)
        for relation_type, count in population[
            "relation_type"
        ].value_counts().items()
    }
    sample_size = int(policy["sample_size"])
    minimum_per_type = int(
        policy["minimum_cases_per_relation_type"]
    )
    seed = str(policy["deterministic_seed"])
    quotas = allocate_relation_type_quotas(
        population_counts,
        sample_size,
        minimum_per_type,
        seed,
    )
    selected_rows: list[dict] = []
    population_records = population.to_dict("records")
    records_by_type: dict[str, list[dict]] = defaultdict(list)
    for row in population_records:
        records_by_type[str(row["relation_type"])].append(row)
    for relation_type in sorted(quotas):
        quota = quotas[relation_type]
        if quota == 0:
            continue
        selected_rows.extend(
            select_rows_within_relation_type(
                records_by_type[relation_type],
                quota,
                seed,
            )
        )
    selected = pd.DataFrame(
        selected_rows,
        columns=population.columns,
    )
    return selected.sort_values(
        ["relation_type", "canonical_relationship_id"],
        kind="stable",
    ).reset_index(drop=True)


def build_goldset_tables(
    facts: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """표본 snapshot·사람 검수본·층별 분포표를 만든다."""
    selected = select_fact_gold_sample(facts, policy)
    registry = canonical_registry[
        ["canonical_id", "display_name", "entity_type"]
    ].copy()
    start_registry = registry.rename(
        columns={
            "canonical_id": "start_canonical_id",
            "display_name": "start_display_name",
            "entity_type": "start_entity_type",
        }
    )
    end_registry = registry.rename(
        columns={
            "canonical_id": "end_canonical_id",
            "display_name": "end_display_name",
            "entity_type": "end_entity_type",
        }
    )
    selected = selected.merge(
        start_registry,
        on="start_canonical_id",
        how="left",
        validate="many_to_one",
    )
    selected = selected.merge(
        end_registry,
        on="end_canonical_id",
        how="left",
        validate="many_to_one",
    )
    if (
        selected["start_display_name"].isna().any()
        or selected["end_display_name"].isna().any()
    ):
        raise ValueError(
            "검증셋 관계가 registry에 없는 CanonicalEntity를 참조합니다."
        )

    relation_labels = {
        str(key): str(value)
        for key, value in policy.get("relation_labels", {}).items()
    }
    selected["fact_gold_case_id"] = selected[
        "canonical_relationship_id"
    ].map(
        lambda relationship_id: (
            str(policy["case_id_prefix"])
            + create_stable_rank(
                str(policy["deterministic_seed"]),
                "case",
                relationship_id,
            )[:20]
        )
    )
    selected["relation_label_ko"] = selected["relation_type"].map(
        lambda relation_type: relation_labels.get(
            str(relation_type),
            str(relation_type),
        )
    )
    snapshot_columns = [
        "fact_gold_case_id",
        "canonical_relationship_id",
        "relation_type",
        "relation_label_ko",
        "start_canonical_id",
        "start_display_name",
        "start_entity_type",
        "end_canonical_id",
        "end_display_name",
        "end_entity_type",
        "evidence_sentences_json",
        "evidence_urls_json",
        "detail_urls_json",
        "source_datasets_json",
        "raw_relation_types_json",
        "extraction_methods_json",
        "verification_statuses_json",
        "evidence_count",
        "source_row_count",
        "verification_status",
        "policy_version",
    ]
    snapshot = selected.reindex(columns=snapshot_columns).fillna("")

    review_columns = [
        "fact_gold_case_id",
        "canonical_relationship_id",
        "relation_type",
        "relation_label_ko",
        "start_canonical_id",
        "start_display_name",
        "start_entity_type",
        "end_canonical_id",
        "end_display_name",
        "end_entity_type",
        "evidence_sentences_json",
        "evidence_urls_json",
        "detail_urls_json",
        "source_datasets_json",
        "evidence_count",
    ]
    human_review = snapshot[review_columns].copy()
    for annotation_field in policy["review"]["annotation_fields"]:
        human_review[str(annotation_field)] = ""
    human_review["review_status"] = str(
        policy["review"]["initial_status"]
    )

    excluded_types = {
        str(value) for value in policy["excluded_relation_types"]
    }
    population = facts[
        ~facts["relation_type"].isin(excluded_types)
    ]
    population_counts = population["relation_type"].value_counts()
    sample_counts = snapshot["relation_type"].value_counts()
    distribution_rows: list[dict] = []
    for relation_type in sorted(population_counts.index):
        population_count = int(population_counts[relation_type])
        sample_count = int(sample_counts.get(relation_type, 0))
        sample_weight = 0.0
        if sample_count > 0:
            sample_weight = population_count / sample_count
        distribution_rows.append(
            {
                "relation_type": relation_type,
                "relation_label_ko": relation_labels.get(
                    str(relation_type),
                    str(relation_type),
                ),
                "population_count": population_count,
                "sample_count": sample_count,
                "population_percent": (
                    population_count / len(population)
                ),
                "sample_percent": sample_count / len(snapshot),
                "sample_weight": sample_weight,
            }
        )
    distribution = pd.DataFrame(distribution_rows)
    return {
        "sample_snapshot": snapshot,
        "human_review": human_review,
        "distribution": distribution,
    }


def review_has_started(review_path: Path, policy: dict) -> bool:
    """기존 사람 검수 CSV에 실제 입력이 시작됐는지 확인한다."""
    if not review_path.is_file():
        return False
    review = pd.read_csv(
        review_path,
        dtype=str,
        encoding="utf-8-sig",
        keep_default_na=False,
    )
    for field_name in policy["review"]["annotation_fields"]:
        if field_name not in review.columns:
            continue
        if review[field_name].str.strip().ne("").any():
            return True
    if "review_status" not in review.columns:
        return False
    initial_status = str(policy["review"]["initial_status"])
    statuses = review["review_status"].str.strip()
    return (~statuses.isin({"", initial_status})).any()


def write_fact_relationship_goldset(
    facts_path: Path,
    registry_path: Path,
    output_directory: Path,
    policy: dict,
    force_overwrite_review: bool = False,
) -> dict[str, object]:
    """사실 관계 검증셋 파일과 manifest를 생성한다."""
    for input_path in [facts_path, registry_path]:
        if not input_path.is_file():
            raise FileNotFoundError(
                f"사실 관계 검증셋 입력이 없습니다: {input_path}"
            )
    facts = pd.read_csv(facts_path, dtype=str).fillna("")
    canonical_registry = pd.read_csv(
        registry_path,
        dtype=str,
    ).fillna("")
    tables = build_goldset_tables(
        facts,
        canonical_registry,
        policy,
    )
    output_paths = {
        name: output_directory / relative_path
        for name, relative_path in policy["outputs"].items()
    }
    review_path = output_paths["human_review"]
    if (
        not force_overwrite_review
        and review_has_started(review_path, policy)
    ):
        raise FileExistsError(
            "사실 관계 사람 검수가 시작되어 CSV를 덮어쓸 수 없습니다."
        )
    for output_path in output_paths.values():
        output_path.parent.mkdir(parents=True, exist_ok=True)
    tables["sample_snapshot"].to_csv(
        output_paths["sample_snapshot"],
        index=False,
        encoding="utf-8-sig",
    )
    tables["human_review"].to_csv(
        review_path,
        index=False,
        encoding="utf-8-sig",
    )
    tables["distribution"].to_csv(
        output_paths["distribution"],
        index=False,
        encoding="utf-8-sig",
    )

    excluded_types = {
        str(value) for value in policy["excluded_relation_types"]
    }
    population = facts[
        ~facts["relation_type"].isin(excluded_types)
    ]
    sample = tables["sample_snapshot"]
    manifest = {
        "status": "COMPLETED",
        "stage": "FACT_RELATIONSHIP_GOLDSET_BUILD",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": str(policy["policy_version"]),
        "input_fact_path": str(facts_path.resolve()),
        "input_fact_sha256": calculate_file_sha256(facts_path),
        "input_registry_path": str(registry_path.resolve()),
        "population_fact_count": len(population),
        "sample_fact_count": len(sample),
        "sample_relation_type_count": int(
            sample["relation_type"].nunique()
        ),
        "population_verification_status_counts": {
            str(status): int(count)
            for status, count in population[
                "verification_status"
            ].value_counts().items()
        },
        "sample_verification_status_counts": {
            str(status): int(count)
            for status, count in sample[
                "verification_status"
            ].value_counts().items()
        },
        "output_paths": {
            name: str(path.resolve())
            for name, path in output_paths.items()
        },
    }
    with output_paths["manifest"].open(
        "w",
        encoding="utf-8",
    ) as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    return manifest


def parse_arguments() -> Namespace:
    """사실 관계 골드셋 생성 CLI 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent.parent
    parser = ArgumentParser(
        description=(
            "Canonical 핵심 사실 관계에서 사람 검수용 층화 골드셋을 "
            "생성합니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(
            neo4j_root / "config" / "fact_relationship_goldset.json"
        ),
    )
    parser.add_argument(
        "--facts",
        default=str(
            neo4j_root
            / "output"
            / "source_relationships"
            / "canonical_fact_relationships.csv"
        ),
    )
    parser.add_argument(
        "--canonical-registry",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "canonical_entity_registry.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(neo4j_root / "goldset" / "fact_relationship"),
    )
    parser.add_argument(
        "--force-overwrite-review",
        action="store_true",
    )
    return parser.parse_args()


def main() -> None:
    """사실 관계 골드셋을 생성하고 요약을 출력한다."""
    cli_args = parse_arguments()
    policy = load_goldset_policy(cli_args.config)
    manifest = write_fact_relationship_goldset(
        Path(cli_args.facts),
        Path(cli_args.canonical_registry),
        Path(cli_args.output_dir),
        policy,
        force_overwrite_review=cli_args.force_overwrite_review,
    )
    print(
        pd.Series(
            {
                "status": manifest["status"],
                "population_fact_count": manifest[
                    "population_fact_count"
                ],
                "sample_fact_count": manifest["sample_fact_count"],
                "sample_relation_type_count": manifest[
                    "sample_relation_type_count"
                ],
            }
        ).to_json(force_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()

from argparse import ArgumentParser
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from json import dumps
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import load_pipeline_policy
from entity_resolution.identifiers import create_stable_id
from entity_resolution.semantic_review import load_jsonl, write_jsonl


def create_stable_rank(seed: str, *values: object) -> str:
    """입력 순서와 무관한 표본 정렬 키를 만든다."""
    payload = "|".join([seed, *[str(value) for value in values]])
    return sha256(payload.encode("utf-8")).hexdigest()


def classify_candidate_count(candidate_count: int, gold_policy: dict) -> str:
    """설정에 정의된 후보 수 구간을 반환한다."""
    for bucket in gold_policy["candidate_count_buckets"]:
        minimum = int(bucket["minimum"])
        maximum = bucket.get("maximum")
        minimum_satisfied = candidate_count >= minimum
        maximum_satisfied = maximum is None
        if maximum is not None:
            maximum_satisfied = candidate_count <= int(maximum)
        if minimum_satisfied and maximum_satisfied:
            return str(bucket["name"])
    raise ValueError(f"후보 수 {candidate_count}에 해당하는 구간이 없습니다.")


def profile_term_task(task: dict, gold_policy: dict) -> dict:
    """층화 표본 추출에 사용할 task 난이도·구조 특성을 계산한다."""
    candidates = task.get("source_candidates", [])
    alternatives = task.get("code_canonical_alternatives", [])
    pairs = task.get("relevant_pair_signals", [])
    candidate_count = len(candidates)
    methods = {
        str(candidate.get("retrieval_method", ""))
        for candidate in candidates
    }
    exact_method = str(gold_policy["exact_retrieval_method"])
    has_exact = exact_method in methods
    has_expanded = bool(methods.difference({exact_method}))
    profile_labels = gold_policy["retrieval_profile_labels"]
    retrieval_profile = profile_labels["expanded_only"]
    if has_exact and has_expanded:
        retrieval_profile = profile_labels["exact_and_expanded"]
    elif has_exact:
        retrieval_profile = profile_labels["exact_only"]
    confidence_tier = str(gold_policy["multi_source_confidence_tier"])
    multi_source_supported = any(
        alternative.get("confidence_tier") == confidence_tier
        for alternative in alternatives
    )
    conflict_pair_count = sum(
        1 for pair in pairs if pair.get("conflicts")
    )
    return {
        "category": str(task.get("category", "")),
        "candidate_count": candidate_count,
        "candidate_count_bucket": classify_candidate_count(
            candidate_count,
            gold_policy,
        ),
        "source_count": len(
            {
                str(candidate.get("source", ""))
                for candidate in candidates
            }
        ),
        "alternative_count": len(alternatives),
        "retrieval_profile": retrieval_profile,
        "multi_source_supported": multi_source_supported,
        "conflict_present": conflict_pair_count > 0,
        "conflict_pair_count": conflict_pair_count,
    }


def order_records_by_strata(
    records: list[dict],
    stratum_fields: list[str],
    seed: str,
) -> list[dict]:
    """각 층에서 한 건씩 순환해 특정 층으로 표본이 쏠리지 않게 한다."""
    grouped: dict[tuple, list[dict]] = {}
    for record in records:
        key = tuple(record["profile"][field] for field in stratum_fields)
        grouped.setdefault(key, []).append(record)
    ordered_keys = sorted(
        grouped,
        key=lambda key: create_stable_rank(seed, "stratum", *key),
    )
    for key in ordered_keys:
        grouped[key].sort(
            key=lambda record: create_stable_rank(
                seed,
                "task",
                record["task"]["term_review_task_id"],
            )
        )
    ordered: list[dict] = []
    positions = {key: 0 for key in ordered_keys}
    remaining = len(records)
    while remaining > 0:
        for key in ordered_keys:
            position = positions[key]
            group = grouped[key]
            if position >= len(group):
                continue
            ordered.append(group[position])
            positions[key] = position + 1
            remaining -= 1
    return ordered


def select_gold_tasks(tasks: list[dict], policy: dict) -> list[dict]:
    """카테고리 최소 할당 후 복합 층을 순환해 결정적으로 표본을 뽑는다."""
    gold_policy = policy["entity_resolution"]["semantic_review"]["gold_set"]
    sample_size = int(gold_policy["sample_size"])
    if sample_size > len(tasks):
        raise ValueError(
            f"표본 크기 {sample_size}가 전체 task {len(tasks)}보다 큽니다."
        )
    seed = str(gold_policy["deterministic_seed"])
    profiled_records = [
        {
            "task": task,
            "profile": profile_term_task(task, gold_policy),
        }
        for task in tasks
    ]
    records_by_category: dict[str, list[dict]] = {}
    for record in profiled_records:
        category = record["profile"]["category"]
        records_by_category.setdefault(category, []).append(record)
    categories = sorted(
        records_by_category,
        key=lambda category: create_stable_rank(seed, "category", category),
    )
    within_category_fields = [
        "candidate_count_bucket",
        "retrieval_profile",
        "multi_source_supported",
        "conflict_present",
    ]
    minimum_per_category = int(gold_policy["minimum_cases_per_category"])
    maximum_candidates = int(
        gold_policy["maximum_candidates_per_pilot_case"]
    )
    selected: list[dict] = []
    selected_task_ids: set[str] = set()
    for category in categories:
        if len(selected) >= sample_size:
            break
        category_records = [
            record
            for record in records_by_category[category]
            if record["profile"]["candidate_count"] <= maximum_candidates
        ]
        if not category_records:
            category_records = sorted(
                records_by_category[category],
                key=lambda record: (
                    record["profile"]["candidate_count"],
                    create_stable_rank(
                        seed,
                        "category-fallback",
                        record["task"]["term_review_task_id"],
                    ),
                ),
            )
        category_order = order_records_by_strata(
            category_records,
            within_category_fields,
            seed,
        )
        category_limit = min(minimum_per_category, len(category_order))
        for record in category_order[:category_limit]:
            if len(selected) >= sample_size:
                break
            task_id = str(record["task"]["term_review_task_id"])
            selected.append(record)
            selected_task_ids.add(task_id)

    remaining_records = [
        record
        for record in profiled_records
        if record["task"]["term_review_task_id"] not in selected_task_ids
        and record["profile"]["candidate_count"] <= maximum_candidates
    ]
    remaining_order = order_records_by_strata(
        remaining_records,
        ["category", *within_category_fields],
        seed,
    )
    for record in remaining_order:
        if len(selected) >= sample_size:
            break
        selected.append(record)
        selected_task_ids.add(record["task"]["term_review_task_id"])
    if len(selected) < sample_size:
        fallback_records = [
            record
            for record in profiled_records
            if record["task"]["term_review_task_id"]
            not in selected_task_ids
        ]
        fallback_records.sort(
            key=lambda record: (
                record["profile"]["candidate_count"],
                create_stable_rank(
                    seed,
                    "sample-fallback",
                    record["task"]["term_review_task_id"],
                ),
            )
        )
        for record in fallback_records:
            if len(selected) >= sample_size:
                break
            selected.append(record)
    return selected


def build_gold_task_records(selected: list[dict], policy: dict) -> list[dict]:
    """원본 task에 표본 순번과 층화 특성만 덧붙인다."""
    resolution_policy = policy["entity_resolution"]
    gold_policy = resolution_policy["semantic_review"]["gold_set"]
    identifier_policy = resolution_policy["identifier_policy"]
    records: list[dict] = []
    for case_order, record in enumerate(selected, start=1):
        task = dict(record["task"])
        gold_case_id = create_stable_id(
            identifier_policy["gold_case_prefix"],
            [
                task["term_review_task_id"],
                gold_policy["selection_policy_version"],
            ],
            identifier_policy,
        )
        task["gold_set_metadata"] = {
            "gold_case_order": case_order,
            "gold_case_id": gold_case_id,
            "selection_policy_version": gold_policy[
                "selection_policy_version"
            ],
            **record["profile"],
        }
        records.append(task)
    return records


def build_case_annotations(gold_tasks: list[dict]) -> pd.DataFrame:
    """용어 단위 최종 상태를 사람이 기록할 CSV를 만든다."""
    rows: list[dict] = []
    for task in gold_tasks:
        metadata = task["gold_set_metadata"]
        rows.append(
            {
                "gold_case_order": metadata["gold_case_order"],
                "gold_case_id": metadata["gold_case_id"],
                "term_review_task_id": task["term_review_task_id"],
                "resolution_case_id": task["resolution_case_id"],
                "canonical_term": task["canonical_term"],
                "category": task["category"],
                "entity_type_proposal": task["entity_type_proposal"],
                "problem_count": task["problem_count"],
                "candidate_count": metadata["candidate_count"],
                "source_count": metadata["source_count"],
                "code_alternative_count": metadata["alternative_count"],
                "candidate_count_bucket": metadata[
                    "candidate_count_bucket"
                ],
                "retrieval_profile": metadata["retrieval_profile"],
                "multi_source_supported": metadata[
                    "multi_source_supported"
                ],
                "conflict_pair_count": metadata["conflict_pair_count"],
                "problem_context_samples_json": dumps(
                    task["problem_context_samples"],
                    ensure_ascii=False,
                ),
                "gold_link_status": "",
                "requires_problem_review": "",
                "gold_decision_reason": "",
                "reviewer": "",
                "case_review_status": "NOT_STARTED",
            }
        )
    return pd.DataFrame(rows)


def build_candidate_annotations(
    gold_tasks: list[dict],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """후보 근거와 코드 제안값을 분리해 블라인드 검수 CSV를 만든다."""
    annotation_rows: list[dict] = []
    baseline_rows: list[dict] = []
    for task in gold_tasks:
        metadata = task["gold_set_metadata"]
        pairs = task["relevant_pair_signals"]
        alternative_by_id = {
            alternative["canonical_alternative_id"]: alternative
            for alternative in task["code_canonical_alternatives"]
        }
        for candidate in task["source_candidates"]:
            candidate_id = candidate["source_candidate_id"]
            candidate_pairs = [
                pair
                for pair in pairs
                if candidate_id
                in {
                    pair["left_source_candidate_id"],
                    pair["right_source_candidate_id"],
                }
            ]
            annotation_rows.append(
                {
                    "gold_case_order": metadata["gold_case_order"],
                    "gold_case_id": metadata["gold_case_id"],
                    "term_review_task_id": task["term_review_task_id"],
                    "resolution_case_id": task["resolution_case_id"],
                    "canonical_term": task["canonical_term"],
                    "category": task["category"],
                    "problem_context_samples_json": dumps(
                        task["problem_context_samples"],
                        ensure_ascii=False,
                    ),
                    "source_candidate_id": candidate_id,
                    "source_record_id": candidate["source_record_id"],
                    "source": candidate["source"],
                    "candidate_rank": candidate["candidate_rank"],
                    "matched_name": candidate["matched_name"],
                    "matched_field": candidate["matched_field"],
                    "retrieval_method": candidate["retrieval_method"],
                    "retrieval_score": candidate["retrieval_score"],
                    "category_compatibility": candidate[
                        "category_compatibility"
                    ],
                    "normalized_names_json": dumps(
                        candidate["normalized_names"],
                        ensure_ascii=False,
                    ),
                    "hanja_json": dumps(
                        candidate["hanja"],
                        ensure_ascii=False,
                    ),
                    "era_values_json": dumps(
                        candidate["era_values"],
                        ensure_ascii=False,
                    ),
                    "birth_year": candidate["birth_year"],
                    "death_year": candidate["death_year"],
                    "bonkwan_json": dumps(
                        candidate["bonkwan"],
                        ensure_ascii=False,
                    ),
                    "source_entity_type_proposal": candidate[
                        "source_entity_type_proposal"
                    ],
                    "source_context_json": dumps(
                        candidate["source_context"],
                        ensure_ascii=False,
                    ),
                    "candidate_pair_signals_json": dumps(
                        candidate_pairs,
                        ensure_ascii=False,
                    ),
                    "gold_candidate_role": "",
                    "gold_alternative_key": "",
                    "gold_display_name": "",
                    "gold_entity_type": "",
                    "gold_related_entity_key": "",
                    "gold_related_display_name": "",
                    "gold_related_entity_type": "",
                    "gold_reason": "",
                }
            )
            alternative_id = candidate["code_canonical_alternative_id"]
            alternative = alternative_by_id.get(alternative_id, {})
            baseline_rows.append(
                {
                    "gold_case_order": metadata["gold_case_order"],
                    "gold_case_id": metadata["gold_case_id"],
                    "source_candidate_id": candidate_id,
                    "code_proposed_role": candidate["code_proposed_role"],
                    "code_canonical_alternative_id": alternative_id,
                    "code_confidence_tier": alternative.get(
                        "confidence_tier",
                        "",
                    ),
                    "code_merge_signals_json": dumps(
                        alternative.get("merge_signals", []),
                        ensure_ascii=False,
                    ),
                }
            )
    return pd.DataFrame(annotation_rows), pd.DataFrame(baseline_rows)


def build_distribution(
    all_tasks: list[dict],
    gold_tasks: list[dict],
    policy: dict,
) -> pd.DataFrame:
    """전체 모집단과 표본의 주요 층별 분포를 비교한다."""
    gold_policy = policy["entity_resolution"]["semantic_review"]["gold_set"]
    population_profiles = [
        profile_term_task(task, gold_policy) for task in all_tasks
    ]
    sample_profiles = [task["gold_set_metadata"] for task in gold_tasks]
    dimensions = [
        "category",
        "candidate_count_bucket",
        "retrieval_profile",
        "multi_source_supported",
        "conflict_present",
    ]
    rows: list[dict] = []
    for dimension in dimensions:
        population_counts = Counter(
            str(profile[dimension]) for profile in population_profiles
        )
        sample_counts = Counter(
            str(profile[dimension]) for profile in sample_profiles
        )
        values = sorted(set(population_counts).union(sample_counts))
        for value in values:
            population_count = population_counts[value]
            sample_count = sample_counts[value]
            rows.append(
                {
                    "dimension": dimension,
                    "value": value,
                    "population_count": population_count,
                    "population_percent": population_count
                    / len(population_profiles),
                    "sample_count": sample_count,
                    "sample_percent": sample_count / len(sample_profiles),
                }
            )
    return pd.DataFrame(rows)


def calculate_file_sha256(input_path: str) -> str:
    """입력 task 스냅샷을 감사할 수 있도록 파일 해시를 계산한다."""
    digest = sha256()
    with open(input_path, "rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def annotation_file_has_started(
    annotation_path: Path,
    annotation_fields: list[str],
    status_field: str,
    initial_status: str,
) -> bool:
    """기존 검수 CSV에 사람이 입력한 값이 있는지 확인한다."""
    if not annotation_path.is_file():
        return False
    annotations = pd.read_csv(
        annotation_path,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
    )
    for field_name in annotation_fields:
        if field_name not in annotations.columns:
            continue
        if annotations[field_name].str.strip().ne("").any():
            return True
    if status_field and status_field in annotations.columns:
        statuses = annotations[status_field].str.strip()
        started_statuses = statuses[~statuses.isin({"", initial_status})]
        if not started_statuses.empty:
            return True
    return False


def protect_started_review_files(
    case_path: Path,
    candidate_path: Path,
    gold_policy: dict,
    allow_review_overwrite: bool,
) -> None:
    """검수가 시작된 사람 작성 CSV의 실수 덮어쓰기를 막는다."""
    if allow_review_overwrite:
        return
    importer_policy = gold_policy["importer"]
    protection_policy = importer_policy["overwrite_protection"]
    initial_status = str(importer_policy["initial_review_status"])
    case_started = annotation_file_has_started(
        case_path,
        protection_policy["case_annotation_fields"],
        str(protection_policy["case_status_field"]),
        initial_status,
    )
    candidate_started = annotation_file_has_started(
        candidate_path,
        protection_policy["candidate_annotation_fields"],
        str(protection_policy["candidate_status_field"]),
        initial_status,
    )
    if case_started or candidate_started:
        raise FileExistsError(
            "검수가 시작된 골든셋 CSV는 자동으로 덮어쓸 수 없습니다. "
            "내용을 확인한 뒤 --force-overwrite-review를 명시하세요."
        )


def write_gold_set(
    all_tasks: list[dict],
    input_path: str,
    output_dir: str,
    policy: dict,
    generated_at: str = "",
    review_output_dir: str = "",
    allow_review_overwrite: bool = False,
) -> dict[str, str]:
    """
    표본 task·검수 CSV·분포·감사 manifest를 한 번에 저장한다.
    review_output_dir를 주면 검수용 case·candidate CSV는 그 폴더에
    importer 입력 파일명(human_review_*.csv)으로 저장한다.
    검수가 시작된 기존 CSV는 명시적 허용 없이는 덮어쓰지 않는다.
    """
    gold_policy = policy["entity_resolution"]["semantic_review"]["gold_set"]
    selected = select_gold_tasks(all_tasks, policy)
    gold_tasks = build_gold_task_records(selected, policy)
    case_annotations = build_case_annotations(gold_tasks)
    candidate_annotations, code_baseline = build_candidate_annotations(
        gold_tasks
    )
    distribution = build_distribution(all_tasks, gold_tasks, policy)
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_files = gold_policy["output_files"]
    output_paths = {
        name: output_directory / filename
        for name, filename in output_files.items()
    }
    if review_output_dir:
        review_directory = Path(review_output_dir)
        review_directory.mkdir(parents=True, exist_ok=True)
        review_file_names = gold_policy["importer"]["csv_input_files"]
        output_paths["case_annotations"] = (
            review_directory / review_file_names["case_annotations"]
        )
        output_paths["candidate_annotations"] = (
            review_directory / review_file_names["candidate_annotations"]
        )
        protect_started_review_files(
            output_paths["case_annotations"],
            output_paths["candidate_annotations"],
            gold_policy,
            allow_review_overwrite,
        )
    write_jsonl(gold_tasks, str(output_paths["gold_tasks"]))
    case_annotations.to_csv(
        output_paths["case_annotations"],
        index=False,
        encoding="utf-8-sig",
    )
    candidate_annotations.to_csv(
        output_paths["candidate_annotations"],
        index=False,
        encoding="utf-8-sig",
    )
    code_baseline.to_csv(
        output_paths["code_baseline"],
        index=False,
        encoding="utf-8-sig",
    )
    distribution.to_csv(
        output_paths["distribution"],
        index=False,
        encoding="utf-8-sig",
    )
    creation_time = generated_at
    if not creation_time:
        creation_time = datetime.now(timezone.utc).isoformat()
    manifest = {
        "selection_policy_version": gold_policy["selection_policy_version"],
        "resolution_policy_version": policy["policy_version"],
        "normalization_policy_version": policy[
            "normalization_policy_version"
        ],
        "input_task_path": str(Path(input_path).resolve()),
        "input_task_sha256": calculate_file_sha256(input_path),
        "population_case_count": len(all_tasks),
        "sample_case_count": len(gold_tasks),
        "sample_candidate_count": len(candidate_annotations),
        "generated_at": creation_time,
        "output_files": {
            name: str(path.resolve()) for name, path in output_paths.items()
        },
    }
    with output_paths["manifest"].open("w", encoding="utf-8") as output_file:
        output_file.write(dumps(manifest, ensure_ascii=False, indent=2))
    return {name: str(path) for name, path in output_paths.items()}


if __name__ == "__main__":
    parser = ArgumentParser(
        description="Entity Resolution term task에서 층화 골든셋 검수 파일 생성"
    )
    parser.add_argument(
        "input_tasks",
        nargs="?",
        default="",
        help="term review task JSONL 경로 (생략하면 본 실행 출력 경로 사용)",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default="",
        help="골든셋 출력 디렉터리 (생략하면 정책의 default_output_directory 사용)",
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
    parser.add_argument(
        "--force-overwrite-review",
        action="store_true",
        help="검수가 시작된 human_review_csv도 확인 후 강제로 다시 생성",
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    gold_set_policy = pipeline_policy["entity_resolution"]["semantic_review"][
        "gold_set"
    ]
    input_tasks_argument = cli_args.input_tasks
    if not input_tasks_argument:
        # 본 실행 파이프라인의 term task 출력 경로를 정책 레이아웃에서 조립한다
        neo4j_root = Path(__file__).resolve().parent.parent
        output_layout = pipeline_policy["output_layout"]
        input_tasks_path = (
            neo4j_root
            / output_layout["default_output_root"]
            / output_layout["directories"]["llm_review"]
            / pipeline_policy["entity_resolution"]["semantic_review"][
                "term_task_file"
            ]
        )
        if not input_tasks_path.is_file():
            raise FileNotFoundError(
                f"term task JSONL이 없습니다: {input_tasks_path}\n"
                "run_neo4j_preprocessing.py를 먼저 실행하거나 경로를 인자로 지정하세요."
            )
        input_tasks_argument = str(input_tasks_path)

    output_directory_argument = cli_args.output_dir
    review_directory_argument = ""
    if not output_directory_argument:
        # 인자 생략 시: 내부 산출물은 snapshot 폴더에, 검수 CSV는
        # human_review_csv에 importer 파일명으로 저장한다 (기존 검수본 덮어씀)
        neo4j_root = Path(__file__).resolve().parent.parent
        output_directory_argument = str(
            neo4j_root / gold_set_policy["default_output_directory"]
        )
        review_directory_argument = str(
            neo4j_root / gold_set_policy["review_csv_directory"]
        )
    term_tasks = load_jsonl(input_tasks_argument)
    written_paths = write_gold_set(
        term_tasks,
        input_tasks_argument,
        output_directory_argument,
        pipeline_policy,
        review_output_dir=review_directory_argument,
        allow_review_overwrite=cli_args.force_overwrite_review,
    )
    print(dumps(written_paths, ensure_ascii=False, indent=2))

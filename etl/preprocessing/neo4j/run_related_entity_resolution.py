import sys
from argparse import ArgumentParser
from json import dump
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent / "terms"))

from common import load_pipeline_policy
from entity_resolution.build_resolution_package import (
    build_resolution_tables,
    summarize_resolution_tables,
    write_resolution_package,
)
from entity_resolution.related_entity_resolution import (
    build_related_term_table,
    inject_related_entity_seed_candidates,
)
from entity_resolution.semantic_review import (
    build_term_review_tasks,
    load_jsonl,
    write_jsonl,
)
from goldset.build_gold_set import calculate_file_sha256
from match_names import match_names
from run_neo4j_preprocessing import resolve_pipeline_paths
from scan_body_mentions import scan_body_mentions
from scan_definitions import scan_definitions


def resolve_related_entity_paths(
    queue_path: str,
    output_dir: str,
    policy: dict,
) -> dict[str, Path]:
    """명시 경로를 우선하고 관련 엔티티 정책의 기본 경로를 적용한다."""
    neo4j_root = Path(__file__).resolve().parent
    related_policy = policy["entity_resolution"][
        "related_entity_resolution"
    ]
    importer_files = policy["entity_resolution"]["semantic_review"][
        "gold_set"
    ]["importer"]["output_files"]
    resolved_queue_path = Path(queue_path)
    if not queue_path:
        workflow_policy = policy["entity_resolution"]["semantic_review"][
            "gold_set"
        ]["workflow"]
        resolved_queue_path = (
            neo4j_root / workflow_policy["validation_directory"]
            / importer_files["related_entity_tasks"]
        )
    resolved_output_directory = Path(output_dir)
    if not output_dir:
        resolved_output_directory = (
            neo4j_root / related_policy["default_output_directory"]
        )
    output_files = related_policy["output_files"]
    return {
        "queue": resolved_queue_path,
        "output_directory": resolved_output_directory,
        "terms": resolved_output_directory / output_files["terms"],
        "name_matches": resolved_output_directory
        / output_files["name_matches"],
        "definition_matches": resolved_output_directory
        / output_files["definition_matches"],
        "body_mention_matches": resolved_output_directory
        / output_files["body_mention_matches"],
        "resolution_package": resolved_output_directory
        / output_files["resolution_package"],
        "term_review_tasks": resolved_output_directory
        / output_files["term_review_tasks"],
        "manifest": resolved_output_directory / output_files["manifest"],
    }


def run_related_entity_resolution(
    queue_path: str,
    output_dir: str,
    thesaurus_csv_path: str,
    encyclopedia_jsonl_path: str,
    itkc_people_csv_path: str,
    itkc_events_csv_path: str,
    policy_path: str,
) -> dict[str, object]:
    """관련 엔티티 seed를 전 원천 재검색 후 기존 ER review task로 변환한다."""
    policy = load_pipeline_policy(policy_path)
    paths = resolve_related_entity_paths(queue_path, output_dir, policy)
    required_inputs = {
        "related entity queue": paths["queue"],
        "thesaurus": Path(thesaurus_csv_path),
        "encyclopedia": Path(encyclopedia_jsonl_path),
        "ITKC people": Path(itkc_people_csv_path),
        "ITKC events": Path(itkc_events_csv_path),
    }
    for input_name, input_path in required_inputs.items():
        if not input_path.is_file():
            raise FileNotFoundError(
                f"{input_name} 입력 파일을 찾을 수 없습니다: {input_path}"
            )
    for path_name, output_path in paths.items():
        if path_name != "queue":
            output_path.parent.mkdir(parents=True, exist_ok=True)

    related_tasks = load_jsonl(str(paths["queue"]))
    if not related_tasks:
        raise ValueError("관련 엔티티 resolution queue가 비어 있습니다.")
    term_table = build_related_term_table(related_tasks)
    term_table.to_csv(paths["terms"], index=False, encoding="utf-8-sig")

    name_matches = match_names(
        terms_csv=str(paths["terms"]),
        thesaurus_csv=thesaurus_csv_path,
        encyclopedia_jsonl=encyclopedia_jsonl_path,
        itkc_people_csv=itkc_people_csv_path,
        itkc_events_csv=itkc_events_csv_path,
        policy=policy,
    )
    name_matches = inject_related_entity_seed_candidates(
        name_matches,
        related_tasks,
        policy,
    )
    with paths["name_matches"].open("w", encoding="utf-8") as output_file:
        dump(name_matches, output_file, ensure_ascii=False, indent=2)

    definition_matches = scan_definitions(
        match_json=str(paths["name_matches"]),
        encyclopedia_jsonl=encyclopedia_jsonl_path,
        policy=policy,
    )
    with paths["definition_matches"].open(
        "w",
        encoding="utf-8",
    ) as output_file:
        dump(definition_matches, output_file, ensure_ascii=False, indent=2)

    body_mention_matches = scan_body_mentions(
        match_json=str(paths["name_matches"]),
        encyclopedia_jsonl=encyclopedia_jsonl_path,
        policy=policy,
    )
    with paths["body_mention_matches"].open(
        "w",
        encoding="utf-8",
    ) as output_file:
        dump(body_mention_matches, output_file, ensure_ascii=False, indent=2)

    empty_problem_contexts = pd.DataFrame(
        columns=["problem_id", "full_text"]
    )
    resolution_tables = build_resolution_tables(
        name_matches,
        definition_matches,
        empty_problem_contexts,
        policy,
        body_mention_results=body_mention_matches,
    )
    resolution_paths = write_resolution_package(
        resolution_tables,
        str(paths["resolution_package"]),
        policy,
    )
    review_tasks = build_term_review_tasks(resolution_tables, policy)
    write_jsonl(review_tasks, str(paths["term_review_tasks"]))

    manifest = {
        "queue_path": str(paths["queue"].resolve()),
        "queue_sha256": calculate_file_sha256(str(paths["queue"])),
        "related_entity_count": len(related_tasks),
        "review_task_count": len(review_tasks),
        "resolution_summary": summarize_resolution_tables(
            resolution_tables
        ),
        "resolution_policy_version": policy["policy_version"],
        "output_files": {
            "terms": str(paths["terms"].resolve()),
            "name_matches": str(paths["name_matches"].resolve()),
            "definition_matches": str(
                paths["definition_matches"].resolve()
            ),
            "body_mention_matches": str(
                paths["body_mention_matches"].resolve()
            ),
            "term_review_tasks": str(
                paths["term_review_tasks"].resolve()
            ),
            "resolution_package": resolution_paths,
        },
    }
    with paths["manifest"].open("w", encoding="utf-8") as output_file:
        dump(manifest, output_file, ensure_ascii=False, indent=2)
    return manifest
if __name__ == "__main__":
    parser = ArgumentParser(
        description="EVIDENCE_ONLY 관련 엔티티를 2차 Entity Resolution에 투입"
    )
    parser.add_argument("queue_path", nargs="?", default="")
    parser.add_argument("output_dir", nargs="?", default="")
    parser.add_argument("--thesaurus", default="")
    parser.add_argument("--encyclopedia-jsonl", default="")
    parser.add_argument("--itkc-people", default="")
    parser.add_argument("--itkc-events", default="")
    parser.add_argument(
        "--policy",
        default=str(
            Path(__file__).resolve().parent
            / "config"
            / "resolution_policy.json"
        ),
    )
    cli_args = parser.parse_args()
    pipeline_paths = resolve_pipeline_paths(
        thesaurus_csv_path=cli_args.thesaurus,
        encyclopedia_jsonl_path=cli_args.encyclopedia_jsonl,
        itkc_people_csv_path=cli_args.itkc_people,
        itkc_events_csv_path=cli_args.itkc_events,
    )
    result = run_related_entity_resolution(
        queue_path=cli_args.queue_path,
        output_dir=cli_args.output_dir,
        thesaurus_csv_path=pipeline_paths["thesaurus_csv_path"],
        encyclopedia_jsonl_path=pipeline_paths[
            "encyclopedia_jsonl_path"
        ],
        itkc_people_csv_path=pipeline_paths["itkc_people_csv_path"],
        itkc_events_csv_path=pipeline_paths["itkc_events_csv_path"],
        policy_path=cli_args.policy,
    )
    print(result)

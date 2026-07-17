"""검수된 AKS 근거에서 왕의 정책·업적 행위 graph CSV를 생성한다."""

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from neo4j_common import require_file, resolve_import_dir, resolve_project_root


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)

    return {
        "source_seed": neo4j_dir / "seed" / "aks_source_seed.csv",
        "action_seed": neo4j_dir / "seed" / "aks_royal_action_seed.csv",
        "action_rule_seed": neo4j_dir / "seed" / "royal_action_rule_seed.csv",
        "articles_path": project_root
        / "etl"
        / "raw_data"
        / "한국민족문화대백과사전"
        / "articles_detail.jsonl",
        "reigns_path": import_dir / "nodes" / "reigns.csv",
        "reign_polity_path": import_dir / "relations" / "reign_of_polity.csv",
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
        "candidate_path": neo4j_dir
        / "staging"
        / "aks_royal_action_candidates.csv",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="검수된 AKS 근거에서 RoyalAction CSV를 생성한다."
    )
    parser.add_argument(
        "--source-seed-path",
        type=Path,
        default=default_paths["source_seed"],
    )
    parser.add_argument(
        "--action-seed-path",
        type=Path,
        default=default_paths["action_seed"],
    )
    parser.add_argument(
        "--action-rule-seed-path",
        type=Path,
        default=default_paths["action_rule_seed"],
    )
    parser.add_argument(
        "--articles-path",
        type=Path,
        default=default_paths["articles_path"],
    )
    parser.add_argument(
        "--reigns-path",
        type=Path,
        default=default_paths["reigns_path"],
    )
    parser.add_argument(
        "--reign-polity-path",
        type=Path,
        default=default_paths["reign_polity_path"],
    )
    parser.add_argument("--nodes-dir", type=Path, default=default_paths["nodes_dir"])
    parser.add_argument(
        "--relations-dir",
        type=Path,
        default=default_paths["relations_dir"],
    )
    parser.add_argument(
        "--candidate-path",
        type=Path,
        default=default_paths["candidate_path"],
    )
    parser.add_argument("--save", action="store_true")

    return parser.parse_args()


def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def normalize_evidence_text(value):
    normalized = clean_text(value)
    normalized = normalized.replace("·", " ").replace("ㆍ", " ")
    normalized = re.sub(r"[\s,]+", " ", normalized)
    return normalized.strip()


def read_csv_rows(csv_path, purpose):
    require_file(csv_path, purpose)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return [
            {key: clean_text(value) for key, value in row.items()}
            for row in csv.DictReader(csv_file)
        ]


def read_source_config(source_seed_path):
    rows = read_csv_rows(source_seed_path, "AKS source seed")

    if len(rows) != 1:
        raise ValueError("AKS source seed에는 설정이 정확히 한 행이어야 합니다.")

    source_config = rows[0]
    required_values = {
        "source_id",
        "source_record_id_prefix",
        "canonical_id_prefix",
    }
    missing_values = sorted(
        column_name
        for column_name in required_values
        if source_config.get(column_name, "") == ""
    )

    if len(missing_values) > 0:
        raise ValueError(
            "AKS source seed 필수값이 비어 있습니다: " + ", ".join(missing_values)
        )

    return source_config


def read_action_seed(action_seed_path):
    rows = read_csv_rows(action_seed_path, "AKS royal action seed")
    required_columns = {
        "action_id",
        "monarch_eid",
        "polity_id",
        "target_eid",
        "target_kind",
        "action_type",
        "actor_role",
        "action_label",
        "action_date_text",
        "date_precision",
        "evidence_source_eid",
        "evidence_field",
        "evidence_text",
        "certainty",
        "review_status",
    }
    action_ids = []

    for row in rows:
        missing_columns = sorted(
            column_name
            for column_name in required_columns
            if row.get(column_name, "") == ""
        )

        if len(missing_columns) > 0:
            raise ValueError(
                f"Royal action seed 필수값이 비어 있습니다: {row.get('action_id', '')} "
                + ", ".join(missing_columns)
            )

        for year_column in ("start_year", "end_year"):
            year_text = row.get(year_column, "")

            if year_text != "" and not re.fullmatch(r"-?\d+", year_text):
                raise ValueError(
                    f"Royal action seed 연도가 정수가 아닙니다: "
                    f"{row['action_id']} {year_column}={year_text}"
                )

        action_ids.append(row["action_id"])

    if len(action_ids) != len(set(action_ids)):
        raise ValueError("Royal action seed의 action_id가 중복되었습니다.")

    return rows


def read_action_rule_seed(action_rule_seed_path):
    rows = read_csv_rows(action_rule_seed_path, "Royal action rule seed")
    rule_ids = []

    for row in rows:
        required_columns = {"rule_id", "action_type", "verb_pattern"}
        missing_columns = sorted(
            column_name
            for column_name in required_columns
            if row.get(column_name, "") == ""
        )

        if len(missing_columns) > 0:
            raise ValueError(
                "Royal action rule seed 필수값이 비어 있습니다: "
                + ", ".join(missing_columns)
            )

        try:
            row["compiled_pattern"] = re.compile(row["verb_pattern"])
        except re.error as exc:
            raise ValueError(
                f"Royal action rule regex가 올바르지 않습니다: {row['rule_id']}"
            ) from exc

        rule_ids.append(row["rule_id"])

    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("Royal action rule seed의 rule_id가 중복되었습니다.")

    return rows


def load_required_articles(articles_path, action_rows):
    require_file(articles_path, "AKS articles_detail")
    required_eids = set()

    for row in action_rows:
        required_eids.update(
            {
                row["monarch_eid"],
                row["target_eid"],
                row["evidence_source_eid"],
            }
        )

    articles = {}

    with articles_path.open("r", encoding="utf-8-sig") as articles_file:
        for line_number, raw_line in enumerate(articles_file, 1):
            if raw_line.strip() == "":
                continue

            try:
                article = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"AKS JSONL parsing failed at line {line_number}: {exc}"
                ) from exc

            source_eid = clean_text(article.get("eid"))

            if source_eid in required_eids:
                articles[source_eid] = article

    missing_eids = sorted(required_eids - set(articles))

    if len(missing_eids) > 0:
        raise ValueError(
            "Royal action seed가 참조한 AKS 문서를 찾지 못했습니다: "
            + ", ".join(missing_eids)
        )

    return articles


def validate_evidence(action_rows, articles):
    allowed_fields = {"definition", "summary", "body"}

    for row in action_rows:
        evidence_field = row["evidence_field"]

        if evidence_field not in allowed_fields:
            raise ValueError(
                f"지원하지 않는 evidence_field입니다: {row['action_id']} "
                f"{evidence_field}"
            )

        evidence_article = articles[row["evidence_source_eid"]]
        source_text = normalize_evidence_text(evidence_article.get(evidence_field))
        evidence_text = normalize_evidence_text(row["evidence_text"])

        if evidence_text not in source_text:
            raise ValueError(
                f"근거 문구가 AKS 원문과 일치하지 않습니다: {row['action_id']}"
            )


def build_reign_lookup(reigns_path, reign_polity_path):
    reign_rows = read_csv_rows(reigns_path, "Reign nodes")
    reign_polity_rows = read_csv_rows(reign_polity_path, "Reign-Polity relations")
    polity_by_reign_id = {
        row["start_reign_id"]: row["end_polity_id"]
        for row in reign_polity_rows
    }
    reigns_by_monarch_eid = defaultdict(list)

    for reign_row in reign_rows:
        reign_id = reign_row["reign_id"]

        if reign_id not in polity_by_reign_id:
            raise ValueError(f"Polity 관계가 없는 Reign입니다: {reign_id}")

        reign_row["polity_id"] = polity_by_reign_id[reign_id]
        reigns_by_monarch_eid[reign_row["anchor_source_eid"]].append(reign_row)

    return reigns_by_monarch_eid


def action_overlaps_reign(action_row, reign_row):
    action_start_text = action_row.get("start_year", "")
    action_end_text = action_row.get("end_year", "")
    reign_start_text = reign_row.get("start_year", "")
    reign_end_text = reign_row.get("end_year", "")

    if action_start_text == "" and action_end_text == "":
        return True

    if reign_start_text == "" or reign_end_text == "":
        return False

    action_start = int(action_start_text or action_end_text)
    action_end = int(action_end_text or action_start_text)
    reign_start = int(reign_start_text)
    reign_end = int(reign_end_text)
    return action_start <= reign_end and action_end >= reign_start


def resolve_action_reigns(action_rows, reigns_by_monarch_eid):
    reign_by_action_id = {}

    for action_row in action_rows:
        candidates = [
            reign_row
            for reign_row in reigns_by_monarch_eid.get(
                action_row["monarch_eid"],
                [],
            )
            if reign_row["polity_id"] == action_row["polity_id"]
            and action_overlaps_reign(action_row, reign_row)
        ]

        if len(candidates) != 1:
            candidate_ids = ", ".join(
                sorted(reign_row["reign_id"] for reign_row in candidates)
            )
            raise ValueError(
                f"Royal action의 Reign을 하나로 확정할 수 없습니다: "
                f"{action_row['action_id']} candidates={candidate_ids}"
            )

        reign_by_action_id[action_row["action_id"]] = candidates[0]

    return reign_by_action_id


def remove_contained_target_names(target_matches):
    sorted_matches = sorted(
        target_matches,
        key=lambda match: (-len(match["name"]), match["name"], match["eid"]),
    )
    selected_matches = []

    for target_match in sorted_matches:
        contained_by_selected = any(
            target_match["name"] != selected_match["name"]
            and target_match["name"] in selected_match["name"]
            for selected_match in selected_matches
        )

        if not contained_by_selected:
            selected_matches.append(target_match)

    return sorted(
        selected_matches,
        key=lambda match: (match["name"], match["eid"]),
    )


def build_candidate_rows(articles_path, monarch_eids, action_rule_rows):
    require_file(articles_path, "AKS articles_detail")
    name_index = defaultdict(list)
    monarch_articles = {}

    with articles_path.open("r", encoding="utf-8-sig") as articles_file:
        for line_number, raw_line in enumerate(articles_file, 1):
            if raw_line.strip() == "":
                continue

            try:
                article = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"AKS JSONL parsing failed at line {line_number}: {exc}"
                ) from exc

            source_eid = clean_text(article.get("eid"))
            headword = clean_text(article.get("headword"))

            if len(headword) >= 2:
                name_index[headword[0]].append(
                    {
                        "eid": source_eid,
                        "name": headword,
                        "primary_type": clean_text(article.get("primaryType")),
                    }
                )

            if source_eid in monarch_eids:
                monarch_articles[source_eid] = article

    missing_monarch_eids = sorted(set(monarch_eids) - set(monarch_articles))

    if len(missing_monarch_eids) > 0:
        raise ValueError(
            "Royal action 후보 생성 중 왕 문서를 찾지 못했습니다: "
            + ", ".join(missing_monarch_eids)
        )

    candidate_rows = []

    for monarch_eid, article in sorted(monarch_articles.items()):
        monarch_name = clean_text(article.get("headword"))
        summary = clean_text(article.get("summary"))
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", summary)
            if sentence.strip() != ""
        ]

        for sentence_index, sentence in enumerate(sentences, 1):
            matched_action_types = sorted(
                {
                    rule_row["action_type"]
                    for rule_row in action_rule_rows
                    if rule_row["compiled_pattern"].search(sentence) is not None
                }
            )

            if len(matched_action_types) == 0:
                continue

            target_matches_by_eid = {}

            for first_character in set(sentence):
                for target_match in name_index.get(first_character, []):
                    if target_match["eid"] == monarch_eid:
                        continue

                    if target_match["name"] not in sentence:
                        continue

                    target_matches_by_eid[target_match["eid"]] = target_match

            target_matches = remove_contained_target_names(
                list(target_matches_by_eid.values())
            )
            review_reason = "NO_CANONICAL_TARGET"

            if len(target_matches) == 1:
                review_reason = "SINGLE_TARGET_CANDIDATE"
            elif len(target_matches) > 1:
                review_reason = "MULTIPLE_TARGET_CANDIDATES"

            candidate_rows.append(
                {
                    "candidate_id": (
                        f"ROYAL_ACTION_CANDIDATE:{monarch_eid}:{sentence_index:02d}"
                    ),
                    "monarch_eid": monarch_eid,
                    "monarch_name": monarch_name,
                    "sentence_index": str(sentence_index),
                    "action_types": "|".join(matched_action_types),
                    "evidence_sentence": sentence,
                    "target_count": str(len(target_matches)),
                    "target_candidates": "|".join(
                        f"{target_match['eid']}:{target_match['name']}"
                        for target_match in target_matches
                    ),
                    "target_primary_types": "|".join(
                        sorted(
                            {
                                target_match["primary_type"]
                                for target_match in target_matches
                                if target_match["primary_type"] != ""
                            }
                        )
                    ),
                    "review_reason": review_reason,
                }
            )

    return candidate_rows


def build_output_rows(action_rows, articles, reign_by_action_id, source_config):
    outputs = {
        "royal_actions": [],
        "monarch_associated_with_royal_action": [],
        "royal_action_targets_entity": [],
        "royal_action_during_reign": [],
        "source_article_evidence_for_royal_action": [],
    }

    for row in action_rows:
        monarch_article = articles[row["monarch_eid"]]
        target_article = articles[row["target_eid"]]
        monarch_canonical_id = (
            f"{source_config['canonical_id_prefix']}:{row['monarch_eid']}"
        )
        target_canonical_id = (
            f"{source_config['canonical_id_prefix']}:{row['target_eid']}"
        )
        evidence_source_record_id = (
            f"{source_config['source_record_id_prefix']}:"
            f"{row['evidence_source_eid']}"
        )
        reign_id = reign_by_action_id[row["action_id"]]["reign_id"]
        outputs["royal_actions"].append(
            {
                "action_id": row["action_id"],
                "name": row["action_label"],
                "action_type": row["action_type"],
                "action_date_text": row["action_date_text"],
                "date_precision": row["date_precision"],
                "start_year": row.get("start_year", ""),
                "end_year": row.get("end_year", ""),
                # monarch_name·target_name·target_kind는 ASSOCIATED_WITH_ACTION·
                # TARGETS 엣지로 표현되므로 노드 속성에서 제외한다
                # (docs/neo4j/neo4j_관계_정규화_점검.md 발견 4).
                "certainty": row["certainty"],
                "anchor_source_id": source_config["source_id"],
                "evidence_source_eid": row["evidence_source_eid"],
                "review_status": row["review_status"],
                "note": row.get("note", ""),
            }
        )
        outputs["monarch_associated_with_royal_action"].append(
            {
                "start_canonical_id": monarch_canonical_id,
                "end_action_id": row["action_id"],
                "relation_type": "ASSOCIATED_WITH_ACTION",
                "actor_role": row["actor_role"],
                "certainty": row["certainty"],
                "review_status": row["review_status"],
            }
        )
        outputs["royal_action_targets_entity"].append(
            {
                "start_action_id": row["action_id"],
                "end_canonical_id": target_canonical_id,
                "relation_type": "TARGETS",
                "target_kind": row["target_kind"],
                "review_status": row["review_status"],
            }
        )
        outputs["royal_action_during_reign"].append(
            {
                "start_action_id": row["action_id"],
                "end_reign_id": reign_id,
                "relation_type": "DURING_REIGN",
                "review_status": row["review_status"],
            }
        )
        outputs["source_article_evidence_for_royal_action"].append(
            {
                "start_source_record_id": evidence_source_record_id,
                "end_action_id": row["action_id"],
                "relation_type": "EVIDENCE_FOR",
                "evidence_field": row["evidence_field"],
                "evidence_text": row["evidence_text"],
                "certainty": row["certainty"],
                "review_status": row["review_status"],
            }
        )

    return outputs


def build_output_specs(args, outputs, candidate_rows):
    return {
        "royal_actions": {
            "path": args.nodes_dir / "royal_actions.csv",
            "rows": outputs["royal_actions"],
            "fieldnames": [
                "action_id",
                "name",
                "action_type",
                "action_date_text",
                "date_precision",
                "start_year",
                "end_year",
                "certainty",
                "anchor_source_id",
                "evidence_source_eid",
                "review_status",
                "note",
            ],
        },
        "monarch_associated_with_royal_action": {
            "path": args.relations_dir
            / "monarch_associated_with_royal_action.csv",
            "rows": outputs["monarch_associated_with_royal_action"],
            "fieldnames": [
                "start_canonical_id",
                "end_action_id",
                "relation_type",
                "actor_role",
                "certainty",
                "review_status",
            ],
        },
        "royal_action_targets_entity": {
            "path": args.relations_dir / "royal_action_targets_entity.csv",
            "rows": outputs["royal_action_targets_entity"],
            "fieldnames": [
                "start_action_id",
                "end_canonical_id",
                "relation_type",
                "target_kind",
                "review_status",
            ],
        },
        "royal_action_during_reign": {
            "path": args.relations_dir / "royal_action_during_reign.csv",
            "rows": outputs["royal_action_during_reign"],
            "fieldnames": [
                "start_action_id",
                "end_reign_id",
                "relation_type",
                "review_status",
            ],
        },
        "source_article_evidence_for_royal_action": {
            "path": args.relations_dir
            / "source_article_evidence_for_royal_action.csv",
            "rows": outputs["source_article_evidence_for_royal_action"],
            "fieldnames": [
                "start_source_record_id",
                "end_action_id",
                "relation_type",
                "evidence_field",
                "evidence_text",
                "certainty",
                "review_status",
            ],
        },
        "aks_royal_action_candidates": {
            "path": args.candidate_path,
            "rows": candidate_rows,
            "fieldnames": [
                "candidate_id",
                "monarch_eid",
                "monarch_name",
                "sentence_index",
                "action_types",
                "evidence_sentence",
                "target_count",
                "target_candidates",
                "target_primary_types",
                "review_reason",
            ],
        },
    }


def save_outputs(output_specs):
    temporary_paths = []

    try:
        for output_spec in output_specs.values():
            output_path = output_spec["path"]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
            temporary_paths.append(temporary_path)

            with temporary_path.open(
                "w", encoding="utf-8-sig", newline=""
            ) as output_file:
                writer = csv.DictWriter(
                    output_file,
                    fieldnames=output_spec["fieldnames"],
                    extrasaction="raise",
                )
                writer.writeheader()
                writer.writerows(output_spec["rows"])

        for output_spec in output_specs.values():
            output_path = output_spec["path"]
            temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
            temporary_path.replace(output_path)
    except Exception:
        for temporary_path in temporary_paths:
            if temporary_path.exists():
                temporary_path.unlink()

        raise


def print_summary(output_specs, save_output):
    for output_name, output_spec in output_specs.items():
        print(f"{output_name}.csv: {len(output_spec['rows'])} rows")
        print(f"output_path: {output_spec['path']}")

    if not save_output:
        print("dry_run: no files saved. Use --save to write CSV files.")


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    source_config = read_source_config(args.source_seed_path)
    action_rows = read_action_seed(args.action_seed_path)
    action_rule_rows = read_action_rule_seed(args.action_rule_seed_path)
    articles = load_required_articles(args.articles_path, action_rows)
    validate_evidence(action_rows, articles)
    reigns_by_monarch_eid = build_reign_lookup(
        args.reigns_path,
        args.reign_polity_path,
    )
    reign_by_action_id = resolve_action_reigns(
        action_rows,
        reigns_by_monarch_eid,
    )
    candidate_rows = build_candidate_rows(
        args.articles_path,
        set(reigns_by_monarch_eid),
        action_rule_rows,
    )
    outputs = build_output_rows(
        action_rows,
        articles,
        reign_by_action_id,
        source_config,
    )
    output_specs = build_output_specs(args, outputs, candidate_rows)

    if args.save:
        save_outputs(output_specs)

    print_summary(output_specs, args.save)


if __name__ == "__main__":
    main()

"""
1차 사전 CSV를 기준으로 Neo4j 적재용 관계/매핑 테이블을 만든다.

먼저 make_base_dictionaries.py --save를 실행해 1차 사전 CSV를 저장해야 한다.
"""

import argparse
import re
from pathlib import Path

import pandas as pd


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Neo4j용 2차 관계/매핑 CSV를 만든다."
    )
    parser.add_argument(
        "--terms-path",
        default=default_paths["terms"],
        type=Path,
        help="정규화된 terms CSV 경로.",
    )
    parser.add_argument(
        "--events-path",
        default=default_paths["events"],
        type=Path,
        help="정규화된 events CSV 경로.",
    )
    parser.add_argument(
        "--dictionary-dir",
        default=default_paths["dictionary_dir"],
        type=Path,
        help="1차 사전 CSV가 저장된 폴더.",
    )
    parser.add_argument(
        "--staging-dir",
        default=default_paths["staging_dir"],
        type=Path,
        help="staging CSV 저장 폴더.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="CSV 파일을 저장한다. 지정하지 않으면 dry-run으로 요약만 출력한다.",
    )
    return parser.parse_args()


def require_file(input_path, purpose):
    if not input_path.exists():
        message = (
            f"{purpose} 파일이 없습니다: {input_path}\n"
            "먼저 make_base_dictionaries.py --save를 실행해 1차 사전을 저장하세요."
        )
        raise FileNotFoundError(message)


def split_category_paths(term_lk):
    invalid_values = {"", "_NULL_", "NULL", "None", "nan"}

    if pd.isna(term_lk):
        return []

    # term_lk에서 ">>"는 복수 카테고리 경로, ">"는 경로 안의 depth를 뜻한다.
    category_paths = []
    for raw_path in str(term_lk).split(">>"):
        path_parts = []

        for path_part in raw_path.split(">"):
            clean_path_part = path_part.strip()

            if clean_path_part not in invalid_values:
                path_parts.append(clean_path_part)

        if len(path_parts) > 0:
            category_paths.append(path_parts)

    return category_paths


def split_event_category_tokens(subject_category):
    if pd.isna(subject_category):
        return []

    # event의 subject_category는 현재 계층이 아니라 평면 라벨 묶음으로 본다.
    cleaned_text = str(subject_category).replace("\r", "\n")
    raw_tokens = re.split(r",|\n+", cleaned_text)
    tokens = []

    for raw_token in raw_tokens:
        clean_token = raw_token.strip()

        if clean_token != "":
            tokens.append(clean_token)

    return tokens


def build_term_relation_rows(terms_data):
    relation_rows = []
    target_data = terms_data.dropna(subset=["term_lk"]).copy()

    if "term_kind" in target_data.columns:
        target_data = target_data[target_data["term_kind"].eq(2)].copy()

    for row in target_data[["term_id", "term_lk"]].itertuples(index=False):
        for path_parts in split_category_paths(row.term_lk):
            relation_rows.append(
                {
                    "term_id": row.term_id,
                    "category_path": ">".join(path_parts),
                    "source_term_lk": row.term_lk,
                }
            )

    return relation_rows


def build_term_category_relation(terms_data, category_dictionary):
    relation_rows = build_term_relation_rows(terms_data)

    if len(relation_rows) == 0:
        return pd.DataFrame(
            columns=["term_id", "category_id", "category_path", "source_term_lk"]
        )

    term_category_relation = (
        pd.DataFrame(relation_rows)
        .drop_duplicates(subset=["term_id", "category_path"])
        .reset_index(drop=True)
    )
    path_to_id = dict(
        zip(category_dictionary["category_path"], category_dictionary["category_id"])
    )
    term_category_relation["category_id"] = term_category_relation["category_path"].map(path_to_id)

    return term_category_relation[
        ["term_id", "category_id", "category_path", "source_term_lk"]
    ]


def build_event_relation_rows(events_data):
    relation_rows = []
    target_data = events_data.dropna(subset=["subject_category"]).copy()

    for row in target_data[["event_id", "subject_category"]].itertuples(index=False):
        for token in split_event_category_tokens(row.subject_category):
            relation_rows.append(
                {
                    "event_id": row.event_id,
                    "event_category_name": token,
                    "source_subject_category": row.subject_category,
                }
            )

    return relation_rows


def build_event_category_relation(events_data, event_category_dictionary):
    relation_rows = build_event_relation_rows(events_data)

    if len(relation_rows) == 0:
        return pd.DataFrame(
            columns=[
                "event_id",
                "event_category_id",
                "event_category_name",
                "source_subject_category",
            ]
        )

    event_category_relation = (
        pd.DataFrame(relation_rows)
        .drop_duplicates(subset=["event_id", "event_category_name"])
        .reset_index(drop=True)
    )
    name_to_id = dict(
        zip(
            event_category_dictionary["event_category_name"],
            event_category_dictionary["event_category_id"],
        )
    )
    event_category_relation["event_category_id"] = (
        event_category_relation["event_category_name"].map(name_to_id)
    )

    return event_category_relation[
        [
            "event_id",
            "event_category_id",
            "event_category_name",
            "source_subject_category",
        ]
    ]


def build_category_mapping(event_category_dictionary, category_dictionary):
    category_leaf = category_dictionary.loc[
        :,
        ["category_id", "category_name", "category_path"],
    ].copy()
    mapping_rows = []

    for row in event_category_dictionary.itertuples(index=False):
        # 우선 이름이 정확히 같은 후보만 자동 매핑한다. 애매하거나 의미 기반인 매핑은 검수 대상으로 둔다.
        exact_matches = category_leaf[
            category_leaf["category_name"].eq(row.event_category_name)
        ]

        if len(exact_matches) > 0:
            for match in exact_matches.itertuples(index=False):
                mapping_rows.append(
                    {
                        "event_category_id": row.event_category_id,
                        "event_category_name": row.event_category_name,
                        "mapped_category_id": match.category_id,
                        "mapped_category_path": match.category_path,
                        "mapping_type": "EXACT_NAME",
                        "confidence": "HIGH",
                        "review_status": "PENDING",
                        "note": "event category name equals category_name",
                    }
                )

        if len(exact_matches) == 0:
            mapping_rows.append(
                {
                    "event_category_id": row.event_category_id,
                    "event_category_name": row.event_category_name,
                    "mapped_category_id": pd.NA,
                    "mapped_category_path": pd.NA,
                    "mapping_type": "UNMAPPED",
                    "confidence": "LOW",
                    "review_status": "PENDING",
                    "note": "manual mapping required",
                }
            )

    return pd.DataFrame(mapping_rows)


def save_csv(data_frame, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_frame.to_csv(output_path, index=False, encoding="utf-8-sig")


def print_summary(file_name, data_frame):
    print(f"{file_name}: {len(data_frame)} rows, {len(data_frame.columns)} columns")


def build_default_paths(script_path):
    normalized_dir = script_path.parent / "normalized"

    return {
        "terms": normalized_dir / "terms.csv",
        "events": normalized_dir / "events.csv",
        "dictionary_dir": script_path.parent / "dictionary",
        "staging_dir": script_path.parent / "staging",
    }


def build_dictionary_paths(args):
    return {
        "category_dictionary": args.dictionary_dir / "category_dictionary.csv",
        "event_category_dictionary": args.dictionary_dir / "event_category_dictionary.csv",
    }


def read_dictionary_files(dictionary_paths):
    require_file(dictionary_paths["category_dictionary"], "category_dictionary")
    require_file(dictionary_paths["event_category_dictionary"], "event_category_dictionary")

    return {
        "category_dictionary": pd.read_csv(dictionary_paths["category_dictionary"]),
        "event_category_dictionary": pd.read_csv(dictionary_paths["event_category_dictionary"]),
    }


def build_output_specs():
    # category_mapping은 검수용 사전 성격이라 dictionary 폴더에 둔다.
    return [
        ("term_category_relation", "term_category_relation.csv", "staging_dir"),
        ("event_category_relation", "event_category_relation.csv", "staging_dir"),
        ("category_mapping", "category_mapping.csv", "dictionary_dir"),
    ]


def build_output_files(args, outputs):
    output_files = []

    for output_key, file_name, target_dir_key in build_output_specs():
        target_dir = getattr(args, target_dir_key)
        output_files.append((file_name, outputs[output_key], target_dir / file_name))

    return output_files


def build_outputs(terms_data, events_data, dictionaries):
    category_dictionary = dictionaries["category_dictionary"]
    event_category_dictionary = dictionaries["event_category_dictionary"]

    return {
        "term_category_relation": build_term_category_relation(
            terms_data,
            category_dictionary,
        ),
        "event_category_relation": build_event_category_relation(
            events_data,
            event_category_dictionary,
        ),
        "category_mapping": build_category_mapping(
            event_category_dictionary,
            category_dictionary,
        ),
    }


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)

    terms_data = pd.read_csv(args.terms_path)
    events_data = pd.read_csv(args.events_path)
    dictionary_paths = build_dictionary_paths(args)
    dictionaries = read_dictionary_files(dictionary_paths)

    outputs = build_outputs(terms_data, events_data, dictionaries)
    output_files = build_output_files(args, outputs)

    if args.save:
        for file_name, data_frame, output_path in output_files:
            save_csv(data_frame, output_path)
            print_summary(file_name, data_frame)

        print(f"dictionary_dir: {args.dictionary_dir}")
        print(f"staging_dir: {args.staging_dir}")

    if not args.save:
        for file_name, data_frame, output_path in output_files:
            print_summary(file_name, data_frame)
            print(f"planned_path: {output_path}")

        print("dry_run: no files saved. Use --save to write CSV files.")


if __name__ == "__main__":
    main()

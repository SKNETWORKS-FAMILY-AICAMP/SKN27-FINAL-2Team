"""seed에 정의된 핵심 graph 양성·음성 사례를 생성 CSV 기준으로 검증한다."""

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from neo4j_common import require_file, resolve_import_dir, resolve_project_root


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)

    return {
        "case_seed": neo4j_dir / "seed" / "graph_qa_case_seed.csv",
        "contract_seed": neo4j_dir / "seed" / "graph_preload_contract_seed.csv",
        "relation_type_seed": neo4j_dir / "seed" / "relation_type_seed.csv",
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
        "node_schema": (
            project_root
            / "storage"
            / "neo4j"
            / "schema"
            / "history_graph_import_nodes.cypher"
        ),
        "relation_schema": (
            project_root
            / "storage"
            / "neo4j"
            / "schema"
            / "history_graph_import_relations.cypher"
        ),
        "event_reign_review": neo4j_dir / "staging" / "event_reign_mapping_review.csv",
        "report_path": neo4j_dir / "staging" / "graph_qa_report.csv",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Neo4j graph 핵심 양성·음성 QA 사례를 검증한다."
    )
    parser.add_argument(
        "--case-seed-path",
        type=Path,
        default=default_paths["case_seed"],
    )
    parser.add_argument(
        "--contract-seed-path",
        type=Path,
        default=default_paths["contract_seed"],
    )
    parser.add_argument(
        "--relation-type-seed-path",
        type=Path,
        default=default_paths["relation_type_seed"],
    )
    parser.add_argument("--nodes-dir", type=Path, default=default_paths["nodes_dir"])
    parser.add_argument(
        "--relations-dir",
        type=Path,
        default=default_paths["relations_dir"],
    )
    parser.add_argument(
        "--node-schema-path",
        type=Path,
        default=default_paths["node_schema"],
    )
    parser.add_argument(
        "--relation-schema-path",
        type=Path,
        default=default_paths["relation_schema"],
    )
    parser.add_argument(
        "--event-reign-review-path",
        type=Path,
        default=default_paths["event_reign_review"],
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=default_paths["report_path"],
    )
    parser.add_argument("--save", action="store_true")

    return parser.parse_args()


def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def read_csv_rows(csv_path, purpose):
    require_file(csv_path, purpose)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = set(reader.fieldnames or [])
        rows = [
            {key: clean_text(value) for key, value in row.items()}
            for row in reader
        ]

    return fieldnames, rows


def read_optional_csv_rows(csv_path):
    if not csv_path.exists():
        return set(), []

    return read_csv_rows(csv_path, str(csv_path))


def read_csv_header(csv_path):
    if not csv_path.exists():
        return []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)
        header = next(reader, [])

    return [clean_text(column_name) for column_name in header]


def build_check_row(case_id, case_type, expected_count, actual_count, description):
    status = "FAIL"

    if actual_count == expected_count:
        status = "PASS"

    return {
        "case_id": case_id,
        "case_type": case_type,
        "expected_count": expected_count,
        "actual_count": actual_count,
        "status": status,
        "description": description,
    }


def read_preload_contract(contract_seed_path):
    fieldnames, rows = read_csv_rows(
        contract_seed_path,
        "Graph pre-load contract seed",
    )
    required_columns = {
        "contract_id",
        "relation_file",
        "start_node_file",
        "start_node_id_column",
        "start_relation_id_column",
        "end_node_file",
        "end_node_id_column",
        "end_relation_id_column",
        "unique_key_columns",
        "expected_row_count",
    }
    missing_columns = sorted(required_columns - fieldnames)

    if len(missing_columns) > 0:
        raise ValueError(
            "Graph pre-load contract seed is missing columns: "
            + ", ".join(missing_columns)
        )

    contract_ids = set()

    for row in rows:
        missing_values = sorted(
            column_name
            for column_name in required_columns
            if row.get(column_name, "") == ""
        )

        if len(missing_values) > 0:
            raise ValueError(
                "Graph pre-load contract has empty values: "
                + ", ".join(missing_values)
            )

        if not row["expected_row_count"].isdigit():
            raise ValueError(
                "Graph pre-load expected_row_count is not an integer: "
                + row["contract_id"]
            )

        if row["contract_id"] in contract_ids:
            raise ValueError(
                "Graph pre-load contract_id is duplicated: " + row["contract_id"]
            )

        contract_ids.add(row["contract_id"])
        row["expected_row_count_number"] = int(row["expected_row_count"])
        row["unique_key_column_names"] = [
            column_name.strip()
            for column_name in row["unique_key_columns"].split("|")
            if column_name.strip() != ""
        ]

    return rows


def parse_cypher_csv_declarations(schema_path, directory_name):
    require_file(schema_path, f"Neo4j {directory_name} import schema")
    schema_text = schema_path.read_text(encoding="utf-8-sig")
    declaration_pattern = re.compile(
        rf"LOAD\s+CSV\s+WITH\s+HEADERS\s+FROM\s+"
        rf"'file:///{re.escape(directory_name)}/(?P<file_name>[^']+\.csv)'\s+"
        rf"AS\s+row(?P<body>.*?);",
        flags=re.IGNORECASE | re.DOTALL,
    )
    property_pattern = re.compile(r"\brow\.([A-Za-z_][A-Za-z0-9_]*)")
    fields_by_file = defaultdict(set)
    declared_files = []

    for match in declaration_pattern.finditer(schema_text):
        file_name = match.group("file_name")
        declared_files.append(file_name)
        fields_by_file[file_name].update(property_pattern.findall(match.group("body")))

    if len(declared_files) == 0:
        raise ValueError(f"No CSV declarations found in schema: {schema_path}")

    return dict(fields_by_file), declared_files


def parse_relation_merge_identities(relation_schema_path):
    """관계 적재 Cypher에서 실제 MERGE identity만 추출한다."""
    require_file(relation_schema_path, "Neo4j relations import schema")
    schema_text = relation_schema_path.read_text(encoding="utf-8-sig")
    block_pattern = re.compile(
        r"^[ \t]*LOAD\s+CSV\s+WITH\s+HEADERS\s+FROM\s+"
        r"'file:///relations/(?P<file_name>[^']+\.csv)'\s+AS\s+row\b"
        r"(?P<body>.*?)"
        r"^[ \t]*\}\s+IN\s+TRANSACTIONS\s+OF\s+\d+\s+ROWS\s*;",
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    endpoint_pattern = re.compile(
        r"^[ \t]*MATCH\s*\(\s*(?P<alias>start|target)\s*:\s*"
        r"(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)\s*"
        r"\{\s*(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
        r"row\.(?P<csv_column>[A-Za-z_][A-Za-z0-9_]*)\s*\}\s*\)",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    merge_pattern = re.compile(
        r"^[ \t]*MERGE\s*\(\s*start\s*\)\s*-\s*"
        r"\[\s*r\s*:\s*(?P<relationship_type>"
        r"\$\(\s*row\.[A-Za-z_][A-Za-z0-9_]*\s*\)"
        r"|`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)"
        r"(?:\s*\{(?P<identity_properties>.*?)\})?\s*"
        r"\]\s*->\s*\(\s*target\s*\)",
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    identity_property_pattern = re.compile(
        r"(?P<property_name>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*:\s*row\.(?P<csv_column>[A-Za-z_][A-Za-z0-9_]*)"
    )
    dynamic_type_pattern = re.compile(
        r"^\$\(\s*row\.(?P<csv_column>[A-Za-z_][A-Za-z0-9_]*)\s*\)$"
    )
    parsed_identities = []
    parse_errors = []
    block_matches = list(block_pattern.finditer(schema_text))

    for block_match in block_matches:
        file_name = block_match.group("file_name")
        body = block_match.group("body")
        endpoint_matches = list(endpoint_pattern.finditer(body))
        start_columns = [
            match.group("csv_column")
            for match in endpoint_matches
            if match.group("alias").lower() == "start"
        ]
        target_columns = [
            match.group("csv_column")
            for match in endpoint_matches
            if match.group("alias").lower() == "target"
        ]
        merge_matches = list(merge_pattern.finditer(body))

        if len(start_columns) != 1 or len(target_columns) != 1:
            parse_errors.append(f"{file_name}:endpoint")
            continue
        if len(merge_matches) != 1:
            parse_errors.append(f"{file_name}:merge")
            continue

        merge_match = merge_matches[0]
        relationship_type = merge_match.group("relationship_type").strip()
        dynamic_type_match = dynamic_type_pattern.match(relationship_type)
        relationship_type_column = ""
        static_relationship_type = relationship_type.strip("`")

        if dynamic_type_match is not None:
            relationship_type_column = dynamic_type_match.group("csv_column")
            static_relationship_type = ""

        identity_properties = merge_match.group("identity_properties") or ""
        property_matches = list(
            identity_property_pattern.finditer(identity_properties)
        )
        unsupported_property_text = identity_property_pattern.sub(
            "",
            identity_properties,
        )
        unsupported_property_text = re.sub(r"[\s,]", "", unsupported_property_text)

        if unsupported_property_text != "":
            parse_errors.append(f"{file_name}:identity_property")
            continue

        identity_columns = [start_columns[0], target_columns[0]]

        if relationship_type_column != "":
            identity_columns.append(relationship_type_column)

        identity_columns.extend(
            match.group("csv_column") for match in property_matches
        )
        parsed_identities.append(
            {
                "file_name": file_name,
                "identity_columns": identity_columns,
                "relationship_type_column": relationship_type_column,
                "static_relationship_type": static_relationship_type,
            }
        )

    return len(block_matches), parsed_identities, parse_errors


def validate_relation_merge_identities(relations_dir, relation_schema_path):
    _, parsed_identities, parse_errors = (
        parse_relation_merge_identities(relation_schema_path)
    )
    _, declared_files = parse_cypher_csv_declarations(
        relation_schema_path,
        "relations",
    )
    declared_file_set = set(declared_files)
    parsed_file_set = {
        identity["file_name"] for identity in parsed_identities
    }
    unparsed_files = sorted(declared_file_set - parsed_file_set)
    unexpected_parsed_files = sorted(parsed_file_set - declared_file_set)
    parse_errors.extend(f"{file_name}:unparsed" for file_name in unparsed_files)
    parse_errors.extend(
        f"{file_name}:undeclared" for file_name in unexpected_parsed_files
    )
    missing_columns = []
    invalid_value_count = 0
    duplicate_count = 0
    duplicate_files = []

    for identity in parsed_identities:
        relation_path = relations_dir / identity["file_name"]

        if not relation_path.exists():
            missing_columns.append(f"{identity['file_name']}:file")
            continue

        with relation_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            fieldnames = set(reader.fieldnames or [])
            required_columns = set(identity["identity_columns"])
            missing_file_columns = sorted(required_columns - fieldnames)

            if len(missing_file_columns) > 0:
                missing_columns.append(
                    f"{identity['file_name']}({','.join(missing_file_columns)})"
                )
                continue

            identity_counts = Counter()

            for row in reader:
                identity_values = [
                    row.get(column_name, "") or ""
                    for column_name in identity["identity_columns"]
                ]
                has_empty_value = any(value == "" for value in identity_values)
                has_outer_whitespace = any(
                    value != value.strip() for value in identity_values
                )

                if has_empty_value or has_outer_whitespace:
                    invalid_value_count += 1
                    continue

                relationship_type = identity["static_relationship_type"]
                property_value_offset = 2

                if identity["relationship_type_column"] != "":
                    relationship_type = row[identity["relationship_type_column"]]
                    property_value_offset = 3

                identity_key = (
                    identity_values[0],
                    identity_values[1],
                    relationship_type,
                    *identity_values[property_value_offset:],
                )
                identity_counts[identity_key] += 1

        file_duplicate_count = sum(
            count - 1 for count in identity_counts.values() if count > 1
        )
        duplicate_count += file_duplicate_count

        if file_duplicate_count > 0:
            duplicate_files.append(
                f"{identity['file_name']}:{file_duplicate_count}"
            )

    return [
        build_check_row(
            "PRELOAD_RELATION_MERGE_IDENTITY_PARSE",
            "PRELOAD_RELATION_IDENTITY",
            len(declared_files),
            len(parsed_identities),
            "Unparsed relation blocks: " + "|".join(parse_errors),
        ),
        build_check_row(
            "PRELOAD_RELATION_MERGE_IDENTITY_COLUMNS",
            "PRELOAD_RELATION_IDENTITY",
            0,
            len(missing_columns),
            "Missing identity columns: " + "|".join(missing_columns),
        ),
        build_check_row(
            "PRELOAD_RELATION_MERGE_IDENTITY_VALUES",
            "PRELOAD_RELATION_IDENTITY",
            0,
            invalid_value_count,
            "Rows with empty or outer-whitespace MERGE identity values",
        ),
        build_check_row(
            "PRELOAD_RELATION_MERGE_IDENTITY_DUPLICATES",
            "PRELOAD_RELATION_IDENTITY",
            0,
            duplicate_count,
            "Duplicate MERGE identities: " + "|".join(duplicate_files),
        ),
    ]


def validate_about_evidence_alignment(relations_dir):
    """집계 ABOUT 엣지의 pipe 위치가 원본 category mapping tuple과 일치하는지 검사한다."""
    mapping_specs = [
        (
            "term_about_country.csv",
            "start_term_id",
            "end_country_id",
            "term_has_canonical_category.csv",
            "canonical_category_about_country.csv",
        ),
        (
            "event_about_country.csv",
            "start_event_id",
            "end_country_id",
            "event_has_canonical_category.csv",
            "canonical_category_about_country.csv",
        ),
        (
            "term_about_region.csv",
            "start_term_id",
            "end_region_id",
            "term_has_canonical_category.csv",
            "canonical_category_about_region.csv",
        ),
        (
            "term_about_economic_domain.csv",
            "start_term_id",
            "end_economic_domain_id",
            "term_has_canonical_category.csv",
            "canonical_category_about_economic_domain.csv",
        ),
        (
            "term_about_taxonomy_facet.csv",
            "start_term_id",
            "end_taxonomy_facet_id",
            "term_has_canonical_category.csv",
            "canonical_category_about_taxonomy_facet.csv",
        ),
        (
            "event_about_taxonomy_facet.csv",
            "start_event_id",
            "end_taxonomy_facet_id",
            "event_has_canonical_category.csv",
            "canonical_category_about_taxonomy_facet.csv",
        ),
    ]
    required_file_names = {
        file_name
        for (
            relation_file_name,
            _,
            _,
            source_category_file_name,
            reference_file_name,
        ) in mapping_specs
        for file_name in [
            relation_file_name,
            source_category_file_name,
            reference_file_name,
        ]
    }
    missing_files = sorted(
        file_name
        for file_name in required_file_names
        if not (relations_dir / file_name).exists()
    )
    report_rows = [
        build_check_row(
            "PRELOAD_ABOUT_EVIDENCE_REQUIRED_FILES",
            "PRELOAD_ABOUT_EVIDENCE",
            0,
            len(missing_files),
            "Missing ABOUT evidence files: " + "|".join(missing_files),
        )
    ]

    if len(missing_files) > 0:
        return report_rows

    table_cache = {}

    for file_name in required_file_names:
        table_cache[file_name] = read_csv_rows(
            relations_dir / file_name,
            f"ABOUT evidence {file_name}",
        )

    missing_columns = []
    arity_mismatch_count = 0
    tuple_mismatch_count = 0

    for (
        relation_file_name,
        source_column,
        target_column,
        source_category_file_name,
        reference_file_name,
    ) in mapping_specs:
        relation_header, relation_rows = table_cache[relation_file_name]
        source_category_header, source_category_rows = table_cache[
            source_category_file_name
        ]
        reference_header, reference_rows = table_cache[reference_file_name]
        relation_required_columns = {
            source_column,
            target_column,
            "canonical_category_id",
            "canonical_category_path",
            "match_type",
        }
        source_category_required_columns = {
            source_column,
            "end_category_id",
        }
        reference_required_columns = {
            "start_category_id",
            target_column,
            "canonical_category_path",
            "match_type",
        }
        missing_relation_columns = sorted(
            relation_required_columns - relation_header
        )
        missing_source_category_columns = sorted(
            source_category_required_columns - source_category_header
        )
        missing_reference_columns = sorted(
            reference_required_columns - reference_header
        )

        if len(missing_relation_columns) > 0:
            missing_columns.append(
                f"{relation_file_name}({','.join(missing_relation_columns)})"
            )
            continue
        if len(missing_source_category_columns) > 0:
            missing_columns.append(
                f"{source_category_file_name}"
                f"({','.join(missing_source_category_columns)})"
            )
            continue
        if len(missing_reference_columns) > 0:
            missing_columns.append(
                f"{reference_file_name}({','.join(missing_reference_columns)})"
            )
            continue

        reference_tuples_by_category = defaultdict(set)

        for reference_row in reference_rows:
            reference_tuples_by_category[
                reference_row["start_category_id"]
            ].add(
                (
                    reference_row[target_column],
                    reference_row["canonical_category_path"],
                    reference_row["match_type"],
                )
            )

        expected_tuples_by_edge = defaultdict(set)

        for source_category_row in source_category_rows:
            source_id = source_category_row[source_column]
            category_id = source_category_row["end_category_id"]

            for target_id, category_path, match_type in (
                reference_tuples_by_category.get(category_id, set())
            ):
                expected_tuples_by_edge[(source_id, target_id)].add(
                    (category_id, category_path, match_type)
                )

        actual_tuples_by_edge = defaultdict(set)

        for relation_row in relation_rows:
            category_ids = relation_row["canonical_category_id"].split("|")
            category_paths = relation_row["canonical_category_path"].split("|")
            match_types = relation_row["match_type"].split("|")
            evidence_lengths = {
                len(category_ids),
                len(category_paths),
                len(match_types),
            }

            if (
                len(evidence_lengths) != 1
                or "" in category_ids
                or "" in category_paths
                or "" in match_types
            ):
                arity_mismatch_count += 1
                continue

            evidence_tuples = list(
                zip(category_ids, category_paths, match_types)
            )
            duplicate_evidence_count = len(evidence_tuples) - len(
                set(evidence_tuples)
            )
            tuple_mismatch_count += duplicate_evidence_count
            actual_tuples_by_edge[
                (relation_row[source_column], relation_row[target_column])
            ].update(evidence_tuples)

        edge_keys = set(expected_tuples_by_edge) | set(actual_tuples_by_edge)

        for edge_key in edge_keys:
            expected_tuples = expected_tuples_by_edge.get(edge_key, set())
            actual_tuples = actual_tuples_by_edge.get(edge_key, set())
            tuple_mismatch_count += len(expected_tuples ^ actual_tuples)

    report_rows.extend(
        [
            build_check_row(
                "PRELOAD_ABOUT_EVIDENCE_REQUIRED_COLUMNS",
                "PRELOAD_ABOUT_EVIDENCE",
                0,
                len(missing_columns),
                "Missing ABOUT evidence columns: " + "|".join(missing_columns),
            ),
            build_check_row(
                "PRELOAD_ABOUT_EVIDENCE_ARITY",
                "PRELOAD_ABOUT_EVIDENCE",
                0,
                arity_mismatch_count,
                "Aggregated evidence columns with different tuple counts",
            ),
            build_check_row(
                "PRELOAD_ABOUT_EVIDENCE_SOURCE_TUPLES",
                "PRELOAD_ABOUT_EVIDENCE",
                0,
                tuple_mismatch_count,
                "Missing or extra source-specific CanonicalCategory mapping tuples",
            ),
        ]
    )

    return report_rows


def validate_declared_file_sets(nodes_dir, relations_dir, node_schema, relation_schema):
    report_rows = []
    validation_targets = [
        ("nodes", nodes_dir, node_schema),
        ("relations", relations_dir, relation_schema),
    ]

    for directory_name, csv_dir, schema_path in validation_targets:
        fields_by_file, declared_files = parse_cypher_csv_declarations(
            schema_path,
            directory_name,
        )
        declared_file_set = set(declared_files)
        actual_file_set = {
            csv_path.name for csv_path in csv_dir.glob("*.csv") if csv_path.is_file()
        }
        missing_files = sorted(declared_file_set - actual_file_set)
        stale_files = sorted(actual_file_set - declared_file_set)
        duplicate_declarations = sum(
            count - 1 for count in Counter(declared_files).values() if count > 1
        )
        invalid_headers = []
        missing_schema_fields = []

        for file_name in sorted(declared_file_set & actual_file_set):
            header = read_csv_header(csv_dir / file_name)
            duplicate_header_count = len(header) - len(set(header))

            if (
                len(header) == 0
                or "" in header
                or duplicate_header_count > 0
            ):
                invalid_headers.append(file_name)

            missing_fields = sorted(fields_by_file[file_name] - set(header))

            if len(missing_fields) > 0:
                missing_schema_fields.append(
                    f"{file_name}({','.join(missing_fields)})"
                )

        report_rows.extend(
            [
                build_check_row(
                    f"PRELOAD_{directory_name.upper()}_DECLARATION_DUPLICATES",
                    "PRELOAD_FILE_SET",
                    0,
                    duplicate_declarations,
                    f"Duplicate CSV declarations in {schema_path.name}",
                ),
                build_check_row(
                    f"PRELOAD_{directory_name.upper()}_MISSING_FILES",
                    "PRELOAD_FILE_SET",
                    0,
                    len(missing_files),
                    "Missing declared files: " + "|".join(missing_files),
                ),
                build_check_row(
                    f"PRELOAD_{directory_name.upper()}_STALE_FILES",
                    "PRELOAD_FILE_SET",
                    0,
                    len(stale_files),
                    "Undeclared stale files: " + "|".join(stale_files),
                ),
                build_check_row(
                    f"PRELOAD_{directory_name.upper()}_INVALID_HEADERS",
                    "PRELOAD_HEADER",
                    0,
                    len(invalid_headers),
                    "Files with empty or duplicate header columns: "
                    + "|".join(invalid_headers),
                ),
                build_check_row(
                    f"PRELOAD_{directory_name.upper()}_SCHEMA_FIELDS",
                    "PRELOAD_HEADER",
                    0,
                    len(missing_schema_fields),
                    "CSV headers missing fields referenced by Cypher: "
                    + "|".join(missing_schema_fields),
                ),
            ]
        )

    return report_rows


def count_duplicate_keys(rows, column_names):
    key_counts = Counter()

    for row in rows:
        key = tuple(row.get(column_name, "") for column_name in column_names)

        if "" not in key:
            key_counts[key] += 1

    return sum(count - 1 for count in key_counts.values() if count > 1)


def validate_preload_contracts(contract_rows, nodes_dir, relations_dir):
    report_rows = []
    table_cache = {}

    def read_table(csv_path):
        cache_key = str(csv_path.resolve())

        if cache_key not in table_cache:
            table_cache[cache_key] = read_optional_csv_rows(csv_path)

        return table_cache[cache_key]

    for contract in contract_rows:
        contract_id = contract["contract_id"]
        relation_path = relations_dir / contract["relation_file"]
        start_node_path = nodes_dir / contract["start_node_file"]
        end_node_path = nodes_dir / contract["end_node_file"]
        relation_header, relation_rows = read_table(relation_path)
        start_header, start_node_rows = read_table(start_node_path)
        end_header, end_node_rows = read_table(end_node_path)
        unique_columns = contract["unique_key_column_names"]
        relation_id_columns = {
            contract["start_relation_id_column"],
            contract["end_relation_id_column"],
            *unique_columns,
        }
        missing_relation_columns = sorted(relation_id_columns - relation_header)
        missing_node_columns = []

        if contract["start_node_id_column"] not in start_header:
            missing_node_columns.append(
                f"{contract['start_node_file']}:{contract['start_node_id_column']}"
            )

        if contract["end_node_id_column"] not in end_header:
            missing_node_columns.append(
                f"{contract['end_node_file']}:{contract['end_node_id_column']}"
            )
        empty_relation_ids = sum(
            1
            for row in relation_rows
            for column_name in relation_id_columns
            if row.get(column_name, "") == ""
        )
        start_node_id_column = contract["start_node_id_column"]
        end_node_id_column = contract["end_node_id_column"]
        start_relation_id_column = contract["start_relation_id_column"]
        end_relation_id_column = contract["end_relation_id_column"]
        start_node_ids = {
            row.get(start_node_id_column, "")
            for row in start_node_rows
            if row.get(start_node_id_column, "") != ""
        }
        end_node_ids = {
            row.get(end_node_id_column, "")
            for row in end_node_rows
            if row.get(end_node_id_column, "") != ""
        }
        endpoint_tables = {
            (str(start_node_path.resolve()), start_node_id_column): start_node_rows,
            (str(end_node_path.resolve()), end_node_id_column): end_node_rows,
        }
        empty_node_ids = sum(
            1
            for (_, node_id_column), node_rows in endpoint_tables.items()
            for row in node_rows
            if row.get(node_id_column, "") == ""
        )
        duplicated_node_ids = sum(
            count_duplicate_keys(node_rows, [node_id_column])
            for (_, node_id_column), node_rows in endpoint_tables.items()
        )
        start_orphans = sum(
            1
            for row in relation_rows
            if row.get(start_relation_id_column, "") != ""
            and row[start_relation_id_column] not in start_node_ids
        )
        end_orphans = sum(
            1
            for row in relation_rows
            if row.get(end_relation_id_column, "") != ""
            and row[end_relation_id_column] not in end_node_ids
        )
        duplicate_unique_keys = count_duplicate_keys(relation_rows, unique_columns)

        report_rows.extend(
            [
                build_check_row(
                    f"PRELOAD_{contract_id}_ROW_COUNT",
                    "PRELOAD_CONTRACT",
                    contract["expected_row_count_number"],
                    len(relation_rows),
                    f"Row count contract for {contract['relation_file']}",
                ),
                build_check_row(
                    f"PRELOAD_{contract_id}_RELATION_COLUMNS",
                    "PRELOAD_CONTRACT",
                    0,
                    len(missing_relation_columns),
                    "Missing relation columns: " + "|".join(missing_relation_columns),
                ),
                build_check_row(
                    f"PRELOAD_{contract_id}_NODE_COLUMNS",
                    "PRELOAD_CONTRACT",
                    0,
                    len(missing_node_columns),
                    "Missing endpoint node columns: " + "|".join(missing_node_columns),
                ),
                build_check_row(
                    f"PRELOAD_{contract_id}_EMPTY_RELATION_IDS",
                    "PRELOAD_CONTRACT",
                    0,
                    empty_relation_ids,
                    "Empty endpoint or unique-key values in relation rows",
                ),
                build_check_row(
                    f"PRELOAD_{contract_id}_EMPTY_NODE_IDS",
                    "PRELOAD_CONTRACT",
                    0,
                    empty_node_ids,
                    "Empty endpoint IDs in node rows",
                ),
                build_check_row(
                    f"PRELOAD_{contract_id}_DUPLICATE_NODE_IDS",
                    "PRELOAD_CONTRACT",
                    0,
                    duplicated_node_ids,
                    "Duplicate endpoint IDs in node rows",
                ),
                build_check_row(
                    f"PRELOAD_{contract_id}_START_ORPHANS",
                    "PRELOAD_CONTRACT",
                    0,
                    start_orphans,
                    "Relation start IDs without endpoint nodes",
                ),
                build_check_row(
                    f"PRELOAD_{contract_id}_END_ORPHANS",
                    "PRELOAD_CONTRACT",
                    0,
                    end_orphans,
                    "Relation end IDs without endpoint nodes",
                ),
                build_check_row(
                    f"PRELOAD_{contract_id}_DUPLICATE_UNIQUE_KEYS",
                    "PRELOAD_CONTRACT",
                    0,
                    duplicate_unique_keys,
                    "Duplicate relation unique keys: " + "|".join(unique_columns),
                ),
            ]
        )

    return report_rows


def validate_person_relation_contract(nodes_dir, relations_dir, relation_type_seed_path):
    people_path = nodes_dir / "people.csv"
    person_relation_path = relations_dir / "person_related_to_person.csv"
    involvement_path = relations_dir / "person_involved_in_event.csv"
    required_paths = [people_path, person_relation_path, involvement_path, relation_type_seed_path]
    missing_paths = [str(csv_path) for csv_path in required_paths if not csv_path.exists()]
    report_rows = [
        build_check_row(
            "PRELOAD_PERSON_REQUIRED_FILES",
            "PRELOAD_PERSON_RELATION",
            0,
            len(missing_paths),
            "Missing person validation inputs: " + "|".join(missing_paths),
        )
    ]

    if len(missing_paths) > 0:
        return report_rows

    people_header, people_rows = read_csv_rows(people_path, "Person nodes")
    relation_header, relation_rows = read_csv_rows(
        person_relation_path,
        "Typed person relations",
    )
    involvement_header, involvement_rows = read_csv_rows(
        involvement_path,
        "Person event relations",
    )
    seed_header, seed_rows = read_csv_rows(
        relation_type_seed_path,
        "Person relation type seed",
    )
    relation_required_columns = {
        "person_relation_id",
        "start_person_id",
        "end_person_id",
        "relation_type",
        "raw_relation_type",
        "normalized_relation_type",
        "relation_group",
        "direction_rule",
        "is_symmetric",
        "inverse_relation_type",
        "evidence_url",
    }
    seed_required_columns = {
        "raw_relation_type",
        "normalized_relation_type",
        "neo4j_rel_type",
        "relation_group",
        "direction_rule",
        "is_symmetric",
        "inverse_relation_type",
    }
    people_required_columns = {"person_id", "core_relation_degree"}
    involvement_required_columns = {"start_person_id"}
    missing_columns = sorted(
        (relation_required_columns - relation_header)
        | (seed_required_columns - seed_header)
        | (people_required_columns - people_header)
        | (involvement_required_columns - involvement_header)
    )
    seed_raw_counts = Counter(row["raw_relation_type"] for row in seed_rows)
    duplicate_seed_raw_types = sum(
        count - 1 for count in seed_raw_counts.values() if count > 1
    )
    seed_by_raw_type = {
        row["raw_relation_type"]: row
        for row in seed_rows
        if row["raw_relation_type"] != ""
    }
    seed_relation_types = {
        row["neo4j_rel_type"]
        for row in seed_rows
        if row["neo4j_rel_type"] != ""
    }
    csv_relation_types = {
        row.get("relation_type", "")
        for row in relation_rows
        if row.get("relation_type", "") != ""
    }
    relation_type_set_difference = seed_relation_types ^ csv_relation_types
    related_to_count = sum(
        1 for row in relation_rows if row.get("relation_type", "") == "RELATED_TO"
    )
    mapping_mismatch_count = 0
    mapping_fields = [
        ("normalized_relation_type", "normalized_relation_type"),
        ("relation_type", "neo4j_rel_type"),
        ("relation_group", "relation_group"),
        ("direction_rule", "direction_rule"),
        ("is_symmetric", "is_symmetric"),
        ("inverse_relation_type", "inverse_relation_type"),
    ]

    for relation_row in relation_rows:
        seed_row = seed_by_raw_type.get(relation_row.get("raw_relation_type", ""))

        if seed_row is None:
            mapping_mismatch_count += 1
            continue

        if any(
            relation_row.get(relation_column, "") != seed_row.get(seed_column, "")
            for relation_column, seed_column in mapping_fields
        ):
            mapping_mismatch_count += 1

    self_relation_count = sum(
        1
        for row in relation_rows
        if row.get("start_person_id", "") == row.get("end_person_id", "")
    )
    exact_duplicate_count = count_duplicate_keys(
        relation_rows,
        ["start_person_id", "end_person_id", "relation_type"],
    )
    symmetric_pair_counts = Counter()
    symmetric_directions = defaultdict(set)

    for row in relation_rows:
        seed_row = seed_by_raw_type.get(row.get("raw_relation_type", ""))

        if seed_row is None or seed_row.get("is_symmetric", "") != "Y":
            continue

        start_person_id = row.get("start_person_id", "")
        end_person_id = row.get("end_person_id", "")
        relation_type = row.get("relation_type", "")
        unordered_pair = tuple(sorted([start_person_id, end_person_id]))
        symmetric_key = (relation_type, *unordered_pair)
        symmetric_pair_counts[symmetric_key] += 1
        symmetric_directions[symmetric_key].add((start_person_id, end_person_id))

    symmetric_duplicate_count = sum(
        count - 1 for count in symmetric_pair_counts.values() if count > 1
    )
    symmetric_reverse_count = sum(
        1 for directions in symmetric_directions.values() if len(directions) > 1
    )
    invalid_symmetric_direction_count = sum(
        1
        for row in relation_rows
        if row.get("is_symmetric", "") == "Y"
        and row.get("start_person_id", "") >= row.get("end_person_id", "")
    )
    incident_counts = defaultdict(int)

    for row in relation_rows:
        incident_counts[row.get("start_person_id", "")] += 1
        incident_counts[row.get("end_person_id", "")] += 1

    for row in involvement_rows:
        incident_counts[row.get("start_person_id", "")] += 1

    people_ids = {
        row.get("person_id", "")
        for row in people_rows
        if row.get("person_id", "") != ""
    }
    unknown_incident_person_count = sum(
        count
        for person_id, count in incident_counts.items()
        if person_id != "" and person_id not in people_ids
    )
    degree_mismatch_count = 0

    for row in people_rows:
        stored_degree_text = row.get("core_relation_degree", "")
        stored_degree = None

        try:
            stored_degree = int(stored_degree_text)
        except (TypeError, ValueError):
            degree_mismatch_count += 1
            continue

        expected_degree = incident_counts.get(row.get("person_id", ""), 0)

        if stored_degree != expected_degree:
            degree_mismatch_count += 1

    report_rows.extend(
        [
            build_check_row(
                "PRELOAD_PERSON_REQUIRED_COLUMNS",
                "PRELOAD_PERSON_RELATION",
                0,
                len(missing_columns),
                "Missing typed person relation columns: " + "|".join(missing_columns),
            ),
            build_check_row(
                "PRELOAD_PERSON_RELATION_TYPE_SEED_KEYS",
                "PRELOAD_PERSON_RELATION",
                0,
                duplicate_seed_raw_types,
                "Duplicate raw_relation_type values in relation type seed",
            ),
            build_check_row(
                "PRELOAD_PERSON_RELATION_TYPE_SET",
                "PRELOAD_PERSON_RELATION",
                0,
                len(relation_type_set_difference),
                "Seed/CSV relationship type set difference: "
                + "|".join(sorted(relation_type_set_difference)),
            ),
            build_check_row(
                "PRELOAD_PERSON_RELATED_TO_REMOVED",
                "PRELOAD_PERSON_RELATION",
                0,
                related_to_count,
                "Generic RELATED_TO rows must not remain",
            ),
            build_check_row(
                "PRELOAD_PERSON_SEED_MAPPING",
                "PRELOAD_PERSON_RELATION",
                0,
                mapping_mismatch_count,
                "Rows that do not match relation_type_seed mapping",
            ),
            build_check_row(
                "PRELOAD_PERSON_SELF_RELATIONS",
                "PRELOAD_PERSON_RELATION",
                0,
                self_relation_count,
                "Self-referencing person relations",
            ),
            build_check_row(
                "PRELOAD_PERSON_EXACT_DUPLICATES",
                "PRELOAD_PERSON_RELATION",
                0,
                exact_duplicate_count,
                "Duplicate directed person relation keys",
            ),
            build_check_row(
                "PRELOAD_PERSON_SYMMETRIC_DUPLICATES",
                "PRELOAD_PERSON_RELATION",
                0,
                symmetric_duplicate_count,
                "Duplicate unordered symmetric relation keys",
            ),
            build_check_row(
                "PRELOAD_PERSON_SYMMETRIC_REVERSE_PAIRS",
                "PRELOAD_PERSON_RELATION",
                0,
                symmetric_reverse_count,
                "Symmetric pairs emitted in both directions",
            ),
            build_check_row(
                "PRELOAD_PERSON_SYMMETRIC_DIRECTION",
                "PRELOAD_PERSON_RELATION",
                0,
                invalid_symmetric_direction_count,
                "Symmetric pairs must use ascending Person endpoint IDs",
            ),
            build_check_row(
                "PRELOAD_PERSON_RELATED_COUNT_REMOVED",
                "PRELOAD_PERSON_RELATION",
                0,
                int("related_count" in relation_header),
                "Legacy related_count relation property must be removed",
            ),
            build_check_row(
                "PRELOAD_PERSON_LEGACY_DEGREE_REMOVED",
                "PRELOAD_PERSON_RELATION",
                0,
                int("degree" in people_header),
                "Legacy degree person property must be removed",
            ),
            build_check_row(
                "PRELOAD_PERSON_UNKNOWN_INCIDENT_ENDPOINTS",
                "PRELOAD_PERSON_RELATION",
                0,
                unknown_incident_person_count,
                "Incident relations referencing missing Person nodes",
            ),
            build_check_row(
                "PRELOAD_PERSON_CORE_RELATION_DEGREE",
                "PRELOAD_PERSON_RELATION",
                0,
                degree_mismatch_count,
                "Person core_relation_degree differs from final incident relation count",
            ),
        ]
    )

    return report_rows


def validate_source_image_contract(nodes_dir, relations_dir):
    image_path = nodes_dir / "source_images.csv"
    source_url_path = nodes_dir / "source_urls.csv"
    related_content_path = relations_dir / "source_image_has_related_content.csv"
    depicts_path = relations_dir / "source_image_depicts_entity.csv"
    required_paths = [image_path, source_url_path, related_content_path, depicts_path]
    missing_paths = [str(csv_path) for csv_path in required_paths if not csv_path.exists()]
    report_rows = [
        build_check_row(
            "PRELOAD_SOURCE_IMAGE_REQUIRED_FILES",
            "PRELOAD_SOURCE_IMAGE",
            0,
            len(missing_paths),
            "Missing SourceImage validation inputs: " + "|".join(missing_paths),
        )
    ]

    if len(missing_paths) > 0:
        return report_rows

    image_header, image_rows = read_csv_rows(image_path, "SourceImage nodes")
    url_header, url_rows = read_csv_rows(source_url_path, "SourceUrl nodes")
    related_header, related_rows = read_csv_rows(
        related_content_path,
        "SourceImage related content relations",
    )
    depicts_header, depicts_rows = read_csv_rows(
        depicts_path,
        "SourceImage depicts relations",
    )
    required_columns = {
        "source_image_id",
        "source_url_id",
        "relation_type",
        "mapping_method",
        "review_status",
    }
    missing_related_columns = sorted(required_columns - related_header)
    missing_depicts_columns = sorted(
        {"source_image_id", "evidence_field", "evidence_text"} - depicts_header
    )
    missing_node_columns = sorted(
        ({"source_image_id", "title"} - image_header)
        | ({"source_url_id", "source_types"} - url_header)
    )
    source_types_by_url_id = {
        row.get("source_url_id", ""): {
            source_type.strip()
            for source_type in row.get("source_types", "").split("|")
            if source_type.strip() != ""
        }
        for row in url_rows
    }
    invalid_related_url_type_count = sum(
        1
        for row in related_rows
        if "IMAGE_RELATED_CONTENT"
        not in source_types_by_url_id.get(row.get("source_url_id", ""), set())
    )
    invalid_related_constant_count = sum(
        1
        for row in related_rows
        if row.get("relation_type", "") != "HAS_RELATED_CONTENT"
        or row.get("mapping_method", "") != "SOURCE_DECLARED_URL"
        or row.get("review_status", "") != "SOURCE_ANCHORED"
    )
    image_title_by_id = {
        row.get("source_image_id", ""): row.get("title", "") for row in image_rows
    }
    invalid_depicts_field_count = sum(
        1 for row in depicts_rows if row.get("evidence_field", "") != "title"
    )
    invalid_depicts_text_count = sum(
        1
        for row in depicts_rows
        if row.get("evidence_text", "")
        != image_title_by_id.get(row.get("source_image_id", ""), "")
    )
    report_rows.extend(
        [
            build_check_row(
                "PRELOAD_SOURCE_IMAGE_RELATED_CONTENT_REMOVED",
                "PRELOAD_SOURCE_IMAGE",
                0,
                int("related_content" in image_header),
                "SourceImage.related_content node property must be removed",
            ),
            build_check_row(
                "PRELOAD_SOURCE_IMAGE_REQUIRED_COLUMNS",
                "PRELOAD_SOURCE_IMAGE",
                0,
                len(missing_related_columns)
                + len(missing_depicts_columns)
                + len(missing_node_columns),
                "Missing SourceImage validation columns: "
                + "|".join(
                    missing_related_columns
                    + missing_depicts_columns
                    + missing_node_columns
                ),
            ),
            build_check_row(
                "PRELOAD_SOURCE_IMAGE_RELATED_URL_TYPE",
                "PRELOAD_SOURCE_IMAGE",
                0,
                invalid_related_url_type_count,
                "HAS_RELATED_CONTENT targets without IMAGE_RELATED_CONTENT source type",
            ),
            build_check_row(
                "PRELOAD_SOURCE_IMAGE_RELATED_CONSTANTS",
                "PRELOAD_SOURCE_IMAGE",
                0,
                invalid_related_constant_count,
                "Invalid HAS_RELATED_CONTENT relation constants",
            ),
            build_check_row(
                "PRELOAD_SOURCE_IMAGE_DEPICTS_EVIDENCE_FIELD",
                "PRELOAD_SOURCE_IMAGE",
                0,
                invalid_depicts_field_count,
                "DEPICTS evidence_field must be title",
            ),
            build_check_row(
                "PRELOAD_SOURCE_IMAGE_DEPICTS_EVIDENCE_TEXT",
                "PRELOAD_SOURCE_IMAGE",
                0,
                invalid_depicts_text_count,
                "DEPICTS evidence_text must exactly equal SourceImage.title",
            ),
        ]
    )

    return report_rows


def validate_event_group_term_candidates(nodes_dir, relations_dir):
    event_group_path = nodes_dir / "event_groups.csv"
    term_path = nodes_dir / "terms.csv"
    relation_path = relations_dir / "event_group_has_term_candidate.csv"
    required_paths = [event_group_path, term_path, relation_path]
    missing_paths = [str(csv_path) for csv_path in required_paths if not csv_path.exists()]
    report_rows = [
        build_check_row(
            "PRELOAD_EVENT_GROUP_TERM_REQUIRED_FILES",
            "PRELOAD_EVENT_GROUP_TERM",
            0,
            len(missing_paths),
            "Missing EventGroup/Term validation inputs: " + "|".join(missing_paths),
        )
    ]

    if len(missing_paths) > 0:
        return report_rows

    event_group_header, event_group_rows = read_csv_rows(
        event_group_path,
        "EventGroup nodes",
    )
    term_header, term_rows = read_csv_rows(term_path, "Term nodes")
    relation_header, relation_rows = read_csv_rows(
        relation_path,
        "EventGroup term candidate relations",
    )
    missing_columns = sorted(
        ({"event_group_id", "name"} - event_group_header)
        | ({"term_id", "name"} - term_header)
        | (
            {
                "start_event_group_id",
                "end_term_id",
                "relation_type",
                "match_method",
                "review_status",
                "answer_eligible",
            }
            - relation_header
        )
    )
    event_group_names = {
        row.get("event_group_id", ""): row.get("name", "")
        for row in event_group_rows
    }
    term_names = {
        row.get("term_id", ""): row.get("name", "") for row in term_rows
    }
    term_name_counts = Counter(
        row.get("name", "") for row in term_rows if row.get("name", "") != ""
    )
    endpoint_name_mismatch_count = sum(
        1
        for row in relation_rows
        if event_group_names.get(row.get("start_event_group_id", ""), "")
        != term_names.get(row.get("end_term_id", ""), "")
    )
    nonunique_term_name_count = sum(
        1
        for row in relation_rows
        if term_name_counts.get(
            term_names.get(row.get("end_term_id", ""), ""),
            0,
        )
        != 1
    )
    invalid_constant_count = sum(
        1
        for row in relation_rows
        if row.get("relation_type", "") != "HAS_TERM_CANDIDATE"
        or row.get("match_method", "") != "UNIQUE_TERM_NAME"
        or row.get("review_status", "") != "AUTO_CANDIDATE"
        or row.get("answer_eligible", "") != "N"
    )
    duplicate_group_count = count_duplicate_keys(
        relation_rows,
        ["start_event_group_id"],
    )
    report_rows.extend(
        [
            build_check_row(
                "PRELOAD_EVENT_GROUP_TERM_REQUIRED_COLUMNS",
                "PRELOAD_EVENT_GROUP_TERM",
                0,
                len(missing_columns),
                "Missing EventGroup term candidate columns: "
                + "|".join(missing_columns),
            ),
            build_check_row(
                "PRELOAD_EVENT_GROUP_TERM_NAME_EXACT",
                "PRELOAD_EVENT_GROUP_TERM",
                0,
                endpoint_name_mismatch_count,
                "EventGroup.name must exactly equal candidate Term.name",
            ),
            build_check_row(
                "PRELOAD_EVENT_GROUP_TERM_NAME_UNIQUE",
                "PRELOAD_EVENT_GROUP_TERM",
                0,
                nonunique_term_name_count,
                "UNIQUE_TERM_NAME candidate targets must have a unique Term.name",
            ),
            build_check_row(
                "PRELOAD_EVENT_GROUP_TERM_CONSTANTS",
                "PRELOAD_EVENT_GROUP_TERM",
                0,
                invalid_constant_count,
                "Invalid HAS_TERM_CANDIDATE relation constants",
            ),
            build_check_row(
                "PRELOAD_EVENT_GROUP_TERM_MAX_ONE",
                "PRELOAD_EVENT_GROUP_TERM",
                0,
                duplicate_group_count,
                "Each EventGroup may have at most one Term candidate",
            ),
        ]
    )

    return report_rows


def validate_event_reign_mapping(nodes_dir, relations_dir, review_path):
    event_path = nodes_dir / "events.csv"
    reign_path = nodes_dir / "reigns.csv"
    started_path = relations_dir / "event_started_during_reign.csv"
    ended_path = relations_dir / "event_ended_during_reign.csv"
    required_paths = [event_path, reign_path, started_path, ended_path, review_path]
    missing_paths = [str(csv_path) for csv_path in required_paths if not csv_path.exists()]
    report_rows = [
        build_check_row(
            "PRELOAD_EVENT_REIGN_REQUIRED_FILES",
            "PRELOAD_EVENT_REIGN",
            0,
            len(missing_paths),
            "Missing event-reign mapping files: " + "|".join(missing_paths),
        )
    ]

    if len(missing_paths) > 0:
        return report_rows

    event_header, event_rows = read_csv_rows(event_path, "Event nodes")
    reign_header, reign_rows = read_csv_rows(reign_path, "Reign nodes")
    started_header, started_rows = read_csv_rows(
        started_path,
        "Event start-reign relations",
    )
    ended_header, ended_rows = read_csv_rows(
        ended_path,
        "Event end-reign relations",
    )
    review_header, review_rows = read_csv_rows(
        review_path,
        "Event-reign mapping review",
    )
    missing_columns = sorted(
        (
            {
                "event_id",
                "start_reign_name",
                "end_reign_name",
                "start_year",
                "end_year",
            }
            - event_header
        )
        | ({"reign_id", "start_year", "end_year"} - reign_header)
        | (
            {"start_event_id", "end_reign_id", "relation_type"}
            - started_header
        )
        | (
            {"start_event_id", "end_reign_id", "relation_type"}
            - ended_header
        )
        | (
            {
                "event_id",
                "relation_type",
                "event_year",
                "issue_code",
                "candidate_reign_ids",
            }
            - review_header
        )
    )

    if len(missing_columns) > 0:
        report_rows.append(
            build_check_row(
                "PRELOAD_EVENT_REIGN_REQUIRED_COLUMNS",
                "PRELOAD_EVENT_REIGN",
                0,
                len(missing_columns),
                "Missing event-reign columns: " + "|".join(missing_columns),
            )
        )
        return report_rows

    event_by_id = {row.get("event_id", ""): row for row in event_rows}
    reign_by_id = {row.get("reign_id", ""): row for row in reign_rows}
    relation_specs = [
        (
            "STARTED_DURING_REIGN",
            "start_reign_name",
            "start_year",
            started_rows,
        ),
        (
            "ENDED_DURING_REIGN",
            "end_reign_name",
            "end_year",
            ended_rows,
        ),
    ]
    expected_attempts = set()
    outcome_counts = Counter()
    relation_year_error_count = 0

    for relation_type, name_column, year_column, relation_rows in relation_specs:
        expected_attempts.update(
            (row["event_id"], relation_type)
            for row in event_rows
            if row.get(name_column, "") != ""
        )

        for relation_row in relation_rows:
            event_id = relation_row.get("start_event_id", "")
            reign_id = relation_row.get("end_reign_id", "")
            outcome_counts[(event_id, relation_type)] += 1
            event_row = event_by_id.get(event_id)
            reign_row = reign_by_id.get(reign_id)

            if event_row is None or reign_row is None:
                continue

            event_year_text = event_row.get(year_column, "")

            if event_year_text == "":
                event_year_text = event_row.get("start_year", "")

            try:
                event_year = int(event_year_text)
                reign_start_year = int(reign_row.get("start_year", ""))
                reign_end_year = int(reign_row.get("end_year", ""))
            except (TypeError, ValueError):
                continue

            if not reign_start_year <= event_year <= reign_end_year:
                relation_year_error_count += 1

    invalid_out_of_range_review_count = 0

    for review_row in review_rows:
        event_id = review_row.get("event_id", "")
        relation_type = review_row.get("relation_type", "")
        outcome_counts[(event_id, relation_type)] += 1

        if review_row.get("issue_code", "") != "YEAR_OUT_OF_RANGE":
            continue

        candidate_ids = [
            candidate_id
            for candidate_id in review_row.get("candidate_reign_ids", "").split("|")
            if candidate_id != ""
        ]
        review_is_invalid = len(candidate_ids) == 0

        try:
            event_year = int(review_row.get("event_year", ""))
        except (TypeError, ValueError):
            review_is_invalid = True
            event_year = None

        for candidate_id in candidate_ids:
            candidate = reign_by_id.get(candidate_id)

            if candidate is None or event_year is None:
                review_is_invalid = True
                continue

            try:
                start_year = int(candidate.get("start_year", ""))
                end_year = int(candidate.get("end_year", ""))
            except (TypeError, ValueError):
                review_is_invalid = True
                continue

            if start_year <= event_year <= end_year:
                review_is_invalid = True

        if review_is_invalid:
            invalid_out_of_range_review_count += 1

    unexpected_outcomes = set(outcome_counts) - expected_attempts
    coverage_error_count = len(unexpected_outcomes) + sum(
        1
        for attempt_key in expected_attempts
        if outcome_counts.get(attempt_key, 0) != 1
    )

    report_rows.extend(
        [
            build_check_row(
                "PRELOAD_EVENT_REIGN_REQUIRED_COLUMNS",
                "PRELOAD_EVENT_REIGN",
                0,
                len(missing_columns),
                "Missing event-reign columns: " + "|".join(missing_columns),
            ),
            build_check_row(
                "PRELOAD_EVENT_REIGN_ATTEMPT_COVERAGE",
                "PRELOAD_EVENT_REIGN",
                0,
                coverage_error_count,
                "Each declared reign name must produce exactly one relation or review row",
            ),
            build_check_row(
                "PRELOAD_EVENT_REIGN_RELATION_YEAR_RANGE",
                "PRELOAD_EVENT_REIGN",
                0,
                relation_year_error_count,
                "Relations whose event year falls outside the matched reign",
            ),
            build_check_row(
                "PRELOAD_EVENT_REIGN_OUT_OF_RANGE_REVIEW",
                "PRELOAD_EVENT_REIGN",
                0,
                invalid_out_of_range_review_count,
                "Invalid YEAR_OUT_OF_RANGE review evidence",
            ),
        ]
    )

    return report_rows


def build_preload_report_rows(args):
    contract_rows = read_preload_contract(args.contract_seed_path)
    report_rows = validate_declared_file_sets(
        args.nodes_dir,
        args.relations_dir,
        args.node_schema_path,
        args.relation_schema_path,
    )
    report_rows.extend(
        validate_preload_contracts(contract_rows, args.nodes_dir, args.relations_dir)
    )
    report_rows.extend(
        validate_relation_merge_identities(
            args.relations_dir,
            args.relation_schema_path,
        )
    )
    report_rows.extend(validate_about_evidence_alignment(args.relations_dir))
    report_rows.extend(
        validate_person_relation_contract(
            args.nodes_dir,
            args.relations_dir,
            args.relation_type_seed_path,
        )
    )
    report_rows.extend(validate_source_image_contract(args.nodes_dir, args.relations_dir))
    report_rows.extend(
        validate_event_group_term_candidates(args.nodes_dir, args.relations_dir)
    )
    report_rows.extend(
        validate_event_reign_mapping(
            args.nodes_dir,
            args.relations_dir,
            args.event_reign_review_path,
        )
    )

    return report_rows


def read_case_seed(case_seed_path):
    fieldnames, rows = read_csv_rows(case_seed_path, "Graph QA case seed")
    required_columns = {
        "case_id",
        "case_type",
        "start_id",
        "middle_id",
        "target_id",
        "property_name",
        "expected_value",
        "expected_count",
        "description",
    }
    missing_columns = sorted(required_columns - fieldnames)

    if len(missing_columns) > 0:
        raise ValueError(
            "Graph QA case seed 필수 컬럼이 없습니다: "
            + ", ".join(missing_columns)
        )

    case_ids = set()

    for row in rows:
        required_values = {"case_id", "case_type", "expected_count", "description"}
        missing_values = sorted(
            column_name
            for column_name in required_values
            if row.get(column_name, "") == ""
        )

        if len(missing_values) > 0:
            raise ValueError(
                "Graph QA case 필수값이 비어 있습니다: "
                + ", ".join(missing_values)
            )

        if not row["expected_count"].isdigit():
            raise ValueError(
                f"Graph QA expected_count가 정수가 아닙니다: {row['case_id']}"
            )

        if row["case_id"] in case_ids:
            raise ValueError(f"Graph QA case_id가 중복되었습니다: {row['case_id']}")

        case_ids.add(row["case_id"])
        row["expected_count_number"] = int(row["expected_count"])

    return rows


def read_graph_context(nodes_dir, relations_dir):
    graph_files = {
        "actions": (nodes_dir / "royal_actions.csv", "RoyalAction nodes"),
        "heritage": (nodes_dir / "heritage_entities.csv", "CulturalHeritage nodes"),
        "monarch_actions": (
            relations_dir / "monarch_associated_with_royal_action.csv",
            "Monarch action relations",
        ),
        "action_targets": (
            relations_dir / "royal_action_targets_entity.csv",
            "RoyalAction target relations",
        ),
        "monarch_reigns": (
            relations_dir / "monarch_held_reign.csv",
            "Monarch reign relations",
        ),
        "reign_polities": (
            relations_dir / "reign_of_polity.csv",
            "Reign polity relations",
        ),
        "image_relations": (
            relations_dir / "source_image_depicts_entity.csv",
            "SourceImage depicts relations",
        ),
        "inscribed_on": (
            relations_dir / "inscription_content_inscribed_on.csv",
            "Inscription physical object relations",
        ),
        "source_presents": (
            relations_dir / "source_text_presents_inscription.csv",
            "SourceText inscription relations",
        ),
    }
    context = {}

    for context_name, (csv_path, purpose) in graph_files.items():
        context[context_name] = read_csv_rows(csv_path, purpose)[1]

    return context


def build_relation_indexes(context):
    actions_by_id = {row["action_id"]: row for row in context["actions"]}
    action_ids_by_monarch = defaultdict(set)
    targets_by_action = defaultdict(set)
    reign_ids_by_monarch = defaultdict(set)
    polities_by_reign = defaultdict(set)
    image_targets = defaultdict(set)
    physical_targets_by_inscription = defaultdict(set)
    inscriptions_by_source_text = defaultdict(set)

    for row in context["monarch_actions"]:
        action_ids_by_monarch[row["start_canonical_id"]].add(row["end_action_id"])

    for row in context["action_targets"]:
        targets_by_action[row["start_action_id"]].add(row["end_canonical_id"])

    for row in context["monarch_reigns"]:
        reign_ids_by_monarch[row["start_canonical_id"]].add(row["end_reign_id"])

    for row in context["reign_polities"]:
        polities_by_reign[row["start_reign_id"]].add(row["end_polity_id"])

    for row in context["image_relations"]:
        image_targets[row["source_image_id"]].add(row["canonical_id"])

    for row in context["inscribed_on"]:
        physical_targets_by_inscription[row["inscription_id"]].add(
            row["canonical_id"]
        )

    for row in context["source_presents"]:
        inscriptions_by_source_text[row["source_text_id"]].add(
            row["inscription_id"]
        )

    return {
        "actions_by_id": actions_by_id,
        "action_ids_by_monarch": action_ids_by_monarch,
        "targets_by_action": targets_by_action,
        "reign_ids_by_monarch": reign_ids_by_monarch,
        "polities_by_reign": polities_by_reign,
        "image_targets": image_targets,
        "physical_targets_by_inscription": physical_targets_by_inscription,
        "inscriptions_by_source_text": inscriptions_by_source_text,
    }


def count_royal_action_path(case_row, indexes):
    actual_count = 0

    for action_id in indexes["action_ids_by_monarch"].get(
        case_row["start_id"],
        set(),
    ):
        action_row = indexes["actions_by_id"].get(action_id)

        if action_row is None:
            continue

        if case_row["target_id"] not in indexes["targets_by_action"].get(
            action_id,
            set(),
        ):
            continue

        if action_row.get(case_row["property_name"], "") != case_row["expected_value"]:
            continue

        actual_count += 1

    return actual_count


def count_reign_path(case_row, indexes):
    actual_count = 0

    for reign_id in indexes["reign_ids_by_monarch"].get(
        case_row["start_id"],
        set(),
    ):
        if case_row["target_id"] in indexes["polities_by_reign"].get(
            reign_id,
            set(),
        ):
            actual_count += 1

    return actual_count


def count_heritage_property(case_row, context):
    return sum(
        1
        for row in context["heritage"]
        if row["canonical_id"] == case_row["target_id"]
        and row.get(case_row["property_name"], "") == case_row["expected_value"]
    )


def count_heritage_absence(case_row, context):
    return sum(
        1
        for row in context["heritage"]
        if row["canonical_id"] == case_row["target_id"]
    )


def count_image_relation(case_row, indexes):
    if case_row["target_id"] in indexes["image_targets"].get(
        case_row["start_id"],
        set(),
    ):
        return 1

    return 0


def count_inscription_source_path(case_row, indexes):
    if case_row["middle_id"] not in indexes["inscriptions_by_source_text"].get(
        case_row["start_id"],
        set(),
    ):
        return 0

    if case_row["target_id"] in indexes["physical_targets_by_inscription"].get(
        case_row["middle_id"],
        set(),
    ):
        return 1

    return 0


def evaluate_case(case_row, context, indexes):
    case_type = case_row["case_type"]

    if case_type == "ROYAL_ACTION_PATH":
        return count_royal_action_path(case_row, indexes)
    elif case_type == "REIGN_PATH":
        return count_reign_path(case_row, indexes)
    elif case_type == "HERITAGE_PROPERTY":
        return count_heritage_property(case_row, context)
    elif case_type == "HERITAGE_ABSENT":
        return count_heritage_absence(case_row, context)
    elif case_type == "IMAGE_RELATION_PATH":
        return count_image_relation(case_row, indexes)
    elif case_type == "INSCRIPTION_SOURCE_PATH":
        return count_inscription_source_path(case_row, indexes)

    raise ValueError(f"지원하지 않는 Graph QA case_type입니다: {case_type}")


def build_report_rows(case_rows, context, indexes):
    report_rows = []

    for case_row in case_rows:
        actual_count = evaluate_case(case_row, context, indexes)
        expected_count = case_row["expected_count_number"]
        status = "FAIL"

        if actual_count == expected_count:
            status = "PASS"

        report_rows.append(
            {
                "case_id": case_row["case_id"],
                "case_type": case_row["case_type"],
                "expected_count": expected_count,
                "actual_count": actual_count,
                "status": status,
                "description": case_row["description"],
            }
        )

    return report_rows


def save_report(report_rows, report_path):
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_suffix(f"{report_path.suffix}.tmp")

    try:
        with temporary_path.open(
            "w", encoding="utf-8-sig", newline=""
        ) as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=[
                    "case_id",
                    "case_type",
                    "expected_count",
                    "actual_count",
                    "status",
                    "description",
                ],
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(report_rows)

        temporary_path.replace(report_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()

        raise


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    preload_report_rows = build_preload_report_rows(args)
    case_rows = read_case_seed(args.case_seed_path)
    context = read_graph_context(args.nodes_dir, args.relations_dir)
    indexes = build_relation_indexes(context)
    golden_report_rows = build_report_rows(case_rows, context, indexes)
    report_rows = [*preload_report_rows, *golden_report_rows]
    failed_rows = [row for row in report_rows if row["status"] == "FAIL"]

    if args.save:
        save_report(report_rows, args.report_path)

    print(f"preload_contract_checks: {len(preload_report_rows)}")
    print(f"golden_qa_cases: {len(golden_report_rows)}")
    print(f"graph_qa_cases: {len(report_rows)}")
    print(f"graph_qa_passed: {len(report_rows) - len(failed_rows)}")
    print(f"graph_qa_failed: {len(failed_rows)}")
    print(f"report_path: {args.report_path}")

    if len(failed_rows) > 0:
        for row in failed_rows:
            print(
                f"[FAIL] {row['case_id']}: expected={row['expected_count']} "
                f"actual={row['actual_count']}"
            )

        raise SystemExit(1)

    if not args.save:
        print("dry_run: no files saved. Use --save to write the QA report.")


if __name__ == "__main__":
    main()

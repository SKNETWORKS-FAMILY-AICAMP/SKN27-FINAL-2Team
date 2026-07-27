from __future__ import annotations

from datetime import datetime, timezone
from json import dumps
from pathlib import Path
from uuid import uuid4

import pandas as pd

from entity_resolution.load_final_identity import (
    execute_load_batches,
    load_neo4j_connection_config,
)


def load_source_relationship_tables(
    output_directory: str,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """전처리 출력 CSV 중 Neo4j 적재 대상만 읽는다."""
    output_path = Path(output_directory)
    table_names = [
        "source_record_nodes",
        "source_record_relationships",
        "thesaurus_category_nodes",
        "source_category_relationships",
        "thesaurus_category_relationships",
        "canonical_entity_relationships",
    ]
    tables: dict[str, pd.DataFrame] = {}
    for table_name in table_names:
        input_path = output_path / policy["outputs"][table_name]
        if not input_path.is_file():
            raise FileNotFoundError(
                f"원천 관계 적재 CSV가 없습니다: {input_path}"
            )
        tables[table_name] = pd.read_csv(
            input_path,
            dtype=str,
        ).fillna("")
    return tables


def build_source_relationship_load_plan(
    tables: dict[str, pd.DataFrame],
) -> dict[str, object]:
    """적재 CSV의 ID 유일성과 endpoint 무결성을 검사한다."""
    errors: list[str] = []
    unique_contracts = [
        ("source_record_nodes", "source_record_id"),
        ("source_record_relationships", "source_relationship_id"),
        ("thesaurus_category_nodes", "category_id"),
        (
            "source_category_relationships",
            "source_category_relationship_id",
        ),
        (
            "thesaurus_category_relationships",
            "category_relationship_id",
        ),
        (
            "canonical_entity_relationships",
            "canonical_relationship_id",
        ),
    ]
    for table_name, id_column in unique_contracts:
        table = tables[table_name]
        if id_column not in table.columns:
            errors.append(f"{table_name}: {id_column} 컬럼이 없습니다.")
            continue
        if table[id_column].eq("").any():
            errors.append(f"{table_name}: 빈 {id_column}가 있습니다.")
        if table[id_column].duplicated().any():
            errors.append(f"{table_name}: 중복 {id_column}가 있습니다.")

    source_ids = set(tables["source_record_nodes"]["source_record_id"])
    category_ids = set(tables["thesaurus_category_nodes"]["category_id"])
    endpoint_contracts = [
        (
            "source_record_relationships",
            "start_source_record_id",
            source_ids,
        ),
        (
            "source_record_relationships",
            "end_source_record_id",
            source_ids,
        ),
        (
            "source_category_relationships",
            "source_record_id",
            source_ids,
        ),
        (
            "source_category_relationships",
            "category_id",
            category_ids,
        ),
        (
            "thesaurus_category_relationships",
            "child_category_id",
            category_ids,
        ),
        (
            "thesaurus_category_relationships",
            "parent_category_id",
            category_ids,
        ),
    ]
    for table_name, id_column, valid_ids in endpoint_contracts:
        missing_ids = set(tables[table_name][id_column]).difference(
            valid_ids
        )
        if missing_ids:
            errors.append(
                f"{table_name}: 없는 {id_column} 참조 "
                f"{len(missing_ids)}건"
            )
    status = "READY"
    if errors:
        status = "BLOCKED"
    return {
        "status": status,
        "validation_errors": errors,
        "load_counts": {
            table_name: len(table)
            for table_name, table in tables.items()
        },
    }


def build_source_relationship_load_queries() -> dict[str, str | list[str]]:
    """SourceRecord 관계·시소러스 분류 적재용 Cypher를 반환한다."""
    constraints = [
        (
            "CREATE CONSTRAINT source_record_id IF NOT EXISTS "
            "FOR (n:SourceRecord) REQUIRE n.source_record_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT thesaurus_category_id IF NOT EXISTS "
            "FOR (n:ThesaurusCategory) REQUIRE n.category_id IS UNIQUE"
        ),
        (
            "CREATE INDEX source_record_type IF NOT EXISTS "
            "FOR (n:SourceRecord) ON (n.record_type)"
        ),
        (
            "CREATE INDEX thesaurus_category_path IF NOT EXISTS "
            "FOR (n:ThesaurusCategory) ON (n.category_path)"
        ),
    ]
    return {
        "constraints": constraints,
        "source_record_nodes": """
UNWIND $rows AS row
MERGE (source:SourceRecord {source_record_id: row.source_record_id})
ON CREATE SET source.record_status = 'SOURCE_ASSERTED'
SET source.source = row.source,
    source.source_key = row.source_key,
    source.source_release = row.source_release,
    source.record_type = row.record_type,
    source.display_name = row.display_name,
    source.source_urls_json = row.source_urls_json,
    source.source_metadata_json = row.source_metadata_json,
    source.source_assertion_status = 'SOURCE_ASSERTED',
    source.source_relationship_import_scope = $import_scope,
    source.source_relationship_load_run_id = $load_run_id
""",
        "thesaurus_category_nodes": """
UNWIND $rows AS row
MERGE (category:ThesaurusCategory {category_id: row.category_id})
SET category.name = row.category_name,
    category.category_path = row.category_path,
    category.depth = toInteger(row.depth),
    category.source = row.source,
    category.source_release = row.source_release,
    category.import_scope = $import_scope,
    category.load_run_id = $load_run_id
""",
        "source_record_relationships": """
UNWIND $rows AS row
MATCH (start:SourceRecord {
    source_record_id: row.start_source_record_id
})
MATCH (end:SourceRecord {
    source_record_id: row.end_source_record_id
})
MERGE (start)-[relation:SOURCE_RELATION {
    source_relationship_id: row.source_relationship_id
}]->(end)
SET relation.relation_type = row.relation_type,
    relation.raw_relation_type = row.raw_relation_type,
    relation.relation_qualifiers_json =
        row.relation_qualifiers_json,
    relation.source_dataset = row.source_dataset,
    relation.source_release = row.source_release,
    relation.verification_status = row.verification_status,
    relation.evidence_urls_json = row.evidence_urls_json,
    relation.detail_urls_json = row.detail_urls_json,
    relation.scopes_json = row.scopes_json,
    relation.source_row_count = toInteger(row.source_row_count),
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
        "source_category_relationships": """
UNWIND $rows AS row
MATCH (source:SourceRecord {
    source_record_id: row.source_record_id
})
MATCH (category:ThesaurusCategory {
    category_id: row.category_id
})
MERGE (source)-[relation:IN_THESAURUS_CATEGORY {
    source_category_relationship_id:
        row.source_category_relationship_id
}]->(category)
SET relation.relation_type = row.relation_type,
    relation.source_dataset = row.source_dataset,
    relation.source_release = row.source_release,
    relation.verification_status = row.verification_status,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
        "thesaurus_category_relationships": """
UNWIND $rows AS row
MATCH (child:ThesaurusCategory {
    category_id: row.child_category_id
})
MATCH (parent:ThesaurusCategory {
    category_id: row.parent_category_id
})
MERGE (child)-[relation:THESAURUS_SUBCATEGORY_OF {
    category_relationship_id: row.category_relationship_id
}]->(parent)
SET relation.relation_type = row.relation_type,
    relation.source_dataset = row.source_dataset,
    relation.source_release = row.source_release,
    relation.verification_status = row.verification_status,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
        "canonical_entity_relationships": """
UNWIND $rows AS row
MATCH (start:CanonicalEntity {
    canonical_id: row.start_canonical_id
})
MATCH (end:CanonicalEntity {
    canonical_id: row.end_canonical_id
})
MERGE (start)-[relation:FACT_RELATION {
    canonical_relationship_id: row.canonical_relationship_id
}]->(end)
SET relation.relation_type = row.relation_type,
    relation.source_relationship_ids_json =
        row.source_relationship_ids_json,
    relation.raw_relation_types_json = row.raw_relation_types_json,
    relation.relation_qualifiers_json =
        row.relation_qualifiers_json,
    relation.source_datasets_json = row.source_datasets_json,
    relation.source_releases_json = row.source_releases_json,
    relation.evidence_urls_json = row.evidence_urls_json,
    relation.detail_urls_json = row.detail_urls_json,
    relation.scopes_json = row.scopes_json,
    relation.evidence_count = toInteger(row.evidence_count),
    relation.source_row_count = toInteger(row.source_row_count),
    relation.verification_status = row.verification_status,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
    }


def load_source_relationships_to_neo4j(
    output_directory: str,
    policy: dict,
    project_root: str,
    database: str = "",
    batch_size: int | None = None,
    dry_run: bool = False,
    canonical_only: bool = False,
) -> dict[str, object]:
    """검증된 원천 관계 CSV를 idempotent 방식으로 Neo4j에 upsert한다."""
    tables = load_source_relationship_tables(output_directory, policy)
    plan = build_source_relationship_load_plan(tables)
    load_order = [
        "source_record_nodes",
        "thesaurus_category_nodes",
        "source_record_relationships",
        "source_category_relationships",
        "thesaurus_category_relationships",
        "canonical_entity_relationships",
    ]
    stage = "NEO4J_SOURCE_RELATIONSHIP_LOAD"
    if canonical_only:
        load_order = ["canonical_entity_relationships"]
        stage = "NEO4J_CANONICAL_FACT_LOAD"
        plan["load_counts"] = {
            table_name: len(tables[table_name])
            for table_name in load_order
        }
    if plan["validation_errors"]:
        return {
            **plan,
            "stage": stage,
            "dry_run": dry_run,
            "canonical_only": canonical_only,
        }
    if dry_run:
        return {
            **plan,
            "stage": stage,
            "dry_run": True,
            "canonical_only": canonical_only,
        }

    from neo4j import GraphDatabase

    load_policy = policy["neo4j_load"]
    resolved_batch_size = int(
        batch_size or load_policy["batch_size"]
    )
    import_scope = str(load_policy["import_scope"])
    if canonical_only:
        import_scope = str(load_policy["canonical_fact_import_scope"])
    load_run_id = str(uuid4())
    connection = load_neo4j_connection_config(
        Path(project_root),
        database=database,
    )
    queries = build_source_relationship_load_queries()
    session_options: dict[str, str] = {}
    if connection["database"]:
        session_options["database"] = connection["database"]
    loaded_counts: dict[str, int] = {}
    with GraphDatabase.driver(
        connection["uri"],
        auth=(connection["user"], connection["password"]),
    ) as driver:
        driver.verify_connectivity()
        with driver.session(**session_options) as session:
            if not canonical_only:
                for constraint in queries["constraints"]:
                    session.run(constraint).consume()
            for table_name in load_order:
                loaded_counts[table_name] = execute_load_batches(
                    session,
                    str(queries[table_name]),
                    tables[table_name],
                    resolved_batch_size,
                    import_scope,
                    load_run_id,
                )

    manifest = {
        "status": "COMPLETED",
        "stage": stage,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": policy["policy_version"],
        "import_scope": import_scope,
        "load_run_id": load_run_id,
        "database": connection["database"],
        "canonical_only": canonical_only,
        "loaded_counts": loaded_counts,
    }
    manifest_file = str(load_policy["manifest_file"])
    if canonical_only:
        manifest_file = str(
            load_policy["canonical_fact_manifest_file"]
        )
    manifest_path = (
        Path(output_directory) / manifest_file
    )
    manifest_path.write_text(
        dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest["manifest_path"] = str(manifest_path)
    return manifest

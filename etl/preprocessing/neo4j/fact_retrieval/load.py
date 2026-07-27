from __future__ import annotations

from datetime import datetime, timezone
from json import JSONDecodeError, dumps, loads
from pathlib import Path
from uuid import uuid4

import pandas as pd

from entity_resolution.load_final_identity import (
    load_neo4j_connection_config,
)


def build_fact_retrieval_load_plan(
    canonical_facts: pd.DataFrame,
    anchor_nodes: pd.DataFrame,
    supporting_source_nodes: pd.DataFrame,
    canonical_anchor_links: pd.DataFrame,
    source_anchor_links: pd.DataFrame,
    anchor_facts: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    policy: dict,
) -> dict[str, object]:
    """통합 사실 검색 그래프의 ID와 endpoint 무결성을 검사한다."""
    errors: list[str] = []
    table_contracts = [
        (
            "canonical_facts",
            canonical_facts,
            "canonical_relationship_id",
            {
                "canonical_relationship_id",
                "start_canonical_id",
                "end_canonical_id",
                "relation_type",
                "verification_status",
            },
        ),
        (
            "anchor_nodes",
            anchor_nodes,
            "anchor_id",
            {
                "anchor_id",
                "anchor_kind",
                "display_name",
                "entity_type",
                "resolution_status",
            },
        ),
        (
            "supporting_source_nodes",
            supporting_source_nodes,
            "source_record_id",
            {"source_record_id", "source"},
        ),
        (
            "canonical_anchor_links",
            canonical_anchor_links,
            "canonical_anchor_link_id",
            {
                "canonical_anchor_link_id",
                "canonical_id",
                "anchor_id",
                "verification_status",
            },
        ),
        (
            "source_anchor_links",
            source_anchor_links,
            "source_anchor_link_id",
            {
                "source_anchor_link_id",
                "source_record_id",
                "anchor_id",
                "link_status",
            },
        ),
        (
            "anchor_facts",
            anchor_facts,
            "anchor_fact_id",
            {
                "anchor_fact_id",
                "start_anchor_id",
                "end_anchor_id",
                "relation_type",
                "verification_status",
                "search_status",
            },
        ),
    ]
    for (
        table_name,
        table,
        id_column,
        required_columns,
    ) in table_contracts:
        missing_columns = required_columns.difference(table.columns)
        if missing_columns:
            errors.append(
                f"{table_name}: 필수 컬럼이 없습니다: "
                + ", ".join(sorted(missing_columns))
            )
            continue
        if id_column not in table.columns:
            errors.append(f"{table_name}: {id_column} 컬럼이 없습니다.")
            continue
        if table[id_column].astype(str).str.strip().eq("").any():
            errors.append(f"{table_name}: 빈 {id_column}가 있습니다.")
        if table[id_column].duplicated().any():
            errors.append(f"{table_name}: 중복 {id_column}가 있습니다.")
    if errors:
        return {
            "status": "BLOCKED",
            "validation_errors": errors,
            "load_counts": {},
        }

    canonical_ids = set(
        canonical_registry["canonical_id"].astype(str)
    )
    source_record_ids = set(
        supporting_source_nodes["source_record_id"].astype(str)
    )
    anchor_ids = set(anchor_nodes["anchor_id"].astype(str))
    endpoint_contracts = [
        (
            "canonical_facts",
            canonical_facts,
            "start_canonical_id",
            canonical_ids,
        ),
        (
            "canonical_facts",
            canonical_facts,
            "end_canonical_id",
            canonical_ids,
        ),
        (
            "canonical_anchor_links",
            canonical_anchor_links,
            "canonical_id",
            canonical_ids,
        ),
        (
            "canonical_anchor_links",
            canonical_anchor_links,
            "anchor_id",
            anchor_ids,
        ),
        (
            "source_anchor_links",
            source_anchor_links,
            "source_record_id",
            source_record_ids,
        ),
        (
            "source_anchor_links",
            source_anchor_links,
            "anchor_id",
            anchor_ids,
        ),
        (
            "anchor_facts",
            anchor_facts,
            "start_anchor_id",
            anchor_ids,
        ),
        (
            "anchor_facts",
            anchor_facts,
            "end_anchor_id",
            anchor_ids,
        ),
    ]
    for table_name, table, column_name, valid_ids in endpoint_contracts:
        missing_ids = set(
            table[column_name].astype(str)
        ).difference(valid_ids)
        if missing_ids:
            errors.append(
                f"{table_name}: 없는 {column_name} 참조 "
                f"{len(missing_ids)}건"
            )
    if (
        canonical_facts["start_canonical_id"]
        == canonical_facts["end_canonical_id"]
    ).any():
        errors.append("canonical_facts: 자기 관계가 있습니다.")
    if (
        anchor_facts["start_anchor_id"]
        == anchor_facts["end_anchor_id"]
    ).any():
        errors.append("anchor_facts: 자기 관계가 있습니다.")
    required_text_columns = [
        ("canonical_facts", canonical_facts, "relation_type"),
        ("anchor_nodes", anchor_nodes, "display_name"),
        ("anchor_nodes", anchor_nodes, "entity_type"),
        ("anchor_facts", anchor_facts, "relation_type"),
    ]
    for table_name, table, column_name in required_text_columns:
        if table[column_name].astype(str).str.strip().eq("").any():
            errors.append(
                f"{table_name}: 빈 {column_name} 값이 있습니다."
            )
    allowed_anchor_kinds = {
        str(policy["anchor_projection"]["canonical_anchor_kind"]),
        str(policy["anchor_projection"]["source_anchor_kind"]),
    }
    invalid_anchor_kinds = set(
        anchor_nodes["anchor_kind"]
    ).difference(allowed_anchor_kinds)
    if invalid_anchor_kinds:
        errors.append(
            "anchor_nodes: 허용되지 않은 anchor_kind "
            + ", ".join(sorted(invalid_anchor_kinds))
        )
    allowed_search_statuses = {
        str(policy["anchor_projection"]["canonical_search_status"]),
        str(
            policy["anchor_projection"][
                "source_neighbor_search_status"
            ]
        ),
    }
    invalid_search_statuses = set(
        anchor_facts["search_status"]
    ).difference(allowed_search_statuses)
    if invalid_search_statuses:
        errors.append(
            "anchor_facts: 허용되지 않은 search_status "
            + ", ".join(sorted(invalid_search_statuses))
        )
    json_columns = [
        (canonical_facts, "evidence_urls_json"),
        (canonical_facts, "evidence_sentences_json"),
        (anchor_nodes, "topic_ids_json"),
        (anchor_nodes, "era_ids_json"),
        (anchor_facts, "source_relationship_ids_json"),
        (anchor_facts, "evidence_urls_json"),
    ]
    for table, column_name in json_columns:
        if column_name not in table.columns:
            errors.append(f"JSON 필수 컬럼이 없습니다: {column_name}")
            continue
        invalid_json_count = 0
        for value in table[column_name]:
            try:
                parsed = loads(str(value))
            except (JSONDecodeError, TypeError):
                invalid_json_count += 1
                continue
            if not isinstance(parsed, list):
                invalid_json_count += 1
        if invalid_json_count:
            errors.append(
                f"{column_name}: JSON 배열이 아닌 값 "
                f"{invalid_json_count}건"
            )
    status = "READY"
    if errors:
        status = "BLOCKED"
    return {
        "status": status,
        "validation_errors": errors,
        "load_counts": {
            "canonical_facts": len(canonical_facts),
            "anchor_nodes": len(anchor_nodes),
            "supporting_source_nodes": len(
                supporting_source_nodes
            ),
            "canonical_anchor_links": len(canonical_anchor_links),
            "source_anchor_links": len(source_anchor_links),
            "anchor_facts": len(anchor_facts),
        },
    }


def build_fact_retrieval_load_queries() -> dict[str, object]:
    """Canonical 사실과 검색 Anchor 적재용 Cypher를 반환한다."""
    constraints = [
        (
            "CREATE CONSTRAINT entity_anchor_id IF NOT EXISTS "
            "FOR (n:EntityAnchor) REQUIRE n.anchor_id IS UNIQUE"
        ),
        (
            "CREATE INDEX entity_anchor_canonical_id IF NOT EXISTS "
            "FOR (n:EntityAnchor) ON (n.canonical_id)"
        ),
        (
            "CREATE INDEX entity_anchor_source_record_id IF NOT EXISTS "
            "FOR (n:EntityAnchor) ON (n.source_record_id)"
        ),
        (
            "CREATE INDEX fact_relation_type IF NOT EXISTS "
            "FOR ()-[r:FACT_RELATION]-() ON (r.relation_type)"
        ),
        (
            "CREATE INDEX anchor_fact_relation_type IF NOT EXISTS "
            "FOR ()-[r:ANCHOR_FACT]-() ON (r.relation_type)"
        ),
    ]
    return {
        "constraints": constraints,
        "canonical_facts": """
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
    relation.description_mention_ids_json =
        row.description_mention_ids_json,
    relation.source_relationship_ids_json =
        row.source_relationship_ids_json,
    relation.raw_relation_types_json = row.raw_relation_types_json,
    relation.source_datasets_json = row.source_datasets_json,
    relation.source_releases_json = row.source_releases_json,
    relation.evidence_urls_json = row.evidence_urls_json,
    relation.detail_urls_json = row.detail_urls_json,
    relation.evidence_sentences_json = row.evidence_sentences_json,
    relation.scopes_json = row.scopes_json,
    relation.extraction_methods_json = row.extraction_methods_json,
    relation.evidence_count = toInteger(row.evidence_count),
    relation.source_row_count = toInteger(row.source_row_count),
    relation.verification_status = row.verification_status,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
RETURN count(relation) AS loaded_count
""",
        "anchor_nodes": """
UNWIND $rows AS row
MERGE (anchor:EntityAnchor {anchor_id: row.anchor_id})
SET anchor.anchor_kind = row.anchor_kind,
    anchor.canonical_id = row.canonical_id,
    anchor.source_record_id = row.source_record_id,
    anchor.display_name = row.display_name,
    anchor.normalized_name = row.normalized_name,
    anchor.entity_type = row.entity_type,
    anchor.resolution_status = row.resolution_status,
    anchor.source = row.source,
    anchor.source_urls_json = row.source_urls_json,
    anchor.topic_ids_json = row.topic_ids_json,
    anchor.era_ids_json = row.era_ids_json,
    anchor.policy_version = row.policy_version,
    anchor.import_scope = $import_scope,
    anchor.load_run_id = $load_run_id
RETURN count(anchor) AS loaded_count
""",
        "supporting_source_nodes": """
UNWIND $rows AS row
MERGE (source:SourceRecord {source_record_id: row.source_record_id})
SET source.source = row.source,
    source.source_key = row.source_key,
    source.source_release = row.source_release,
    source.source_metadata_json = row.source_metadata_json,
    source.import_scope = $import_scope,
    source.load_run_id = $load_run_id
RETURN count(source) AS loaded_count
""",
        "canonical_anchor_links": """
UNWIND $rows AS row
MATCH (canonical:CanonicalEntity {canonical_id: row.canonical_id})
MATCH (anchor:EntityAnchor {anchor_id: row.anchor_id})
MERGE (canonical)-[relation:HAS_RETRIEVAL_ANCHOR {
    canonical_anchor_link_id: row.canonical_anchor_link_id
}]->(anchor)
SET relation.verification_status = row.verification_status,
    relation.policy_version = row.policy_version,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
RETURN count(relation) AS loaded_count
""",
        "source_anchor_links": """
UNWIND $rows AS row
MATCH (source:SourceRecord {source_record_id: row.source_record_id})
MATCH (anchor:EntityAnchor {anchor_id: row.anchor_id})
MERGE (source)-[relation:INDEXED_BY_ANCHOR {
    source_anchor_link_id: row.source_anchor_link_id
}]->(anchor)
SET relation.link_status = row.link_status,
    relation.policy_version = row.policy_version,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
RETURN count(relation) AS loaded_count
""",
        "anchor_facts": """
UNWIND $rows AS row
MATCH (start:EntityAnchor {anchor_id: row.start_anchor_id})
MATCH (end:EntityAnchor {anchor_id: row.end_anchor_id})
MERGE (start)-[relation:ANCHOR_FACT {
    anchor_fact_id: row.anchor_fact_id
}]->(end)
SET relation.relation_type = row.relation_type,
    relation.origin_scopes_json = row.origin_scopes_json,
    relation.source_relationship_ids_json =
        row.source_relationship_ids_json,
    relation.canonical_relationship_ids_json =
        row.canonical_relationship_ids_json,
    relation.evidence_urls_json = row.evidence_urls_json,
    relation.detail_urls_json = row.detail_urls_json,
    relation.evidence_sentences_json = row.evidence_sentences_json,
    relation.source_datasets_json = row.source_datasets_json,
    relation.verification_statuses_json =
        row.verification_statuses_json,
    relation.resolution_scopes_json = row.resolution_scopes_json,
    relation.verification_status = row.verification_status,
    relation.search_status = row.search_status,
    relation.policy_version = row.policy_version,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
RETURN count(relation) AS loaded_count
""",
        "verification": {
            "canonical_facts": """
MATCH ()-[item:FACT_RELATION {load_run_id: $load_run_id}]->()
RETURN count(item) AS loaded_count
""",
            "anchor_nodes": """
MATCH (item:EntityAnchor {load_run_id: $load_run_id})
RETURN count(item) AS loaded_count
""",
            "supporting_source_nodes": """
MATCH (item:SourceRecord {load_run_id: $load_run_id})
RETURN count(item) AS loaded_count
""",
            "canonical_anchor_links": """
MATCH ()-[item:HAS_RETRIEVAL_ANCHOR {
    load_run_id: $load_run_id
}]->()
RETURN count(item) AS loaded_count
""",
            "source_anchor_links": """
MATCH ()-[item:INDEXED_BY_ANCHOR {load_run_id: $load_run_id}]->()
RETURN count(item) AS loaded_count
""",
            "anchor_facts": """
MATCH ()-[item:ANCHOR_FACT {load_run_id: $load_run_id}]->()
RETURN count(item) AS loaded_count
""",
        },
        "prune_stale": [
            """
MATCH ()-[item:FACT_RELATION {import_scope: $import_scope}]->()
WHERE item.load_run_id <> $load_run_id
DELETE item
RETURN count(item) AS pruned_count
""",
            """
MATCH ()-[item:HAS_RETRIEVAL_ANCHOR {
    import_scope: $import_scope
}]->()
WHERE item.load_run_id <> $load_run_id
DELETE item
RETURN count(item) AS pruned_count
""",
            """
MATCH ()-[item:INDEXED_BY_ANCHOR {
    import_scope: $import_scope
}]->()
WHERE item.load_run_id <> $load_run_id
DELETE item
RETURN count(item) AS pruned_count
""",
            """
MATCH ()-[item:ANCHOR_FACT {import_scope: $import_scope}]->()
WHERE item.load_run_id <> $load_run_id
DELETE item
RETURN count(item) AS pruned_count
""",
            """
MATCH (item:EntityAnchor {import_scope: $import_scope})
WHERE item.load_run_id <> $load_run_id
DETACH DELETE item
RETURN count(item) AS pruned_count
""",
        ],
    }


def execute_verified_load_batches(
    transaction: object,
    query: str,
    table: pd.DataFrame,
    batch_size: int,
    import_scope: str,
    load_run_id: str,
) -> int:
    """한 transaction에서 batch별 실제 MATCH·MERGE 행 수를 확인한다."""
    records = table.to_dict("records")
    loaded_count = 0
    for start_index in range(0, len(records), batch_size):
        batch = records[start_index : start_index + batch_size]
        result = transaction.run(
            query,
            rows=batch,
            import_scope=import_scope,
            load_run_id=load_run_id,
        )
        result_row = result.single()
        actual_count = 0
        if result_row is not None:
            actual_count = int(result_row["loaded_count"])
        if actual_count != len(batch):
            raise RuntimeError(
                "Neo4j 적재 행 수가 입력과 다릅니다: "
                f"입력 {len(batch)}건, 반영 {actual_count}건"
            )
        loaded_count += actual_count
    return loaded_count


def load_fact_retrieval_to_neo4j(
    tables: dict[str, pd.DataFrame],
    canonical_registry: pd.DataFrame,
    policy: dict,
    project_root: Path,
    output_directory: Path,
    database: str = "",
    batch_size: int | None = None,
    dry_run: bool = True,
) -> dict[str, object]:
    """검증된 사실 검색 그래프를 Neo4j에 적재하거나 계획만 반환한다."""
    plan = build_fact_retrieval_load_plan(
        tables["canonical_facts"],
        tables["anchor_nodes"],
        tables["supporting_source_nodes"],
        tables["canonical_anchor_links"],
        tables["source_anchor_links"],
        tables["anchor_facts"],
        canonical_registry,
        policy,
    )
    if plan["validation_errors"]:
        return {
            **plan,
            "stage": "NEO4J_FACT_RETRIEVAL_LOAD",
            "dry_run": dry_run,
        }
    if dry_run:
        return {
            **plan,
            "stage": "NEO4J_FACT_RETRIEVAL_LOAD",
            "dry_run": True,
        }

    from neo4j import GraphDatabase

    load_policy = policy["neo4j_load"]
    resolved_batch_size = int(
        batch_size or load_policy["batch_size"]
    )
    import_scope = str(load_policy["import_scope"])
    load_run_id = str(uuid4())
    connection = load_neo4j_connection_config(
        project_root,
        database=database,
    )
    queries = build_fact_retrieval_load_queries()
    session_options: dict[str, str] = {}
    if connection["database"]:
        session_options["database"] = connection["database"]
    load_order = [
        "supporting_source_nodes",
        "anchor_nodes",
        "canonical_facts",
        "canonical_anchor_links",
        "source_anchor_links",
        "anchor_facts",
    ]
    loaded_counts: dict[str, int] = {}
    with GraphDatabase.driver(
        connection["uri"],
        auth=(connection["user"], connection["password"]),
    ) as driver:
        driver.verify_connectivity()
        with driver.session(**session_options) as session:
            for constraint in queries["constraints"]:
                session.run(constraint).consume()

            def load_all_tables(
                transaction: object,
            ) -> tuple[dict[str, int], dict[str, int], int]:
                transaction_loaded_counts: dict[str, int] = {}
                for table_name in load_order:
                    transaction_loaded_counts[table_name] = (
                        execute_verified_load_batches(
                            transaction,
                            str(queries[table_name]),
                            tables[table_name],
                            resolved_batch_size,
                            import_scope,
                            load_run_id,
                        )
                    )
                transaction_verification_counts: dict[str, int] = {}
                verification_queries = queries["verification"]
                for table_name in load_order:
                    result = transaction.run(
                        str(verification_queries[table_name]),
                        load_run_id=load_run_id,
                    ).single()
                    actual_count = 0
                    if result is not None:
                        actual_count = int(result["loaded_count"])
                    expected_count = len(tables[table_name])
                    if actual_count != expected_count:
                        raise RuntimeError(
                            f"{table_name} 적재 후 검증 실패: "
                            f"예상 {expected_count}건, 실제 "
                            f"{actual_count}건"
                        )
                    transaction_verification_counts[
                        table_name
                    ] = actual_count
                transaction_pruned_count = 0
                if bool(load_policy["prune_stale_rows"]):
                    for prune_query in queries["prune_stale"]:
                        result = transaction.run(
                            str(prune_query),
                            import_scope=import_scope,
                            load_run_id=load_run_id,
                        ).single()
                        if result is not None:
                            transaction_pruned_count += int(
                                result["pruned_count"]
                            )
                return (
                    transaction_loaded_counts,
                    transaction_verification_counts,
                    transaction_pruned_count,
                )

            (
                loaded_counts,
                verification_counts,
                pruned_count,
            ) = session.execute_write(load_all_tables)
    manifest = {
        "status": "COMPLETED",
        "stage": "NEO4J_FACT_RETRIEVAL_LOAD",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": str(policy["policy_version"]),
        "import_scope": import_scope,
        "load_run_id": load_run_id,
        "database": connection["database"],
        "loaded_counts": loaded_counts,
        "verification_counts": verification_counts,
        "pruned_stale_count": pruned_count,
    }
    manifest_path = (
        output_directory
        / policy["outputs"]["load_manifest"]
    )
    manifest_path.write_text(
        dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest["manifest_path"] = str(manifest_path)
    return manifest

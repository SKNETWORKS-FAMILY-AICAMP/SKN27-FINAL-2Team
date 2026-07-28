from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return list(csv.DictReader(input_file))


def load_connection_config(project_root: Path) -> dict[str, str]:
    load_dotenv(project_root / ".env")
    user = os.getenv("FACT_NEO4J_USER") or os.getenv("NEO4J_USER")
    password = os.getenv("FACT_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD")
    port = os.getenv("FACT_NEO4J_BOLT_PORT") or "7688"
    missing = [
        name
        for name, value in (
            ("NEO4J_USER", user),
            ("NEO4J_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing Neo4j connection settings: " + ", ".join(missing)
        )
    return {
        "uri": f"bolt://localhost:{port}",
        "user": str(user),
        "password": str(password),
    }


def run_batched_query(
    session: Any,
    query: str,
    rows: list[dict[str, str]],
    batch_size: int,
) -> int:
    processed = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        result = session.run(query, rows=batch).single()
        if result is None:
            raise RuntimeError("Neo4j batch query returned no count")
        processed += int(result["processed"])
    return processed


def create_schema(session: Any) -> None:
    statements = [
        (
            "CREATE CONSTRAINT graph_entity_id IF NOT EXISTS "
            "FOR (n:GraphEntity) REQUIRE n.entity_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT fact_id IF NOT EXISTS "
            "FOR (n:Fact) REQUIRE n.fact_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT evidence_id IF NOT EXISTS "
            "FOR (n:EvidenceSpan) REQUIRE n.evidence_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT source_record_id IF NOT EXISTS "
            "FOR (n:SourceRecord) REQUIRE n.source_record_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT entity_name_id IF NOT EXISTS "
            "FOR (n:EntityName) REQUIRE n.entity_name_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT exam_term_id IF NOT EXISTS "
            "FOR (n:ExamTerm) REQUIRE n.exam_term_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT topic_id IF NOT EXISTS "
            "FOR (n:Topic) REQUIRE n.topic_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT era_id IF NOT EXISTS "
            "FOR (n:Era) REQUIRE n.era_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT entity_type_name IF NOT EXISTS "
            "FOR (n:EntityType) REQUIRE n.name IS UNIQUE"
        ),
        (
            "CREATE INDEX graph_entity_resolution IF NOT EXISTS "
            "FOR (n:GraphEntity) ON (n.resolution_status)"
        ),
        (
            "CREATE INDEX graph_entity_retrieval IF NOT EXISTS "
            "FOR (n:GraphEntity) ON (n.retrieval_eligible)"
        ),
        (
            "CREATE INDEX canonical_entity_exact_search IF NOT EXISTS "
            "FOR (n:CanonicalEntity) ON (n.normalized_search_text)"
        ),
        (
            "CREATE INDEX resolved_search_term_exact IF NOT EXISTS "
            "FOR (n:ResolvedSearchTerm) ON (n.normalized_search_text)"
        ),
        (
            "CREATE INDEX fact_retrieval IF NOT EXISTS "
            "FOR (n:Fact) ON (n.retrieval_eligible)"
        ),
        (
            "CREATE FULLTEXT INDEX canonical_entity_name_search IF NOT EXISTS "
            "FOR (n:CanonicalEntity) ON EACH [n.search_text]"
        ),
        (
            "CREATE FULLTEXT INDEX resolved_search_term_index IF NOT EXISTS "
            "FOR (n:ResolvedSearchTerm) ON EACH [n.search_text]"
        ),
    ]
    for statement in statements:
        session.run(statement).consume()
    session.run("CALL db.awaitIndexes(300)").consume()


def load_nodes_and_static_links(
    session: Any,
    package_directory: Path,
    batch_size: int,
) -> dict[str, int]:
    table_queries = {
        "entities": """
UNWIND $rows AS row
MERGE (n:GraphEntity {entity_id: row.entity_id})
SET n.display_name = row.display_name,
    n.search_text = row.display_name,
    n.normalized_search_text = row.normalized_search_text,
    n.exact_search_eligible = toBoolean(row.exact_search_eligible),
    n.exact_search_status = row.exact_search_status,
    n.exact_search_candidate_count =
        toInteger(row.exact_search_candidate_count),
    n.entity_type = row.entity_type,
    n.resolution_status = row.resolution_status,
    n.retrieval_eligible = toBoolean(row.retrieval_eligible),
    n.anchor_eligible = toBoolean(row.anchor_eligible),
    n.multi_hop_eligible = toBoolean(row.multi_hop_eligible),
    n.source_node_kind = row.source_node_kind,
    n.source_node_id = row.source_node_id,
    n.source_node_ids_json = row.source_node_ids_json,
    n.source_node_kinds_json = row.source_node_kinds_json,
    n.source_member_count = toInteger(row.source_member_count),
    n.merge_scope = row.merge_scope,
    n.context_anchor_id = row.context_anchor_id,
    n.context_direction = row.context_direction,
    n.context_predicate = row.context_predicate,
    n.context_directions_json = row.context_directions_json,
    n.context_predicates_json = row.context_predicates_json,
    n.lifecycle_status = row.lifecycle_status,
    n.identity_confidence = CASE
        WHEN row.identity_confidence = '' THEN null
        ELSE toFloat(row.identity_confidence)
    END,
    n.source_support_count = CASE
        WHEN row.source_support_count = '' THEN null
        ELSE toInteger(row.source_support_count)
    END,
    n.graph_release_id = row.graph_release_id
FOREACH (_ IN CASE WHEN row.entity_kind = 'CANONICAL' THEN [1] ELSE [] END |
    SET n:CanonicalEntity
)
FOREACH (_ IN CASE WHEN row.entity_kind = 'PROVISIONAL' THEN [1] ELSE [] END |
    SET n:ProvisionalEntity
)
RETURN count(*) AS processed
""",
        "facts": """
UNWIND $rows AS row
MERGE (n:Fact {fact_id: row.fact_id})
SET n.predicate = row.predicate,
    n.subject_source_node_id = row.subject_source_node_id,
    n.subject_identity_node_id = row.subject_identity_node_id,
    n.subject_endpoint_resolution_method =
        row.subject_endpoint_resolution_method,
    n.object_source_node_id = row.object_source_node_id,
    n.object_identity_node_id = row.object_identity_node_id,
    n.object_endpoint_resolution_method =
        row.object_endpoint_resolution_method,
    n.assertion_count = toInteger(row.assertion_count),
    n.relation_status = row.relation_status,
    n.endpoint_status = row.endpoint_status,
    n.retrieval_eligible = toBoolean(row.retrieval_eligible),
    n.candidate_retrieval_eligible =
        toBoolean(row.candidate_retrieval_eligible),
    n.terminal_retrieval_eligible =
        toBoolean(row.terminal_retrieval_eligible),
    n.multi_hop_eligible = toBoolean(row.multi_hop_eligible),
    n.evidence_ids_json = row.evidence_ids_json,
    n.source_datasets_json = row.source_datasets_json,
    n.candidate_tiers_json = row.candidate_tiers_json,
    n.source_predicates_json = row.source_predicates_json,
    n.raw_relation_types_json = row.raw_relation_types_json,
    n.relation_qualifiers_json = row.relation_qualifiers_json,
    n.endpoint_projection_status = row.endpoint_projection_status,
    n.endpoint_projection_reference_fact_id =
        row.endpoint_projection_reference_fact_id,
    n.review_model = row.review_model,
    n.review_rationale = row.review_rationale,
    n.review_reason_codes_json = row.review_reason_codes_json,
    n.graph_release_id = row.graph_release_id
WITH n, row
MATCH (subject:GraphEntity {entity_id: row.subject_entity_id})
MATCH (object:GraphEntity {entity_id: row.object_entity_id})
MERGE (n)-[:SUBJECT]->(subject)
MERGE (n)-[:OBJECT]->(object)
RETURN count(*) AS processed
""",
        "evidence": """
UNWIND $rows AS row
MERGE (n:EvidenceSpan {evidence_id: row.evidence_id})
SET n.source_dataset = row.source_dataset,
    n.source_document_id = row.source_document_id,
    n.source_url = row.source_url,
    n.source_text = row.source_text,
    n.evidence_kind = row.evidence_kind,
    n.source_release = row.source_release,
    n.evidence_urls_json = row.evidence_urls_json,
    n.detail_urls_json = row.detail_urls_json,
    n.scopes_json = row.scopes_json,
    n.supported_fact_count = toInteger(row.supported_fact_count),
    n.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "source_records": """
UNWIND $rows AS row
MERGE (n:SourceRecord {source_record_id: row.source_record_id})
SET n.source = row.source,
    n.source_key = row.source_key,
    n.source_release = row.source_release,
    n.source_metadata_json = row.source_metadata_json,
    n.identity_status = row.identity_status,
    n.identity_reason_code = row.identity_reason_code,
    n.preferred_source_node_id = row.preferred_source_node_id,
    n.identity_evidence_urls_json = row.identity_evidence_urls_json,
    n.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "entity_names": """
UNWIND $rows AS row
MERGE (n:EntityName {
    entity_name_id: row.entity_name_id
})
SET n.name = row.name,
    n.search_text = row.search_text,
    n.normalized_search_text = row.normalized_name,
    n.normalized_name = row.normalized_name,
    n.name_type = row.name_type,
    n.target_count = toInteger(row.target_count),
    n.target_resolution_status = row.target_resolution_status,
    n.exact_search_eligible = toBoolean(row.exact_search_eligible),
    n.retrieval_eligible = toBoolean(row.retrieval_eligible),
    n.normalization_policy_version = row.normalization_policy_version,
    n.graph_release_id = row.graph_release_id
REMOVE n:ResolvedSearchTerm
FOREACH (_ IN CASE
    WHEN toBoolean(row.exact_search_eligible) THEN [1]
    ELSE []
END |
    SET n:ResolvedSearchTerm
)
RETURN count(*) AS processed
""",
        "exam_terms": """
UNWIND $rows AS row
MERGE (n:ExamTerm {exam_term_id: row.exam_term_id})
SET n.term = row.term,
    n.search_text = row.search_text,
    n.normalized_term = row.normalized_term,
    n.normalized_search_text = row.normalized_term,
    n.term_variants_json = row.term_variants_json,
    n.resolution_case_ids_json = row.resolution_case_ids_json,
    n.categories_json = row.categories_json,
    n.entity_type_proposals_json = row.entity_type_proposals_json,
    n.problem_count = toInteger(row.problem_count),
    n.problem_ids_json = row.problem_ids_json,
    n.source_link_status = row.source_link_status,
    n.resolution_status = row.resolution_status,
    n.target_count = toInteger(row.target_count),
    n.target_resolution_status = row.target_resolution_status,
    n.exact_search_eligible = toBoolean(row.exact_search_eligible),
    n.retrieval_eligible = toBoolean(row.retrieval_eligible),
    n.fact_retrieval_eligible =
        toBoolean(row.fact_retrieval_eligible),
    n.terminal_fact_retrieval_eligible =
        toBoolean(row.terminal_fact_retrieval_eligible),
    n.graph_release_id = row.graph_release_id
REMOVE n:ResolvedSearchTerm
FOREACH (_ IN CASE
    WHEN toBoolean(row.exact_search_eligible) THEN [1]
    ELSE []
END |
    SET n:ResolvedSearchTerm
)
RETURN count(*) AS processed
""",
        "topics": """
UNWIND $rows AS row
MERGE (n:Topic {topic_id: row.topic_id})
SET n.name = row.name,
    n.status = row.status,
    n.version = row.version,
    n.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "eras": """
UNWIND $rows AS row
MERGE (n:Era {era_id: row.era_id})
SET n.name = row.name,
    n.status = row.status,
    n.version = row.version,
    n.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "fact_evidence_links": """
UNWIND $rows AS row
MATCH (fact:Fact {fact_id: row.fact_id})
MATCH (evidence:EvidenceSpan {evidence_id: row.evidence_id})
MERGE (fact)-[relation:SUPPORTED_BY]->(evidence)
SET relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "evidence_source_links": """
UNWIND $rows AS row
MATCH (evidence:EvidenceSpan {evidence_id: row.evidence_id})
MATCH (source:SourceRecord {source_record_id: row.source_record_id})
MERGE (evidence)-[relation:FROM_SOURCE]->(source)
SET relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "provisional_source_links": """
UNWIND $rows AS row
MATCH (entity:ProvisionalEntity {entity_id: row.entity_id})
MATCH (source:SourceRecord {source_record_id: row.source_record_id})
MERGE (entity)-[relation:DERIVED_FROM]->(source)
SET relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "entity_name_links": """
UNWIND $rows AS row
MATCH (name:EntityName {entity_name_id: row.entity_name_id})
MATCH (entity:CanonicalEntity {entity_id: row.canonical_id})
MERGE (name)-[relation:REFERS_TO]->(entity)
SET relation.match_status = row.match_status,
    relation.method = row.method,
    relation.version = row.version,
    relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "exam_term_links": """
UNWIND $rows AS row
MATCH (term:ExamTerm {exam_term_id: row.exam_term_id})
MATCH (entity:CanonicalEntity {entity_id: row.canonical_id})
MERGE (term)-[relation:REFERS_TO]->(entity)
SET relation.match_status = row.match_status,
    relation.method = row.method,
    relation.version = row.version,
    relation.term_decision_id = row.term_decision_id,
    relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "source_resolution_links": """
UNWIND $rows AS row
MATCH (source:SourceRecord {source_record_id: row.source_record_id})
MATCH (entity:CanonicalEntity {entity_id: row.canonical_id})
MERGE (source)-[relation:RESOLVES_TO]->(entity)
SET relation.match_status = row.match_status,
    relation.method = row.method,
    relation.version = row.version,
    relation.term_decision_id = row.term_decision_id,
    relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "entity_topic_links": """
UNWIND $rows AS row
MATCH (entity:CanonicalEntity {entity_id: row.canonical_id})
MATCH (topic:Topic {topic_id: row.topic_id})
MERGE (entity)-[relation:HAS_TOPIC]->(topic)
SET relation.verification_status = row.verification_status,
    relation.method = row.method,
    relation.evidence_json = row.evidence_json,
    relation.version = row.version,
    relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "entity_era_links": """
UNWIND $rows AS row
MATCH (entity:CanonicalEntity {entity_id: row.canonical_id})
MATCH (era:Era {era_id: row.era_id})
MERGE (entity)-[relation:IN_ERA]->(era)
SET relation.verification_status = row.verification_status,
    relation.method = row.method,
    relation.evidence_json = row.evidence_json,
    relation.version = row.version,
    relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
        "entity_type_links": """
UNWIND $rows AS row
MATCH (entity:GraphEntity {entity_id: row.entity_id})
MERGE (entity_type:EntityType {name: row.entity_type})
SET entity_type.graph_release_id = row.graph_release_id
MERGE (entity)-[relation:HAS_ENTITY_TYPE]->(entity_type)
SET relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
""",
    }

    load_order = [
        "entities",
        "facts",
        "evidence",
        "source_records",
        "entity_names",
        "exam_terms",
        "topics",
        "eras",
        "fact_evidence_links",
        "evidence_source_links",
        "provisional_source_links",
        "entity_name_links",
        "exam_term_links",
        "source_resolution_links",
        "entity_topic_links",
        "entity_era_links",
        "entity_type_links",
    ]
    counts: dict[str, int] = {}
    for table_name in load_order:
        rows = read_csv_rows(package_directory / f"{table_name}.csv")
        counts[table_name] = run_batched_query(
            session,
            table_queries[table_name],
            rows,
            batch_size,
        )
        print(f"loaded {table_name}: {counts[table_name]}")
    return counts


def load_direct_fact_relationships(
    session: Any,
    package_directory: Path,
    batch_size: int,
    relation_type_pattern: str,
) -> int:
    rows_by_predicate: defaultdict[str, list[dict[str, Any]]] = defaultdict(
        list
    )
    for row in read_csv_rows(package_directory / "semantic_relations.csv"):
        predicate = row["predicate"]
        if re.fullmatch(relation_type_pattern, predicate) is None:
            raise ValueError(f"Unsafe Neo4j relationship type: {predicate}")
        enriched_row = dict(row)
        enriched_row["fact_ids"] = json.loads(row["fact_ids_json"])
        enriched_row["relation_statuses"] = json.loads(
            row["relation_statuses_json"]
        )
        enriched_row["evidence_ids"] = json.loads(
            row["evidence_ids_json"]
        )
        enriched_row["source_datasets"] = json.loads(
            row["source_datasets_json"]
        )
        enriched_row["source_predicates"] = json.loads(
            row["source_predicates_json"]
        )
        enriched_row["raw_relation_types"] = json.loads(
            row["raw_relation_types_json"]
        )
        enriched_row["relation_qualifiers"] = json.loads(
            row["relation_qualifiers_json"]
        )
        enriched_row["kinship_kind"] = ""
        if predicate in {"HAS_CHILD", "HAS_FATHER", "HAS_MOTHER"}:
            enriched_row["kinship_kind"] = "UNSPECIFIED"
            if "BIOLOGICAL" in enriched_row["relation_qualifiers"]:
                enriched_row["kinship_kind"] = "BIOLOGICAL"
        enriched_row["review_models"] = json.loads(
            row["review_models_json"]
        )
        rows_by_predicate[predicate].append(enriched_row)

    processed = 0
    for predicate, rows in sorted(rows_by_predicate.items()):
        query = f"""
UNWIND $rows AS row
MATCH (subject:GraphEntity {{entity_id: row.subject_entity_id}})
MATCH (object:GraphEntity {{entity_id: row.object_entity_id}})
MERGE (subject)-[relation:`{predicate}` {{
    semantic_relation_id: row.semantic_relation_id
}}]->(object)
SET relation.representative_fact_id = row.representative_fact_id,
    relation.directionality = row.directionality,
    relation.fact_ids = row.fact_ids,
    relation.fact_count = toInteger(row.fact_count),
    relation.assertion_count = toInteger(row.assertion_count),
    relation.relation_status = row.relation_status,
    relation.relation_statuses = row.relation_statuses,
    relation.endpoint_status = row.endpoint_status,
    relation.retrieval_eligible = toBoolean(row.retrieval_eligible),
    relation.candidate_retrieval_eligible =
        toBoolean(row.candidate_retrieval_eligible),
    relation.terminal_retrieval_eligible =
        toBoolean(row.terminal_retrieval_eligible),
    relation.multi_hop_eligible = toBoolean(row.multi_hop_eligible),
    relation.evidence_ids = row.evidence_ids,
    relation.source_datasets = row.source_datasets,
    relation.source_predicates = row.source_predicates,
    relation.raw_relation_types = row.raw_relation_types,
    relation.relation_qualifiers = row.relation_qualifiers,
    relation.kinship_kind = CASE
        WHEN row.kinship_kind = '' THEN null
        ELSE row.kinship_kind
    END,
    relation.review_models = row.review_models,
    relation.graph_release_id = row.graph_release_id
RETURN count(*) AS processed
"""
        processed += run_batched_query(session, query, rows, batch_size)
    print(f"loaded direct semantic relationships: {processed}")
    return processed


def verify_loaded_graph(
    session: Any,
    expected_manifest: dict[str, Any],
) -> dict[str, Any]:
    statistics = expected_manifest["statistics"]
    query_results = {
        "graph_entity_count": session.run(
            "MATCH (n:GraphEntity) RETURN count(n) AS count"
        ).single()["count"],
        "canonical_entity_count": session.run(
            "MATCH (n:CanonicalEntity) RETURN count(n) AS count"
        ).single()["count"],
        "provisional_entity_count": session.run(
            "MATCH (n:ProvisionalEntity) RETURN count(n) AS count"
        ).single()["count"],
        "fact_count": session.run(
            "MATCH (n:Fact) RETURN count(n) AS count"
        ).single()["count"],
        "evidence_count": session.run(
            "MATCH (n:EvidenceSpan) RETURN count(n) AS count"
        ).single()["count"],
        "direct_semantic_relationship_count": session.run(
            """
MATCH (:GraphEntity)-[r]->(:GraphEntity)
WHERE r.semantic_relation_id IS NOT NULL
RETURN count(r) AS count
"""
        ).single()["count"],
        "default_retrieval_relationship_count": session.run(
            """
MATCH (:GraphEntity)-[r]->(:GraphEntity)
WHERE r.semantic_relation_id IS NOT NULL
  AND r.retrieval_eligible = true
RETURN count(r) AS count
"""
        ).single()["count"],
        "terminal_retrieval_relationship_count": session.run(
            """
MATCH (:GraphEntity)-[r]->(:GraphEntity)
WHERE r.semantic_relation_id IS NOT NULL
  AND r.terminal_retrieval_eligible = true
RETURN count(r) AS count
"""
        ).single()["count"],
        "searchable_provisional_count": session.run(
            """
MATCH (n:ProvisionalEntity)
WHERE n.retrieval_eligible = true
   OR n.anchor_eligible = true
   OR n.multi_hop_eligible = true
RETURN count(n) AS count
"""
        ).single()["count"],
        "unsafe_retrieval_relationship_count": session.run(
            """
MATCH (start:GraphEntity)-[r]->(end:GraphEntity)
WHERE r.semantic_relation_id IS NOT NULL
  AND r.retrieval_eligible = true
  AND (
      start.resolution_status <> 'RESOLVED'
      OR end.resolution_status <> 'RESOLVED'
  )
RETURN count(r) AS count
"""
        ).single()["count"],
        "unsafe_terminal_relationship_count": session.run(
            """
MATCH (start:GraphEntity)-[r]->(end:GraphEntity)
WHERE r.semantic_relation_id IS NOT NULL
  AND r.terminal_retrieval_eligible = true
  AND start.resolution_status <> 'RESOLVED'
  AND end.resolution_status <> 'RESOLVED'
RETURN count(r) AS count
"""
        ).single()["count"],
        "unsafe_provisional_traversal_count": session.run(
            """
MATCH (start:GraphEntity)-[r]->(end:GraphEntity)
WHERE r.semantic_relation_id IS NOT NULL
  AND r.terminal_retrieval_eligible = true
  AND r.multi_hop_eligible = true
  AND (
      start.resolution_status <> 'RESOLVED'
      OR end.resolution_status <> 'RESOLVED'
  )
RETURN count(r) AS count
"""
        ).single()["count"],
        "unsafe_candidate_endpoint_resolution_count": session.run(
            """
MATCH (fact:Fact)-[:SUBJECT]->(subject:GraphEntity)
WHERE fact.subject_endpoint_resolution_method <> ''
  AND NOT subject:CanonicalEntity
RETURN count(fact) AS count
UNION ALL
MATCH (fact:Fact)-[:OBJECT]->(object:GraphEntity)
WHERE fact.object_endpoint_resolution_method <> ''
  AND NOT object:CanonicalEntity
RETURN count(fact) AS count
"""
        ).values(),
        "duplicate_semantic_relation_id_count": session.run(
            """
MATCH (:GraphEntity)-[r]->(:GraphEntity)
WHERE r.semantic_relation_id IS NOT NULL
WITH r.semantic_relation_id AS semantic_relation_id,
     count(r) AS relation_count
WHERE relation_count <> 1
RETURN count(semantic_relation_id) AS count
"""
        ).single()["count"],
        "direct_fact_reference_count": session.run(
            """
MATCH (:GraphEntity)-[r]->(:GraphEntity)
WHERE r.semantic_relation_id IS NOT NULL
RETURN coalesce(sum(r.fact_count), 0) AS count
"""
        ).single()["count"],
        "canonical_without_exact_search_key_count": session.run(
            """
MATCH (n:CanonicalEntity)
WHERE n.normalized_search_text IS NULL
   OR n.normalized_search_text = ''
RETURN count(n) AS count
"""
        ).single()["count"],
        "duplicate_exact_search_canonical_name_count": session.run(
            """
MATCH (n:CanonicalEntity)
WHERE n.exact_search_eligible = true
WITH n.normalized_search_text AS normalized_search_text,
     count(n) AS candidate_count
WHERE candidate_count > 1
RETURN count(normalized_search_text) AS count
"""
        ).single()["count"],
        "ambiguous_retrievable_exam_term_count": session.run(
            """
MATCH (n:ExamTerm)
WHERE n.target_resolution_status = 'AMBIGUOUS'
  AND (
      n.exact_search_eligible = true
      OR n.retrieval_eligible = true
      OR n:ResolvedSearchTerm
  )
RETURN count(n) AS count
"""
        ).single()["count"],
        "ineligible_resolved_search_term_count": session.run(
            """
MATCH (n:ResolvedSearchTerm)
WHERE n.exact_search_eligible <> true
RETURN count(n) AS count
"""
        ).single()["count"],
        "resolved_term_without_exact_search_key_count": session.run(
            """
MATCH (n:ResolvedSearchTerm)
WHERE n.normalized_search_text IS NULL
   OR n.normalized_search_text = ''
RETURN count(n) AS count
"""
        ).single()["count"],
        "provisional_resolved_search_term_count": session.run(
            """
MATCH (n:ProvisionalEntity:ResolvedSearchTerm)
RETURN count(n) AS count
"""
        ).single()["count"],
        "online_exact_search_index_count": session.run(
            """
SHOW INDEXES YIELD name, state
WHERE name IN [
    'canonical_entity_exact_search',
    'resolved_search_term_exact'
]
  AND state = 'ONLINE'
RETURN count(*) AS count
"""
        ).single()["count"],
    }
    query_results["unsafe_candidate_endpoint_resolution_count"] = sum(
        int(row[0])
        for row in query_results[
            "unsafe_candidate_endpoint_resolution_count"
        ]
    )

    expected = {
        "graph_entity_count": statistics["entity_count"],
        "canonical_entity_count": statistics["canonical_entity_count"],
        "provisional_entity_count": statistics["provisional_entity_count"],
        "fact_count": statistics["fact_count"],
        "evidence_count": statistics["evidence_count"],
        "direct_semantic_relationship_count": statistics[
            "direct_semantic_relation_count"
        ],
        "default_retrieval_relationship_count": statistics[
            "default_retrieval_semantic_relation_count"
        ],
        "terminal_retrieval_relationship_count": statistics[
            "terminal_retrieval_semantic_relation_count"
        ],
        "searchable_provisional_count": 0,
        "unsafe_retrieval_relationship_count": 0,
        "unsafe_terminal_relationship_count": 0,
        "unsafe_provisional_traversal_count": 0,
        "unsafe_candidate_endpoint_resolution_count": 0,
        "duplicate_semantic_relation_id_count": 0,
        "direct_fact_reference_count": statistics["fact_count"],
        "canonical_without_exact_search_key_count": 0,
        "duplicate_exact_search_canonical_name_count": 0,
        "ambiguous_retrievable_exam_term_count": 0,
        "ineligible_resolved_search_term_count": 0,
        "resolved_term_without_exact_search_key_count": 0,
        "provisional_resolved_search_term_count": 0,
        "online_exact_search_index_count": 2,
    }
    mismatches = {
        name: {
            "expected": expected[name],
            "actual": query_results[name],
        }
        for name in expected
        if expected[name] != query_results[name]
    }
    return {
        "status": "PASSED" if not mismatches else "FAILED",
        "expected": expected,
        "actual": query_results,
        "mismatches": mismatches,
    }


def reset_generated_graph(
    session: Any,
    graph_release_prefix: str,
    batch_size: int,
) -> dict[str, int]:
    release_ids = session.run(
        """
MATCH (n)
WHERE n.graph_release_id IS NOT NULL
RETURN collect(DISTINCT n.graph_release_id) AS release_ids
"""
    ).single()["release_ids"]
    foreign_release_ids = [
        release_id
        for release_id in release_ids
        if not str(release_id).startswith(graph_release_prefix)
    ]
    unmanaged_node_count = session.run(
        """
MATCH (n)
WHERE n.graph_release_id IS NULL
  AND NOT n:EntityType
RETURN count(n) AS count
"""
    ).single()["count"]
    if foreign_release_ids or unmanaged_node_count:
        raise RuntimeError(
            "Refusing to replace a database containing unmanaged data: "
            f"foreign_release_ids={foreign_release_ids}, "
            f"unmanaged_node_count={unmanaged_node_count}"
        )

    deleted_relationship_count = 0
    while True:
        deleted = session.run(
            """
MATCH ()-[relation]->()
WITH relation LIMIT $batch_size
DELETE relation
RETURN count(relation) AS count
""",
            batch_size=batch_size,
        ).single()["count"]
        deleted_relationship_count += int(deleted)
        if deleted == 0:
            break

    deleted_node_count = 0
    while True:
        deleted = session.run(
            """
MATCH (n)
WITH n LIMIT $batch_size
DELETE n
RETURN count(n) AS count
""",
            batch_size=batch_size,
        ).single()["count"]
        deleted_node_count += int(deleted)
        if deleted == 0:
            break

    return {
        "deleted_relationship_count": deleted_relationship_count,
        "deleted_node_count": deleted_node_count,
    }


def parse_arguments(project_root: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="신규 사실 그래프 패키지를 별도 Neo4j에 적재합니다."
    )
    parser.add_argument(
        "--package-dir",
        default=str(
            project_root
            / "etl"
            / "preprocessing"
            / "neo4j"
            / "output"
            / "fact_graph_release"
        ),
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing generated fact-graph release.",
    )
    return parser.parse_args()


def load_fact_graph(
    project_root: Path,
    package_directory: Path,
    config_path: Path,
    batch_size: int,
    replace: bool,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("batch-size must be greater than zero")

    manifest_path = package_directory / "manifest.json"
    with manifest_path.open("r", encoding="utf-8") as input_file:
        expected_manifest = json.load(input_file)
    with config_path.open("r", encoding="utf-8") as input_file:
        release_config = json.load(input_file)

    connection = load_connection_config(project_root)
    with GraphDatabase.driver(
        connection["uri"],
        auth=(connection["user"], connection["password"]),
    ) as driver:
        driver.verify_connectivity()
        reset_counts = {
            "deleted_relationship_count": 0,
            "deleted_node_count": 0,
        }
        with driver.session() as session:
            existing = session.run(
                "MATCH (n) RETURN count(n) AS count"
            ).single()["count"]
            if existing and replace:
                reset_counts = reset_generated_graph(
                    session,
                    str(release_config["graph_release_prefix"]),
                    batch_size,
                )
                existing = 0
            if existing and not replace:
                release_ids = session.run(
                    """
MATCH (n)
WHERE n.graph_release_id IS NOT NULL
RETURN collect(DISTINCT n.graph_release_id) AS release_ids
"""
                ).single()["release_ids"]
                if release_ids != [expected_manifest["graph_release_id"]]:
                    raise RuntimeError(
                        "Target Neo4j is not empty and contains another release: "
                        f"{release_ids}"
                    )

            create_schema(session)
            loaded_counts = load_nodes_and_static_links(
                session,
                package_directory,
                batch_size,
            )
            direct_relationship_count = load_direct_fact_relationships(
                session,
                package_directory,
                batch_size,
                release_config["relation_type_pattern"],
            )
            verification = verify_loaded_graph(session, expected_manifest)

    load_manifest = {
        "status": (
            "COMPLETED"
            if verification["status"] == "PASSED"
            else "FAILED_VALIDATION"
        ),
        "stage": "FACT_GRAPH_NEO4J_LOAD",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "graph_release_id": expected_manifest["graph_release_id"],
        "target_uri": connection["uri"],
        "replacement_counts": reset_counts,
        "loaded_table_counts": loaded_counts,
        "direct_relationship_count": direct_relationship_count,
        "verification": verification,
    }
    load_manifest_path = package_directory / "neo4j_load_manifest.json"
    with load_manifest_path.open("w", encoding="utf-8") as output_file:
        json.dump(load_manifest, output_file, ensure_ascii=False, indent=2)
    print(json.dumps(load_manifest, ensure_ascii=False, indent=2))
    if verification["status"] != "PASSED":
        raise RuntimeError("Neo4j load validation failed")
    return load_manifest


def main() -> None:
    current_directory = Path(__file__).resolve().parent
    project_root = current_directory.parents[1]
    args = parse_arguments(project_root)
    config_path = (
        project_root
        / "etl"
        / "preprocessing"
        / "neo4j"
        / "config"
        / "fact_graph_release.json"
    )
    load_fact_graph(
        project_root=project_root,
        package_directory=Path(args.package_dir),
        config_path=config_path,
        batch_size=args.batch_size,
        replace=args.replace,
    )


if __name__ == "__main__":
    main()

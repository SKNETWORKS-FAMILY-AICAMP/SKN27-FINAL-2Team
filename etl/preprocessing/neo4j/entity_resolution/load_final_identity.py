from argparse import ArgumentParser
from datetime import datetime, timezone
from json import dumps
from pathlib import Path
from uuid import uuid4
import os
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import load_pipeline_policy


def load_final_identity_tables(
    final_identity_dir: str,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """finalizer가 만든 Neo4j 입력 CSV를 정책 파일명으로 읽는다."""
    final_directory = Path(final_identity_dir)
    output_files = policy["entity_resolution"]["canonical_registry"][
        "output_files"
    ]
    table_names = [
        "canonical_registry",
        "exam_term_nodes",
        "canonical_entity_nodes",
        "source_record_nodes",
        "entity_name_nodes",
        "source_record_resolutions",
        "entity_name_references",
        "exam_term_references",
        "topic_nodes",
        "era_nodes",
        "canonical_topic_relationships",
        "canonical_era_relationships",
        "canonical_classification_review",
        "final_problem_assignments",
        "canonical_acceptance_review_queue",
    ]
    tables: dict[str, pd.DataFrame] = {}
    for table_name in table_names:
        input_path = final_directory / output_files[table_name]
        if not input_path.is_file():
            raise FileNotFoundError(
                f"최종 identity CSV를 찾을 수 없습니다: {input_path}"
            )
        tables[table_name] = pd.read_csv(
            input_path,
            dtype=str,
        ).fillna("")
    return tables


def validate_final_identity_tables(
    tables: dict[str, pd.DataFrame],
) -> list[str]:
    """Neo4j upsert 전에 ID 유일성·참조 무결성·승인 상태를 검사한다."""
    errors: list[str] = []
    unique_contracts = [
        ("canonical_entity_nodes", "canonical_id"),
        ("exam_term_nodes", "exam_term_id"),
        ("source_record_nodes", "source_record_id"),
        ("entity_name_nodes", "entity_name_id"),
        ("topic_nodes", "topic_id"),
        ("era_nodes", "era_id"),
    ]
    for table_name, id_column in unique_contracts:
        table = tables[table_name]
        if id_column not in table.columns:
            errors.append(f"{table_name}: {id_column} 컬럼이 없습니다.")
            continue
        blank_count = int(table[id_column].eq("").sum())
        duplicate_count = int(table[id_column].duplicated().sum())
        if blank_count:
            errors.append(
                f"{table_name}: 빈 {id_column}가 {blank_count}건입니다."
            )
        if duplicate_count:
            errors.append(
                f"{table_name}: 중복 {id_column}가 {duplicate_count}건입니다."
            )

    canonical_ids = set(
        tables["canonical_entity_nodes"].get(
            "canonical_id",
            pd.Series(dtype=str),
        )
    )
    exam_term_ids = set(
        tables["exam_term_nodes"].get(
            "exam_term_id",
            pd.Series(dtype=str),
        )
    )
    source_record_ids = set(
        tables["source_record_nodes"].get(
            "source_record_id",
            pd.Series(dtype=str),
        )
    )
    entity_name_ids = set(
        tables["entity_name_nodes"].get(
            "entity_name_id",
            pd.Series(dtype=str),
        )
    )
    topic_ids = set(
        tables["topic_nodes"].get(
            "topic_id",
            pd.Series(dtype=str),
        )
    )
    era_ids = set(
        tables["era_nodes"].get(
            "era_id",
            pd.Series(dtype=str),
        )
    )
    relationship_contracts = [
        (
            "source_record_resolutions",
            "source_record_id",
            source_record_ids,
            "canonical_id",
            canonical_ids,
        ),
        (
            "entity_name_references",
            "entity_name_id",
            entity_name_ids,
            "canonical_id",
            canonical_ids,
        ),
        (
            "exam_term_references",
            "exam_term_id",
            exam_term_ids,
            "canonical_id",
            canonical_ids,
        ),
    ]
    for (
        table_name,
        left_column,
        left_ids,
        right_column,
        right_ids,
    ) in relationship_contracts:
        table = tables[table_name]
        required_columns = {
            left_column,
            right_column,
            "match_status",
        }
        missing_columns = required_columns.difference(table.columns)
        if missing_columns:
            errors.append(
                f"{table_name}: 필수 컬럼이 없습니다: "
                + ", ".join(sorted(missing_columns))
            )
            continue
        missing_left = set(table[left_column]).difference(left_ids)
        missing_right = set(table[right_column]).difference(right_ids)
        invalid_statuses = set(table["match_status"]).difference(
            {"ACCEPTED"}
        )
        if missing_left:
            errors.append(
                f"{table_name}: 없는 {left_column} 참조 "
                f"{len(missing_left)}건"
            )
        if missing_right:
            errors.append(
                f"{table_name}: 없는 {right_column} 참조 "
                f"{len(missing_right)}건"
            )
        if invalid_statuses:
            errors.append(
                f"{table_name}: ACCEPTED가 아닌 상태 "
                + ", ".join(sorted(invalid_statuses))
            )

    classification_contracts = [
        (
            "canonical_topic_relationships",
            "canonical_id",
            canonical_ids,
            "topic_id",
            topic_ids,
        ),
        (
            "canonical_era_relationships",
            "canonical_id",
            canonical_ids,
            "era_id",
            era_ids,
        ),
    ]
    for (
        table_name,
        left_column,
        left_ids,
        right_column,
        right_ids,
    ) in classification_contracts:
        table = tables[table_name]
        required_columns = {
            left_column,
            right_column,
            "verification_status",
        }
        missing_columns = required_columns.difference(table.columns)
        if missing_columns:
            errors.append(
                f"{table_name}: 필수 컬럼이 없습니다: "
                + ", ".join(sorted(missing_columns))
            )
            continue
        missing_left = set(table[left_column]).difference(left_ids)
        missing_right = set(table[right_column]).difference(right_ids)
        invalid_statuses = set(table["verification_status"]).difference(
            {"VERIFIED"}
        )
        if missing_left:
            errors.append(
                f"{table_name}: 없는 {left_column} 참조 "
                f"{len(missing_left)}건"
            )
        if missing_right:
            errors.append(
                f"{table_name}: 없는 {right_column} 참조 "
                f"{len(missing_right)}건"
            )
        if invalid_statuses:
            errors.append(
                f"{table_name}: VERIFIED가 아닌 상태 "
                + ", ".join(sorted(invalid_statuses))
            )

    source_resolutions = tables["source_record_resolutions"]
    if {
        "source_record_id",
        "canonical_id",
    }.issubset(source_resolutions.columns):
        canonical_counts = source_resolutions.groupby(
            "source_record_id"
        )["canonical_id"].nunique()
        conflicting_sources = canonical_counts[canonical_counts > 1]
        if not conflicting_sources.empty:
            errors.append(
                "하나의 SourceRecord가 여러 CanonicalEntity로 승인됐습니다: "
                f"{len(conflicting_sources)}건"
            )
    return errors


def build_final_identity_load_plan(
    tables: dict[str, pd.DataFrame],
) -> dict[str, object]:
    """검증 결과와 실제 Neo4j 적재 대상 건수를 반환한다."""
    validation_errors = validate_final_identity_tables(tables)
    status = "READY"
    if validation_errors:
        status = "BLOCKED"
    return {
        "status": status,
        "validation_errors": validation_errors,
        "load_counts": {
            "canonical_entities": len(tables["canonical_entity_nodes"]),
            "exam_terms": len(tables["exam_term_nodes"]),
            "source_records": len(tables["source_record_nodes"]),
            "entity_names": len(tables["entity_name_nodes"]),
            "source_resolutions": len(
                tables["source_record_resolutions"]
            ),
            "name_references": len(tables["entity_name_references"]),
            "exam_term_references": len(
                tables["exam_term_references"]
            ),
            "topics": len(tables["topic_nodes"]),
            "eras": len(tables["era_nodes"]),
            "canonical_topic_relationships": len(
                tables["canonical_topic_relationships"]
            ),
            "canonical_era_relationships": len(
                tables["canonical_era_relationships"]
            ),
        },
        "not_loaded_counts": {
            "problem_assignments": len(
                tables["final_problem_assignments"]
            ),
            "single_source_review_queue": len(
                tables["canonical_acceptance_review_queue"]
            ),
            "classification_review_queue": len(
                tables["canonical_classification_review"]
            ),
        },
    }


def load_neo4j_connection_config(
    project_root: Path,
    database: str = "",
) -> dict[str, str]:
    """프로젝트 .env에서 Neo4j 접속 설정을 읽는다."""
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env")
    uri = str(os.getenv("NEO4J_URI") or "")
    if not uri:
        bolt_port = str(os.getenv("NEO4J_BOLT_PORT") or "7687")
        uri = f"bolt://localhost:{bolt_port}"
    user = str(os.getenv("NEO4J_USER") or "neo4j")
    password = str(os.getenv("NEO4J_PASSWORD") or "")
    resolved_database = database or str(
        os.getenv("NEO4J_DATABASE") or ""
    )
    if not password:
        raise ValueError("NEO4J_PASSWORD 환경변수가 필요합니다.")
    return {
        "uri": uri,
        "user": user,
        "password": password,
        "database": resolved_database,
    }


def build_identity_load_queries() -> dict[str, str | list[str]]:
    """최종 identity CSV 계약에 대응하는 idempotent Cypher를 반환한다."""
    constraints = [
        (
            "CREATE CONSTRAINT canonical_entity_id IF NOT EXISTS "
            "FOR (n:CanonicalEntity) REQUIRE n.canonical_id IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT exam_term_id IF NOT EXISTS "
            "FOR (n:ExamTerm) REQUIRE n.exam_term_id IS UNIQUE"
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
            "CREATE CONSTRAINT entity_type_id IF NOT EXISTS "
            "FOR (n:EntityType) REQUIRE n.entity_type_id IS UNIQUE"
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
            "CREATE INDEX entity_name_normalized IF NOT EXISTS "
            "FOR (n:EntityName) ON (n.normalized_name)"
        ),
        (
            "CREATE INDEX canonical_entity_type IF NOT EXISTS "
            "FOR (n:CanonicalEntity) ON (n.entity_type_id)"
        ),
        (
            "CREATE INDEX exam_term_normalized IF NOT EXISTS "
            "FOR (n:ExamTerm) ON (n.normalized_term)"
        ),
    ]
    return {
        "constraints": constraints,
        "exam_term_nodes": """
UNWIND $rows AS row
MERGE (term:ExamTerm {exam_term_id: row.exam_term_id})
SET term.display_name = row.term,
    term.normalized_term = row.normalized_term,
    term.term_variants_json = row.term_variants_json,
    term.resolution_case_ids_json = row.resolution_case_ids_json,
    term.categories_json = row.categories_json,
    term.entity_type_proposals_json = row.entity_type_proposals_json,
    term.problem_count = toInteger(row.problem_count),
    term.problem_ids_json = row.problem_ids_json,
    term.source_link_status = row.source_link_status,
    term.normalization_policy_version = row.normalization_policy_version,
    term.resolution_policy_version = row.resolution_policy_version,
    term.import_scope = $import_scope,
    term.load_run_id = $load_run_id
""",
        "canonical_entity_nodes": """
UNWIND $rows AS row
MERGE (entity:CanonicalEntity {canonical_id: row.canonical_id})
SET entity.display_name = row.display_name,
    entity.entity_type_id = row.entity_type,
    entity.lifecycle_status = row.lifecycle_status,
    entity.identity_confidence = row.identity_confidence,
    entity.source_support_count = toInteger(row.source_support_count),
    entity.resolution_status = 'ACCEPTED',
    entity.registry_version = row.registry_version,
    entity.import_scope = $import_scope,
    entity.load_run_id = $load_run_id
WITH entity, row
OPTIONAL MATCH (entity)-[old:HAS_ENTITY_TYPE]->(old_type:EntityType)
WHERE old.import_scope = $import_scope
  AND old_type.entity_type_id <> row.entity_type
DELETE old
WITH entity, row
MERGE (entity_type:EntityType {entity_type_id: row.entity_type})
ON CREATE SET entity_type.name = row.entity_type
MERGE (entity)-[relation:HAS_ENTITY_TYPE]->(entity_type)
SET relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
        "source_record_nodes": """
UNWIND $rows AS row
MERGE (source:SourceRecord {source_record_id: row.source_record_id})
SET source.source = row.source,
    source.source_key = row.source_key,
    source.source_release = row.source_release,
    source.source_metadata_json = row.source_metadata_json,
    source.record_status = 'ACCEPTED',
    source.import_scope = $import_scope,
    source.load_run_id = $load_run_id
""",
        "entity_name_nodes": """
UNWIND $rows AS row
MERGE (name:EntityName {entity_name_id: row.entity_name_id})
SET name.display_name = row.name,
    name.normalized_name = row.normalized_name,
    name.name_kind = row.name_type,
    name.normalization_version = row.normalization_policy_version,
    name.review_status = 'VERIFIED',
    name.import_scope = $import_scope,
    name.load_run_id = $load_run_id
""",
        "topic_nodes": """
UNWIND $rows AS row
MERGE (topic:Topic {topic_id: row.topic_id})
SET topic.name = row.name,
    topic.status = row.status,
    topic.version = row.version,
    topic.import_scope = $import_scope,
    topic.load_run_id = $load_run_id
""",
        "era_nodes": """
UNWIND $rows AS row
MERGE (era:Era {era_id: row.era_id})
SET era.name = row.name,
    era.status = row.status,
    era.version = row.version,
    era.import_scope = $import_scope,
    era.load_run_id = $load_run_id
""",
        "source_record_resolutions": """
UNWIND $rows AS row
MATCH (source:SourceRecord {source_record_id: row.source_record_id})
MATCH (entity:CanonicalEntity {canonical_id: row.canonical_id})
OPTIONAL MATCH (source)-[old:RESOLVES_TO]->(old_entity:CanonicalEntity)
WHERE old.import_scope = $import_scope
  AND old_entity.canonical_id <> row.canonical_id
DELETE old
WITH source, entity, row
MERGE (source)-[relation:RESOLVES_TO]->(entity)
SET relation.match_status = row.match_status,
    relation.method = row.method,
    relation.version = row.version,
    relation.term_decision_id = row.term_decision_id,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
        "entity_name_references": """
UNWIND $rows AS row
MATCH (name:EntityName {entity_name_id: row.entity_name_id})
MATCH (entity:CanonicalEntity {canonical_id: row.canonical_id})
OPTIONAL MATCH (name)-[old:REFERS_TO]->(old_entity:CanonicalEntity)
WHERE old.import_scope = $import_scope
  AND old_entity.canonical_id <> row.canonical_id
DELETE old
WITH name, entity, row
MERGE (name)-[relation:REFERS_TO]->(entity)
SET relation.match_status = row.match_status,
    relation.method = row.method,
    relation.version = row.version,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
        "exam_term_references": """
UNWIND $rows AS row
MATCH (term:ExamTerm {exam_term_id: row.exam_term_id})
MATCH (entity:CanonicalEntity {canonical_id: row.canonical_id})
MERGE (term)-[relation:REFERS_TO]->(entity)
SET relation.match_status = row.match_status,
    relation.method = row.method,
    relation.version = row.version,
    relation.term_decision_id = row.term_decision_id,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
        "canonical_topic_relationships": """
UNWIND $rows AS row
MATCH (entity:CanonicalEntity {canonical_id: row.canonical_id})
MATCH (topic:Topic {topic_id: row.topic_id})
MERGE (entity)-[relation:HAS_TOPIC]->(topic)
SET relation.verification_status = row.verification_status,
    relation.method = row.method,
    relation.evidence_json = row.evidence_json,
    relation.version = row.version,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
        "canonical_era_relationships": """
UNWIND $rows AS row
MATCH (entity:CanonicalEntity {canonical_id: row.canonical_id})
MATCH (era:Era {era_id: row.era_id})
MERGE (entity)-[relation:IN_ERA]->(era)
SET relation.verification_status = row.verification_status,
    relation.method = row.method,
    relation.evidence_json = row.evidence_json,
    relation.version = row.version,
    relation.import_scope = $import_scope,
    relation.load_run_id = $load_run_id
""",
    }


def execute_load_batches(
    session: object,
    query: str,
    table: pd.DataFrame,
    batch_size: int,
    import_scope: str,
    load_run_id: str,
) -> int:
    """DataFrame을 제한된 크기의 UNWIND transaction으로 upsert한다."""
    if batch_size <= 0:
        raise ValueError("Neo4j batch_size는 1 이상이어야 합니다.")
    records = table.to_dict("records")
    loaded_count = 0
    for start_index in range(0, len(records), batch_size):
        batch = records[start_index : start_index + batch_size]
        result = session.run(
            query,
            rows=batch,
            import_scope=import_scope,
            load_run_id=load_run_id,
        )
        result.consume()
        loaded_count += len(batch)
    return loaded_count


def load_final_identity_to_neo4j(
    final_identity_dir: str,
    policy: dict,
    project_root: str,
    database: str = "",
    batch_size: int | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """검증된 identity CSV만 Neo4j에 idempotent upsert한다."""
    tables = load_final_identity_tables(final_identity_dir, policy)
    plan = build_final_identity_load_plan(tables)
    if plan["validation_errors"]:
        return {
            **plan,
            "stage": "NEO4J_IDENTITY_LOAD",
            "dry_run": dry_run,
        }
    if dry_run:
        return {
            **plan,
            "stage": "NEO4J_IDENTITY_LOAD",
            "dry_run": True,
        }

    from neo4j import GraphDatabase

    full_pipeline_policy = policy["full_pipeline"]
    resolved_batch_size = int(
        batch_size or full_pipeline_policy["neo4j_batch_size"]
    )
    import_scope = str(full_pipeline_policy["neo4j_import_scope"])
    load_run_id = str(uuid4())
    connection = load_neo4j_connection_config(
        Path(project_root),
        database=database,
    )
    queries = build_identity_load_queries()
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
            for constraint in queries["constraints"]:
                session.run(constraint).consume()
            for table_name in [
                "exam_term_nodes",
                "canonical_entity_nodes",
                "source_record_nodes",
                "entity_name_nodes",
                "topic_nodes",
                "era_nodes",
                "source_record_resolutions",
                "entity_name_references",
                "exam_term_references",
                "canonical_topic_relationships",
                "canonical_era_relationships",
            ]:
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
        "stage": "NEO4J_IDENTITY_LOAD",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": full_pipeline_policy["pipeline_version"],
        "resolution_policy_version": policy["policy_version"],
        "import_scope": import_scope,
        "load_run_id": load_run_id,
        "database": connection["database"],
        "loaded_counts": loaded_counts,
        "not_loaded_counts": plan["not_loaded_counts"],
    }
    manifest_path = (
        Path(final_identity_dir)
        / full_pipeline_policy["neo4j_load_manifest_file"]
    )
    manifest_path.write_text(
        dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest["manifest_path"] = str(manifest_path)
    return manifest


if __name__ == "__main__":
    neo4j_root = Path(__file__).resolve().parent.parent
    project_directory = neo4j_root.parents[2]
    parser = ArgumentParser(
        description="검증된 final_identity CSV를 Neo4j에 안전하게 upsert"
    )
    parser.add_argument("final_identity_dir")
    parser.add_argument("--database", default="")
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--policy",
        default=str(neo4j_root / "config" / "resolution_policy.json"),
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    result = load_final_identity_to_neo4j(
        cli_args.final_identity_dir,
        pipeline_policy,
        str(project_directory),
        database=cli_args.database,
        batch_size=cli_args.batch_size,
        dry_run=cli_args.dry_run,
    )
    print(dumps(result, ensure_ascii=False, indent=2))
    if result["status"] not in {"READY", "COMPLETED"}:
        raise SystemExit(1)

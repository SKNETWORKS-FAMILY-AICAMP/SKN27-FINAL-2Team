from pathlib import Path
import os
import time

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import TransientError


def load_neo4j_config(project_root):
    load_dotenv(project_root / ".env")

    required_keys = ["NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_BOLT_PORT"]
    missing_keys = [key for key in required_keys if not os.getenv(key)]

    if missing_keys:
        missing_text = ", ".join(missing_keys)
        raise ValueError(f"Missing required environment variables: {missing_text}")

    return {
        "uri": f"bolt://localhost:{os.getenv('NEO4J_BOLT_PORT')}",
        "user": os.getenv("NEO4J_USER"),
        "password": os.getenv("NEO4J_PASSWORD"),
    }


def split_cypher_statements(cypher_text):
    statements = []
    statement_lines = []

    for line in cypher_text.splitlines():
        statement_lines.append(line)

        if line.rstrip().endswith(";"):
            statement = "\n".join(statement_lines).strip()
            statements.append(statement.rstrip(";").strip())
            statement_lines = []

    remaining_statement = "\n".join(statement_lines).strip()

    if remaining_statement:
        statements.append(remaining_statement)

    return [statement for statement in statements if statement]


def build_default_schema_file_names():
    return [
        "history_graph_constraints.cypher",
        "history_graph_import_nodes.cypher",
        "history_graph_import_relations.cypher",
        "history_graph_verify.cypher",
    ]


def build_default_schema_file_text():
    return ",".join(build_default_schema_file_names())


def build_schema_file_names(schema_file_text):
    return [
        schema_file.strip()
        for schema_file in schema_file_text.split(",")
        if schema_file.strip() and schema_file.strip() != "history_graph_reset.cypher"
    ]


def build_optional_import_csv_paths():
    return {
        "relations/event_about_region.csv",
        "relations/event_about_economic_domain.csv",
        "relations/person_has_evidence_url.csv",
        "relations/term_mentions_person.csv",
    }


def extract_import_csv_path(statement):
    marker = "file:///"
    start_index = statement.find(marker)

    if start_index == -1:
        return ""

    csv_start_index = start_index + len(marker)
    quote_index = statement.find("'", csv_start_index)

    if quote_index == -1:
        return ""

    return statement[csv_start_index:quote_index]


def should_skip_optional_import(statement, import_dir):
    import_csv_path = extract_import_csv_path(statement)

    if import_csv_path in build_optional_import_csv_paths():
        local_import_path = import_dir / import_csv_path

        if not local_import_path.exists():
            return True

    return False


def load_schema_paths(schema_dir):
    schema_file_text = os.getenv("NEO4J_SCHEMA_FILES")

    if not schema_file_text:
        schema_file_text = build_default_schema_file_text()

    schema_files = build_schema_file_names(schema_file_text)
    schema_paths = []

    for schema_name in schema_files:
        schema_paths.append(schema_dir / schema_name)

    if not schema_paths:
        raise ValueError("No schema files configured")

    missing_paths = [str(path) for path in schema_paths if not path.exists()]

    if missing_paths:
        missing_text = ", ".join(missing_paths)
        raise FileNotFoundError(f"Missing schema files: {missing_text}")

    return schema_paths


def load_statement_max_retries():
    raw_max_retries = os.getenv("NEO4J_STATEMENT_MAX_RETRIES")

    if not raw_max_retries:
        return 5

    max_retries = int(raw_max_retries)

    if max_retries < 0:
        raise ValueError("NEO4J_STATEMENT_MAX_RETRIES must be 0 or greater")

    return max_retries


def run_statement(session, statement, import_dir):
    if should_skip_optional_import(statement, import_dir):
        return {
            "executed": False,
            "skipped": True,
            "records": [],
        }

    max_retries = load_statement_max_retries()

    for attempt_index in range(max_retries + 1):
        try:
            result = session.run(statement)
            records = [record.data() for record in result]
            result.consume()
            break
        except TransientError as transient_error:
            if attempt_index >= max_retries:
                raise

            wait_seconds = 2 ** attempt_index
            print(
                f"transient error ({transient_error.code}), "
                f"retry {attempt_index + 1}/{max_retries} "
                f"after {wait_seconds}s"
            )
            time.sleep(wait_seconds)

    return {
        "executed": True,
        "skipped": False,
        "records": records,
    }


def load_reset_batch_size():
    raw_batch_size = os.getenv("NEO4J_RESET_BATCH_SIZE")

    if not raw_batch_size:
        return 10000

    batch_size = int(raw_batch_size)

    if batch_size <= 0:
        raise ValueError("NEO4J_RESET_BATCH_SIZE must be greater than 0")

    return batch_size


def run_batched_delete(session, statement, batch_size, phase_name):
    total_deleted = 0
    batch_count = 0

    while True:
        record = session.run(statement, batch_size=batch_size).single()
        deleted_count = record["deleted_count"]

        if deleted_count == 0:
            break

        total_deleted += deleted_count
        batch_count += 1
        print(
            f"reset {phase_name}: deleted {deleted_count} "
            f"(total {total_deleted})"
        )

    return {
        "phase": phase_name,
        "deleted": total_deleted,
        "batches": batch_count,
        "batch_size": batch_size,
    }


def run_reset_schema(driver):
    batch_size = load_reset_batch_size()
    relationship_delete_statement = """
MATCH ()-[r]->()
WITH r LIMIT $batch_size
DELETE r
RETURN count(*) AS deleted_count
"""
    node_delete_statement = """
MATCH (n)
WITH n LIMIT $batch_size
DELETE n
RETURN count(*) AS deleted_count
"""

    with driver.session() as session:
        relationship_result = run_batched_delete(
            session,
            relationship_delete_statement,
            batch_size,
            "relationships",
        )
        node_result = run_batched_delete(
            session,
            node_delete_statement,
            batch_size,
            "nodes",
        )

    return {
        "schema": "internal_graph_reset",
        "statements": 2,
        "executed_statements": 2,
        "skipped_statements": 0,
        "returned_results": [
            {
                "statement_index": 1,
                "records": [relationship_result],
            },
            {
                "statement_index": 2,
                "records": [node_result],
            },
        ],
    }


def run_schema(driver, schema_path, import_dir):
    cypher_text = schema_path.read_text(encoding="utf-8-sig")
    statements = split_cypher_statements(cypher_text)
    executed_statements = 0
    skipped_statements = 0
    returned_results = []

    with driver.session() as session:
        for statement_index, statement in enumerate(statements, start=1):
            try:
                statement_result = run_statement(session, statement, import_dir)
            except Exception:
                statement_preview = " ".join(statement.split())[:200]
                print(f"Failed schema file: {schema_path.name}")
                print(f"Failed statement index: {statement_index}/{len(statements)}")
                print(f"Failed statement: {statement_preview}")
                raise

            if statement_result["skipped"]:
                skipped_statements += 1

            if statement_result["executed"]:
                executed_statements += 1

            if statement_result["records"]:
                returned_results.append(
                    {
                        "statement_index": statement_index,
                        "records": statement_result["records"],
                    }
                )

    return {
        "schema": schema_path.name,
        "statements": len(statements),
        "executed_statements": executed_statements,
        "skipped_statements": skipped_statements,
        "returned_results": returned_results,
    }


def count_graph_totals(driver):
    with driver.session() as session:
        node_count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
        relationship_count = session.run(
            "MATCH ()-[r]->() RETURN count(r) AS count"
        ).single()["count"]

    return {
        "nodes": node_count,
        "relationships": relationship_count,
    }


def print_schema_result(result):
    print(f"Schema: {result['schema']}")
    print(f"Total statements: {result['statements']}")
    print(f"Executed statements: {result['executed_statements']}")
    print(f"Skipped optional statements: {result['skipped_statements']}")

    for returned_result in result["returned_results"]:
        print(
            f"Returned rows from {result['schema']} "
            f"statement #{returned_result['statement_index']}:"
        )

        for record in returned_result["records"]:
            print(record)


def main():
    current_dir = Path(__file__).resolve().parent
    project_root = current_dir.parents[1]
    schema_dir = current_dir / "schema"
    import_dir = current_dir / "neo4j_import"

    config = load_neo4j_config(project_root)
    schema_paths = load_schema_paths(schema_dir)

    with GraphDatabase.driver(
        config["uri"],
        auth=(config["user"], config["password"]),
    ) as driver:
        reset_result = run_reset_schema(driver)
        schema_results = [
            run_schema(driver, schema_path, import_dir)
            for schema_path in schema_paths
        ]
        results = [reset_result, *schema_results]
        graph_totals = count_graph_totals(driver)

    for result in results:
        print_schema_result(result)

    total_statements = sum(result["statements"] for result in results)
    total_executed_statements = sum(result["executed_statements"] for result in results)
    total_skipped_statements = sum(result["skipped_statements"] for result in results)

    print(f"Total statements: {total_statements}")
    print(f"Total executed statements: {total_executed_statements}")
    print(f"Total skipped optional statements: {total_skipped_statements}")
    print(f"Final node count: {graph_totals['nodes']}")
    print(f"Final relationship count: {graph_totals['relationships']}")


if __name__ == "__main__":
    main()

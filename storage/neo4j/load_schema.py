from pathlib import Path
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase


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
        "history_graph_reset.cypher",
        "history_graph_constraints.cypher",
        "history_graph_import_nodes.cypher",
        "history_graph_import_relations.cypher",
        "history_graph_verify.cypher",
    ]


def build_default_schema_file_text():
    return ",".join(build_default_schema_file_names())


def prepend_reset_schema_file(schema_files):
    if "history_graph_reset.cypher" not in schema_files:
        return ["history_graph_reset.cypher", *schema_files]

    return schema_files


def build_schema_file_names(schema_file_text):
    schema_files = [
        schema_file.strip()
        for schema_file in schema_file_text.split(",")
        if schema_file.strip()
    ]

    return prepend_reset_schema_file(schema_files)


def build_optional_import_csv_paths():
    return {
        "relations/event_about_region.csv",
        "relations/event_about_economic_domain.csv",
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


def run_statement(session, statement, import_dir):
    if should_skip_optional_import(statement, import_dir):
        return {
            "executed": False,
            "skipped": True,
            "records": [],
        }

    result = session.run(statement)
    records = [record.data() for record in result]
    result.consume()

    return {
        "executed": True,
        "skipped": False,
        "records": records,
    }


def run_schema(driver, schema_path, import_dir):
    cypher_text = schema_path.read_text(encoding="utf-8")
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
        results = [
            run_schema(driver, schema_path, import_dir)
            for schema_path in schema_paths
        ]
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

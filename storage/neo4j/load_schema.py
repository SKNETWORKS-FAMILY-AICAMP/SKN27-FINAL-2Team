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


def load_schema_paths(schema_dir):
    schema_file_text = os.getenv("NEO4J_SCHEMA_FILES", "init.cypher,event.cypher")
    schema_paths = []

    for schema_file in schema_file_text.split(","):
        schema_name = schema_file.strip()

        if schema_name:
            schema_paths.append(schema_dir / schema_name)

    if not schema_paths:
        raise ValueError("No schema files configured")

    missing_paths = [str(path) for path in schema_paths if not path.exists()]

    if missing_paths:
        missing_text = ", ".join(missing_paths)
        raise FileNotFoundError(f"Missing schema files: {missing_text}")

    return schema_paths


def run_schema(driver, schema_path):
    cypher_text = schema_path.read_text(encoding="utf-8")
    statements = split_cypher_statements(cypher_text)

    with driver.session() as session:
        for statement in statements:
            session.run(statement).consume()

        node_count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
        relationship_count = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]

    return {
        "schema": schema_path.name,
        "statements": len(statements),
        "nodes": node_count,
        "relationships": relationship_count,
    }


def main():
    current_dir = Path(__file__).resolve().parent
    project_root = current_dir.parents[1]
    schema_dir = current_dir / "schema"

    config = load_neo4j_config(project_root)
    schema_paths = load_schema_paths(schema_dir)

    with GraphDatabase.driver(
        config["uri"],
        auth=(config["user"], config["password"]),
    ) as driver:
        results = [run_schema(driver, schema_path) for schema_path in schema_paths]

    for result in results:
        print(f"Schema: {result['schema']}")
        print(f"Executed statements: {result['statements']}")
        print(f"Node count: {result['nodes']}")
        print(f"Relationship count: {result['relationships']}")

    total_statements = sum(result["statements"] for result in results)
    final_result = results[-1]

    print(f"Total executed statements: {total_statements}")
    print(f"Final node count: {final_result['nodes']}")
    print(f"Final relationship count: {final_result['relationships']}")


if __name__ == "__main__":
    main()

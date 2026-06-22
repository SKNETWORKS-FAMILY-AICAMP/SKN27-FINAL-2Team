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


def run_schema(driver, schema_path):
    cypher_text = schema_path.read_text(encoding="utf-8")
    statements = split_cypher_statements(cypher_text)

    with driver.session() as session:
        for statement in statements:
            session.run(statement).consume()

        node_count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
        relationship_count = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]

    return {
        "statements": len(statements),
        "nodes": node_count,
        "relationships": relationship_count,
    }


def main():
    current_dir = Path(__file__).resolve().parent
    project_root = current_dir.parents[1]
    schema_path = current_dir / "schema" / "init.cypher"

    config = load_neo4j_config(project_root)

    with GraphDatabase.driver(
        config["uri"],
        auth=(config["user"], config["password"]),
    ) as driver:
        result = run_schema(driver, schema_path)

    print(f"Executed statements: {result['statements']}")
    print(f"Node count: {result['nodes']}")
    print(f"Relationship count: {result['relationships']}")


if __name__ == "__main__":
    main()

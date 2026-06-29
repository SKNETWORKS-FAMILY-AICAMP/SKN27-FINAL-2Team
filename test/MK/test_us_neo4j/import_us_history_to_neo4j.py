"""
Import an RDF/Turtle(.ttl) file into Neo4j using the n10s plugin.

Assumed Docker volume mapping:
  ./import/history:/import

So the Turtle file should be located on the host at:
  test/MK/import/history/us_history.ttl

Inside the Neo4j container, n10s sees it as:
  file:///us_history.ttl

Install dependency:
  pip install neo4j

Run:
  python import_us_history_to_neo4j.py

Optional reset and re-import:
  python import_us_history_to_neo4j.py --reset
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import os
from neo4j import GraphDatabase
from neo4j.exceptions import ClientError, ServiceUnavailable, AuthError


DEFAULT_URI = "bolt://localhost:7688"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD")
DEFAULT_TTL_URI = "file:///us_history.ttl"


CYPHER_CONSTRAINT = """
CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS
FOR (r:Resource)
REQUIRE r.uri IS UNIQUE
"""

CYPHER_GRAPH_CONFIG = """
CALL n10s.graphconfig.init({
  handleVocabUris: "SHORTEN",
  handleMultival: "ARRAY"
})
"""

CYPHER_IMPORT = """
CALL n10s.rdf.import.fetch($ttl_uri, "Turtle")
YIELD terminationStatus, triplesLoaded, triplesParsed, namespaces, extraInfo
RETURN terminationStatus, triplesLoaded, triplesParsed, namespaces, extraInfo
"""

CYPHER_NODE_COUNT = """
MATCH (n)
RETURN count(n) AS node_count
"""

CYPHER_REL_COUNT = """
MATCH ()-[r]->()
RETURN count(r) AS relationship_count
"""

CYPHER_LABEL_COUNTS = """
MATCH (n)
RETURN labels(n) AS labels, count(*) AS count
ORDER BY count DESC
LIMIT 20
"""

CYPHER_REL_TYPE_COUNTS = """
MATCH ()-[r]->()
RETURN type(r) AS relationship_type, count(*) AS count
ORDER BY count DESC
LIMIT 20
"""

CYPHER_US_HISTORY_NEIGHBORS = """
MATCH (center:Resource)-[r]-(neighbor:Resource)
WHERE center.uri CONTAINS "History_of_the_United_States"
RETURN
  center.uri AS center_uri,
  type(r) AS relationship_type,
  neighbor.uri AS neighbor_uri
LIMIT 30
"""

CYPHER_SEARCH_RESOURCE = """
MATCH (n:Resource)
WHERE toLower(n.uri) CONTAINS toLower($keyword)
RETURN n.uri AS uri, labels(n) AS labels
LIMIT 30
"""

CYPHER_RESET = """
MATCH (n)
DETACH DELETE n
"""


def print_rows(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("No rows")
        return

    for row in rows:
        print(row)


def run_query(session, cypher: str, **params: Any) -> list[dict[str, Any]]:
    result = session.run(cypher, **params)
    return [dict(record) for record in result]


def wait_for_neo4j(driver, retries: int = 30, delay: float = 1.0) -> None:
    last_error: Exception | None = None

    for _ in range(retries):
        try:
            driver.verify_connectivity()
            return
        except (ServiceUnavailable, AuthError) as exc:
            last_error = exc
            time.sleep(delay)

    raise RuntimeError(f"Neo4j connection failed: {last_error}")


def ensure_n10s_ready(session) -> None:
    print("Creating :Resource(uri) unique constraint...")
    run_query(session, CYPHER_CONSTRAINT)

    print("Initializing n10s graph config...")
    try:
        run_query(session, CYPHER_GRAPH_CONFIG)
    except ClientError as exc:
        message = str(exc)
        # n10s allows only one graph config. If it already exists, continuing is fine.
        if "GraphConfig" in message or "already" in message.lower():
            print("n10s graph config already exists. Continue.")
        elif "n10s.graphconfig.init" in message or "no procedure" in message.lower():
            raise RuntimeError(
                "n10s plugin is not available. Check Docker environment: "
                'NEO4J_PLUGINS=["apoc","n10s"]'
            ) from exc
        else:
            raise


def import_turtle(session, ttl_uri: str) -> None:
    print(f"Importing Turtle file from Neo4j server path: {ttl_uri}")
    rows = run_query(session, CYPHER_IMPORT, ttl_uri=ttl_uri)
    print_rows("Import result", rows)


def print_basic_stats(session) -> None:
    print_rows("Node count", run_query(session, CYPHER_NODE_COUNT))
    print_rows("Relationship count", run_query(session, CYPHER_REL_COUNT))
    print_rows("Labels", run_query(session, CYPHER_LABEL_COUNTS))
    print_rows("Relationship types", run_query(session, CYPHER_REL_TYPE_COUNTS))
    print_rows("History_of_the_United_States neighbors", run_query(session, CYPHER_US_HISTORY_NEIGHBORS))


def search_resource(session, keyword: str) -> None:
    rows = run_query(session, CYPHER_SEARCH_RESOURCE, keyword=keyword)
    print_rows(f"Search Resource by keyword: {keyword}", rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import RDF/Turtle data into Neo4j with n10s.")
    parser.add_argument("--uri", default=DEFAULT_URI, help=f"Neo4j Bolt URI. Default: {DEFAULT_URI}")
    parser.add_argument("--user", default=DEFAULT_USER, help=f"Neo4j user. Default: {DEFAULT_USER}")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Neo4j password.")
    parser.add_argument("--ttl-uri", default=DEFAULT_TTL_URI, help=f"Turtle file URI from Neo4j server view. Default: {DEFAULT_TTL_URI}")
    parser.add_argument("--reset", action="store_true", help="Delete all nodes and relationships before import. Use only for test DB.")
    parser.add_argument("--search", default="", help="Search imported Resource uri by keyword after import.")
    args = parser.parse_args()

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))

    try:
        wait_for_neo4j(driver)
        with driver.session() as session:
            if args.reset:
                answer = input("This will delete every node and relationship in the connected Neo4j DB. Type YES to continue: ")
                if answer != "YES":
                    print("Reset cancelled.")
                    return 1
                print("Deleting all nodes and relationships...")
                run_query(session, CYPHER_RESET)

            ensure_n10s_ready(session)
            import_turtle(session, args.ttl_uri)
            print_basic_stats(session)

            if args.search:
                search_resource(session, args.search)

    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

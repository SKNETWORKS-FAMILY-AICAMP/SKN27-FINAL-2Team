from __future__ import annotations

import os
import time

from neo4j import GraphDatabase

from storage.postgresql.connection import connect_db


def wait_for_postgresql(timeout_seconds: int, retry_interval_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            connection = connect_db()
            connection.close()
            return
        except Exception as exc:
            last_error = type(exc).__name__
        time.sleep(retry_interval_seconds)
    raise TimeoutError(f"PostgreSQL did not become ready: {last_error}")


def wait_for_fact_neo4j(timeout_seconds: int, retry_interval_seconds: int) -> None:
    uri = os.environ["FACT_NEO4J_URI"]
    user = os.environ["FACT_NEO4J_USER"]
    password = os.environ["FACT_NEO4J_PASSWORD"]
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with GraphDatabase.driver(
                uri,
                auth=(user, password),
                connection_timeout=retry_interval_seconds,
            ) as driver:
                driver.verify_connectivity()
            return
        except Exception as exc:
            last_error = type(exc).__name__
        time.sleep(retry_interval_seconds)
    raise TimeoutError(f"Fact Neo4j did not become ready: {last_error}")


def main() -> int:
    timeout_seconds = int(os.environ["FACT_BATCH_DEPENDENCY_TIMEOUT_SECONDS"])
    retry_interval_seconds = int(
        os.environ["FACT_BATCH_DEPENDENCY_RETRY_INTERVAL_SECONDS"]
    )
    if timeout_seconds <= 0:
        raise ValueError("FACT_BATCH_DEPENDENCY_TIMEOUT_SECONDS must be positive")
    if retry_interval_seconds <= 0:
        raise ValueError(
            "FACT_BATCH_DEPENDENCY_RETRY_INTERVAL_SECONDS must be positive"
        )
    wait_for_postgresql(timeout_seconds, retry_interval_seconds)
    wait_for_fact_neo4j(timeout_seconds, retry_interval_seconds)
    print("Fact batch dependencies are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

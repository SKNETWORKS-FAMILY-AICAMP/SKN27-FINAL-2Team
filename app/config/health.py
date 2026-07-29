from __future__ import annotations

import os

from django.db import connection
from django.http import HttpRequest, JsonResponse
from neo4j import GraphDatabase


def health_check(request: HttpRequest) -> JsonResponse:
    service_status = {
        "status": "ok",
        "postgresql": "ok",
        "neo4j": "ok",
    }

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        service_status["status"] = "unhealthy"
        service_status["postgresql"] = "unavailable"
        service_status["neo4j"] = "not_checked"
        return JsonResponse(service_status, status=503)

    neo4j_uri = os.getenv("NEO4J_URI", "").strip()
    neo4j_user = os.getenv("NEO4J_USER", "").strip()
    neo4j_password = os.getenv("NEO4J_PASSWORD", "").strip()
    neo4j_timeout_seconds = float(
        os.getenv("NEO4J_CONNECT_TIMEOUT_SECONDS", "3")
    )

    if not neo4j_uri or not neo4j_user or not neo4j_password:
        service_status["status"] = "unhealthy"
        service_status["neo4j"] = "configuration_missing"
        return JsonResponse(service_status, status=503)

    try:
        with GraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password),
            connection_timeout=neo4j_timeout_seconds,
        ) as driver:
            driver.verify_connectivity()
    except Exception:
        service_status["status"] = "unhealthy"
        service_status["neo4j"] = "unavailable"
        return JsonResponse(service_status, status=503)

    return JsonResponse(service_status)

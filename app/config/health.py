from __future__ import annotations

import os

from django.conf import settings
from django.core.cache import caches
from django.db import connection
from django.http import HttpRequest, JsonResponse
from neo4j import GraphDatabase


def health_response(service_status: dict, http_status: int) -> JsonResponse:
    """상태 코드는 유지하되, 운영에서는 내부 구성 힌트를 노출하지 않는다.

    postgresql/neo4j 하위 상태값(schema_incomplete, configuration_missing 등)은
    내부 구성을 드러내므로 DEBUG 일 때만 상세를 담고, 운영에서는 최상위
    status 만 반환한다.
    """
    if settings.DEBUG:
        return JsonResponse(service_status, status=http_status)
    return JsonResponse({"status": service_status["status"]}, status=http_status)


def liveness(request: HttpRequest) -> JsonResponse:
    """프로세스 생존만 확인하는 가벼운 엔드포인트.

    DB·Neo4j 를 건드리지 않으므로 외부에서 자주 호출돼도 부하가 없다.
    로드밸런서의 잦은 헬스체크는 이 경로를 쓰게 한다.
    """
    return JsonResponse({"status": "ok"})


def _compute_readiness() -> tuple[dict, int]:
    """DB·Neo4j 를 실제로 검사해 준비 상태를 판정한다."""
    service_status = {
        "status": "ok",
        "postgresql": "ok",
        "neo4j": "ok",
    }

    required_tables = {
        table_name.strip()
        for table_name in os.getenv("POSTGRES_REQUIRED_TABLES", "").split(",")
        if table_name.strip()
    }
    if not required_tables:
        service_status["status"] = "unhealthy"
        service_status["postgresql"] = "configuration_missing"
        service_status["neo4j"] = "not_checked"
        return service_status, 503

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
            existing_tables = set(
                connection.introspection.table_names(cursor)
            )
    except Exception:
        service_status["status"] = "unhealthy"
        service_status["postgresql"] = "unavailable"
        service_status["neo4j"] = "not_checked"
        return service_status, 503

    if required_tables - existing_tables:
        service_status["status"] = "unhealthy"
        service_status["postgresql"] = "schema_incomplete"
        service_status["neo4j"] = "not_checked"
        return service_status, 503

    neo4j_uri = os.getenv("NEO4J_URI", "").strip()
    neo4j_user = os.getenv("NEO4J_USER", "").strip()
    neo4j_password = os.getenv("NEO4J_PASSWORD", "").strip()
    neo4j_database = (
        os.getenv("NEO4J_DATABASE", "neo4j").strip() or "neo4j"
    )
    neo4j_timeout_seconds = float(
        os.getenv("NEO4J_CONNECT_TIMEOUT_SECONDS", "3")
    )

    if not neo4j_uri or not neo4j_user or not neo4j_password:
        service_status["status"] = "unhealthy"
        service_status["neo4j"] = "configuration_missing"
        return service_status, 503

    try:
        with GraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password),
            connection_timeout=neo4j_timeout_seconds,
        ) as driver:
            driver.verify_connectivity()
            with driver.session(database=neo4j_database) as session:
                session.run("RETURN 1").consume()
    except Exception:
        service_status["status"] = "unhealthy"
        service_status["neo4j"] = "unavailable"
        return service_status, 503

    return service_status, 200


def health_check(request: HttpRequest) -> JsonResponse:
    """준비 상태(readiness) 검사. 결과를 짧게 캐시해 외부 호출이 잦아도
    DB·Neo4j 를 매번 두드리지 않게 한다."""
    cache_seconds = int(os.getenv("HEALTH_READINESS_CACHE_SECONDS", "10"))
    cache_key = "health-readiness"
    health_cache = caches["healthcheck"]
    cached = health_cache.get(cache_key) if cache_seconds > 0 else None
    if cached is None:
        service_status, http_status = _compute_readiness()
        if cache_seconds > 0:
            health_cache.set(
                cache_key,
                (service_status, http_status),
                cache_seconds,
            )
    else:
        service_status, http_status = cached

    return health_response(service_status, http_status)

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.http import HttpRequest
from django.test import TestCase, override_settings

from config.health import health_check


@override_settings(DEBUG=True)
class HealthCheckTest(TestCase):
    def setUp(self) -> None:
        cache.clear()

    @override_settings(ALLOWED_HOSTS=["health.example.com"])
    @patch.dict(
        "os.environ",
        {
            "NEO4J_URI": "bolt://neo4j.example.internal:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "test-password",
            "NEO4J_CONNECT_TIMEOUT_SECONDS": "1",
            "POSTGRES_REQUIRED_TABLES": "user_accounts",
        },
        clear=False,
    )
    @patch("config.health.GraphDatabase.driver")
    @patch("config.health.connection")
    def test_health_url_accepts_configured_healthcheck_host(
        self,
        connection_mock: MagicMock,
        driver_mock: MagicMock,
    ) -> None:
        connection_mock.introspection.table_names.return_value = [
            "user_accounts",
        ]

        response = self.client.get(
            "/health/",
            headers={"host": "health.example.com"},
        )

        self.assertEqual(response.status_code, 200)
        (
            driver_mock.return_value.__enter__
            .return_value.verify_connectivity.assert_called_once_with()
        )

    @patch.dict(
        "os.environ",
        {
            "NEO4J_URI": "bolt://neo4j.example.internal:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "test-password",
            "NEO4J_CONNECT_TIMEOUT_SECONDS": "1",
            "POSTGRES_REQUIRED_TABLES": "user_accounts,questions",
        },
        clear=False,
    )
    @patch("config.health.GraphDatabase.driver")
    @patch("config.health.connection")
    def test_health_check_returns_ok_when_dependencies_are_available(
        self,
        connection_mock: MagicMock,
        driver_mock: MagicMock,
    ) -> None:
        connection_mock.introspection.table_names.return_value = [
            "user_accounts",
            "questions",
        ]

        response = health_check(HttpRequest())

        self.assertEqual(response.status_code, 200)
        connection_mock.cursor.assert_called_once_with()
        (
            driver_mock.return_value.__enter__
            .return_value.verify_connectivity.assert_called_once_with()
        )

    @patch("config.health.connection")
    def test_health_check_stops_when_postgresql_is_unavailable(
        self,
        connection_mock: MagicMock,
    ) -> None:
        with patch.dict(
            "os.environ",
            {"POSTGRES_REQUIRED_TABLES": "user_accounts"},
            clear=False,
        ):
            connection_mock.cursor.side_effect = RuntimeError(
                "database unavailable"
            )

            response = health_check(HttpRequest())

        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"postgresql": "unavailable"', response.content)

    @patch.dict(
        "os.environ",
        {
            "NEO4J_URI": "bolt://neo4j.example.internal:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "test-password",
            "NEO4J_CONNECT_TIMEOUT_SECONDS": "1",
            "POSTGRES_REQUIRED_TABLES": "user_accounts,questions",
        },
        clear=False,
    )
    @patch("config.health.GraphDatabase.driver")
    @patch("config.health.connection")
    def test_health_check_returns_unavailable_when_neo4j_connection_fails(
        self,
        connection_mock: MagicMock,
        driver_mock: MagicMock,
    ) -> None:
        connection_mock.introspection.table_names.return_value = [
            "user_accounts",
            "questions",
        ]
        driver_mock.return_value.__enter__.return_value.verify_connectivity.side_effect = (
            RuntimeError("neo4j unavailable")
        )

        response = health_check(HttpRequest())

        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"neo4j": "unavailable"', response.content)
        connection_mock.cursor.assert_called_once_with()

    @patch.dict(
        "os.environ",
        {"POSTGRES_REQUIRED_TABLES": "user_accounts,questions"},
        clear=False,
    )
    @patch("config.health.connection")
    def test_health_check_rejects_incomplete_postgresql_schema(
        self,
        connection_mock: MagicMock,
    ) -> None:
        connection_mock.introspection.table_names.return_value = [
            "user_accounts",
        ]

        response = health_check(HttpRequest())

        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"postgresql": "schema_incomplete"', response.content)

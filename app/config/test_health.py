from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.http import HttpRequest
from django.test import SimpleTestCase

from config.health import health_check


class HealthCheckTest(SimpleTestCase):
    @patch.dict(
        "os.environ",
        {
            "NEO4J_URI": "bolt://neo4j.example.internal:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "test-password",
            "NEO4J_CONNECT_TIMEOUT_SECONDS": "1",
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
        connection_mock.cursor.side_effect = RuntimeError("database unavailable")

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
        driver_mock.return_value.__enter__.return_value.verify_connectivity.side_effect = (
            RuntimeError("neo4j unavailable")
        )

        response = health_check(HttpRequest())

        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"neo4j": "unavailable"', response.content)
        connection_mock.cursor.assert_called_once_with()

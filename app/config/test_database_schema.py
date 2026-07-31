from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class CheckDatabaseSchemaCommandTest(SimpleTestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_command_rejects_missing_table_configuration(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "POSTGRES_REQUIRED_TABLES",
        ):
            call_command("check_database_schema")

    @patch.dict(
        "os.environ",
        {"POSTGRES_REQUIRED_TABLES": "user_accounts,questions"},
        clear=False,
    )
    @patch(
        "user.management.commands.check_database_schema.apps"
    )
    @patch(
        "user.management.commands.check_database_schema.connection"
    )
    def test_command_accepts_complete_schema(
        self,
        connection_mock: MagicMock,
        apps_mock: MagicMock,
    ) -> None:
        connection_mock.introspection.table_names.return_value = [
            "questions",
            "user_accounts",
        ]
        apps_mock.get_models.return_value = []
        stdout = StringIO()

        call_command("check_database_schema", stdout=stdout)

        self.assertIn(
            "Required PostgreSQL tables and model columns are present.",
            stdout.getvalue(),
        )

    @patch.dict(
        "os.environ",
        {"POSTGRES_REQUIRED_TABLES": "user_accounts,questions"},
        clear=False,
    )
    @patch(
        "user.management.commands.check_database_schema.apps"
    )
    @patch(
        "user.management.commands.check_database_schema.connection"
    )
    def test_command_rejects_missing_table(
        self,
        connection_mock: MagicMock,
        apps_mock: MagicMock,
    ) -> None:
        connection_mock.introspection.table_names.return_value = [
            "user_accounts",
        ]
        apps_mock.get_models.return_value = []

        with self.assertRaisesMessage(CommandError, "questions"):
            call_command("check_database_schema")

    @patch.dict(
        "os.environ",
        {"POSTGRES_REQUIRED_TABLES": "user_accounts"},
        clear=False,
    )
    @patch(
        "user.management.commands.check_database_schema.apps"
    )
    @patch(
        "user.management.commands.check_database_schema.connection"
    )
    def test_command_rejects_missing_model_column(
        self,
        connection_mock: MagicMock,
        apps_mock: MagicMock,
    ) -> None:
        connection_mock.introspection.table_names.return_value = [
            "user_accounts",
        ]
        connection_mock.introspection.get_table_description.return_value = [
            SimpleNamespace(name="user_id"),
            SimpleNamespace(name="provider"),
        ]
        apps_mock.get_models.return_value = [
            SimpleNamespace(
                _meta=SimpleNamespace(
                    managed=False,
                    db_table="user_accounts",
                    local_fields=[
                        SimpleNamespace(column="user_id"),
                        SimpleNamespace(column="provider"),
                        SimpleNamespace(column="provider_id"),
                    ],
                )
            )
        ]

        with self.assertRaisesMessage(
            CommandError,
            "user_accounts.provider_id",
        ):
            call_command("check_database_schema")

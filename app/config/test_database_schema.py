from io import StringIO
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
        "user.management.commands.check_database_schema.connection"
    )
    def test_command_accepts_complete_schema(
        self,
        connection_mock: MagicMock,
    ) -> None:
        connection_mock.introspection.table_names.return_value = [
            "questions",
            "user_accounts",
        ]
        stdout = StringIO()

        call_command("check_database_schema", stdout=stdout)

        self.assertIn(
            "Required PostgreSQL tables are present.",
            stdout.getvalue(),
        )

    @patch.dict(
        "os.environ",
        {"POSTGRES_REQUIRED_TABLES": "user_accounts,questions"},
        clear=False,
    )
    @patch(
        "user.management.commands.check_database_schema.connection"
    )
    def test_command_rejects_missing_table(
        self,
        connection_mock: MagicMock,
    ) -> None:
        connection_mock.introspection.table_names.return_value = [
            "user_accounts",
        ]

        with self.assertRaisesMessage(CommandError, "questions"):
            call_command("check_database_schema")

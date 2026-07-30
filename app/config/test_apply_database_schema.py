from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import SimpleTestCase, override_settings


class ApplyDatabaseSchemaCommandTests(SimpleTestCase):
    def test_command_applies_schema_files_in_order(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            schema_directory = (
                project_root / "storage" / "postgresql" / "schema"
            )
            schema_directory.mkdir(parents=True)
            (schema_directory / "init.sql").write_text(
                "SELECT 1;",
                encoding="utf-8",
            )
            (schema_directory / "alter_apply_latest.sql").write_text(
                "SELECT 2;",
                encoding="utf-8",
            )

            connection_mock = MagicMock()
            cursor = (
                connection_mock.cursor.return_value.__enter__.return_value
            )
            with override_settings(BASE_DIR=project_root / "app"), patch(
                "user.management.commands.apply_database_schema.connection",
                connection_mock,
            ), patch(
                "user.management.commands.apply_database_schema.transaction",
            ):
                call_command("apply_database_schema")

        self.assertEqual(
            [call.args[0] for call in cursor.execute.call_args_list],
            ["SELECT 1;", "SELECT 2;"],
        )

    def test_command_rejects_missing_schema_files(self) -> None:
        with TemporaryDirectory() as temporary_directory, override_settings(
            BASE_DIR=Path(temporary_directory) / "app"
        ):
            with self.assertRaisesMessage(
                CommandError,
                "Missing PostgreSQL schema files",
            ):
                call_command("apply_database_schema")

    def test_command_wraps_database_errors(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            schema_directory = (
                project_root / "storage" / "postgresql" / "schema"
            )
            schema_directory.mkdir(parents=True)
            for schema_file_name in (
                "init.sql",
                "alter_apply_latest.sql",
            ):
                (schema_directory / schema_file_name).write_text(
                    "SELECT 1;",
                    encoding="utf-8",
                )

            connection_mock = MagicMock()
            cursor = (
                connection_mock.cursor.return_value.__enter__.return_value
            )
            cursor.execute.side_effect = DatabaseError("schema failure")
            with override_settings(BASE_DIR=project_root / "app"), patch(
                "user.management.commands.apply_database_schema.connection",
                connection_mock,
            ), patch(
                "user.management.commands.apply_database_schema.transaction",
            ):
                with self.assertRaisesMessage(
                    CommandError,
                    "Unable to apply the PostgreSQL schema files",
                ):
                    call_command("apply_database_schema")

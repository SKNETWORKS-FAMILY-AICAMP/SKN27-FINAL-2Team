import os

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "운영에 필요한 PostgreSQL 테이블이 모두 존재하는지 확인합니다."

    def handle(self, *args: object, **options: object) -> None:
        required_tables_value = os.getenv("POSTGRES_REQUIRED_TABLES", "").strip()
        if not required_tables_value:
            raise CommandError("POSTGRES_REQUIRED_TABLES must not be empty.")

        required_tables = {
            table_name.strip()
            for table_name in required_tables_value.split(",")
            if table_name.strip()
        }

        try:
            with connection.cursor() as cursor:
                existing_tables = set(
                    connection.introspection.table_names(cursor)
                )

                missing_tables = sorted(required_tables - existing_tables)
                if missing_tables:
                    raise CommandError(
                        "Missing required PostgreSQL tables: "
                        + ", ".join(missing_tables)
                    )

                required_models = {
                    model._meta.db_table: model
                    for model in apps.get_models()
                    if (
                        not model._meta.managed
                        and model._meta.db_table in required_tables
                    )
                }
                missing_columns = []
                for table_name, model in required_models.items():
                    existing_columns = {
                        column.name
                        for column in (
                            connection.introspection.get_table_description(
                                cursor,
                                table_name,
                            )
                        )
                    }
                    expected_columns = {
                        field.column
                        for field in model._meta.local_fields
                    }
                    missing_columns.extend(
                        f"{table_name}.{column_name}"
                        for column_name in sorted(
                            expected_columns - existing_columns
                        )
                    )
        except Exception as error:
            if isinstance(error, CommandError):
                raise
            raise CommandError(
                "Unable to inspect the PostgreSQL schema."
            ) from error

        if missing_columns:
            raise CommandError(
                "Missing required PostgreSQL columns: "
                + ", ".join(sorted(missing_columns))
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Required PostgreSQL tables and model columns are present."
            )
        )

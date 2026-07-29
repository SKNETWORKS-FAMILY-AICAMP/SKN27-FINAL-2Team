import os

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
        except Exception as error:
            raise CommandError(
                "Unable to inspect the PostgreSQL schema."
            ) from error

        missing_tables = sorted(required_tables - existing_tables)
        if missing_tables:
            raise CommandError(
                "Missing required PostgreSQL tables: "
                + ", ".join(missing_tables)
            )

        self.stdout.write(
            self.style.SUCCESS("Required PostgreSQL tables are present.")
        )

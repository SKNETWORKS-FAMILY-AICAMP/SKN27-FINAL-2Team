from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction


class Command(BaseCommand):
    help = "PostgreSQL 초기 스키마와 최신 변경 스크립트를 순서대로 적용합니다."

    def handle(self, *args: object, **options: object) -> None:
        schema_directory = (
            Path(settings.BASE_DIR).parent
            / "storage"
            / "postgresql"
            / "schema"
        )
        schema_files = (
            schema_directory / "init.sql",
            schema_directory / "alter_apply_latest.sql",
        )

        missing_files = [
            str(schema_file)
            for schema_file in schema_files
            if not schema_file.is_file()
        ]
        if missing_files:
            raise CommandError(
                "Missing PostgreSQL schema files: " + ", ".join(missing_files)
            )

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    for schema_file in schema_files:
                        schema_sql = schema_file.read_text(encoding="utf-8")
                        if not schema_sql.strip():
                            raise CommandError(
                                f"PostgreSQL schema file is empty: {schema_file.name}"
                            )
                        cursor.execute(schema_sql)
                        self.stdout.write(
                            f"Applied PostgreSQL schema file: {schema_file.name}"
                        )
        except CommandError:
            raise
        except (OSError, UnicodeError) as error:
            raise CommandError(
                "Unable to read the PostgreSQL schema files."
            ) from error
        except DatabaseError as error:
            raise CommandError(
                "Unable to apply the PostgreSQL schema files."
            ) from error

        self.stdout.write(
            self.style.SUCCESS("PostgreSQL schema files applied successfully.")
        )

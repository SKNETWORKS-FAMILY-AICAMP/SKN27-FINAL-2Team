from django.db import migrations


def timestamp_sql(column_type: str) -> list[str]:
    timestamp_columns = {
        "user_accounts": (
            "locked_at",
            "last_login",
            "created_at",
            "updated_at",
            "deleted_at",
        ),
        "email_verification_codes": (
            "created_at",
            "expires_at",
            "used_at",
        ),
        "chat_sessions": ("created_at",),
        "chat_messages": ("created_at",),
        "study_plan_mypage": (
            "created_at",
            "modified_at",
        ),
        "note_mypage": (
            "created_at",
            "modified_at",
        ),
        "ml_trend_top5": ("created_at",),
        "analytics": ("created_at",),
        "solve_records": ("saved_at",),
    }
    statements = []
    for table_name, column_names in timestamp_columns.items():
        for column_name in column_names:
            statements.append(
                f'ALTER TABLE IF EXISTS "{table_name}" '
                f'ALTER COLUMN "{column_name}" TYPE {column_type} '
                f'USING "{column_name}" AT TIME ZONE \'UTC\';'
            )
    return statements


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0003_social_login"),
    ]

    operations = [
        migrations.RunSQL(
            sql=timestamp_sql("timestamp with time zone"),
            reverse_sql=timestamp_sql("timestamp without time zone"),
        ),
    ]

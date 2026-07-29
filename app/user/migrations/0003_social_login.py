from django.db import migrations


class Migration(migrations.Migration):
    """소셜 로그인용 스키마 변경. user_accounts 는 managed=False 라 RunSQL 로 직접 바꾼다.

    - password_hash 를 NULL 허용으로(소셜 사용자는 비번 없음)
    - provider / provider_id 컬럼 추가
    - (provider, provider_id) 부분 유니크 인덱스로 중복 소셜 계정 방지
    """

    dependencies = [
        ("user", "0002_create_cache_table"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                ("ALTER TABLE IF EXISTS user_accounts "
                 "ALTER COLUMN password_hash DROP NOT NULL;", None),
                ("ALTER TABLE IF EXISTS user_accounts "
                 "ADD COLUMN IF NOT EXISTS provider varchar(20);", None),
                ("ALTER TABLE IF EXISTS user_accounts "
                 "ADD COLUMN IF NOT EXISTS provider_id varchar(255);", None),
                (
                    """
                    DO $$
                    BEGIN
                        IF to_regclass('user_accounts') IS NOT NULL THEN
                            EXECUTE
                                'CREATE UNIQUE INDEX IF NOT EXISTS '
                                'user_accounts_provider_uidx '
                                'ON user_accounts (provider, provider_id) '
                                'WHERE provider IS NOT NULL';
                        END IF;
                    END;
                    $$;
                    """,
                    None,
                ),
            ],
            reverse_sql=[
                ("DROP INDEX IF EXISTS user_accounts_provider_uidx;", None),
                ("ALTER TABLE IF EXISTS user_accounts DROP COLUMN IF EXISTS provider_id;", None),
                ("ALTER TABLE IF EXISTS user_accounts DROP COLUMN IF EXISTS provider;", None),
            ],
        ),
    ]

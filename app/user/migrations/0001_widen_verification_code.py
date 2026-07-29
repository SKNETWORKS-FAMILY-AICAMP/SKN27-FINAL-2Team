from django.db import migrations


class Migration(migrations.Migration):
    """email_verification_codes.code 컬럼을 해시 저장용으로 넓힌다.

    이 앱의 테이블은 managed = False 로 Django 밖에서 만들어졌으므로,
    상태 조작 없이 RunSQL 로 실제 컬럼 타입만 변경한다. 배포 전에
    `python manage.py migrate user` 로 적용해야 인증코드 해시 저장이 동작한다.
    """

    initial = True
    dependencies = []

    operations = [
        migrations.RunSQL(
            sql=[
                (
                    "ALTER TABLE IF EXISTS email_verification_codes "
                    "ALTER COLUMN code TYPE varchar(128);",
                    None,
                ),
                (
                    "ALTER TABLE IF EXISTS email_verification_codes "
                    "ADD COLUMN IF NOT EXISTS attempt_count "
                    "smallint NOT NULL DEFAULT 0;",
                    None,
                ),
            ],
            reverse_sql=[
                (
                    "ALTER TABLE IF EXISTS email_verification_codes "
                    "DROP COLUMN IF EXISTS attempt_count;",
                    None,
                ),
                (
                    "ALTER TABLE IF EXISTS email_verification_codes "
                    "ALTER COLUMN code TYPE varchar(10);",
                    None,
                ),
            ],
        ),
    ]

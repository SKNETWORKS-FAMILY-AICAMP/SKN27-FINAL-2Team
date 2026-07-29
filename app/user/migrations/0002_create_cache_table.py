from django.db import migrations


class Migration(migrations.Migration):
    """DatabaseCache 가 사용할 캐시 테이블을 만든다.

    createcachetable 을 수동 실행하는 대신, 배포의 migrate 흐름에 태운다.
    관리형이 아닌 유틸리티 테이블이라 RunSQL 로 직접 만든다.
    """

    # 0002_initial(makemigrations 생성) 뒤에 붙여 리프를 하나로 유지한다.
    dependencies = [
        ("user", "0002_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                (
                    'CREATE TABLE IF NOT EXISTS "himate_cache" ('
                    '"cache_key" varchar(255) NOT NULL PRIMARY KEY, '
                    '"value" text NOT NULL, '
                    '"expires" timestamp with time zone NOT NULL);',
                    None,
                ),
                (
                    'CREATE INDEX IF NOT EXISTS "himate_cache_expires" '
                    'ON "himate_cache" ("expires");',
                    None,
                ),
            ],
            reverse_sql=[
                ('DROP TABLE IF EXISTS "himate_cache";', None),
            ],
        ),
    ]

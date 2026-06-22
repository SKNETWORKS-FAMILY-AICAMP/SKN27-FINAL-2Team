# 저장소를 정의하는 공간

- postgre





- neo4j

```bash
docker compose --env-file .env -f storage\neo4j\docker-compose.yml up -d
docker compose --env-file .env -f storage\neo4j\docker-compose.yml down -v

docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
docker compose --env-file .env -f storage\postgresql\docker-compose.yml down -v

python storage\neo4j\load_schema.py
```


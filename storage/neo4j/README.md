# docker-compose

```bash
docker compose --env-file .env -f storage\neo4j\docker-compose.yml up -d
docker compose --env-file .env -f storage\neo4j\docker-compose.yml down -v

docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
docker compose --env-file .env -f storage\postgresql\docker-compose.yml down -v

python storage\neo4j\load_schema.py
```

# import

```
python etl/preprocessing/run_neo4j_preprecessing.py
python storage/neo4j/load_schema.py
```



# 문제 생성용 Fact Neo4j

기존 `skn27-neo4j`와 데이터·포트·볼륨을 공유하지 않는 별도
Neo4j Community 인스턴스다.

```powershell
docker compose -p skn27-fact-neo4j --env-file .env `
  -f storage/fact_neo4j/docker-compose.yml up -d
```

- Browser: `http://localhost:7475`
- Bolt: `bolt://localhost:7688`
- 컨테이너: `skn27-fact-neo4j`
- 데이터 볼륨: `skn27-fact-neo4j-data`

일반 종료에는 볼륨을 삭제하지 않는다.

```powershell
docker compose -p skn27-fact-neo4j --env-file .env `
  -f storage/fact_neo4j/docker-compose.yml down
```

`down -v`는 신규 Fact DB 데이터를 모두 버릴 때만 사용한다.

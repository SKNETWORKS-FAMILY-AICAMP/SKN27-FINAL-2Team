# HiMate ECS on EC2 배포

## 운영 구성

```text
Route53
  → Public subnet의 t3.small ECS Container Instance
  → Nginx(80/443)
  → Gunicorn/Django(8000)
  → Private Aurora PostgreSQL(5432)
  → Main Neo4j AuraDB(neo4j+s)

EventBridge
  → Fact 배치 EC2 시작
  → Fact Neo4j + 문제 생성 작업
  → RunPod Serverless 호출
  → 결과 저장
  → Fact 배치 EC2 중지
```

초기 저비용 구성에서는 ALB와 NAT Gateway를 사용하지 않는다. Main Neo4j는
AuraDB Free에서 상시 제공하고, Fact Neo4j는 문제 생성 시점에만 별도 EC2에서
실행한다.

## ECS 인스턴스 분리

두 종류의 EC2를 같은 ECS 클러스터에 등록하되 인스턴스 속성으로 Task 배치를
분리한다.

- Web EC2: `himate.workload=web`
- Fact 배치 EC2: `himate.workload=fact-batch`

사용자 데이터 템플릿:

- `web-container-instance-user-data.sh.template`
- `fact-container-instance-user-data.sh.template`

`__ECS_CLUSTER__`를 실제 클러스터 이름으로 바꾼 뒤 EC2 사용자 데이터에 넣는다.

## Task Definition

- `service-task-definition.json.template`: Web·Nginx 상시 Task
- `migration-task-definition.json.template`: Django migration 일회성 Task
- `fact-neo4j-task-definition.json.template`: Fact 배치 EC2 전용 Neo4j Task

Web과 migration Task는 `himate.workload=web`, Fact Neo4j Task는
`himate.workload=fact-batch` 인스턴스에서만 실행된다.

## t3.small 메모리 기준

- Django: hard `1024 MiB`, reservation `768 MiB`
- Nginx: hard `128 MiB`, reservation `64 MiB`
- Migration: hard `512 MiB`, reservation `384 MiB`
- Gunicorn: worker 2개, worker당 thread 4개

고정 host port `80/443`과 단일 Web EC2를 사용하므로 ECS Service 배포 설정은
minimum healthy percent `0`, maximum percent `100`으로 설정한다. 배포 중 짧은
중단이 발생한다.

## 주요 설정

- Public Web EC2 subnet 1개
- Private RDS subnet 2개
- Aurora Serverless v2: 최소 `0 ACU`, 최대 `1 ACU`
- Aurora auto-pause: `300초`
- `POSTGRES_CONN_MAX_AGE=0`
- 운영 Parameter Store prefix: `/himate/prod`
- 비밀번호와 API Key: Parameter Store `SecureString`
- Main Neo4j URI: `neo4j+s://...databases.neo4j.io`
- ECS liveness 경로: `/health/live/`
- ECR tag immutability와 scan on push 활성화

## 최초 배포 순서

1. VPC, Aurora, Parameter Store, IAM을 구성한다.
2. Web EC2를 `himate.workload=web` 속성으로 ECS 클러스터에 등록한다.
3. Main Neo4j 데이터를 AuraDB Free에 적재한다.
4. CodeBuild로 이미지를 ECR에 push한다.
5. migration Task를 실행한다.
6. Web ECS Service를 실행하고 HTTPS와 `/health/live/`를 확인한다.
7. CodePipeline 배포 단계를 연결한다.
8. 문제 생성 자동화가 필요할 때 Fact 배치 EC2와 EBS를 추가한다.

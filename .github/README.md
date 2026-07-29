# GitHub Actions

## CI

`.github/workflows/ci.yml`은 `dev`, `main` 브랜치의 코드를 검증한다.

- `dev`, `main` 브랜치 push 시 실행
- `dev`, `main` 브랜치를 대상으로 하는 Pull Request 생성·변경 시 실행
- `test`: PostgreSQL을 사용한 Django 앱 테스트와 Docker 이미지 빌드 확인
- `neo4j-regression`: `test/MK/test_neo4j`의 mock 기반 회귀 테스트 실행
- `neo4j-integration`: 임시 Neo4j 서비스를 띄워 `graph_service`의 실제 쿼리와 연결 확인

CI에서는 이미지를 ECR에 올리거나 운영 서버에 배포하지 않는다.

`python app/manage.py test`만 실행하면 현재 프로젝트 구조에서는 테스트를 찾지
못하므로, CI는 `diagnosis`, `chatbot`, `analytics`, `config.test_health`를
명시하여 실행한다.

CodePipeline 배포 전에 CI 통과를 강제하려면 GitHub 브랜치 보호 규칙 또는
CodePipeline 안의 별도 테스트 단계를 설정해야 한다.

## 운영 배포

운영 배포는 GitHub Actions를 사용하지 않는다.

```text
CodePipeline
  → CodeBuild
  → ECR
  → CodePipeline EC2 Deploy
  → EC2 Docker 컨테이너
```

배포 관련 파일은 저장소 루트의 `buildspec.yml`, `deployspec.yml`과
`deployment/deploy.sh`이다. CodePipeline의 소스 브랜치와 대상 EC2 설정은
AWS에서 관리한다.

배포 스크립트는 새 이미지로 Django 운영 설정과 migration을 검사한 뒤 컨테이너를
교체한다. `/health/`에서 PostgreSQL과 Neo4j 연결이 모두 확인되어야 배포가
성공하며, 실패하면 이전 이미지를 다시 실행한다.

EC2 환경파일은 `deployment/ec2.env.example`을 참고하되 실제 비밀값은 저장소에
커밋하지 않는다. CodeBuild에는 `EC2_WEB_HEALTH_TIMEOUT_SECONDS`와
`EC2_WEB_HEALTH_POLL_SECONDS`, `EC2_WEB_DOCKER_NETWORK`도 설정해야 한다.


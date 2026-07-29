# GitHub Actions

## CI 역할

`.github/workflows/ci.yml`은 `dev`, `main` 브랜치의 push와 Pull Request에서
애플리케이션을 검증합니다.

- PostgreSQL 기반 Django 테스트
- Neo4j mock 회귀 테스트
- 임시 Neo4j 서비스를 사용한 통합 테스트
- 운영 Docker 이미지 빌드 확인

GitHub Actions에서는 ECR push와 운영 배포를 수행하지 않습니다.

## 운영 CD 역할

운영 배포는 GitHub Actions가 아니라 AWS CodePipeline이 담당합니다.

```text
CodePipeline
  → CodeBuild: 테스트, 앱·Nginx 이미지 빌드, ECR Basic scan, migration
  → ECR
  → ECS Standard Deploy
  → Public EC2의 ECS on EC2
```

배포 관련 파일은 `buildspec.yml`과 `deployment/ecs/`,
`deployment/nginx/`에 있습니다. 이전 EC2 직접 배포용 `deployspec.yml`과
`deploy.sh`는 사용하지 않습니다. 현재 Nginx는 ECS 컨테이너로 실행하고,
EC2 호스트의 Certbot은 인증서 발급·갱신만 담당합니다.

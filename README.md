# SKN27-FINAL-2Team · HiMate

Python 3.12와 Django 6 기반 한국사 학습 서비스입니다.

## 주요 디렉터리

```text
app/          Django 애플리케이션
ai/           LLM·ML 및 문제 생성 코드
etl/          수집·전처리 파이프라인
storage/      로컬 PostgreSQL·Neo4j 개발 구성
test/         개별 실험 및 평가 테스트
deployment/   AWS ECS와 Nginx 배포 설정
```

## 로컬 개발

로컬 개발은 저장소 루트의 `.env`를 사용합니다. `.env`는 비밀값이므로 Git과
Docker build context에서 제외됩니다.

```powershell
.\.venv\Scripts\python.exe app\manage.py check
.\.venv\Scripts\python.exe app\manage.py test
```

## CI

GitHub Actions는 `dev`, `main` push와 Pull Request에서 다음 항목만 검증합니다.
운영 배포는 수행하지 않습니다.

- PostgreSQL 기반 Django 테스트
- Neo4j mock 회귀 테스트
- 임시 Neo4j 서비스 통합 테스트
- 운영 Docker 이미지 빌드

## 운영 배포

```text
Route53 → Elastic IP → Public EC2 / ECS on EC2
                         └─ Nginx HTTPS → Gunicorn/Django

Django → Private Aurora PostgreSQL Serverless v2
Django → Main Neo4j AuraDB Free
CodePipeline → CodeBuild → ECR → ECS Standard Deploy

문제 생성 배치(추가 구축)
EventBridge → 임시 Fact EC2/ECS → Fact Neo4j → RunPod Serverless → EC2 중지
```

- ALB와 NAT Gateway 없이 단일 Web EC2로 비용 절감
- ECS Task는 `bridge` 네트워크 사용
- 앱·Nginx 이미지는 Git commit SHA immutable tag와 digest로 배포
- ECR Basic scan on push 결과로 취약점 gate 적용
- Parameter Store 값을 ECS secret 환경변수로 주입
- Nginx와 Django는 비루트, read-only root filesystem으로 실행
- Nginx HTTPS 인증서는 EC2 Certbot이 자동 갱신
- 챗봇용 Main Neo4j는 AuraDB Free에서 상시 운영
- 문제 생성용 Fact Neo4j는 별도 임시 EC2에서만 실행

상세 설정과 적용 순서는
[deployment/ecs/README.md](deployment/ecs/README.md)를 참고하세요.

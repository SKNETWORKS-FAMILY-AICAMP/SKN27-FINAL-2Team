# -SKN27-FINAL-2Team

# python 3.12

# 폴더구조

```text
SKN27-FINAL-2Team/
|-- README.md
|-- requirements.txt
|-- ai/
|   |-- llm/
|   |-- ml/
|   `-- models/
|-- app/
|   |-- README.md
|   |-- manage.py
|   |-- config/
|   |   |-- settings.py
|   |   |-- urls.py
|   |   |-- asgi.py
|   |   `-- wsgi.py
|   |-- user/
|   |-- chatbot/
|   |-- analytics/
|   |-- diagnosis/
|   `-- question/
|-- docs/
|   |-- README.md
|   |-- setup-guide.md
|   `-- image/
|-- etl/
|   |-- README.md
|   |-- crawling/
|   `-- exam_question_pipeline/
|-- storage/
|   |-- README.md
|   |-- postgre/
|   `-- neo4j/
`-- test/
    |-- README.md
    |-- CJ/
    |-- HS/
    |-- MK/
    `-- YJ/
```

- `ai/`: LLM, ML, 모델 파일 작업 공간
- `app/`: Django 프로젝트 및 서비스 앱
- `docs/`: 프로젝트 문서와 이미지 자료
- `etl/`: 크롤링 및 문제 데이터 파이프라인 작업 공간
- `storage/`: 저장소 작업 공간
- `test/`: 팀원별 테스트 작업 공간

## 운영 배포

운영 배포는 CodePipeline → CodeBuild → ECR → EC2 Docker 순서로 실행합니다.

- 일반 설정: EC2의 `EC2_WEB_ENV_FILE`에 저장
- 비밀값: SSM Parameter Store `SecureString`에 저장
- SSM 경로: CodeBuild의 `EC2_WEB_SSM_PARAMETER_PREFIX`로 지정
- 비밀값 전달: EC2의 임시 파일에서 전용 Docker volume으로 옮긴 뒤 `app` 사용자만 읽기
- 이미지: Git SHA 태그와 revision label을 검증한 뒤 취약점 검사를 통과한 digest로 배포
- ECR: tag immutability와 Enhanced Scanning이 켜져 있지 않으면 빌드 중단
- 배포 게이트: Django 설정·마이그레이션·테스트를 통과해야 ECR push 진행
- 웹 포트: EC2의 `127.0.0.1`에만 연결하고 로컬 HTTPS proxy를 통해 공개
- Nginx: 배포 artifact의 템플릿을 실제 EC2 설정으로 생성한 뒤 `nginx -t`와 reload 실행
- 인증서: 기존 Certbot 인증서와 systemd 갱신 timer를 확인하고 갱신 후 Nginx reload hook 설치

GitHub Actions의 `uses`는 움직일 수 있는 버전 tag 대신 전체 commit SHA로
고정합니다. Docker base·CI service image도 움직일 수 있는 tag와 함께
내용 기반 `sha256` digest를 지정하여 항상 검토한 코드와 이미지를 실행합니다.

예를 들어 prefix가 `/himate/prod`이면 다음 SecureString이 필요합니다.

- `/himate/prod/POSTGRES_PASSWORD`
- `/himate/prod/NEO4J_PASSWORD`
- `/himate/prod/OPENAI_API_KEY`
- `/himate/prod/DJANGO_SECRET_KEY`
- `/himate/prod/EMAIL_HOST_PASSWORD`

EC2 Instance Role에는 위 경로의 `ssm:GetParameter` 권한과, 고객 관리형 KMS 키를
사용하는 경우 해당 키의 `kms:Decrypt` 권한이 필요합니다.
CodeBuild Role에는 ECR push·조회 권한 외에
`ecr:GetRegistryScanningConfiguration` 권한도 필요합니다.

운영 환경파일은 PostgreSQL `verify-full`, Neo4j의 인증서 검증 TLS 주소
(`bolt+s://` 또는 `neo4j+s://`), SMTP backend와 HTTPS 보안 설정을 사용해야 합니다.
운영 이미지는 AWS 공식 RDS global CA bundle을
`/etc/ssl/certs/aws-rds-global-bundle.pem`에 포함합니다.

CodeBuild 프로젝트에는 다음 Nginx 배포 변수도 설정해야 합니다.

- `EC2_WEB_SERVER_NAME`: 실제 단일 도메인
- `EC2_WEB_NGINX_CONFIG_PATH`: 실제 Nginx server 설정 경로
- `EC2_WEB_TLS_CERTIFICATE_PATH`: Certbot `fullchain.pem` 경로
- `EC2_WEB_TLS_PRIVATE_KEY_PATH`: Certbot `privkey.pem` 경로
- `EC2_WEB_CERTBOT_WEBROOT`: ACME challenge webroot
- `EC2_WEB_CERTBOT_RENEWAL_HOOK_PATH`: 인증서 갱신 후 Nginx reload hook 경로
- `EC2_WEB_PRIVATE_NETWORK_CIDR`: readiness 접근을 허용할 VPC CIDR

첫 배포 전에는 다음 준비가 끝나 있어야 합니다.

1. 기존 PostgreSQL schema와 데이터를 RDS에 import합니다. 프로젝트의 운영
   모델은 `managed=False`이므로 Django가 빈 RDS에 원본 테이블을 만들지 않습니다.
2. EC2에 Nginx와 Certbot을 설치하고 최초 인증서를 발급합니다.
3. `certbot.timer` 또는 `snap.certbot.renew.timer`가 설치되어 있는지 확인합니다.
4. EC2 Docker network와 일반 운영 환경파일을 생성합니다.

배포 스크립트는 migration을 기록하기 전에 `check_database_schema`를 실행하므로,
필수 테이블이 하나라도 없으면 기존 컨테이너를 교체하지 않고 배포를 중단합니다.


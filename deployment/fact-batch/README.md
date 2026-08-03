# Fact 문제 생성 배치

운영 Web 이미지와 문제 생성 이미지를 분리한다. EventBridge Scheduler가 Systems
Manager Automation을 호출하면 다음 순서로 실행된다.

```text
EventBridge Scheduler
  → Fact EC2 시작
  → SSM Agent Online 대기
  → Fact 전용 이미지 실행
  → Fact Neo4j + Aurora 조회
  → OpenAI + RunPod Serverless 문제 생성
  → S3 검수 경로에 결과 업로드
  → Fact EC2 중지
```

생성 결과를 운영 PostgreSQL에 자동 저장하지 않는다. S3 결과의
`artifact-manifest.json` 상태가 `READY_FOR_REVIEW`인지 확인하고 사람이 검수한 후
별도 적재 절차를 수행한다.

## 저장소 파일

- `Dockerfile`: Fact 배치 전용 비루트 이미지
- `entrypoint.sh`: Graph Pack과 문항 생성 실행
- `wait_for_dependencies.py`: Aurora와 Fact Neo4j 준비 상태 확인
- `ssm-command-document.yml`: 실행·S3 업로드 Command 문서
- `ssm-automation-document.yml`: EC2 시작·Command 실행·EC2 중지 Runbook
- `instance-policy.json.template`: Fact EC2 인스턴스 역할의 추가 권한
- `automation-role-*.json*`: Automation 역할 정책
- `scheduler-role-*.json*`: EventBridge Scheduler 역할 정책

`buildspec.yml`은 커밋 SHA 기반의 `-fact-batch` 태그로 이미지를 ECR에 올리고,
digest가 고정된 URI를 `fact-batch-image.json` 빌드 산출물에 기록한다.

## Parameter Store

모든 값은 `/himate/prod` 아래에 둔다. 비밀번호와 API Key는 `SecureString`으로
저장한다.

- PostgreSQL: `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `POSTGRES_CONNECT_TIMEOUT_SECONDS`,
  `POSTGRES_SSLMODE`, `POSTGRES_SSLROOTCERT`
- Fact Neo4j: `FACT_NEO4J_URI`, `FACT_NEO4J_USER`, `FACT_NEO4J_PASSWORD`
- OpenAI: `OPENAI_API_KEY`, `OPENAI_CHAT_MODEL`
- RunPod: `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY`, `RUNPOD_SLLM_MODEL`

실제 값, 계정 비밀번호, API Key, EC2 개인 키는 저장소에 기록하지 않는다.

## AWS 등록 순서

1. 전용 S3 버킷에 검수된 spec JSON을 업로드한다.
2. `instance-policy.json.template`의 자리표시자를 바꾸어 Fact EC2 역할에 붙인다.
3. `ssm-command-document.yml`을 Command 문서
   `Himate-GenerateFactQuestions`로 등록한다.
4. Automation 역할과 Scheduler 역할을 각 trust/policy 템플릿으로 만든다.
5. `ssm-automation-document.yml`을 Automation Runbook으로 등록한다.
6. 먼저 Automation 콘솔에서 수동 실행해 S3 결과와 EC2 자동 중지를 확인한다.
7. 검증 후 EventBridge Scheduler 대상에 Automation Runbook을 연결한다.

Scheduler 입력에는 ECR 태그 URI가 아니라 `fact-batch-image.json`의 digest URI를
사용한다. S3 입력·출력 경로와 IAM 정책은 전용 버킷 범위로 제한한다.

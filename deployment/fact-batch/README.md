# Fact 문제 생성 배치

운영 Web과 문제 생성 리소스를 분리한다. EventBridge Scheduler가 Systems Manager
Automation을 호출하면 다음 순서로 실행된다.

```text
EventBridge Scheduler (매주 화요일 00:00, Asia/Seoul)
  → Fact EC2 시작
  → SSM Agent Online 대기
  → CLI에서 선택·승인한 spec 중 미사용 spec 5개 선택
  → spec 5개로 Graph Pack 5개 생성
  → OpenAI + RunPod Serverless로 문제 생성
  → S3 실행 결과 업로드
  → 누적 Pack Bank 갱신
  → Fact EC2 중지
```

## 안전 제약

- spec은 `ai.question_generation.interactive_cli`에서 필요한 시점에 사람이 조건을
  선택하고 승인한 뒤 S3 `SpecS3Uri`에 업로드한다.
- 배치는 spec을 생성하거나 승인하지 않고, S3의 승인 spec만 소비한다.
- 한 번에 사용할 수 있는 spec은 1~5개이고 운영 기본값은 5개다.
- spec의 전체 내용을 SHA-256으로 식별한다. 이미 사용한 spec은 다시 사용하지 않는다.
- 미사용 spec이 실행 수량보다 적으면 재사용하거나 자동 생성하지 않고 실패한다.
- 승인 spec 고갈은 종료 코드 42로 생성 작업을 중단하고 SNS 알림을 발송한다.
- spec 하나는 Pack 하나만 만든다. 생성 Pack 수가 선택 spec 수와 다르면 실패한다.
- 기존 Pack Bank와 새 Pack 전체를 함께 검증하여 fact 재사용을 막는다.
- 성공한 실행만 `cumulative_pack_bank.json`을 만들고 S3 누적 Pack Bank를 갱신한다.
- 결과는 아직 운영 PostgreSQL에 자동 적재하지 않는다. `artifact-manifest.json`이
  `READY_FOR_REVIEW`인지 확인한 뒤 별도 적재 절차를 수행한다.

승인 spec이 10개이고 실행당 5개이면 2주간 실행할 수 있다. 세 번째 실행은 새로
승인된 spec이 추가될 때까지 실패한다.

## S3 구성

예시 경로:

```text
input/approved-specs/graph_pack_specs.json
state/cumulative-pack-bank.json
output/<run-id>/
```

최초 `state/cumulative-pack-bank.json`에는 다음 파일을 업로드한다.

```text
ai/question_generation/data/production_20260723/packs/standard_50.json
```

S3 버킷 버전 관리를 켜 두면 누적 Pack Bank를 이전 버전으로 복구할 수 있다.

## 배포 파일

- `Dockerfile`: Fact 배치 전용 이미지
- `entrypoint.sh`: spec 선택, Pack·문제 생성, 누적 Bank 병합
- `../../ai/pack_generation/batch_constraints.py`: 주간 수량·재사용 제약
- `wait_for_dependencies.py`: Aurora와 Fact Neo4j 준비 상태 확인
- `ssm-command-document.yml`: 컨테이너 실행과 S3 업로드
- `ssm-automation-document.yml`: EC2 시작·Command 실행·EC2 중지
- `instance-policy.json.template`: Fact EC2 IAM 정책
- `automation-role-*.json*`: SSM Automation 역할 정책
- `scheduler-role-*.json*`: EventBridge Scheduler 역할 정책

## Parameter Store

운영 값은 `/himate/prod` 아래에 저장한다. 비밀번호와 API Key는 `SecureString`을
사용한다.

- PostgreSQL: `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `POSTGRES_CONNECT_TIMEOUT_SECONDS`,
  `POSTGRES_SSLMODE`, `POSTGRES_SSLROOTCERT`
- Fact Neo4j: `FACT_NEO4J_URI`, `FACT_NEO4J_USER`, `FACT_NEO4J_PASSWORD`
- OpenAI: `OPENAI_API_KEY`, `OPENAI_CHAT_MODEL`
- RunPod: `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY`, `RUNPOD_SLLM_MODEL`

실제 값, 계정 비밀번호, API Key, EC2 개인 키는 저장소에 기록하지 않는다.

## spec 고갈 알림

SNS Standard Topic과 이메일 구독을 만든 뒤 Topic ARN을 Automation의
`AlertTopicArn`에 입력한다. `instance-policy.json.template`의
`__FACT_BATCH_ALERT_TOPIC_ARN__`도 같은 ARN으로 치환한다. 미사용 spec이
`PacksPerRun`보다 적으면 문제 생성과 누적 Pack Bank 갱신을 모두 중단한다.

## AWS 등록 순서

1. 통합 CLI에서 spec을 선택·승인하여 S3에 업로드하고, 최초
   `standard_50.json` Pack Bank도 S3에 업로드한다.
2. spec 고갈 알림용 SNS Topic과 이메일 구독을 생성한다.
3. `ssm-command-document.yml`을 Command 문서 `Himate-GenerateFactQuestions`로 등록한다.
4. `ssm-automation-document.yml`을 Automation Runbook으로 등록한다.
5. 수동 실행에서 `SpecS3Uri`, `PacksPerRun=5`, `AlertTopicArn`을 지정해 검증한다.
6. S3 결과와 누적 Pack Bank의 `consumed_spec_ids`를 확인한다.
7. EventBridge Scheduler에 `cron(0 0 ? * TUE *)`, 시간대 `Asia/Seoul`로 연결한다.

Scheduler에는 태그가 아닌 `fact-batch-image.json`의 digest 고정 ECR URI를 넣는다.

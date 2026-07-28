# analytics

학습계획 생성과 학습 분석, 주간 리포트를 담당한다.

## management command

### run_weekly_report_worker

주간 리포트를 실제로 생성하는 워커다. 주간복습을 제출하면 `study_plan_mypage.weekly_report_data`
에 `pending` 리포트가 예약될 뿐이고, 문장 생성은 이 워커가 맡는다. **띄우지 않으면 화면의
"작성 중" 표시가 끝나지 않는다.**

```bash
python manage.py run_weekly_report_worker
```

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--interval` | 10 | 폴링 간격(초) |
| `--batch-size` | 1 | 한 번에 처리할 건수 |
| `--recovery-every` | 60 | 몇 번 폴링마다 복구 스캔을 돌릴지. 0 이면 하지 않는다 |
| `--once` | - | 1회만 돌고 종료 |

출력에 찍히는 결과 코드:

- `ready` — 생성 완료. 화면에 리포트가 나온다
- `retried` — 생성 실패. 30초 → 120초 간격으로 다시 시도하고, 3회째에는 기본 문구로 확정한다
- (무출력) — 처리할 건이 없다

`--once` 는 한 바퀴만 돌고 끝난다. 재시도는 같은 프로세스가 기다렸다 하는 것이 아니라 다음
폴링 주기에 DB 에서 다시 집어가는 구조이므로, 재시도까지 보려면 `--once` 없이 띄워야 한다.

`WEEKLY_REPORT_LLM_MODEL` 또는 `OPENAI_CHAT_MODEL` 환경변수가 없으면 기동 단계에서 경고를
출력하고, 이후 모든 리포트가 기본 문구로만 만들어진다.

### reset_weekly_review_demo

학습계획을 "오늘이 7일차(주간 평가일)" 인 상태로 되돌린다. **개발·검증 전용이다.**

주간 평가는 그 날짜가 오늘이고 같은 계획의 학습 블록이 전부 완료됐을 때만 시작할 수 있다.
6일치를 실제로 풀지 않고 트리거부터 화면까지 확인하려면 그 상태를 만들어야 한다.

```bash
python manage.py reset_weekly_review_demo --user-id 1
```

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--user-id` | (필수) | 대상 사용자 ID |
| `--study-plan-id` | 0 | 대상 학습계획 ID. 0 이면 활성 계획 |
| `--keep-records` | - | 풀이 기록을 지우지 않는다 |

하는 일:

1. 주간 평가일이 오늘이 되도록 계획 전체 날짜를 같은 폭으로 민다
2. 학습 블록은 완료로, 주간 평가는 미응시로 되돌린다
3. 이 계획에 연결된 `solve_records` / `solve_sessions` / `analytics` 스냅샷을 지운다
4. `weekly_report_data` 를 비운다

3번이 핵심이다. 블록 완료 여부는 계획 JSON 뿐 아니라 `solve_records` 에서도 파생되므로
(`service.py` 의 `_get_progress_block_ids`), 풀이 기록을 남겨두면 두 번째 실행부터는 주간
평가가 계속 완료로 잡혀 다시 응시할 수 없다. `--keep-records` 를 주면 이 단계를 건너뛴다.

계획과 무관한 진단평가 기록은 지우지 않는다. 주간 리포트가 직전 점수를 비교 기준으로 쓰기
때문이다. 다른 계획의 기록이 섞인 세션도 세션 자체는 남기고 기록만 지운다.

## 주간 리포트 전체 흐름 확인

```bash
# 1. 계획을 7일차 상태로 되돌린다
python manage.py reset_weekly_review_demo --user-id 1

# 2. 마이페이지에서 '주간 평가 시작' → 50문항 제출
#    diagnosis/views.py 가 enqueue_weekly_report 를 불러 pending 리포트를 만든다

# 3. 워커를 띄워 문장을 생성한다
python manage.py run_weekly_report_worker --interval 10

# 4. 마이페이지 새로고침
```

2번에서 리포트가 예약됐는지 확인:

```sql
SELECT studyplan_id,
       weekly_report_data->>'status'          AS report_status,
       weekly_report_data->>'sourceSessionId' AS session_id
  FROM study_plan_mypage
 WHERE user_id = 1;
```

## 테스트

```bash
python manage.py test analytics
```

모델이 전부 `managed = False` 라 테스트 러너가 테이블을 만들지 못한다. 그래서 DB 를 쓰지
않고 ORM 을 mock 으로 대체한다. 실제 행 잠금과 커밋 시점은 자동 검증할 수 없다.

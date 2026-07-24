# 학습계획·AI 리포트 상세 실행 계획

> 상태: DETAILED-PLAN (초안)
> 기준일: 2026-07-16 / SPEC·AI_WORKFLOW 대조 검증: 2026-07-17
> 원천 문서: `학습계획_AI리포트_정의_재검토.md` (RESOLVED), `기출_분포_조사_75_76_77.md` (EVIDENCE-SNAPSHOT),
> `취약점_분석_개선_설계.md`, `취약점_개선_협조_요청서.md`, `study_plan/SPEC.md`, `study_plan/AI_WORKFLOW.md`
> 2026-07-17 대조 결과 기존 `[가정]` 항목은 전부 해소·정정했다 (9장 기록 참조).

## 0. 문서 목적과 범위

재검토 문서에서 확정된 6개 결정(5.1~5.6)을 구현 가능한 수준으로 상세화하고,
기출 분포 조사의 blueprint 근거를 계획에 직접 통합한다. 이 문서는 정의 문서가
아니라 **실행 계획**이다. 정의의 단일 기준은 여전히 SPEC.md·AI_WORKFLOW.md이며,
이 문서와 충돌 시 canonical이 우선한다.

## 1. 문서 위계 (현재 상태)

| 문서 | 상태 | 역할 |
|---|---|---|
| study_plan/SPEC.md | PROPOSED CANONICAL, 미구현 | 계획 v2 단일 정책 기준 |
| study_plan/AI_WORKFLOW.md | PROPOSED CANONICAL, 미구현 | 리포트 v2 workflow 기준 |
| 기출_분포_조사_75_76_77.md | EVIDENCE-SNAPSHOT | blueprint quota 근거 |
| 취약점_분석_개선_설계.md | 구현 전 검토용 | weaknessScore·status·trend 단일 정의 |
| 취약점_개선_협조_요청서.md | 요청 중 | 오답노트 로직·결과 화면 라벨 (범위 외 2건) |
| 학습계획_AI리포트_정의_재검토.md | RESOLVED | 결정 기록 (본 문서의 원천) |
| 본 문서 | DETAILED-PLAN 초안 | 결정 → 실행 항목 전개 |
| 구문서 3종 (학습계획_설계 등) | 삭제됨 (2026-07-16) | 유니크 내용 canonical로 이관 완료 |

## 2. 생성 파이프라인 상세

v2 흐름을 단계별 입력·출력·실패 처리로 전개한다. 결정론/LLM 구분을 명시한다.

| # | 단계 | 성격 | 입력 | 출력 | 실패 시 처리 |
|---|---|---|---|---|---|
| 1 | 주간평가 제출 | 트랜잭션 | 답안 | 답안 저장 + 세션 완료 + 블록 완료 + report/job upsert (원자적) | 트랜잭션 롤백, 재제출 가능 |
| 2 | ai_jobs enqueue | durable queue | job row | 대기 job | 멱등키로 중복 방지 |
| 3 | worker claim | polling | 대기 job | claim된 job | lease 10분·full-jitter backoff, attempt 상한: WEEKLY_REPORT 3 / NEXT_PLAN 2 (AI_WORKFLOW 6·7·14장) |
| 4 | Collector | 결정론 | 제출 답안, 이력 | collected_facts 스냅샷 (최초 1회 고정, 재시도는 digest 검증 후 재사용) | 데이터 결손 시 job 실패 (부분 생성 금지) |
| 5 | Weakness Analyst | 결정론 | 최근 90일 SolveRecords (완료 세션) | `build_weakness_rows` 산출 행 (2.1 참조) | 계산 불가 시 job 실패 |
| 6 | Recommender | LLM (LangGraph) | 분석 결과 | 추천 후보 | **soft-fail**: 재작성 1회 내 guard 미통과면 추천을 비우고 리포트는 계속 생성 (AI_WORKFLOW 9장) |
| 7 | guard | 결정론 | LLM 출력 | 검증 통과 출력 | 스키마·범위 위반 차단 |
| 8 | Writer | LLM (LangGraph) | 추천 결과 | 리포트 서술문 | **hard-fail**: 재작성 2회 내 guard 미통과면 리포트 실패 (Recommender와 다름) |
| 9 | Renderer | 결정론 | 검증된 출력 | report READY + NEXT_PLAN job (원자적) | 원자성 깨지면 전체 롤백 |
| 10 | Planner | 결정론 | NEXT_PLAN job, priority 결과 | PlanDraft | 검증 실패 시 기존 active 유지 |
| 11 | finalize | 트랜잭션 | 검증된 PlanDraft | 기존 active archive + 새 active insert | 트랜잭션 롤백, 기존 active 유지 |

원칙:

- LLM 노드는 Recommender·Writer 2개뿐이다. 날짜·문항수·우선순위 등 수치 결정은
  전부 결정론 단계가 담당하고, LLM 출력은 guard를 통과해야만 다음 단계로 간다.
- 어떤 단계가 실패해도 기존 active 계획은 훼손되지 않는다 (finalize 원자성).

### 2.1 Weakness Analyst 상세 (취약점_분석_개선_설계.md 기준)

취약 점수는 단일 정의다: **weakness_score = 보정 오답률 (최근성 가중 표본 기준)**,
0.0~1.0. 합성 지표가 아니며, 시간 부담·출제 예상은 우선순위 단계(4장)에서만 결합한다.

계산 순서 (`app/analytics/service/weakness.py`의 `build_weakness_rows`):

1. **감쇠 가중**: `weight = 0.5^(days_ago / half_life)`, half_life=14일, 계산 범위
   최근 90일. `effective_total = Σweight`, `effective_wrong = Σweight×is_wrong`.
   days_ago 기준은 세션 recorded_date.
2. **Wilson 하한 보정**: 감쇠 가중 표본에 z=1.28(80% 신뢰)의 이항 신뢰구간 하한을
   적용 → weaknessScore. 소표본 과대평가(1/1=100% 문제)를 구조적으로 방지.
3. **status 판정**: INSUFFICIENT(effective_total < min_sample 3.0) / WEAK(≥0.50) /
   STABLE(≤0.20) / NEUTRAL(그 외). INSUFFICIENT 판정도 raw 건수가 아니라
   effective_total 기준.
4. **trend 판정**: 최근 14일 vs 이전 14일의 보정 오답률 delta. ±0.10 임계로
   WORSENING/IMPROVING/FLAT, 한쪽 표본 부족 시 UNKNOWN. 구간 비교에는 감쇠를
   쓰지 않는다 (현재 상태 추정과 구간 간 변화는 다른 목적).

출력은 설계 문서 5장 스키마의 행 목록: groupKeyId(canonical string, 매칭용) /
groupKey(구조) / raw / effective / weaknessScore / status / trend / insufficientReason.
raw와 effective가 항상 함께 다닌다 (표시=raw, 판정=보정 원칙).

파라미터(min_sample, 임계값, z, half_life, trend_threshold, trendBonus)는 전부
`get_weakness_config()` 한 곳에서 관리하며 초기값은 가설 — 캘리브레이션
(설계 문서 8장) 후 확정한다. **주의: 설계 문서 8장은 실데이터 전제인데, 런칭 전에는
그 데이터가 없다.** 데드락 방지를 위해 2단계로 나눈다: ① 런칭 전 — 합성/시뮬레이션
프로필(신규·소량·다량 기록)로 골든 케이스와 분포 sanity만 검증하고 초기값 그대로
출시, ② 런칭 후 — 실데이터 누적 시 설계 문서 8장 절차로 재캘리브레이션.

AI 리포트의 취약·추세 입력은 이 산출을 **그대로** 받는다 (별도 계산 금지).
`[충돌 C2]` 설계 문서 4.2가 수신 노드를 "Diagnostician"으로 지칭하나 v2 노드
어휘(Collector/Weakness Analyst)에 없다 — 6.3 어휘 검수 대상.

## 3. 생성 조건 상세

### 3.1 계획 기간

- `plan_days = min(7, exam_date - anchor)`, 시험일 제외.
- 7일 계획: 6일 학습 + 7일차 weekly_review 1개 (마지막 날은 평가 전용).
- 1~6일 계획: 압축 학습, weekly_review 없음.
- 경계 케이스 (SPEC 6장 확정, 구현 시 테스트 대상):
  - `exam_date - anchor = 1` → 1일 압축 학습만.
  - `exam_date == anchor` → **당일 압축 계획 1일** (생성 불가가 아님).
  - `exam_date < anchor` → 생성 불가.
  - `exam_date 없음` → 7일 계획.
  - anchor·exam_date의 시간대 기준은 Asia/Seoul snapshot, DB NOW() 기준
    (7장 위험 R1과 연결).

### 3.2 생성 차단·실패 조건

| 조건 | 상태 | 해소 |
|---|---|---|
| 후보 없음 / question pool 부족 | 생성 실패 | 기존 active 유지, 명시적 실패 기록 |
| 후보 부족 (고갈 전 단계) | 폴백 구성 | 4.3 폴백 ①→② 순차 적용, planSource 기록 |
| 진행 중 세션 존재 | BLOCKED (IN_PROGRESS_SESSION) | 세션 terminal 후 재개 |
| source plan 변경 | CANCELLED (SOURCE_PLAN_SUPERSEDED) | 새 job으로 대체 |
| 중복 요청 | 차단 | 멱등키·dedupe key |

- active 계획은 사용자당 최대 1개. finalize 시 partial unique index로 강제
  (7장 위험 R2 — index 적용 전까지 미구현 상태).

## 4. 우선순위 계산·배치 상세

### 4.1 priority 계산

```text
priority = clamp(weaknessScore·Wweak + predictionScore·Wprediction
                 + timeBurden·Wtime + trendBonus, 0, 1)
```

- weaknessScore: 2.1의 산출값 (감쇠 가중 표본 + Wilson 하한). 현행 raw wrongRate를
  이 값으로 교체한다.
- 가중치 세트(Wweak/Wprediction/Wtime)는 5.1 결정에 따라 **기간 구간별 프리셋**을
  versioned config에서 선택한다 (6.1 참조). 취약점 설계 6장이 "남은 기간별 가중치 표
  (short/medium/long) 현행 유지"라 하므로 **현행 표를 초기 프리셋으로 채택**하고,
  구간→세트 매핑만 versioned config로 옮긴다.
- trendBonus (취약점 설계 6장 초기값): WORSENING +0.08, IMPROVING −0.04,
  FLAT/UNKNOWN 0. 악화 가산 > 개선 감산 (손실 회피 비대칭). UNKNOWN 조합도
  후보에서 빼지 않는다 (bonus 0으로 포함).
- clamp는 **있음으로 확정** (SPEC 8장 수식에 명시, canonical 우선). 취약점 설계
  6장 수식에만 clamp가 빠져 있으므로 해당 문서에 추가하면 된다 (구 C3, 해소).

### 4.2 병합·배치 규칙

- weakness·prediction 후보는 **groupKeyId로만 병합**한다. 이름(문자열) 병합 금지 —
  동명이의 병합 사고 방지. groupKeyId는 `build_group_key_id` 한 곳에서만 생성한다
  (`|`·`=` 이스케이프 포함).
- **후보 포함 조건** (취약점 설계 6장): "오답 1건 이상" 폐기 →
  `status ≠ INSUFFICIENT AND weaknessScore > stable_threshold`.
  안정 조합·판단 불가 조합은 계획 후보에서 제외.
- 결정론 정렬 (SPEC 8장 확정): **priority DESC → weaknessScore DESC →
  predictionScore DESC → groupKeyId ASC**. 이후 round-robin 일자 배치.
  (오답노트의 tie-break 규칙(raw wrong DESC)은 오답노트 전용이며 Planner에
  적용하지 않는다 — 협조 요청서와 SPEC은 화면·용도가 다른 별개 규칙.)
- 같은 target은 같은 날 1회만 배치.
- 복습 offset: 학습일 +1일, +3일. +7일 복습은 현재 계획 범위를 넘으므로
  **다음 계획의 deferred candidate**로 넘긴다.
- 블록 어휘는 v2 2축으로 통일: `block_type(practice/review/weekly_review)` ×
  `focus_kind(weakness/prediction/mixed/carryover/extra)`. v1 어휘
  (`new_weakness`, `prediction_focus` 등)는 코드·문서 전체에서 금지.

### 4.3 후보 고갈 폴백 (취약점 설계 6장)

기본 조건(4.2, WEAK·NEUTRAL 포함)으로 후보가 부족하면 단계적으로 완화한다.

1. 폴백 ①: INSUFFICIENT 중 오답 존재 조합(`raw.wrong > 0`) 포함
2. 폴백 ②: 출제 예상만으로 구성

- 폴백 단계는 블록의 **planSource** 신규 필드에 기록한다
  (normal / fallback_any_wrong / fallback_prediction_only). 기존 블록에 없으면
  normal로 간주 (하위 호환).
- fallback_prediction_only 계획은 화면에 고지한다 ("아직 학습 기록이 부족해
  출제 예상 중심으로 구성했어요"류).
- 폴백과 생성 실패의 순서는 **SPEC 8장에 이미 확정돼 있다** (구 C1, 해소):
  기본 후보 → 폴백 ①(관찰 부족 중 오답 존재) → 폴백 ②(prediction-only) →
  그래도 후보 0이거나 pool 부족이면 생성 실패, 기존 active 유지.

## 5. 주간평가 blueprint 상세

### 5.1 규격 (기출 75·76·77회 3회 전수 조사 근거)

- 50문항 · 100점 · 80분.
- 배점: 1점×10 + 2점×30 + 3점×10 = 100점 (3회 모두 동일 확인).
- 배점 배치 경향(soft 참고): 1점은 선사·문화유산·기초 사실형, 3점은 순서 배열·
  연표 삽입·시기 판별형에 몰린다.

### 5.2 stratum·quota (서비스 era 10개, 단일 축)

기출 조사 7.4 표를 그대로 채택한다. stratum은 상호배타적 era 단일 키.

| era | quota | 재배분 후 평균 | 비고 |
|---|---:|---:|---|
| 선사시대 | 1 | 1.00 | 3회 고정 출제 |
| 고조선 | 1 | 0.67 | 최소 1 보장 |
| 초기국가 | 1 | 0.67 | 최소 1 보장 |
| 삼국시대 | 4 | 4.00 | |
| 남북국시대 | 3 | 3.33 | |
| 고려 | 9 | 9.33 | |
| 조선 | 9 | 9.00 | 3회 고정 (전기+후기 합산) |
| 개항기 | 7 | 7.00 | |
| 일제강점기 | 10 | 9.33 | 고조선·초기 반올림분 -1 흡수 |
| 현대 | 5 | 5.67 | 75회 관통 2건은 이례, 기본 5 |
| 합계 | 50 | | |

### 5.3 경계 규칙 (Questions.era 분류와 함께 고정)

- 후삼국(견훤·궁예·왕건) → 고려.
- 왜란·호란 → 조선.
- 지역사·시대 관통 소재 → 중심 시대로 귀속 (통합 era 없음. 예: 공주→삼국시대,
  개성→고려, 제주 4·3→현대).
- 이 규칙은 문항 생성·적재 시점의 분류 규칙이자 pool 검증의 전제다.
  blueprint 승인 문서에 경계 규칙을 함께 명문화한다.

### 5.4 soft 조건 (quota로 강제하지 않음)

- topic 축(사건·인물·정치·제도·문화·사회·군사·경제·사상종교·외교)과 난이도는
  stratum 내 2차 soft 조건. 교차 분포 충돌 방지 원칙 (SPEC 11장 `[가정]`).
- 인물 편중 방지: 기출에서 인물 중심 문항이 회당 8~12로 많으므로, era 내 선발 시
  같은 topic 연속 편중을 줄이는 soft 규칙을 둔다. 구체 임계값(예: era 내 동일
  topic 비율 상한)은 구현 시 config로 정하고 하드코딩하지 않는다.
- 주제 분포 참고치(기출 3회 평균): 정치 55~60%, 문화 15~20%, 경제 8~10%,
  사회 6~8%, 대외관계 6~8%. 조사 주제 → 서비스 topic 대응: 정치→정치·사건·인물·
  제도, 대외관계→외교·군사, 문화→문화·사상종교.

### 5.5 pool 검증 (blueprint 적용 선행조건)

1. Questions 테이블에 생성 문항 적재 완료.
2. era별 pool 조사 — `pool < quota × 여유배수`이면 **명시적 실패** (조용한 축소
   금지). 여유배수 값은 versioned config로 관리 (`[가정]` 권장 초기값 예: 3배,
   승인 시 확정).
3. 조사 SQL 초안:

```sql
SELECT era, COUNT(*) AS total,
       COUNT(*) FILTER (WHERE difficulty = 'high') AS high_cnt
FROM questions
GROUP BY era
ORDER BY total DESC;
```

4. Questions.era 값이 서비스 era 10개와 정확히 일치하는지 검증 (오타·미정의 값
   존재 시 실패).
5. 통과 후 versioned assessment blueprint로 승인해야 적용된다.

## 6. 결정사항별 실행 계획 (5.1~5.6)

각 결정을 작업 항목·선행조건·완료 기준으로 전개한다. 번호는 재검토 문서 기준.

### 6.1 기간별 전략 → 가중치 프리셋 (결정 A)

- 내용: 계획 기간은 7일 고정 유지. 생성 시점 `exam_date - anchor` 구간에 따라
  versioned config에서 가중치 세트(Wweak/Wprediction/Wtime)를 선택.
- 작업:
  1. 구간 정의 — SPEC 8장 예시 기준 **4구간**: 7일 이하 / 8~21일 / 22일 이상 /
     **exam_date 없음**. 구간 경계·개수는 config 스키마가 수용하도록 설계.
  2. 구간별 가중치 초기값 — **현행 short/medium/long 표를 초기 프리셋으로 채택**
     (취약점 설계 6장 "현행 유지" 근거). 단, SPEC 8장의 방향 원칙("짧은 구간일수록
     Wprediction↑, 긴 구간일수록 Wweak↑")과 현행 표가 일치하는지 코드 확인 필요.
     불일치 시 SPEC 방향이 우선. 시험 직전 half-life 단축 여부(취약점 설계 11장
     열어둔 결정)를 이 구간 체계에 통합할지 함께 결정.
  3. config 스키마에 `구간 → 가중치 세트` 매핑 추가, plan의 config_snapshot에
     선택된 세트를 기록 (결정론·재현성).
- 완료 기준: 같은 입력 + 같은 config version → 항상 같은 계획이 재현됨.

### 6.2 하루 시간 배분 비율 → 폐기 (결정 A)

- 내용: 50/25/15/10 비율은 SUPERSEDED. 해설·오답 시간은 문항당 `unit_seconds`
  버퍼로, 복습은 독립 review 블록으로 흡수.
- 작업:
  1. ~~unit_seconds 산정 근거 문서화~~ — **불필요 확인.** SPEC 9장에 이미
     `unit_seconds = clamped_average_solve_seconds + review_seconds`로 명시됨.
  2. 잔존 참조 검색 — 코드·문서에서 비율(50%/25% 등) 참조가 남아 있으면 제거.
- 완료 기준: 비율 개념 참조 0건, estimated_minutes 계산이 unit_seconds 기반으로만
  동작.

### 6.3 블록 어휘 통일 (결정: v2 2축 전면 통일)

- 내용: `block_type` × `focus_kind` 2축으로 통일. 구문서 3종은 삭제 완료.
- 작업:
  1. 이관 내용 검수 — 구문서에서 SPEC/AI_WORKFLOW로 이관된 7개 항목(기간별
     가중치 프리셋, 우선순위 4그룹 표, 분산 배치 원칙, 서비스 책임 분리,
     과거 날짜 차단 시나리오, 주간평가-진단평가 연결 세부, 나의 학습실 표시 규칙)이
     v2 어휘로 정확히 반영됐는지 원문 대조.
  2. 코드 내 v1 어휘(`new_weakness`, `review`(단독 유형), `prediction_focus`)
     잔존 여부 grep 검사 — DB enum·API 응답 필드 포함.
  2-1. `[충돌 C2]` 취약점 설계 4.2의 "Diagnostician" 명칭 정리 — v2 노드 어휘
     (Collector/Weakness Analyst)에 없는 이름. 해당 문서의 표현을 v2 노드명으로
     수정하거나, AI_WORKFLOW에 별칭임을 명시.
  3. DB 컬럼·enum이 2축을 지원하는지 확인 (7장 R2의 스키마 변경 요청과 연동).
- 완료 기준: canonical 2개 문서와 코드에서 v1 어휘 0건.

### 6.4 blueprint 승인 (결정: 기출 근거 초안 확정)

- 내용: 5장 전체가 이 결정의 상세다.
- 작업 순서: 문항 적재 → pool 검증(5.5) → versioned blueprint 승인 → 적용.
- 완료 기준: 승인된 blueprint version이 존재하고, 주간평가 생성이 해당 version을
  참조하며, pool 부족 시 명시적 실패가 동작.

### 6.5 SUPERSEDED 표현 정리 (해소 완료)

- 구문서 삭제로 충돌 표현 소멸. 실행 방식 단일 기준은 AI_WORKFLOW 1·6장
  (durable queue + polling worker).
- 잔여 작업 없음. 단, 6.3-1의 이관 검수 시 "이벤트 트리거" 표현이 이관본에
  섞여 들어오지 않았는지만 확인.

### 6.6 알려진 위험 → 구현 이행 (재검토 불필요, 추적만)

정의는 canonical에 확정. 7장 위험 추적표로 관리한다.

## 7. 위험 추적표

| ID | 위험 | 영향 | 확정된 정의 | 이행 상태 | 추적 문서 |
|---|---|---|---|---|---|
| R1 | TIME_ZONE=UTC vs timezone.localdate | KST 자정 경계 9시간 어긋남 → plan_days 오계산 | SPEC 6장: Asia/Seoul snapshot, DB NOW() 기준 | 미구현 | IMPLEMENTATION_STATUS |
| R2 | active partial unique index 부재 (코드는 존재 가정) | active 계획 2개 생성 가능 | DB_스키마_변경_요청서로 요청 중 | 적용 확인 전까지 "미구현" 유지 | IMPLEMENTATION_STATUS |
| R3 | 새 세션 시작 시 in_progress 세션 DELETE | plan 연결 세션까지 삭제 | CONTRACTS 명시적 취소 API로 대체 | 요청 중 | IMPLEMENTATION_STATUS |
| R4 | 자동 Planner 부재 | 주간평가 후 다음 계획 미생성 | AI_WORKFLOW 6장 worker | CUTOVER Phase 5에서 구현 | CUTOVER |

- R1·R2는 finalize 트랜잭션의 정합성 전제이므로 **6.4(blueprint 적용)보다 먼저**
  해소돼야 한다. R2 index 미적용 상태에서 v2 finalize를 켜면 안 된다.

## 8. 실행 순서 체크리스트

의존 관계 기준 순서. 각 항목은 완료 기준(6장) 충족 시 체크.

1. [ ] R2: active partial unique index + 2축 어휘 DB 스키마 적용 확인 (선행)
2. [ ] R1: 시간대 처리 SPEC 6장대로 구현
3. [ ] R3: 세션 명시적 취소 API 반영
4. [ ] 6.3: 어휘 통일 검수 (이관 검수 + 코드 grep)
5. [ ] 6.1: 가중치 프리셋 구간·초기값 승인 → versioned config 반영
6. [ ] 6.2: unit_seconds 근거 명시 + 비율 참조 제거
6-1. [ ] 취약점 개선 구현 (설계 문서 10장 1~7단계: weakness.py + 골든 테스트 →
    캘리브레이션 → 화면 교체 → 학습계획 결합) — 이 계획의 4장은 5단계
    (priorityScore·폴백) 완료를 전제로 한다
6-2. [ ] 충돌 해소: C2 Diagnostician 명칭 정리 + 취약점 설계 6장 수식에 clamp 추가
    (C1·C3는 SPEC 대조로 이미 해소 — 4.1·4.3 참조)
7. [ ] Questions 문항 적재
8. [ ] 5.5: era pool 검증 (여유배수 config 확정 포함)
9. [ ] 6.4: versioned blueprint 승인·적용
10. [ ] R4: worker 구현 (CUTOVER Phase 5) → 자동 Planner 가동
11. [ ] E2E 검증 — 두 갈래로 분리 (LLM 구간은 비결정적이라 재현성 검증 불가):
    - 결정론 구간: 같은 입력/config/anchor → 같은 PlanDraft (SPEC 7장 재현성)
    - LLM 구간: 리포트가 structured schema·guard를 통과하고 READY 도달하는지만
      검증 (문장 동일성은 요구하지 않음)

## 9. 미확정 항목 (승인 필요)

| 항목 | 현재 상태 | 결정 주체 |
|---|---|---|
| 가중치 프리셋 현행 표 ↔ SPEC 방향 일치 여부 (6.1) | 코드 미확인 | 코드 대조 후 확정 |
| pool 여유배수 값 (5.5) | 미정 (예: 3배 제안) | 승인 필요 |
| 인물 편중 soft 규칙 임계값 (5.4) | 미정, config 관리만 확정 | 구현 시 확정 |
| C2: Diagnostician 명칭 (2.1) | v2 노드 어휘와 불일치 | 취약점 설계 문서 수정 필요 |
| 취약점 파라미터 확정값 (2.1) | 초기값 전부 가설, 런칭 전 합성 데이터 sanity → 런칭 후 재캘리브레이션 | 캘리브레이션 후 확정 |
| 시험 직전 half-life 단축 (6.1) | 열어둔 결정 (취약점 설계 11장) | 캘리브레이션 후 결정 |

### 9.1 해소 기록 (2026-07-17 SPEC·AI_WORKFLOW 대조)

| 구분 | 항목 | 결론 |
|---|---|---|
| 오류 정정 | Planner tie-break (4.2) | SPEC 8장: priority → weakness → prediction → groupKeyId. 오답노트 규칙 오적용 정정 |
| 오류 정정 | exam_date == anchor (3.1) | 당일 압축 1일 (생성 불가 아님). exam 없음 → 7일 추가 |
| 오류 정정 | Recommender 실패 (2장) | soft-fail — 추천만 비우고 리포트 생성. hard-fail은 Writer·Renderer만 |
| 충돌 해소 | 구 C1 폴백-실패 순서 | SPEC 8장에 이미 확정. 충돌 아님 |
| 충돌 해소 | 구 C3 clamp 유무 | SPEC 수식에 clamp 있음. 취약점 설계 문서만 수정 |
| 가정 해소 | unit_seconds 정의 | SPEC 9장에 명시 (6.2 작업 1 불필요) |
| 가정 해소 | worker 재시도·타임아웃 | AI_WORKFLOW 6·7·14장 (lease 10분, attempt 3/2, backoff) |
| 가정 해소 | guard 재작성 상한 | recommend 1회, write 2회 (AI_WORKFLOW 9·14장) |
| 가정 해소 | 가중치 구간 | SPEC 8장 4구간 (exam_date 없음 포함) |
| 현실화 | 캘리브레이션 (2.1) | 런칭 전 합성 데이터 / 런칭 후 실데이터 2단계로 분리 |
| 현실화 | E2E 재현성 (8장 11) | 결정론 구간만 재현성, LLM 구간은 스키마·READY 검증 |

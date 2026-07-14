# 학습계획 상세 흐름도

`app/analytics/service/studyplan.py`의 학습계획 생성·보장·진행률·블록 조작과 `display.py`의 표시 가공을 세부 함수 단위로 정리했다. 각 노드는 `함수명 — 한글 설명` 형식으로 표기했다. 마이페이지 전체 흐름은 `mypage_service_flow.md`를 참조한다.

## 1. 오늘 계획 보장 (마이페이지 진입 시)

```mermaid
flowchart TD
    start["ensure_today_study_plan()<br/>— 오늘 계획 보장"] --> userLock["lock_study_plan_user()<br/>— 사용자 단위 생성·교체 직렬화"]
    userLock --> lock["get_active_study_plans(lock=True)<br/>— active 계획 잠금 조회"]
    lock --> has{"active 계획 있음?"}
    has -->|"없음"| create["create_study_plan()<br/>— 새 계획 생성"]
    has -->|"있음"| expired{"기간 만료?<br/>(오늘 > end_date)"}
    expired -->|"예"| none["아무것도 안 함 (None 반환)<br/>— 만료 표시·새 계획 유도는 화면 담당"]
    expired -->|"아니오"| carry["carry_over_incomplete_past_blocks_to_today()<br/>— 과거 미완료 블록 오늘로 이월"]
    carry --> changed{"변경 있음?"}
    changed -->|"예"| keep["keep_study_plan_period_boundary_days()<br/>— 기간 경계 날짜 보존"]
    keep --> save["save_study_plan_items()<br/>— 저장"]
    changed -->|"아니오"| empty{"오늘 블록 공백이며<br/>오늘 수정 이력 없음?"}
    empty -->|"예"| create
    empty -->|"아니오"| none
    create --> content{"생성 블록 있음?"}
    content -->|"없음"| unavailable["StudyPlanGenerationUnavailable<br/>— 기존 계획 유지 · 빈 계획 저장 금지"]
    content -->|"있음"| created["새 active 계획 저장"]
```

> 이월 규칙: 완료·주간평가 블록 제외, 복습 블록은 같은 대상의 다음 복습 블록 도달 전까지만, blockId 유지. 오늘 블록 공백은 같은 날 반복 생성을 막으면서 새 계획으로 복구한다. 상세는 `study_plan_policy.md`의 "미완료 블록 이월 정책"·"오늘 계획 동기화 상태 분기" 참조. 주간평가 완료/미응시 구분은 `SolveSessions.review_type` 도입 후 추가한다.

## 2. 학습계획 생성

```mermaid
flowchart TD
    c["create_study_plan()<br/>— 계획 생성 진입"] --> gen{"상세 계획이<br/>전달됐나?"}
    gen -->|"아니오 (자동 생성)"| build["build_user_study_plan()<br/>— 맞춤 계획 자동 생성"]
    gen -->|"예 (직접 전달)"| prep

    subgraph B["build_user_study_plan — 자동 생성 내부"]
      direction TB
      p1["get_user_study_info / get_remaining_days /<br/>get_daily_available_minutes<br/>— 프로필·남은일수·하루 가용시간"]
      p2["get_composite_weak_targets()<br/>— 복합 취약 항목 (weakness.py)"]
      p3["get_predicted_targets()<br/>— 출제 예상 항목 (prediction.py)"]
      p4["build_priority_targets()<br/>— 우선순위 점수 계산"]
      p5{"remaining_days < 7?"}
      p6["압축 계획<br/>— 시험 전 날짜만 취약 영역 학습 · 주간평가 없음"]
      p7["기본 계획<br/>— 6일 학습 + 1일 주간평가"]
      p1 --> p2 --> p3 --> p4 --> p5
      p5 -->|"예"| p6
      p5 -->|"아니오"| p7
    end
    build --> B
    B --> prep["prepare_study_plan_items()<br/>— 기간·완료율 정리"]
    prep --> tx["트랜잭션: archive_study_plan()<br/>기존 active 보관 → 새 active row 생성"]
    tx --> snap["create_study_plan_base_snapshot()<br/>— 생성 시점 분석 스냅샷 저장"]
    snap --> ser["serialize_study_plan()<br/>— 응답 형태로 직렬화"]
```

## 3. 진행률 계산 (기록 기반)

```mermaid
flowchart TD
    prog["calculate_record_based_plan_progress()<br/>— 실제 기록으로 달성률 계산"] --> recs["get_plan_progress_records()<br/>— 완료 세션의 SolveRecords 조회"]
    recs --> loop["각 블록 순회"]
    loop --> t{"blockType?"}
    t -->|"review"| rv["build_review_block_progress()<br/>— 복습: isCompleted 기준"]
    t -->|"weekly_review"| wr["build_weekly_review_block_progress()<br/>— 주간평가: 목표 문항 기준"]
    t -->|"일반 학습"| nm["count_block_matched_records()<br/>— blockId 연결 기록 수 집계"]
    rv --> sum["target/achieved 합산<br/>→ completionRate (달성률)"]
    wr --> sum
    nm --> sum
```

진행률은 날짜·분류 추정이 아니라 `SolveRecords.studyplan_id + study_plan_block_id`로 해당 blockId에 직접 연결된 완료 기록만 센다.

## 4. 블록 조작

```mermaid
flowchart TD
    subgraph done["완료 처리"]
      direction TB
      k1["complete_study_plan_block / _by_id<br/>— active 계획 잠금"] --> k2["set_study_plan_block_completion()<br/>→ update_study_plan() — 완료 저장"]
    end
    subgraph del["삭제"]
      direction TB
      d1["delete_study_plan_block()<br/>— active 계획 잠금"] --> d2["삭제 한도 확인<br/>(daily_delete_limit)"]
      d2 --> d3["increase_study_plan_day_delete_count()<br/>— 그날 삭제 횟수 증가"]
      d3 --> d4["refill_deleted_plan_block()<br/>— 우선순위 높은 블록으로 보충"]
    end
    subgraph add["추가"]
      direction TB
      a1["add_extra_study_plan_block()<br/>— 추가 학습 블록 요청"] --> a2["build_extra_study_plan_block()<br/>→ insert_extra_study_block() — 블록 삽입"]
    end
```

- 완료 처리는 멱등: 이미 완료된 블록을 다시 완료해도 같은 상태를 유지한다.
- 삭제·완료는 트랜잭션 안에서 active 계획 행을 잠그며 archived 계획 요청은 거부한다.
- 주간평가 블록은 삭제 대상에서 제외한다.
- 자동 이월은 삭제 횟수에 포함하지 않는다.

## 5. 표시용 가공 (display.py)

```mermaid
flowchart TD
    bp["build_planner_summary()<br/>— 달력 표시 데이터 생성"] --> byd["plans_by_date 구성<br/>— 날짜별 블록 정리 (review 제외)"]
    byd --> cal["build_calendar_progress_by_date()<br/>calculate_date_progress_percent()<br/>— 날짜별 진행률 색상"]
    byd --> cs["canStart · 상태 라벨 계산<br/>— 오늘=문제풀기 / 과거=미달성 / 미래=예정<br/>주간평가 미응시=미응시"]
    byd --> recovery["복구 조건 판정<br/>— 미응시 만료 또는 미완료 시간이 하루 가용시간의 2배 초과"]
    byd --> sel["get_default_planner_selected_key()<br/>— 기본 선택 날짜 결정"]
    cal --> out["planner_summary<br/>— 달력 · 오늘 항목 · 모달 데이터"]
    cs --> out
    recovery --> out
    sel --> out
```

주간평가 블록은 시작 라벨이 "주간 평가 시작"이며 `/diagnosis/api/start/` 흐름으로 연결된다(일반 학습 블록은 "문제 풀기" → 문제풀이 API).

정상 주간평가 완료는 수동 재생성 버튼 조건이 아니다. 주간 리포트가 `READY`가 되면 analytics worker가 Planner를 자동 실행하고, Planner finalize 트랜잭션에서 기존 active 계획을 archived 처리한 뒤 다음 계획을 active로 저장한다.

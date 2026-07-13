# 마이페이지 서비스 흐름도

`GET /analytics/mypage` 요청에서 view가 서비스 함수를 어떤 순서로 호출하고 각 파일이 어떤 역할을 맡는지 정리한 흐름도다. view(`views.mypage`)는 **호출 순서만** 담당하고, 실제 DB 조회·계산·표시 가공은 service 계층이 맡는다. (근거: `app/analytics/views.py:53`)

각 노드는 `함수명 — 한글 설명` 형식으로 표기했다.

## 전체 흐름

```mermaid
flowchart TD
    req["GET /analytics/mypage<br/>views.mypage() — 마이페이지 진입"] --> ensure

    subgraph view["view 계층 — 호출 순서 담당 (views.py)"]
      direction TB
      ensure["ensure_today_study_plan()<br/>— 오늘 계획 동기화·공백 복구"]
      getplan["get_study_plan_info()<br/>— 현재 active 계획 + 진행률 조회"]
      planner["build_planner_summary()<br/>— 달력·플래너 표시 데이터 생성"]
      wrongtype["build_wrong_type_summary()<br/>— 오답 유형 요약"]
      weak["build_weakness_summary()<br/>— 취약 항목 요약"]
      learning["build_learning_summary()<br/>— 주간 학습 현황"]
      diagnosis["build_diagnosis_comparison_summary()<br/>— 진단평가 비교"]
      dday["build_d_day_label()<br/>— 시험 D-day"]
      ctx["context 구성 → mypage.html 렌더"]
      ensure --> getplan --> planner --> wrongtype --> weak --> learning --> diagnosis --> dday --> ctx
    end

    subgraph svc["service 계층 — DB조회·계산·표시 (service/)"]
      direction TB
      s_studyplan["studyplan.py<br/>— 계획 조회·생성·진행률"]
      s_display["display.py<br/>— 화면표시용 가공"]
      s_mypage["mypage.py<br/>— 요약·비교·D-day"]
      s_weak["weakness.py<br/>— 취약 판정"]
      s_analytics["analytics.py<br/>— 집계 요약"]
    end

    ensure -.->|"active 없으면 create_study_plan<br/>빈 결과는 저장하지 않음"| s_studyplan
    getplan -.->|"serialize_study_plans_with_progress"| s_studyplan
    planner -.-> s_display
    wrongtype -.-> s_mypage
    weak -.-> s_weak
    learning -.-> s_mypage
    diagnosis -.-> s_mypage
    dday -.-> s_mypage

    subgraph db["데이터"]
      direction LR
      DB1[("StudyPlanMypage<br/>— 학습계획 저장")]
      DB2[("SolveSessions / SolveRecords<br/>— 풀이 세션·기록")]
      DB3[("Analytics<br/>— 분석 스냅샷")]
    end
    s_studyplan --> DB1
    s_weak --> DB2
    s_analytics --> DB3
    s_mypage --> DB2
```

## context에 담기는 표시 데이터

| context 키 | 생성 함수 | 한글 설명 | 파일 |
|---|---|---|---|
| `planner_summary` / `planner_data` | `build_planner_summary` | 달력·플래너 표시 데이터 | display.py |
| `weakness_summary` | `build_weakness_summary` | 취약 항목 요약 | mypage.py → weakness.py |
| `wrong_type_summary` | `build_wrong_type_summary` | 오답 유형 요약 | mypage.py |
| `learning_summary` | `build_learning_summary` | 학습 현황(연속 학습일 등) | mypage.py |
| `diagnosis_comparison` | `build_diagnosis_comparison_summary` | 진단평가 전/후 비교 | mypage.py |
| `d_day_label` | `build_d_day_label` | 시험 D-day 라벨 | mypage.py |

## 마이페이지에서 발생하는 학습계획 액션 (POST)

마이페이지 화면에서 사용자가 계획을 조작하면 별도 view가 각 서비스 함수를 호출한다.

```mermaid
flowchart LR
    u["사용자 조작"] --> c["create_study_plan_view<br/>→ create_study_plan() — 새 계획 생성"]
    u --> d["delete_study_plan_block_view<br/>→ active 계획 잠금 후 블록 삭제"]
    u --> k["complete_study_plan_block_view<br/>→ active 계획 잠금 후 블록 완료"]
    u --> a["add_extra_study_plan_block_view<br/>→ add_extra_study_plan_block() — 블록 추가"]
    c --> save[("StudyPlanMypage 갱신")]
    d --> save
    k --> save
    a --> save
```

세부 학습계획 생성·진행률·블록 조작 로직은 `study_plan_flow.md`를 참조한다.

정상 주간평가 완료는 위 수동 `create_study_plan_view`를 사용하지 않는다. 주간 AI 리포트가 `READY`가 되면 analytics worker가 Planner를 자동 enqueue하고, 다음 계획 생성 finalize 트랜잭션에서 기존 active 계획을 교체한다. 수동 생성 버튼은 미응시 만료·과도한 미완료 누적 같은 예외 복구에만 사용한다.

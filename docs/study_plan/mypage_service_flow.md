# 마이페이지 서비스 흐름도

`GET /analytics/mypage` 요청에서 view가 서비스 함수를 어떤 순서로 호출하고 각 파일이 어떤 역할을 맡는지 정리한 흐름도다. view(`views.mypage`)는 **호출 순서만** 담당하고, 실제 DB 조회·계산·표시 가공은 service 계층이 맡는다. (근거: `app/analytics/views.py:53`)

각 노드는 `함수명 — 한글 설명` 형식으로 표기했다.

## 전체 흐름

```mermaid
flowchart TD
    req["GET /analytics/mypage<br/>views.mypage() — 마이페이지 진입"] --> ensure

    subgraph view["view 계층 — 호출 순서 담당 (views.py)"]
      direction TB
      ensure["ensure_today_study_plan()<br/>— 오늘 계획 없으면 생성"]
      getplan["get_study_plan_info()<br/>— 현재 active 계획 + 진행률 조회"]
      planner["build_planner_summary()<br/>— 달력·플래너 표시 데이터 생성"]
      wrongtype["build_wrong_type_summary()<br/>— 오답 유형 요약"]
      weak["build_weakness_summary()<br/>— 취약 항목 요약"]
      valid["build_mypage_summary_validation()<br/>— 요약 검증 + 로그 기록"]
      ctx["context 구성 → mypage.html 렌더"]
      ensure --> getplan --> planner --> wrongtype --> weak --> valid --> ctx
    end

    subgraph svc["service 계층 — DB조회·계산·표시 (service/)"]
      direction TB
      s_studyplan["studyplan.py<br/>— 계획 조회·생성·진행률"]
      s_display["display.py<br/>— 화면표시용 가공"]
      s_mypage["mypage.py<br/>— 요약·비교·D-day"]
      s_weak["weakness.py<br/>— 취약 판정"]
      s_analytics["analytics.py<br/>— 집계 요약"]
    end

    ensure -.->|"active 없으면 create_study_plan"| s_studyplan
    getplan -.->|"serialize_study_plans_with_progress"| s_studyplan
    planner -.-> s_display
    wrongtype -.-> s_mypage
    weak -.-> s_weak
    ctx -.->|"analytics_summary · build_learning_summary ·<br/>build_diagnosis_comparison_summary · build_d_day_label"| s_mypage
    ctx -.-> s_analytics

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
| `study_plan` | `get_study_plan_info` | 현재 active 학습계획 + 진행률 | studyplan.py |
| `planner_summary` / `planner_data` | `build_planner_summary` | 달력·플래너 표시 데이터 | display.py |
| `weakness_summary` | `build_weakness_summary` | 취약 항목 요약 | mypage.py → weakness.py |
| `wrong_type_summary` | `build_wrong_type_summary` | 오답 유형 요약 | mypage.py |
| `learning_summary` | `build_learning_summary` | 학습 현황(연속 학습일 등) | mypage.py |
| `diagnosis_comparison` | `build_diagnosis_comparison_summary` | 진단평가 전/후 비교 | mypage.py |
| `d_day_label` | `build_d_day_label` | 시험 D-day 라벨 | mypage.py |
| `analytics` | `analytics_summary` | 전체 집계 요약 | analytics.py |

## 마이페이지에서 발생하는 학습계획 액션 (POST)

마이페이지 화면에서 사용자가 계획을 조작하면 별도 view가 각 서비스 함수를 호출한다.

```mermaid
flowchart LR
    u["사용자 조작"] --> c["create_study_plan_view<br/>→ create_study_plan() — 새 계획 생성"]
    u --> d["delete_study_plan_block_view<br/>→ delete_study_plan_block() — 블록 삭제"]
    u --> k["complete_study_plan_block_view<br/>→ complete_study_plan_block() — 블록 완료"]
    u --> a["add_extra_study_plan_block_view<br/>→ add_extra_study_plan_block() — 블록 추가"]
    c --> save[("StudyPlanMypage 갱신")]
    d --> save
    k --> save
    a --> save
```

세부 학습계획 생성·진행률·블록 조작 로직은 `study_plan_flow.md`를 참조한다.

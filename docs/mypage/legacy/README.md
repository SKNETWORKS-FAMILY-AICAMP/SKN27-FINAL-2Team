# 마이페이지·학습계획 레거시 문서 지도

상태: ARCHIVED — 참고 전용  
보관일: 2026-07-19  
현재 기준 문서: [학습계획·AI 주간리포트 통합설계](../학습계획_AI주간리포트_통합설계.md)

이 디렉터리는 통합설계 이전 문서를 원문 중심으로 보관한다. 아래 우선순위와 작업 원칙은 당시 문서 체계를 설명하는 기록이며 현재 구현 기준으로 사용하지 않는다.

## 당시 Canonical v2 문서

1. [study_plan/SPEC.md](study_plan/SPEC.md)
   - 학습계획 도메인 정책, 데이터 모델, 상태 전이, 생성 규칙, 불변조건
2. [study_plan/CONTRACTS.md](study_plan/CONTRACTS.md)
   - Python 공개 경로, HTTP 요청·응답, 블록 연결, 오류·멱등성 계약
3. [study_plan/AI_WORKFLOW.md](study_plan/AI_WORKFLOW.md)
   - 주간 리포트 AI workflow, PostgreSQL job queue, worker, 비용·보안·관측성
4. [study_plan/CUTOVER.md](study_plan/CUTOVER.md)
   - v1 보존, side-by-side 구현, shadow/canary, 전환·롤백·삭제 조건
5. [study_plan/IMPLEMENTATION_STATUS.md](study_plan/IMPLEMENTATION_STATUS.md)
   - 현재 코드와 DB에 실제 반영된 사실만 기록

우선순위는 SPEC·CONTRACTS가 가장 높고, AI_WORKFLOW·CUTOVER가 이를 구체화한다. IMPLEMENTATION_STATUS는 목표 정책을 정하지 않고 현재 사실만 기록한다.

## Reference·legacy 문서

다음 문서는 배경과 기존 동작 확인용이다. canonical v2 문서와 충돌하면 v2 문서를 따른다.

- study_plan_policy.md: v1 정책과 후속 제안이 섞인 기존 문서
- study_plan_flow.md: 현재 함수 중심 흐름과 미구현 미래 흐름이 섞인 문서
- weekly_review_ai_report_plan.md: 초기 AI 리포트 설계
- 학습계획_설계.md: 초기 계획 생성 설계
- study_plan_progress_api.md: 현재 v1 진행률 연동 참고
- mypage_service_flow.md: 현재 마이페이지 서비스 흐름 참고
- 주간평가_협조_요청서.md: 초기 앱 간 협조 요청
- 취약점_분석_개선_설계.md: 취약점 입력 산출 규칙의 상세 배경

## 작업 원칙

- v2가 acceptance gate를 통과하기 전에는 기존 studyplan.py를 삭제하지 않는다.
- 기존 blockId와 SolveRecords의 studyplan_id·study_plan_block_id를 재발급하거나 임의로 다시 연결하지 않는다.
- DB 변경은 cutover 전까지 additive 방식으로만 적용한다.
- 현재 구현 설명과 목표 설계를 같은 문단에 섞지 않는다.
- 문서의 미결정 사항은 코드에서 임의로 결정하지 않는다.
- 구현은 SPEC → CONTRACTS → AI_WORKFLOW → CUTOVER 순서로 검토한 뒤 시작한다.


"""주간 리포트 그래프가 어느 단계에서 대체 문구로 빠지는지 찾는다.

generate_graph_report_content 는 실패를 삼키고 대체 문구를 돌려주므로
원인이 안 보인다. 여기서는 그래프를 직접 invoke 해 최종 state 를 그대로 찍는다.

확인용 일회용 스크립트다. 커밋하지 말 것.
실행: python probe_weekly_graph.py   (app 폴더에서)
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from analytics.models import StudyPlanMypage
from analytics.service.weekly_report.agents import LangGraphWeeklyReportAgentSuite
from analytics.service.weekly_report.config import get_weekly_report_config
from analytics.service.weekly_report.graph import build_weekly_report_graph


STUDY_PLAN_ID = 1
EVIDENCE_SECTIONS = (
    "strengths",
    "priorityImprovements",
    "conceptWeaknesses",
    "examTrends",
    "timeSummary",
    "confusionPatterns",
    "assessment",
    "comparison",
    "planProgress",
)


def load_result() -> dict:
    plan = StudyPlanMypage.objects.filter(studyplan_id=STUDY_PLAN_ID).first()
    if plan is None or not isinstance(plan.weekly_report_data, dict):
        print("리포트를 찾을 수 없습니다. studyplan_id =", STUDY_PLAN_ID)
        sys.exit(1)
    return dict(plan.weekly_report_data.get("result") or {})


def print_available_evidence(result: dict) -> None:
    print("=" * 66)
    print("[1] 인용 가능한 근거 ID")
    for section in EVIDENCE_SECTIONS:
        value = result.get(section)
        if isinstance(value, list):
            ids = [str(item.get("evidenceId")) for item in value if isinstance(item, dict)]
            print(f"  {section:<20} {len(value)}건  {ids}")
        elif isinstance(value, dict):
            print(f"  {section:<20} 1건  ['{value.get('evidenceId')}']")
        else:
            print(f"  {section:<20} 없음")


def print_final_state(result: dict) -> None:
    print("=" * 66)
    print("[2] 그래프 직접 실행")
    config = get_weekly_report_config()
    graph = build_weekly_report_graph(LangGraphWeeklyReportAgentSuite(config), config)
    final_state = graph.invoke(
        {
            "report_type": "weekly",
            "result": deepcopy(result),
            "revision_feedback": [],
            "revision_count": 0,
            "failed_stage": None,
        }
    )

    print("  failed_stage     :", final_state.get("failed_stage"))
    print("  revision_count   :", final_state.get("revision_count"))
    print("  guard_errors     :", final_state.get("guard_errors"))
    print("  revision_feedback:", final_state.get("revision_feedback"))
    critic = final_state.get("critic_result")
    if critic:
        print("  critic           :", critic)
    draft = final_state.get("draft")
    if draft:
        print("  draft            :", json.dumps(draft, ensure_ascii=False)[:600])
    content = final_state.get("content") or {}
    print("  fallbackUsed     :", content.get("fallbackUsed"))


def main() -> None:
    result = load_result()
    print_available_evidence(result)
    print_final_state(result)
    print("=" * 66)


main()

"""주간 리포트 AI 실패 원인 진단.

워커와 그래프는 실패를 삼키고 대체 문구로 넘어가도록 설계돼 있어서
로그만으로는 원인이 안 보인다. 이 스크립트는 예외를 그대로 터뜨린다.
확인용 일회용 스크립트다. 커밋하지 말 것.

실행: python probe_weekly_agent.py   (app 폴더에서)
"""

from __future__ import annotations

import os
import sys
import traceback

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from analytics.models import StudyPlanMypage
from analytics.service.weekly_report.agents import LangGraphWeeklyReportAgentSuite
from analytics.service.weekly_report.config import get_weekly_report_config
from analytics.service.weekly_report.graph import generate_graph_report_content


STUDY_PLAN_ID = 1


def print_environment(config) -> None:
    api_key = os.getenv("OPENAI_API_KEY") or ""
    print("=" * 60)
    print("[1] 환경")
    print("  config.model          :", config.model)
    print("  OPENAI_CHAT_MODEL     :", os.getenv("OPENAI_CHAT_MODEL"))
    print("  WEEKLY_REPORT_LLM_MODEL:", os.getenv("WEEKLY_REPORT_LLM_MODEL"))
    print("  OPENAI_API_KEY 길이   :", len(api_key))
    print("  OPENAI_API_KEY 앞자리 :", api_key[:8] if api_key else "(없음)")
    print("  OPENAI_BASE_URL       :", os.getenv("OPENAI_BASE_URL") or "(기본값)")


def load_result() -> dict:
    plan = StudyPlanMypage.objects.filter(studyplan_id=STUDY_PLAN_ID).first()
    if plan is None:
        print("  학습계획을 찾을 수 없습니다. studyplan_id =", STUDY_PLAN_ID)
        sys.exit(1)
    report = plan.weekly_report_data
    if not isinstance(report, dict):
        print("  weekly_report_data 가 객체가 아닙니다:", type(report))
        sys.exit(1)
    print("  저장된 상태          :", report.get("status"))
    print("  attemptCount         :", (report.get("worker") or {}).get("attemptCount"))
    print("  lastError            :", (report.get("worker") or {}).get("lastError"))
    return dict(report.get("result") or {})


def probe_single_agent(config, result: dict) -> None:
    """분석 에이전트 하나만 직접 부른다. 예외를 잡지 않는다."""
    print("=" * 60)
    print("[3] 분석 에이전트 직접 호출 (예외 그대로 노출)")
    suite = LangGraphWeeklyReportAgentSuite(config)
    analysis = suite.analyze("weekly", result)
    print("  성공. summary =", getattr(analysis, "summary", None))


def probe_graph(config, result: dict) -> None:
    print("=" * 60)
    print("[4] 그래프 전체 호출 (실패해도 대체 문구로 넘어감)")
    content = generate_graph_report_content(result, config=config, report_type="weekly")
    print("  fallbackUsed :", content.get("fallbackUsed"))
    print("  validation   :", content.get("validation"))
    comment = content.get("comment") or {}
    print("  comment      :", comment.get("text"))


def main() -> None:
    config = get_weekly_report_config()
    print_environment(config)

    print("=" * 60)
    print("[2] 저장된 리포트")
    result = load_result()

    try:
        probe_single_agent(config, result)
    except Exception:
        print("  실패 — 아래가 진짜 원인이다.")
        traceback.print_exc()

    probe_graph(config, result)
    print("=" * 60)


main()

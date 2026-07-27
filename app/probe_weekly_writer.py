"""작성 에이전트가 왜 실패하는지 예외를 그대로 본다.

그래프는 WRITER_CALL_OR_SCHEMA_ERROR 로 뭉뚱그리므로 원인이 안 보인다.
여기서는 분석 → 코칭 → 작성 순서로 직접 부르고 예외를 잡지 않는다.
토큰 한도가 원인인지 확인하려고 한도를 올려 한 번 더 시도한다.

확인용 일회용 스크립트다. 커밋하지 말 것.
실행: python probe_weekly_writer.py   (app 폴더에서)
"""

from __future__ import annotations

import os
import sys
import traceback
from copy import deepcopy
from dataclasses import replace

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from analytics.models import StudyPlanMypage
from analytics.service.weekly_report.agents import LangGraphWeeklyReportAgentSuite
from analytics.service.weekly_report.config import get_weekly_report_config


STUDY_PLAN_ID = 1


def load_result() -> dict:
    plan = StudyPlanMypage.objects.filter(studyplan_id=STUDY_PLAN_ID).first()
    if plan is None or not isinstance(plan.weekly_report_data, dict):
        print("리포트를 찾을 수 없습니다. studyplan_id =", STUDY_PLAN_ID)
        sys.exit(1)
    return dict(plan.weekly_report_data.get("result") or {})


def try_write(config, result: dict, label: str) -> bool:
    print("=" * 66)
    print(f"[{label}] writer_maximum_tokens = {config.writer_maximum_tokens}")
    suite = LangGraphWeeklyReportAgentSuite(config)
    try:
        analysis = suite.analyze("weekly", deepcopy(result))
        print("  분석 OK")
        coaching = suite.coach("weekly", deepcopy(result), analysis.model_dump(by_alias=True))
        print("  코칭 OK, 제안", len(coaching.recommendations), "건")
        draft = suite.write(
            "weekly",
            deepcopy(result),
            analysis.model_dump(by_alias=True),
            coaching.model_dump(by_alias=True),
            [],
        )
    except Exception:
        print("  작성 실패 — 아래가 원인이다.")
        traceback.print_exc()
        return False

    print("  작성 OK")
    print("  comment :", draft.comment.text)
    print("  근거    :", draft.comment.evidence_ids)
    for tip in draft.tips:
        print("  tip     :", tip.text, tip.evidence_ids)
    return True


def main() -> None:
    result = load_result()
    config = get_weekly_report_config()

    if try_write(config, result, "1) 현재 설정"):
        print("\n현재 설정으로 성공했다. 실패는 간헐적이다.")
        return

    raised_config = replace(
        config,
        writer_maximum_tokens=config.writer_maximum_tokens * 4,
        coach_maximum_tokens=config.coach_maximum_tokens * 2,
        analyst_maximum_tokens=config.analyst_maximum_tokens * 2,
    )
    if try_write(raised_config, result, "2) 토큰 한도 4배"):
        print("\n토큰 한도가 원인이다. config 의 writer_maximum_tokens 를 올리면 된다.")
        return

    print("\n토큰 한도 문제는 아니다. 위 트레이스백이 진짜 원인이다.")


main()

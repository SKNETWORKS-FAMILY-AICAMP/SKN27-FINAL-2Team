"""주간 평가 버튼을 지금 열기 위한 개발용 스크립트.

주간 평가는 (1) 그 날짜가 오늘이고 (2) 그 계획의 학습 블록이 전부 완료됐을 때만
시작할 수 있다(display.py:99, service.py:686). 실제로 6일치를 풀지 않고
트리거를 확인하려고 계획 날짜를 오늘 기준으로 당기고 학습 블록을 완료로 표시한다.

확인용 일회용 스크립트다. 커밋하지 말 것.
실행: python open_weekly_review.py   (app 폴더에서)
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.utils import timezone

from analytics.models import StudyPlanMypage
from analytics.service.study_plan.dto import normalize_block_type, parse_plan_items


STUDY_PLAN_ID = 1


def find_weekly_review_day_index(plan_items: list[dict]) -> int:
    for day_index, day_plan in enumerate(plan_items):
        for block in day_plan.get("blocks", []):
            if normalize_block_type(block) == "weekly_review":
                return day_index
    return -1


def shift_plan_dates(plan_items: list[dict], review_day_index: int, today: date) -> None:
    """주간 평가 날짜가 오늘이 되도록 전체 일정을 통째로 민다."""
    for day_index, day_plan in enumerate(plan_items):
        offset = review_day_index - day_index
        day_plan["date"] = (today - timedelta(days=offset)).isoformat()


def complete_learning_blocks(plan_items: list[dict]) -> tuple[int, int]:
    """학습 블록은 완료로, 주간 평가는 미완료로 되돌린다."""
    completed_count = 0
    review_count = 0
    for day_plan in plan_items:
        for block in day_plan.get("blocks", []):
            if normalize_block_type(block) == "weekly_review":
                block["status"] = "scheduled"
                block["isCompleted"] = False
                block["isAchieved"] = False
                review_count += 1
                continue
            block["status"] = "completed"
            block["isCompleted"] = True
            block["isAchieved"] = True
            completed_count += 1
    return completed_count, review_count


def main() -> None:
    plan = StudyPlanMypage.objects.filter(studyplan_id=STUDY_PLAN_ID).first()
    if plan is None:
        print("학습계획을 찾을 수 없습니다. studyplan_id =", STUDY_PLAN_ID)
        return

    today = timezone.localdate()
    plan_items = parse_plan_items(plan.study_plan_items)
    if not plan_items:
        print("study_plan_items 가 비어 있습니다.")
        return

    review_day_index = find_weekly_review_day_index(plan_items)
    if review_day_index < 0:
        print("주간 평가 블록이 없습니다. 계획을 다시 생성해야 합니다.")
        return

    shift_plan_dates(plan_items, review_day_index, today)
    completed_count, review_count = complete_learning_blocks(plan_items)

    plan.study_plan_items = json.dumps(plan_items, ensure_ascii=False)
    plan.start_date = date.fromisoformat(plan_items[0]["date"])
    plan.end_date = date.fromisoformat(plan_items[-1]["date"])
    plan.status = "active"
    plan.completion_rate = 1.0
    plan.weekly_report_data = None
    plan.modified_at = timezone.now()
    plan.save(
        update_fields=[
            "study_plan_items",
            "start_date",
            "end_date",
            "status",
            "completion_rate",
            "weekly_report_data",
            "modified_at",
        ],
    )

    print("오늘          :", today)
    print("계획 기간     :", plan.start_date, "~", plan.end_date)
    print("학습 블록 완료:", completed_count, "건")
    print("주간 평가     :", review_count, "건 (미완료로 유지)")
    print("주간 리포트   : 비움 (평가 제출 시 새로 생성됨)")
    print()
    print("마이페이지 새로고침 후 '주간 평가 시작' 버튼을 눌러라.")
    print("50문항 제출하면 트리거가 pending 리포트를 만든다.")
    print("그다음 워커: python manage.py run_weekly_report_worker --interval 10")


main()

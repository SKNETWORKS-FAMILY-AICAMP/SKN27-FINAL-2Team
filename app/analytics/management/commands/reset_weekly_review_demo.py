"""주간 리포트 전체 흐름을 다시 확인하기 위해 학습계획을 7일차 상태로 되돌린다.

주간 평가는 (1) 그 날짜가 오늘이고 (2) 같은 계획의 학습 블록이 전부 완료됐을 때만
시작할 수 있다(display.py 의 can_start, service.py 의 validate_block_start).
6일치를 실제로 풀지 않고 트리거부터 화면까지 확인하려면 그 상태를 만들어야 한다.

블록 완료 여부는 계획 JSON 이 아니라 solve_records 에서도 파생되므로
(service.py 의 _get_progress_block_ids), 이 계획에 연결된 풀이 기록을 지우지 않으면
두 번째 실행부터는 주간 평가가 계속 완료로 잡혀 다시 응시할 수 없다.

개발·검증 전용이다. 운영에서 실행하면 해당 계획의 풀이 기록이 사라진다.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Mapping, Sequence

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from analytics.models import Analytics, StudyPlanMypage
from analytics.service.study_plan.dto import normalize_block_type, parse_plan_items
from question.models import SolveRecords, SolveSessions


class Command(BaseCommand):
    help = "학습계획을 '오늘이 7일차(주간 평가일)'인 상태로 되돌린다. 개발 전용."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--user-id", type=int, required=True, help="대상 사용자 ID")
        parser.add_argument(
            "--study-plan-id",
            type=int,
            default=0,
            help="대상 학습계획 ID. 0 이면 활성 계획을 쓴다",
        )
        parser.add_argument(
            "--keep-records",
            action="store_true",
            help="풀이 기록을 지우지 않는다. 주간 평가를 이미 봤다면 다시 응시할 수 없다",
        )

    def handle(self, *args: object, **options: object) -> None:
        user_id = int(options["user_id"])
        study_plan_id = int(options["study_plan_id"])
        keep_records = bool(options["keep_records"])

        with transaction.atomic():
            study_plan = self._find_study_plan(user_id, study_plan_id)
            if study_plan is None:
                self.stderr.write("대상 학습계획을 찾을 수 없습니다.")
                return

            plan_items = parse_plan_items(study_plan.study_plan_items)
            if not plan_items:
                self.stderr.write("study_plan_items 가 비어 있습니다.")
                return

            review_day_index = self._find_weekly_review_day_index(plan_items)
            if review_day_index < 0:
                self.stderr.write("주간 평가 블록이 없습니다. 계획을 다시 생성해야 합니다.")
                return

            today = timezone.localdate()
            self._shift_plan_dates(plan_items, review_day_index, today)
            completed_count = self._reset_block_status(plan_items)

            deleted_records = 0
            deleted_sessions = 0
            deleted_analytics = 0
            if not keep_records:
                deleted = self._delete_plan_history(user_id, study_plan.studyplan_id)
                deleted_records = deleted["records"]
                deleted_sessions = deleted["sessions"]
                deleted_analytics = deleted["analytics"]

            study_plan.study_plan_items = json.dumps(plan_items, ensure_ascii=False)
            study_plan.start_date = date.fromisoformat(str(plan_items[0]["date"]))
            study_plan.end_date = date.fromisoformat(str(plan_items[-1]["date"]))
            study_plan.status = "active"
            study_plan.completion_rate = 1.0
            study_plan.weekly_report_data = None
            study_plan.modified_at = timezone.now()
            study_plan.save(
                update_fields=(
                    "study_plan_items",
                    "start_date",
                    "end_date",
                    "status",
                    "completion_rate",
                    "weekly_report_data",
                    "modified_at",
                ),
            )

        self.stdout.write(f"학습계획      : {study_plan.studyplan_id}")
        self.stdout.write(f"계획 기간     : {study_plan.start_date} ~ {study_plan.end_date}")
        self.stdout.write(f"학습 블록     : {completed_count}건 완료 처리")
        self.stdout.write(f"주간 평가     : 오늘({study_plan.end_date})로 이동, 미응시")
        self.stdout.write("주간 리포트   : 비움")
        if keep_records:
            self.stdout.write("풀이 기록     : 유지 (--keep-records)")
        else:
            self.stdout.write(
                f"풀이 기록     : 기록 {deleted_records}건 / 세션 {deleted_sessions}건 "
                f"/ 분석 {deleted_analytics}건 삭제",
            )
        self.stdout.write("")
        self.stdout.write("마이페이지 새로고침 후 '주간 평가 시작' 을 누르면 된다.")

    def _find_study_plan(self, user_id: int, study_plan_id: int) -> StudyPlanMypage | None:
        plans = StudyPlanMypage.objects.filter(user_id=user_id).exclude(status="deleted")
        if study_plan_id:
            return plans.filter(studyplan_id=study_plan_id).first()
        return plans.filter(status="active").order_by("-plan_version").first()

    def _find_weekly_review_day_index(self, plan_items: Sequence[Mapping[str, object]]) -> int:
        for day_index, day_plan in enumerate(plan_items):
            for block in day_plan.get("blocks", []):
                if normalize_block_type(block) == "weekly_review":
                    return day_index
        return -1

    def _shift_plan_dates(
        self,
        plan_items: Sequence[dict],
        review_day_index: int,
        today: date,
    ) -> None:
        """주간 평가일이 오늘이 되도록 일정 전체를 같은 폭으로 민다."""
        for day_index, day_plan in enumerate(plan_items):
            offset = review_day_index - day_index
            day_plan["date"] = (today - timedelta(days=offset)).isoformat()

    def _reset_block_status(self, plan_items: Sequence[dict]) -> int:
        """학습 블록은 완료로, 주간 평가는 미응시로 되돌린다."""
        completed_count = 0
        for day_plan in plan_items:
            for block in day_plan.get("blocks", []):
                if normalize_block_type(block) == "weekly_review":
                    block["status"] = "scheduled"
                    block["isCompleted"] = False
                    block["isAchieved"] = False
                    continue
                block["status"] = "completed"
                block["isCompleted"] = True
                block["isAchieved"] = True
                completed_count += 1
        return completed_count

    def _delete_plan_history(self, user_id: int, study_plan_id: int) -> dict[str, int]:
        """이 계획에 연결된 풀이 기록과 분석 스냅샷을 지운다.

        계획과 무관한 진단평가 기록은 남긴다. 주간 리포트가 직전 점수를
        비교 기준으로 쓰기 때문이다.
        """
        session_ids = set(
            SolveRecords.objects.filter(
                session__user_id=user_id,
                studyplan_id=study_plan_id,
            ).values_list("session_id", flat=True)
        )
        deleted_records = SolveRecords.objects.filter(
            session__user_id=user_id,
            studyplan_id=study_plan_id,
        ).delete()[0]

        deleted_analytics = 0
        deleted_sessions = 0
        if session_ids:
            deleted_analytics = Analytics.objects.filter(
                user_id=user_id,
                session_id__in=session_ids,
            ).delete()[0]
            # 다른 계획의 기록이 남아 있는 세션은 지우지 않는다.
            reusable_session_ids = set(
                SolveRecords.objects.filter(session_id__in=session_ids)
                .values_list("session_id", flat=True)
            )
            removable_session_ids = session_ids - reusable_session_ids
            if removable_session_ids:
                deleted_sessions = SolveSessions.objects.filter(
                    user_id=user_id,
                    session_id__in=removable_session_ids,
                ).delete()[0]

        return {
            "records": deleted_records,
            "sessions": deleted_sessions,
            "analytics": deleted_analytics,
        }

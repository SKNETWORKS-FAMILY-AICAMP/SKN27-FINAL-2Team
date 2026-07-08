import json
from datetime import date, timedelta
from uuid import uuid4

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from analytics.models import StudyPlanMypage
from analytics.serializers import parse_study_plan_items, serialize_study_plan, serialize_study_plans
from analytics.service.analysis_snapshot import (
    create_study_plan_base_snapshot,
    create_study_plan_result_snapshot,
)
from analytics.service.analytics import get_classification_fields, get_composite_weak_targets
from analytics.service.prediction import get_predicted_targets
from question.models import SolveRecords
from user.models import UserAccounts


class StudyPlanBlockDeleteLimitExceeded(Exception):
    pass


class StudyPlanMoveDateOutOfRange(Exception):
    pass


def get_user_study_info(user_id):
    """
    사용자의 학습 설정 프로필을 조회한다.

    하루 학습 가능 시간과 시험일 정보를 가져오는 기본 조회 함수이며,
    프로필이 없으면 None을 반환한다.
    """
    return UserAccounts.objects.filter(user_id=user_id).first()


def get_daily_available_minutes(user_id, profile=None):
    """
    사용자의 하루 학습 가능 시간을 분 단위로 변환한다.

    UserAccounts.daily_available_hours 값을 읽어 60을 곱하고,
    설정값이 없으면 0분으로 반환한다. 이미 조회한 프로필을 받으면
    같은 사용자의 프로필을 다시 조회하지 않고 그 값을 사용한다.
    """
    study_profile = profile
    if study_profile is None:
        study_profile = get_user_study_info(user_id)
    if study_profile and study_profile.daily_available_hours is not None:
        return int(float(study_profile.daily_available_hours) * 60)

    return 0


def get_remaining_days(user_id, today=None, profile=None):
    """
    시험일까지 남은 일수를 계산한다.

    시험일이 미래면 실제 남은 일수를 반환하고,
    시험일이 오늘이거나 지난 경우에는 당일 압축 계획 일수를 반환한다.
    이미 조회한 프로필을 받으면 같은 사용자의 프로필을 다시 조회하지 않는다.
    """
    config = get_study_plan_config()
    base_date = today or timezone.localdate()
    study_profile = profile
    if study_profile is None:
        study_profile = get_user_study_info(user_id)
    if study_profile and study_profile.exam_date:
        remaining_days = (study_profile.exam_date - base_date).days
        if remaining_days > 0:
            return remaining_days
        elif remaining_days <= 0:
            return config["same_day_plan_days"]

    return config["default_remaining_days"]


def format_plan_items(study_plan_items):
    """
    DB에 저장할 학습계획 상세 항목을 JSON 문자열로 정규화한다.

    None은 빈 리스트 문자열로, 이미 문자열인 값은 그대로,
    리스트/딕셔너리 형태는 한글이 깨지지 않도록 JSON으로 변환한다.
    """
    if study_plan_items is None:
        return "[]"
    elif isinstance(study_plan_items, str):
        return study_plan_items

    return json.dumps(study_plan_items, ensure_ascii=False)


def get_study_plan_info(user_id):
    """
    사용자의 현재 active 학습계획을 최신순으로 조회한다.

    DB 모델을 그대로 넘기지 않고 serializer를 통해
    화면/API에서 쓰는 응답 형태로 변환한다.
    """
    daily_available_minutes = get_daily_available_minutes(user_id)
    display_plan_count = 1
    study_plans = list(
        StudyPlanMypage.objects.filter(user_id=user_id)
        .filter(status="active")
        .order_by("-plan_version", "-modified_at")[:display_plan_count]
    )
    return serialize_study_plans_with_progress(user_id, study_plans, daily_available_minutes)


def ensure_today_study_plan(user_id, today=None):
    should_create_plan = False

    with transaction.atomic():
        study_plan = get_active_study_plans(user_id, lock=True).first()
        if study_plan is None:
            should_create_plan = True

    if should_create_plan:
        return create_study_plan(user_id)

    return None


def save_study_plan_items(user_id, study_plan, plan_items):
    start_date, end_date = get_plan_date_range(plan_items)
    completion_stats = calculate_plan_completion(plan_items)
    study_plan.study_plan_items = format_plan_items(plan_items)
    study_plan.start_date = start_date
    study_plan.end_date = end_date
    study_plan.completion_rate = completion_stats["completion_rate"]
    study_plan.modified_at = timezone.now()
    study_plan.save(
        update_fields=[
            "study_plan_items",
            "start_date",
            "end_date",
            "completion_rate",
            "modified_at",
        ],
    )
    daily_available_minutes = get_daily_available_minutes(user_id)
    return serialize_study_plan(study_plan, daily_available_minutes)


def carry_over_incomplete_past_blocks_to_today(plan_items, today):
    today_key = today.isoformat()
    target_day_plan = get_or_create_study_plan_day(plan_items, today_key)
    moved_blocks = []
    changed = False

    for day_plan in plan_items:
        plan_date = parse_study_plan_day_date(day_plan)
        if plan_date is None:
            continue
        if plan_date >= today:
            continue

        blocks = day_plan.get("blocks", [])
        remaining_blocks = []
        for block in blocks:
            if block.get("isCompleted"):
                remaining_blocks.append(block)
            elif not block.get("isCompleted"):
                moved_blocks.append(block)

        if len(remaining_blocks) != len(blocks):
            day_plan["blocks"] = remaining_blocks
            changed = True

    if moved_blocks:
        target_day_plan.setdefault("blocks", []).extend(moved_blocks)
        changed = True

    if changed:
        plan_items = prune_empty_study_plan_days(plan_items)
        plan_items = sorted(plan_items, key=lambda plan: str(plan.get("date", ""))[:10])

    return {
        "items": plan_items,
        "changed": changed,
    }


def parse_study_plan_day_date(day_plan):
    raw_date = str(day_plan.get("date", ""))[:10]
    try:
        return date.fromisoformat(raw_date)
    except ValueError:
        return None


def get_or_create_study_plan_day(plan_items, date_key):
    for day_plan in plan_items:
        raw_date = str(day_plan.get("date", ""))[:10]
        if raw_date == date_key:
            return day_plan

    day_plan = {"date": date_key, "blocks": []}
    plan_items.append(day_plan)
    return day_plan


def prune_empty_study_plan_days(plan_items):
    return [
        day_plan
        for day_plan in plan_items
        if day_plan.get("blocks") or has_study_plan_day_delete_history(day_plan)
    ]


def has_study_plan_day_delete_history(day_plan):
    config = get_study_plan_config()
    delete_count_key = config["daily_delete_count_key"]
    try:
        return int(day_plan.get(delete_count_key) or 0) > 0
    except (TypeError, ValueError):
        return False


def should_create_plan_for_today(study_plan, plan_items, today):
    if has_study_plan_blocks_on_date(plan_items, today):
        return False
    if was_study_plan_touched_today(study_plan, today):
        return False

    return True


def has_study_plan_blocks_on_date(plan_items, target_date):
    target_key = target_date.isoformat()
    for day_plan in plan_items:
        raw_date = str(day_plan.get("date", ""))[:10]
        if raw_date == target_key and day_plan.get("blocks"):
            return True

    return False


def was_study_plan_touched_today(study_plan, today):
    touched_at = study_plan.modified_at or study_plan.created_at
    if touched_at is None:
        return False
    if timezone.is_naive(touched_at):
        touched_at = timezone.make_aware(touched_at, timezone.get_current_timezone())

    return timezone.localtime(touched_at).date() == today


def get_previous_study_plan_info(user_id):
    """
    이전에 보관된 학습계획을 최신순으로 조회하고 풀이 기록 기반 달성률을 붙인다.
    """
    daily_available_minutes = get_daily_available_minutes(user_id)
    config = get_study_plan_config()
    display_plan_count = config["history_display_limit"]
    study_plans = list(
        StudyPlanMypage.objects.filter(user_id=user_id, status="archived")
        .order_by("-plan_version", "-modified_at")[:display_plan_count]
    )
    return serialize_study_plans_with_progress(user_id, study_plans, daily_available_minutes)


def serialize_study_plans_with_progress(user_id, study_plans, daily_available_minutes):
    """
    학습계획 직렬화 결과에 실제 풀이 기록 기반 달성률을 추가한다.
    """
    serialized_plans = serialize_study_plans(study_plans, daily_available_minutes)
    for index, study_plan in enumerate(study_plans):
        progress_data = calculate_record_based_plan_progress(user_id, study_plan)
        serialized_plans[index]["progress"] = progress_data["summary"]
        serialized_plans[index]["historyDisplay"] = build_plan_history_display(study_plan)
        apply_block_progress(
            serialized_plans[index]["plans"],
            progress_data["block_progress"],
        )

    return serialized_plans


def build_plan_history_display(study_plan):
    """
    이전 학습계획 카드에서 식별 가능한 제목과 보조 정보를 만든다.
    """
    title = "기간 미정"
    start_label = format_plan_history_date(study_plan.start_date)
    end_label = format_plan_history_date(study_plan.end_date)
    created_label = format_plan_history_datetime(study_plan.created_at)

    if start_label and end_label and start_label == end_label:
        title = start_label
    elif start_label and end_label:
        title = f"{start_label} - {end_label}"
    elif start_label:
        title = f"{start_label} 시작"
    elif created_label:
        title = f"{created_label} 생성"

    return {
        "title": title,
        "meta": f"계획 #{study_plan.plan_version}",
        "statusLabel": format_plan_history_status(study_plan.status),
    }


def format_plan_history_status(status):
    """
    이전 학습계획 카드에서 사용할 상태 라벨을 만든다.
    """
    if status == "active":
        return "진행 중"
    elif status == "archived":
        return "종료"
    elif status == "deleted":
        return "삭제"

    return status or "계획"


def format_plan_history_date(value):
    """
    계획 날짜를 이전 학습계획 카드용 MM.DD 문자열로 변환한다.
    """
    if value:
        return value.strftime("%m.%d")

    return ""


def format_plan_history_datetime(value):
    """
    계획 생성 시각을 현재 시간대 기준 MM.DD 문자열로 변환한다.
    """
    if value:
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return timezone.localtime(value).strftime("%m.%d")

    return ""


def apply_block_progress(plan_items, block_progress):
    """
    날짜별 계획 블록에 달성 문항 수와 남은 문항 수를 주입한다.
    """
    for day_index, day_plan in enumerate(plan_items):
        for block_index, block in enumerate(day_plan.get("blocks", [])):
            progress = block_progress.get((day_index, block_index))
            if progress is None:
                continue
            block.update(progress)


def calculate_record_based_plan_progress(user_id, study_plan):
    """
    수동 완료 여부 대신 실제 완료 풀이 기록으로 계획 달성률을 계산한다.
    """
    plan_items = parse_study_plan_items(study_plan.study_plan_items)
    records = get_plan_progress_records(user_id, study_plan)
    used_record_ids = set()
    block_progress = {}
    target_total = 0
    achieved_total = 0

    for day_index, day_plan in enumerate(plan_items):
        for block_index, block in enumerate(day_plan.get("blocks", [])):
            if is_review_plan_block(block):
                block_progress[(day_index, block_index)] = build_review_block_progress(block)
                continue
            if is_weekly_review_plan_block(block):
                target_count = int(block.get("questionCount") or 0)
                target_total += target_count
                progress = build_weekly_review_block_progress(block, target_count)
                achieved_total += progress["achievedCount"]
                block_progress[(day_index, block_index)] = progress
                continue

            target_count = int(block.get("questionCount") or 0)
            target_total += target_count
            achieved_count = count_block_matched_records(
                records,
                used_record_ids,
                block,
                target_count,
            )
            achieved_total += achieved_count
            remaining_count = max(target_count - achieved_count, 0)
            progress_rate = calculate_progress_rate(achieved_count, target_count)
            block_progress[(day_index, block_index)] = {
                "achievedCount": achieved_count,
                "remainingCount": remaining_count,
                "progressRate": progress_rate,
                "progressPercent": round(progress_rate * 100),
                "isAchieved": target_count > 0 and remaining_count == 0,
                "progressMode": "question",
                "statusLabel": "",
            }

    remaining_total = max(target_total - achieved_total, 0)
    completion_rate = calculate_progress_rate(achieved_total, target_total)
    return {
        "summary": {
            "targetCount": target_total,
            "achievedCount": achieved_total,
            "remainingCount": remaining_total,
            "completionRate": completion_rate,
            "completionPercent": round(completion_rate * 100),
            "periodLabel": format_plan_progress_period(study_plan),
        },
        "block_progress": block_progress,
    }


def is_review_plan_block(block):
    """
    오답 복습 블록은 일반 문제풀이 달성률과 별도 상태로 관리한다.
    """
    return block.get("blockType") == "review"


def is_weekly_review_plan_block(block):
    config = get_study_plan_config()
    return block.get("blockType") == config["weekly_review_block_type"]


def build_review_block_progress(block):
    """
    오답 복습 블록은 solve_records 추정 매칭을 하지 않고 확인 상태만 반영한다.
    """
    is_confirmed = bool(block.get("isCompleted"))
    progress_rate = 0.0
    progress_percent = 0
    status_label = "복습 확인 전"
    if is_confirmed:
        progress_rate = 1.0
        progress_percent = 100
        status_label = "복습 확인 완료"

    return {
        "achievedCount": 0,
        "remainingCount": 0,
        "progressRate": progress_rate,
        "progressPercent": progress_percent,
        "isAchieved": is_confirmed,
        "progressMode": "review",
        "statusLabel": status_label,
    }


def build_weekly_review_block_progress(block, target_count):
    is_completed = bool(block.get("isCompleted"))
    achieved_count = 0
    status_label = "주간 평가 전"
    if is_completed:
        achieved_count = target_count
        status_label = "주간 평가 완료"

    remaining_count = max(target_count - achieved_count, 0)
    progress_rate = calculate_progress_rate(achieved_count, target_count)
    return {
        "achievedCount": achieved_count,
        "remainingCount": remaining_count,
        "progressRate": progress_rate,
        "progressPercent": round(progress_rate * 100),
        "isAchieved": is_completed,
        "progressMode": "question",
        "statusLabel": status_label,
    }


def get_plan_progress_records(user_id, study_plan):
    """
    계획 달성률 계산에 사용할 완료 풀이 기록을 조회한다.
    """
    queryset = SolveRecords.objects.filter(
        session__user_id=user_id,
        session__status="completed",
        studyplan_id=study_plan.studyplan_id,
        study_plan_block_id__isnull=False,
        selected_no__isnull=False,
    )

    return list(
        queryset.values(
            "record_id",
            "study_plan_block_id",
        ).order_by("session__recorded_date", "record_id")
    )


def get_plan_progress_period_end(study_plan):
    """
    active 계획은 오늘까지, archived 계획은 보관 시점까지의 기록만 본다.
    """
    period_end = study_plan.end_date
    if study_plan.archived_at is not None:
        archived_at = study_plan.archived_at
        if timezone.is_naive(archived_at):
            archived_at = timezone.make_aware(archived_at, timezone.get_current_timezone())
        archived_date = timezone.localtime(archived_at).date()
        if period_end is None or archived_date < period_end:
            period_end = archived_date
    elif study_plan.status == "active":
        today = timezone.localdate()
        if period_end is None or today < period_end:
            period_end = today

    return period_end


def count_block_matched_records(records, used_record_ids, block, target_count):
    """
    하나의 계획 블록과 매칭되는 풀이 기록 수를 계산한다.
    """
    if target_count <= 0:
        return 0

    block_id = block.get("blockId")
    if not block_id:
        return 0

    matched_record_ids = []
    for record in records:
        record_id = record["record_id"]
        if record_id in used_record_ids:
            continue
        if str(record.get("study_plan_block_id")) == str(block_id):
            matched_record_ids.append(record_id)
        if len(matched_record_ids) >= target_count:
            break

    used_record_ids.update(matched_record_ids)
    return len(matched_record_ids)


def get_block_record_field(block):
    """
    계획 블록의 분류명을 SolveRecords 컬럼명으로 변환한다.
    """
    classification = block.get("classification")
    for classification_label, field_name in get_classification_fields():
        if classification == classification_label:
            return field_name

    return None


def calculate_progress_rate(count, total):
    """
    달성률을 0~1 사이 소수로 계산한다.
    """
    if total:
        return round(count / total, 4)

    return 0.0


def format_plan_progress_period(study_plan):
    """
    계획 달성률 기준 기간을 화면 표시용 문자열로 만든다.
    """
    period_start = get_study_plan_result_period_start(study_plan)
    period_end = get_plan_progress_period_end(study_plan)
    if period_start and period_end:
        return f"{period_start.strftime('%m.%d')} - {period_end.strftime('%m.%d')}"
    if period_start:
        return f"{period_start.strftime('%m.%d')} 시작"
    if period_end:
        return f"{period_end.strftime('%m.%d')}까지"

    return "기간 미정"


def create_study_plan(user_id, study_plans="", study_plan_items=None, predicted_targets=None):
    """
    사용자의 학습계획을 생성하고 저장한다.

    상세 계획이 전달되지 않으면 취약점/출제예상 기반으로 자동 생성하고,
    기존 active 계획은 archived 처리한 뒤 새 active 계획 row를 생성한다.
    생성 직후에는 study_plan_base 분석 결과를 analytics 테이블에 저장한다.
    """
    if study_plan_items is None:
        generated_plan = build_user_study_plan(user_id, predicted_targets)
        study_plan_items = generated_plan["plans"]
        if not study_plans:
            study_plans = generated_plan["summary"]

    study_plan_items = prepare_study_plan_items(study_plan_items)
    start_date, end_date = get_plan_date_range(study_plan_items)
    completion_stats = calculate_plan_completion(study_plan_items)
    now = timezone.now()

    with transaction.atomic():
        active_plans = get_active_study_plans(user_id, lock=True)
        for active_plan in active_plans:
            archive_study_plan(user_id, active_plan, now)

        study_plan = StudyPlanMypage.objects.create(
            user_id=user_id,
            study_plans=study_plans,
            study_plan_items=format_plan_items(study_plan_items),
            created_at=now,
            modified_at=now,
            status="active",
            plan_version=get_next_plan_version(user_id),
            start_date=start_date,
            end_date=end_date,
            completion_rate=completion_stats["completion_rate"],
        )

    create_study_plan_base_snapshot(user_id, study_plan.studyplan_id)
    daily_available_minutes = get_daily_available_minutes(user_id)
    return serialize_study_plan(study_plan, daily_available_minutes)


def update_study_plan(user_id, study_plan_id, study_plans, study_plan_items):
    """
    기존 학습계획의 요약과 날짜별 상세 항목을 수정한다.

    사용자 소유의 특정 studyplan_id만 수정하며,
    modified_at을 현재 시각으로 갱신한 뒤 직렬화된 결과를 반환한다.
    """
    study_plan = StudyPlanMypage.objects.get(
        user_id=user_id,
        studyplan_id=study_plan_id,
    )
    study_plan_items = prepare_study_plan_items(study_plan_items)
    start_date, end_date = get_plan_date_range(study_plan_items)
    completion_stats = calculate_plan_completion(study_plan_items)
    study_plan.study_plans = study_plans
    study_plan.study_plan_items = format_plan_items(study_plan_items)
    study_plan.start_date = start_date
    study_plan.end_date = end_date
    study_plan.completion_rate = completion_stats["completion_rate"]
    study_plan.modified_at = timezone.now()
    study_plan.save(
        update_fields=[
            "study_plans",
            "study_plan_items",
            "start_date",
            "end_date",
            "completion_rate",
            "modified_at",
        ],
    )
    daily_available_minutes = get_daily_available_minutes(user_id)
    return serialize_study_plan(study_plan, daily_available_minutes)


def delete_study_plan(user_id, study_plan_id):
    """
    학습계획을 소프트 삭제하고 삭제 전 데이터를 반환한다.

    과거 이력 보존을 위해 row를 실제 삭제하지 않고 status/deleted_at을 갱신한다.
    """
    study_plan = StudyPlanMypage.objects.get(
        user_id=user_id,
        studyplan_id=study_plan_id,
    )
    daily_available_minutes = get_daily_available_minutes(user_id)
    deleted_study_plan = serialize_study_plan(study_plan, daily_available_minutes)
    now = timezone.now()
    study_plan.status = "deleted"
    study_plan.deleted_at = now
    study_plan.modified_at = now
    study_plan.save(update_fields=["status", "deleted_at", "modified_at"])
    return deleted_study_plan


def delete_study_plan_block(user_id, study_plan_id, day_index, block_index):
    """
    학습계획 안의 특정 날짜/블록 하나를 삭제한다.

    마이페이지 달력에서 개별 학습 항목의 삭제 버튼을 눌렀을 때 사용하며,
    study_plan_items JSON을 다시 저장한 뒤 최신 직렬화 결과를 반환한다.
    """
    study_plan = StudyPlanMypage.objects.filter(
        user_id=user_id,
        studyplan_id=study_plan_id,
    ).first()
    if study_plan is None:
        return None

    plan_items = parse_study_plan_items(study_plan.study_plan_items)
    if 0 <= day_index < len(plan_items):
        day_plan = plan_items[day_index]
        if is_study_plan_delete_limit_reached(plan_items):
            raise StudyPlanBlockDeleteLimitExceeded

        blocks = day_plan.get("blocks", [])
        if 0 <= block_index < len(blocks):
            block = blocks[block_index]
            if block.get("blockType") == get_study_plan_config()["weekly_review_block_type"]:
                return None

            deleted_block = blocks.pop(block_index)
            increase_study_plan_day_delete_count(day_plan)
            refill_deleted_plan_block(
                user_id,
                plan_items,
                day_index,
                block_index,
                deleted_block,
            )
            plan_items = [
                plan
                for plan in plan_items
                if plan.get("blocks") or get_study_plan_day_delete_count(plan)
            ]
            return update_study_plan(
                user_id,
                study_plan_id,
                study_plan.study_plans,
                plan_items,
            )

    return None


def get_study_plan_day_delete_count(day_plan, base_date=None):
    config = get_study_plan_config()
    delete_count_key = config["daily_delete_count_key"]
    delete_date_key = config["daily_delete_count_date_key"]
    target_date = base_date or timezone.localdate()
    if day_plan.get(delete_date_key) != target_date.isoformat():
        return 0

    try:
        return int(day_plan.get(delete_count_key) or 0)
    except (TypeError, ValueError):
        return 0


def get_study_plan_daily_delete_count(plan_items, base_date=None):
    target_date = base_date or timezone.localdate()
    delete_count = 0
    for day_plan in plan_items:
        delete_count += get_study_plan_day_delete_count(day_plan, target_date)

    return delete_count


def is_study_plan_delete_limit_reached(plan_items):
    config = get_study_plan_config()
    return get_study_plan_daily_delete_count(plan_items) >= config["daily_delete_limit"]


def increase_study_plan_day_delete_count(day_plan):
    config = get_study_plan_config()
    delete_count_key = config["daily_delete_count_key"]
    delete_date_key = config["daily_delete_count_date_key"]
    today = timezone.localdate()
    delete_count = get_study_plan_day_delete_count(day_plan, today)
    day_plan[delete_date_key] = today.isoformat()
    day_plan[delete_count_key] = delete_count + 1


def refill_deleted_plan_block(user_id, plan_items, day_index, block_index, deleted_block):
    if not 0 <= day_index < len(plan_items):
        return

    day_plan = plan_items[day_index]
    blocks = day_plan.get("blocks", [])
    replacement_block = build_replacement_plan_block(user_id, plan_items, deleted_block)
    if replacement_block is None:
        return

    insert_index = min(block_index, len(blocks))
    blocks.insert(insert_index, replacement_block)


def build_replacement_plan_block(user_id, plan_items, deleted_block):
    config = get_study_plan_config()
    base_date = timezone.localdate()
    profile = get_user_study_info(user_id)
    remaining_days = get_remaining_days(user_id, base_date, profile)
    priority_targets = build_priority_targets(
        get_composite_weak_targets(user_id),
        get_predicted_targets(user_id),
        remaining_days,
        config,
    )
    planned_keys = get_planned_target_keys(plan_items)
    deleted_key = get_optional_priority_target_key(deleted_block)
    if deleted_key is not None:
        planned_keys.add(deleted_key)

    for target in priority_targets:
        target_key = get_optional_priority_target_key(target)
        if target_key is None:
            continue
        if target_key in planned_keys:
            continue

        estimated_minutes = get_replacement_estimated_minutes(deleted_block, config)
        block_type = get_target_block_type(target)
        return build_study_block(target, block_type, estimated_minutes, config)

    return None


def get_planned_target_keys(plan_items):
    target_keys = set()
    for day_plan in plan_items:
        for block in day_plan.get("blocks", []):
            target_key = get_optional_priority_target_key(block)
            if target_key is not None:
                target_keys.add(target_key)

    return target_keys


def get_optional_priority_target_key(target):
    try:
        return get_priority_target_key(target)
    except KeyError:
        return None


def get_replacement_estimated_minutes(deleted_block, config):
    estimated_minutes = deleted_block.get("estimatedMinutes") or config["min_block_minutes"]
    try:
        estimated_minutes = int(estimated_minutes)
    except (TypeError, ValueError):
        estimated_minutes = config["min_block_minutes"]

    if estimated_minutes < config["min_block_minutes"]:
        return config["min_block_minutes"]

    return estimated_minutes


def complete_study_plan_block(user_id, study_plan_id, day_index, block_index, is_completed=True):
    """
    학습계획 안의 특정 블록 완료 상태를 변경한다.

    block의 isCompleted/completedAt 값을 갱신하고, 전체 completion_rate를 다시 계산한다.
    """
    study_plan = StudyPlanMypage.objects.filter(
        user_id=user_id,
        studyplan_id=study_plan_id,
    ).first()
    if study_plan is None:
        return None

    plan_items = parse_study_plan_items(study_plan.study_plan_items)
    if 0 <= day_index < len(plan_items):
        day_plan = plan_items[day_index]
        blocks = day_plan.get("blocks", [])
        if 0 <= block_index < len(blocks):
            set_study_plan_block_completion(blocks[block_index], is_completed)
            return update_study_plan(
                user_id,
                study_plan_id,
                study_plan.study_plans,
                plan_items,
            )

    return None


def complete_study_plan_block_by_id(user_id, study_plan_id, block_id, is_completed=True):
    if not block_id:
        return None

    study_plan = StudyPlanMypage.objects.filter(
        user_id=user_id,
        studyplan_id=study_plan_id,
    ).first()
    if study_plan is None:
        return None

    plan_items = parse_study_plan_items(study_plan.study_plan_items)
    for day_plan in plan_items:
        for block in day_plan.get("blocks", []):
            if str(block.get("blockId")) == str(block_id):
                set_study_plan_block_completion(block, is_completed)
                return update_study_plan(
                    user_id,
                    study_plan_id,
                    study_plan.study_plans,
                    plan_items,
                )

    return None


def set_study_plan_block_completion(block, is_completed):
    block["isCompleted"] = bool(is_completed)
    block["completedAt"] = None
    if is_completed:
        block["completedAt"] = timezone.now().isoformat()


def move_study_plan_blocks(user_id, move_items, target_date):
    """
    선택한 학습 블록들을 지정 날짜로 이동한다.

    학습일 변경 모달에서 체크한 항목 목록을 받아 같은 study_plan_mypage
    row 안의 기존 날짜 blocks에서 제거하고 target_date 날짜 blocks로 옮긴다.
    """
    if not move_items:
        return []

    target_plan_date = date.fromisoformat(str(target_date)[:10])
    target_date_key = target_plan_date.isoformat()
    study_plan_ids = sorted({item["studyPlanId"] for item in move_items})
    updated_plans = []

    with transaction.atomic():
        for study_plan_id in study_plan_ids:
            study_plan = (
                StudyPlanMypage.objects.select_for_update()
                .filter(user_id=user_id, studyplan_id=study_plan_id)
                .first()
            )
            if study_plan is not None:
                if not is_study_plan_move_date_allowed(study_plan, target_plan_date):
                    raise StudyPlanMoveDateOutOfRange

                plan_items = parse_study_plan_items(study_plan.study_plan_items)
                selected_indexes_by_day = {}
                for item in move_items:
                    if item["studyPlanId"] == study_plan_id:
                        selected_indexes_by_day.setdefault(item["dayIndex"], set()).add(
                            item["blockIndex"],
                        )

                blocks_to_move = []
                for day_index in sorted(selected_indexes_by_day):
                    if 0 <= day_index < len(plan_items):
                        blocks = plan_items[day_index].get("blocks", [])
                        for block_index in sorted(selected_indexes_by_day[day_index], reverse=True):
                            if 0 <= block_index < len(blocks):
                                blocks_to_move.insert(0, blocks.pop(block_index))

                if blocks_to_move:
                    target_day_plan = None
                    for day_plan in plan_items:
                        raw_date = str(day_plan.get("date", ""))[:10]
                        if raw_date == target_date_key:
                            target_day_plan = day_plan

                    if target_day_plan is None:
                        target_day_plan = {
                            "date": target_date_key,
                            "blocks": [],
                        }
                        plan_items.append(target_day_plan)

                    target_day_plan.setdefault("blocks", []).extend(blocks_to_move)
                    plan_items = keep_study_plan_period_boundary_days(
                        plan_items,
                        study_plan.start_date,
                        study_plan.end_date,
                    )
                    plan_items = sorted(plan_items, key=lambda plan: str(plan.get("date", ""))[:10])
                    updated_plans.append(
                        update_study_plan(
                            user_id,
                            study_plan_id,
                            study_plan.study_plans,
                            plan_items,
                        )
                    )

    return updated_plans


def is_study_plan_move_date_allowed(study_plan, target_date):
    if study_plan.start_date and target_date < study_plan.start_date:
        return False
    if study_plan.end_date and target_date > study_plan.end_date:
        return False

    return True


def keep_study_plan_period_boundary_days(plan_items, period_start, period_end):
    period_keys = set()
    if period_start:
        period_keys.add(period_start.isoformat())
    if period_end:
        period_keys.add(period_end.isoformat())

    existing_keys = {str(day_plan.get("date", ""))[:10] for day_plan in plan_items}
    for period_key in period_keys:
        if period_key not in existing_keys:
            plan_items.append({"date": period_key, "blocks": []})

    return [
        plan
        for plan in plan_items
        if plan.get("blocks") or str(plan.get("date", ""))[:10] in period_keys
    ]


def get_active_study_plans(user_id, lock=False):
    """
    사용자의 현재 active 학습계획 목록을 조회한다.

    새 계획 생성 중 기존 active 계획을 archived 처리할 때는 lock=True로
    select_for_update를 적용해 동시에 두 active 계획이 생기는 상황을 줄인다.
    """
    queryset = StudyPlanMypage.objects.filter(user_id=user_id, status="active")
    if lock:
        queryset = queryset.select_for_update()

    return queryset.order_by("-plan_version", "-modified_at")


def archive_study_plan(user_id, study_plan, archived_at):
    """
    기존 active 학습계획을 archived 상태로 전환한다.

    archived 처리 직전에 계획 기간 기준 study_plan_result 분석을 analytics에 저장하고,
    현재 study_plan_items 기준 완료율도 다시 계산해 보존한다.
    """
    plan_items = parse_study_plan_items(study_plan.study_plan_items)
    completion_stats = calculate_plan_completion(plan_items)
    result_period_start = get_study_plan_result_period_start(study_plan)
    create_study_plan_result_snapshot(
        user_id=user_id,
        study_plan_id=study_plan.studyplan_id,
        period_start=result_period_start,
        period_end=study_plan.end_date,
    )
    study_plan.status = "archived"
    study_plan.archived_at = archived_at
    study_plan.modified_at = archived_at
    study_plan.completion_rate = completion_stats["completion_rate"]
    study_plan.save(
        update_fields=[
            "status",
            "archived_at",
            "modified_at",
            "completion_rate",
        ],
    )


def get_study_plan_result_period_start(study_plan):
    """
    계획 결과 분석 시작일을 계획 생성일보다 앞서지 않게 보정한다.
    """
    period_start = study_plan.start_date
    created_at = study_plan.created_at
    if created_at is not None:
        if timezone.is_naive(created_at):
            created_at = timezone.make_aware(created_at, timezone.get_current_timezone())
        created_date = timezone.localtime(created_at).date()
        if period_start is None or created_date > period_start:
            period_start = created_date

    return period_start


def get_next_plan_version(user_id):
    """
    사용자의 다음 학습계획 버전 번호를 계산한다.

    기존 계획이 없으면 1부터 시작하고, 있으면 최대 plan_version에 1을 더한다.
    """
    max_version = StudyPlanMypage.objects.filter(user_id=user_id).aggregate(
        max_version=Max("plan_version"),
    )["max_version"]
    if max_version is None:
        return 1

    return max_version + 1


def prepare_study_plan_items(study_plan_items):
    """
    날짜별 학습계획 JSON에 블록 추적 필드를 보강한다.

    완료율 계산과 블록 단위 완료 처리에 필요한 blockId, isCompleted,
    completedAt 값이 없으면 기본값을 추가한다.
    """
    prepared_items = parse_study_plan_items(study_plan_items)
    delete_count_key = get_study_plan_config()["daily_delete_count_key"]
    delete_date_key = get_study_plan_config()["daily_delete_count_date_key"]
    for day_plan in prepared_items:
        if delete_count_key not in day_plan:
            day_plan[delete_count_key] = 0
        if delete_date_key not in day_plan:
            day_plan[delete_date_key] = None
        for block in day_plan.get("blocks", []):
            if not block.get("blockId"):
                block["blockId"] = str(uuid4())
            if "isCompleted" not in block:
                block["isCompleted"] = False
            if "completedAt" not in block:
                block["completedAt"] = None

    return prepared_items


def get_plan_date_range(study_plan_items):
    """
    학습계획 JSON의 날짜 목록에서 시작일과 종료일을 계산한다.

    날짜가 없거나 파싱할 수 없는 항목만 있으면 둘 다 None을 반환한다.
    """
    plan_dates = []
    for day_plan in study_plan_items:
        raw_date = str(day_plan.get("date", ""))[:10]
        try:
            plan_dates.append(date.fromisoformat(raw_date))
        except ValueError:
            continue

    if not plan_dates:
        return None, None

    return min(plan_dates), max(plan_dates)


def calculate_plan_completion(study_plan_items):
    """
    학습계획 JSON의 block 완료 상태를 기준으로 완료율을 계산한다.

    completion_rate는 answer_rate와 같은 방식으로 0~1 사이 소수로 저장한다.
    """
    total_block_count = 0
    completed_block_count = 0
    for day_plan in study_plan_items:
        for block in day_plan.get("blocks", []):
            total_block_count += 1
            if block.get("isCompleted"):
                completed_block_count += 1

    completion_rate = 0.0
    if total_block_count:
        completion_rate = round(completed_block_count / total_block_count, 4)

    return {
        "total_block_count": total_block_count,
        "completed_block_count": completed_block_count,
        "completion_rate": completion_rate,
    }


def build_user_study_plan(user_id, predicted_targets=None, today=None):
    """
    취약점과 출제 예상 데이터를 기반으로 사용자 맞춤 학습계획을 생성한다.

    남은 기간, 하루 가용시간, 취약 항목, 출제 예상 항목을 합쳐
    우선순위를 만들고 날짜별 학습 블록 목록을 반환한다.
    """
    config = get_study_plan_config()
    base_date = today or timezone.localdate()
    profile = get_user_study_info(user_id)
    remaining_days = get_remaining_days(user_id, base_date, profile)
    daily_available_minutes = get_daily_available_minutes(user_id, profile)
    if daily_available_minutes <= 0:
        daily_available_minutes = config["fallback_daily_available_minutes"]

    weak_targets = get_composite_weak_targets(user_id)
    prediction_targets = predicted_targets
    if prediction_targets is None:
        prediction_targets = get_predicted_targets(user_id)

    priority_targets = build_priority_targets(
        weak_targets,
        prediction_targets,
        remaining_days,
        config,
    )
    plans = build_daily_plan_items(
        priority_targets,
        daily_available_minutes,
        remaining_days,
        base_date,
        config,
    )

    return {
        "summary": build_plan_summary(priority_targets, daily_available_minutes, config),
        "dailyAvailableMinutes": daily_available_minutes,
        "remainingDays": remaining_days,
        "plans": plans,
    }


def build_study_plan_target_label(era, topic, q_type):
    return " · ".join([era, topic, q_type])


def get_priority_target_identity(target):
    era = target.get("era") or ""
    topic = target.get("topic") or ""
    q_type = target.get("qType") or target.get("q_type") or target.get("questionType") or ""
    if era and topic and q_type:
        label = target.get("label") or build_study_plan_target_label(era, topic, q_type)
        return (era, topic, q_type), {
            "classification": target.get("classification") or "복합",
            "label": label,
            "era": era,
            "topic": topic,
            "qType": q_type,
        }

    classification = target.get("classification")
    label = target.get("label")
    if classification and label:
        return (classification, label), {
            "classification": classification,
            "label": label,
            "era": "",
            "topic": "",
            "qType": "",
        }

    return None, None


def build_priority_target_seed(identity):
    return {
        "classification": identity["classification"],
        "label": identity["label"],
        "era": identity["era"],
        "topic": identity["topic"],
        "qType": identity["qType"],
        "wrongRate": 0.0,
        "predictionScore": 0.0,
        "averageTimeSec": 0,
        "predictionReason": "",
    }


def get_priority_target_key(target):
    era = target.get("era") or ""
    topic = target.get("topic") or ""
    q_type = target.get("qType") or ""
    if era and topic and q_type:
        return (era, topic, q_type)

    return (target["classification"], target["label"])


def build_priority_targets(weak_targets, predicted_targets, remaining_days, config):
    """
    취약 항목과 출제 예상 항목을 병합해 우선순위 점수를 계산한다.

    동일한 classification/label 항목을 하나로 합치고,
    남은 기간 전략에 따른 가중치로 priorityScore를 산출한다.
    """
    target_map = {}
    for weak_target in weak_targets:
        key, identity = get_priority_target_identity(weak_target)
        if key and identity:
            target = target_map.setdefault(
                key,
                build_priority_target_seed(identity),
            )
            wrong_rate = weak_target.get("wrongRate")
            if wrong_rate is None:
                wrong_rate = weak_target.get("wrong_rate") or 0.0
            average_time_sec = weak_target.get("averageTimeSec")
            if average_time_sec is None:
                average_time_sec = weak_target.get("average_time_sec") or 0
            target["wrongRate"] = float(wrong_rate)
            target["averageTimeSec"] = average_time_sec or 0

    for predicted_target in predicted_targets:
        key, identity = get_priority_target_identity(predicted_target)
        if key and identity:
            target = target_map.setdefault(
                key,
                build_priority_target_seed(identity),
            )
            prediction_score = predicted_target.get("predictionScore")
            if prediction_score is None:
                prediction_score = predicted_target.get("prediction_score") or 0.0
            target["predictionScore"] = float(prediction_score)
            target["predictionReason"] = predicted_target.get("reason") or ""

    strategy = get_study_strategy(remaining_days, config)
    weights = config["strategy_weights"][strategy]
    priority_targets = []
    for target in target_map.values():
        average_time_sec = target["averageTimeSec"] or 0
        time_burden_score = 0.0
        if average_time_sec:
            time_burden_score = average_time_sec / config["default_average_time_sec"]
            if time_burden_score > 1:
                time_burden_score = 1

        priority_score = (
            target["wrongRate"] * weights["weakness"]
            + target["predictionScore"] * weights["prediction"]
            + time_burden_score * weights["time_burden"]
        )
        if priority_score >= config["minimum_priority_score"]:
            target["priorityScore"] = round(priority_score, 4)
            target["reason"] = build_priority_reason(target)
            priority_targets.append(target)

    return sorted(
        priority_targets,
        key=lambda item: (
            -item["priorityScore"],
            -item["wrongRate"],
            -item["predictionScore"],
            item["classification"],
            item["label"],
            item["era"],
            item["topic"],
            item["qType"],
        ),
    )


def build_daily_plan_items(priority_targets, daily_available_minutes, remaining_days, today, config):
    """
    우선순위 대상들을 날짜별 학습 블록으로 배치한다.

    하루 가용시간에 따라 블록 수와 블록별 시간을 나눈다.
    """
    if not priority_targets:
        return []

    plan_days = config["weekly_plan_days"]
    learning_days = config["weekly_learning_days"]
    if learning_days >= plan_days:
        learning_days = plan_days - 1

    blocks_per_day = get_blocks_per_day(daily_available_minutes, config)
    target_index = 0
    plans = []

    for day_offset in range(learning_days):
        plan_date = today + timedelta(days=day_offset)
        remaining_minutes = daily_available_minutes
        used_target_keys = set()
        blocks = []

        while len(blocks) < blocks_per_day and remaining_minutes >= config["min_block_minutes"]:
            target = priority_targets[target_index % len(priority_targets)]
            target_index += 1
            target_key = get_priority_target_key(target)
            block_type = get_target_block_type(target)
            if target_key in used_target_keys:
                if len(used_target_keys) < len(priority_targets):
                    continue

            block_minutes = get_block_minutes(remaining_minutes, blocks_per_day, len(blocks), config)
            blocks.append(build_study_block(target, block_type, block_minutes, config))
            if target_key not in used_target_keys:
                used_target_keys.add(target_key)
            remaining_minutes -= block_minutes

        if blocks:
            plans.append(
                {
                    "date": plan_date.isoformat(),
                    "blocks": blocks,
                }
            )

    review_date = today + timedelta(days=learning_days)
    plans.append(
        {
            "date": review_date.isoformat(),
            "blocks": [build_weekly_review_block(config)],
        }
    )

    return plans


def build_weekly_review_block(config):
    return {
        "blockId": str(uuid4()),
        "blockType": config["weekly_review_block_type"],
        "classification": "",
        "label": "주간 평가",
        "era": "",
        "topic": "",
        "qType": "",
        "activity": "주간 평가로 6일 학습 개선도 확인",
        "questionCount": config["weekly_review_question_count"],
        "estimatedMinutes": config["weekly_review_minutes"],
        "priorityScore": 0,
        "reason": "6일 학습 후 전체 범위 평가",
        "isCompleted": False,
        "completedAt": None,
    }


def build_study_block(target, block_type, estimated_minutes, config):
    """
    단일 학습 대상과 배정 시간을 실제 학습 블록 데이터로 변환한다.

    평균 풀이시간과 해설/오답 정리 시간을 기준으로 문제 수를 계산하고,
    블록 유형에 맞는 활동 문구를 만든다.
    """
    average_time_sec = target["averageTimeSec"] or config["default_average_time_sec"]
    unit_time_sec = average_time_sec + config["review_time_sec"]
    question_count = int((estimated_minutes * 60) // unit_time_sec)
    if question_count < config["min_question_count"]:
        question_count = config["min_question_count"]
    elif question_count > config["max_question_count"]:
        question_count = config["max_question_count"]

    activity = f"{target['label']} 취약 문제 풀이 및 해설 정리"
    if block_type == "predictionFocus":
        activity = f"{target['label']} 출제 예상 문제 풀이"

    return {
        "blockId": str(uuid4()),
        "blockType": block_type,
        "classification": target["classification"],
        "label": target["label"],
        "era": target.get("era", ""),
        "topic": target.get("topic", ""),
        "qType": target.get("qType", ""),
        "activity": activity,
        "questionCount": question_count,
        "estimatedMinutes": estimated_minutes,
        "priorityScore": target["priorityScore"],
        "reason": target["reason"],
        "isCompleted": False,
        "completedAt": None,
    }


def build_priority_reason(target):
    """
    우선순위 점수에 사용된 근거를 사용자 표시 문장으로 만든다.

    오답률, 출제 예상도, 평균 풀이시간, 예측 사유를 조합해
    학습계획 블록의 reason 필드로 사용할 문자열을 반환한다.
    """
    reasons = []
    if target["wrongRate"]:
        wrong_rate = round(target["wrongRate"] * 100)
        reasons.append(f"오답률 {wrong_rate}%")
    if target["predictionScore"]:
        prediction_rate = round(target["predictionScore"] * 100)
        reasons.append(f"출제 예상도 {prediction_rate}%")
    if target["averageTimeSec"]:
        reasons.append("평균 풀이시간이 긴 항목")
    if target["predictionReason"]:
        reasons.append(target["predictionReason"])
    if reasons:
        return " / ".join(reasons)

    return "학습 유지가 필요한 항목입니다."


def build_plan_summary(priority_targets, daily_available_minutes, config):
    """
    생성된 학습계획의 한 줄 요약 문장을 만든다.

    우선순위 대상이 없으면 생성 불가 안내를 반환하고,
    있으면 최상위 취약 항목과 기간/시간 기준을 포함해 요약한다.
    """
    if not priority_targets:
        return "취약점과 출제 예상 데이터가 부족해 학습 계획을 생성하지 못했습니다."

    top_target = priority_targets[0]
    return (
        f"{config['weekly_plan_days']}일 동안 하루 {daily_available_minutes}분 기준으로 "
        f"{top_target['label']} 중심의 {config['weekly_learning_days']}일 학습과 "
        f"{config['weekly_plan_days']}일차 주간 평가 계획을 생성했습니다."
    )


def find_review_target(scheduled_targets, day_offset, review_offsets):
    """
    이전에 배치된 학습 대상 중 현재 날짜에 복습해야 할 대상을 찾는다.

    현재 day_offset과 과거 배치일의 차이가 설정된 복습 간격에 포함되면
    해당 대상을 review 블록 후보로 반환한다.
    """
    for scheduled_target in scheduled_targets:
        if day_offset - scheduled_target["dayOffset"] in review_offsets:
            return scheduled_target["target"]

    return None


def get_block_minutes(remaining_minutes, blocks_per_day, current_block_count, config):
    """
    현재 블록에 배정할 학습 시간을 계산한다.

    남은 시간을 남은 블록 수로 나누되,
    최소 블록 시간과 남은 시간 범위를 벗어나지 않도록 보정한다.
    """
    remaining_blocks = blocks_per_day - current_block_count
    block_minutes = remaining_minutes
    if remaining_blocks > 0:
        block_minutes = remaining_minutes // remaining_blocks
    if block_minutes < config["min_block_minutes"]:
        block_minutes = config["min_block_minutes"]
    if block_minutes > remaining_minutes:
        block_minutes = remaining_minutes

    return block_minutes


def get_blocks_per_day(daily_available_minutes, config):
    """
    하루 학습 가능 시간에 따라 하루에 배치할 블록 수를 결정한다.

    짧은 학습 시간은 1개 블록, 중간 시간은 2개 블록,
    충분한 시간은 3개 블록으로 나눠 과밀한 계획을 피한다.
    """
    blocks_per_day = config["large_daily_block_count"]
    if daily_available_minutes < config["small_daily_available_minutes"]:
        blocks_per_day = config["small_daily_block_count"]
    elif daily_available_minutes < config["medium_daily_available_minutes"]:
        blocks_per_day = config["medium_daily_block_count"]

    return blocks_per_day


def get_review_offsets(remaining_days, config):
    """
    남은 기간에 맞는 복습 간격 목록을 반환한다.

    단기 계획은 짧은 복습 간격만 사용하고,
    중기/장기 계획은 1일, 3일, 7일 복습 구조를 점진적으로 적용한다.
    """
    review_offsets = config["long_term_review_offsets"]
    if remaining_days <= config["short_term_days"]:
        review_offsets = config["short_term_review_offsets"]
    elif remaining_days <= config["medium_term_days"]:
        review_offsets = config["medium_term_review_offsets"]

    return review_offsets


def get_target_block_type(target):
    """
    학습 대상의 블록 유형을 결정한다.

    출제 예상도가 오답률보다 높으면 predictionFocus,
    그 외에는 새 취약점 보완 블록인 newWeakness로 분류한다.
    """
    block_type = "newWeakness"
    if target["predictionScore"] > target["wrongRate"]:
        block_type = "predictionFocus"

    return block_type


def get_study_strategy(remaining_days, config):
    """
    남은 기간에 따라 단기, 중기, 장기 학습 전략을 선택한다.

    전략 이름은 priorityScore 계산 시 취약도/출제예상도/시간부담
    가중치를 선택하는 키로 사용된다.
    """
    strategy = "long"
    if remaining_days <= config["short_term_days"]:
        strategy = "short"
    elif remaining_days <= config["medium_term_days"]:
        strategy = "medium"

    return strategy


def get_study_plan_config():
    """
    학습계획 생성에 사용하는 설정값과 전략별 가중치를 반환한다.

    하루 블록 수, 문제 수 제한, 복습 간격, 기간별 우선순위 가중치 등
    계획 생성 로직에서 반복적으로 쓰는 값을 한곳에서 관리한다.
    """
    return {
        "default_remaining_days": 14,
        "same_day_plan_days": 1,
        "weekly_plan_days": 7,
        "weekly_learning_days": 6,
        "weekly_review_block_type": "weekly_review",
        "weekly_review_question_count": 50,
        "weekly_review_minutes": 80,
        "daily_delete_limit": 2,
        "daily_delete_count_key": "deletedBlockCount",
        "daily_delete_count_date_key": "deletedBlockCountDate",
        "history_display_limit": 3,
        "fallback_daily_available_minutes": 60,
        "small_daily_available_minutes": 45,
        "medium_daily_available_minutes": 90,
        "small_daily_block_count": 1,
        "medium_daily_block_count": 2,
        "large_daily_block_count": 3,
        "max_plan_days": 30,
        "min_block_minutes": 15,
        "default_average_time_sec": 60,
        "review_time_sec": 90,
        "min_question_count": 3,
        "max_question_count": 20,
        "minimum_priority_score": 0.01,
        "short_term_days": 7,
        "medium_term_days": 21,
        "short_term_review_offsets": [1],
        "medium_term_review_offsets": [1, 3],
        "long_term_review_offsets": [1, 3, 7],
        "strategy_weights": {
            "short": {
                "weakness": 0.4,
                "prediction": 0.45,
                "time_burden": 0.15,
            },
            "medium": {
                "weakness": 0.45,
                "prediction": 0.4,
                "time_burden": 0.15,
            },
            "long": {
                "weakness": 0.55,
                "prediction": 0.3,
                "time_burden": 0.15,
            },
        },
    }

import json

from analytics.models import StudyPlanMypage
from analytics.serializers import serialize_study_plan, serialize_study_plans
from django.utils import timezone
from user.models import UserStudyProfile


def get_daily_available_minutes(user_id):
    profile = UserStudyProfile.objects.filter(user_id=user_id).first()
    if profile is None:
        return 0

    return int(float(profile.daily_available_hours) * 60)


def format_plan_items(study_plan_items):
    if study_plan_items is None:
        return "[]"
    elif isinstance(study_plan_items, str):
        return study_plan_items

    return json.dumps(study_plan_items, ensure_ascii=False)


def get_study_plan_info(user_id):
    daily_available_minutes = get_daily_available_minutes(user_id)
    study_plans = StudyPlanMypage.objects.filter(user_id=user_id).order_by(
        "-modified_at",
    )
    return serialize_study_plans(study_plans, daily_available_minutes)


def create_study_plan(user_id, study_plans="", study_plan_items=None):
    now = timezone.now()
    if study_plan_items is None:
        study_plan_items = []

    study_plan = StudyPlanMypage.objects.create(
        user_id=user_id,
        study_plans=study_plans,
        study_plan_items=format_plan_items(study_plan_items),
        created_at=now,
        modified_at=now,
    )
    daily_available_minutes = get_daily_available_minutes(user_id)
    return serialize_study_plan(study_plan, daily_available_minutes)


def update_study_plan(user_id, study_plan_id, study_plans, study_plan_items):
    study_plan = StudyPlanMypage.objects.get(
        user_id=user_id,
        studyplan_id=study_plan_id,
    )
    study_plan.study_plans = study_plans
    study_plan.study_plan_items = format_plan_items(study_plan_items)
    study_plan.modified_at = timezone.now()
    study_plan.save(
        update_fields=["study_plans", "study_plan_items", "modified_at"],
    )
    daily_available_minutes = get_daily_available_minutes(user_id)
    return serialize_study_plan(study_plan, daily_available_minutes)


def delete_study_plan(user_id, study_plan_id):
    study_plan = StudyPlanMypage.objects.get(
        user_id=user_id,
        studyplan_id=study_plan_id,
    )
    daily_available_minutes = get_daily_available_minutes(user_id)
    deleted_study_plan = serialize_study_plan(study_plan, daily_available_minutes)
    study_plan.delete()
    return deleted_study_plan

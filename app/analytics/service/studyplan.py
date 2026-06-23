# 학습계획 정보 조회 서비스
from app.analytics.models import StudyPlanMypage
from django.utils import timezone

import uuid

def get_study_plan_info(user_id):
    # 학습계획 정보 조회
    study_plan = StudyPlanMypage.objects.get(user_id=user_id)
    return study_plan

def create_study_plan(user_id):
    # 학습계획 생성
    study_plan = StudyPlanMypage.objects.create(user_id=user_id)
    study_plan.study_plan_id = uuid.uuid4()
    study_plan.study_plans = "[]"
    study_plan.study_plan_items = "[]"
    study_plan.created_at = timezone.now()
    study_plan.modified_at = timezone.now()
    study_plan.save()
    return study_plan



def update_study_plan(user_id, study_plan_id, study_plans, study_plan_items):
    # 학습계획 수정
    study_plan = StudyPlanMypage.objects.get(user_id=user_id, study_plan_id=study_plan_id)
    study_plan.study_plans = study_plans
    study_plan.study_plan_items = study_plan_items
    study_plan.save()
    return study_plan

def delete_study_plan(user_id, study_plan_id):
    # 학습계획 삭제
    study_plan = StudyPlanMypage.objects.get(user_id=user_id, study_plan_id=study_plan_id)
    study_plan.delete()
    return study_plan
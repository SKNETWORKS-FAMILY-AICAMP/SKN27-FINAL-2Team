from analytics.models import NoteMypage
from analytics.service.analystic import analytics_summary
from analytics.service.studyplan import get_study_plan_info


def get_mypage_info(user_id):
    notes = NoteMypage.objects.filter(user_id=user_id).order_by("-modified_at")
    return {
        "studyPlans": get_study_plan_info(user_id),
        "notes": list(notes),
        "analytics": analytics_summary(user_id),
    }

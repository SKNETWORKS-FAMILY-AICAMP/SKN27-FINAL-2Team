from django.shortcuts import render
from django.contrib.auth.decorators import login_required

from analytics.service.analytics import analytics_summary
from analytics.service.studyplan import get_study_plan_info



@login_required
def mypage(request):
    user_id = request.user.user_id

    analytics = analytics_summary(user_id)
    study_plan = get_study_plan_info(user_id)

    return render(
        request,
        "analytics/mypage.html",
        {
            "user": request.user,
            "analytics": analytics,
            "study_plan": study_plan,
        },
    )
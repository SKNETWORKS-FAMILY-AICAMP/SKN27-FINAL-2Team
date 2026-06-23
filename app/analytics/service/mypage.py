# mypage에서 분석 대시보드, 학습계획 정보 조회 서비스

from app.analytics.models import StudyPlanMypage, NoteMypage

def get_mypage_info(user_id):
    # 학습계획 정보 조회
    study_plan = StudyPlanMypage.objects.get(user_id=user_id)
    # 오답노트 정보 조회
    note = NoteMypage.objects.get(user_id=user_id)
    # 분석 대시보드 정보 조회
    
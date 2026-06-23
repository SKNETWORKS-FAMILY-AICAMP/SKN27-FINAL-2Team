from rest_framework import serializers


# 선택지 1개의 응답 형식
class ChoiceData(serializers.Serializer):
    choice_id = serializers.IntegerField()
    choice_no = serializers.IntegerField()
    content = serializers.CharField()


# 문제 1개의 기본 응답 형식
class QuestionData(serializers.Serializer):
    question_id = serializers.IntegerField()
    content = serializers.CharField()
    passage = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    visual_note = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    question_image_path = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    q_score = serializers.IntegerField()
    era = serializers.CharField()
    topic = serializers.CharField()
    question_type = serializers.CharField()
    question_subtype = serializers.CharField()
    choices = ChoiceData(many=True)


# 저장된 풀이 세션에서 문제와 사용자의 임시 답안을 함께 내려주는 형식
class SavedQuestionData(QuestionData):
    selected_choice_id = serializers.IntegerField(allow_null=True)
    selected_choice_no = serializers.IntegerField(allow_null=True)
    time_spent_ms = serializers.IntegerField(allow_null=True)
    is_answered = serializers.BooleanField()


# 문제 생성 화면의 필터 목록 응답 형식
class FilterOptionsResponse(serializers.Serializer):
    eras = serializers.ListField(child=serializers.CharField())
    topics = serializers.ListField(child=serializers.CharField())
    difficulties = serializers.ListField(child=serializers.CharField())
    question_types = serializers.ListField(child=serializers.CharField())
    question_subtypes = serializers.ListField(child=serializers.CharField())
    counts = serializers.ListField(child=serializers.IntegerField())


# 문제 생성 요청 형식
class StartQuestionsRequest(serializers.Serializer):
    eras = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    topics = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    difficulties = serializers.ListField(
        child=serializers.ChoiceField(choices=["상", "중", "하"]),
        required=False,
        default=list,
    )
    question_types = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    question_subtypes = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    count = serializers.IntegerField(min_value=1, default=20)


# 문제 생성 결과 응답 형식
class StartQuestionsResponse(serializers.Serializer):
    session_id = serializers.IntegerField(allow_null=True)
    total_count = serializers.IntegerField()
    is_saved = serializers.BooleanField()
    questions = QuestionData(many=True)


# 저장된 풀이 세션 조회 응답 형식
class SavedSessionResponse(serializers.Serializer):
    session_id = serializers.IntegerField()
    session_type = serializers.CharField()
    total_count = serializers.IntegerField()
    elapsed_sec = serializers.IntegerField(allow_null=True)
    remaining_sec = serializers.IntegerField()
    status = serializers.CharField()
    answered_count = serializers.IntegerField()
    questions = SavedQuestionData(many=True)


# 이어 풀 수 있는 세션 1개의 응답 형식
class InProgressSessionData(serializers.Serializer):
    session_id = serializers.IntegerField()
    total_count = serializers.IntegerField()
    answered_count = serializers.IntegerField()
    recorded_date = serializers.DateField()
    status = serializers.CharField()


# 이어 풀 수 있는 세션 목록 응답 형식
class InProgressSessionsResponse(serializers.Serializer):
    sessions = InProgressSessionData(many=True)


# 답안 임시 저장 요청 형식
class SaveAnswerRequest(serializers.Serializer):
    question_id = serializers.IntegerField()
    choice_id = serializers.IntegerField(allow_null=True, required=False, default=None)
    time_spent_ms = serializers.IntegerField(allow_null=True, required=False, default=None)
    elapsed_sec = serializers.IntegerField(allow_null=True, required=False, default=None)


# 답안 임시 저장 결과 응답 형식
class SaveAnswerResponse(serializers.Serializer):
    session_id = serializers.IntegerField()
    question_id = serializers.IntegerField()
    selected_choice_id = serializers.IntegerField(allow_null=True)
    selected_choice_no = serializers.IntegerField(allow_null=True)
    time_spent_ms = serializers.IntegerField(allow_null=True)
    elapsed_sec = serializers.IntegerField(allow_null=True)
    is_answered = serializers.BooleanField()

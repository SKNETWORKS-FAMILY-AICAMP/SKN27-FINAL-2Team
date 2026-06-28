from rest_framework import serializers


# ── Start ──────────────────────────────────────────────────────────────────────

class DiagnosisStartRequestSerializer(serializers.Serializer):
    """POST /api/diagnosis/start/"""
    pass  # 인증 연동 전까지 body 불필요 (user_id는 서버에서 하드코딩)


class ChoiceSerializer(serializers.Serializer):
    choice_id = serializers.IntegerField()
    choice_no = serializers.IntegerField()   # 셔플 후 표시 번호
    content = serializers.CharField()
    choice_image_path = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    choice_explanation = serializers.CharField(allow_blank=True, allow_null=True, required=False)


class DiagnosisQuestionSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    content = serializers.CharField()
    passage = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    image_caption = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    visual_note = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    question_image_path = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    q_score = serializers.IntegerField()
    era = serializers.CharField()
    topic = serializers.CharField()
    question_type = serializers.CharField()
    question_subtype = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    choices = ChoiceSerializer(many=True)


class DiagnosisStartResponseSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()
    total_count = serializers.IntegerField()
    time_limit_sec = serializers.IntegerField()
    questions = DiagnosisQuestionSerializer(many=True)


# ── Submit ─────────────────────────────────────────────────────────────────────

class AnswerItemSerializer(serializers.Serializer):
    """단일 답안"""
    question_id = serializers.IntegerField()
    selected_no = serializers.IntegerField(
        required=False, allow_null=True, default=None
    )
    choice_id = serializers.IntegerField(
        required=False, allow_null=True, default=None
    )  # 미응답 시 null
    time_spent_ms = serializers.IntegerField(
        required=False, allow_null=True, default=None
    )


class DiagnosisSubmitRequestSerializer(serializers.Serializer):
    """POST /api/diagnosis/submit/"""
    session_id = serializers.IntegerField()
    elapsed_sec = serializers.IntegerField()
    answers = AnswerItemSerializer(many=True)


class DiagnosisSubmitResponseSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()
    redirect_url = serializers.CharField()


# ── Result ─────────────────────────────────────────────────────────────────────

class AnalyticsItemSerializer(serializers.Serializer):
    label = serializers.CharField()       # 시대명 / 유형명
    classification = serializers.CharField()  # '시대' | '유형'
    total = serializers.IntegerField()
    correct = serializers.IntegerField()
    wrong_rate = serializers.FloatField()  # 0.0 ~ 1.0


class DiagnosisResultResponseSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()
    total_count = serializers.IntegerField()
    correct_count = serializers.IntegerField()
    total_score = serializers.IntegerField()
    max_score = serializers.IntegerField()
    score_rate = serializers.FloatField()     # 취득점 / 최대점
    expected_grade = serializers.CharField()  # '1급' | '2급' | '3급' | '탈락'
    era_analytics = AnalyticsItemSerializer(many=True)
    type_analytics = AnalyticsItemSerializer(many=True)
    question_ids = serializers.ListField(
        child=serializers.IntegerField()
    )  # 해설 조회용 question_id 목록


# ── Explanation ────────────────────────────────────────────────────────────────

class DiagnosisExplanationResponseSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    content = serializers.CharField()
    passage = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    visual_note = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    question_image_path = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    q_score = serializers.IntegerField()
    era = serializers.CharField(allow_null=True)
    topic = serializers.CharField(allow_null=True)
    question_type = serializers.CharField(allow_null=True)
    question_subtype = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    correct_choice_no = serializers.IntegerField()
    correct_choice_id = serializers.IntegerField(allow_null=True, required=False)
    answer_explanation = serializers.CharField(allow_null=True)
    core_concept = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    time_spent_ms = serializers.IntegerField(allow_null=True, required=False)
    choices = ChoiceSerializer(many=True)          # 원래 순서 (choice_no 기준)
    user_choice_no = serializers.IntegerField(allow_null=True)
    user_choice_id = serializers.IntegerField(allow_null=True, required=False)
    is_correct = serializers.BooleanField()
    chatbot_url = serializers.CharField()          # 챗봇 연결 URL

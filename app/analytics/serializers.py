import json

from rest_framework import serializers


class StudyPlanBlockData(serializers.Serializer):
    blockId = serializers.CharField(required=False)
    groupKeyId = serializers.CharField(required=False, allow_blank=True)
    blockType = serializers.CharField()
    classification = serializers.CharField()
    label = serializers.CharField()
    era = serializers.CharField(required=False, allow_blank=True)
    topic = serializers.CharField(required=False, allow_blank=True)
    qType = serializers.CharField(required=False, allow_blank=True)
    activity = serializers.CharField()
    questionCount = serializers.IntegerField()
    estimatedMinutes = serializers.IntegerField()
    priorityScore = serializers.FloatField()
    reason = serializers.CharField()
    isCompleted = serializers.BooleanField(required=False)
    completedAt = serializers.CharField(required=False, allow_null=True)


class StudyPlanDayData(serializers.Serializer):
    date = serializers.DateField()
    blocks = StudyPlanBlockData(many=True)


class StudyPlanSerializer(serializers.Serializer):
    studyPlanId = serializers.IntegerField(source="studyplan_id")
    status = serializers.CharField()
    planVersion = serializers.IntegerField(source="plan_version")
    summary = serializers.SerializerMethodField()
    totalDays = serializers.SerializerMethodField()
    dailyAvailableMinutes = serializers.SerializerMethodField()
    completionRate = serializers.FloatField(source="completion_rate")
    startDate = serializers.DateField(source="start_date", allow_null=True)
    endDate = serializers.DateField(source="end_date", allow_null=True)
    plans = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at")
    updatedAt = serializers.DateTimeField(source="modified_at")
    archivedAt = serializers.DateTimeField(source="archived_at", allow_null=True)
    deletedAt = serializers.DateTimeField(source="deleted_at", allow_null=True)

    def get_summary(self, study_plan):
        return study_plan.study_plans or ""

    def get_totalDays(self, study_plan):
        return len(parse_study_plan_items(study_plan.study_plan_items))

    def get_dailyAvailableMinutes(self, study_plan):
        daily_available_minutes = self.context.get("daily_available_minutes")
        if daily_available_minutes is None:
            return 0

        return daily_available_minutes

    def get_plans(self, study_plan):
        return parse_study_plan_items(study_plan.study_plan_items)


def parse_study_plan_items(study_plan_items):
    if isinstance(study_plan_items, list):
        return study_plan_items
    elif not study_plan_items:
        return []

    try:
        parsed_items = json.loads(study_plan_items)
    except (TypeError, json.JSONDecodeError):
        return []

    if isinstance(parsed_items, list):
        return parsed_items

    return []


def serialize_study_plan(study_plan, daily_available_minutes=0):
    serializer = StudyPlanSerializer(
        study_plan,
        context={"daily_available_minutes": daily_available_minutes},
    )
    return dict(serializer.data)


def serialize_study_plans(study_plans, daily_available_minutes=0):
    serializer = StudyPlanSerializer(
        study_plans,
        many=True,
        context={"daily_available_minutes": daily_available_minutes},
    )
    return list(serializer.data)

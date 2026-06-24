import json
from datetime import timedelta

from django.utils import timezone

from analytics.models import StudyPlanMypage
from analytics.serializers import serialize_study_plan, serialize_study_plans
from analytics.service.analytics import get_weak_targets
from analytics.service.prediction import get_predicted_targets
from user.models import UserStudyProfile


def get_user_study_info(user_id):
    return UserStudyProfile.objects.filter(user_id=user_id).first()


def get_daily_available_minutes(user_id):
    profile = get_user_study_info(user_id)
    if profile and profile.daily_available_hours is not None:
        return int(float(profile.daily_available_hours) * 60)

    return 0


def get_remaining_days(user_id, today=None):
    config = get_study_plan_config()
    base_date = today or timezone.localdate()
    profile = get_user_study_info(user_id)
    if profile and profile.exam_date:
        remaining_days = (profile.exam_date - base_date).days
        if remaining_days > 0:
            return remaining_days
        elif remaining_days <= 0:
            return config["same_day_plan_days"]

    return config["default_remaining_days"]


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


def create_study_plan(user_id, study_plans="", study_plan_items=None, predicted_targets=None):
    if study_plan_items is None:
        generated_plan = build_user_study_plan(user_id, predicted_targets)
        study_plan_items = generated_plan["plans"]
        if not study_plans:
            study_plans = generated_plan["summary"]

    now = timezone.now()
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


def build_user_study_plan(user_id, predicted_targets=None, today=None):
    config = get_study_plan_config()
    base_date = today or timezone.localdate()
    remaining_days = get_remaining_days(user_id, base_date)
    daily_available_minutes = get_daily_available_minutes(user_id)
    if daily_available_minutes <= 0:
        daily_available_minutes = config["fallback_daily_available_minutes"]

    weak_targets = get_weak_targets(user_id)
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
        "summary": build_plan_summary(priority_targets, remaining_days, daily_available_minutes),
        "dailyAvailableMinutes": daily_available_minutes,
        "remainingDays": remaining_days,
        "plans": plans,
    }


def build_priority_targets(weak_targets, predicted_targets, remaining_days, config):
    target_map = {}
    for weak_target in weak_targets:
        classification = weak_target.get("classification")
        label = weak_target.get("label")
        if classification and label:
            key = (classification, label)
            target = target_map.setdefault(
                key,
                {
                    "classification": classification,
                    "label": label,
                    "wrongRate": 0.0,
                    "predictionScore": 0.0,
                    "averageTimeSec": 0,
                    "predictionReason": "",
                },
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
        classification = predicted_target.get("classification")
        label = predicted_target.get("label")
        if classification and label:
            key = (classification, label)
            target = target_map.setdefault(
                key,
                {
                    "classification": classification,
                    "label": label,
                    "wrongRate": 0.0,
                    "predictionScore": 0.0,
                    "averageTimeSec": 0,
                    "predictionReason": "",
                },
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
        ),
    )


def build_daily_plan_items(priority_targets, daily_available_minutes, remaining_days, today, config):
    if not priority_targets:
        return []

    plan_days = remaining_days
    if plan_days < 1:
        plan_days = config["same_day_plan_days"]
    elif plan_days > config["max_plan_days"]:
        plan_days = config["max_plan_days"]

    blocks_per_day = get_blocks_per_day(daily_available_minutes, config)
    review_offsets = get_review_offsets(remaining_days, config)
    target_index = 0
    scheduled_targets = []
    plans = []

    for day_offset in range(plan_days):
        plan_date = today + timedelta(days=day_offset)
        remaining_minutes = daily_available_minutes
        used_target_keys = set()
        blocks = []

        review_target = find_review_target(scheduled_targets, day_offset, review_offsets)
        if review_target and blocks_per_day > 1:
            review_minutes = get_block_minutes(remaining_minutes, blocks_per_day, len(blocks), config)
            blocks.append(build_study_block(review_target, "review", review_minutes, config))
            remaining_minutes -= review_minutes
            used_target_keys.add((review_target["classification"], review_target["label"]))

        while len(blocks) < blocks_per_day and remaining_minutes >= config["min_block_minutes"]:
            target = priority_targets[target_index % len(priority_targets)]
            target_index += 1
            target_key = (target["classification"], target["label"])
            if target_key in used_target_keys:
                if len(used_target_keys) >= len(priority_targets):
                    break
                continue

            block_type = get_target_block_type(target)
            block_minutes = get_block_minutes(remaining_minutes, blocks_per_day, len(blocks), config)
            blocks.append(build_study_block(target, block_type, block_minutes, config))
            used_target_keys.add(target_key)
            scheduled_targets.append(
                {
                    "target": target,
                    "dayOffset": day_offset,
                }
            )
            remaining_minutes -= block_minutes

        if blocks:
            plans.append(
                {
                    "date": plan_date.isoformat(),
                    "blocks": blocks,
                }
            )

    return plans


def build_study_block(target, block_type, estimated_minutes, config):
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
    elif block_type == "review":
        activity = f"{target['label']} 오답 복습"

    return {
        "blockType": block_type,
        "classification": target["classification"],
        "label": target["label"],
        "activity": activity,
        "questionCount": question_count,
        "estimatedMinutes": estimated_minutes,
        "priorityScore": target["priorityScore"],
        "reason": target["reason"],
    }


def build_priority_reason(target):
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


def build_plan_summary(priority_targets, remaining_days, daily_available_minutes):
    if not priority_targets:
        return "취약점과 출제 예상 데이터가 부족해 학습 계획을 생성하지 못했습니다."

    top_target = priority_targets[0]
    return (
        f"{remaining_days}일 동안 하루 {daily_available_minutes}분 기준으로 "
        f"{top_target['label']} 중심의 취약점 보완 계획을 생성했습니다."
    )


def find_review_target(scheduled_targets, day_offset, review_offsets):
    for scheduled_target in scheduled_targets:
        if day_offset - scheduled_target["dayOffset"] in review_offsets:
            return scheduled_target["target"]

    return None


def get_block_minutes(remaining_minutes, blocks_per_day, current_block_count, config):
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
    blocks_per_day = config["large_daily_block_count"]
    if daily_available_minutes < config["small_daily_available_minutes"]:
        blocks_per_day = config["small_daily_block_count"]
    elif daily_available_minutes < config["medium_daily_available_minutes"]:
        blocks_per_day = config["medium_daily_block_count"]

    return blocks_per_day


def get_review_offsets(remaining_days, config):
    review_offsets = config["long_term_review_offsets"]
    if remaining_days <= config["short_term_days"]:
        review_offsets = config["short_term_review_offsets"]
    elif remaining_days <= config["medium_term_days"]:
        review_offsets = config["medium_term_review_offsets"]

    return review_offsets


def get_target_block_type(target):
    block_type = "newWeakness"
    if target["predictionScore"] > target["wrongRate"]:
        block_type = "predictionFocus"

    return block_type


def get_study_strategy(remaining_days, config):
    strategy = "long"
    if remaining_days <= config["short_term_days"]:
        strategy = "short"
    elif remaining_days <= config["medium_term_days"]:
        strategy = "medium"

    return strategy


def get_study_plan_config():
    return {
        "default_remaining_days": 14,
        "same_day_plan_days": 1,
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

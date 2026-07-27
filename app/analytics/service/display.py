from datetime import date, datetime

from analytics.service.studyplan import get_study_plan_config
from analytics.service.weakness import get_status_class, get_weakness_config


def parse_display_date(raw_date):
    if isinstance(raw_date, datetime):
        return raw_date.date()
    if isinstance(raw_date, date):
        return raw_date
    if isinstance(raw_date, str):
        try:
            return date.fromisoformat(raw_date[:10])
        except ValueError:
            return None

    return None


def build_planner_summary(study_plans, today, plan_generation_available=True):
    """
    저장된 학습계획 목록을 마이페이지 달력 표시용 데이터로 변환한다.

    날짜별 계획 목록, 완료/예정 날짜 키, 오늘 표시 데이터,
    모달에서 사용할 오늘 학습 항목을 함께 구성한다.
    """
    achieved_label = "달성"
    default_title = "학습 계획"
    missed_label = "미달성"
    missed_weekly_review_label = "미응시"
    planned_label = "예정"
    today_label = "오늘"
    config = get_study_plan_config()
    weekly_review_block_type = config["weekly_review_block_type"]
    weekly_review_label = "주간 평가"
    weekly_review_question_count = config["weekly_review_question_count"]
    weekly_review_minutes = config["weekly_review_minutes"]
    daily_delete_limit = config["daily_delete_limit"]
    daily_delete_count_key = config["daily_delete_count_key"]
    daily_delete_count_date_key = config["daily_delete_count_date_key"]
    plans_by_date = {}
    plan_learning_completion = build_plan_learning_completion_lookup(
        study_plans,
        weekly_review_block_type,
    )

    for study_plan in study_plans:
        study_plan_id = study_plan.get("studyPlanId")
        plan_start_date = parse_display_date(study_plan.get("startDate"))
        plan_end_date = parse_display_date(study_plan.get("endDate"))
        plan_start_key = ""
        plan_end_key = ""
        if plan_start_date:
            plan_start_key = plan_start_date.isoformat()
        if plan_end_date:
            plan_end_key = plan_end_date.isoformat()
        today_delete_count = get_today_delete_count(
            study_plan.get("plans", []),
            today,
            daily_delete_count_key,
            daily_delete_count_date_key,
        )
        can_delete_more = today_delete_count < daily_delete_limit
        for day_index, day_plan in enumerate(study_plan.get("plans", [])):
            raw_date = day_plan.get("date")
            plan_date = parse_display_date(raw_date)
            date_key = ""
            if plan_date:
                date_key = plan_date.isoformat()

            blocks = day_plan.get("blocks", [])
            if date_key and plan_date and blocks:
                plans_by_date.setdefault(date_key, [])
                for block_index, block in enumerate(blocks):
                    block_type = block.get("blockType")
                    if block_type == "review":
                        continue

                    is_weekly_review = block_type == weekly_review_block_type
                    start_label = "문제 풀기"
                    if is_weekly_review:
                        start_label = "주간 평가 시작"
                    is_achieved = bool(block.get("isAchieved"))
                    status_label = planned_label
                    if is_achieved:
                        status_label = achieved_label
                    elif is_weekly_review and plan_date < today:
                        status_label = missed_weekly_review_label
                    elif plan_date < today:
                        status_label = missed_label
                    elif plan_date == today:
                        status_label = today_label

                    is_past_plan = plan_date < today
                    is_future_plan = plan_date > today
                    show_start = not is_past_plan
                    can_start = plan_date == today and not is_achieved
                    if is_weekly_review:
                        can_start = (
                            can_start
                            and plan_learning_completion.get(study_plan_id, False)
                        )
                    if is_weekly_review and is_achieved:
                        can_start = False
                        start_label = "주간 평가 완료"
                    can_delete = (
                        not is_weekly_review
                        and plan_date == today
                        and not is_achieved
                        and can_delete_more
                    )
                    meta_parts = [status_label]
                    classification = block.get("classification")
                    label = block.get("label")
                    era = block.get("era") or ""
                    topic = block.get("topic") or ""
                    q_type = block.get("qType") or block.get("q_type") or block.get("questionType") or ""
                    composite_title = " · ".join(
                        value for value in [era, topic, q_type] if value
                    )
                    title = label or composite_title or classification or default_title
                    if is_weekly_review:
                        title = label or weekly_review_label
                    question_count = block.get("questionCount")
                    estimated_minutes = block.get("estimatedMinutes")
                    achieved_count = block.get("achievedCount") or 0
                    remaining_count = block.get("remainingCount") or 0
                    progress_percent = block.get("progressPercent") or 0
                    progress_mode = block.get("progressMode") or "question"
                    block_status_label = block.get("statusLabel") or ""
                    if is_weekly_review:
                        question_count = weekly_review_question_count
                        estimated_minutes = weekly_review_minutes
                        remaining_count = question_count
                        if is_achieved:
                            achieved_count = question_count
                            remaining_count = 0
                            progress_percent = 100
                    classification_label = classification or ""
                    if classification == "복합":
                        classification_label = "복합 취약점"
                    if is_weekly_review:
                        meta_parts.append("개선도 확인")
                    elif classification:
                        meta_parts.append(classification_label)
                    if progress_mode == "review" and block_status_label:
                        meta_parts.append(block_status_label)
                    elif question_count:
                        meta_parts.append(f"{achieved_count}/{question_count}문항")
                    if remaining_count:
                        meta_parts.append(f"앞으로 {remaining_count}문항")
                    if estimated_minutes:
                        meta_parts.append(f"{estimated_minutes}분")

                    plans_by_date[date_key].append(
                        {
                            "studyPlanId": study_plan_id,
                            "planStartDate": plan_start_key,
                            "planEndDate": plan_end_key,
                            "dayIndex": day_index,
                            "blockIndex": block_index,
                            "blockId": block.get("blockId"),
                            "classification": classification or "",
                            "label": label or "",
                            "era": era,
                            "topic": topic,
                            "qType": q_type,
                            "title": title,
                            "meta": " · ".join(meta_parts),
                            "done": is_achieved,
                            "questionCount": question_count or 0,
                            "achievedCount": achieved_count,
                            "remainingCount": remaining_count,
                            "progressPercent": progress_percent,
                            "progressMode": progress_mode,
                            "statusLabel": block_status_label,
                            "canConfirm": progress_mode == "review" and not is_achieved,
                            "isWeeklyReview": is_weekly_review,
                            "showStart": show_start,
                            "canStart": can_start,
                            "canDelete": can_delete,
                            "deleteCount": today_delete_count,
                            "deleteLimit": daily_delete_limit,
                            "startLabel": start_label,
                        }
                    )

    display_plans_by_date = plans_by_date

    completed_keys = []
    planned_keys = []
    for date_key, plan_items in display_plans_by_date.items():
        plan_date = date.fromisoformat(date_key)
        is_achieved_date = bool(plan_items) and all(item["done"] for item in plan_items)
        if is_achieved_date:
            completed_keys.append(date_key)
        elif plan_date >= today:
            planned_keys.append(date_key)

    today_key = today.isoformat()
    progress_by_date = build_calendar_progress_by_date(display_plans_by_date, today)
    selected_key = get_default_planner_selected_key(display_plans_by_date, today)
    selected_date = date.fromisoformat(selected_key)
    has_active_plan = bool(study_plans)
    active_plan_end_date = None
    if has_active_plan:
        active_plan_end_date = parse_display_date(study_plans[0].get("endDate"))
    is_expired_active_plan = bool(
        active_plan_end_date
        and active_plan_end_date < today
    )
    is_overloaded_active_plan = bool(
        has_active_plan
        and not is_expired_active_plan
        and is_study_plan_workload_overloaded(
            study_plans[0],
            today,
            weekly_review_block_type,
            config["regeneration_overload_multiplier"],
            config["fallback_daily_available_minutes"],
        )
    )
    weekly_review_done = has_completed_weekly_review(plans_by_date)
    is_recoverable_expired_plan = is_expired_active_plan and not weekly_review_done
    has_weekly_review = has_weekly_review_item(plans_by_date)
    last_plan_key = get_last_plan_key(plans_by_date)
    is_empty_active_plan = has_active_plan and not plans_by_date
    is_finished_legacy_plan = (
        has_active_plan
        and not has_weekly_review
        and last_plan_key
        and last_plan_key < today_key
    )
    can_create_plan = plan_generation_available and (
        not has_active_plan
        or is_finished_legacy_plan
        or is_empty_active_plan
        or is_recoverable_expired_plan
        or is_overloaded_active_plan
    )
    show_add_extra_study = (
        plan_generation_available
        and has_active_plan
        and not can_create_plan
        and not is_empty_active_plan
        and not is_expired_active_plan
        and not weekly_review_done
    )
    can_add_extra_study = (
        show_add_extra_study
        and is_due_learning_plan_completed(
            study_plans,
            today,
            weekly_review_block_type,
        )
    )
    create_plan_label = ""
    if not has_active_plan or is_empty_active_plan:
        create_plan_label = "7일 계획 만들기"
    elif is_overloaded_active_plan:
        create_plan_label = "학습계획 재생성"
    elif is_finished_legacy_plan or is_recoverable_expired_plan:
        create_plan_label = "다음 7일 계획 만들기"
    create_plan_confirm = ""
    if has_active_plan:
        create_plan_confirm = "기존 학습계획을 보관하고 다음 7일 계획을 만들까요?"
    active_study_plan_id = ""
    if has_active_plan:
        active_study_plan_id = study_plans[0].get("studyPlanId") or ""
    return {
        "month_label": f"{selected_date.year}년 {selected_date.month:02d}월",
        "day_label": f"{selected_date.month:02d}월 {selected_date.day:02d}일",
        # 계획 재생성 버튼이 "어느 계획을 대체할지" 를 서버에 알려야 한다.
        # 이 값이 없으면 생성 서비스가 활성 계획과 None 을 비교해 무반응이 된다.
        "active_study_plan_id": active_study_plan_id,
        "progress": build_planner_progress_summary(study_plans),
        "today_key": today_key,
        "selected_key": selected_key,
        "today_items": display_plans_by_date.get(today_key, []),
        "selected_items": display_plans_by_date.get(selected_key, []),
        "has_active_plan": has_active_plan,
        "is_expired_plan": is_expired_active_plan,
        "is_overloaded_plan": is_overloaded_active_plan,
        "plan_generation_available": plan_generation_available,
        "can_create_plan": can_create_plan,
        "show_add_extra_study": show_add_extra_study,
        "can_add_extra_study": can_add_extra_study,
        "create_plan_label": create_plan_label,
        "create_plan_confirm": create_plan_confirm,
        "data": {
            "plansByDate": display_plans_by_date,
            "completedKeys": sorted(completed_keys),
            "plannedKeys": sorted(planned_keys),
            "progressByDate": progress_by_date,
        },
    }


def is_study_plan_workload_overloaded(
    study_plan,
    today,
    weekly_review_block_type,
    overload_multiplier,
    fallback_daily_available_minutes,
):
    try:
        daily_available_minutes = int(study_plan.get("dailyAvailableMinutes") or 0)
    except (TypeError, ValueError):
        return False
    if daily_available_minutes <= 0:
        daily_available_minutes = fallback_daily_available_minutes

    incomplete_minutes = 0
    for day_plan in study_plan.get("plans", []):
        plan_date = parse_display_date(day_plan.get("date"))
        if plan_date is None or plan_date > today:
            continue
        for block in day_plan.get("blocks", []):
            if not is_learning_plan_block(block, weekly_review_block_type):
                continue
            if block.get("isAchieved") or block.get("isCompleted"):
                continue
            try:
                incomplete_minutes += max(int(block.get("estimatedMinutes") or 0), 0)
            except (TypeError, ValueError):
                continue

    return incomplete_minutes > daily_available_minutes * overload_multiplier


def build_plan_learning_completion_lookup(study_plans, weekly_review_block_type):
    completion_lookup = {}
    for study_plan in study_plans:
        study_plan_id = study_plan.get("studyPlanId")
        learning_blocks = []
        for day_plan in study_plan.get("plans", []):
            for block in day_plan.get("blocks", []):
                if is_learning_plan_block(block, weekly_review_block_type):
                    learning_blocks.append(block)

        completion_lookup[study_plan_id] = bool(learning_blocks) and all(
            bool(block.get("isAchieved") or block.get("isCompleted"))
            for block in learning_blocks
        )

    return completion_lookup


def is_learning_plan_block(block, weekly_review_block_type):
    block_type = block.get("blockType")
    if block_type == weekly_review_block_type:
        return False
    if block_type == "review":
        return False

    return True


def is_due_learning_plan_completed(study_plans, today, weekly_review_block_type):
    learning_blocks = []
    for study_plan in study_plans:
        for day_plan in study_plan.get("plans", []):
            plan_date = parse_display_date(day_plan.get("date"))
            if plan_date is None:
                continue
            if plan_date > today:
                continue

            for block in day_plan.get("blocks", []):
                if is_learning_plan_block(block, weekly_review_block_type):
                    learning_blocks.append(block)

    return bool(learning_blocks) and all(
        bool(block.get("isAchieved") or block.get("isCompleted"))
        for block in learning_blocks
    )


def build_calendar_progress_by_date(plans_by_date, today):
    progress_by_date = {}
    today_key = today.isoformat()
    for date_key, plan_items in plans_by_date.items():
        progress_percent = calculate_date_progress_percent(plan_items)
        hue = get_progress_hue(progress_percent)
        state = "future"
        label = "예정"
        if date_key < today_key:
            state = get_past_progress_state(progress_percent)
            label = f"{progress_percent}%"
            if state != "complete":
                hue = get_progress_hue(0)
        elif date_key == today_key:
            state = "today"
            label = f"{progress_percent}%"

        progress_by_date[date_key] = {
            "percent": progress_percent,
            "hue": hue,
            "state": state,
            "label": label,
        }

    return progress_by_date


def get_today_delete_count(plan_items, today, count_key, date_key):
    today_key = today.isoformat()
    delete_count = 0
    for day_plan in plan_items:
        if day_plan.get(date_key) != today_key:
            continue
        try:
            delete_count += int(day_plan.get(count_key) or 0)
        except (TypeError, ValueError):
            continue

    return delete_count


def calculate_date_progress_percent(plan_items):
    total_count = 0
    achieved_count = 0
    for item in plan_items:
        question_count = int(item.get("questionCount") or 0)
        if question_count <= 0:
            continue

        total_count += question_count
        achieved_count += int(item.get("achievedCount") or 0)

    if total_count:
        return round(min(achieved_count, total_count) / total_count * 100)

    if plan_items and all(item.get("done") for item in plan_items):
        return 100

    return 0


def get_past_progress_state(progress_percent):
    state = "partial"
    if progress_percent >= 100:
        state = "complete"
    elif progress_percent <= 0:
        state = "empty"

    return state


def get_progress_hue(progress_percent):
    minimum_percent = 0
    maximum_percent = 100
    red_hue = 0
    green_hue = 128
    bounded_percent = max(minimum_percent, min(maximum_percent, progress_percent))
    return round(red_hue + (green_hue - red_hue) * bounded_percent / maximum_percent)


def has_weekly_review_item(plans_by_date):
    for plan_items in plans_by_date.values():
        for item in plan_items:
            if item.get("isWeeklyReview"):
                return True

    return False


def has_completed_weekly_review(plans_by_date):
    for plan_items in plans_by_date.values():
        for item in plan_items:
            if item.get("isWeeklyReview") and item.get("done"):
                return True

    return False


def get_last_plan_key(plans_by_date):
    plan_keys = sorted(plans_by_date)
    if plan_keys:
        return plan_keys[-1]

    return ""


def get_default_planner_selected_key(plans_by_date, today):
    today_key = today.isoformat()
    if plans_by_date.get(today_key):
        return today_key

    future_keys = sorted(date_key for date_key in plans_by_date if date_key >= today_key)
    if future_keys:
        return future_keys[0]

    past_keys = sorted(plans_by_date)
    if past_keys:
        return past_keys[-1]

    return today_key


def build_planner_progress_summary(study_plans):
    """
    현재 플래너 상단에 표시할 active 계획의 달성률 요약을 반환한다.
    """
    if study_plans:
        progress = study_plans[0].get("progress")
        if progress:
            return progress

    return {
        "targetCount": 0,
        "achievedCount": 0,
        "remainingCount": 0,
        "completionRate": 0,
        "completionPercent": 0,
        "periodLabel": "기간 미정",
    }


def build_wrong_rate_display(stats, weakness_rows=None):
    """
    오답률 통계를 상세 화면의 막대 그래프 카드 데이터로 변환한다.

    평균 풀이시간은 MM:SS 문자열로 바꾸고, 판정 배지는 공용 취약 점수 결과를 사용한다.
    """
    weakness_map = {
        row["groupKeyId"]: row
        for row in weakness_rows or []
    }
    display_items = []
    for stat in stats:
        total = stat["total"] or 0
        rate = stat["rate"] or 0
        average_time_sec = stat.get("averageTimeSec")
        average_time_label = "00:00"
        if average_time_sec is not None:
            total_seconds = max(0, int(round(average_time_sec)))
            minutes, seconds = divmod(total_seconds, 60)
            average_time_label = f"{minutes:02d}:{seconds:02d}"

        weakness_row = weakness_map.get(stat.get("groupKeyId"))
        status_display = build_wrong_rate_status_display(weakness_row, total)

        display_items.append(
            {
                "label": stat["label"] or "미분류",
                "groupKeyId": stat.get("groupKeyId", ""),
                "groupKey": stat.get("groupKey", []),
                "total": total,
                "wrong": stat["wrong"] or 0,
                "rate": rate,
                "average_time_label": average_time_label,
                "status": status_display["status"],
                "status_label": status_display["label"],
                "status_class": status_display["class"],
                "weaknessScore": status_display["weaknessScore"],
                "trend": status_display["trend"],
                "trend_label": status_display["trendLabel"],
            }
        )

    return sorted(
        display_items,
        key=lambda item: (
            -item["rate"],
            -item["wrong"],
            -item["total"],
            item["label"],
        ),
    )


def build_wrong_rate_status_display(weakness_row, total):
    config = get_weakness_config()
    if weakness_row is None:
        if not total:
            return {
                "status": "",
                "label": "기록 없음",
                "class": "empty",
                "weaknessScore": 0.0,
                "trend": config["trend_unknown"],
                "trendLabel": "",
            }
        return {
            "status": config["status_neutral"],
            "label": "",
            "class": "neutral",
            "weaknessScore": 0.0,
            "trend": config["trend_unknown"],
            "trendLabel": "",
        }

    status = weakness_row["status"]
    trend = weakness_row["trend"]
    return {
        "status": status,
        "label": get_weakness_status_label(status, config),
        "class": get_status_class(status),
        "weaknessScore": weakness_row["weaknessScore"],
        "trend": trend,
        "trendLabel": get_weakness_trend_label(status, trend, config),
    }


def get_weakness_status_label(status, config):
    if status == config["status_weak"]:
        return "취약"
    elif status == config["status_stable"]:
        return "안정"
    elif status == config["status_insufficient"]:
        return "판단 보류"
    elif status == config["status_neutral"]:
        return "중립"

    return ""


def get_weakness_trend_label(status, trend, config):
    trend_value = trend
    if isinstance(trend, dict):
        trend_value = trend.get("value")
    if status == config["status_weak"] and trend_value == config["trend_worsening"]:
        return "악화"
    elif trend_value == config["trend_improving"]:
        return "개선 중"

    return ""


def build_wrong_rate_donut_summary(items):
    """
    분류별 오답 수 비중을 도넛 차트 표시 데이터로 변환한다.
    """
    chart_config = get_wrong_rate_donut_config()
    total_wrong = sum(item["wrong"] or 0 for item in items)
    if not total_wrong:
        return {
            "hasRecords": False,
            "totalWrong": 0,
            "gradient": chart_config["empty_gradient"],
            "items": [],
        }

    sorted_items = sorted(
        items,
        key=lambda item: (-(item["wrong"] or 0), -item["rate"], item["label"]),
    )
    segments = build_wrong_rate_donut_segments(
        sorted_items,
        total_wrong,
        chart_config,
    )
    return {
        "hasRecords": True,
        "totalWrong": total_wrong,
        "gradient": build_wrong_rate_donut_gradient(segments),
        "items": segments,
    }


def build_wrong_rate_donut_segments(items, total_wrong, chart_config):
    """
    도넛 차트 범례와 conic-gradient 구간 데이터를 만든다.
    """
    display_limit = chart_config["display_limit"]
    colors = chart_config["colors"]
    primary_items = items[:display_limit]
    remaining_wrong = sum(item["wrong"] or 0 for item in items[display_limit:])
    segments = []
    for index, item in enumerate(primary_items):
        wrong_count = item["wrong"] or 0
        segments.append(
            build_wrong_rate_donut_segment(
                item["label"],
                wrong_count,
                total_wrong,
                colors[index % len(colors)],
                item,
            )
        )

    if remaining_wrong:
        segments.append(
            build_wrong_rate_donut_segment(
                chart_config["remaining_label"],
                remaining_wrong,
                total_wrong,
                colors[len(segments) % len(colors)],
            )
        )

    return segments


def build_wrong_rate_donut_segment(label, wrong_count, total_wrong, color, item=None):
    """
    도넛 차트의 단일 구간을 만든다.
    """
    share_value = round((wrong_count / total_wrong) * 100, 2)
    segment = {
        "label": label,
        "wrong": wrong_count,
        "share": round(share_value),
        "shareValue": share_value,
        "color": color,
    }
    if item:
        segment.update(
            {
                "status_label": item.get("status_label", ""),
                "status_class": item.get("status_class", ""),
                "weaknessScore": item.get("weaknessScore", 0.0),
                "trend_label": item.get("trend_label", ""),
            }
        )

    return segment


def build_wrong_rate_donut_gradient(segments):
    """
    도넛 차트 CSS conic-gradient 문자열을 만든다.
    """
    gradient_parts = []
    start = 0
    for index, segment in enumerate(segments):
        end = start + segment["shareValue"]
        if index == len(segments) - 1:
            end = 100
        angle = round((start + end) * 1.8, 2)
        segment["labelAngle"] = angle
        segment["labelReverseAngle"] = -angle
        gradient_parts.append(f"{segment['color']} {start}% {end}%")
        start = end

    return f"conic-gradient({', '.join(gradient_parts)})"


def get_wrong_rate_donut_config():
    """
    취약점 상세 도넛 차트의 표시 기준을 반환한다.
    """
    return {
        "display_limit": 5,
        "remaining_label": "기타",
        "empty_gradient": "conic-gradient(#dfe8d7 0% 100%)",
        "colors": [
            "#ef8a75",
            "#58c3b6",
            "#74a9ff",
            "#f2b65f",
            "#8fcf7a",
            "#a08ce8",
        ],
    }

from datetime import date, datetime, timedelta

from analytics.service.analytics import get_wrong_rate_weak_threshold
from analytics.service.studyplan import get_study_plan_config


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


def build_planner_summary(study_plans, today):
    """
    저장된 학습계획 목록을 마이페이지 달력 표시용 데이터로 변환한다.

    날짜별 계획 목록, 완료/예정 날짜 키, 오늘 표시 데이터,
    모달에서 사용할 오늘 학습 항목을 함께 구성한다.
    """
    achieved_label = "달성"
    default_title = "학습 계획"
    missed_label = "미달성"
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
                    elif plan_date < today:
                        status_label = missed_label
                    elif plan_date == today:
                        status_label = today_label

                    is_past_plan = plan_date < today
                    is_future_plan = plan_date > today
                    show_start = not is_past_plan
                    can_start = plan_date == today and not is_achieved
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

    plans_by_date = apply_overdue_plan_display(plans_by_date, today)

    completed_keys = []
    planned_keys = []
    for date_key, plan_items in plans_by_date.items():
        plan_date = date.fromisoformat(date_key)
        is_achieved_date = bool(plan_items) and all(item["done"] for item in plan_items)
        if is_achieved_date:
            completed_keys.append(date_key)
        elif plan_date >= today:
            planned_keys.append(date_key)

    today_key = today.isoformat()
    progress_by_date = build_calendar_progress_by_date(plans_by_date, today)
    selected_key = get_default_planner_selected_key(plans_by_date, today)
    selected_date = date.fromisoformat(selected_key)
    has_active_plan = bool(study_plans)
    weekly_review_done = has_completed_weekly_review(plans_by_date)
    has_weekly_review = has_weekly_review_item(plans_by_date)
    last_plan_key = get_last_plan_key(plans_by_date)
    is_empty_active_plan = has_active_plan and not plans_by_date
    is_finished_legacy_plan = (
        has_active_plan
        and not has_weekly_review
        and last_plan_key
        and last_plan_key < today_key
    )
    can_create_plan = (
        not has_active_plan
        or weekly_review_done
        or is_finished_legacy_plan
        or is_empty_active_plan
    )
    create_plan_label = ""
    if not has_active_plan or is_empty_active_plan:
        create_plan_label = "7일 계획 만들기"
    elif weekly_review_done or is_finished_legacy_plan:
        create_plan_label = "다음 7일 계획 만들기"
    create_plan_confirm = ""
    if has_active_plan:
        create_plan_confirm = "기존 학습계획을 보관하고 다음 7일 계획을 만들까요?"
    return {
        "month_label": f"{selected_date.year}년 {selected_date.month:02d}월",
        "day_label": f"{selected_date.month:02d}월 {selected_date.day:02d}일",
        "progress": build_planner_progress_summary(study_plans),
        "today_key": today_key,
        "selected_key": selected_key,
        "next_date_key": (today + timedelta(days=1)).isoformat(),
        "today_items": plans_by_date.get(today_key, []),
        "selected_items": plans_by_date.get(selected_key, []),
        "has_active_plan": has_active_plan,
        "can_create_plan": can_create_plan,
        "can_move_plan": has_active_plan and not can_create_plan,
        "create_plan_label": create_plan_label,
        "create_plan_confirm": create_plan_confirm,
        "data": {
            "plansByDate": plans_by_date,
            "completedKeys": sorted(completed_keys),
            "plannedKeys": sorted(planned_keys),
            "progressByDate": progress_by_date,
        },
    }


def apply_overdue_plan_display(plans_by_date, today):
    today_key = today.isoformat()
    overdue_key = find_earliest_overdue_plan_key(plans_by_date, today_key)
    if not overdue_key:
        return plans_by_date

    overdue_items = []
    remaining_overdue_items = []
    for item in plans_by_date.get(overdue_key, []):
        if is_overdue_plan_item(item):
            overdue_items.append(build_today_overdue_plan_item(item))
        elif not is_overdue_plan_item(item):
            remaining_overdue_items.append(item)

    if not overdue_items:
        return plans_by_date

    updated_plans_by_date = {
        date_key: list(plan_items)
        for date_key, plan_items in plans_by_date.items()
    }
    if remaining_overdue_items:
        updated_plans_by_date[overdue_key] = remaining_overdue_items
    elif not remaining_overdue_items:
        updated_plans_by_date.pop(overdue_key, None)

    today_items = updated_plans_by_date.get(today_key, [])
    preserved_today_items = [
        item
        for item in today_items
        if item.get("isWeeklyReview") or item.get("done")
    ]
    updated_plans_by_date[today_key] = overdue_items + preserved_today_items
    return updated_plans_by_date


def find_earliest_overdue_plan_key(plans_by_date, today_key):
    for date_key in sorted(plans_by_date):
        if date_key >= today_key:
            continue
        if any(is_overdue_plan_item(item) for item in plans_by_date[date_key]):
            return date_key

    return ""


def is_overdue_plan_item(item):
    return not item.get("done") and not item.get("isWeeklyReview")


def build_today_overdue_plan_item(item):
    overdue_item = item.copy()
    overdue_item["showStart"] = True
    overdue_item["canStart"] = True
    overdue_item["canDelete"] = False
    overdue_item["meta"] = f"오늘 · 이월 · {item.get('meta', '')}".strip(" ·")
    return overdue_item


def build_calendar_progress_by_date(plans_by_date, today):
    progress_by_date = {}
    today_key = today.isoformat()
    for date_key, plan_items in plans_by_date.items():
        progress_percent = calculate_date_progress_percent(plan_items)
        state = "future"
        label = "예정"
        if date_key < today_key:
            state = get_past_progress_state(progress_percent)
            label = f"{progress_percent}%"
        elif date_key == today_key:
            state = "today"
            label = "오늘"

        progress_by_date[date_key] = {
            "percent": progress_percent,
            "hue": get_progress_hue(progress_percent),
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


def build_wrong_rate_display(stats):
    """
    오답률 통계를 상세 화면의 막대 그래프 카드 데이터로 변환한다.

    평균 풀이시간은 MM:SS 문자열로 바꾸고, 오답률 기준으로
    취약/안정/데이터 부족 상태 라벨과 CSS 클래스를 부여한다.
    """
    weak_rate_threshold = get_wrong_rate_weak_threshold()
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

        if not total:
            status_label = "데이터 부족"
            status_class = "empty"
        elif rate >= weak_rate_threshold:
            status_label = "취약"
            status_class = "weak"
        elif rate < weak_rate_threshold:
            status_label = "안정"
            status_class = "stable"

        display_items.append(
            {
                "label": stat["label"] or "미분류",
                "total": total,
                "wrong": stat["wrong"] or 0,
                "rate": rate,
                "average_time_label": average_time_label,
                "status_label": status_label,
                "status_class": status_class,
            }
        )

    return sorted(
        display_items,
        key=lambda item: (-item["rate"], -item["total"], item["label"]),
    )


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


def build_wrong_rate_donut_segment(label, wrong_count, total_wrong, color):
    """
    도넛 차트의 단일 구간을 만든다.
    """
    share_value = round((wrong_count / total_wrong) * 100, 2)
    return {
        "label": label,
        "wrong": wrong_count,
        "share": round(share_value),
        "shareValue": share_value,
        "color": color,
    }


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

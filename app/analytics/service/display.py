from datetime import date, datetime, timedelta


def build_planner_summary(study_plans, today):
    """
    저장된 학습계획 목록을 마이페이지 달력 표시용 데이터로 변환한다.

    날짜별 계획 목록, 완료/예정 날짜 키, 오늘 표시 데이터,
    모달에서 사용할 오늘 학습 항목을 함께 구성한다.
    """
    completed_label = "완료"
    default_title = "학습 계획"
    planned_label = "예정"
    today_label = "오늘"
    plans_by_date = {}

    for study_plan in study_plans:
        study_plan_id = study_plan.get("studyPlanId")
        for day_index, day_plan in enumerate(study_plan.get("plans", [])):
            raw_date = day_plan.get("date")
            date_key = ""
            plan_date = None

            if isinstance(raw_date, datetime):
                plan_date = raw_date.date()
                date_key = plan_date.isoformat()
            elif isinstance(raw_date, date):
                plan_date = raw_date
                date_key = plan_date.isoformat()
            elif isinstance(raw_date, str):
                try:
                    plan_date = date.fromisoformat(raw_date[:10])
                    date_key = plan_date.isoformat()
                except ValueError:
                    date_key = ""

            if date_key and plan_date:
                plans_by_date.setdefault(date_key, [])
                for block_index, block in enumerate(day_plan.get("blocks", [])):
                    status_label = planned_label
                    if plan_date < today:
                        status_label = completed_label
                    elif plan_date == today:
                        status_label = today_label

                    label = block.get("label")
                    activity = block.get("activity")
                    title = default_title
                    if label and activity:
                        title = f"{label} {activity}"
                    elif label:
                        title = label
                    elif activity:
                        title = activity

                    meta_parts = [status_label]
                    classification = block.get("classification")
                    question_count = block.get("questionCount")
                    estimated_minutes = block.get("estimatedMinutes")
                    if classification:
                        meta_parts.append(classification)
                    if question_count:
                        meta_parts.append(f"{question_count}문항")
                    if estimated_minutes:
                        meta_parts.append(f"{estimated_minutes}분")

                    is_completed = bool(block.get("isCompleted"))
                    plans_by_date[date_key].append(
                        {
                            "studyPlanId": study_plan_id,
                            "dayIndex": day_index,
                            "blockIndex": block_index,
                            "blockId": block.get("blockId"),
                            "title": title,
                            "meta": " · ".join(meta_parts),
                            "done": is_completed,
                        }
                    )

    completed_keys = []
    planned_keys = []
    for date_key, plan_items in plans_by_date.items():
        plan_date = date.fromisoformat(date_key)
        is_completed_date = bool(plan_items) and all(item["done"] for item in plan_items)
        if is_completed_date:
            completed_keys.append(date_key)
        elif plan_date >= today:
            planned_keys.append(date_key)

    today_key = today.isoformat()
    return {
        "month_label": f"{today.year}년 {today.month:02d}월",
        "day_label": f"{today.month:02d}월 {today.day:02d}일",
        "today_key": today_key,
        "selected_key": today_key,
        "next_date_key": (today + timedelta(days=1)).isoformat(),
        "today_items": plans_by_date.get(today_key, []),
        "data": {
            "plansByDate": plans_by_date,
            "completedKeys": sorted(completed_keys),
            "plannedKeys": sorted(planned_keys),
        },
    }


def build_wrong_rate_display(stats):
    """
    오답률 통계를 상세 화면의 막대 그래프 카드 데이터로 변환한다.

    평균 풀이시간은 MM:SS 문자열로 바꾸고, 오답률 기준으로
    취약/안정/데이터 부족 상태 라벨과 CSS 클래스를 부여한다.
    """
    weak_rate_threshold = 20
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

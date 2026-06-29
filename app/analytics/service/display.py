from datetime import date, datetime, timedelta


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
                    if block.get("blockType") == "review":
                        continue

                    is_achieved = bool(block.get("isAchieved"))
                    status_label = planned_label
                    if is_achieved:
                        status_label = achieved_label
                    elif plan_date < today:
                        status_label = missed_label
                    elif plan_date == today:
                        status_label = today_label

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
                    question_count = block.get("questionCount")
                    estimated_minutes = block.get("estimatedMinutes")
                    achieved_count = block.get("achievedCount") or 0
                    remaining_count = block.get("remainingCount") or 0
                    progress_percent = block.get("progressPercent") or 0
                    progress_mode = block.get("progressMode") or "question"
                    status_label = block.get("statusLabel") or ""
                    classification_label = classification or ""
                    if classification == "복합":
                        classification_label = "복합 취약점"
                    if classification:
                        meta_parts.append(classification_label)
                    if progress_mode == "review" and status_label:
                        meta_parts.append(status_label)
                    elif question_count:
                        meta_parts.append(f"{achieved_count}/{question_count}문항")
                    if remaining_count:
                        meta_parts.append(f"앞으로 {remaining_count}문항")
                    if estimated_minutes:
                        meta_parts.append(f"{estimated_minutes}분")

                    plans_by_date[date_key].append(
                        {
                            "studyPlanId": study_plan_id,
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
                            "statusLabel": status_label,
                            "canConfirm": progress_mode == "review" and not is_achieved,
                        }
                    )

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
    return {
        "month_label": f"{today.year}년 {today.month:02d}월",
        "day_label": f"{today.month:02d}월 {today.day:02d}일",
        "progress": build_planner_progress_summary(study_plans),
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

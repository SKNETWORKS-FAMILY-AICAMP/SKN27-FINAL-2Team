import json
import random
import secrets
import smtplib
from collections import defaultdict
from datetime import date
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import connection, transaction
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from analytics.models import StudyPlanMypage
from analytics.serializers import parse_study_plan_items
from chatbot.models import ChatSessions
from question.models import QuestionOptions, SolveRecords, SolveSessions

from .models import EmailVerificationCode, UserAccounts


def _note_choice_display_data(session, question, options, selected_no):
    display_options = options[:]
    if session.session_type != "diagnostic":
        random.Random(f"{session.session_id}:{question.question_id}").shuffle(display_options)

    choices = []
    answer_no = question.answer_no
    selected_answer = None
    wrong_explanations = []

    for display_no, option in enumerate(display_options, start=1):
        if option.is_answer:
            answer_no = display_no
        if selected_no is not None and option.choice_no == selected_no:
            selected_answer = display_no
        if not option.is_answer and option.choice_explanation:
            wrong_explanations.append(f"{display_no}번. {option.choice_explanation}")
        choices.append({
            "number": display_no,
            "content": option.content,
            "imagePath": option.choice_image_path or "",
            "isAnswer": option.is_answer,
            "explanation": option.choice_explanation or "",
        })

    return choices, answer_no, selected_answer, wrong_explanations


def _wrong_note_session_source(session, records):
    has_study_plan_record = any(
        record.studyplan_id or record.study_plan_block_id
        for record in records
    )
    if session.session_type == "diagnostic":
        return "diagnostic", "진단평가"
    if has_study_plan_record:
        return "study_plan", "학습계획 문제 풀이"
    return "practice", "문제풀이"


def _iter_plan_blocks(items):
    for item in items:
        if not isinstance(item, dict):
            continue
        yield item
        children = item.get("blocks")
        if isinstance(children, list):
            yield from _iter_plan_blocks(children)


def _weekly_review_session_ids(user, sessions):
    records = SolveRecords.objects.filter(
        session__in=sessions,
        studyplan_id__isnull=False,
        study_plan_block_id__isnull=False,
    ).values("session_id", "studyplan_id", "study_plan_block_id")
    refs = {(row["studyplan_id"], row["study_plan_block_id"]): row["session_id"] for row in records}
    if not refs:
        return set()

    weekly_ids = set()
    plans = StudyPlanMypage.objects.filter(user=user, studyplan_id__in={plan_id for plan_id, _ in refs})
    for plan in plans:
        for block in _iter_plan_blocks(parse_study_plan_items(plan.study_plan_items)):
            block_id = block.get("blockId") or block.get("id")
            if block.get("blockType") == "weekly_review" and (plan.studyplan_id, block_id) in refs:
                weekly_ids.add(refs[(plan.studyplan_id, block_id)])
    return weekly_ids


def build_session_display_map(user, sessions):
    weekly_ids = _weekly_review_session_ids(user, sessions)
    counters = defaultdict(int)
    display_map = {}

    for session in sorted(sessions, key=lambda item: (item.recorded_date, item.session_id)):
        solved_date = session.recorded_date
        date_text = solved_date.strftime("%Y.%m.%d") if solved_date else ""

        if session.session_id in weekly_ids:
            title = "주간평가"
        elif session.session_type == "diagnostic":
            counters[(solved_date, "diagnostic")] += 1
            title = f"진단평가 {counters[(solved_date, 'diagnostic')]}회차"
        else:
            counters[(solved_date, "practice")] += 1
            title = f"일반평가 {counters[(solved_date, 'practice')]}회차"

        display_map[session.session_id] = {
            "date": date_text,
            "title": title,
            "full": f"{date_text} {title}".strip(),
        }

    return display_map


def _safe_next_url(request, raw_next: str) -> str:
    """오픈 리다이렉트 방지. 같은 호스트의 상대 경로만 허용한다."""
    default_url = "/analytics/mypage/"
    if raw_next and url_has_allowed_host_and_scheme(
        raw_next,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return raw_next

    return default_url


def login_page(request):
    """소셜 로그인 전용 화면. next 는 세션에 담아 콜백 후 복귀에 쓴다."""
    request.session["oauth_next"] = _safe_next_url(request, request.GET.get("next") or "")
    return render(request, "user/login.html")


@require_POST
def logout_page(request):
    storage_scope = str(request.user.user_id) if request.user.is_authenticated else ""
    logout(request)
    messages.success(request, "로그아웃되었습니다.")
    return render(
        request,
        "user/logout.html",
        {
            "redirect_url": reverse("pages:index"),
            "storage_scope": storage_scope,
        },
    )

@login_required
def profile_edit(request):
    profile = request.user

    if request.method == "POST":
        nickname = (request.POST.get("nickname") or "").strip()
        daily_available_hours = request.POST.get("daily_available_hours") or ""
        exam_date = request.POST.get("exam_date") or ""

        if not nickname:
            messages.error(request, "닉네임을 입력해 주세요.")
        elif len(nickname) > 30:
            messages.error(request, "닉네임은 30자 이내로 입력해 주세요.")
        else:
            try:
                hours = float(daily_available_hours)
            except ValueError:
                hours = 0

            if hours < 0.5 or hours > 12:
                messages.error(request, "학습 가용시간은 0.5시간부터 12시간까지 입력할 수 있습니다.")
            else:
                parsed_exam_date = None
                if exam_date:
                    try:
                        parsed_exam_date = date.fromisoformat(exam_date)
                    except ValueError:
                        messages.error(request, "시험일 형식이 올바르지 않습니다.")
                        return render(request, "user/profile_edit.html", {"profile": profile})

                request.user.nickname = nickname
                request.user.daily_available_hours = hours
                request.user.exam_date = parsed_exam_date
                request.user.updated_at = timezone.now()
                request.user.save(
                    update_fields=[
                        "nickname",
                        "daily_available_hours",
                        "exam_date",
                        "updated_at",
                    ]
                )
                messages.success(request, "학습 정보가 저장되었습니다.")
                return redirect("/analytics/mypage/")

    return render(request, "user/profile_edit.html", {"profile": profile})


@login_required
def wrong_note(request):
    sessions = list(
        SolveSessions.objects.filter(user=request.user, status="completed")
        .order_by("-session_id")
    )
    session_ids = [session.session_id for session in sessions]
    session_map = {session.session_id: session for session in sessions}

    records = list(
        SolveRecords.objects.filter(
            session_id__in=session_ids,
        )
        .select_related("question", "session")
        .order_by("-session_id", "record_id")
    )
    question_ids = [record.question_id for record in records]
    option_map = {}
    for option in QuestionOptions.objects.filter(question_id__in=question_ids).order_by(
        "question_id",
        "choice_no",
    ):
        option_map.setdefault(option.question_id, []).append(option)

    session_summaries = []
    records_by_session = {}
    for record in records:
        records_by_session.setdefault(record.session_id, []).append(record)

    for session in sessions:
        session_records = records_by_session.get(session.session_id, [])
        session_source, session_type_label = _wrong_note_session_source(session, session_records)
        answered_records = [record for record in session_records if record.selected_no is not None]
        correct_count = sum(1 for record in answered_records if record.is_correct)
        wrong_count = sum(1 for record in answered_records if not record.is_correct)
        saved_count = sum(1 for record in session_records if record.is_saved)
        total_time_ms = sum(record.time_spent_ms or 0 for record in session_records)
        session_summaries.append({
            "sessionId": session.session_id,
            "sessionType": session_source,
            "rawSessionType": session.session_type,
            "sessionTypeLabel": session_type_label,
            "label": f"Session #{session.session_id}",
            "status": session.status,
            "recordedDate": session.recorded_date.isoformat() if session.recorded_date else "",
            "totalCount": session.total_count,
            "answeredCount": len(answered_records),
            "correctCount": correct_count,
            "wrongCount": wrong_count,
            "savedCount": saved_count,
            "timeSpent": _format_ms_duration(total_time_ms),
        })

    session_number_map = {}
    for session_id, session_records in records_by_session.items():
        for number, record in enumerate(sorted(session_records, key=lambda item: item.record_id), start=1):
            session_number_map[record.record_id] = number

    note_records = []
    for record in records:
        question = record.question
        session = session_map[record.session_id]
        session_source, session_type_label = _wrong_note_session_source(session, [record])
        options = option_map.get(question.question_id, [])
        choices, answer_no, user_answer, wrong_explanations = _note_choice_display_data(
            session,
            question,
            options,
            record.selected_no,
        )
        selected_choice = next(
            (choice for choice in choices if choice["number"] == user_answer),
            None,
        )
        note_records.append({
            "id": record.record_id,
            "sessionId": record.session_id,
            "sessionType": session_source,
            "rawSessionType": session.session_type,
            "sessionTypeLabel": session_type_label,
            "sessionLabel": session.recorded_date.isoformat()
            if session.recorded_date
            else f"Session #{record.session_id}",
            "number": session_number_map.get(record.record_id, 0),
            "questionId": question.question_id,
            "era": record.era,
            "topic": record.topic,
            "type": record.q_type,
            "subtype": question.question_subtype,
            "score": record.q_score,
            "isCorrect": record.is_correct,
            "isSaved": record.is_saved,
            "savedAt": record.saved_at.isoformat() if record.saved_at else "",
            "title": question.content,
            "source": question.passage or question.image_caption or "",
            "questionImagePath": question.question_image_path or "",
            "choices": choices,
            "answer": answer_no,
            "userAnswer": user_answer,
            "selectedExplanation": selected_choice["explanation"] if selected_choice else "",
            "solution": question.answer_explanation,
            "wrongs": wrong_explanations,
            "concept": question.core_concept,
            "timeSpent": _format_ms_duration(record.time_spent_ms),
        })

    note_payload = {
        "sessions": session_summaries,
        "records": note_records,
    }
    return render(request, "user/wrong_note.html", {"note_payload": note_payload})




@login_required
def solved_problems(request):
    sessions = list(
        SolveSessions.objects.filter(user=request.user)
        .order_by("-session_id")
    )
    selected_session = None
    selected_session_id = request.GET.get("session_id")
    if selected_session_id:
        selected_session = next(
            (session for session in sessions if str(session.session_id) == selected_session_id),
            None,
        )

    records = []
    option_map = {}
    if selected_session:
        records = list(
            SolveRecords.objects.filter(session=selected_session)
            .select_related("question")
            .order_by("record_id")
        )
        question_ids = [record.question_id for record in records]
        options = QuestionOptions.objects.filter(question_id__in=question_ids).order_by(
            "question_id",
            "choice_no",
        )
        for option in options:
            option_map.setdefault(option.question_id, []).append(option)

    session_cards = []
    session_display_map = build_session_display_map(request.user, sessions)
    for session in sessions:
        answer_rate = session.answer_rate or 0
        answer_rate_percent = round(answer_rate * 100 if answer_rate <= 1 else answer_rate)
        display = session_display_map.get(session.session_id, {})
        session_cards.append(
            {
                "session": session,
                "answer_rate": max(0, min(100, answer_rate_percent)),
                "elapsed_time": _format_duration(session.elapsed_sec),
                "display_date": display.get("date", ""),
                "display_title": display.get("title", "풀이 기록"),
                "status_label": "완료" if session.status == "completed" else "미완료",
                "is_active": selected_session and session.session_id == selected_session.session_id,
            }
        )

    record_cards = []
    record_payload = []
    for index, record in enumerate(records, start=1):
        question = record.question
        options = option_map.get(question.question_id, [])
        record_cards.append(
            {
                "record": record,
                "question": question,
                "number": index,
                "options": options,
                "time_spent": _format_ms_duration(record.time_spent_ms),
                "selected_label": f"{record.selected_no}번" if record.selected_no else "미응답",
                "answer_label": f"{question.answer_no}번",
            }
        )
        record_payload.append(
            {
                "number": index,
                "question_id": question.question_id,
                "content": question.content,
                "passage": question.passage or "",
                "question_type": question.question_type,
                "answer_no": question.answer_no,
                "answer_explanation": question.answer_explanation,
                "core_concept": question.core_concept,
                "question_image_path": question.question_image_path or "",
                "selected_no": record.selected_no,
                "is_correct": record.is_correct,
                "time_spent": _format_ms_duration(record.time_spent_ms),
                "era": record.era,
                "topic": record.topic,
                "q_score": record.q_score,
                "options": [
                    {
                        "choice_no": option.choice_no,
                        "content": option.content,
                        "is_answer": option.is_answer,
                        "choice_explanation": option.choice_explanation,
                        "choice_image_path": option.choice_image_path or "",
                    }
                    for option in options
                ],
            }
        )

    return render(
        request,
        "user/solved_problems.html",
        {
            "session_cards": session_cards,
            "selected_session": selected_session,
            "selected_session_status_label": "완료" if selected_session and selected_session.status == "completed" else "미완료",
            "selected_session_display": session_display_map.get(selected_session.session_id) if selected_session else None,
            "record_cards": record_cards,
            "record_payload": record_payload,
        },
    )


def _format_duration(seconds):
    if seconds is None:
        return "00:00"
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _format_ms_duration(milliseconds):
    if milliseconds is None:
        return "00:00"
    seconds = max(0, int(round(milliseconds / 1000)))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"



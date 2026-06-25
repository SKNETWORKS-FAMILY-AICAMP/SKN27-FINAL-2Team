import json
import random
import smtplib
from datetime import date
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.db import connection
from django.db.models import Avg, Count, Min, Q, Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from analytics.models import Analytics
from chatbot.models import ChatSessions
from question.models import QuestionOptions, SolveRecords, SolveSessions

from .models import EmailVerificationCode, UserAccounts, UserStudyProfile


def login_page(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""
        remember = request.POST.get("remember")
        next_url = request.GET.get("next") or "/analytics/mypage/"

        user = authenticate(request, email=email, password=password)
        if user is None:
            messages.error(request, "이메일 또는 비밀번호를 확인해 주세요. 5회 실패 시 30분간 잠깁니다.")
        else:
            login(request, user)
            if not remember:
                request.session.set_expiry(0)
            messages.success(request, "로그인되었습니다.")
            return redirect(next_url)

    return render(request, "user/login.html")


def register_page(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        nickname = (request.POST.get("nickname") or "").strip()
        password = request.POST.get("password") or ""
        password_confirm = request.POST.get("password_confirm") or ""
        code = (request.POST.get("verification_code") or "").strip()

        if not email or not nickname or not password or not password_confirm:
            messages.error(request, "이메일, 닉네임, 비밀번호를 모두 입력해 주세요.")
        elif len(nickname) > 30:
            messages.error(request, "닉네임은 30자 이내로 입력해 주세요.")
        elif UserAccounts.objects.filter(nickname=nickname, deleted_at__isnull=True).exists():
            messages.error(request, "이미 사용 중인 닉네임입니다.")
        elif password != password_confirm:
            messages.error(request, "비밀번호 확인이 일치하지 않습니다.")
        elif UserAccounts.objects.filter(email=email, deleted_at__isnull=True).exists():
            messages.error(request, "이미 가입된 이메일입니다.")
        elif not _is_valid_verification_code(email, code):
            messages.error(request, "이메일 인증번호가 올바르지 않거나 만료되었습니다.")
        else:
            now = timezone.now()
            user = UserAccounts.objects.create(
                email=email,
                password_hash=make_password(password),
                nickname=nickname,
                login_fail_count=0,
                is_locked=False,
                locked_at=None,
                status="active",
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            _consume_verification_code(email, code)
            login(request, user, backend="user.backends.UserAccountsBackend")
            messages.success(request, "회원가입과 이메일 인증이 완료되었습니다.")
            return redirect("/analytics/mypage/")

    return render(request, "user/register.html")


@require_POST
def send_verification_code(request):
    email = _extract_email(request)
    if not email:
        return JsonResponse({"ok": False, "message": "이메일을 입력해 주세요."}, status=400)

    if UserAccounts.objects.filter(email=email, deleted_at__isnull=True).exists():
        return JsonResponse({"ok": False, "message": "이미 가입된 이메일입니다."}, status=400)

    now = timezone.now()
    code = f"{random.randint(0, 999999):06d}"
    EmailVerificationCode.objects.filter(
        email=email,
        purpose="register",
        is_used=False,
    ).update(is_used=True, used_at=now)
    verification = EmailVerificationCode.objects.create(
        email=email,
        code=code,
        purpose="register",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    try:
        send_mail(
            subject="[himate] 이메일 인증번호",
            message=f"himate 회원가입 인증번호는 {code} 입니다. 5분 안에 입력해 주세요.",
            from_email=None,
            recipient_list=[email],
            fail_silently=False,
        )
    except smtplib.SMTPAuthenticationError:
        verification.mark_used()
        return JsonResponse(
            {
                "ok": False,
                "message": "메일 계정 인증에 실패했습니다. Gmail 앱 비밀번호를 확인해 주세요.",
            },
            status=502,
        )
    except smtplib.SMTPException:
        verification.mark_used()
        return JsonResponse(
            {
                "ok": False,
                "message": "메일 서버 연결에 실패했습니다. SMTP 설정을 확인해 주세요.",
            },
            status=502,
        )

    return JsonResponse({"ok": True, "message": "인증번호를 발송했습니다. 5분 안에 입력해 주세요."})


@require_POST
def check_nickname(request):
    nickname = _extract_nickname(request)
    if not nickname:
        return JsonResponse({"ok": False, "message": "닉네임을 입력해 주세요."}, status=400)
    if len(nickname) > 30:
        return JsonResponse({"ok": False, "message": "닉네임은 30자 이내로 입력해 주세요."}, status=400)
    if UserAccounts.objects.filter(nickname=nickname, deleted_at__isnull=True).exists():
        return JsonResponse({"ok": False, "message": "이미 사용 중인 닉네임입니다."}, status=409)
    return JsonResponse({"ok": True, "message": "사용 가능한 닉네임입니다."})


@require_POST
def verify_verification_code(request):
    email = _extract_email(request)
    code = _extract_code(request)
    if _is_valid_verification_code(email, code):
        return JsonResponse({"ok": True, "message": "이메일 인증이 확인되었습니다."})
    return JsonResponse(
        {"ok": False, "message": "인증번호가 올바르지 않거나 만료되었습니다."},
        status=400,
    )


def logout_page(request):
    logout(request)
    messages.success(request, "로그아웃되었습니다.")
    return redirect("/")

@login_required
def profile_edit(request):
    profile = _get_or_create_study_profile(request.user)

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
                request.user.updated_at = timezone.now()
                request.user.save(update_fields=["nickname", "updated_at"])

                _update_study_profile(request.user, hours, parsed_exam_date)
                messages.success(request, "학습 정보가 저장되었습니다.")
                return redirect("/analytics/mypage/")

    return render(request, "user/profile_edit.html", {"profile": profile})


@login_required
def wrong_note(request):
    sessions = list(
        SolveSessions.objects.filter(user=request.user)
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
        answered_records = [record for record in session_records if record.selected_no is not None]
        correct_count = sum(1 for record in answered_records if record.is_correct)
        wrong_count = sum(1 for record in answered_records if not record.is_correct)
        saved_count = sum(1 for record in session_records if record.is_saved)
        total_time_ms = sum(record.time_spent_ms or 0 for record in session_records)
        session_summaries.append({
            "sessionId": session.session_id,
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
        options = option_map.get(question.question_id, [])
        selected_option = next(
            (option for option in options if option.choice_no == record.selected_no),
            None,
        )
        wrong_explanations = [
            option.choice_explanation
            for option in options
            if option.choice_no != question.answer_no and option.choice_explanation
        ]
        note_records.append({
            "id": record.record_id,
            "sessionId": record.session_id,
            "sessionLabel": session_map[record.session_id].recorded_date.isoformat()
            if session_map[record.session_id].recorded_date
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
            "choices": [
                {
                    "number": option.choice_no,
                    "content": option.content,
                    "isAnswer": option.is_answer,
                    "explanation": option.choice_explanation or "",
                }
                for option in options
            ],
            "answer": question.answer_no,
            "userAnswer": record.selected_no,
            "selectedExplanation": selected_option.choice_explanation if selected_option else "",
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
    analytics_dates = {
        row["session_id"]: row["first_date"]
        for row in Analytics.objects.filter(session__in=sessions)
        .values("session_id")
        .annotate(first_date=Min("date"))
    }
    for session in sessions:
        answer_rate = session.answer_rate or 0
        answer_rate_percent = round(answer_rate * 100 if answer_rate <= 1 else answer_rate)
        solved_date = analytics_dates.get(session.session_id)
        session_cards.append(
            {
                "session": session,
                "answer_rate": max(0, min(100, answer_rate_percent)),
                "elapsed_time": _format_duration(session.elapsed_sec),
                "date_label": f"{solved_date.month}월 {solved_date.day}일 푼 문제" if solved_date else f"Session #{session.session_id}",
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
                "question_type": question.question_type,
                "answer_no": question.answer_no,
                "answer_explanation": question.answer_explanation,
                "core_concept": question.core_concept,
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
            "record_cards": record_cards,
            "record_payload": record_payload,
        },
    )


def _get_or_create_study_profile(user):
    now = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO user_study_profiles (
                user_id,
                daily_available_hours,
                exam_date,
                created_at,
                updated_at
            )
            VALUES (%s, %s, NULL, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            [user.user_id, 1.0, now, now],
        )
    return UserStudyProfile.objects.get(user=user)


def _update_study_profile(user, daily_available_hours, exam_date):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE user_study_profiles
            SET daily_available_hours = %s,
                exam_date = %s,
                updated_at = %s
            WHERE user_id = %s
            """,
            [daily_available_hours, exam_date, timezone.now(), user.user_id],
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


def _extract_email(request):
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return ""
        return (payload.get("email") or "").strip().lower()
    return (request.POST.get("email") or "").strip().lower()


def _extract_code(request):
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return ""
        return (payload.get("code") or "").strip()
    return (request.POST.get("verification_code") or "").strip()


def _extract_nickname(request):
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return ""
        return (payload.get("nickname") or "").strip()
    return (request.POST.get("nickname") or "").strip()


def _is_valid_verification_code(email, code):
    if not email or not code:
        return False
    return EmailVerificationCode.objects.filter(
        email=email,
        code=code,
        purpose="register",
        is_used=False,
        expires_at__gt=timezone.now(),
    ).exists()


def _consume_verification_code(email, code):
    verification = EmailVerificationCode.objects.filter(
        email=email,
        code=code,
        purpose="register",
        is_used=False,
        expires_at__gt=timezone.now(),
    ).order_by("-created_at").first()
    if verification:
        verification.mark_used()

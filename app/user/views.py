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

from chatbot.models import ChatSessions
from question.models import Analytics, QuestionOptions, SolveRecords, SolveSessions

from .models import EmailVerificationCode, UserAccounts, UserStudyProfile


def login_page(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""
        remember = request.POST.get("remember")
        next_url = request.GET.get("next") or "/user/mypage/"

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
            return redirect("/user/mypage/")

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
def mypage(request):
    profile = _get_or_create_study_profile(request.user)
    d_day = None
    if profile.exam_date:
        d_day = (profile.exam_date - timezone.localdate()).days

    solve_stats = _build_solve_stats(request.user)
    chat_stats = _build_chat_stats(request.user)
    type_wrong_stats = _build_mypage_type_wrong_stats(request.user)

    return render(
        request,
        "user/mypage.html",
        {
            "profile": profile,
            "d_day": d_day,
            "solve_stats": solve_stats,
            "chat_stats": chat_stats,
            "type_wrong_stats": type_wrong_stats,
        },
    )


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
                return redirect("/user/mypage/")

    return render(request, "user/profile_edit.html", {"profile": profile})


@login_required
def wrong_note(request):
    return render(request, "user/wrong_note.html")


@login_required
def wrong_rate_detail(request):
    era_stats = _build_wrong_rate_group(
        request.user,
        "era",
        ["선사", "삼국", "고려", "조선", "근대", "현대"],
    )
    type_stats = _build_wrong_rate_group(
        request.user,
        "q_type",
        ["연표", "사료", "개념", "인물", "지역"],
    )
    topic_stats = _build_wrong_rate_group(
        request.user,
        "topic",
        ["정치", "경제", "사회", "문화", "외교"],
    )

    return render(
        request,
        "user/wrong_rate_detail.html",
        {
            "era_stats": era_stats,
            "type_stats": type_stats,
            "topic_stats": topic_stats,
        },
    )


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
                "time_spent": _format_duration(record.time_spent_sec),
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
                "time_spent": _format_duration(record.time_spent_sec),
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


def _build_solve_stats(user):
    completed_sessions = SolveSessions.objects.filter(
        user=user,
        status="completed",
    )
    records = SolveRecords.objects.filter(session__user=user)
    record_stats = records.aggregate(
        avg_question_time=Avg("time_spent_sec"),
        solved_count=Count("record_id"),
    )
    session_stats = completed_sessions.aggregate(
        avg_session_time=Avg("elapsed_sec"),
        total_session_time=Sum("elapsed_sec"),
        session_count=Count("session_id"),
        avg_answer_rate=Avg("answer_rate"),
    )
    avg_answer_rate = session_stats["avg_answer_rate"] or 0
    answer_rate_percent = round(avg_answer_rate * 100 if avg_answer_rate <= 1 else avg_answer_rate)

    return {
        "avg_question_time": _format_duration(record_stats["avg_question_time"]),
        "avg_session_time": _format_duration(session_stats["avg_session_time"]),
        "total_session_time": _format_duration(session_stats["total_session_time"]),
        "solved_count": record_stats["solved_count"] or 0,
        "session_count": session_stats["session_count"] or 0,
        "answer_rate": max(0, min(100, answer_rate_percent)),
    }


def _build_chat_stats(user):
    sessions = ChatSessions.objects.filter(user=user)
    total_count = sessions.count()
    type_counts = list(
        sessions.values("chat_type")
        .annotate(count=Count("session_id"))
        .order_by("-count", "chat_type")
    )
    top_type = type_counts[0]["chat_type"] if type_counts else "없음"

    return {
        "total_count": total_count,
        "type_counts": type_counts[:2],
        "top_type": top_type,
    }


def _build_mypage_type_wrong_stats(user):
    preferred_order = ["연표", "사료", "개념", "인물", "지역"]
    rows = (
        SolveRecords.objects.filter(session__user=user)
        .values("q_type")
        .annotate(
            total=Count("record_id"),
            wrong=Count("record_id", filter=Q(is_correct=False)),
        )
    )
    row_map = {
        (row["q_type"] or "미분류"): {
            "total": row["total"] or 0,
            "wrong": row["wrong"] or 0,
        }
        for row in rows
    }

    labels = list(preferred_order)
    for label in row_map:
        if label not in labels:
            labels.append(label)

    items = []
    total_count = 0
    wrong_count = 0
    for label in labels:
        item = row_map.get(label, {"total": 0, "wrong": 0})
        total = item["total"]
        wrong = item["wrong"]
        total_count += total
        wrong_count += wrong
        rate = round((wrong / total) * 100) if total else 0
        items.append(
            {
                "label": label,
                "total": total,
                "wrong": wrong,
                "rate": max(0, min(100, rate)),
            }
        )

    ranked_items = sorted(items, key=lambda item: (-item["rate"], -item["total"], item["label"]))
    visible_items = [item for item in ranked_items if item["total"]][:3]
    if not visible_items:
        visible_items = items[:3]

    overall_rate = round((wrong_count / total_count) * 100) if total_count else 0
    return {
        "overall_rate": max(0, min(100, overall_rate)),
        "items": visible_items,
        "has_records": total_count > 0,
    }


def _build_wrong_rate_group(user, field_name, preferred_order):
    rows = (
        SolveRecords.objects.filter(session__user=user)
        .values(field_name)
        .annotate(
            total=Count("record_id"),
            wrong=Count("record_id", filter=Q(is_correct=False)),
        )
    )
    row_map = {
        (row[field_name] or "미분류"): {
            "total": row["total"] or 0,
            "wrong": row["wrong"] or 0,
        }
        for row in rows
    }

    labels = list(preferred_order)
    for label in row_map:
        if label not in labels:
            labels.append(label)

    stats = []
    for label in labels:
        item = row_map.get(label, {"total": 0, "wrong": 0})
        total = item["total"]
        wrong = item["wrong"]
        rate = round((wrong / total) * 100) if total else 0
        if not total:
            status_label = "데이터 부족"
            status_class = "empty"
        elif rate >= 20:
            status_label = "취약"
            status_class = "weak"
        else:
            status_label = "안정"
            status_class = "stable"
        stats.append(
            {
                "label": label,
                "total": total,
                "wrong": wrong,
                "rate": max(0, min(100, rate)),
                "status_label": status_label,
                "status_class": status_class,
            }
        )
    return stats


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

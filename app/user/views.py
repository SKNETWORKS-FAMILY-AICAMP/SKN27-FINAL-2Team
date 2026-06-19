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
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

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

    code = f"{random.randint(0, 999999):06d}"
    EmailVerificationCode.objects.filter(
        email=email,
        purpose="register",
        is_used=False,
    ).update(is_used=True, used_at=timezone.now())
    verification = EmailVerificationCode.objects.create(
        email=email,
        code=code,
        purpose="register",
        expires_at=timezone.now() + timedelta(minutes=5),
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
    profile, _ = UserStudyProfile.objects.get_or_create(user=request.user)
    d_day = None
    if profile.exam_date:
        d_day = (profile.exam_date - timezone.localdate()).days
    return render(request, "user/mypage.html", {"profile": profile, "d_day": d_day})


@login_required
def profile_edit(request):
    profile, _ = UserStudyProfile.objects.get_or_create(user=request.user)

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

                profile.daily_available_hours = hours
                profile.exam_date = parsed_exam_date
                profile.save(update_fields=["daily_available_hours", "exam_date", "updated_at"])
                messages.success(request, "학습 정보가 저장되었습니다.")
                return redirect("/user/mypage/")

    return render(request, "user/profile_edit.html", {"profile": profile})


@login_required
def wrong_note(request):
    return render(request, "user/wrong_note.html")


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

import random
from datetime import date
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.db import models, transaction
from django.db import connection
from django.shortcuts import render
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from analytics.models import Analytics
from analytics.models import StudyPlanMypage
from analytics.serializers import parse_study_plan_items
from analytics.service.analysis_snapshot import create_session_snapshot
from analytics.service.study_plan import (
    StudyPlanBlockNotDue,
    StudyPlanBlockTerminal,
    validate_study_plan_block_start,
)
from analytics.service.study_plan.config import get_study_plan_config
from analytics.service.studyplan import (
    complete_study_plan_block_by_id,
    is_weekly_review_plan_block,
)
from analytics.service.weekly_report.collector import enqueue_weekly_report
from question.models import (
    QuestionOptions,
    Questions,
    SolveRecords,
    SolveSessions,
)
from .serializers import (
    DiagnosisExplanationResponseSerializer,
    DiagnosisResultResponseSerializer,
    DiagnosisStartRequestSerializer,
    DiagnosisStartResponseSerializer,
    DiagnosisSubmitRequestSerializer,
    DiagnosisSubmitResponseSerializer,
)

# ── 상수 ───────────────────────────────────────────────────────────────────────
DIAGNOSIS_QUESTION_COUNT = 50
DIAGNOSIS_TARGET_SCORE = 100
DIAGNOSIS_TIME_LIMIT_SEC = 4800  # 80분
DIAGNOSIS_ERA_GROUPS = [
    {"eras": ("선사 시대", "고조선", "초기 국가"), "count": 2},
    {"eras": ("삼국 시대", "남북국 시대"), "count": 7},
    {"eras": ("고려",), "count": 8},
    {"eras": ("조선",), "count": 12},
    {"eras": ("개항기",), "count": 6},
    {"eras": ("일제 강점기",), "count": 8},
    {"eras": ("현대",), "count": 7},
]
DIAGNOSIS_SCORE_COUNTS = {1: 10, 2: 30, 3: 10}

# 예상 수준 기준 (진단평가 총점 100점 기준 취득 점수)
GRADE_THRESHOLDS = [
    (80, "1급"),
    (70, "2급"),
    (60, "3급"),
]


def _get_local_recorded_date() -> date:
    """학습 기준 시간대의 날짜를 반환한다."""
    study_plan_timezone = ZoneInfo(get_study_plan_config().timezone)
    return timezone.localdate(timezone=study_plan_timezone)


def _get_expected_grade(total_score: int) -> str:
    """total_score: 진단평가 취득 점수"""
    for threshold, grade in GRADE_THRESHOLDS:
        if total_score >= threshold:
            return grade
    return "탈락"


def _find_weekly_review_block(user_id, studyplan_id, block_id):
    if not studyplan_id and not block_id:
        return None, None
    if not studyplan_id or not block_id:
        return None, {"error": "studyplan_id와 study_plan_block_id가 모두 필요합니다."}

    study_plan = StudyPlanMypage.objects.filter(
        user_id=user_id,
        studyplan_id=studyplan_id,
        status="active",
    ).first()
    if study_plan is None:
        return None, {"error": "활성 학습계획을 찾을 수 없습니다."}

    for day_plan in parse_study_plan_items(study_plan.study_plan_items):
        for block in day_plan.get("blocks", []):
            if str(block.get("blockId")) == str(block_id):
                if not is_weekly_review_plan_block(block):
                    return None, {"error": "주간평가 블록이 아닙니다."}
                return block, None

    return None, {"error": "학습계획 블록을 찾을 수 없습니다."}


def _session_study_plan_ref(session):
    return (
        SolveRecords.objects.filter(
            session=session,
            studyplan_id__isnull=False,
            study_plan_block_id__isnull=False,
        )
        .exclude(study_plan_block_id="")
        .values("studyplan_id", "study_plan_block_id")
        .first()
    )


def _complete_weekly_review_block_for_session(session, user_id):
    if session.session_type != "diagnostic" or session.status != "completed":
        return None

    ref = _session_study_plan_ref(session)
    if not ref:
        return None

    block, error = _find_weekly_review_block(
        user_id,
        ref["studyplan_id"],
        ref["study_plan_block_id"],
    )
    if error or block is None:
        return None

    completed_plan = complete_study_plan_block_by_id(
        user_id,
        ref["studyplan_id"],
        ref["study_plan_block_id"],
        True,
    )
    if completed_plan is not None:
        enqueue_weekly_report(user_id, session.session_id, ref["studyplan_id"])
    return completed_plan


def _has_solve_sessions_review_type():
    return any(
        column.name == "review_type"
        for column in connection.introspection.get_table_description(
            connection.cursor(),
            "solve_sessions",
        )
    )


# ── 페이지 뷰 (템플릿) ──────────────────────────────────────────────────────────

@login_required
def diagnosis_intro(request):
    return render(request, "diagnosis/intro.html")


@login_required
def diagnosis_exam(request):
    return render(request, "question/question_exam.html", {"exam_mode": "diagnostic"})


@login_required
def diagnosis_result(request):
    return render(request, "study/result.html", {"result_mode": "diagnostic"})


# ── API: 안내 정보 ──────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([AllowAny])
def diagnosis_info(request):
    """
    GET /api/diagnosis/info/
    진단평가 시작 전 안내 정보 (하드코딩)
    """
    data = {
        "total_count": DIAGNOSIS_QUESTION_COUNT,
        "time_limit_sec": DIAGNOSIS_TIME_LIMIT_SEC,
        "description": "한국사능력검정시험 진단평가입니다.",
        "notice": [
            "총 {}문항으로 구성되어 있습니다.".format(DIAGNOSIS_QUESTION_COUNT),
            "제한 시간은 {}분입니다.".format(DIAGNOSIS_TIME_LIMIT_SEC // 60),
            "모든 문항을 풀어야 진단 결과를 확인할 수 있습니다.",
            "문제와 선택지 순서는 매 시험마다 랜덤으로 변경됩니다.",
        ],
    }
    return Response(data, status=status.HTTP_200_OK)


# ── API: 진단 시작 ──────────────────────────────────────────────────────────────

@api_view(["POST"])
def diagnosis_start(request):
    """
    POST /api/diagnosis/start/
    세션 생성 + 문제 DIAGNOSIS_QUESTION_COUNT개 랜덤 선택 + 선택지 셔플 반환
    """
    if not request.user.is_authenticated:
        return Response(
            {"error": "로그인이 필요한 기능입니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    user_id = request.user.user_id
    req_serializer = DiagnosisStartRequestSerializer(data=request.data)
    if not req_serializer.is_valid():
        return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    start_data = req_serializer.validated_data
    studyplan_id = start_data.get("studyplan_id")
    study_plan_block_id = start_data.get("study_plan_block_id")
    weekly_review_block, block_error = _find_weekly_review_block(
        user_id,
        studyplan_id,
        study_plan_block_id,
    )
    if block_error:
        return Response(block_error, status=status.HTTP_400_BAD_REQUEST)
    if weekly_review_block:
        try:
            weekly_review_block = validate_study_plan_block_start(
                user_id,
                studyplan_id,
                study_plan_block_id,
                "diagnosis",
            )
        except StudyPlanBlockTerminal:
            return Response(
                {
                    "code": "BLOCK_TERMINAL",
                    "error": "종료된 블록은 새로 시작할 수 없습니다.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        except StudyPlanBlockNotDue:
            return Response(
                {
                    "code": "BLOCK_NOT_DUE",
                    "error": "오늘 예정된 블록만 시작할 수 있습니다.",
                },
                status=status.HTTP_409_CONFLICT,
            )
    review_type = "weekly_review" if weekly_review_block else None
    has_review_type = _has_solve_sessions_review_type()

    # 1) 전체 문제에서 50문항 / 총점 100점 조합을 랜덤 선택
    selected_ids = _pick_diagnosis_question_ids()
    if not selected_ids:
        return Response(
            {"error": "50문항 총점 100점 조합을 만들 수 없습니다."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    questions_qs = Questions.objects.filter(question_id__in=selected_ids)
    # 새 진단평가를 시작하면 이전 진행 중 세션을 정리해 저장 진단지는 1개만 유지한다.
    in_progress_sessions = SolveSessions.objects.filter(
        user_id=user_id,
        session_type="diagnostic",
        status="in_progress",
    )
    if has_review_type:
        in_progress_sessions = in_progress_sessions.filter(review_type=review_type)
    in_progress_sessions.delete()

    # 2) 세션 생성
    session_data = {
        "user_id": user_id,
        "session_type": "diagnostic",
        "total_count": DIAGNOSIS_QUESTION_COUNT,
        "status": "in_progress",
        "recorded_date": _get_local_recorded_date(),
    }
    if has_review_type:
        session_data["review_type"] = review_type
    session = SolveSessions.objects.create(
        **session_data,
    )

    # 3) 문제 목록 구성 (문제 순서 랜덤 + 선택지 셔플)
    questions_list = list(questions_qs)
    random.shuffle(questions_list)
    # 진단평가도 중간 저장/이어풀기를 지원하기 위해 시작 시 빈 풀이 기록을 만든다.
    SolveRecords.objects.bulk_create([
        SolveRecords(
            session=session,
            question=question,
            selected_no=None,
            is_correct=False,
            time_spent_ms=None,
            q_type=question.question_type,
            topic=question.topic,
            era=question.era,
            q_score=question.q_score,
            studyplan_id=studyplan_id if weekly_review_block else None,
            study_plan_block_id=study_plan_block_id if weekly_review_block else None,
        )
        for question in questions_list
    ])

    serialized_questions = []
    for q in questions_list:
        options = list(QuestionOptions.objects.filter(question_id=q.question_id).order_by("choice_no"))

        # 셔플 후 표시 번호 재부여
        choices = []
        for idx, opt in enumerate(options, start=1):
            choices.append({
                "choice_id": opt.choice_id,
                "choice_no": idx,
                "content": opt.content,
                "choice_image_path": getattr(opt, "choice_image_path", ""),
            })

        serialized_questions.append({
            "question_id": q.question_id,
            "content": q.content,
            "passage": getattr(q, "passage", ""),
            "image_caption": getattr(q, "image_caption", ""),
            "visual_note": getattr(q, "visual_note", ""),
            "question_image_path": getattr(q, "question_image_path", ""),
            "q_score": q.q_score,
            "era": q.era,
            "topic": q.topic,
            "question_type": q.question_type,
            "question_subtype": getattr(q, "question_subtype", ""),
            "choices": choices,
        })

    serializer = DiagnosisStartResponseSerializer({
        "session_id": session.session_id,
        "total_count": DIAGNOSIS_QUESTION_COUNT,
        "time_limit_sec": DIAGNOSIS_TIME_LIMIT_SEC,
        "questions": serialized_questions,
    })
    return Response(serializer.data, status=status.HTTP_201_CREATED)


# ── API: 답안 제출 ──────────────────────────────────────────────────────────────

@api_view(["POST"])
@transaction.atomic
def diagnosis_submit(request):
    """
    POST /api/diagnosis/submit/
    전체 답안 제출 -> solve_records, analytics 저장 -> 세션 완료 처리

    answers 항목:
      - question_id: int
      - choice_id: int | null  (미응답 시 null)
      - time_spent_ms: int | null
    """
    req_serializer = DiagnosisSubmitRequestSerializer(data=request.data)
    if not req_serializer.is_valid():
        return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    if not request.user.is_authenticated:
        return Response(
            {"error": "로그인이 필요한 기능입니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    data = req_serializer.validated_data
    session_id = data["session_id"]
    elapsed_sec = data["elapsed_sec"]
    answers = data["answers"]
    user_id = request.user.user_id
    question_ids = [answer["question_id"] for answer in answers]
    if len(question_ids) != len(set(question_ids)):
        return Response(
            {"error": "중복된 문항 답안이 포함되어 있습니다."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 세션 확인
    try:
        session = SolveSessions.objects.select_for_update().get(
            session_id=session_id,
            user_id=user_id,
            session_type="diagnostic",
        )
    except SolveSessions.DoesNotExist:
        return Response(
            {"error": "세션을 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if session.status != "in_progress":
        return Response(
            {"error": "이미 제출된 세션입니다."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 선택지 정보 일괄 조회 (choice_id -> QuestionOptions)
    options_map = {
        opt.choice_id: opt
        for opt in QuestionOptions.objects.filter(
            question_id__in=question_ids,
            choice_id__in=[
                answer["choice_id"]
                for answer in answers
                if answer.get("choice_id") is not None
            ],
        )
    }

    # 세션에 배정된 문항만 잠근 뒤 제출 목록과 정확히 일치하는지 확인한다.
    existing_records = {
        record.question_id: record
        for record in SolveRecords.objects.select_for_update()
        .select_related("question")
        .filter(session=session)
    }
    expected_question_ids = set(existing_records)
    if set(question_ids) != expected_question_ids:
        return Response(
            {
                "error": "제출 답안 목록이 세션 문항과 일치하지 않습니다.",
                "expected_count": len(expected_question_ids),
                "submitted_count": len(question_ids),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    records_to_update = []
    total_score = 0
    correct_count = 0

    for ans in answers:
        q_id = ans["question_id"]
        choice_id = ans["choice_id"]
        time_spent_ms = ans.get("time_spent_ms")

        record = existing_records.get(q_id)
        if record is None:
            return Response(
                {"error": "세션 문항 기록을 찾을 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        q = record.question

        # choice_id -> selected_no (choice_no) 변환
        selected_no = None
        is_correct = False
        if choice_id is not None:
            opt = options_map.get(choice_id)
            if opt is None or opt.question_id != q_id:
                return Response(
                    {"error": "선택지가 세션 문항에 속하지 않습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            selected_no = opt.choice_no
            is_correct = opt.is_answer

        if is_correct:
            total_score += q.q_score
            correct_count += 1

        record.selected_no = selected_no
        record.is_correct = is_correct
        record.time_spent_ms = time_spent_ms
        record.q_type = q.question_type
        record.topic = q.topic
        record.era = q.era
        record.q_score = q.q_score
        records_to_update.append(record)

    # 진단 시작 시 만들어 둔 세션 records만 업데이트한다.
    if records_to_update:
        SolveRecords.objects.bulk_update(
            records_to_update,
            ["selected_no", "is_correct", "time_spent_ms", "q_type", "topic", "era", "q_score"],
        )

    answer_rate = round(correct_count / len(answers), 4) if answers else 0.0

    session.status = "completed"
    session.elapsed_sec = min(elapsed_sec, DIAGNOSIS_TIME_LIMIT_SEC)
    session.total_score = total_score
    session.answer_rate = answer_rate
    session.save(
        update_fields=[
            "status",
            "elapsed_sec",
            "total_score",
            "answer_rate",
        ]
    )
    # 완료된 세션 기록을 기준으로 overall/era/type/topic 분석 스냅샷을 저장한다.
    create_session_snapshot(session.session_id)
    _complete_weekly_review_block_for_session(session, user_id)

    resp_serializer = DiagnosisSubmitResponseSerializer({
        "session_id": session.session_id,
        "redirect_url": "/diagnosis/result/?session_id={}".format(session.session_id),
    })
    return Response(resp_serializer.data, status=status.HTTP_200_OK)


# ── API: 진단 결과 ──────────────────────────────────────────────────────────────

def _serialize_diagnosis_session_questions(records):
    """진단평가 이어풀기 화면에서 사용할 문항과 저장된 답안 상태를 반환한다."""
    serialized_questions = []
    for record in records:
        question = record.question
        options = list(
            QuestionOptions.objects.filter(question_id=question.question_id)
            .order_by("choice_no")
        )
        choices = [
            {
                "choice_id": option.choice_id,
                "choice_no": option.choice_no,
                "content": option.content,
                "choice_image_path": getattr(option, "choice_image_path", ""),
            }
            for option in options
        ]
        selected_choice_id = None
        if record.selected_no is not None:
            selected = next(
                (option for option in options if option.choice_no == record.selected_no),
                None,
            )
            selected_choice_id = selected.choice_id if selected else None

        serialized_questions.append({
            "question_id": question.question_id,
            "content": question.content,
            "passage": getattr(question, "passage", ""),
            "image_caption": getattr(question, "image_caption", ""),
            "visual_note": getattr(question, "visual_note", ""),
            "question_image_path": getattr(question, "question_image_path", ""),
            "q_score": question.q_score,
            "era": question.era,
            "topic": question.topic,
            "question_type": question.question_type,
            "question_subtype": getattr(question, "question_subtype", ""),
            "choices": choices,
            "selected_choice_id": selected_choice_id,
            "selected_choice_no": record.selected_no,
            "time_spent_ms": record.time_spent_ms,
            "is_answered": record.selected_no is not None,
        })
    return serialized_questions


@api_view(["GET"])
def diagnosis_in_progress_sessions(request):
    """로그인 사용자의 진행 중인 진단평가 세션 목록을 반환한다."""
    if not request.user.is_authenticated:
        return Response({"error": "로그인이 필요한 기능입니다."}, status=status.HTTP_401_UNAUTHORIZED)

    sessions = list(
        SolveSessions.objects.filter(
            user_id=request.user.user_id,
            session_type="diagnostic",
            status="in_progress",
        ).order_by("-recorded_date", "-session_id")
    )
    # 진단평가는 저장 세션을 1개만 보여주는 정책이므로 과거 진행 중 세션은 정리한다.
    old_session_ids = [session.session_id for session in sessions[1:]]
    if old_session_ids:
        SolveSessions.objects.filter(session_id__in=old_session_ids).delete()
        sessions = sessions[:1]
    session_ids = [session.session_id for session in sessions]
    answered_counts = {
        row["session_id"]: row["answered_count"]
        for row in (
            SolveRecords.objects.filter(session_id__in=session_ids, selected_no__isnull=False)
            .values("session_id")
            .annotate(answered_count=models.Count("record_id"))
        )
    }
    return Response(
        {
            "sessions": [
                {
                    "session_id": session.session_id,
                    "session_type": session.session_type,
                    "total_count": session.total_count,
                    "answered_count": answered_counts.get(session.session_id, 0),
                    "recorded_date": session.recorded_date,
                    "status": session.status,
                }
                for session in sessions
            ]
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
def diagnosis_session(request, session_id):
    """진행 중인 진단평가 세션의 문항과 저장된 답안을 이어풀기용으로 반환한다."""
    if not request.user.is_authenticated:
        return Response({"error": "로그인이 필요한 기능입니다."}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        session = SolveSessions.objects.get(
            session_id=session_id,
            user_id=request.user.user_id,
            session_type="diagnostic",
            status="in_progress",
        )
    except SolveSessions.DoesNotExist:
        return Response({"error": "이어 풀 진단평가 세션을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

    records = SolveRecords.objects.filter(session=session).select_related("question").order_by("record_id")
    elapsed_sec = session.elapsed_sec or 0
    return Response(
        {
            "session_id": session.session_id,
            "session_type": session.session_type,
            "total_count": session.total_count,
            "elapsed_sec": elapsed_sec,
            "remaining_sec": max(DIAGNOSIS_TIME_LIMIT_SEC - elapsed_sec, 0),
            "status": session.status,
            "answered_count": records.filter(selected_no__isnull=False).count(),
            "time_limit_sec": DIAGNOSIS_TIME_LIMIT_SEC,
            "questions": _serialize_diagnosis_session_questions(records),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["PATCH"])
def diagnosis_save_answer(request, session_id):
    """진단평가 풀이 중 선택 답안과 문항별 풀이 시간을 임시 저장한다."""
    if not request.user.is_authenticated:
        return Response({"error": "로그인이 필요한 기능입니다."}, status=status.HTTP_401_UNAUTHORIZED)

    question_id = request.data.get("question_id")
    choice_id = request.data.get("choice_id")
    time_spent_ms = request.data.get("time_spent_ms")
    elapsed_sec = request.data.get("elapsed_sec")
    if not question_id:
        return Response({"error": "question_id가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            session = SolveSessions.objects.select_for_update().get(
                session_id=session_id,
                user_id=request.user.user_id,
                session_type="diagnostic",
                status="in_progress",
            )
            record = SolveRecords.objects.select_for_update().get(session=session, question_id=question_id)

            selected_no = None
            selected_choice_id = None
            is_correct = False
            if choice_id is not None:
                option = QuestionOptions.objects.get(choice_id=choice_id, question_id=question_id)
                selected_no = option.choice_no
                selected_choice_id = option.choice_id
                is_correct = option.is_answer

            record.selected_no = selected_no
            record.is_correct = is_correct
            record.time_spent_ms = time_spent_ms
            record.save(update_fields=["selected_no", "is_correct", "time_spent_ms"])

            if elapsed_sec is not None:
                session.elapsed_sec = elapsed_sec
                session.save(update_fields=["elapsed_sec"])
    except SolveSessions.DoesNotExist:
        return Response({"error": "진단평가 세션을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
    except (SolveRecords.DoesNotExist, QuestionOptions.DoesNotExist):
        return Response({"error": "세션에 포함된 문항 또는 선택지가 아닙니다."}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {
            "session_id": session.session_id,
            "question_id": int(question_id),
            "selected_choice_id": selected_choice_id,
            "selected_choice_no": selected_no,
            "time_spent_ms": time_spent_ms,
            "elapsed_sec": elapsed_sec,
            "is_answered": selected_no is not None,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
def diagnosis_result_api(request, session_id):
    """
    GET /api/diagnosis/result/<session_id>/
    예상 수준 + 시대별/유형별 분석 반환
    """
    if not request.user.is_authenticated:
        return Response(
            {"error": "로그인이 필요한 기능입니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    try:
        session = SolveSessions.objects.get(
            session_id=session_id,
            user_id=request.user.user_id,
            session_type="diagnostic",
        )
    except SolveSessions.DoesNotExist:
        return Response(
            {"error": "세션을 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if session.status != "completed":
        return Response(
            {"error": "아직 완료되지 않은 세션입니다."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    records = list(SolveRecords.objects.filter(session=session))
    analytics = list(Analytics.objects.filter(session=session))

    # 점수 계산
    total_score = session.total_score or 0
    max_score = sum(r.q_score for r in records)
    correct_count = sum(1 for r in records if r.is_correct)
    score_rate = round(total_score / max_score, 4) if max_score else 0.0
    expected_grade = _get_expected_grade(total_score)
    previous_session = (
        SolveSessions.objects.filter(
            user_id=session.user_id,
            session_type="diagnostic",
            status="completed",
            session_id__lt=session.session_id,
        )
        .order_by("-session_id")
        .first()
    )
    previous_score = previous_session.total_score if previous_session else None
    score_change = total_score - previous_score if previous_score is not None else None

    # 시대별/유형별 분석 구성
    era_analytics = []
    type_analytics = []
    for a in analytics:
        total_cnt = _get_total_from_records(records, a)
        correct_cnt = round(a.topic_rate * total_cnt)
        item = {
            "label": a.key_concept,
            "classification": a.classification,
            "total": total_cnt,
            "correct": int(correct_cnt),
            "wrong_rate": round(1 - a.topic_rate, 4),
        }
        if a.classification == "시대":
            era_analytics.append(item)
        elif a.classification == "유형":
            type_analytics.append(item)

    question_ids = [r.question_id for r in records]

    resp_serializer = DiagnosisResultResponseSerializer({
        "session_id": session.session_id,
        "total_count": session.total_count,
        "correct_count": correct_count,
        "total_score": total_score,
        "max_score": max_score,
        "score_rate": score_rate,
        "expected_grade": expected_grade,
        "previous_score": previous_score,
        "score_change": score_change,
        "era_analytics": era_analytics,
        "type_analytics": type_analytics,
        "question_ids": question_ids,
    })
    return Response(resp_serializer.data, status=status.HTTP_200_OK)


def _get_total_from_records(records, analytics_obj):
    """analytics key_concept에 해당하는 records 수 반환"""
    if analytics_obj.classification == "시대":
        return sum(1 for r in records if r.era == analytics_obj.key_concept)
    elif analytics_obj.classification == "유형":
        return sum(1 for r in records if r.q_type == analytics_obj.key_concept)
    return 0


def _pick_diagnosis_question_ids():
    rows = list(Questions.objects.values("question_id", "q_score", "era", "question_type"))
    random.shuffle(rows)
    return _pick_balanced_diagnosis_question_ids(rows)


def _pick_balanced_diagnosis_question_ids(rows):
    buckets = {
        (group_index, score): []
        for group_index in range(len(DIAGNOSIS_ERA_GROUPS))
        for score in DIAGNOSIS_SCORE_COUNTS
    }
    for row in rows:
        group_index = _diagnosis_era_group_index(row["era"])
        score = row["q_score"] or 0
        if group_index is not None and score in DIAGNOSIS_SCORE_COUNTS:
            buckets[(group_index, score)].append(row["question_id"])

    score_plan = _pick_score_plan_by_era(buckets)
    if score_plan is None:
        return []

    selected_ids = []
    for group_index, score_counts in score_plan.items():
        for score, count in score_counts.items():
            selected_ids.extend(buckets[(group_index, score)][:count])
    random.shuffle(selected_ids)
    return selected_ids


def _diagnosis_era_group_index(era):
    for index, group in enumerate(DIAGNOSIS_ERA_GROUPS):
        if era in group["eras"]:
            return index
    return None


def _pick_score_plan_by_era(buckets):
    target_1 = DIAGNOSIS_SCORE_COUNTS[1]
    target_2 = DIAGNOSIS_SCORE_COUNTS[2]
    dp = {(0, 0): {}}

    for group_index, group in enumerate(DIAGNOSIS_ERA_GROUPS):
        next_dp = {}
        for counts in _possible_group_score_counts(group["count"], group_index, buckets):
            for (used_1, used_2), plan in dp.items():
                next_1 = used_1 + counts[1]
                next_2 = used_2 + counts[2]
                if next_1 > target_1 or next_2 > target_2:
                    continue
                next_plan = {**plan, group_index: counts}
                next_dp.setdefault((next_1, next_2), next_plan)
        dp = next_dp

    plan = dp.get((target_1, target_2))
    if not plan:
        return None
    if sum(group_counts[3] for group_counts in plan.values()) != DIAGNOSIS_SCORE_COUNTS[3]:
        return None
    return plan


def _possible_group_score_counts(total_count, group_index, buckets):
    options = []
    max_1 = min(total_count, len(buckets[(group_index, 1)]))
    max_2 = min(total_count, len(buckets[(group_index, 2)]))
    max_3 = min(total_count, len(buckets[(group_index, 3)]))
    for count_1 in range(max_1 + 1):
        for count_2 in range(max_2 + 1):
            count_3 = total_count - count_1 - count_2
            if 0 <= count_3 <= max_3:
                options.append({1: count_1, 2: count_2, 3: count_3})
    random.shuffle(options)
    return options


def _pick_exact_score_question_ids(rows, count, target_score):
    dp = {(0, 0): []}
    for row in rows:
        qid = row["question_id"]
        score = row["q_score"] or 0
        for (used_count, used_score), ids in list(dp.items()):
            next_count = used_count + 1
            next_score = used_score + score
            if next_count > count or next_score > target_score:
                continue
            state = (next_count, next_score)
            if state not in dp:
                dp[state] = ids + [qid]
                if state == (count, target_score):
                    return dp[state]
    return []


# ── API: 문항 해설 ──────────────────────────────────────────────────────────────

@api_view(["GET"])
def diagnosis_explanation(request, session_id, question_id):
    """
    GET /api/diagnosis/result/<session_id>/explanation/<question_id>/
    문항별 해설 + 챗봇 연결 URL
    """
    if not request.user.is_authenticated:
        return Response(
            {"error": "로그인이 필요한 기능입니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    # 세션 확인
    try:
        session = SolveSessions.objects.get(
            session_id=session_id,
            user_id=request.user.user_id,
            session_type="diagnostic",
        )
    except SolveSessions.DoesNotExist:
        return Response(
            {"error": "세션을 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # 문제 확인
    try:
        question = Questions.objects.get(question_id=question_id)
    except Questions.DoesNotExist:
        return Response(
            {"error": "문제를 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # 해당 세션의 풀이 기록 확인
    try:
        record = SolveRecords.objects.get(session=session, question=question)
    except SolveRecords.DoesNotExist:
        return Response(
            {"error": "해당 세션에 포함된 문제가 아닙니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # 선택지 (원래 choice_no 순서)
    options = list(
        QuestionOptions.objects.filter(question_id=question_id).order_by("choice_no")
    )
    choices = [
        {
            "choice_id": opt.choice_id,
            "choice_no": opt.choice_no,
            "content": opt.content,
            "choice_explanation": opt.choice_explanation,
        }
        for opt in options
    ]

    # 정답 choice_no
    correct_opt = next((opt for opt in options if opt.is_answer), None)
    correct_choice_no = correct_opt.choice_no if correct_opt else None
    correct_choice_id = correct_opt.choice_id if correct_opt else None
    user_opt = next((opt for opt in options if opt.choice_no == record.selected_no), None)
    user_choice_id = user_opt.choice_id if user_opt else None

    # 챗봇 URL (챗봇 앱 연결)
    chatbot_url = "/chatbot/?question_id={}".format(question_id)

    resp_serializer = DiagnosisExplanationResponseSerializer({
        "question_id": question.question_id,
        "content": question.content,
        "passage": getattr(question, "passage", ""),
        "visual_note": getattr(question, "visual_note", ""),
        "question_image_path": getattr(question, "question_image_path", ""),
        "q_score": question.q_score,
        "era": question.era,
        "topic": question.topic,
        "question_type": question.question_type,
        "question_subtype": getattr(question, "question_subtype", ""),
        "correct_choice_no": correct_choice_no,
        "correct_choice_id": correct_choice_id,
        "answer_explanation": question.answer_explanation,
        "core_concept": question.core_concept,
        "time_spent_ms": record.time_spent_ms,
        "choices": choices,
        "user_choice_no": record.selected_no,
        "user_choice_id": user_choice_id,
        "is_correct": record.is_correct,
        "chatbot_url": chatbot_url,
    })
    return Response(resp_serializer.data, status=status.HTTP_200_OK)

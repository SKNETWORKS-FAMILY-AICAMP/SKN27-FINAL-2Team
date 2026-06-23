import random
from datetime import date

from django.db import models
from django.shortcuts import render
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import QuestionOptions, Questions, SolveRecords, SolveSessions
from .serializers import (
    FilterOptionsResponse,
    InProgressSessionsResponse,
    SaveAnswerRequest,
    SaveAnswerResponse,
    SavedSessionResponse,
    StartQuestionsRequest,
    StartQuestionsResponse,
)


PRACTICE_SESSION_TYPE = "practice"
GUEST_DAILY_COUNT = 10
GUEST_QUESTION_BASE_DATE = date(2026, 6, 23)
PLACEHOLDER_CONTENT = "문항 이미지를 보고 정답을 선택하세요."


# 문제 생성 조건 페이지를 렌더링한다.
def question_create(request):
    return render(request, "question/create.html")


# 문제 풀이 페이지를 렌더링한다.
def question_exam(request):
    return render(request, "question/question_exam.html")


# 문제 풀이 결과 페이지를 렌더링한다.
def question_result(request):
    return render(request, "question/question_result.html")


# 문제 생성/풀이에 사용할 기본 문제 목록을 조회한다.
# placeholder로 생성된 임시 문항은 실제 풀이 대상에서 제외한다.
def _base_question_queryset():
    return Questions.objects.exclude(content=PLACEHOLDER_CONTENT)


# 특정 컬럼의 고유 값을 목록으로 반환한다.
# 문제 생성 조건의 시대, 주제, 유형 목록을 만들 때 사용한다.
def _distinct_values(qs, field_name):
    return list(
        qs.exclude(**{field_name: ""})
        .exclude(**{field_name: None})
        .values_list(field_name, flat=True)
        .distinct()
        .order_by(field_name)
    )


# Questions 모델 목록을 문제 생성 API 응답 JSON으로 변환한다.
# 아직 풀이 전이므로 사용자 선택 답안 정보는 포함하지 않는다.
def _serialize_questions(questions):
    serialized_questions = []
    for question in questions:
        options = QuestionOptions.objects.filter(
            question_id=question.question_id
        ).order_by("choice_no")
        choices = [
            {
                "choice_id": option.choice_id,
                "choice_no": option.choice_no,
                "content": option.content,
            }
            for option in options
        ]
        serialized_questions.append({
            "question_id": question.question_id,
            "content": question.content,
            "passage": question.passage,
            "visual_note": question.visual_note,
            "question_image_path": question.question_image_path,
            "q_score": question.q_score,
            "era": question.era,
            "topic": question.topic,
            "question_type": question.question_type,
            "choices": choices,
        })
    return serialized_questions


# SolveRecords 목록을 풀이 세션 조회 API 응답 JSON으로 변환한다.
# 문제 정보와 함께 사용자의 임시 저장 답안 상태를 포함한다.
def _serialize_session_questions(records):
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
            "passage": question.passage,
            "visual_note": question.visual_note,
            "question_image_path": question.question_image_path,
            "q_score": question.q_score,
            "era": question.era,
            "topic": question.topic,
            "question_type": question.question_type,
            "choices": choices,
            "selected_choice_id": selected_choice_id,
            "selected_choice_no": record.selected_no,
            "time_spent_sec": record.time_spent_sec,
            "is_answered": record.selected_no is not None,
        })
    return serialized_questions


# 로그인 사용자의 user_id를 반환한다.
# 비로그인 사용자는 None을 반환해 저장/불러오기 기능을 제한한다.
def _get_login_user_id(request):
    if getattr(request.user, "is_authenticated", False):
        return request.user.user_id
    return None


# 비로그인 사용자에게 제공할 오늘의 10문항을 결정한다.
# 같은 날짜에서 항상 같은 문제 세트가 나오고, 날짜가 바뀌면 다른 세트가 나온다.
def _daily_guest_question_ids(question_ids):
    # 비로그인 사용자는 DB의 question_id 순서대로 하루 10문항씩 제공한다.
    # 전체 문제를 끝까지 사용하면 나머지 연산으로 다시 첫 문제부터 이어서 제공한다.
    day_index = (timezone.localdate() - GUEST_QUESTION_BASE_DATE).days
    start_index = (day_index * GUEST_DAILY_COUNT) % len(question_ids)
    return [
        question_ids[(start_index + offset) % len(question_ids)]
        for offset in range(GUEST_DAILY_COUNT)
    ]


# 1. 문제 생성 조건 API
# - GET /question/api/filters/: DB에 있는 문제의 시대/주제/배점/유형/문항 수 조건을 제공한다.
# - POST /question/api/start/: 선택 조건에 맞는 문제를 뽑는다.
#   로그인 사용자는 solve_sessions/solve_records에 저장되어 이어 풀기가 가능하고,
#   비로그인 사용자는 날짜 기준으로 하루 동안 고정된 10문항을 받아 바로 풀이한다.
@api_view(["GET"])
# 문제 생성 화면에서 사용할 필터 조건 목록을 제공한다.
def question_filters(request):
    qs = _base_question_queryset()
    total_count = qs.count()
    counts = [count for count in [10, 20, 30, 50] if count <= total_count]
    if total_count and not counts:
        counts = [total_count]

    serializer = FilterOptionsResponse({
        "eras": _distinct_values(qs, "era"),
        "topics": _distinct_values(qs, "topic"),
        "scores": list(qs.values_list("q_score", flat=True).distinct().order_by("q_score")),
        "question_types": _distinct_values(qs, "question_type"),
        "counts": counts,
    })
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
# 선택한 조건으로 문제를 생성한다.
# 로그인 사용자는 풀이 세션을 DB에 저장하고, 비로그인 사용자는 오늘의 고정 10문항만 반환한다.
def question_start(request):
    req_serializer = StartQuestionsRequest(data=request.data)
    if not req_serializer.is_valid():
        return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = req_serializer.validated_data
    user_id = _get_login_user_id(request)
    qs = _base_question_queryset()

    if user_id is not None and data["eras"]:
        qs = qs.filter(era__in=data["eras"])
    if user_id is not None and data["topics"]:
        qs = qs.filter(topic__in=data["topics"])
    if user_id is not None and data["scores"]:
        qs = qs.filter(q_score__in=data["scores"])
    if user_id is not None and data["question_types"]:
        qs = qs.filter(question_type__in=data["question_types"])

    question_ids = list(qs.order_by("question_id").values_list("question_id", flat=True))
    count = data["count"] if user_id is not None else GUEST_DAILY_COUNT
    if len(question_ids) < count:
        return Response(
            {
                "error": "조건에 맞는 문제가 부족합니다.",
                "available_count": len(question_ids),
                "requested_count": count,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if user_id is None:
        selected_ids = _daily_guest_question_ids(question_ids)
    else:
        selected_ids = random.sample(question_ids, count)
    questions = list(Questions.objects.filter(question_id__in=selected_ids))
    questions.sort(key=lambda question: selected_ids.index(question.question_id))

    session = None
    if user_id is not None:
        session = SolveSessions.objects.create(
            user_id=user_id,
            session_type=PRACTICE_SESSION_TYPE,
            total_count=count,
            status="in_progress",
            recorded_date=date.today(),
        )
        SolveRecords.objects.bulk_create([
            SolveRecords(
                session=session,
                question=question,
                selected_no=None,
                is_correct=False,
                q_type=question.question_type,
                topic=question.topic,
                era=question.era,
                q_score=question.q_score,
            )
            for question in questions
        ])

    serializer = StartQuestionsResponse({
        "session_id": session.session_id if session else None,
        "total_count": count,
        "is_saved": session is not None,
        "questions": _serialize_questions(questions),
    })
    return Response(serializer.data, status=status.HTTP_201_CREATED)


# 2. 문제 풀이 API
# - solve_sessions는 문제풀이 한 판을 저장한다. 사용자가 중간에 나가도 같은 세션으로 이어 풀 수 있다.
# - solve_records는 해당 세션에 포함된 문제를 1문항씩 저장하고, 선택 답안/풀이 시간/정오 여부를 기록한다.
# - 비로그인 사용자는 문제 생성/풀이만 가능하며, 저장/불러오기 API는 사용할 수 없다.
# - GET /question/api/sessions/in-progress/: 이어 풀 수 있는 practice 세션 목록을 조회한다.
# - GET /question/api/session/<session_id>/: practice 세션의 문제와 임시 저장 답안을 조회한다.
# - PATCH /question/api/session/<session_id>/answer/: 특정 문항의 선택 답안을 solve_records에 임시 저장한다.
@api_view(["GET"])
# 로그인 사용자의 진행 중인 practice 풀이 세션 목록을 반환한다.
# 문제 생성 화면의 "문제 불러오기" 기능에서 사용한다.
def question_in_progress_sessions(request):
    user_id = _get_login_user_id(request)
    if user_id is None:
        return Response(
            {"error": "로그인이 필요한 기능입니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    sessions = list(
        SolveSessions.objects.filter(
            user_id=user_id,
            session_type=PRACTICE_SESSION_TYPE,
            status="in_progress",
        ).order_by("-recorded_date", "-session_id")
    )
    session_ids = [session.session_id for session in sessions]
    answered_counts = {
        row["session_id"]: row["answered_count"]
        for row in (
            SolveRecords.objects.filter(
                session_id__in=session_ids,
                selected_no__isnull=False,
            )
            .values("session_id")
            .annotate(answered_count=models.Count("record_id"))
        )
    }

    serializer = InProgressSessionsResponse({
        "sessions": [
            {
                "session_id": session.session_id,
                "total_count": session.total_count,
                "answered_count": answered_counts.get(session.session_id, 0),
                "recorded_date": session.recorded_date,
                "status": session.status,
            }
            for session in sessions
        ]
    })
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["GET"])
# 특정 practice 세션의 문제 목록과 임시 저장 답안을 반환한다.
# 사용자가 풀이 도중 나갔다가 이어 풀 때 사용한다.
def question_session(request, session_id):
    user_id = _get_login_user_id(request)
    if user_id is None:
        return Response(
            {"error": "로그인이 필요한 기능입니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        session = SolveSessions.objects.get(
            session_id=session_id,
            user_id=user_id,
            session_type=PRACTICE_SESSION_TYPE,
        )
    except SolveSessions.DoesNotExist:
        return Response(
            {"error": "풀이 세션을 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    records = list(
        SolveRecords.objects.filter(session=session)
        .select_related("question")
        .order_by("record_id")
    )
    serializer = SavedSessionResponse({
        "session_id": session.session_id,
        "session_type": session.session_type,
        "total_count": session.total_count,
        "status": session.status,
        "answered_count": sum(1 for record in records if record.selected_no is not None),
        "questions": _serialize_session_questions(records),
    })
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["PATCH"])
# 특정 practice 세션의 한 문항 답안을 임시 저장한다.
# 선택 해제 시 choice_id를 null로 보내면 selected_no도 비워진다.
def question_save_answer(request, session_id):
    user_id = _get_login_user_id(request)
    if user_id is None:
        return Response(
            {"error": "로그인이 필요한 기능입니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    req_serializer = SaveAnswerRequest(data=request.data)
    if not req_serializer.is_valid():
        return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    try:
        session = SolveSessions.objects.get(
            session_id=session_id,
            user_id=user_id,
            session_type=PRACTICE_SESSION_TYPE,
        )
    except SolveSessions.DoesNotExist:
        return Response(
            {"error": "풀이 세션을 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if session.status == "completed":
        return Response(
            {"error": "이미 제출된 세션입니다."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = req_serializer.validated_data
    question_id = data["question_id"]
    choice_id = data["choice_id"]

    try:
        record = SolveRecords.objects.select_related("question").get(
            session=session,
            question_id=question_id,
        )
    except SolveRecords.DoesNotExist:
        return Response(
            {"error": "이 세션에 포함된 문제가 아닙니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    selected_choice_id = None
    selected_choice_no = None
    is_correct = False
    if choice_id is not None:
        try:
            option = QuestionOptions.objects.get(
                choice_id=choice_id,
                question_id=question_id,
            )
        except QuestionOptions.DoesNotExist:
            return Response(
                {"error": "선택지가 해당 문제에 속하지 않습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        selected_choice_id = option.choice_id
        selected_choice_no = option.choice_no
        is_correct = option.is_answer

    record.selected_no = selected_choice_no
    record.is_correct = is_correct
    record.time_spent_sec = data["time_spent_sec"]
    record.save(update_fields=["selected_no", "is_correct", "time_spent_sec"])

    serializer = SaveAnswerResponse({
        "session_id": session.session_id,
        "question_id": question_id,
        "selected_choice_id": selected_choice_id,
        "selected_choice_no": selected_choice_no,
        "is_answered": selected_choice_no is not None,
    })
    return Response(serializer.data, status=status.HTTP_200_OK)


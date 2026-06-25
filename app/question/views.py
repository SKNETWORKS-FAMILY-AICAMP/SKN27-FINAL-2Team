import random
from datetime import date

from django.db import models, transaction
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
    SaveNoteRequest,
    SavedSessionResponse,
    StartQuestionsRequest,
    StartQuestionsResponse,
)


PRACTICE_SESSION_TYPE = "practice"
GUEST_DAILY_COUNT = 10
IN_PROGRESS_SESSION_STATUS = "in_progress"
SAVED_SESSION_STATUS = "saved"
COMPLETED_SESSION_STATUS = "completed"
GUEST_QUESTION_BASE_DATE = date(2026, 6, 23)
TIME_LIMIT_SECONDS_BY_COUNT = {
    50: 80 * 60,
    40: 65 * 60,
    30: 50 * 60,
    20: 35 * 60,
    10: 20 * 60,
}
SCORE_RATIO = {
    3: 1,
    2: 3,
    1: 1,
}
BASIC_SCORE_COUNTS = {3: 10, 2: 30, 1: 10}
HARD_SCORE_COUNTS = {3: 20, 2: 10, 1: 20}
DETAIL_DIFFICULTY_RATIOS = {
    "상": {3: 2, 2: 1, 1: 2},
    "중": {3: 1, 2: 3, 1: 1},
    "하": {3: 0, 2: 2, 1: 3},
}
DIFFICULTY_TO_SCORE = {
    "상": 3,
    "중": 2,
    "하": 1,
}
PLACEHOLDER_CONTENT = "문항 이미지를 보고 정답을 선택하세요."
ERA_FILTER_VALUES = [
    "선사 시대",
    "고조선",
    "초기 국가",
    "삼국 시대",
    "남북국 시대",
    "고려",
    "조선 전기",
    "조선 후기",
    "개항기",
    "일제 강점기",
    "현대",
    "통합 주제",
]
QUESTION_SUBTYPE_FILTER_VALUES = ["개념", "사료", "연표", "인물", "지역"]


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
# 현재 테스트 데이터는 이미지 기반 문항도 포함하므로 questions 테이블의 전체 문항을 풀이 대상으로 사용한다.
def _base_question_queryset():
    return Questions.objects.all()


def _delete_other_saved_sessions(user_id, current_session_id):
    old_saved_session_ids = list(
        SolveSessions.objects.filter(
            user_id=user_id,
            session_type=PRACTICE_SESSION_TYPE,
            status=SAVED_SESSION_STATUS,
        )
        .exclude(session_id=current_session_id)
        .values_list("session_id", flat=True)
    )
    if not old_saved_session_ids:
        return

    SolveRecords.objects.filter(session_id__in=old_saved_session_ids).delete()
    SolveSessions.objects.filter(session_id__in=old_saved_session_ids).delete()


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


# 기준 목록 순서대로 DB에 존재하는 값만 반환한다.
# 화면에 불분명한 시대값이 노출되지 않도록 시대 필터에서 사용한다.
def _ordered_existing_values(qs, field_name, ordered_values):
    existing_values = set(
        qs.exclude(**{field_name: ""})
        .exclude(**{field_name: None})
        .values_list(field_name, flat=True)
        .distinct()
    )
    return [value for value in ordered_values if value in existing_values]


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
                "choice_image_path": option.choice_image_path,
                "choice_explanation": option.choice_explanation,
            }
            for option in options
        ]
        serialized_questions.append({
            "question_id": question.question_id,
            "content": question.content,
            "passage": question.passage,
            "image_caption": question.image_caption,
            "question_image_path": question.question_image_path,
            "q_score": question.q_score,
            "era": question.era,
            "topic": question.topic,
            "question_type": question.question_type,
            "question_subtype": question.question_subtype,
            "answer_no": question.answer_no,
            "answer_explanation": question.answer_explanation,
            "core_concept": question.core_concept,
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
                "choice_image_path": option.choice_image_path,
                "choice_explanation": option.choice_explanation,
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
            "image_caption": question.image_caption,
            "question_image_path": question.question_image_path,
            "q_score": question.q_score,
            "era": question.era,
            "topic": question.topic,
            "question_type": question.question_type,
            "question_subtype": question.question_subtype,
            "answer_no": question.answer_no,
            "answer_explanation": question.answer_explanation,
            "core_concept": question.core_concept,
            "choices": choices,
            "selected_choice_id": selected_choice_id,
            "selected_choice_no": record.selected_no,
            "time_spent_ms": record.time_spent_ms,
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


# 문제 수에 따라 풀이 제한 시간을 초 단위로 반환한다.
def _time_limit_seconds(total_count):
    return TIME_LIMIT_SECONDS_BY_COUNT.get(total_count, 20 * 60)


# 요청 문항 수를 3점:2점:1점 = 1:3:1 비율로 나눈다.
# 일부 난이도만 선택한 경우에는 선택한 난이도끼리 같은 비율 기준으로 다시 나눈다.
def _score_counts(total_count, selected_scores):
    scores = [score for score in [3, 2, 1] if score in selected_scores]
    ratio_sum = sum(SCORE_RATIO[score] for score in scores)
    counts = {
        score: (total_count * SCORE_RATIO[score]) // ratio_sum
        for score in scores
    }

    assigned_count = sum(counts.values())
    remainders = sorted(
        scores,
        key=lambda score: (
            (total_count * SCORE_RATIO[score]) % ratio_sum,
            SCORE_RATIO[score],
        ),
        reverse=True,
    )
    for score in remainders[:total_count - assigned_count]:
        counts[score] += 1
    return counts


def _score_counts_by_ratio(total_count, score_ratio):
    scores = [score for score in [3, 2, 1] if score_ratio.get(score, 0) > 0]
    ratio_sum = sum(score_ratio[score] for score in scores)
    counts = {
        score: (total_count * score_ratio[score]) // ratio_sum
        for score in scores
    }

    assigned_count = sum(counts.values())
    remainders = sorted(
        scores,
        key=lambda score: (
            (total_count * score_ratio[score]) % ratio_sum,
            score_ratio[score],
        ),
        reverse=True,
    )
    for score in remainders[:total_count - assigned_count]:
        counts[score] += 1
    return counts


# 난이도별 목표 개수에 맞춰 문제 ID를 추출한다.
def _sample_questions_by_score(qs, count, selected_scores):
    score_counts = _score_counts(count, selected_scores)
    return _sample_questions_by_score_counts(qs, score_counts)


# 점수별 목표 개수가 직접 지정된 경우 해당 개수에 맞춰 문제 ID를 추출한다.
def _sample_questions_by_score_counts(qs, score_counts):
    selected_ids = []

    for score, score_count in score_counts.items():
        if score_count <= 0:
            continue
        score_ids = list(
            qs.filter(q_score=score).values_list("question_id", flat=True)
        )
        if len(score_ids) < score_count:
            return None, {
                "error": "선택한 난이도 구성에 맞는 문제가 부족합니다.",
                "score": score,
                "available_count": len(score_ids),
                "requested_count": score_count,
            }
        selected_ids.extend(random.sample(score_ids, score_count))

    random.shuffle(selected_ids)
    return selected_ids, None


def _score_counts_for_generation_mode(data):
    generation_mode = data["generation_mode"]

    if generation_mode == "basic":
        return BASIC_SCORE_COUNTS.copy(), None
    if generation_mode == "hard":
        return HARD_SCORE_COUNTS.copy(), None

    missing_fields = []
    if not data["eras"]:
        missing_fields.append("시대")
    if not data["topics"]:
        missing_fields.append("주제")
    if not data["question_types"]:
        missing_fields.append("대유형")
    if not data["question_subtypes"]:
        missing_fields.append("소유형")
    if len(data["difficulties"]) != 1:
        missing_fields.append("난이도")
    if missing_fields:
        return None, {
            "error": "문제 생성 조건을 모두 선택해 주세요.",
            "missing_fields": missing_fields,
        }

    difficulty = data["difficulties"][0]
    return _score_counts_by_ratio(data["count"], DETAIL_DIFFICULTY_RATIOS[difficulty]), None


# 1. 문제 생성 조건 API
# - GET은 문제 생성 화면의 선택 조건 목록을 제공한다.
# - POST는 생성 모드와 조건에 맞는 문제를 뽑아 풀이 세션을 만든다.
@api_view(["GET"])
# 문제 생성 화면에서 사용할 필터 조건 목록을 제공한다.
def question_filters(request):
    qs = _base_question_queryset()

    q_filters = FilterOptionsResponse({
        "eras": _ordered_existing_values(qs, "era", ERA_FILTER_VALUES),
        "topics": _distinct_values(qs, "topic"),
        "difficulties": ["상", "중", "하"],
        "question_types": _distinct_values(qs, "question_type"),
        "question_subtypes": QUESTION_SUBTYPE_FILTER_VALUES,
        "counts": [10, 20, 30, 40, 50],
    })
    return Response(q_filters.data, status=status.HTTP_200_OK)


@api_view(["POST"])
# 선택한 조건으로 문제를 생성한다.
# 로그인 사용자는 풀이 세션을 DB에 저장하고, 비로그인 사용자는 오늘의 고정 10문항만 반환한다.
def question_start(request):
    req_question_start = StartQuestionsRequest(data=request.data)
    if not req_question_start.is_valid():
        return Response(req_question_start.errors, status=status.HTTP_400_BAD_REQUEST)

    data = req_question_start.validated_data
    user_id = _get_login_user_id(request)
    qs = _base_question_queryset()
    score_counts = {}
    score_counts_error = None
    if user_id is not None:
        score_counts, score_counts_error = _score_counts_for_generation_mode(data)
        if score_counts_error:
            return Response(score_counts_error, status=status.HTTP_400_BAD_REQUEST)

    if user_id is not None and data["eras"]:
        qs = qs.filter(era__in=data["eras"])
    if user_id is not None and data["topics"]:
        qs = qs.filter(topic__in=data["topics"])
    if user_id is not None and data["question_types"]:
        qs = qs.filter(question_type__in=data["question_types"])
    if user_id is not None and data["question_subtypes"]:
        qs = qs.filter(question_subtype__in=data["question_subtypes"])

    count = sum(score_counts.values()) if user_id is not None else GUEST_DAILY_COUNT
    if user_id is not None:
        selected_scores = list(score_counts.keys())
        qs = qs.filter(q_score__in=selected_scores)

    question_ids = list(qs.order_by("question_id").values_list("question_id", flat=True))
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
        selected_ids, score_error = _sample_questions_by_score_counts(qs, score_counts)
        if score_error:
            return Response(score_error, status=status.HTTP_400_BAD_REQUEST)
    questions = list(Questions.objects.filter(question_id__in=selected_ids))
    questions.sort(key=lambda question: selected_ids.index(question.question_id))

    session = None
    if user_id is not None:
        session = SolveSessions.objects.create(
            user_id=user_id,
            session_type=PRACTICE_SESSION_TYPE,
            total_count=count,
            elapsed_sec=0,
            status=IN_PROGRESS_SESSION_STATUS,
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

    start_question_response = StartQuestionsResponse({
        "session_id": session.session_id if session else None,
        "total_count": count,
        "is_saved": session is not None,
        "questions": _serialize_questions(questions),
    })
    return Response(start_question_response.data, status=status.HTTP_201_CREATED)


# 2. 문제 풀이 API
# - 저장 버튼을 누른 practice 세션만 불러오기 목록에 노출한다.
# - 세션 상세/답안 저장 API는 선택 답안, 남은 시간, 문항별 풀이 시간을 관리한다.
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
            status=SAVED_SESSION_STATUS,
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

    inprogress_question_sessions = InProgressSessionsResponse({
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
    return Response(inprogress_question_sessions.data, status=status.HTTP_200_OK)


@api_view(["GET"])
# 특정 practice 세션의 문제 목록과 임시 저장 답안을 반환한다.
# 사용자가 풀이 도중 나갔다가 이어 풀 때 사용한다.
def question_save_session(request, session_id):
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
        "elapsed_sec": session.elapsed_sec,
        "remaining_sec": max(_time_limit_seconds(session.total_count) - (session.elapsed_sec or 0), 0),
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
        is_correct = selected_choice_no == record.question.answer_no

    record.selected_no = selected_choice_no
    record.is_correct = is_correct
    record.time_spent_ms = data["time_spent_ms"]
    record.save(update_fields=["selected_no", "is_correct", "time_spent_ms"])

    update_session_fields = []
    if data["elapsed_sec"] is not None:
        session.elapsed_sec = data["elapsed_sec"]
        update_session_fields.append("elapsed_sec")
    if data["mark_saved"]:
        session.status = SAVED_SESSION_STATUS
        update_session_fields.append("status")
    if data["mark_completed"]:
        session.status = COMPLETED_SESSION_STATUS
        if "status" not in update_session_fields:
            update_session_fields.append("status")
    if update_session_fields:
        if data["mark_saved"]:
            with transaction.atomic():
                _delete_other_saved_sessions(user_id, session.session_id)
                session.save(update_fields=update_session_fields)
        else:
            session.save(update_fields=update_session_fields)

    serializer = SaveAnswerResponse({
        "session_id": session.session_id,
        "question_id": question_id,
        "selected_choice_id": selected_choice_id,
        "selected_choice_no": selected_choice_no,
        "time_spent_ms": record.time_spent_ms,
        "elapsed_sec": session.elapsed_sec,
        "is_answered": selected_choice_no is not None,
    })
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
# 3. 노트 저장 API
# - 결과 화면에서 사용자가 특정 문항을 노트에 저장하거나 저장 해제할 때 사용한다.
# - 세션 소유자만 자신의 solve_records.is_saved 값을 변경할 수 있다.
def question_save_note(request, session_id):
    user_id = _get_login_user_id(request)
    if user_id is None:
        return Response(
            {"error": "로그인이 필요한 기능입니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    req_serializer = SaveNoteRequest(data=request.data)
    if not req_serializer.is_valid():
        return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = req_serializer.validated_data
    try:
        session = SolveSessions.objects.get(
            session_id=session_id,
            user_id=user_id,
            session_type=PRACTICE_SESSION_TYPE,
        )
    except SolveSessions.DoesNotExist:
        return Response(
            {"error": "저장할 세션을 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        record = SolveRecords.objects.get(
            session=session,
            question_id=data["question_id"],
        )
    except SolveRecords.DoesNotExist:
        return Response(
            {"error": "세션에 포함된 문제가 아닙니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    record.is_saved = data["is_saved"]
    record.saved_at = timezone.now() if data["is_saved"] else None
    record.save(update_fields=["is_saved", "saved_at"])

    return Response(
        {
            "record_id": record.record_id,
            "session_id": session.session_id,
            "question_id": record.question_id,
            "is_saved": record.is_saved,
            "saved_at": record.saved_at,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
def question_wrong_chat_context(request, session_id):
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
    question_ids = [record.question_id for record in records]
    option_map = {}
    for option in QuestionOptions.objects.filter(question_id__in=question_ids).order_by(
        "question_id",
        "choice_no",
    ):
        option_map.setdefault(option.question_id, []).append(option)

    wrong_questions = []
    for number, record in enumerate(records, start=1):
        if record.is_correct:
            continue
        question = record.question
        options = option_map.get(question.question_id, [])
        wrong_questions.append({
            "record_id": record.record_id,
            "session_id": session.session_id,
            "number": number,
            "question_id": question.question_id,
            "content": question.content,
            "passage": question.passage,
            "image_caption": question.image_caption,
            "selected_no": record.selected_no,
            "answer_no": question.answer_no,
            "is_correct": record.is_correct,
            "time_spent_ms": record.time_spent_ms,
            "era": record.era,
            "topic": record.topic,
            "question_type": record.q_type,
            "question_subtype": question.question_subtype,
            "q_score": record.q_score,
            "keyword": question.core_concept,
            "answer_explanation": question.answer_explanation,
            "options": [
                {
                    "choice_no": option.choice_no,
                    "content": option.content,
                    "is_answer": option.is_answer,
                    "choice_explanation": option.choice_explanation,
                }
                for option in options
            ],
        })

    return Response(
        {
            "session_id": session.session_id,
            "wrong_count": len(wrong_questions),
            "wrong_questions": wrong_questions,
        },
        status=status.HTTP_200_OK,
    )

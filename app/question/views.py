import random
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db import models, transaction
from django.shortcuts import render
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from analytics.service.analysis_snapshot import create_session_snapshot
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
    SubmitAnswersRequest,
    SubmitAnswersResponse,
)


PRACTICE_SESSION_TYPE = "practice"
GUEST_DAILY_COUNT = 10
IN_PROGRESS_SESSION_STATUS = "in_progress"
COMPLETED_SESSION_STATUS = "completed"
GUEST_QUESTION_BASE_DATE = date(2026, 6, 23)
TIME_LIMIT_SECONDS_BY_COUNT = {
    50: 80 * 60,
    40: 65 * 60,
    30: 50 * 60,
    20: 35 * 60,
    10: 20 * 60,
}
STUDY_PLAN_BLOCK_FIELDS = {
    "시대": "era",
    "유형": "question_type",
    "주제": "topic",
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
@login_required
def question_create(request):
    return render(request, "question/create.html")


# 문제 풀이 페이지를 렌더링한다.
@login_required
def question_exam(request):
    return render(request, "question/question_exam.html", {"exam_mode": "practice"})


# 문제 풀이 결과 페이지를 렌더링한다.
@login_required
def question_result(request):
    return render(request, "study/result.html", {"result_mode": "practice"})


# 문제 생성/풀이에 사용할 기본 문제 목록을 조회한다.
# 현재 테스트 데이터는 이미지 기반 문항도 포함하므로 questions 테이블의 전체 문항을 풀이 대상으로 사용한다.
def _base_question_queryset():
    return Questions.objects.all()


def _delete_other_in_progress_sessions(user_id, current_session_id=None):
    old_session_queryset = SolveSessions.objects.filter(
        user_id=user_id,
        session_type=PRACTICE_SESSION_TYPE,
        status=IN_PROGRESS_SESSION_STATUS,
    )
    if current_session_id is not None:
        old_session_queryset = old_session_queryset.exclude(session_id=current_session_id)

    old_session_ids = list(
        old_session_queryset.values_list("session_id", flat=True)
    )
    if not old_session_ids:
        return

    SolveRecords.objects.filter(session_id__in=old_session_ids).delete()
    SolveSessions.objects.filter(session_id__in=old_session_ids).delete()


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
def _shuffled_choices_for_question(question, seed_key):
    options = list(
        QuestionOptions.objects.filter(question_id=question.question_id)
        .order_by("choice_no")
    )
    shuffled_options = options[:]
    random.Random(f"{seed_key}:{question.question_id}").shuffle(shuffled_options)

    choices = [
        {
            "choice_id": option.choice_id,
            "choice_no": display_no,
            "content": option.content,
            "choice_image_path": option.choice_image_path,
            "choice_explanation": option.choice_explanation,
            "is_answer": option.is_answer,
        }
        for display_no, option in enumerate(shuffled_options, start=1)
    ]
    answer_no = next(
        (
            display_no
            for display_no, option in enumerate(shuffled_options, start=1)
            if option.is_answer
        ),
        question.answer_no,
    )
    return choices, answer_no, options


def _display_choice_no(choices, choice_id):
    if choice_id is None:
        return None
    matched_choice = next(
        (choice for choice in choices if choice["choice_id"] == choice_id),
        None,
    )
    return matched_choice["choice_no"] if matched_choice else None


def _serialize_questions(questions, seed_key):
    serialized_questions = []
    for question in questions:
        choices, answer_no, _options = _shuffled_choices_for_question(question, seed_key)
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
            "answer_no": answer_no,
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
        choices, answer_no, options = _shuffled_choices_for_question(
            question,
            record.session_id,
        )
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
            "answer_no": answer_no,
            "answer_explanation": question.answer_explanation,
            "core_concept": question.core_concept,
            "choices": choices,
            "selected_choice_id": selected_choice_id,
            "selected_choice_no": _display_choice_no(choices, selected_choice_id),
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


def _find_study_plan_block(user_id, studyplan_id, block_id):
    if not studyplan_id and not block_id:
        return None, None
    if not studyplan_id or not block_id:
        return None, {
            "error": "학습계획 문제를 시작하려면 studyplan_id와 study_plan_block_id가 모두 필요합니다.",
        }

    from analytics.models import StudyPlanMypage
    from analytics.serializers import parse_study_plan_items

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
                return block, None

    return None, {"error": "학습계획 블록을 찾을 수 없습니다."}


def _apply_study_plan_block_filter(qs, block):
    if block is None:
        return qs

    field_name = STUDY_PLAN_BLOCK_FIELDS.get(block.get("classification"))
    label = block.get("label")
    if not field_name or not label:
        return qs

    return qs.filter(**{field_name: label})


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

    if generation_mode == "study_plan":
        return None, {
            "error": "학습 계획 문제를 시작하려면 studyplan_id와 study_plan_block_id가 필요합니다.",
        }

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
    # 학습 계획 문제 풀기는 학습 플래너가 전달한 사용자별 계획 블록을 기준으로 문제를 생성한다.
    if data["generation_mode"] == "study_plan" and user_id is None:
        return Response(
            {"error": "학습 계획 문제 풀기는 로그인이 필요합니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    study_plan_block = None
    if user_id is not None:
        study_plan_block, study_plan_error = _find_study_plan_block(
            user_id,
            data.get("studyplan_id"),
            data.get("study_plan_block_id"),
        )
        if study_plan_error:
            return Response(study_plan_error, status=status.HTTP_400_BAD_REQUEST)

    qs = _base_question_queryset()
    qs = _apply_study_plan_block_filter(qs, study_plan_block)
    score_counts = {}
    score_counts_error = None
    if user_id is not None:
        if study_plan_block is not None:
            data["count"] = int(study_plan_block.get("questionCount") or data["count"])
            score_counts = _score_counts(data["count"], [3, 2, 1])
        else:
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
        with transaction.atomic():
            _delete_other_in_progress_sessions(user_id)
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
                    studyplan_id=data.get("studyplan_id") if study_plan_block else None,
                    study_plan_block_id=data.get("study_plan_block_id") if study_plan_block else None,
                    q_type=question.question_type,
                    topic=question.topic,
                    era=question.era,
                    q_score=question.q_score,
                )
                for question in questions
            ])

    choice_seed_key = session.session_id if session else f"guest:{timezone.localdate().isoformat()}"
    start_question_response = StartQuestionsResponse({
        "session_id": session.session_id if session else None,
        "total_count": count,
        "is_saved": session is not None,
        "questions": _serialize_questions(questions, choice_seed_key),
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
            status=IN_PROGRESS_SESSION_STATUS,
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
    study_plan_sessions = {}
    for row in (
        SolveRecords.objects.filter(
            session_id__in=session_ids,
            studyplan_id__isnull=False,
        )
        .values("session_id", "studyplan_id", "study_plan_block_id")
        .distinct()
    ):
        study_plan_sessions[row["session_id"]] = row

    inprogress_question_sessions = InProgressSessionsResponse({
        "sessions": [
            {
                "session_id": session.session_id,
                "session_source": "study_plan" if session.session_id in study_plan_sessions else "practice",
                "studyplan_id": study_plan_sessions.get(session.session_id, {}).get("studyplan_id"),
                "study_plan_block_id": study_plan_sessions.get(session.session_id, {}).get("study_plan_block_id"),
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


@api_view(["GET"])
# 문제풀이 결과 페이지에서 사용할 completed practice 세션 결과를 반환한다.
# solve_sessions/solve_records 기준으로 점수, 선택 답안, 해설을 다시 조회한다.
# 진단평가 결과는 diagnosis 앱 API를 그대로 사용하므로 이 API는 문제풀이 세션만 담당한다.
def question_session_result(request, session_id):
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
            {"error": "결과 세션을 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if session.status != COMPLETED_SESSION_STATUS:
        return Response(
            {"error": "아직 제출되지 않은 세션입니다."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    records = list(
        SolveRecords.objects.filter(session=session)
        .select_related("question")
        .order_by("record_id")
    )
    questions = []
    correct_count = 0
    total_score = 0
    max_score = 0
    for number, record in enumerate(records, start=1):
        question = record.question
        choices, answer_no, options = _shuffled_choices_for_question(
            question,
            session.session_id,
        )
        selected_option = next(
            (option for option in options if option.choice_no == record.selected_no),
            None,
        )
        selected_choice_id = selected_option.choice_id if selected_option else None
        earned_score = record.q_score if record.is_correct else 0
        correct_count += 1 if record.is_correct else 0
        total_score += earned_score
        max_score += record.q_score or 0
        questions.append({
            "record_id": record.record_id,
            "question_id": question.question_id,
            "number": number,
            "content": question.content,
            "passage": question.passage,
            "image_caption": question.image_caption,
            "question_image_path": question.question_image_path,
            "q_score": record.q_score,
            "earned_score": earned_score,
            "era": record.era,
            "topic": record.topic,
            "question_type": record.q_type,
            "question_subtype": question.question_subtype,
            "selected_choice_no": _display_choice_no(choices, selected_choice_id),
            "selected_choice_id": selected_choice_id,
            "answer_no": answer_no,
            "is_correct": record.is_correct,
            "is_saved": record.is_saved,
            "time_spent_ms": record.time_spent_ms,
            "answer_explanation": question.answer_explanation,
            "core_concept": question.core_concept,
            "choices": choices,
        })

    return Response(
        {
            "mode": "practice",
            "session_id": session.session_id,
            "session_type": session.session_type,
            "total_count": session.total_count,
            "correct_count": correct_count,
            "total_score": total_score,
            "max_score": max_score,
            "elapsed_sec": session.elapsed_sec,
            "questions": questions,
        },
        status=status.HTTP_200_OK,
    )


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
    record.time_spent_ms = data["time_spent_ms"]
    record.save(update_fields=["selected_no", "is_correct", "time_spent_ms"])

    update_session_fields = []
    if data["elapsed_sec"] is not None:
        session.elapsed_sec = data["elapsed_sec"]
        update_session_fields.append("elapsed_sec")
    if update_session_fields:
        session.save(update_fields=update_session_fields)

    choices, _answer_no, _options = _shuffled_choices_for_question(
        record.question,
        session.session_id,
    )
    selected_display_no = _display_choice_no(choices, selected_choice_id)

    serializer = SaveAnswerResponse({
        "session_id": session.session_id,
        "question_id": question_id,
        "selected_choice_id": selected_choice_id,
        "selected_choice_no": selected_display_no,
        "time_spent_ms": record.time_spent_ms,
        "elapsed_sec": session.elapsed_sec,
        "is_answered": selected_display_no is not None,
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
            session_type__in=[PRACTICE_SESSION_TYPE, "diagnostic"],
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
    wrong_questions = []
    for number, record in enumerate(records, start=1):
        if record.is_correct:
            continue
        question = record.question
        choices, answer_no, options = _shuffled_choices_for_question(
            question,
            session.session_id,
        )
        selected_option = next(
            (option for option in options if option.choice_no == record.selected_no),
            None,
        )
        selected_choice_id = selected_option.choice_id if selected_option else None
        wrong_questions.append({
            "record_id": record.record_id,
            "session_id": session.session_id,
            "number": number,
            "question_id": question.question_id,
            "content": question.content,
            "passage": question.passage,
            "image_caption": question.image_caption,
            "selected_no": _display_choice_no(choices, selected_choice_id),
            "answer_no": answer_no,
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
                    "choice_no": choice["choice_no"],
                    "content": choice["content"],
                    "is_answer": choice["is_answer"],
                    "choice_explanation": choice["choice_explanation"],
                }
                for choice in choices
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


@api_view(["POST"])
# practice 세션 제출 API
# 제출 시 전체 문항 답안 목록을 한 번에 저장하고 세션을 completed로 확정한다.
def question_submit_session(request, session_id):
    user_id = _get_login_user_id(request)
    if user_id is None:
        return Response(
            {"error": "로그인이 필요한 기능입니다."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    req_serializer = SubmitAnswersRequest(data=request.data)
    if not req_serializer.is_valid():
        return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = req_serializer.validated_data
    answers = data["answers"]
    answer_question_ids = [answer["question_id"] for answer in answers]
    if len(answer_question_ids) != len(set(answer_question_ids)):
        return Response(
            {"error": "중복된 문항 답안이 포함되어 있습니다."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        with transaction.atomic():
            session = SolveSessions.objects.select_for_update().get(
                session_id=session_id,
                user_id=user_id,
                session_type=PRACTICE_SESSION_TYPE,
            )
            if session.status == COMPLETED_SESSION_STATUS:
                return Response(
                    {"error": "이미 제출된 세션입니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            records = list(
                SolveRecords.objects.select_for_update()
                .select_related("question")
                .filter(session=session)
                .order_by("record_id")
            )
            record_map = {record.question_id: record for record in records}
            record_question_ids = set(record_map.keys())
            submitted_question_ids = set(answer_question_ids)

            if submitted_question_ids != record_question_ids:
                return Response(
                    {
                        "error": "제출 답안 목록이 세션 문항과 일치하지 않습니다.",
                        "expected_count": len(record_question_ids),
                        "submitted_count": len(submitted_question_ids),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            choice_ids = [
                answer["choice_id"]
                for answer in answers
                if answer.get("choice_id") is not None
            ]
            option_map = {
                option.choice_id: option
                for option in QuestionOptions.objects.filter(
                    question_id__in=record_question_ids,
                    choice_id__in=choice_ids,
                )
            }

            for answer in answers:
                record = record_map[answer["question_id"]]
                choice_id = answer.get("choice_id")
                selected_no = None
                is_correct = False

                if choice_id is not None:
                    option = option_map.get(choice_id)
                    if option is None or option.question_id != record.question_id:
                        return Response(
                            {"error": "선택지가 세션 문항에 속하지 않습니다."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    selected_no = option.choice_no
                    is_correct = option.is_answer

                record.selected_no = selected_no
                record.is_correct = is_correct
                record.time_spent_ms = answer.get("time_spent_ms")

            SolveRecords.objects.bulk_update(
                records,
                ["selected_no", "is_correct", "time_spent_ms"],
            )

            answered_count = sum(1 for record in records if record.selected_no is not None)
            correct_count = sum(1 for record in records if record.is_correct)
            total_score = sum(record.q_score or 0 for record in records if record.is_correct)
            answer_rate = round((correct_count / session.total_count) * 100, 2) if session.total_count else 0

            session.status = COMPLETED_SESSION_STATUS
            session.elapsed_sec = data["elapsed_sec"]
            session.answer_rate = answer_rate
            session.total_score = total_score
            session.save(update_fields=["status", "elapsed_sec", "answer_rate", "total_score"])
            create_session_snapshot(session.session_id)
    except SolveSessions.DoesNotExist:
        return Response(
            {"error": "저장 세션을 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    response_serializer = SubmitAnswersResponse({
        "session_id": session.session_id,
        "status": session.status,
        "total_count": session.total_count,
        "answered_count": answered_count,
        "correct_count": correct_count,
        "answer_rate": answer_rate,
        "total_score": total_score,
    })
    return Response(response_serializer.data, status=status.HTTP_200_OK)

import random
from collections import defaultdict
from datetime import datetime, timezone

# from django.contrib.auth.decorators import login_required  # TODO: 인증 연동 후 활성화
from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from analytics.models import Analytics
from question.models import (
    QuestionOptions,
    Questions,
    SolveRecords,
    SolveSessions,
)
from .serializers import (
    DiagnosisExplanationResponseSerializer,
    DiagnosisResultResponseSerializer,
    DiagnosisStartResponseSerializer,
    DiagnosisSubmitRequestSerializer,
    DiagnosisSubmitResponseSerializer,
)

# ── 상수 ───────────────────────────────────────────────────────────────────────
DIAGNOSIS_QUESTION_COUNT = 20
DIAGNOSIS_TIME_LIMIT_SEC = 1200  # 20분

# 예상 수준 기준 (취득점 / 최대점 × 100)
GRADE_THRESHOLDS = [
    (80, "1급"),
    (70, "2급"),
    (60, "3급"),
]


def _get_expected_grade(score_rate_pct: float) -> str:
    """score_rate_pct: 0~100 사이 백분율"""
    for threshold, grade in GRADE_THRESHOLDS:
        if score_rate_pct >= threshold:
            return grade
    return "과락"


# ── 페이지 뷰 (템플릿) ──────────────────────────────────────────────────────────

# @login_required  # TODO: 인증 연동 후 활성화
def diagnosis_intro(request):
    return render(request, "diagnosis/intro.html")


# @login_required  # TODO: 인증 연동 후 활성화
def diagnosis_exam(request):
    return render(request, "diagnosis/exam.html")


# @login_required  # TODO: 인증 연동 후 활성화
def diagnosis_result(request):
    return render(request, "diagnosis/result.html")


# ── API: 안내 정보 ──────────────────────────────────────────────────────────────

@api_view(["GET"])
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

    TODO: 인증 연동 필요 - request.user.user_id 로 교체
    """
    # TODO: 인증 연동 필요 - 아래 user_id를 request.user.user_id 로 교체
    user_id = 1

    # 1) 전체 문제에서 랜덤으로 DIAGNOSIS_QUESTION_COUNT개 선택
    all_question_ids = list(
        Questions.objects.values_list("question_id", flat=True)
    )
    if len(all_question_ids) < DIAGNOSIS_QUESTION_COUNT:
        return Response(
            {"error": "문제 수가 부족합니다."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    selected_ids = random.sample(all_question_ids, DIAGNOSIS_QUESTION_COUNT)
    questions_qs = Questions.objects.filter(question_id__in=selected_ids)

    # 2) 세션 생성
    session = SolveSessions.objects.create(
        user_id=user_id,
        session_type="diagnostic",
        total_count=DIAGNOSIS_QUESTION_COUNT,
        status="in_progress",
    )

    # 3) 문제 목록 구성 (문제 순서 랜덤 + 선택지 셔플)
    questions_list = list(questions_qs)
    random.shuffle(questions_list)

    serialized_questions = []
    for q in questions_list:
        options = list(QuestionOptions.objects.filter(question_id=q.question_id))
        random.shuffle(options)

        # 셔플 후 표시 번호 재부여
        choices = []
        for idx, opt in enumerate(options, start=1):
            choices.append({
                "choice_id": opt.choice_id,
                "choice_no": idx,
                "content": opt.content,
            })

        serialized_questions.append({
            "question_id": q.question_id,
            "content": q.content,
            "passage": q.passage,
            "visual_note": q.visual_note,
            "question_image_path": q.question_image_path,
            "q_score": q.q_score,
            "era": q.era,
            "topic": q.topic,
            "question_type": q.question_type,
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

    data = req_serializer.validated_data
    session_id = data["session_id"]
    elapsed_sec = data["elapsed_sec"]
    answers = data["answers"]

    # 세션 확인
    try:
        session = SolveSessions.objects.get(
            session_id=session_id, session_type="diagnostic"
        )
    except SolveSessions.DoesNotExist:
        return Response(
            {"error": "세션을 찾을 수 없습니다."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if session.status == "completed":
        return Response(
            {"error": "이미 제출된 세션입니다."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 문제 정보 일괄 조회
    question_ids = [a["question_id"] for a in answers]
    questions_map = {
        q.question_id: q
        for q in Questions.objects.filter(question_id__in=question_ids)
    }
    # 선택지 정보 일괄 조회 (choice_id -> QuestionOptions)
    options_map = {
        opt.choice_id: opt
        for opt in QuestionOptions.objects.filter(question_id__in=question_ids)
    }

    # solve_records 생성 + 점수 계산
    records = []
    total_score = 0
    correct_count = 0

    # analytics 집계용
    era_stats = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})
    type_stats = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})

    for ans in answers:
        q_id = ans["question_id"]
        choice_id = ans["choice_id"]
        time_spent_ms = ans.get("time_spent_ms")

        q = questions_map.get(q_id)
        if not q:
            continue

        # choice_id -> selected_no (choice_no) 변환
        selected_no = None
        is_correct = False
        if choice_id is not None:
            opt = options_map.get(choice_id)
            if opt:
                selected_no = opt.choice_no
                is_correct = opt.is_answer

        if is_correct:
            total_score += q.q_score
            correct_count += 1

        records.append(SolveRecords(
            session=session,
            question=q,
            selected_no=selected_no,
            is_correct=is_correct,
            time_spent_ms=time_spent_ms,
            q_type=q.question_type,
            topic=q.topic,
            era=q.era,
            q_score=q.q_score,
        ))

        # analytics 집계
        era_stats[q.era]["total"] += 1
        era_stats[q.era]["correct"] += int(is_correct)
        era_stats[q.era]["time_sum"] += time_spent_ms or 0

        type_stats[q.question_type]["total"] += 1
        type_stats[q.question_type]["correct"] += int(is_correct)
        type_stats[q.question_type]["time_sum"] += time_spent_ms or 0

    # DB 저장
    SolveRecords.objects.bulk_create(records)

    # analytics 저장
    analytics_rows = []
    now = datetime.now(tz=timezone.utc)

    for era, stat in era_stats.items():
        total = stat["total"]
        correct = stat["correct"]
        avg_time = (stat["time_sum"] // total) // 1000 if total else None
        analytics_rows.append(Analytics(
            session=session,
            key_concept=era,
            classification="시대",
            avg_time_sec=avg_time,
            topic_rate=round(correct / total, 4) if total else 0.0,
            date=now,
        ))

    for q_type, stat in type_stats.items():
        total = stat["total"]
        correct = stat["correct"]
        avg_time = (stat["time_sum"] // total) // 1000 if total else None
        analytics_rows.append(Analytics(
            session=session,
            key_concept=q_type,
            classification="유형",
            avg_time_sec=avg_time,
            topic_rate=round(correct / total, 4) if total else 0.0,
            date=now,
        ))

    Analytics.objects.bulk_create(analytics_rows)

    # 세션 완료 업데이트
    answer_rate = round(correct_count / len(answers), 4) if answers else 0.0

    session.status = "completed"
    session.elapsed_sec = elapsed_sec
    session.total_score = total_score
    session.answer_rate = answer_rate
    session.save()

    resp_serializer = DiagnosisSubmitResponseSerializer({
        "session_id": session.session_id,
        "redirect_url": "/diagnosis/result/?session_id={}".format(session.session_id),
    })
    return Response(resp_serializer.data, status=status.HTTP_200_OK)


# ── API: 진단 결과 ──────────────────────────────────────────────────────────────

@api_view(["GET"])
def diagnosis_result_api(request, session_id):
    """
    GET /api/diagnosis/result/<session_id>/
    예상 수준 + 시대별/유형별 분석 반환
    """
    try:
        session = SolveSessions.objects.get(
            session_id=session_id, session_type="diagnostic"
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
    score_rate_pct = score_rate * 100
    expected_grade = _get_expected_grade(score_rate_pct)

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


# ── API: 문항 해설 ──────────────────────────────────────────────────────────────

@api_view(["GET"])
def diagnosis_explanation(request, session_id, question_id):
    """
    GET /api/diagnosis/result/<session_id>/explanation/<question_id>/
    문항별 해설 + 챗봇 연결 URL
    """
    # 세션 확인
    try:
        session = SolveSessions.objects.get(
            session_id=session_id, session_type="diagnostic"
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
        }
        for opt in options
    ]

    # 정답 choice_no
    correct_opt = next((opt for opt in options if opt.is_answer), None)
    correct_choice_no = correct_opt.choice_no if correct_opt else None

    # 챗봇 URL (챗봇 앱 연결)
    chatbot_url = "/chatbot/?question_id={}".format(question_id)

    resp_serializer = DiagnosisExplanationResponseSerializer({
        "question_id": question.question_id,
        "content": question.content,
        "passage": question.passage,
        "visual_note": question.visual_note,
        "question_image_path": question.question_image_path,
        "era": question.era,
        "topic": question.topic,
        "question_type": question.question_type,
        "correct_choice_no": correct_choice_no,
        "answer_explanation": question.answer_explanation,
        "choices": choices,
        "user_choice_no": record.selected_no,
        "is_correct": record.is_correct,
        "chatbot_url": chatbot_url,
    })
    return Response(resp_serializer.data, status=status.HTTP_200_OK)

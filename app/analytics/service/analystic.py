from collections import defaultdict

# 사용자 분석 대시보드 정보 조회 서비스
from question.models import Analytics, SolveSessions


def get_user_analytics(user_id):
    # 사용자의 모든 세션 조회
    session_ids = SolveSessions.objects.filter(user_id=user_id).values_list(
        "session_id",
        flat=True,
    )
    return Analytics.objects.filter(session_id__in=session_ids)


def get_analytics_info(user_id):
    # 분석 대시보드 정보 조회
    analytics = get_user_analytics(user_id).first()

    return analytics


def analytics_summary(user_id):
    user_analytics = get_user_analytics(user_id)
    type_analytics = user_analytics.filter(classification="유형")
    era_analytics = user_analytics.filter(classification="시대")
    topic_analytics = user_analytics.filter(classification="주제")

    # 평균 풀이시간 (유형별, 시대별)
    average_time_type = defaultdict(lambda: {"total": 0, "time_sum": 0})
    for analytics in type_analytics:
        average_time_type[analytics.key_concept]["total"] += 1
        average_time_type[analytics.key_concept]["time_sum"] += analytics.avg_time_sec
    average_time_era = defaultdict(lambda: {"total": 0, "time_sum": 0})
    for analytics in era_analytics:
        average_time_era[analytics.key_concept]["total"] += 1
        average_time_era[analytics.key_concept]["time_sum"] += analytics.avg_time_sec

    # 취약점 분석
    weak_topics_type = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})
    for analytics in type_analytics:
        weak_topics_type[analytics.key_concept]["total"] += 1
        weak_topics_type[analytics.key_concept]["correct"] += analytics.topic_rate
        weak_topics_type[analytics.key_concept]["time_sum"] += analytics.avg_time_sec
    weak_topics_era = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})
    for analytics in era_analytics:
        weak_topics_era[analytics.key_concept]["total"] += 1
        weak_topics_era[analytics.key_concept]["correct"] += analytics.topic_rate
        weak_topics_era[analytics.key_concept]["time_sum"] += analytics.avg_time_sec

    # 오답률(시대별, 유형별, 주제별)
    wrong_rate_type = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})
    for analytics in type_analytics:
        wrong_rate_type[analytics.key_concept]["total"] += 1
        wrong_rate_type[analytics.key_concept]["correct"] += 1 - analytics.topic_rate
        wrong_rate_type[analytics.key_concept]["time_sum"] += analytics.avg_time_sec
    wrong_rate_era = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})
    for analytics in era_analytics:
        wrong_rate_era[analytics.key_concept]["total"] += 1
        wrong_rate_era[analytics.key_concept]["correct"] += analytics.topic_rate
        wrong_rate_era[analytics.key_concept]["time_sum"] += analytics.avg_time_sec
    wrong_rate_topic = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})
    for analytics in topic_analytics:
        wrong_rate_topic[analytics.key_concept]["total"] += 1
        wrong_rate_topic[analytics.key_concept]["correct"] += analytics.topic_rate
        wrong_rate_topic[analytics.key_concept]["time_sum"] += analytics.avg_time_sec

    return {
        "average_time_type": average_time_type,
        "average_time_era": average_time_era,
        "weak_topics_type": weak_topics_type,
        "weak_topics_era": weak_topics_era,
        "wrong_rate_type": wrong_rate_type,
        "wrong_rate_era": wrong_rate_era,
        "wrong_rate_topic": wrong_rate_topic,
    }

def calculate_average_rate(correct, total):
    return correct / total if total else 0.0

def cant_create_analytics(user_id):
    # 분석 데이터 생성 불가능 여부 확인
    user_analytics = get_user_analytics(user_id)
    if user_analytics.count() == 0:
        return True
    return False

def create_analytics(user_id):
    # 분석 데이터 생성
    user_analytics = get_user_analytics(user_id)
    for analytics in user_analytics:
        create_analytics(analytics)
    return True
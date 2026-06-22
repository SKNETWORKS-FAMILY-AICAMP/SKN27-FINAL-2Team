"""프론트엔드와 백엔드 병렬 개발을 위한 페이지별 데이터 계약."""

# 여러 페이지 계약에서 공통으로 재사용하는 요청/응답 객체 형태.
COMMON_TYPES = {
    # 대시보드, 채팅, 노트, 학습 계획에서 공통으로 사용하는 사용자 정보.
    "User": {
        "userId": "int",
        "email": "str",
        "nickname": "str",
        "studyPlans": "list[StudyPlan]",
    },
    # 사용자 학습계획 정보
    "StudyPlan": {
        "studyPlanId": "int",
        "studyPlanName": "str",
        "studyPlanDescription": "str",
        "studyPlanStartDate": "datetime",
        "studyPlanEndDate": "datetime",
        "studyPlanStatus": "str",
        "studyPlanCreatedAt": "datetime",
    },
    # 역사 용어에 연결된 카테고리 정보.
    "Category": {
        "categoryName": "str",
        "categoryCh": "str | None",
        "categoryTimes": "str",
    },
    # 정답 정보 없이 클라이언트에 전달되는 문제 정보.
    "Question": {
        "questionId": "int",
        "era": "str",
        "topic": "str",
        "questionType": "str",
        "content": "str",
        "choices": "list[Choice]",
    },
    # 객관식 문제에 표시되는 보기 정보.
    "Choice": {
        "choiceNo": "int",
        "content": "str",
    },
    # 하나의 문제 풀이 세션에 대한 최종 점수 요약.
    "QuizResult": {
        "sessionId": "int",
        "totalCount": "int",
        "correctCount": "int",
        "wrongCount": "int",
        "answerRate": "float",
        "totalScore": "int",
    },
    # 사용자의 정답률이 낮은 시대 또는 주제.
    "WeakTopic": {
        "era": "str",
        "topic": "str",
        "wrongCount": "int",
        "answerRate": "float",
    },
    # 문제 풀이 또는 분석 결과를 바탕으로 생성된 학습 추천 대상.
    "RecommendedStudyTarget": {
        "era": "str",
        "topic": "str",
        "reason": "str",
        "priority": "int",
        "recommendedQuestionCount": "int",
    },
    # 하나의 채팅 세션 안에서 주고받는 메시지.
    "ChatMessage": {
        "messageId": "int",
        "senderType": "user | assistant",
        "content": "str",
        "createdAt": "datetime",
    },
    # Neo4j 기반 용어 검색 또는 그래프 조회에서 반환되는 역사 용어.
    "HistoryTerm": {
        "termName": "str",
        "termCh": "str | None",
        "termTimes": "str",
        "categories": "list[Category]",
    },
}

# 각 페이지가 최초 로딩과 사용자 액션마다 필요한 데이터를 정의한다.
PAGE_CONTRACTS = [
    {
        # 사용자 로그인을 처리하고 이후 페이지에서 쓸 토큰과 사용자 정보를 받는다.
        "page": "loginPage",
        "route": "/login",
        "initialData": {},
        "actions": [
            {
                "name": "login",
                "send": {
                    "email": "str",
                    "password": "str",
                },
                "receive": {
                    "accessToken": "str",
                    "user": "User",
                },
                "navigateTo": "dashboardPage",
            }
        ],
    },
    {
        # 새 계정을 생성한 뒤 로그인 페이지로 이동한다.
        "page": "signupPage",
        "route": "/signup",
        "initialData": {},
        "actions": [
            {
                "name": "signup",
                "send": {
                    "email": "str",
                    "password": "str",
                    "nickname": "str",
                },
                "receive": {
                    "userId": "int",
                    "email": "str",
                    "nickname": "str",
                },
                "navigateTo": "loginPage",
            }
        ],
    },
    {
        # 로그인 후 첫 화면으로 최근 학습 현황과 주요 이동 경로를 제공한다.
        "page": "dashboardPage",
        "route": "/dashboard",
        "pageInputs": {
            "accessToken": "str",
        },
        "initialData": {
            "user": "User",
            "recentSolveSessions": "list[SolveSessionSummary]",
            "analyticsSummary": "AnalyticsSummary",
            "weakTopics": "list[WeakTopic]",
            "recommendedStudyTargets": "list[RecommendedStudyTarget]",
        },
        "actions": [
            {
                "name": "goToQuizSetup",
                "send": {},
                "receive": {},
                "navigateTo": "quizSetupPage",
            },
            {
                "name": "goToAnalytics",
                "send": {},
                "receive": {},
                "navigateTo": "analyticsPage",
            },
        ],
    },
    {
        # 문제 풀이 세션을 만들기 전에 시대, 주제, 난이도 같은 조건을 선택한다.
        "page": "quizSetupPage",
        "route": "/quiz/setup",
        "initialData": {
            "eras": "list[str]",
            "topics": "list[str]",
            "questionTypes": "list[str]",
            "difficultyLevels": "list[str]",
        },
        "actions": [
            {
                "name": "startQuiz",
                "send": {
                    "era": "str",
                    "topic": "str",
                    "questionType": "str",
                    "difficulty": "str",
                    "questionCount": "int",
                },
                "receive": {
                    "sessionId": "int",
                    "questions": "list[Question]",
                },
                # 사용자가 답안을 제출하기 전까지 정답 관련 필드는 내려주지 않는다.
                "forbiddenReceiveFields": [
                    "answerNo",
                    "isAnswer",
                    "answerExplanation",
                    "choiceExplanation",
                ],
                "navigateTo": "quizPage",
            }
        ],
    },
    {
        # 진행 중인 문제 풀이 세션에서 답안 제출과 세션 완료를 처리한다.
        "page": "quizPage",
        "route": "/quiz/{sessionId}",
        "pageInputs": {
            "sessionId": "int",
        },
        "initialData": {
            "sessionId": "int",
            "questions": "list[Question]",
            "currentQuestionIndex": "int",
        },
        "actions": [
            {
                "name": "submitAnswer",
                "send": {
                    "sessionId": "int",
                    "questionId": "int",
                    "selectedNo": "int | None",
                    "textAnswer": "str | None",
                    "timeSpentSec": "int",
                },
                "receive": {
                    "recordId": "int",
                    "questionId": "int",
                    "isCorrect": "bool",
                    "correctAnswer": "CorrectAnswer",
                    "answerExplanation": "str",
                    "choiceExplanation": "str | None",
                },
            },
            {
                "name": "completeQuiz",
                "send": {
                    "sessionId": "int",
                    "totalTimeSec": "int",
                },
                "receive": {
                    "sessionResult": "QuizResult",
                    "weakTopics": "list[WeakTopic]",
                    "reviewTargets": "list[ReviewTarget]",
                },
                "navigateTo": "quizResultPage",
            },
        ],
    },
    {
        # 완료된 풀이 결과를 보여주고 오답 노트나 학습 계획 생성을 연결한다.
        "page": "quizResultPage",
        "route": "/quiz/{sessionId}/result",
        "pageInputs": {
            "sessionId": "int",
        },
        "initialData": {
            "sessionResult": "QuizResult",
            "records": "list[SolveRecordDetail]",
            "weakTopics": "list[WeakTopic]",
            "reviewTargets": "list[ReviewTarget]",
            "recommendedStudyTargets": "list[RecommendedStudyTarget]",
        },
        "actions": [
            {
                "name": "createNoteFromWrongAnswer",
                "send": {
                    "recordId": "int",
                    "questionId": "int",
                },
                "receive": {
                    "noteId": "int",
                    "title": "str",
                },
                "navigateTo": "noteDetailPage",
            },
            {
                "name": "createStudyPlan",
                "send": {
                    "recommendedStudyTargets": "list[RecommendedStudyTarget]",
                },
                "receive": {
                    "studyPlanId": "int",
                    "studyPlans": "str",
                    "term": "str",
                },
                "navigateTo": "studyPlanPage",
            },
        ],
    },
    {
        # 기간, 시대, 주제, 점수 추이 기준으로 학습 성과를 집계한다.
        "page": "analyticsPage",
        "route": "/analytics",
        "initialData": {
            "period": "DateRange",
            "summary": "AnalyticsSummary",
            "byEra": "list[EraStat]",
            "byTopic": "list[TopicStat]",
            "weakTopics": "list[WeakTopic]",
            "scoreTrend": "list[ScoreTrend]",
            "recommendedStudyTargets": "list[RecommendedStudyTarget]",
        },
        "actions": [
            {
                "name": "changePeriod",
                "send": {
                    "fromDate": "date",
                    "toDate": "date",
                },
                "receive": {
                    "summary": "AnalyticsSummary",
                    "byEra": "list[EraStat]",
                    "byTopic": "list[TopicStat]",
                    "weakTopics": "list[WeakTopic]",
                    "scoreTrend": "list[ScoreTrend]",
                },
            }
        ],
    },
    {
        # 채팅 세션을 관리하고 사용자 메시지를 AI 응답 로직으로 보낸다.
        "page": "chatPage",
        "route": "/chat",
        "initialData": {
            "chatSessions": "list[ChatSession]",
        },
        "actions": [
            {
                "name": "createChatSession",
                "send": {
                    "title": "str",
                },
                "receive": {
                    "chatSessionId": "str",
                    "title": "str",
                    "createdAt": "datetime",
                },
            },
            {
                "name": "sendMessage",
                "send": {
                    "chatSessionId": "str",
                    "content": "str",
                },
                "receive": {
                    "userMessage": "ChatMessage",
                    "assistantMessage": "ChatMessage",
                },
            },
        ],
    },
    {
        # 사용자의 노트를 조회, 생성, 수정하며 오답 기반 노트도 포함한다.
        "page": "notePage",
        "route": "/notes",
        "initialData": {
            "notes": "list[NoteSummary]",
        },
        "actions": [
            {
                "name": "createNote",
                "send": {
                    "title": "str",
                    "content": "str",
                    "era": "str | None",
                    "topic": "str | None",
                    "difficulty": "str | None",
                    "questionType": "str | None",
                },
                "receive": {
                    "noteId": "int",
                    "title": "str",
                    "createdAt": "datetime",
                },
            },
            {
                "name": "updateNote",
                "send": {
                    "noteId": "int",
                    "title": "str",
                    "content": "str",
                },
                "receive": {
                    "noteId": "int",
                    "updatedAt": "datetime",
                },
            },
        ],
    },
    {
        # 학습 계획을 조회하거나 생성하며 분석 추천 결과를 선택적으로 활용한다.
        "page": "studyPlanPage",
        "route": "/study-plans",
        "initialData": {
            "studyPlans": "list[StudyPlan]",
        },
        "actions": [
            {
                "name": "createStudyPlan",
                "send": {
                    "studyPlans": "str",
                    "term": "str",
                    "recommendedStudyTargets": "list[RecommendedStudyTarget] | None",
                },
                "receive": {
                    "studyPlanId": "int",
                    "studyPlans": "str",
                    "term": "str",
                    "createdAt": "datetime",
                },
            }
        ],
    },
    {
        # 역사 용어를 검색하고 선택한 용어의 Neo4j 그래프 데이터를 조회한다.
        "page": "historySearchPage",
        "route": "/history",
        "initialData": {
            "categoryNames": "list[str]",
            "termTimes": "list[str]",
        },
        "actions": [
            {
                "name": "searchTerms",
                "send": {
                    "keyword": "str",
                    "categoryName": "str | None",
                    "termTimes": "str | None",
                },
                "receive": {
                    "terms": "list[HistoryTerm]",
                },
            },
            {
                "name": "openTermGraph",
                "send": {
                    "termName": "str",
                    "termCh": "str | None",
                    "termTimes": "str",
                },
                "receive": {
                    "nodes": "list[GraphNode]",
                    "relationships": "list[GraphRelationship]",
                },
                "navigateTo": "historyGraphPage",
            },
        ],
    },
]

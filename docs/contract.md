# 프론트엔드와 백엔드 병렬 개발을 위한 페이지별 데이터 계약

```python
# 여러 페이지 계약에서 공통으로 재사용하는 요청/응답 객체 형태.
COMMON_TYPES = {
    # 대시보드, 채팅, 노트, 학습 계획에서 공통으로 사용하는 사용자 정보.
    "User": {
        "userId": "int",
        "email": "str",
        "nickname": "str",
        "studyPlans": "list[StudyPlan]",
    },
    # 사용자에게 보여줄 학습계획 요약과 날짜별 상세 계획.
    "StudyPlan": {
        "studyPlanId": "int",
        "summary": "str",
        "totalDays": "int",
        "dailyAvailableMinutes": "int",
        "plans": "list[StudyPlanDay]",
        "createdAt": "datetime",
        "updatedAt": "datetime",
    },
    # 하루 단위 학습계획.
    "StudyPlanDay": {
        "date": "date",
        "blocks": "list[StudyPlanBlock]",
    },
    # 하루 계획 안에 들어가는 개별 학습 블록.
    "StudyPlanBlock": {
        "blockType": "newWeakness | review | predictionFocus",
        "classification": "시대 | 유형 | 주제",
        "label": "str",
        "activity": "str",
        "questionCount": "int",
        "estimatedMinutes": "int",
        "priorityScore": "float",
        "reason": "str",
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
    # 최근 문제 풀이 세션 요약.
    "SolveSessionSummary": {
        "sessionId": "int",
        "sessionType": "str",
        "totalCount": "int",
        "answerRate": "float",
        "totalScore": "int",
        "createdAt": "datetime",
    },
    # 풀이 결과 상세 화면에서 보여줄 문항별 풀이 기록.
    "SolveRecordDetail": {
        "recordId": "int",
        "questionId": "int",
        "content": "str",
        "selectedNo": "int | None",
        "isCorrect": "bool",
        "timeSpentSec": "int | None",
        "correctAnswer": "CorrectAnswer",
        "answerExplanation": "str",
        "choiceExplanation": "str | None",
    },
    # 답안 제출 후 내려주는 정답 정보.
    "CorrectAnswer": {
        "answerNo": "int | None",
        "answerText": "str | None",
    },
    # 오답 복습 대상으로 넘기는 문항 요약.
    "ReviewTarget": {
        "questionId": "int",
        "content": "str",
        "userAnswer": "str | None",
        "correctAnswer": "str",
        "explanation": "str",
    },
    # 기간 필터.
    "DateRange": {
        "fromDate": "date",
        "toDate": "date",
    },
    # 마이페이지 상단 분석 요약.
    "AnalyticsSummary": {
        "totalSolveCount": "int",
        "averageScore": "float",
        "averageAnswerRate": "float",
        "averageTimeSec": "int | None",
    },
    # 시대별 분석 결과.
    "EraStat": {
        "era": "str",
        "totalCount": "int",
        "answerRate": "float",
        "wrongRate": "float",
        "averageTimeSec": "int | None",
    },
    # 유형별 분석 결과.
    "TypeStat": {
        "questionType": "str",
        "totalCount": "int",
        "answerRate": "float",
        "wrongRate": "float",
        "averageTimeSec": "int | None",
    },
    # 주제별 분석 결과.
    "TopicStat": {
        "topic": "str",
        "totalCount": "int",
        "answerRate": "float",
        "wrongRate": "float",
        "averageTimeSec": "int | None",
    },
    # 날짜별 점수 변화.
    "ScoreTrend": {
        "date": "date",
        "averageScore": "float",
        "averageAnswerRate": "float",
    },
    # 사용자의 정답률이 낮은 시대 또는 주제.
    "WeakTopic": {
        "era": "str",
        "topic": "str",
        "wrongCount": "int",
        "answerRate": "float",
    },
    # 퀴즈 결과에서 바로 학습계획을 만들 때 쓰는 간단 추천 대상.
    "RecommendedStudyTarget": {
        "era": "str",
        "topic": "str",
        "reason": "str",
        "priority": "int",
        "recommendedQuestionCount": "int",
    },
    # 사용자의 풀이 결과에서 계산된 취약 학습 대상.
    "WeakTarget": {
        "classification": "시대 | 유형 | 주제",
        "label": "str",
        "wrongRate": "float",
        "averageTimeSec": "int",
    },
    # 기출 기반 출제 예상 로직에서 전달하는 우선 학습 대상.
    "PredictedTarget": {
        "classification": "시대 | 유형 | 주제",
        "label": "str",
        "predictionScore": "float",
        "reason": "str",
    },
    # 하나의 채팅 세션 안에서 주고받는 메시지.
    "ChatMessage": {
        "messageId": "int",
        "senderType": "user | assistant",
        "content": "str",
        "createdAt": "datetime",
    },
    # 채팅 목록에 표시할 채팅 세션 요약.
    "ChatSession": {
        "chatSessionId": "str",
        "title": "str",
        "createdAt": "datetime",
    },
    # question 앱에서 제공하는 노트 목록 요약.
    "NoteSummary": {
        "noteId": "int",
        "title": "str",
        "era": "str | None",
        "topic": "str | None",
        "createdAt": "datetime",
        "updatedAt": "datetime",
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
                "navigateTo": "myPage",
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
        # 로그인 후 첫 화면으로 사용자 정보, 분석, 학습 계획을 함께 제공한다.
        # notes 데이터는 question 앱에서 별도로 제공한다.
        "page": "myPage",
        "route": "/mypage",
        "pageInputs": {
            "accessToken": "str",
        },
        "initialData": {
            "user": "User",
            "recentSolveSessions": "list[SolveSessionSummary]",
            "analyticsPeriod": "DateRange",
            "analyticsSummary": "AnalyticsSummary",
            "analyticsByEra": "list[EraStat]",
            "analyticsByType": "list[TypeStat]",
            "analyticsByTopic": "list[TopicStat]",
            "analyticsScoreTrend": "list[ScoreTrend]",
            "weakTargets": "list[WeakTarget]",
            "recommendedStudyTargets": "list[RecommendedStudyTarget]",
            "studyPlans": "list[StudyPlan]",
        },
        "actions": [
            {
                "name": "goToQuizSetup",
                "send": {},
                "receive": {},
                "navigateTo": "quizSetupPage",
            },
            {
                "name": "changeAnalyticsPeriod",
                "send": {
                    "fromDate": "date",
                    "toDate": "date",
                },
                "receive": {
                    "analyticsPeriod": "DateRange",
                    "analyticsSummary": "AnalyticsSummary",
                    "analyticsByEra": "list[EraStat]",
                    "analyticsByType": "list[TypeStat]",
                    "analyticsByTopic": "list[TopicStat]",
                    "analyticsScoreTrend": "list[ScoreTrend]",
                    "weakTargets": "list[WeakTarget]",
                    "recommendedStudyTargets": "list[RecommendedStudyTarget]",
                },
            },
            {
                "name": "goToNote",
                "send": {},
                "receive": {},
                "navigateTo": "notePage",
            },
            {
                "name": "goToStudyPlan",
                "send": {},
                "receive": {},
                "navigateTo": "studyPlanPage",
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
                # 방금 푼 결과의 추천 대상만 사용해 빠르게 학습계획을 만드는 간단 생성 액션.
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
        # 학습계획 설계 로직에 따라 날짜별 학습 블록을 조회하거나 생성한다.
        "page": "studyPlanPage",
        "route": "/study-plans",
        "initialData": {
            "studyPlans": "list[StudyPlan]",
        },
        "actions": [
            {
                "name": "createStudyPlan",
                "send": {
                    "dailyAvailableHours": "float",
                    "remainingDays": "int",
                    "weakTargets": "list[WeakTarget]",
                    "predictedTargets": "list[PredictedTarget] | None",
                },
                "receive": {
                    "studyPlan": "StudyPlan",
                },
            }
        ],
    },
]
```

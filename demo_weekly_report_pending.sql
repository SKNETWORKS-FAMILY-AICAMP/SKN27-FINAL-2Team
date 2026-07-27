-- 데모 주간 리포트 주입 (pending) — 워커가 실제 AI 문구를 생성한다
-- 사용법: :email 자리에 로그인 계정 이메일을 넣고 실행한다.
\set demo_email 'admin@gmail.com'

UPDATE study_plan_mypage
   SET weekly_report_data = $demo${
  "schemaVersion": "1",
  "status": "pending",
  "reportType": "weekly",
  "sourceSessionId": 9001,
  "result": {
    "snapshotAt": "2026-07-27T03:00:00Z",
    "recoveredSnapshot": false,
    "assessment": {
      "sessionId": 9001,
      "score": 74,
      "totalScore": 100,
      "questionCount": 50,
      "evidenceId": "assessment-current"
    },
    "comparison": {
      "status": "AVAILABLE",
      "baselineType": "weekly_review",
      "baselineSessionId": 8800,
      "previousScore": 62.0,
      "scoreChange": 12.0,
      "evidenceId": "comparison-baseline"
    },
    "planProgress": {
      "targetCount": 24,
      "achievedCount": 18,
      "completionRate": 0.75,
      "completionPercent": 75,
      "evidenceId": "plan-progress"
    },
    "strengths": [
      {
        "evidenceId": "strength-1",
        "groupKeyId": "era=%EA%B0%9C%ED%95%AD%EA%B8%B0|topic=%EC%99%B8%EA%B5%90",
        "label": "개항기 · 외교",
        "sampleCount": 13,
        "effectiveTotal": 8.6,
        "trend": "improving",
        "trendDelta": -0.21,
        "recentWeaknessScore": 0.19,
        "previousWeaknessScore": 0.4
      }
    ],
    "priorityImprovements": [
      {
        "evidenceId": "priority-1",
        "groupKeyId": "era=%EC%A1%B0%EC%84%A0|topic=%EC%A0%95%EC%B9%98",
        "label": "조선 · 정치",
        "sampleCount": 14,
        "wrongCount": 10,
        "wrongRate": 0.7143,
        "wrongPercent": 71,
        "effectiveTotal": 9.4,
        "weaknessScore": 0.71,
        "trend": "worsening",
        "trendDelta": 0.18,
        "recentWeaknessScore": 0.71,
        "previousWeaknessScore": 0.53,
        "repeatedError": 0.67
      },
      {
        "evidenceId": "priority-2",
        "groupKeyId": "era=%EC%9D%BC%EC%A0%9C%EA%B0%95%EC%A0%90%EA%B8%B0|topic=%EC%82%AC%EA%B1%B4",
        "label": "일제강점기 · 사건",
        "sampleCount": 12,
        "wrongCount": 8,
        "wrongRate": 0.6667,
        "wrongPercent": 67,
        "effectiveTotal": 8.1,
        "weaknessScore": 0.64,
        "trend": "flat",
        "trendDelta": 0.02,
        "recentWeaknessScore": 0.64,
        "previousWeaknessScore": 0.62,
        "repeatedError": 0.33,
        "examTrendRank": 2,
        "examQuestionSharePercent": 7.2
      },
      {
        "evidenceId": "priority-3",
        "groupKeyId": "era=%EA%B3%A0%EB%A0%A4|topic=%EC%A0%9C%EB%8F%84",
        "label": "고려 · 제도",
        "sampleCount": 11,
        "wrongCount": 7,
        "wrongRate": 0.6364,
        "wrongPercent": 64,
        "effectiveTotal": 7.2,
        "weaknessScore": 0.58,
        "trend": "worsening",
        "trendDelta": 0.11,
        "recentWeaknessScore": 0.58,
        "previousWeaknessScore": 0.47,
        "repeatedError": 0.33
      }
    ],
    "conceptWeaknesses": [
      {
        "evidenceId": "concept-1",
        "groupKeyId": "coreConcept=%EB%B6%95%EB%8B%B9%20%EC%A0%95%EC%B9%98",
        "label": "붕당 정치",
        "sampleCount": 9,
        "wrongCount": 7,
        "wrongRate": 0.7778,
        "wrongPercent": 78,
        "effectiveTotal": 6.1,
        "weaknessScore": 0.74,
        "trend": "worsening",
        "trendDelta": 0.16
      },
      {
        "evidenceId": "concept-2",
        "groupKeyId": "coreConcept=3%C2%B71%20%EC%9A%B4%EB%8F%99",
        "label": "3·1 운동",
        "sampleCount": 8,
        "wrongCount": 5,
        "wrongRate": 0.625,
        "wrongPercent": 62,
        "effectiveTotal": 5.3,
        "weaknessScore": 0.61,
        "trend": "flat",
        "trendDelta": 0.03
      },
      {
        "evidenceId": "concept-3",
        "groupKeyId": "coreConcept=%EC%A0%84%EC%8B%9C%EA%B3%BC",
        "label": "전시과",
        "sampleCount": 7,
        "wrongCount": 4,
        "wrongRate": 0.5714,
        "wrongPercent": 57,
        "effectiveTotal": 4.6,
        "weaknessScore": 0.55,
        "trend": "unknown",
        "trendDelta": 0.0
      }
    ],
    "examTrends": [
      {
        "evidenceId": "trend-1",
        "groupKeyId": "era=%EA%B0%9C%ED%95%AD%EA%B8%B0|topic=%EC%82%AC%EA%B1%B4",
        "label": "개항기 + 사건",
        "rank": 1,
        "ratioPercent": 8.0,
        "questionCount": 20,
        "recentRounds": "73~77"
      },
      {
        "evidenceId": "trend-2",
        "groupKeyId": "era=%EC%9D%BC%EC%A0%9C%EA%B0%95%EC%A0%90%EA%B8%B0|topic=%EC%82%AC%EA%B1%B4",
        "label": "일제 강점기 + 사건",
        "rank": 2,
        "ratioPercent": 7.2,
        "questionCount": 18,
        "recentRounds": "73~77"
      },
      {
        "evidenceId": "trend-3",
        "groupKeyId": "era=%EC%9D%BC%EC%A0%9C%EA%B0%95%EC%A0%90%EA%B8%B0|topic=%EC%A0%95%EC%B9%98",
        "label": "일제 강점기 + 정치",
        "rank": 3,
        "ratioPercent": 6.0,
        "questionCount": 15,
        "recentRounds": "73~77"
      },
      {
        "evidenceId": "trend-4",
        "groupKeyId": "era=%ED%98%84%EB%8C%80|topic=%EC%82%AC%EA%B1%B4",
        "label": "현대 + 사건",
        "rank": 4,
        "ratioPercent": 5.6,
        "questionCount": 14,
        "recentRounds": "73~77"
      },
      {
        "evidenceId": "trend-5",
        "groupKeyId": "era=%EC%A1%B0%EC%84%A0|topic=%EC%9D%B8%EB%AC%BC",
        "label": "조선 + 인물",
        "rank": 5,
        "ratioPercent": 5.2,
        "questionCount": 13,
        "recentRounds": "73~77"
      }
    ],
    "timeSummary": [
      {
        "qType": "사료형",
        "label": "사료형",
        "sampleCount": 20,
        "userMedianSeconds": 96.0,
        "referenceMedianSeconds": 71.0,
        "timeRatio": 1.3521,
        "evidenceId": "time-1"
      }
    ],
    "confusionPatterns": [],
    "nextPlanTargets": [
      {
        "evidenceId": "target-1",
        "groupKeyId": "era=%EC%A1%B0%EC%84%A0|topic=%EC%A0%95%EC%B9%98",
        "label": "조선 · 정치",
        "priorityScore": 0.84
      },
      {
        "evidenceId": "target-2",
        "groupKeyId": "era=%EC%9D%BC%EC%A0%9C%EA%B0%95%EC%A0%90%EA%B8%B0|topic=%EC%82%AC%EA%B1%B4",
        "label": "일제강점기 · 사건",
        "priorityScore": 0.79
      },
      {
        "evidenceId": "target-3",
        "groupKeyId": "era=%EA%B3%A0%EB%A0%A4|topic=%EC%A0%9C%EB%8F%84",
        "label": "고려 · 제도",
        "priorityScore": 0.62
      }
    ]
  },
  "content": {
    "comment": null,
    "tips": [],
    "fallbackUsed": false,
    "validation": null
  },
  "worker": {
    "attemptCount": 0,
    "availableAt": "2026-01-01T00:00:00Z",
    "startedAt": null,
    "lastError": null
  },
  "nextPlan": {
    "status": "pending",
    "studyPlanId": null,
    "blockedReason": null
  },
  "version": "weekly-report-v3-langgraph",
  "model": "configured-model",
  "createdAt": "2026-07-27T03:00:00Z"
}$demo$::jsonb,
       modified_at = NOW()
 WHERE studyplan_id = (
        SELECT p.studyplan_id
          FROM study_plan_mypage p
          JOIN user_accounts u ON u.user_id = p.user_id
         WHERE u.email = :'demo_email'
           AND p.status = 'active'
         ORDER BY p.plan_version DESC, p.studyplan_id DESC
         LIMIT 1
       );

SELECT p.studyplan_id,
       p.weekly_report_data->>'status' AS report_status,
       p.weekly_report_data->'result'->'assessment'->>'score' AS score
  FROM study_plan_mypage p
  JOIN user_accounts u ON u.user_id = p.user_id
 WHERE u.email = :'demo_email'
   AND p.status = 'active';

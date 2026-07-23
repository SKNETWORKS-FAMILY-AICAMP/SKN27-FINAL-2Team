from __future__ import annotations

from dataclasses import replace
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from analytics.service.weekly_report.relation_evidence import (
    ResolvedChoiceRelation,
    ResolvedRelationFact,
    WrongChoiceCandidate,
    build_confusion_patterns,
    build_weekly_confusion_patterns,
    build_wrong_choice_candidates,
)


class FakeChoiceRelationResolver:
    def __init__(
        self,
        resolutions: dict[int, ResolvedChoiceRelation | None],
    ) -> None:
        self._resolutions = resolutions
        self.candidates: list[WrongChoiceCandidate] = []

    def resolve(
        self,
        candidate: WrongChoiceCandidate,
    ) -> ResolvedChoiceRelation | None:
        self.candidates.append(candidate)
        return self._resolutions.get(candidate.record_id)


class WeeklyReportWrongChoiceCandidateTests(TestCase):
    def test_builds_selected_and_correct_choice_context_without_entity_columns(self) -> None:
        candidates = build_wrong_choice_candidates(
            record_rows=[
                {
                    "record_id": 1,
                    "session_id": 10,
                    "question_id": 100,
                    "selected_no": 3,
                    "is_correct": False,
                }
            ],
            question_rows=[
                {
                    "question_id": 100,
                    "answer_no": 1,
                    "content": "왕과 정책의 연결로 옳은 것은?",
                    "passage": None,
                    "image_caption": None,
                    "answer_explanation": "광해군 때 대동법이 시행되었다.",
                    "core_concept": "조선의 수취 제도",
                    "era": "조선",
                    "topic": "정치",
                    "question_type": "개념",
                    "question_subtype": "인물-정책",
                }
            ],
            option_rows=[
                {
                    "question_id": 100,
                    "choice_no": 1,
                    "content": "광해군 때 대동법을 시행하였다.",
                    "choice_explanation": "정답",
                },
                {
                    "question_id": 100,
                    "choice_no": 3,
                    "content": "영조 때 균역법을 시행하였다.",
                    "choice_explanation": "다른 왕의 정책",
                },
            ],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.correct_choice_no, 1)
        self.assertEqual(candidate.selected_choice_no, 3)
        self.assertIn("대동법", candidate.correct_choice_text)
        self.assertIn("균역법", candidate.selected_choice_text)
        self.assertEqual(candidate.core_concept, "조선의 수취 제도")

    def test_skips_unanswered_correct_and_missing_choice_rows(self) -> None:
        candidates = build_wrong_choice_candidates(
            record_rows=[
                {
                    "record_id": 1,
                    "session_id": 10,
                    "question_id": 100,
                    "selected_no": None,
                    "is_correct": False,
                },
                {
                    "record_id": 2,
                    "session_id": 10,
                    "question_id": 100,
                    "selected_no": 1,
                    "is_correct": True,
                },
                {
                    "record_id": 3,
                    "session_id": 10,
                    "question_id": 100,
                    "selected_no": 5,
                    "is_correct": False,
                },
            ],
            question_rows=[{"question_id": 100, "answer_no": 1}],
            option_rows=[
                {"question_id": 100, "choice_no": 1, "content": "정답"}
            ],
        )

        self.assertEqual(candidates, [])


class WeeklyReportConfusionPatternTests(TestCase):
    @patch(
        "analytics.service.weekly_report.relation_evidence."
        "load_wrong_choice_candidates"
    )
    def test_disabled_resolver_does_not_query_wrong_choice_records(
        self,
        load_candidates,
    ) -> None:
        patterns = build_weekly_confusion_patterns(
            user_id=1,
            period_start=date(2026, 7, 13),
            period_end=date(2026, 7, 19),
            resolver=None,
        )

        self.assertEqual(patterns, [])
        load_candidates.assert_not_called()

    def test_groups_repeated_resolved_relations_and_preserves_graph_evidence(self) -> None:
        relation = self._person_policy_relation()
        candidates = [self._candidate(1, 100), self._candidate(2, 101)]
        resolver = FakeChoiceRelationResolver({1: relation, 2: relation})

        patterns = build_confusion_patterns(candidates, resolver)

        self.assertEqual(len(patterns), 1)
        pattern = patterns[0]
        self.assertEqual(pattern["evidenceId"], "confusion-1")
        self.assertEqual(pattern["repeatCount"], 2)
        self.assertEqual(pattern["sourceQuestionIds"], [100, 101])
        self.assertEqual(pattern["correctFact"]["subjectLabel"], "광해군")
        self.assertEqual(pattern["correctFact"]["objectLabel"], "대동법")
        self.assertEqual(pattern["selectedFact"]["subjectLabel"], "영조")
        self.assertEqual(pattern["selectedFact"]["objectLabel"], "균역법")
        self.assertEqual(pattern["graphEvidenceIds"], ["evidence-1", "evidence-2"])

    def test_does_not_call_a_single_or_ambiguous_match_repeated(self) -> None:
        relation = self._person_policy_relation()
        candidates = [self._candidate(1, 100), self._candidate(2, 101)]
        resolver = FakeChoiceRelationResolver({1: relation, 2: None})

        patterns = build_confusion_patterns(candidates, resolver)

        self.assertEqual(patterns, [])

    def test_rejects_relation_without_graph_evidence(self) -> None:
        relation = replace(
            self._person_policy_relation(),
            graph_evidence_ids=(),
        )
        candidates = [self._candidate(1, 100), self._candidate(2, 101)]
        resolver = FakeChoiceRelationResolver({1: relation, 2: relation})

        patterns = build_confusion_patterns(candidates, resolver)

        self.assertEqual(patterns, [])

    @staticmethod
    def _candidate(record_id: int, question_id: int) -> WrongChoiceCandidate:
        return WrongChoiceCandidate(
            record_id=record_id,
            session_id=10,
            question_id=question_id,
            selected_choice_no=3,
            correct_choice_no=1,
            selected_choice_text="영조 때 균역법을 시행하였다.",
            correct_choice_text="광해군 때 대동법을 시행하였다.",
            selected_choice_explanation=None,
            correct_choice_explanation=None,
            question_text="왕과 정책의 연결로 옳은 것은?",
            passage=None,
            image_caption=None,
            answer_explanation="광해군 때 대동법이 시행되었다.",
            core_concept="조선의 수취 제도",
            era="조선",
            topic="정치",
            question_type="개념",
            question_subtype="인물-정책",
        )

    @staticmethod
    def _person_policy_relation() -> ResolvedChoiceRelation:
        return ResolvedChoiceRelation(
            question_intent="person-policy",
            relation_family="person-policy",
            correct_fact=ResolvedRelationFact(
                subject_id="person-gwanghae",
                subject_label="광해군",
                relation_type="IMPLEMENTED_POLICY",
                relation_label="시행",
                object_id="policy-daedong",
                object_label="대동법",
            ),
            selected_fact=ResolvedRelationFact(
                subject_id="person-yeongjo",
                subject_label="영조",
                relation_type="IMPLEMENTED_POLICY",
                relation_label="시행",
                object_id="policy-gyunyeok",
                object_label="균역법",
            ),
            comparison_dimensions=("시행 왕", "세금 대상", "시행 목적"),
            graph_evidence_ids=("evidence-2", "evidence-1"),
            graph_version="future-graph-version",
        )

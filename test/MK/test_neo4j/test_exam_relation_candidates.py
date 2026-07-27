import sys
import unittest
from pathlib import Path

import pandas as pd


class ExamRelationCandidatesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from choice_relation.deterministic_candidates import (
            build_exam_relation_candidate_tables,
            collect_predicate_families,
            load_exam_relation_candidate_policy,
        )

        cls.build_tables = staticmethod(
            build_exam_relation_candidate_tables
        )
        cls.collect_predicate_families = staticmethod(
            collect_predicate_families
        )
        cls.policy = load_exam_relation_candidate_policy(
            str(
                neo4j_root
                / "config"
                / "exam_relation_candidates.json"
            )
        )

    def make_problem(
        self,
        problem_id: str,
        question_task: str,
    ) -> dict:
        choices = [
            {
                "is_answer": True,
                "content": "을사사화가 발생하였다.",
            },
            {
                "is_answer": False,
                "content": "삼정이정청이 설치되었다.",
            },
            {
                "is_answer": False,
                "content": "최제우가 동학을 창시하였다.",
            },
            {
                "is_answer": False,
                "content": "이양선이 통상을 요구하였다.",
            },
            {
                "is_answer": False,
                "content": "홍경래가 난을 일으켰다.",
            },
        ]
        return {
            "problem_id": problem_id,
            "data_source": "han_cj_v41",
            "question_task": question_task,
            "material": "안동 김씨가 비변사를 중심으로 권력을 장악하였다.",
            "question": "이 시기에 있었던 사실로 옳지 않은 것은?",
            "topic": "세도 정치",
            "choices": choices,
        }

    def build_inputs(
        self,
    ) -> tuple[list[dict], pd.DataFrame, pd.DataFrame]:
        cases = pd.DataFrame(
            [
                {
                    "resolution_case_id": "CASE-EULSA",
                    "canonical_term": "을사사화",
                    "category": "사건",
                    "entity_type_proposal": "Event",
                },
                {
                    "resolution_case_id": "CASE-OFFICE",
                    "canonical_term": "삼정이정청",
                    "category": "기관",
                    "entity_type_proposal": "Institution",
                },
                {
                    "resolution_case_id": "CASE-CHOE",
                    "canonical_term": "최제우",
                    "category": "인물",
                    "entity_type_proposal": "Person",
                },
                {
                    "resolution_case_id": "CASE-DONGHAK",
                    "canonical_term": "동학",
                    "category": "사상",
                    "entity_type_proposal": "Concept",
                },
            ]
        )
        assignments = pd.DataFrame(
            [
                {
                    "problem_id": "NEGATIVE-1",
                    "resolution_case_id": "CASE-EULSA",
                    "link_status": "ACCEPTED",
                    "canonical_ids_json": '["CAN-EULSA"]',
                },
                {
                    "problem_id": "NEGATIVE-1",
                    "resolution_case_id": "CASE-OFFICE",
                    "link_status": "ACCEPTED",
                    "canonical_ids_json": '["CAN-OFFICE"]',
                },
                {
                    "problem_id": "NEGATIVE-1",
                    "resolution_case_id": "CASE-CHOE",
                    "link_status": "ACCEPTED",
                    "canonical_ids_json": '["CAN-CHOE"]',
                },
                {
                    "problem_id": "NEGATIVE-1",
                    "resolution_case_id": "CASE-DONGHAK",
                    "link_status": "ACCEPTED",
                    "canonical_ids_json": '["CAN-DONGHAK"]',
                },
            ]
        )
        problems = [self.make_problem("NEGATIVE-1", "negative_select")]
        return problems, cases, assignments

    def test_negative_answer_is_preserved_as_blocked_template(self):
        problems, cases, assignments = self.build_inputs()
        tables, statistics = self.build_tables(
            problems,
            cases,
            assignments,
            self.policy,
        )
        claims = tables["segment_claims"]
        answer = claims[
            claims["segment_type"].eq("CHOICE")
            & claims["is_answer_key"].eq(True)
        ].iloc[0]
        candidates = tables["relation_candidates"]
        answer_candidates = candidates[
            candidates["claim_segment_id"].eq(
                answer["claim_segment_id"]
            )
        ]

        self.assertEqual(
            answer["contextual_truth_status"],
            "CONTEXTUALLY_FALSE",
        )
        self.assertEqual(
            answer["claim_role"],
            "TARGET_MISALIGNED_RELATION_TEMPLATE",
        )
        self.assertFalse(answer_candidates.empty)
        self.assertEqual(
            set(answer_candidates["candidate_status"]),
            {"BLOCKED_FALSE_CONTEXT"},
        )
        self.assertTrue(
            answer_candidates["must_not_project_as_fact"].all()
        )
        self.assertEqual(
            statistics["negative_answer_template_count"],
            1,
        )
        self.assertEqual(
            statistics["negative_answer_candidate_segment_count"],
            1,
        )

    def test_non_answer_choices_of_negative_question_are_true_candidates(
        self,
    ):
        problems, cases, assignments = self.build_inputs()
        tables, _ = self.build_tables(
            problems,
            cases,
            assignments,
            self.policy,
        )
        claims = tables["segment_claims"]
        non_answers = claims[
            claims["segment_type"].eq("CHOICE")
            & claims["is_answer_key"].eq(False)
        ]
        candidates = tables["relation_candidates"]
        reviewable_true_candidate_ids = set(
            candidates[
                candidates["candidate_status"].isin(
                    [
                        "NEEDS_OFFICIAL_CORROBORATION",
                        "TARGET_RESOLUTION_REQUIRED",
                    ]
                )
            ]["claim_segment_id"]
        )

        self.assertEqual(
            set(non_answers["contextual_truth_status"]),
            {"CONTEXTUALLY_TRUE"},
        )
        self.assertIn(
            claims[
                claims["text"].eq("삼정이정청이 설치되었다.")
            ].iloc[0]["claim_segment_id"],
            reviewable_true_candidate_ids,
        )
        office_candidate = candidates[
            candidates["evidence_text"].eq(
                "삼정이정청이 설치되었다."
            )
        ].iloc[0]
        self.assertEqual(
            office_candidate["candidate_status"],
            "TARGET_RESOLUTION_REQUIRED",
        )
        self.assertEqual(
            office_candidate["start_canonical_id"],
            "CAN-OFFICE",
        )
        self.assertEqual(
            office_candidate["end_canonical_id"],
            "",
        )
        donghak_candidate = candidates[
            candidates["evidence_text"].eq(
                "최제우가 동학을 창시하였다."
            )
        ].iloc[0]
        self.assertEqual(
            donghak_candidate["candidate_kind"],
            "TRIGGERED_ENTITY_PAIR_CANDIDATE",
        )

    def test_positive_question_reverses_answer_truth(self):
        problems, cases, assignments = self.build_inputs()
        problems[0]["question_task"] = "standard_select"
        tables, _ = self.build_tables(
            problems,
            cases,
            assignments,
            self.policy,
        )
        choices = tables["segment_claims"][
            tables["segment_claims"]["segment_type"].eq("CHOICE")
        ]
        answer = choices[choices["is_answer_key"].eq(True)].iloc[0]
        non_answers = choices[choices["is_answer_key"].eq(False)]

        self.assertEqual(
            answer["contextual_truth_status"],
            "CONTEXTUALLY_TRUE",
        )
        self.assertEqual(
            set(non_answers["contextual_truth_status"]),
            {"CONTEXTUALLY_FALSE"},
        )

    def test_predicate_inside_entity_name_is_not_triggered(self):
        families = self.collect_predicate_families(
            "환곡의 폐단을 시정하기 위해 사창제를 시행하였다.",
            self.policy,
        )

        self.assertEqual(
            families,
            ["IMPLEMENT_OR_ENACT"],
        )


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path

import pandas as pd


class ProblemReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))
        sys.path.insert(0, str(neo4j_root / "terms"))

        from common import load_pipeline_policy
        from entity_resolution.problem_review import (
            build_problem_review_inputs,
            collect_verified_entity_anchor_terms,
            resolve_problem_tasks_by_context,
            validate_problem_decisions,
        )

        cls.build_inputs = staticmethod(build_problem_review_inputs)
        cls.collect_anchor_terms = staticmethod(
            collect_verified_entity_anchor_terms
        )
        cls.resolve_by_context = staticmethod(
            resolve_problem_tasks_by_context
        )
        cls.validate_decisions = staticmethod(validate_problem_decisions)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def build_fixture(self, alternative_count: int = 2):
        resolution_tables = {
            "resolution_cases": pd.DataFrame(
                [
                    {
                        "resolution_case_id": "case-gojong",
                        "canonical_term": "고종",
                        "category": "인물",
                    }
                ]
            ),
            "problem_contexts": pd.DataFrame(
                [
                    {
                        "problem_id": "question-1",
                        "full_text": "강화도 조약을 체결한 고종에 대한 문항",
                    },
                    {
                        "problem_id": "question-2",
                        "full_text": "몽골 침입기 고종에 대한 문항",
                    },
                ]
            ),
            "problem_resolution_assignments": pd.DataFrame(
                [
                    {
                        "problem_assignment_id": "assignment-1",
                        "problem_id": "question-1",
                        "resolution_case_id": "case-gojong",
                    },
                    {
                        "problem_assignment_id": "assignment-2",
                        "problem_id": "question-2",
                        "resolution_case_id": "case-gojong",
                    },
                ]
            ),
        }
        alternatives = [
            {
                "canonical_alternative_id": "alternative-joseon",
                "resolution_case_id": "case-gojong",
                "display_name_proposal": "고종(조선)",
                "entity_type_proposal": "Person",
                "identity_member_source_ids_json": json.dumps(
                    ["AKS:ARTICLE:JOSEON:release"],
                    ensure_ascii=False,
                ),
                "decision_reason": "조선의 고종",
                "verification_status": "VERIFIED",
            },
            {
                "canonical_alternative_id": "alternative-goryeo",
                "resolution_case_id": "case-gojong",
                "display_name_proposal": "고종(고려)",
                "entity_type_proposal": "Person",
                "identity_member_source_ids_json": json.dumps(
                    ["AKS:ARTICLE:GORYEO:release"],
                    ensure_ascii=False,
                ),
                "decision_reason": "고려의 고종",
                "verification_status": "VERIFIED",
            },
        ][:alternative_count]
        term_tables = {
            "term_resolution_decisions": pd.DataFrame(
                [
                    {
                        "resolution_case_id": "case-gojong",
                        "verification_status": "VERIFIED",
                    }
                ]
            ),
            "reviewed_canonical_alternatives": pd.DataFrame(alternatives),
        }
        return resolution_tables, term_tables

    def make_decision(self, task: dict) -> dict:
        return {
            "problem_review_task_id": task["problem_review_task_id"],
            "problem_assignment_id": task["problem_assignment_id"],
            "resolution_case_id": task["resolution_case_id"],
            "decision_status": "PROPOSED",
            "review_model": self.policy["entity_resolution"][
                "semantic_review"
            ]["problem_model"]["model"],
            "prompt_version": self.policy["entity_resolution"][
                "semantic_review"
            ]["problem_prompt_version"],
            "selection_mode": "SINGLE",
            "selected_canonical_alternative_ids": [
                "alternative-joseon"
            ],
            "reason": "강화도 조약 문맥은 조선 고종이다.",
        }

    def test_multiple_alternatives_create_problem_level_tasks(self):
        resolution_tables, term_tables = self.build_fixture()

        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )

        self.assertEqual(len(tasks), 2)
        self.assertTrue(deterministic.empty)
        self.assertEqual(len(tasks[0]["canonical_alternatives"]), 2)
        self.assertTrue(tasks[0]["problem_full_text"])

    def test_single_verified_alternative_is_deterministically_assigned(self):
        resolution_tables, term_tables = self.build_fixture(
            alternative_count=1
        )

        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )

        self.assertFalse(tasks)
        self.assertEqual(len(deterministic), 2)
        self.assertEqual(set(deterministic["selection_mode"]), {"SINGLE"})
        self.assertEqual(
            set(deterministic["resolution_method"]),
            {"structured_rule"},
        )

    def test_valid_problem_selection_is_verified(self):
        resolution_tables, term_tables = self.build_fixture()
        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )
        decision = self.make_decision(tasks[0])

        outputs = self.validate_decisions(
            [decision],
            tasks,
            deterministic,
            self.policy,
        )

        self.assertEqual(
            outputs["problem_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "VERIFIED",
        )
        self.assertEqual(len(outputs["verified_problem_assignments"]), 1)

    def test_exclusive_era_signal_resolves_without_llm(self):
        resolution_tables, term_tables = self.build_fixture()
        tasks, _ = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )
        tasks[0]["problem_full_text"] += " 조선 후기"
        for alternative in tasks[0]["canonical_alternatives"]:
            era_token = "고려 후기"
            if alternative["canonical_alternative_id"] == (
                "alternative-joseon"
            ):
                era_token = "조선 후기"
            alternative["context_features"] = {
                "era_tokens": [era_token],
                "aliases": [],
                "hanja": [],
                "years": [],
            }

        remaining, assignments, audit = self.resolve_by_context(
            tasks,
            self.policy,
        )

        self.assertEqual(len(remaining), 1)
        self.assertEqual(len(assignments), 2)
        verified_assignment = assignments[
            assignments["verification_status"] == "VERIFIED"
        ].iloc[0]
        self.assertEqual(
            json.loads(
                verified_assignment[
                    "selected_canonical_alternative_ids_json"
                ]
            ),
            ["alternative-joseon"],
        )
        self.assertEqual(
            set(audit["resolution_status"]),
            {"RESOLVED", "DEFERRED"},
        )

    def test_verified_entity_in_official_definition_resolves_without_llm(
        self,
    ):
        resolution_tables, term_tables = self.build_fixture()
        resolution_tables["resolution_cases"] = pd.concat(
            [
                resolution_tables["resolution_cases"],
                pd.DataFrame(
                    [
                        {
                            "resolution_case_id": "case-daehan-empire",
                            "canonical_term": "대한제국",
                            "category": "국가",
                            "entity_type_proposal": "Polity",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        resolution_tables["source_record_candidates"] = pd.DataFrame(
            [
                {
                    "source_candidate_id": "candidate-joseon",
                    "source": "AKS",
                    "source_metadata_json": json.dumps(
                        {"definition": "대한제국의 제26대 국왕이자 황제."},
                        ensure_ascii=False,
                    ),
                },
                {
                    "source_candidate_id": "candidate-goryeo",
                    "source": "AKS",
                    "source_metadata_json": json.dumps(
                        {"definition": "고려의 제23대 국왕."},
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        alternatives = term_tables["reviewed_canonical_alternatives"]
        alternatives.loc[
            alternatives["canonical_alternative_id"]
            == "alternative-joseon",
            "source_candidate_ids_json",
        ] = json.dumps(["candidate-joseon"])
        alternatives.loc[
            alternatives["canonical_alternative_id"]
            == "alternative-goryeo",
            "source_candidate_ids_json",
        ] = json.dumps(["candidate-goryeo"])
        term_tables["reviewed_canonical_alternatives"] = pd.concat(
            [
                alternatives,
                pd.DataFrame(
                    [
                        {
                            "canonical_alternative_id": (
                                "alternative-daehan-empire"
                            ),
                            "resolution_case_id": "case-daehan-empire",
                            "display_name_proposal": "대한제국",
                            "entity_type_proposal": "Polity",
                            "identity_member_source_ids_json": "[]",
                            "source_candidate_ids_json": "[]",
                            "decision_reason": "검증된 단일 대안",
                            "verification_status": "VERIFIED",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        resolution_tables["problem_contexts"].loc[
            resolution_tables["problem_contexts"]["problem_id"]
            == "question-1",
            "full_text",
        ] = "대한제국의 고종에 대한 문항"

        tasks, _ = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )
        remaining, assignments, audit = self.resolve_by_context(
            tasks,
            self.policy,
        )

        self.assertEqual(len(remaining), 1)
        resolved_assignment = assignments[
            assignments["verification_status"] == "VERIFIED"
        ].iloc[0]
        self.assertEqual(
            json.loads(
                resolved_assignment[
                    "selected_canonical_alternative_ids_json"
                ]
            ),
            ["alternative-joseon"],
        )
        resolved_audit = audit[
            audit["resolution_status"] == "RESOLVED"
        ].iloc[0]
        self.assertIn(
            "대한제국",
            json.loads(resolved_audit["evidence_json"])[
                "alternative-joseon"
            ]["entity_anchors"],
        )

    def test_concept_anchor_requires_two_independent_sources(self):
        alternatives_by_case = {
            "case-single": [
                {
                    "identity_member_source_ids_json": json.dumps(
                        ["AKS:ARTICLE:SINGLE:release"]
                    )
                }
            ],
            "case-corroborated": [
                {
                    "identity_member_source_ids_json": json.dumps(
                        [
                            "AKS:ARTICLE:MULTI:release",
                            "THESAURUS:TERM:MULTI:release",
                        ]
                    )
                }
            ],
        }
        case_by_id = {
            "case-single": {
                "canonical_term": "단일출처개념",
                "entity_type_proposal": "Concept",
            },
            "case-corroborated": {
                "canonical_term": "교차검증개념",
                "entity_type_proposal": "Concept",
            },
        }
        context_policy = self.policy["entity_resolution"][
            "semantic_review"
        ]["problem_context_rule"]

        anchor_terms = self.collect_anchor_terms(
            alternatives_by_case,
            case_by_id,
            context_policy,
        )

        self.assertNotIn("단일출처개념", anchor_terms)
        self.assertIn("교차검증개념", anchor_terms)

    def test_neighbor_sentence_signal_does_not_select_alternative(self):
        task = {
            "problem_review_task_id": "task-taejo",
            "problem_assignment_id": "assignment-taejo",
            "problem_id": "question-taejo",
            "resolution_case_id": "case-taejo",
            "canonical_term": "태조",
            "problem_full_text": (
                "고려 시대에는 태조만 조의 묘호가 붙었다. "
                "그러나 고려 후기에는 충렬왕처럼 조를 붙이지 못했다."
            ),
            "canonical_alternatives": [
                {
                    "canonical_alternative_id": "alternative-goryeo",
                    "context_features": {
                        "era_tokens": ["고려전기"],
                        "aliases": [],
                        "hanja": [],
                        "years": [],
                    },
                },
                {
                    "canonical_alternative_id": "alternative-joseon",
                    "context_features": {
                        "era_tokens": ["고려후기"],
                        "aliases": [],
                        "hanja": [],
                        "years": [],
                    },
                },
            ],
        }

        remaining, assignments, audit = self.resolve_by_context(
            [task],
            self.policy,
        )

        self.assertEqual(len(remaining), 1)
        self.assertEqual(
            assignments.iloc[0]["verification_status"],
            "NEEDS_MANUAL_REVIEW",
        )
        self.assertEqual(
            audit.iloc[0]["resolution_status"],
            "DEFERRED",
        )

    def test_alias_inside_longer_word_is_not_context_evidence(self):
        task = {
            "problem_review_task_id": "task-jigonggeo",
            "problem_assignment_id": "assignment-jigonggeo",
            "problem_id": "question-jigonggeo",
            "resolution_case_id": "case-jigonggeo",
            "canonical_term": "지공거",
            "problem_full_text": "한림학사 쌍기를 지공거로 임명하였다.",
            "canonical_alternatives": [
                {
                    "canonical_alternative_id": "alternative-haksa",
                    "context_features": {
                        "era_tokens": [],
                        "aliases": ["학사"],
                        "hanja": [],
                        "years": [],
                    },
                },
                {
                    "canonical_alternative_id": "alternative-jwaju",
                    "context_features": {
                        "era_tokens": [],
                        "aliases": ["좌주"],
                        "hanja": [],
                        "years": [],
                    },
                },
            ],
        }

        remaining, assignments, audit = self.resolve_by_context(
            [task],
            self.policy,
        )

        self.assertEqual(len(remaining), 1)
        self.assertEqual(
            assignments.iloc[0]["verification_status"],
            "NEEDS_MANUAL_REVIEW",
        )
        self.assertEqual(
            audit.iloc[0]["resolution_status"],
            "DEFERRED",
        )

    def test_unknown_alternative_is_invalid(self):
        resolution_tables, term_tables = self.build_fixture()
        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )
        decision = self.make_decision(tasks[0])
        decision["selected_canonical_alternative_ids"] = ["not-an-option"]

        outputs = self.validate_decisions(
            [decision],
            tasks,
            deterministic,
            self.policy,
        )

        self.assertEqual(
            outputs["problem_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "INVALID",
        )
        self.assertIn(
            "UNKNOWN_CANONICAL_ALTERNATIVE",
            set(outputs["problem_decision_validation_errors"]["error_code"]),
        )

    def test_ambiguous_selection_requires_manual_review(self):
        resolution_tables, term_tables = self.build_fixture()
        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )
        decision = self.make_decision(tasks[0])
        decision["selection_mode"] = "AMBIGUOUS"
        decision["selected_canonical_alternative_ids"] = []

        outputs = self.validate_decisions(
            [decision],
            tasks,
            deterministic,
            self.policy,
        )

        self.assertEqual(
            outputs["problem_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "NEEDS_MANUAL_REVIEW",
        )


if __name__ == "__main__":
    unittest.main()

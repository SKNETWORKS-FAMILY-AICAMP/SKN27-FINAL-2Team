import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import pandas as pd


class SemanticReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))
        sys.path.insert(0, str(neo4j_root / "terms"))

        from common import load_pipeline_policy
        from entity_resolution.build_resolution_package import (
            build_resolution_tables,
        )
        from entity_resolution.semantic_review import (
            build_validation_tables_from_review_tasks,
            build_term_review_tasks,
            validate_term_decisions,
        )
        from entity_resolution.manual_term_review import (
            build_manual_review_table,
            prepare_manual_decisions,
        )

        cls.build_resolution_tables = staticmethod(build_resolution_tables)
        cls.build_term_review_tasks = staticmethod(build_term_review_tasks)
        cls.build_validation_tables_from_review_tasks = staticmethod(
            build_validation_tables_from_review_tasks
        )
        cls.validate_term_decisions = staticmethod(validate_term_decisions)
        cls.build_manual_review_table = staticmethod(
            build_manual_review_table
        )
        cls.prepare_manual_decisions = staticmethod(
            prepare_manual_decisions
        )
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def make_candidate(
        self,
        source: str,
        source_id: str,
        metadata: dict,
    ) -> dict:
        candidate = {
            "source": source,
            "source_id": source_id,
            "source_release": "test-release",
            "source_record_id": f"{source}:RECORD:{source_id}:test-release",
            "matched_name": "이순신",
            "matched_field": "name",
            "retrieval_method": "exact",
            "retrieval_methods": ["exact"],
            "retrieval_score": 1.0,
            "score_components": {},
            "verification_status": "PROPOSED",
            "retrieval_policy_version": self.policy["policy_version"],
            "category_mismatch": False,
        }
        candidate.update(metadata)
        return candidate

    def build_fixture(self, include_weak_person: bool = False):
        aks = self.make_candidate(
            "AKS",
            "E1",
            {
                "eid": "E1",
                "headword": "이순신",
                "aliases": ["李舜臣"],
                "primary_type": "인물/전통 인물",
                "primary_type_part": "인물",
                "era": "조선/조선 후기",
                "definition": "조선 시대의 무신이다.",
            },
        )
        thesaurus = self.make_candidate(
            "THESAURUS",
            "T1",
            {
                "term_id": "T1",
                "term_name": "이순신",
                "hanja": "李舜臣",
                "era": "조선 후기",
                "thesaurus_category": "인명",
                "description": "조선의 무신이다.",
            },
        )
        event = self.make_candidate(
            "ITKC_EVENT",
            "EV1",
            {
                "event_id": "EV1",
                "event_name": "이순신의 사망",
                "subject_category": "인물",
                "period": "조선",
                "event_date": "1598",
            },
        )
        event["category_mismatch"] = None
        people = []
        if include_weak_person:
            weak_person = self.make_candidate(
                "ITKC_PERSON",
                "P1",
                {
                    "person_id": "P1",
                    "name": "이순신",
                    "hanja": "",
                    "birth_year": "",
                    "death_year": "",
                    "bonkwan": "",
                },
            )
            weak_person["category_mismatch"] = None
            people.append(weak_person)
        match_results = [
            {
                "canonical_term": "이순신",
                "category": "인물",
                "problem_ids": ["question-1"],
                "is_noise": False,
                "encyclopedia": [aks],
                "thesaurus": [thesaurus],
                "itkc_people": people,
                "itkc_events": [event],
                "extraction_model": "test-model",
                "extraction_policy_version": "test-extraction",
            }
        ]
        contexts = pd.DataFrame(
            [{"problem_id": "question-1", "full_text": "이순신 관련 문항"}]
        )
        tables = self.build_resolution_tables(
            match_results,
            [],
            contexts,
            self.policy,
        )
        tasks = self.build_term_review_tasks(tables, self.policy)
        return tables, tasks

    def make_decision(self, task: dict) -> dict:
        candidate_ids = {
            item["source"]: item["source_candidate_id"]
            for item in task["source_candidates"]
        }
        return {
            "term_review_task_id": task["term_review_task_id"],
            "resolution_case_id": task["resolution_case_id"],
            "decision_status": "PROPOSED",
            "review_model": self.policy["entity_resolution"][
                "semantic_review"
            ]["term_model"]["model"],
            "prompt_version": self.policy["entity_resolution"][
                "semantic_review"
            ]["prompt_version"],
            "proposed_alternatives": [
                {
                    "display_name": "이순신(조선)",
                    "entity_type": "Person",
                    "identity_member_source_candidate_ids": [
                        candidate_ids["AKS"],
                        candidate_ids["THESAURUS"],
                    ],
                    "reason": "이름·한자·시대가 일치한다.",
                }
            ],
            "evidence_only_sources": [
                {
                    "source_candidate_id": candidate_ids["ITKC_EVENT"],
                    "reason": "인물 자체가 아니라 사망 사건이다.",
                }
            ],
            "rejected_sources": [],
            "ambiguous_sources": [],
            "decision_reason": "인물 원천과 사건 원천을 분리했다.",
        }

    def test_task_preserves_multiple_sources_and_problem_context(self):
        _, tasks = self.build_fixture()

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(len(task["source_candidates"]), 3)
        self.assertEqual(
            task["problem_context_samples"][0]["full_text"],
            "이순신 관련 문항",
        )
        self.assertTrue(task["code_canonical_alternatives"])

    def test_valid_multiple_source_decision_is_verified(self):
        tables, tasks = self.build_fixture()
        decision = self.make_decision(tasks[0])

        outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )

        decision_row = outputs["term_resolution_decisions"].iloc[0]
        alternatives = outputs["reviewed_canonical_alternatives"]
        roles = outputs["reviewed_source_roles"]
        self.assertEqual(decision_row["verification_status"], "VERIFIED")
        self.assertEqual(len(alternatives), 1)
        self.assertEqual(int(alternatives.iloc[0]["member_count"]), 2)
        self.assertEqual(
            set(roles["verified_role"]),
            {"IDENTITY_MEMBER", "EVIDENCE_ONLY"},
        )

        tasks[0]["canonical_term"] = "unrelated-upstream-input"
        tasks[0]["term_variants"] = ["unrelated-upstream-input"]
        alignment_outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )
        alignment_summary = alignment_outputs[
            "term_resolution_decisions"
        ].iloc[0]
        alignment_error_codes = set(
            alignment_outputs["term_decision_validation_errors"][
                "error_code"
            ]
        )
        self.assertEqual(
            alignment_summary["verification_status"],
            "NEEDS_MANUAL_REVIEW",
        )
        self.assertIn(
            "TERM_SOURCE_ALIGNMENT_REVIEW_REQUIRED",
            alignment_error_codes,
        )

    def test_candidate_omission_is_invalid(self):
        tables, tasks = self.build_fixture()
        decision = self.make_decision(tasks[0])
        decision["evidence_only_sources"] = []

        outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )

        self.assertEqual(
            outputs["term_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "INVALID",
        )
        self.assertIn(
            "MISSING_CANDIDATE_CLASSIFICATION",
            set(outputs["term_decision_validation_errors"]["error_code"]),
        )

    def test_each_identity_member_must_align_with_target_term(self):
        tables, tasks = self.build_fixture()
        decision = self.make_decision(tasks[0])
        thesaurus_candidate = next(
            candidate
            for candidate in tasks[0]["source_candidates"]
            if candidate["source"] == "THESAURUS"
        )
        thesaurus_candidate["matched_name"] = "이순신전기"
        thesaurus_candidate["normalized_names"] = ["이순신전기"]

        outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )

        decision_row = outputs["term_resolution_decisions"].iloc[0]
        validation_errors = outputs["term_decision_validation_errors"]
        alignment_errors = validation_errors.loc[
            validation_errors["error_code"]
            == "TERM_SOURCE_ALIGNMENT_REVIEW_REQUIRED"
        ]
        self.assertEqual(
            decision_row["verification_status"],
            "NEEDS_MANUAL_REVIEW",
        )
        self.assertEqual(len(alignment_errors), 1)
        self.assertIn(
            thesaurus_candidate["source_candidate_id"],
            alignment_errors.iloc[0]["message"],
        )

    def test_strong_conflict_inside_identity_group_is_invalid(self):
        tables, tasks = self.build_fixture()
        decision = self.make_decision(tasks[0])
        event_id = decision["evidence_only_sources"][0]["source_candidate_id"]
        decision["evidence_only_sources"] = []
        decision["proposed_alternatives"][0][
            "identity_member_source_candidate_ids"
        ].append(event_id)

        outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )

        self.assertEqual(
            outputs["term_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "INVALID",
        )
        self.assertIn(
            "STRONG_PAIR_CONFLICT",
            set(outputs["term_decision_validation_errors"]["error_code"]),
        )
        self.assertNotIn(
            "INSUFFICIENT_PAIR_EVIDENCE",
            set(outputs["term_decision_validation_errors"]["error_code"]),
        )
        manually_reviewed_outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
            manual_verifications={
                tasks[0]["resolution_case_id"]: {
                    "reviewer": "tester",
                    "reviewed_at": "2026-07-22T00:00:00+00:00",
                }
            },
        )
        self.assertEqual(
            manually_reviewed_outputs["term_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "INVALID",
        )

    def test_insufficient_pair_signal_requires_manual_review(self):
        tables, tasks = self.build_fixture(include_weak_person=True)
        decision = self.make_decision(tasks[0])
        candidate_ids = {
            item["source"]: item["source_candidate_id"]
            for item in tasks[0]["source_candidates"]
        }
        decision["proposed_alternatives"][0][
            "identity_member_source_candidate_ids"
        ].append(candidate_ids["ITKC_PERSON"])

        reconstructed_tables = self.build_validation_tables_from_review_tasks(
            tasks
        )
        outputs = self.validate_term_decisions(
            [decision],
            tasks,
            reconstructed_tables,
            self.policy,
        )

        self.assertEqual(
            outputs["term_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "NEEDS_MANUAL_REVIEW",
        )
        self.assertIn(
            "INSUFFICIENT_PAIR_EVIDENCE",
            set(outputs["term_decision_validation_errors"]["error_code"]),
        )

    def test_connected_pair_evidence_verifies_transitive_identity_group(self):
        _, tasks = self.build_fixture(include_weak_person=True)
        decision = self.make_decision(tasks[0])
        candidate_ids = {
            item["source"]: item["source_candidate_id"]
            for item in tasks[0]["source_candidates"]
        }
        weak_candidate_id = candidate_ids["ITKC_PERSON"]
        decision["proposed_alternatives"][0][
            "identity_member_source_candidate_ids"
        ].append(weak_candidate_id)
        tables = self.build_validation_tables_from_review_tasks(tasks)
        aks_candidate_id = candidate_ids["AKS"]
        weak_edge_mask = (
            (
                tables["source_candidate_pair_signals"][
                    "left_source_candidate_id"
                ]
                == aks_candidate_id
            )
            & (
                tables["source_candidate_pair_signals"][
                    "right_source_candidate_id"
                ]
                == weak_candidate_id
            )
        ) | (
            (
                tables["source_candidate_pair_signals"][
                    "left_source_candidate_id"
                ]
                == weak_candidate_id
            )
            & (
                tables["source_candidate_pair_signals"][
                    "right_source_candidate_id"
                ]
                == aks_candidate_id
            )
        )
        tables["source_candidate_pair_signals"].loc[
            weak_edge_mask,
            "merge_eligible",
        ] = True

        connected_outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )
        complete_graph_policy = deepcopy(self.policy)
        identity_pair_gate_policy = complete_graph_policy[
            "entity_resolution"
        ]["semantic_review"]["identity_pair_gate"]
        identity_pair_gate_policy["active_evidence_mode"] = (
            identity_pair_gate_policy["evidence_modes"]["complete"]
        )
        complete_outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            complete_graph_policy,
        )

        self.assertEqual(
            connected_outputs["term_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "VERIFIED",
        )
        self.assertEqual(
            complete_outputs["term_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "NEEDS_MANUAL_REVIEW",
        )

    def test_completed_manual_review_promotes_safe_model_proposal(self):
        tables, tasks = self.build_fixture(include_weak_person=True)
        decision = self.make_decision(tasks[0])
        candidate_ids = {
            item["source"]: item["source_candidate_id"]
            for item in tasks[0]["source_candidates"]
        }
        decision["proposed_alternatives"][0][
            "identity_member_source_candidate_ids"
        ].append(candidate_ids["ITKC_PERSON"])
        automatic_outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            review_path = str(
                Path(temporary_directory) / "related_manual_review.csv"
            )
            review_table = self.build_manual_review_table(
                [decision],
                tasks,
                automatic_outputs,
                review_path,
                self.policy,
            )

        review_table.at[0, "manual_status"] = "VERIFIED"
        review_table.at[0, "manual_reason"] = "사람이 동일 인물로 확인함"
        review_table.at[0, "reviewer"] = "tester"
        prepared = self.prepare_manual_decisions(
            review_table,
            [decision],
            tasks,
            automatic_outputs,
            self.policy,
        )
        reviewed_outputs = self.validate_term_decisions(
            prepared["decisions"],
            tasks,
            tables,
            self.policy,
            manual_verifications=prepared["manual_verifications"],
        )

        summary = reviewed_outputs["term_resolution_decisions"].iloc[0]
        self.assertTrue(prepared["validation_errors"].empty)
        self.assertEqual(summary["verification_status"], "VERIFIED")
        self.assertEqual(summary["verification_method"], "HUMAN_REVIEW")
        self.assertEqual(summary["verified_by"], "tester")

    def test_pending_review_refreshes_model_fields_and_keeps_source_context(
        self,
    ):
        tables, tasks = self.build_fixture(include_weak_person=True)
        decision = self.make_decision(tasks[0])
        candidate_ids = {
            item["source"]: item["source_candidate_id"]
            for item in tasks[0]["source_candidates"]
        }
        decision["proposed_alternatives"][0][
            "identity_member_source_candidate_ids"
        ].append(candidate_ids["ITKC_PERSON"])
        automatic_outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            review_path = str(
                Path(temporary_directory) / "related_manual_review.csv"
            )
            existing_table = self.build_manual_review_table(
                [decision],
                tasks,
                automatic_outputs,
                review_path,
                self.policy,
            )
            existing_table.at[0, "canonical_alternatives_json"] = json.dumps(
                [{"display_name": "stale-model-output"}],
                ensure_ascii=False,
            )
            existing_table.to_csv(
                review_path,
                index=False,
                encoding="utf-8-sig",
            )

            refreshed_table = self.build_manual_review_table(
                [decision],
                tasks,
                automatic_outputs,
                review_path,
                self.policy,
            )

        alternatives = json.loads(
            refreshed_table.iloc[0]["canonical_alternatives_json"]
        )
        candidate_reference = json.loads(
            refreshed_table.iloc[0]["candidate_reference_json"]
        )
        self.assertNotEqual(
            alternatives[0]["display_name"],
            "stale-model-output",
        )
        self.assertTrue(
            all("source_context" in item for item in candidate_reference)
        )

    def test_completed_review_preserves_human_decision_fields(self):
        tables, tasks = self.build_fixture(include_weak_person=True)
        decision = self.make_decision(tasks[0])
        candidate_ids = {
            item["source"]: item["source_candidate_id"]
            for item in tasks[0]["source_candidates"]
        }
        decision["proposed_alternatives"][0][
            "identity_member_source_candidate_ids"
        ].append(candidate_ids["ITKC_PERSON"])
        automatic_outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            review_path = str(
                Path(temporary_directory) / "related_manual_review.csv"
            )
            existing_table = self.build_manual_review_table(
                [decision],
                tasks,
                automatic_outputs,
                review_path,
                self.policy,
            )
            human_alternatives = [{"display_name": "human-reviewed-output"}]
            existing_table.at[0, "canonical_alternatives_json"] = json.dumps(
                human_alternatives,
                ensure_ascii=False,
            )
            existing_table.at[0, "manual_status"] = "VERIFIED"
            existing_table.at[0, "manual_reason"] = "사람이 판정을 완료함"
            existing_table.at[0, "reviewer"] = "tester"
            existing_table.to_csv(
                review_path,
                index=False,
                encoding="utf-8-sig",
            )

            refreshed_table = self.build_manual_review_table(
                [decision],
                tasks,
                automatic_outputs,
                review_path,
                self.policy,
            )

        self.assertEqual(
            json.loads(
                refreshed_table.iloc[0]["canonical_alternatives_json"]
            ),
            human_alternatives,
        )
        self.assertEqual(
            refreshed_table.iloc[0]["manual_status"],
            "VERIFIED",
        )

    def test_remaining_ambiguous_source_requires_manual_review(self):
        tables, tasks = self.build_fixture()
        decision = self.make_decision(tasks[0])
        event_item = decision["evidence_only_sources"].pop()
        decision["ambiguous_sources"].append(event_item)

        outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )

        self.assertEqual(
            outputs["term_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "NEEDS_MANUAL_REVIEW",
        )

    def test_malformed_decision_is_recorded_without_crashing(self):
        tables, tasks = self.build_fixture()
        decision = self.make_decision(tasks[0])
        decision["proposed_alternatives"] = "invalid"

        outputs = self.validate_term_decisions(
            [decision],
            tasks,
            tables,
            self.policy,
        )

        self.assertEqual(
            outputs["term_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "INVALID",
        )
        self.assertIn(
            "DECISION_SCHEMA_ERROR",
            set(outputs["term_decision_validation_errors"]["error_code"]),
        )


if __name__ == "__main__":
    unittest.main()

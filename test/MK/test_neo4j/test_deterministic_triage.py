import sys
import unittest
from copy import deepcopy
from pathlib import Path


class DeterministicTriageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))

        from common import load_pipeline_policy
        from entity_resolution.deterministic_triage import (
            select_budgeted_tasks,
            triage_term_tasks,
        )

        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )
        cls.select_budgeted_tasks = staticmethod(select_budgeted_tasks)
        cls.triage_term_tasks = staticmethod(triage_term_tasks)

    def build_task(self, problem_count: int = 2) -> dict:
        candidates = [
            {
                "source_candidate_id": "candidate-aks",
                "source": "AKS",
                "matched_name": "이순신",
                "matched_field": "name",
                "retrieval_method": "exact",
                "category_compatibility": "COMPATIBLE",
                "source_entity_type_proposal": "Person",
                "code_canonical_alternative_id": "alternative-safe",
            },
            {
                "source_candidate_id": "candidate-thesaurus",
                "source": "THESAURUS",
                "matched_name": "이순신",
                "matched_field": "name",
                "retrieval_method": "exact",
                "category_compatibility": "UNKNOWN",
                "source_entity_type_proposal": "Person",
                "code_canonical_alternative_id": "alternative-safe",
            },
        ]
        return {
            "term_review_task_id": "task-1",
            "resolution_case_id": "case-1",
            "canonical_term": "이순신",
            "category": "인물",
            "entity_type_proposal": "Person",
            "problem_count": problem_count,
            "source_candidates": candidates,
            "code_canonical_alternatives": [
                {
                    "canonical_alternative_id": "alternative-safe",
                    "confidence_tier": "MULTI_SOURCE_SUPPORTED",
                    "source_candidate_ids": [
                        "candidate-aks",
                        "candidate-thesaurus",
                    ],
                }
            ],
            "relevant_pair_signals": [
                {
                    "left_source_candidate_id": "candidate-aks",
                    "right_source_candidate_id": "candidate-thesaurus",
                    "merge_eligible": True,
                    "conflicts": [],
                }
            ],
        }

    def test_safe_multi_source_exact_is_code_linkable(self):
        triage, decisions, context_tasks = self.triage_term_tasks(
            [self.build_task()],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "CODE_LINKABLE")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(len(context_tasks), 0)

    def test_competing_exact_candidate_is_preserved_for_context(self):
        task = self.build_task()
        competitor = deepcopy(task["source_candidates"][0])
        competitor["source_candidate_id"] = "candidate-competitor"
        competitor["code_canonical_alternative_id"] = "alternative-other"
        task["source_candidates"].append(competitor)

        triage, decisions, context_tasks = self.triage_term_tasks(
            [task],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "CONTEXT_REQUIRED")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(len(decisions[0]["proposed_alternatives"]), 2)
        self.assertEqual(len(context_tasks), 1)

    def test_low_frequency_exact_task_is_still_code_linkable(self):
        task = self.build_task(problem_count=1)

        triage, decisions, context_tasks = self.triage_term_tasks(
            [task],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "CODE_LINKABLE")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(len(context_tasks), 0)

    def test_non_exact_task_stays_term_only(self):
        task = self.build_task()
        for candidate in task["source_candidates"]:
            candidate["matched_name"] = "다른 인물"

        triage, decisions, context_tasks = self.triage_term_tasks(
            [task],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "TERM_ONLY")
        self.assertEqual(len(decisions), 0)
        self.assertEqual(len(context_tasks), 0)

    def test_source_metadata_type_overrides_conflicting_task_type(self):
        institution_task = self.build_task()
        concept_task = deepcopy(institution_task)
        for task, entity_type, task_id in [
            (institution_task, "Institution", "task-institution"),
            (concept_task, "Concept", "task-concept"),
        ]:
            task["term_review_task_id"] = task_id
            task["resolution_case_id"] = f"case-{entity_type.lower()}"
            task["entity_type_proposal"] = entity_type
            task["source_candidates"] = [
                {
                    "source_candidate_id": f"candidate-{task_id}",
                    "source_record_id": "THESAURUS:TERM:shared",
                    "source": "THESAURUS",
                    "matched_name": task["canonical_term"],
                    "matched_field": "name",
                    "retrieval_method": "exact",
                    "category_compatibility": "UNKNOWN",
                    "source_entity_type_proposal": "",
                    "source_context": {
                        "thesaurus_category": (
                            "정치·행정·법제>행정>중앙행정기구"
                        ),
                        "description": "왕실 업무를 담당한 관청.",
                    },
                }
            ]
            task["code_canonical_alternatives"] = []
            task["relevant_pair_signals"] = []

        triage, decisions, _ = self.triage_term_tasks(
            [institution_task, concept_task],
            self.policy,
        )

        disposition_by_task = dict(
            zip(
                triage["term_review_task_id"],
                triage["disposition"],
            )
        )
        self.assertEqual(
            disposition_by_task["task-institution"],
            "CODE_LINKABLE",
        )
        self.assertEqual(
            disposition_by_task["task-concept"],
            "TERM_ONLY",
        )
        self.assertEqual(len(decisions), 1)

    def test_untyped_source_shared_by_multiple_types_is_deferred(self):
        concept_task = self.build_task()
        work_task = deepcopy(concept_task)
        for task, entity_type, task_id in [
            (concept_task, "Concept", "task-concept"),
            (work_task, "Work", "task-work"),
        ]:
            task["term_review_task_id"] = task_id
            task["resolution_case_id"] = f"case-{entity_type.lower()}"
            task["entity_type_proposal"] = entity_type
            task["source_candidates"] = [
                {
                    "source_candidate_id": f"candidate-{task_id}",
                    "source_record_id": "THESAURUS:TERM:ambiguous",
                    "source": "THESAURUS",
                    "matched_name": task["canonical_term"],
                    "matched_field": "name",
                    "retrieval_method": "exact",
                    "category_compatibility": "UNKNOWN",
                    "source_entity_type_proposal": "",
                    "source_context": {
                        "thesaurus_category": "문화·예술>음악",
                        "description": "",
                    },
                }
            ]
            task["code_canonical_alternatives"] = []
            task["relevant_pair_signals"] = []

        triage, decisions, context_tasks = self.triage_term_tasks(
            [concept_task, work_task],
            self.policy,
        )

        self.assertEqual(set(triage["disposition"]), {"TERM_ONLY"})
        self.assertEqual(
            set(triage["reason_code"]),
            {"SOURCE_ENTITY_TYPE_AMBIGUOUS"},
        )
        self.assertEqual(
            set(triage["ambiguous_source_type_count"]),
            {1},
        )
        self.assertEqual(decisions, [])
        self.assertEqual(context_tasks, [])

    def test_identical_thesaurus_records_are_one_entity_component(self):
        task = self.build_task()
        task["entity_type_proposal"] = "Institution"
        task["source_candidates"] = []
        for suffix in ["a", "b"]:
            task["source_candidates"].append(
                {
                    "source_candidate_id": f"candidate-{suffix}",
                    "source_record_id": f"THESAURUS:TERM:{suffix}",
                    "source": "THESAURUS",
                    "matched_name": task["canonical_term"],
                    "matched_field": "name",
                    "retrieval_method": "exact",
                    "category_compatibility": "UNKNOWN",
                    "source_entity_type_proposal": "",
                    "source_context": {
                        "term_name": task["canonical_term"],
                        "hanja": "度支部",
                        "era": "개항기",
                        "thesaurus_category": (
                            "정치·행정·법제>행정>중앙행정기구"
                        ),
                        "description": (
                            "1895년 설치되어 재정을 담당한 관청."
                        ),
                        "term_year": "1895",
                        "term_remark": "",
                    },
                }
            )
        task["code_canonical_alternatives"] = []
        task["relevant_pair_signals"] = []

        triage, decisions, context_tasks = self.triage_term_tasks(
            [task],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "CODE_LINKABLE")
        proposed_alternatives = decisions[0]["proposed_alternatives"]
        self.assertEqual(len(proposed_alternatives), 1)
        self.assertEqual(
            len(
                proposed_alternatives[0][
                    "identity_member_source_candidate_ids"
                ]
            ),
            2,
        )
        self.assertEqual(context_tasks, [])

    def test_same_name_with_different_definitions_stays_competing(self):
        task = self.build_task()
        task["entity_type_proposal"] = "Institution"
        task["source_candidates"] = []
        for suffix, description in [
            ("a", "1895년 설치되어 재정을 담당한 관청."),
            ("b", "일제시기 설치된 중앙 행정 조직."),
        ]:
            task["source_candidates"].append(
                {
                    "source_candidate_id": f"candidate-{suffix}",
                    "source_record_id": f"THESAURUS:TERM:{suffix}",
                    "source": "THESAURUS",
                    "matched_name": task["canonical_term"],
                    "matched_field": "name",
                    "retrieval_method": "exact",
                    "category_compatibility": "UNKNOWN",
                    "source_entity_type_proposal": "",
                    "source_context": {
                        "term_name": task["canonical_term"],
                        "hanja": "度支部",
                        "era": "",
                        "thesaurus_category": (
                            "정치·행정·법제>행정>중앙행정기구"
                        ),
                        "description": description,
                        "term_year": "",
                        "term_remark": "",
                    },
                }
            )
        task["code_canonical_alternatives"] = []
        task["relevant_pair_signals"] = []

        triage, decisions, context_tasks = self.triage_term_tasks(
            [task],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "CONTEXT_REQUIRED")
        self.assertEqual(len(decisions[0]["proposed_alternatives"]), 2)
        self.assertEqual(len(context_tasks), 1)

    def test_same_identity_in_different_taxonomies_is_one_component(self):
        task = self.build_task()
        task["entity_type_proposal"] = "Institution"
        task["source_candidates"] = []
        for suffix, taxonomy in [
            ("administration", "administration-taxonomy"),
            ("education", "education-taxonomy"),
        ]:
            task["source_candidates"].append(
                {
                    "source_candidate_id": f"candidate-{suffix}",
                    "source_record_id": f"THESAURUS:TERM:{suffix}",
                    "source": "THESAURUS",
                    "matched_name": task["canonical_term"],
                    "matched_field": "name",
                    "retrieval_method": "exact",
                    "category_compatibility": "UNKNOWN",
                    "source_entity_type_proposal": "Institution",
                    "source_context": {
                        "term_name": task["canonical_term"],
                        "hanja": "同一機關",
                        "era": "test-era",
                        "thesaurus_category": taxonomy,
                        "description": "The exact same official definition.",
                        "term_year": "1900-1910",
                        "term_remark": "",
                    },
                }
            )
        task["code_canonical_alternatives"] = []
        task["relevant_pair_signals"] = []

        triage, decisions, context_tasks = self.triage_term_tasks(
            [task],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "CODE_LINKABLE")
        self.assertEqual(len(decisions[0]["proposed_alternatives"]), 1)
        self.assertEqual(
            len(
                decisions[0]["proposed_alternatives"][0][
                    "identity_member_source_candidate_ids"
                ]
            ),
            2,
        )
        self.assertEqual(context_tasks, [])

    def test_taxonomy_duplicate_joins_multi_source_component(self):
        task = self.build_task()
        task["entity_type_proposal"] = "Institution"
        common_context = {
            "term_name": task["canonical_term"],
            "hanja": "同一機關",
            "era": "test-era",
            "description": "The exact same official definition.",
            "term_year": "1900-1910",
            "term_remark": "",
        }
        task["source_candidates"][0][
            "source_entity_type_proposal"
        ] = "Institution"
        task["source_candidates"][1].update(
            {
                "source_entity_type_proposal": "Institution",
                "source_context": {
                    **common_context,
                    "thesaurus_category": "administration-taxonomy",
                },
            }
        )
        task["source_candidates"].append(
            {
                "source_candidate_id": "candidate-taxonomy-duplicate",
                "source_record_id": "THESAURUS:TERM:taxonomy-duplicate",
                "source": "THESAURUS",
                "matched_name": task["canonical_term"],
                "matched_field": "name",
                "retrieval_method": "exact",
                "category_compatibility": "UNKNOWN",
                "source_entity_type_proposal": "Institution",
                "source_context": {
                    **common_context,
                    "thesaurus_category": "education-taxonomy",
                },
            }
        )

        triage, decisions, context_tasks = self.triage_term_tasks(
            [task],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "CODE_LINKABLE")
        self.assertEqual(len(decisions[0]["proposed_alternatives"]), 1)
        self.assertEqual(
            len(
                decisions[0]["proposed_alternatives"][0][
                    "identity_member_source_candidate_ids"
                ]
            ),
            3,
        )
        self.assertEqual(context_tasks, [])

    def test_reference_only_thesaurus_index_is_rejected(self):
        task = self.build_task()
        task["source_candidates"] = [
            {
                "source_candidate_id": "candidate-primary",
                "source_record_id": "THESAURUS:TERM:primary",
                "source": "THESAURUS",
                "matched_name": task["canonical_term"],
                "matched_field": "name",
                "retrieval_method": "exact",
                "category_compatibility": "UNKNOWN",
                "source_entity_type_proposal": "Person",
                "source_context": {
                    "term_name": task["canonical_term"],
                    "term_kind": "2",
                    "hanja": "人物",
                },
            },
            {
                "source_candidate_id": "candidate-index",
                "source_record_id": "THESAURUS:TERM:index",
                "source": "THESAURUS",
                "matched_name": task["canonical_term"],
                "matched_field": "name",
                "retrieval_method": "exact",
                "category_compatibility": "UNKNOWN",
                "source_entity_type_proposal": "Person",
                "source_context": {
                    "term_name": task["canonical_term"],
                    "term_kind": "1",
                    "term_remark": self.policy["entity_resolution"][
                        "source_feature_policy"
                    ]["sources"]["THESAURUS"]["reference_only_filter"][
                        "remark_values"
                    ][0],
                },
            },
        ]
        task["code_canonical_alternatives"] = []
        task["relevant_pair_signals"] = []

        triage, decisions, context_tasks = self.triage_term_tasks(
            [task],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "CODE_LINKABLE")
        self.assertEqual(len(decisions[0]["proposed_alternatives"]), 1)
        self.assertEqual(
            decisions[0]["rejected_sources"][0]["source_candidate_id"],
            "candidate-index",
        )
        self.assertEqual(context_tasks, [])

    def test_reference_only_index_does_not_promote_alias_anchor(self):
        task = self.build_task()
        reference_remark = self.policy["entity_resolution"][
            "source_feature_policy"
        ]["sources"]["THESAURUS"]["reference_only_filter"][
            "remark_values"
        ][0]
        task["source_candidates"] = [
            {
                "source_candidate_id": "candidate-alias",
                "source_record_id": "AKS:ARTICLE:alias",
                "source": "AKS",
                "matched_name": task["canonical_term"],
                "matched_field": "name",
                "retrieval_method": "exact",
                "category_compatibility": "COMPATIBLE",
                "source_entity_type_proposal": "Person",
                "source_context": {
                    "headword": "different-primary-name",
                    "aliases": [task["canonical_term"]],
                },
            },
            {
                "source_candidate_id": "candidate-index",
                "source_record_id": "THESAURUS:TERM:index",
                "source": "THESAURUS",
                "matched_name": task["canonical_term"],
                "matched_field": "name",
                "retrieval_method": "exact",
                "category_compatibility": "UNKNOWN",
                "source_entity_type_proposal": "Person",
                "source_context": {
                    "term_name": task["canonical_term"],
                    "term_kind": "1",
                    "term_remark": reference_remark,
                },
            },
        ]
        task["code_canonical_alternatives"] = []
        task["relevant_pair_signals"] = []

        triage, decisions, context_tasks = self.triage_term_tasks(
            [task],
            self.policy,
        )

        self.assertEqual(triage.iloc[0]["disposition"], "CONTEXT_REQUIRED")
        self.assertEqual(len(decisions[0]["proposed_alternatives"]), 2)
        self.assertEqual(len(context_tasks), 1)

    def test_default_budget_selects_no_llm_tasks(self):
        executor_policy = self.policy["entity_resolution"][
            "semantic_review"
        ]["term_executor"]
        selected, effective_limit = self.select_budgeted_tasks(
            [self.build_task()],
            0,
            executor_policy,
        )

        self.assertEqual(effective_limit, 0)
        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main()

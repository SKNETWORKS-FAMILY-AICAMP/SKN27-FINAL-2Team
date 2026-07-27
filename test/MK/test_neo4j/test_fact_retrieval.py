from json import dumps, load
import sys
import unittest
from pathlib import Path

import pandas as pd


class FactRetrievalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from fact_retrieval.build import build_entity_anchor_tables
        from fact_retrieval.retrieve import build_swap_candidates
        from fact_retrieval.truth_gate import (
            evaluate_distractor_truth_gate,
        )
        from fact_retrieval.external_results import (
            apply_external_verification_results,
        )
        from fact_retrieval.load import (
            build_fact_retrieval_load_plan,
            build_fact_retrieval_load_queries,
            execute_verified_load_batches,
        )

        cls.build_anchor_tables = staticmethod(
            build_entity_anchor_tables
        )
        cls.build_candidates = staticmethod(build_swap_candidates)
        cls.evaluate_gate = staticmethod(
            evaluate_distractor_truth_gate
        )
        cls.apply_external_results = staticmethod(
            apply_external_verification_results
        )
        cls.build_load_plan = staticmethod(
            build_fact_retrieval_load_plan
        )
        cls.build_load_queries = staticmethod(
            build_fact_retrieval_load_queries
        )
        cls.execute_verified_load_batches = staticmethod(
            execute_verified_load_batches
        )
        with (
            neo4j_root / "config" / "fact_retrieval.json"
        ).open("r", encoding="utf-8") as policy_file:
            cls.policy = load(policy_file)
        cls.exam_canonical_ids = {
            "C-P1",
            "C-P2",
            "C-W1",
            "C-W2",
        }

    def make_anchor_inputs(self) -> tuple[pd.DataFrame, ...]:
        registry = pd.DataFrame(
            [
                {
                    "canonical_id": "C-P1",
                    "display_name": "인물1",
                    "entity_type": "Person",
                },
                {
                    "canonical_id": "C-W1",
                    "display_name": "저작1",
                    "entity_type": "Work",
                },
            ]
        )
        canonical_facts = pd.DataFrame(
            [
                {
                    "canonical_relationship_id": "CF-1",
                    "start_canonical_id": "C-P1",
                    "end_canonical_id": "C-W1",
                    "relation_type": "AUTHORED",
                    "source_relationship_ids_json": "[]",
                    "evidence_urls_json": '["https://example.test/fact"]',
                    "detail_urls_json": "[]",
                    "evidence_sentences_json": '["저술했다."]',
                    "source_datasets_json": '["AKS_DESCRIPTION"]',
                    "verification_status": "PATTERN_ASSERTED",
                }
            ]
        )
        source_nodes = pd.DataFrame(
            [
                {
                    "source_record_id": "S-P1",
                    "source": "ITKC_PERSON",
                    "record_type": "PERSON",
                    "display_name": "인물1",
                    "source_urls_json": "[]",
                },
                {
                    "source_record_id": "S-P2",
                    "source": "ITKC_PERSON",
                    "record_type": "PERSON",
                    "display_name": "인물2",
                    "source_urls_json": "[]",
                },
                {
                    "source_record_id": "S-P3",
                    "source": "ITKC_PERSON",
                    "record_type": "PERSON",
                    "display_name": "인물3",
                    "source_urls_json": "[]",
                },
            ]
        )
        source_relationships = pd.DataFrame(
            [
                {
                    "source_relationship_id": "SR-1",
                    "start_source_record_id": "S-P1",
                    "end_source_record_id": "S-P2",
                    "relation_type": "HAS_TEACHER",
                    "source_dataset": "ITKC_PERSON_RELATION",
                    "verification_status": "SOURCE_ASSERTED",
                    "evidence_urls_json": '["https://example.test/edge"]',
                    "detail_urls_json": "[]",
                },
                {
                    "source_relationship_id": "SR-2",
                    "start_source_record_id": "S-P2",
                    "end_source_record_id": "S-P3",
                    "relation_type": "HAS_TEACHER",
                    "source_dataset": "ITKC_PERSON_RELATION",
                    "verification_status": "SOURCE_ASSERTED",
                    "evidence_urls_json": "[]",
                    "detail_urls_json": "[]",
                },
            ]
        )
        source_resolutions = pd.DataFrame(
            [
                {
                    "source_record_id": "S-P1",
                    "canonical_id": "C-P1",
                    "match_status": "ACCEPTED",
                }
            ]
        )
        topics = pd.DataFrame(
            [
                {
                    "canonical_id": "C-P1",
                    "topic_id": "topic:person",
                },
                {
                    "canonical_id": "C-W1",
                    "topic_id": "topic:culture",
                },
            ]
        )
        eras = pd.DataFrame(
            [
                {
                    "canonical_id": "C-P1",
                    "era_id": "era:joseon",
                }
            ]
        )
        return (
            registry,
            canonical_facts,
            source_nodes,
            source_relationships,
            source_resolutions,
            topics,
            eras,
        )

    def test_one_hop_source_neighbor_is_not_promoted_to_canonical(self):
        tables = self.build_anchor_tables(
            *self.make_anchor_inputs(),
            self.policy,
        )
        anchors = tables["anchor_nodes"]
        facts = tables["anchor_fact_relationships"]

        self.assertEqual(len(anchors), 3)
        self.assertEqual(
            (anchors["anchor_kind"] == "OFFICIAL_SOURCE").sum(),
            1,
        )
        self.assertEqual(
            set(facts["relation_type"]),
            {"AUTHORED", "HAS_TEACHER"},
        )
        self.assertNotIn(
            "S-P3",
            set(anchors["source_record_id"]),
        )

    def make_retrieval_inputs(
        self,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        canonical_facts = pd.DataFrame(
            [
                {
                    "canonical_relationship_id": "CF-CORRECT",
                    "start_canonical_id": "C-P1",
                    "end_canonical_id": "C-W1",
                    "relation_type": "AUTHORED",
                    "verification_status": "SOURCE_ASSERTED",
                },
                {
                    "canonical_relationship_id": "CF-PARALLEL",
                    "start_canonical_id": "C-P2",
                    "end_canonical_id": "C-W2",
                    "relation_type": "AUTHORED",
                    "verification_status": "SOURCE_ASSERTED",
                },
            ]
        )
        anchor_rows = []
        for canonical_id, display_name, entity_type, topic_id in [
            ("C-P1", "인물1", "Person", "topic:person"),
            ("C-P2", "인물2", "Person", "topic:person"),
            ("C-W1", "저작1", "Work", "topic:culture"),
            ("C-W2", "저작2", "Work", "topic:culture"),
        ]:
            anchor_rows.append(
                {
                    "anchor_id": f"EA-{canonical_id}",
                    "anchor_kind": "CANONICAL",
                    "canonical_id": canonical_id,
                    "display_name": display_name,
                    "entity_type": entity_type,
                    "topic_ids_json": dumps([topic_id]),
                    "era_ids_json": dumps(["era:joseon"]),
                }
            )
        anchor_nodes = pd.DataFrame(anchor_rows)
        anchor_facts = pd.DataFrame(
            [
                {
                    "start_anchor_id": "EA-C-P1",
                    "end_anchor_id": "EA-C-W1",
                    "relation_type": "AUTHORED",
                    "search_status": "PRIMARY",
                },
                {
                    "start_anchor_id": "EA-C-P2",
                    "end_anchor_id": "EA-C-W2",
                    "relation_type": "AUTHORED",
                    "search_status": "PRIMARY",
                },
                {
                    "start_anchor_id": "EA-C-P1",
                    "end_anchor_id": "EA-C-P2",
                    "relation_type": "SOCIAL_ASSOCIATE",
                    "search_status": "PRIMARY",
                },
            ]
        )
        return canonical_facts, anchor_nodes, anchor_facts

    def test_rag_finds_same_role_replacement_candidate(self):
        facts, anchors, anchor_facts = self.make_retrieval_inputs()

        candidates = self.build_candidates(
            facts,
            anchors,
            anchor_facts,
            self.policy,
            self.exam_canonical_ids,
        )
        target = candidates[
            candidates["correct_canonical_relationship_id"].eq(
                "CF-CORRECT"
            )
            & candidates["swap_dimension"].eq("START")
            & candidates["candidate_canonical_id"].eq("C-P2")
        ]

        self.assertEqual(len(target), 1)
        self.assertEqual(
            target.iloc[0]["same_relation_role"],
            "true",
        )
        self.assertEqual(
            target.iloc[0]["proposed_start_canonical_id"],
            "C-P2",
        )
        self.assertFalse(
            candidates["proposed_start_canonical_id"]
            .eq(candidates["proposed_end_canonical_id"])
            .any()
        )
        self.assertTrue(
            target.iloc[0]["graph_path_relation_types_json"]
        )

    def test_rag_rejects_disconnected_candidate_without_role(self):
        facts, anchors, anchor_facts = self.make_retrieval_inputs()
        disconnected = pd.DataFrame(
            [
                {
                    "anchor_id": "EA-C-P3",
                    "anchor_kind": "CANONICAL",
                    "canonical_id": "C-P3",
                    "display_name": "인물3",
                    "entity_type": "Person",
                    "topic_ids_json": dumps(["topic:person"]),
                    "era_ids_json": dumps(["era:joseon"]),
                }
            ]
        )
        anchors = pd.concat(
            [anchors, disconnected],
            ignore_index=True,
        )

        candidates = self.build_candidates(
            facts,
            anchors,
            anchor_facts,
            self.policy,
            {*self.exam_canonical_ids, "C-P3"},
        )

        self.assertNotIn(
            "C-P3",
            set(candidates["candidate_canonical_id"]),
        )

    def test_truth_gate_never_treats_absence_as_false(self):
        facts, anchors, anchor_facts = self.make_retrieval_inputs()
        candidates = self.build_candidates(
            facts,
            anchors,
            anchor_facts,
            self.policy,
            self.exam_canonical_ids,
        )
        candidate = candidates[
            candidates["correct_canonical_relationship_id"].eq(
                "CF-CORRECT"
            )
            & candidates["swap_dimension"].eq("START")
            & candidates["candidate_canonical_id"].eq("C-P2")
        ].copy()

        results, tasks = self.evaluate_gate(
            candidate,
            facts,
            self.policy,
        )

        self.assertEqual(
            results.iloc[0]["truth_gate_status"],
            "NEEDS_EXTERNAL_VERIFICATION",
        )
        self.assertEqual(len(tasks), 1)

    def test_truth_gate_blocks_symmetric_reverse_fact(self):
        facts = pd.DataFrame(
            [
                {
                    "canonical_relationship_id": "CF-SYMMETRIC",
                    "start_canonical_id": "C-P1",
                    "end_canonical_id": "C-P2",
                    "relation_type": "SOCIAL_ASSOCIATE",
                    "verification_status": "SOURCE_ASSERTED",
                }
            ]
        )
        candidate = pd.DataFrame(
            [
                {
                    "swap_candidate_id": "SC-SYMMETRIC",
                    "proposed_start_canonical_id": "C-P2",
                    "proposed_end_canonical_id": "C-P1",
                    "relation_type": "SOCIAL_ASSOCIATE",
                    "swap_dimension": "START",
                    "candidate_rank": "1",
                }
            ]
        )

        results, tasks = self.evaluate_gate(
            candidate,
            facts,
            self.policy,
        )

        self.assertEqual(
            results.iloc[0]["truth_gate_status"],
            "BLOCKED_KNOWN_TRUE",
        )
        self.assertFalse(tasks)

    def test_external_verification_tasks_are_deduplicated(self):
        facts, anchors, anchor_facts = self.make_retrieval_inputs()
        candidates = self.build_candidates(
            facts,
            anchors,
            anchor_facts,
            self.policy,
            self.exam_canonical_ids,
        )
        candidate = candidates[
            candidates["correct_canonical_relationship_id"].eq(
                "CF-CORRECT"
            )
            & candidates["swap_dimension"].eq("START")
            & candidates["candidate_canonical_id"].eq("C-P2")
        ].copy()
        duplicate = candidate.copy()
        duplicate.loc[:, "swap_candidate_id"] = "SC-DUPLICATE"
        duplicated_candidates = pd.concat(
            [candidate, duplicate],
            ignore_index=True,
        )

        _, tasks = self.evaluate_gate(
            duplicated_candidates,
            facts,
            self.policy,
        )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(
            tasks[0]["candidate_occurrence_count"],
            2,
        )

        verification_results = [
            {
                "external_verification_task_id": tasks[0][
                    "external_verification_task_id"
                ],
                "decision": "FALSE_RELATION",
                "evidence_urls": ["https://example.test/verify"],
                "reason": "공식 자료에서 다른 저자를 확인했습니다.",
                "verifier": "test",
                "verified_at": "2026-07-26T00:00:00+00:00",
            }
        ]
        gate_results, _ = self.evaluate_gate(
            duplicated_candidates,
            facts,
            self.policy,
        )
        final_results, application = self.apply_external_results(
            gate_results,
            tasks,
            verification_results,
            self.policy,
        )

        self.assertEqual(application["updated_gate_count"], 2)
        self.assertTrue(
            final_results["truth_gate_status"]
            .eq("EXTERNALLY_VERIFIED_CONTRADICTION")
            .all()
        )

    def test_external_true_false_decision_requires_evidence_url(self):
        gate_results = pd.DataFrame(
            [
                {
                    "truth_gate_id": "TG-1",
                    "truth_gate_status": (
                        "NEEDS_EXTERNAL_VERIFICATION"
                    ),
                    "truth_gate_reason": "검증 필요",
                }
            ]
        )
        backlog = [
            {
                "external_verification_task_id": "EVT-1",
                "supporting_truth_gate_ids": ["TG-1"],
            }
        ]
        invalid_result = [
            {
                "external_verification_task_id": "EVT-1",
                "decision": "FALSE_RELATION",
                "evidence_urls": [],
                "reason": "근거 URL 없음",
            }
        ]

        with self.assertRaises(ValueError):
            self.apply_external_results(
                gate_results,
                backlog,
                invalid_result,
                self.policy,
            )

    def test_load_plan_validates_anchor_endpoints(self):
        inputs = self.make_anchor_inputs()
        tables = self.build_anchor_tables(*inputs, self.policy)
        registry, canonical_facts, source_nodes = inputs[:3]

        plan = self.build_load_plan(
            canonical_facts,
            tables["anchor_nodes"],
            source_nodes,
            tables["canonical_anchor_links"],
            tables["source_anchor_links"],
            tables["anchor_fact_relationships"],
            registry,
            self.policy,
        )
        queries = self.build_load_queries()

        self.assertEqual(plan["status"], "READY")
        self.assertFalse(plan["validation_errors"])
        self.assertIn("ANCHOR_FACT", queries["anchor_facts"])
        self.assertIn("FACT_RELATION", queries["canonical_facts"])

        invalid_anchor_facts = tables[
            "anchor_fact_relationships"
        ].copy()
        invalid_anchor_facts.loc[0, "end_anchor_id"] = "UNKNOWN"
        blocked_plan = self.build_load_plan(
            canonical_facts,
            tables["anchor_nodes"],
            source_nodes,
            tables["canonical_anchor_links"],
            tables["source_anchor_links"],
            invalid_anchor_facts,
            registry,
            self.policy,
        )
        self.assertEqual(blocked_plan["status"], "BLOCKED")

    def test_load_batch_rejects_silent_match_drop(self):
        class EmptyResult:
            def single(self) -> dict[str, int]:
                return {"loaded_count": 0}

        class EmptyTransaction:
            def run(self, query: str, **parameters: object) -> EmptyResult:
                return EmptyResult()

        table = pd.DataFrame([{"id": "ROW-1"}])
        with self.assertRaises(RuntimeError):
            self.execute_verified_load_batches(
                EmptyTransaction(),
                "RETURN 0 AS loaded_count",
                table,
                100,
                "test-scope",
                "test-run",
            )


if __name__ == "__main__":
    unittest.main()

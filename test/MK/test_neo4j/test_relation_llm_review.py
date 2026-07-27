from __future__ import annotations

import unittest

from etl.preprocessing.neo4j.fact_retrieval.relation_llm_review import (
    compile_final_decisions,
    summarize_usage,
)


class RelationLlmReviewTest(unittest.TestCase):
    def build_task(self, start_kind: str = "CANONICAL") -> dict:
        return {
            "relation_review_task_id": "relation-review:test",
            "fact_id": "fact:test",
            "candidate_id": "candidate:test",
            "review_origin": "STRICT_RELATION_REVIEW",
            "relation": {"display": "가 -[BUILT]-> 나", "type": "BUILT"},
            "start": {
                "node_id": "start:test",
                "node_kind": start_kind,
                "display_name": "가",
                "proposed_entity_type": "Person",
            },
            "end": {
                "node_id": "end:test",
                "node_kind": "CANONICAL",
                "display_name": "나",
                "proposed_entity_type": "Heritage",
            },
            "evidence": {
                "source_dataset": "AKS",
                "source_document_id": "source:test",
                "source_title": "가",
                "atomic_clause": "가가 나를 세웠다.",
                "sentence": "가가 나를 세웠다.",
            },
        }

    def build_evaluation(self, verdict: str = "VERIFIED") -> dict:
        return {
            "relation_review_task_id": "relation-review:test",
            "model": "gpt-5.5",
            "decision": {
                "verdict": verdict,
                "subject_explicit": True,
                "object_explicit": True,
                "predicate_supported": True,
                "causative_or_indirect": False,
                "negated_hypothetical_or_quoted": False,
                "start_type_supported": True,
                "end_type_supported": True,
                "start_graph_entity": True,
                "end_graph_entity": True,
                "safe_for_one_hop_retrieval": True,
                "safe_for_multi_hop_retrieval": True,
                "reason_codes": ["DIRECT_RELATION"],
                "rationale": "직접 관계",
            },
        }

    def test_open_endpoint_is_limited_to_one_hop(self) -> None:
        records = compile_final_decisions(
            tasks=[self.build_task("OPEN_ENTITY_CANDIDATE")],
            evaluation_records=[self.build_evaluation()],
        )
        self.assertEqual(records[0]["final_status"], "REVIEWED_APPROVED_ONE_HOP")

    def test_canonical_endpoints_can_be_multi_hop(self) -> None:
        records = compile_final_decisions(
            tasks=[self.build_task()],
            evaluation_records=[self.build_evaluation()],
        )
        self.assertEqual(records[0]["final_status"], "REVIEWED_APPROVED_MULTI_HOP")

    def test_uncertain_evaluation_stays_manual_review(self) -> None:
        records = compile_final_decisions(
            tasks=[self.build_task()],
            evaluation_records=[self.build_evaluation("NEEDS_REVIEW")],
        )
        self.assertEqual(records[0]["final_status"], "NEEDS_MANUAL_REVIEW")

    def test_usage_cost_uses_cached_input_price(self) -> None:
        records = [
            {
                "model": "gpt-5.4-mini",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 500},
                    "output_tokens_details": {"reasoning_tokens": 50},
                },
            }
        ]
        pricing = {
            "gpt-5.4-mini": {
                "input": 0.75,
                "cached_input": 0.075,
                "output": 4.5,
            }
        }
        summary = summarize_usage(records, pricing)
        self.assertEqual(summary["estimated_total_cost_usd"], 0.000862)


if __name__ == "__main__":
    unittest.main()

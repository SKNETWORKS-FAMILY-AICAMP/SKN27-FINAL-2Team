from __future__ import annotations

import copy
import unittest

from ai.pack_generation.builder import validate_pack_bank
from ai.pack_generation.graph_builder import build_pack, select_review_candidates, validate_spec


def pack() -> dict:
    return {
        "family_id": "family:1",
        "status": "final_reviewed",
        "difficulty": 2,
        "era": "조선",
        "relation_axis_id": "person.activity_achievement",
        "topic_type": "인물",
        "answer_eligible_owner_ids": [f"owner:{index}" for index in range(9)],
        "question_frames": [
            {"question_task": "standard_select", "stem_pattern": "target_description", "material_type": "자료 제시문", "major_type": "역사 자료의 분석 및 해석", "minor_type": "자료 기반 시대·대상 추론", "question_task_instruction": "검증된 지시", "distractor_type": "same_category_wrong_target", "answer_owner_scope": "material_target"},
            {"question_task": "standard_select", "stem_pattern": "activity_achievement", "material_type": "탐구 자료", "major_type": "역사 자료의 분석 및 해석", "minor_type": "자료 기반 시대·대상 추론", "question_task_instruction": "검증된 지시", "distractor_type": "same_category_wrong_target", "answer_owner_scope": "material_target"},
        ],
        "members": [
            {
                "choice_fact_id": f"fact:{index}",
                "owner_id": f"owner:{index}",
                "owner_label": f"대상 {index}",
                "owner_type": "인물",
                "fact_basis": f"대상 {index}의 고유 사실이다.",
                "fact_evidence_chunks": [{"chunk_id": f"fact:{index}", "article_id": f"owner:{index}"}],
                "material_clue_basis": f"대상 {index}을 식별하는 단서이다.",
                "material_evidence_chunks": [{"chunk_id": f"material:{index}", "article_id": f"owner:{index}"}],
            }
            for index in range(9)
        ],
    }


class PackValidationTest(unittest.TestCase):
    def test_prepared_pack_is_validated_without_candidate_selection(self) -> None:
        validate_pack_bank([pack()])
        duplicate = copy.deepcopy(pack())
        duplicate["family_id"] = "family:2"
        with self.assertRaisesRegex(ValueError, "fact reused"):
            validate_pack_bank([pack(), duplicate])

    def test_graph_review_builds_only_nine_direct_canonical_members(self) -> None:
        spec = {
            "anchor_node_id": "topic:person",
            "candidate_hops": 2,
            "topic_id": "topic:person",
            "era_id": "era:test",
            "era": "조선",
            "era_criteria": "검증 기준",
            "owner_type": "Person",
            "rag_owner_type": "인물",
            "relation_axis_id": "person.activity_achievement",
            "topic_type": "인물",
            "difficulty": 2,
            "question_frames": pack()["question_frames"],
        }
        candidates = [
            {
                "owner_entity_id": f"canonical:person:{index}",
                "article_id": f"E{index:07d}",
                "owner_label": f"인물 {index}",
                "owner_type": "인물/전통 인물",
                "graph_release_id": "release:test",
                "candidate_distance": 2,
                "evidence_candidates": [
                    {"chunk_id": f"fact:{index}", "article_id": f"E{index:07d}"},
                    {"chunk_id": f"material:{index}", "article_id": f"E{index:07d}"},
                ],
            }
            for index in range(9)
        ]
        review = {
            "members": [
                {
                    "owner_id": f"E{index:07d}",
                    "fact_basis": f"인물 {index}의 검증된 활동이다. 해당 활동은 조선 시대에 이루어졌다.",
                    "fact_evidence_chunk_ids": [f"fact:{index}"],
                    "material_clue_basis": f"인물 {index}의 별도 단서이다. 이 단서는 활동 사실과 다르다.",
                    "material_evidence_chunk_ids": [f"material:{index}"],
                    "approved": True,
                }
                for index in range(9)
            ]
        }
        result = build_pack(spec, candidates, review, "test-model")
        validate_pack_bank([result])
        self.assertEqual(result["status"], "final_reviewed")
        self.assertEqual(len(result["answer_eligible_owner_ids"]), 9)
        self.assertEqual(result["graph_source"]["candidate_hops"], 2)
        self.assertFalse(result["members"][0]["material_fact_semantically_distinct"])
        self.assertNotIn("graph_predicate", result["members"][0])
        self.assertEqual(len(select_review_candidates(spec, candidates, 9)), 9)

        shared_candidates = copy.deepcopy(candidates)
        shared_review = copy.deepcopy(review)
        shared_review["members"][0]["material_evidence_chunk_ids"] = ["fact:0"]
        with self.assertRaisesRegex(ValueError, "invalid or unsafe"):
            build_pack(spec, shared_candidates, shared_review, "test-model")
        shared_review["members"][0]["material_fact_semantically_distinct"] = True
        shared_result = build_pack(spec, shared_candidates, shared_review, "test-model")
        self.assertTrue(shared_result["members"][0]["material_fact_semantically_distinct"])

    def test_graph_hops_must_match_difficulty(self) -> None:
        spec = {
            "anchor_node_id": "topic:person",
            "candidate_hops": 2,
            "topic_id": "topic:person",
            "era_id": "era:test",
            "era": "조선",
            "era_criteria": "검증 기준",
            "owner_type": "Person",
            "rag_owner_type": "인물",
            "relation_axis_id": "person.activity_achievement",
            "topic_type": "인물",
            "difficulty": 1,
            "question_frames": pack()["question_frames"],
        }
        with self.assertRaisesRegex(ValueError, "candidate_hops"):
            validate_spec(spec)


if __name__ == "__main__":
    unittest.main()

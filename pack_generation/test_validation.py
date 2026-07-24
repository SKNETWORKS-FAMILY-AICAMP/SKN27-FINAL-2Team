from __future__ import annotations

import copy
import unittest

from pack_generation.builder import validate_pack_bank


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


if __name__ == "__main__":
    unittest.main()

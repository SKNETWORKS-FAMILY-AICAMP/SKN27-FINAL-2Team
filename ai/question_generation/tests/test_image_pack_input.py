from __future__ import annotations

import copy
import unittest

from ai.question_generation.core.contracts import validate_pack
from ai.question_generation.retrieval.image_pack_input import build_input, build_inputs


def image_source() -> dict:
    members = [
        {
            "choice_fact_id": f"fact:{index}",
            "owner_id": f"owner:{index}",
            "owner_label": f"유산 {index}",
            "fact_basis": f"유산 {index}의 검증된 특징이다.",
            "service_era": "남북국 시대",
            "service_topic": "문화",
            "evidence_chunks": [{"chunk_id": f"fact:{index}", "article_id": f"owner:{index}"}],
            "image": {
                "image_chunk_id": f"image:{index}",
                "visual_clue_basis": f"검증된 시각 특징 {index}",
                "source_url": f"https://example.com/source/{index}",
                "original_image_url": f"https://example.com/image/{index}.jpg",
            },
        }
        for index in range(9)
    ]
    return {
        "family_id": "image:test",
        "difficulty": 2,
        "era": "고대",
        "topic_type": "문화유산",
        "answer_owner_id": "owner:3",
        "distractor_owner_ids": ["owner:0", "owner:2", "owner:5", "owner:7"],
        "frame_id": "frame:1",
        "members": members,
        "question_frames": [{
            "frame_id": "frame:1",
            "choice_mode": "generated",
            "question_task": "standard_select",
            "stem_pattern": "target_description",
            "relation_axis_id": "visual.feature_identification",
            "material_type": "탐구 자료",
            "major_type": "시각 자료의 분석 및 해석",
            "minor_type": "시각 자료 기반 대상 추론",
            "service_question_type": "역사 자료의 분석 및 해석",
            "service_question_subtype": "시각 자료 해석",
            "question_task_instruction": "검증된 이미지 발문 지시",
            "distractor_type": "same_category_wrong_target",
        }],
    }


class ImagePackInputTest(unittest.TestCase):
    def test_explicit_owners_and_frame_are_required(self) -> None:
        source = image_source()
        pack = validate_pack(build_input(source))
        self.assertEqual(pack["items"][0]["article_id"], "owner:3")
        self.assertEqual([item["article_id"] for item in pack["items"][1:]], source["distractor_owner_ids"])
        self.assertEqual(pack["service_era"], "남북국 시대")
        self.assertEqual(pack["service_question_subtype"], "시각 자료 해석")
        broken = copy.deepcopy(source)
        broken.pop("answer_owner_id")
        with self.assertRaisesRegex(ValueError, "answer_owner_id"):
            build_input(broken)

    def test_reviewed_image_choice_rotations_become_generation_packs(self) -> None:
        members = [
            {
                "owner_id": f"owner:{index}",
                "owner_label": f"유산 {index}",
                "era": "고대",
                "service_era": "삼국 시대",
                "service_topic": "문화",
                "material_clue_sources": [{
                    "clue_source_id": f"clue:{index}",
                    "basis": f"유산 {index}을 식별하는 검수 단서",
                    "evidence_chunk_ids": [f"chunk:{index}"],
                }],
                "choice_image": {
                    "image_chunk_id": f"image:{index}",
                    "original_image_url": f"https://example.com/{index}.jpg",
                },
            }
            for index in range(9)
        ]
        source = {
            "family_id": "image_choice:test",
            "choice_mode": "image",
            "difficulty": 3,
            "topic_type": "문화유산",
            "relation_axis_id": "common.definition_feature",
            "validation": {
                "member_count": 9,
                "distinct_owner_count": 9,
                "distinct_image_count": 9,
                "unique_answer_contract": "pass",
            },
            "members": members,
            "rotation_compatibility": [
                {
                    "answer_owner_id": member["owner_id"],
                    "eligible_distractor_owner_ids": [
                        other["owner_id"] for other in members if other is not member
                    ],
                    "status": "pass",
                }
                for member in members
            ],
            "question_frames": [{
                "frame_id": "image_choice_description",
                "choice_mode": "image",
                "question_task": "standard_select",
                "stem_pattern": "target_description",
                "relation_axis_id": "common.definition_feature",
                "material_type": "자료 제시문",
                "major_type": "역사 자료의 분석 및 해석",
                "minor_type": "시각 자료 해석",
                "service_question_type": "역사 자료의 분석 및 해석",
                "service_question_subtype": "시각 자료 해석",
                "question_task_instruction": "검수된 단서로 대상을 묻는다.",
            }],
        }
        packs = [validate_pack(pack) for pack in build_inputs(source)]
        self.assertEqual(len(packs), 9)
        self.assertEqual(len({pack["variant_key"] for pack in packs}), 9)
        self.assertTrue(all(pack["choice_mode"] == "image" for pack in packs))


if __name__ == "__main__":
    unittest.main()

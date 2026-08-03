import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai.question_generation.automated_spec_planner import (
    plan_automated_specs,
    read_reviewed_generation_contracts,
)


def frame_contract(contract_index: int, stem_pattern: str) -> dict:
    return {
        "contract_index": contract_index,
        "relation_axis_id": "person.activity_achievement",
        "topic_type": "인물",
        "stem_pattern": stem_pattern,
        "material_type": "자료 제시문",
        "major_type": "역사 자료의 분석 및 해석",
        "minor_type": "자료 기반 시대·대상 추론",
    }


class AutomatedSpecPlannerTests(unittest.TestCase):
    def test_reviewed_pack_bank_provides_generation_contracts(self) -> None:
        bank = {
            "packs": [
                {
                    "topic_type": "인물",
                    "question_frames": [
                        {
                            "question_task": "standard_select",
                            "material_type": "자료 제시문",
                            "major_type": "역사 자료의 분석 및 해석",
                            "minor_type": "자료 기반 시대·대상 추론",
                            "question_task_instruction": "검수된 단서로 대상을 판단한다.",
                            "distractor_type": "same_category_wrong_target",
                        }
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pack_bank.json"
            path.write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
            contracts = read_reviewed_generation_contracts(path)

        self.assertEqual(contracts["distractors"][0]["topic_type"], "인물")
        self.assertEqual(
            contracts["instructions"][0]["question_task_instruction"],
            "검수된 단서로 대상을 판단한다.",
        )

    @patch("ai.question_generation.automated_spec_planner.plan_pack_spec")
    @patch("ai.question_generation.automated_spec_planner.planning_contracts")
    @patch("ai.question_generation.automated_spec_planner.read_graph_candidates")
    @patch("ai.question_generation.automated_spec_planner.graph_options")
    @patch("ai.question_generation.automated_spec_planner.read_reviewed_generation_contracts")
    def test_planning_balances_difficulty_and_inverts_hops(
        self,
        generation_contracts,
        options,
        candidates,
        contracts,
        plan,
    ) -> None:
        generation_contracts.return_value = {"distractors": [], "instructions": []}
        options.return_value = [
            {
                "era_id": "era:goryeo",
                "era_name": "고려",
                "topic_id": "topic:person",
                "topic_name": "인물",
                "owner_type": "Person",
            }
        ]
        candidates.return_value = [{"article_id": f"E{index:07d}"} for index in range(9)]
        contracts.return_value = {
            "service_eras": ["고려"],
            "service_topics": ["인물"],
            "rag_owner_types": ["인물"],
            "frame_contracts": [
                frame_contract(1, "target_description"),
                frame_contract(2, "activity_achievement"),
            ],
            "distractor_contracts": [
                {
                    "topic_type": "인물",
                    "material_type": "자료 제시문",
                    "major_type": "역사 자료의 분석 및 해석",
                    "minor_type": "자료 기반 시대·대상 추론",
                    "distractor_type": "same_category_wrong_target",
                }
            ],
            "instruction_contracts": [
                {
                    "topic_type": "인물",
                    "question_task": "standard_select",
                    "material_type": "자료 제시문",
                    "major_type": "역사 자료의 분석 및 해석",
                    "minor_type": "자료 기반 시대·대상 추론",
                    "question_task_instruction": "검수된 단서로 대상을 판단한다.",
                }
            ],
        }
        plan.return_value = {
            "era": "고려",
            "service_era": "고려",
            "era_criteria": "고려 시대 사실만 선택한다.",
            "rag_owner_type": "인물",
            "relation_axis_id": "person.activity_achievement",
            "topic_type": "인물",
            "question_frames": [{"contract_index": 1}, {"contract_index": 2}],
        }

        specs = plan_automated_specs(3, "model", Path("bank.json"), 7, "https://example.com", "key")

        self.assertEqual([spec["difficulty"] for spec in specs], [1, 2, 3])
        self.assertEqual([spec["candidate_hops"] for spec in specs], [3, 2, 1])
        self.assertTrue(
            all(frame["question_task_instruction"] for spec in specs for frame in spec["question_frames"])
        )


if __name__ == "__main__":
    unittest.main()

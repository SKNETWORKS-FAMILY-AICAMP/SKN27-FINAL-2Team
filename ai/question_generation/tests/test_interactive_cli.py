import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai.question_generation.interactive_cli import build_planned_spec, create_graph_packs, run_image_packs


class InteractiveCliTests(unittest.TestCase):
    def test_planner_can_only_fill_contract_fields(self):
        selection = {
            "topic_id": "topic:person",
            "topic_name": "인물",
            "era_id": "era:goryeo",
            "owner_type": "Person",
            "difficulty": 2,
        }
        contracts = {
            "service_eras": ["고려"],
            "service_topics": ["인물"],
            "rag_owner_types": ["인물"],
            "distractor_contracts": [
                {
                    "topic_type": "인물",
                    "material_type": "탐구 자료",
                    "major_type": "역사 탐구의 설계 및 수행",
                    "minor_type": "탐구 주제·활동 선정",
                    "distractor_type": "same_category_wrong_target",
                },
                {
                    "topic_type": "인물",
                    "material_type": "자료 제시문",
                    "major_type": "역사 자료의 분석 및 해석",
                    "minor_type": "자료 기반 시대·대상 추론",
                    "distractor_type": "same_category_wrong_target",
                },
            ],
            "instruction_contracts": [
                {
                    "topic_type": "인물",
                    "question_task": "standard_select",
                    "material_type": "탐구 자료",
                    "major_type": "역사 탐구의 설계 및 수행",
                    "minor_type": "탐구 주제·활동 선정",
                    "question_task_instruction": "대상의 활동을 판단하게 한다.",
                },
                {
                    "topic_type": "인물",
                    "question_task": "standard_select",
                    "material_type": "자료 제시문",
                    "major_type": "역사 자료의 분석 및 해석",
                    "minor_type": "자료 기반 시대·대상 추론",
                    "question_task_instruction": "자료의 빈칸 대상을 판단하게 한다.",
                },
            ],
            "frame_contracts": [
                {
                    "contract_index": 1,
                    "relation_axis_id": "person.activity_achievement",
                    "topic_type": "인물",
                    "stem_pattern": "activity_achievement",
                    "material_type": "탐구 자료",
                    "major_type": "역사 탐구의 설계 및 수행",
                    "minor_type": "탐구 주제·활동 선정",
                },
                {
                    "contract_index": 2,
                    "relation_axis_id": "person.activity_achievement",
                    "topic_type": "인물",
                    "stem_pattern": "fill_blank",
                    "material_type": "자료 제시문",
                    "major_type": "역사 자료의 분석 및 해석",
                    "minor_type": "자료 기반 시대·대상 추론",
                },
            ],
        }
        plan = {
            "era": "고려",
            "service_era": "고려",
            "era_criteria": "고려 시대에 해당하는 사실만 선택한다.",
            "rag_owner_type": "인물",
            "relation_axis_id": "person.activity_achievement",
            "topic_type": "인물",
            "service_topic": "인물",
            "question_frames": [
                {
                    "contract_index": 1,
                    "question_task_instruction": "대상의 활동을 판단하게 한다.",
                    "distractor_type": "same_category_wrong_target",
                },
                {
                    "contract_index": 2,
                    "question_task_instruction": "자료의 빈칸 대상을 판단하게 한다.",
                    "distractor_type": "same_category_wrong_target",
                },
            ],
        }

        spec = build_planned_spec(selection, contracts, plan)

        self.assertEqual(spec["anchor_node_id"], "topic:person")
        self.assertEqual(spec["candidate_hops"], 2)
        self.assertEqual(spec["era_id"], "era:goryeo")
        self.assertEqual({frame["answer_owner_scope"] for frame in spec["question_frames"]}, {"material_target"})

    @patch("ai.question_generation.interactive_cli.subprocess.run", return_value=Mock(returncode=0))
    @patch("ai.question_generation.interactive_cli.yes", return_value=True)
    @patch("ai.question_generation.interactive_cli.plan_graph_pack_spec")
    @patch("ai.question_generation.interactive_cli.v41_generation_contracts", return_value={"contracts": []})
    @patch("ai.question_generation.interactive_cli.graph_options", return_value=[])
    def test_requested_graph_pack_count_is_written(
        self,
        _graph_options,
        _generation_contracts,
        plan_spec,
        _yes,
        run,
    ):
        base = {
            "candidate_hops": 1,
            "era_id": "era:test",
            "owner_type": "Person",
            "relation_axis_id": "person.activity_achievement",
            "topic_type": "인물",
            "question_frames": [
                {
                    "stem_pattern": "activity_achievement",
                    "material_type": "자료 제시문",
                    "major_type": "역사 자료의 분석 및 해석",
                    "minor_type": "자료 기반 시대·대상 추론",
                }
            ],
        }
        plan_spec.side_effect = [
            {**base, "anchor_node_id": "topic:a"},
            {**base, "anchor_node_id": "topic:b"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = create_graph_packs(
                Path(directory),
                None,
                "model",
                Path("validation.jsonl"),
                2,
            )
            specs = json.loads(output.with_name("spec.json").read_text(encoding="utf-8"))["packs"]

        self.assertEqual([row["anchor_node_id"] for row in specs], ["topic:a", "topic:b"])
        self.assertIn("ai.pack_generation.graph_builder", run.call_args.args[0])

    @patch("ai.question_generation.interactive_cli.subprocess.run", return_value=Mock(returncode=0))
    def test_new_image_manifest_can_run_image_only(self, run):
        code = run_image_packs(
            Path("reviewed.json"),
            Path("manifest.json"),
            Path("output"),
            Path("usage.json"),
            7,
            11,
            evaluate=True,
            dry_run=False,
        )
        command = run.call_args.args[0]

        self.assertEqual(code, 0)
        self.assertIn("--image-only", command)
        self.assertEqual(command[command.index("--image-count") + 1], "7")


if __name__ == "__main__":
    unittest.main()

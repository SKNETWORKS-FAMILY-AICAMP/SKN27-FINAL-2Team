"""문제은행 파이프라인의 핵심 데이터 계약과 호출 분리를 검증하는 회귀 테스트."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from argparse import Namespace
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from ai.question_generation.core.exam_distribution import apportion
from ai.question_generation.retrieval.closed_pack_bank import build_reviewed_packs, read_frames
from ai.question_generation.retrieval.closed_pack_input import build_generation_pack, plan_variants, select_closed_pack
from ai.question_generation.core.contracts import generation_item, validate_pack
from ai.question_generation.evaluation.v18 import build_messages, evaluation_few_shot_messages, normalize_gate, pending_records, post_chat, records_from_assembled
from ai.question_generation.generation.assemble import assemble_question, validate_question
from ai.question_generation.generation.material import generate_material, material_few_shot_messages
from ai.question_generation.generation.material_rules import (
    DEFAULT_MATERIAL_EXAMPLES,
    choose_material_examples,
    load_json_dict,
    material_type_format_status,
    material_type_rules_text,
)
from ai.question_generation.generation.material_validation import material_contract_status
from ai.question_generation.generation.sllm_inputs import clean_basis_text, correct_record, distractor_record
from ai.question_generation.generation.sllm_transport import clean_model_text
from ai.question_generation.workflows.question_pipeline import (
    CallBudget,
    PipelineLimitError,
    call_choice_model,
    call_sllm,
    generate_material_stage,
    generate_v41_question_stage,
    invalidate,
    material_evidence_usage_status,
    material_question_error,
    new_state,
    parse_args,
    prepare_failed_resume,
    select_question_stage,
    selected_question,
)
from ai.question_generation.workflows.closed_pack_batch import (
    assign_material_frames,
    completed_checkpoint,
    evaluation_accepted,
    evaluation_repair_feedback,
    evaluate_questions,
    exam_era_quotas,
    load_repair_actions,
    material_type_targets,
    next_repair_cycle,
    prepare_evaluation_repair,
    question_args,
    replace_closed_with_images,
    render_markdown,
    select_exam_packs,
    select_image_packs,
)
from ai.question_generation.workflows.source_repair import apply_override


def basis_pack() -> dict:
    """테스트에서 공통으로 사용하는 최소 5선지 승인 pack을 만든다."""
    return {
        "pack_id": "pack:test",
        "target_label": "김홍집",
        "topic_type": "인물",
        "service_era": "조선",
        "service_topic": "정치",
        "service_question_type": "역사 자료의 분석 및 해석",
        "service_question_subtype": "자료 기반 시대·대상 추론",
        "question_task": "standard_select",
        "question_task_instruction": "검증된 V41 출제 지시",
        "distractor_type": "same_category_wrong_target",
        "stem_pattern": "activity_achievement",
        "relation_axis_id": "person.activity_achievement",
        "material_type": "자료 제시문",
        "major_type": "역사 자료의 분석 및 해석",
        "minor_type": "자료 기반 시대·대상 추론",
        "difficulty_label": "보통",
        "material_clue_basis": "김홍집은 조선 말 개화 정책 추진 과정에서 중요한 역할을 담당하였다.",
        "material_evidence_chunks": [{"chunk_id": "chunk:clue", "article_id": "article:0", "exact_text": "별도 식별 단서"}],
        "status": "rag_ready",
        "semantic_status": "pass",
        "items": [
            {
                "slot_no": slot,
                "basis_item_id": slot + 100,
                "role": "answer" if slot == 0 else "distractor",
                "article_id": f"article:{slot}",
                "truth_owner_label": f"인물 {slot}",
                "fact_basis": f"인물 {slot}의 검증된 사실이다.",
                "evidence_chunks": [{"chunk_id": f"chunk:{slot}", "article_id": f"article:{slot}", "exact_text": "검증 근거"}],
                "status": "rag_ready",
                "semantic_status": "pass",
            }
            for slot in range(5)
        ],
    }


class QuestionBankPipelineTest(unittest.TestCase):
    """팩 검증, 호출 분리, 재시도, 평가 입력의 핵심 회귀 조건을 검증한다."""

    def test_frame_candidates_do_not_fallback_across_eras(self) -> None:
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def execute(self, *_):
                return None

            def fetchall(self):
                return [
                    ("보통", "조선", "person.activity_achievement", "standard_select", "activity_achievement", "자료 제시문", 2),
                    ("보통", "조선", "person.activity_achievement", "standard_select", "fill_blank", "짧은 설명 자료", 1),
                ]

        frames, _ = read_frames(type("Connection", (), {"cursor": lambda self: Cursor()})())
        self.assertIn((2, "조선", "person.activity_achievement"), frames)
        self.assertNotIn((2, "고려", "person.activity_achievement"), frames)

    def test_completed_checkpoint_skips_only_complete_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "item.json"
            self.assertFalse(completed_checkpoint(path, "variant:a"))
            path.write_text('{"status":"prepared","input":{"variant_key":"variant:a"}}', encoding="utf-8")
            self.assertFalse(completed_checkpoint(path, "variant:a"))
            path.write_text('{"status":"complete","input":{"variant_key":"variant:a"}}', encoding="utf-8")
            self.assertTrue(completed_checkpoint(path, "variant:a"))
            self.assertFalse(completed_checkpoint(path, "variant:b"))

    def test_single_pipeline_requires_explicit_paths(self) -> None:
        with patch("sys.argv", ["run_question_bank_pipeline.py"]), self.assertRaises(SystemExit):
            parse_args()

    def test_closed_pack_builder_keeps_nine_distinct_owners(self) -> None:
        members = [
            {
                "difficulty": 1,
                "era": "조선",
                "relation_axis_id": "person.activity_achievement",
                "choice_fact_id": f"fact:{index}",
                "owner_id": f"owner:{index}",
                "owner_label": f"인물 {index}",
                "fact_evidence_chunks": [{"chunk_id": f"fact-chunk:{index}", "article_id": f"owner:{index}"}],
                "material_clue_basis": f"별도 단서 {index}",
                "material_evidence_chunks": [{"chunk_id": f"clue-chunk:{index}", "article_id": f"owner:{index}"}],
            }
            for index in range(9)
        ]
        frames = {
            (1, "조선", "person.activity_achievement"): [
                {"question_task": "standard_select", "stem_pattern": "activity_achievement", "material_type": "자료 제시문", "answer_owner_scope": "material_target"},
                {"question_task": "standard_select", "stem_pattern": "fill_blank", "material_type": "자료 제시문", "answer_owner_scope": "material_target"},
            ]
        }
        self.assertEqual(sum(apportion(Counter({"조선": 3, "고려": 1}), 10).values()), 10)

        reviewed = build_reviewed_packs(
            members,
            {
                "packs": [{
                    "difficulty": 1,
                    "era": "조선",
                    "relation_axis_id": "person.activity_achievement",
                    "topic_type": "인물",
                    "question_frames": frames[(1, "조선", "person.activity_achievement")],
                    "choice_fact_ids": [f"fact:{index}" for index in range(9)],
                    "material_overrides": {
                        "fact:0": {
                            "material_clue_basis": "검수자가 고른 별도 단서",
                            "material_evidence_chunk_ids": ["reviewed-chunk"],
                        }
                    },
                }]
            },
            {"reviewed-chunk": {"chunk_id": "reviewed-chunk", "article_id": "owner:0"}},
        )
        self.assertEqual(reviewed[0]["status"], "pending_user_review")
        self.assertEqual(reviewed[0]["members"][0]["material_clue_basis"], "검수자가 고른 별도 단서")

        rag_reviewed = build_reviewed_packs(
            members,
            {
                "packs": [{
                    "difficulty": 1,
                    "era": "조선",
                    "relation_axis_id": "person.activity_achievement",
                    "topic_type": "인물",
                    "question_frames": frames[(1, "조선", "person.activity_achievement")],
                    "choice_fact_ids": [f"fact:{index}" for index in range(8)],
                    "additional_members": [{
                        "member_id": "rag-fact:8",
                        "owner_id": "rag-owner:8",
                        "owner_label": "추가 인물",
                        "owner_type": "인물",
                        "fact_basis": "추가 인물은 검증된 활동을 하였다.",
                        "fact_evidence_chunk_ids": ["rag-fact-chunk"],
                        "material_clue_basis": "추가 인물은 별도의 경력을 지녔다.",
                        "material_evidence_chunk_ids": ["rag-clue-chunk"],
                    }],
                }]
            },
            {
                "rag-fact-chunk": {"chunk_id": "rag-fact-chunk", "article_id": "rag-owner:8"},
                "rag-clue-chunk": {"chunk_id": "rag-clue-chunk", "article_id": "rag-owner:8"},
            },
        )
        self.assertEqual(rag_reviewed[0]["members"][-1]["source_type"], "reviewed_rag")

    def test_closed_pack_adapter_builds_current_five_choice_contract(self) -> None:
        members = [
            {
                "choice_fact_id": f"fact:{index}",
                "owner_id": f"owner:{index}",
                "owner_label": f"인물 {index}",
                "owner_type": "인물/전통 인물",
                "fact_basis": f"인물 {index}의 검증된 사실이다.",
                "fact_evidence_chunks": [{"chunk_id": f"fact-chunk:{index}", "article_id": f"owner:{index}"}],
                "material_clue_basis": f"인물 {index}의 별도 식별 단서이다.",
                "material_evidence_chunks": [{"chunk_id": f"clue-chunk:{index}", "article_id": f"owner:{index}"}],
                "material_evidence_disjoint": True,
            }
            for index in range(9)
        ]
        closed = {
            "family_id": "closed:test",
            "status": "final_reviewed",
            "difficulty": 3,
            "era": "조선",
            "relation_axis_id": "person.policy_reform",
            "topic_type": "인물",
            "question_frames": [{"question_task": "standard_select", "stem_pattern": "policy_system", "material_type": "자료 제시문", "major_type": "역사 자료의 분석 및 해석", "minor_type": "자료 기반 시대·대상 추론", "answer_owner_scope": "material_target", "question_task_instruction": "검증된 V41 출제 지시", "distractor_type": "same_category_wrong_target"}],
            "answer_eligible_owner_ids": [f"owner:{index}" for index in range(9)],
            "members": members,
        }
        closed["question_frames"].append({**closed["question_frames"][0], "material_type": "탐구 자료"})
        selected = select_closed_pack({"packs": [closed]}, "closed:test")
        pack = build_generation_pack(selected, answer_owner_id="owner:3", seed=7)
        validated = validate_pack(pack)
        self.assertEqual(validated["items"][0]["article_id"], "owner:3")
        self.assertEqual(pack["target_label"], "인물 3")
        self.assertEqual(pack["topic_type"], "인물")
        self.assertEqual(len({item["article_id"] for item in validated["items"]}), 5)
        self.assertEqual(generation_item(validated)["target_score"], 3)

        variants = plan_variants(closed, 18, seed=7)
        self.assertEqual({row["answer_owner_id"] for row in variants[:9]}, {f"owner:{index}" for index in range(9)})
        self.assertEqual({row["answer_owner_id"] for row in variants[9:]}, {f"owner:{index}" for index in range(9)})
        self.assertEqual(len({row["variant_key"] for row in variants}), 18)
        first_answers = {plan_variants(closed, 1, seed=seed)[0]["answer_owner_id"] for seed in range(20)}
        self.assertGreater(len(first_answers), 1)
        self.assertEqual(plan_variants(closed, 1, seed=7), plan_variants(closed, 1, seed=7))
        explicit = build_generation_pack(
            closed,
            answer_owner_id=variants[0]["answer_owner_id"],
            distractor_owner_ids=variants[0]["distractor_owner_ids"],
            frame_index=variants[0]["frame_index"],
        )
        self.assertEqual(
            [item["article_id"] for item in explicit["items"][1:]],
            variants[0]["distractor_owner_ids"],
        )

    def test_closed_pack_rejects_cross_owner_question_frame(self) -> None:
        closed = {
            "family_id": "closed:cross-owner",
            "difficulty": 3,
            "era": "조선",
            "relation_axis_id": "chronology.before_after",
            "question_frames": [{
                "question_task": "standard_select",
                "stem_pattern": "before_after",
                "material_type": "자료 제시문",
                "answer_owner_scope": "independent",
            }],
            "members": [
                {
                    "choice_fact_id": f"fact:{index}",
                    "owner_id": f"owner:{index}",
                    "owner_label": f"사건 {index}",
                }
                for index in range(9)
            ],
        }
        with self.assertRaisesRegex(ValueError, "final_reviewed"):
            plan_variants(closed, 1)

    def test_mock_exam_plan_uses_official_era_quota_and_unique_families(self) -> None:
        records = [
            {"input": {"target_score": 1, "era": "조선 후기"}},
            {"input": {"target_score": 2, "era": "고려 시대"}},
            {"input": {"target_score": 3, "era": "삼국 시대"}},
        ]
        quotas = exam_era_quotas(records, {1: 1, 2: 1, 3: 1})
        packs = [
            {"family_id": "family:1", "difficulty": 1, "era": "조선"},
            {"family_id": "family:2", "difficulty": 2, "era": "고려"},
            {"family_id": "family:3", "difficulty": 3, "era": "고대"},
        ]
        selected = select_exam_packs(packs, quotas, seed=7)
        self.assertEqual(len(selected), 3)
        self.assertEqual(len({pack["family_id"] for pack in selected}), 3)

    def test_mock_exam_selects_images_without_breaking_quota_cells(self) -> None:
        quotas = {
            1: {"조선": 1},
            2: {"고려": 1},
            3: {"고대": 1},
        }
        images = [
            {"family_id": "image:1", "difficulty": 1, "era": "조선"},
            {"family_id": "image:2", "difficulty": 2, "era": "고려"},
            {"family_id": "image:3", "difficulty": 3, "era": "고대"},
        ]
        selected = select_image_packs(images, quotas, 1, 3, seed=7)
        self.assertTrue(1 <= len(selected) <= 3)
        self.assertEqual(len({row["family_id"] for row in selected}), len(selected))

    def test_image_replacement_preserves_count_and_quota_cells(self) -> None:
        plan = [
            {"family_id": f"closed:{index}", "difficulty": 2, "era": "조선", "source_kind": "closed"}
            for index in range(3)
        ]
        images = [{"family_id": "image:1", "difficulty": 2, "era": "조선", "source_kind": "image"}]
        replace_closed_with_images(plan, images, seed=7)
        self.assertEqual(len(plan), 3)
        self.assertEqual(sum(row["source_kind"] == "image" for row in plan), 1)
        self.assertTrue(all((row["difficulty"], row["era"]) == (2, "조선") for row in plan))

    def test_material_frame_assignment_uses_only_pack_frames(self) -> None:
        materials = ["자료 제시문", "짧은 설명 자료", "탐구 자료"]
        self.assertEqual(material_type_targets(dict(zip(materials, (60, 25, 15))), 50), dict(zip(materials, (30, 13, 7))))
        packs = [
            {
                "family_id": f"family:{index}",
                "question_frames": [
                    {"material_type": material, "stem_pattern": f"stem:{slot}"}
                    for slot, material in enumerate(materials)
                ],
            }
            for index in range(10)
        ]
        plan = [{"family_id": pack["family_id"], "source_kind": "closed", "frame_index": 0} for pack in packs]
        actual, shortages = assign_material_frames(plan, packs, dict(zip(materials, (6, 3, 1))), 7)
        self.assertEqual(actual, dict(zip(materials, (6, 3, 1))))
        self.assertEqual(shortages, [])
        self.assertTrue(all(pack["question_frames"][item["frame_index"]]["material_type"] == item["material_type"] for item, pack in zip(plan, packs)))
        repeated = [{"family_id": pack["family_id"], "source_kind": "closed", "frame_index": 0} for pack in packs]
        assign_material_frames(repeated, packs, dict(zip(materials, (6, 3, 1))), 7)
        self.assertEqual(plan, repeated)

    def test_image_question_args_preserve_plan_metadata(self) -> None:
        args = Namespace(pack_input=Path("closed.json"), seed=7, dry_run=True)
        item = {
            "pack_input": "image.json",
            "source_kind": "image",
            "family_id": "image:family",
            "era": "고대",
            "variant_key": "image:variant",
        }
        command = question_args(args, item, Path("output.json"), 1)
        self.assertNotIn("--family-id", command)
        self.assertEqual(command[command.index("--pack-input") + 1], "image.json")
        self.assertNotIn("--era", command)
        self.assertEqual(command[command.index("--variant-key") + 1], "image:variant")

    def test_closed_pack_adapter_rejects_answer_with_shared_evidence(self) -> None:
        members = [
            {
                "choice_fact_id": f"fact:{index}",
                "owner_id": f"owner:{index}",
                "owner_label": f"인물 {index}",
                "owner_type": "인물",
                "fact_basis": "검증된 사실이다.",
                "fact_evidence_chunks": [{"chunk_id": f"chunk:{index}", "article_id": f"owner:{index}"}],
                "material_clue_basis": "별도 단서이다.",
                "material_evidence_chunks": [{"chunk_id": f"{'chunk' if index == 0 else 'clue'}:{index}", "article_id": f"owner:{index}"}],
                "material_evidence_disjoint": index != 0,
            }
            for index in range(9)
        ]
        closed = {
            "family_id": "closed:overlap",
            "status": "final_reviewed",
            "difficulty": 2,
            "era": "조선",
            "relation_axis_id": "common.definition_feature",
            "topic_type": "인물",
            "question_frames": [{"question_task": "standard_select", "stem_pattern": "target_description", "material_type": "자료 제시문", "major_type": "역사 자료의 분석 및 해석", "minor_type": "자료 기반 시대·대상 추론", "answer_owner_scope": "material_target", "question_task_instruction": "검증된 V41 출제 지시", "distractor_type": "same_category_wrong_target"}],
            "answer_eligible_owner_ids": [f"owner:{index}" for index in range(1, 9)],
            "members": members,
        }
        closed["question_frames"].append({**closed["question_frames"][0], "material_type": "탐구 자료"})
        with self.assertRaisesRegex(ValueError, "not generation eligible"):
            build_generation_pack(closed, answer_owner_id="owner:0")

        closed["answer_eligible_owner_ids"].append("owner:0")
        members[0]["material_evidence_chunks"] = [{"chunk_id": "clue:0", "article_id": "owner:0"}]
        generated = build_generation_pack(closed, answer_owner_id="owner:0")
        self.assertEqual(generated["target_label"], "인물 0")
        self.assertEqual(validate_pack(generated)["pack_id"], generated["pack_id"])

    def test_closed_pack_adapter_requires_final_review_status(self) -> None:
        members = [
            {
                "choice_fact_id": f"fact:{index}",
                "owner_id": f"owner:{index}",
                "owner_label": f"인물 {index}",
                "owner_type": "인물",
                "fact_basis": "검증된 사실이다.",
                "fact_evidence_chunks": [{"chunk_id": f"fact:{index}", "article_id": f"owner:{index}"}],
                "material_clue_basis": "별도 단서이다.",
                "material_evidence_chunks": [{"chunk_id": f"clue:{index}", "article_id": f"owner:{index}"}],
                "material_evidence_disjoint": True,
            }
            for index in range(9)
        ]
        closed = {
            "family_id": "closed:status",
            "difficulty": 1,
            "era": "조선",
            "relation_axis_id": "person.activity_achievement",
            "topic_type": "인물",
            "question_frames": [{"question_task": "standard_select", "stem_pattern": "target_description", "material_type": "자료 제시문", "major_type": "역사 자료의 분석 및 해석", "minor_type": "자료 기반 시대·대상 추론", "answer_owner_scope": "material_target", "question_task_instruction": "검증된 V41 출제 지시", "distractor_type": "same_category_wrong_target"}],
            "answer_eligible_owner_ids": [f"owner:{index}" for index in range(9)],
            "members": members,
        }
        closed["question_frames"].append({**closed["question_frames"][0], "material_type": "탐구 자료"})
        for status in (None, "pending_user_review"):
            closed["status"] = status
            with self.assertRaisesRegex(ValueError, "final_reviewed"):
                build_generation_pack(closed)
        closed["status"] = "final_reviewed"
        self.assertEqual(build_generation_pack(closed, answer_owner_id="owner:0")["target_label"], "인물 0")

        for missing in ("major_type", "minor_type"):
            broken = copy.deepcopy(closed)
            broken["question_frames"][0].pop(missing)
            with self.assertRaisesRegex(ValueError, missing):
                build_generation_pack(broken)

    def test_evaluator_accepts_single_pipeline_question(self) -> None:
        records = records_from_assembled({"question": {"seed_id": "one", "choices": []}})
        self.assertEqual(records[0]["question_id"], "one")

    def test_evaluator_resume_skips_completed_question_ids(self) -> None:
        records = [{"question_id": str(index)} for index in range(1, 51)]
        rows = [{"question_id": str(index)} for index in range(1, 49)]
        self.assertEqual([row["question_id"] for row in pending_records(records, rows)], ["49", "50"])

    def test_evaluator_resume_rechecks_changed_question(self) -> None:
        original = records_from_assembled({"questions": [{"seed_id": "same", "question": "원래 발문", "choices": []}]})[0]
        changed = records_from_assembled({"questions": [{"seed_id": "same", "question": "수정 발문", "choices": []}]})[0]
        rows = [{"question_id": "same", "question_hash": original["question_hash"]}]
        self.assertEqual(pending_records([changed], rows), [changed])

    def test_evaluator_hash_does_not_depend_on_subset_position(self) -> None:
        question = {"seed_id": "same", "question": "같은 발문", "choices": []}
        single = records_from_assembled({"questions": [question]})[0]
        second = records_from_assembled({"questions": [{"seed_id": "other", "choices": []}, question]})[1]
        self.assertEqual(single["question_hash"], second["question_hash"])

    def test_repair_plan_accepts_only_known_questions_and_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repair.json"
            path.write_text(json.dumps({"items": [{"question_id": "q1", "action": "component_repair"}]}), encoding="utf-8")
            self.assertEqual(load_repair_actions(path, ["q1"]), {"q1": "component_repair"})
            path.write_text(json.dumps({"items": [{"question_id": "q1", "action": "discard"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid action"):
                load_repair_actions(path, ["q1"])

    @patch("ai.question_generation.workflows.closed_pack_batch.subprocess.run")
    def test_batch_resume_uses_latest_final_evaluation(self, mocked_run) -> None:
        mocked_run.return_value = type("Completed", (), {"returncode": 0})()
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            evaluation_dir = output_dir / "evaluation"
            evaluation_dir.mkdir()
            row = {"question_id": "q1", "question_hash": "hash"}
            (evaluation_dir / "cycle_0.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            (evaluation_dir / "final.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            args = Namespace(output_dir=output_dir, eval_model="gpt-5.6-terra", resume=True)
            evaluate_questions(args, [{"seed_id": "q1"}], 0)
            self.assertEqual(
                mocked_run.call_args.args[0][-2:],
                ["--resume-from", str(evaluation_dir / "final.jsonl")],
            )

    def test_repair_evaluation_cycle_continues_after_latest_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation_dir = Path(directory)
            for cycle in (0, 1, 3):
                (evaluation_dir / f"cycle_{cycle}.jsonl").write_text("", encoding="utf-8")
            self.assertEqual(next_repair_cycle(evaluation_dir), 4)

    def test_evaluator_includes_gate_and_score_calibration_examples(self) -> None:
        examples = evaluation_few_shot_messages()
        self.assertEqual(len(examples), 14)
        self.assertEqual([message["role"] for message in examples], ["user", "assistant"] * 7)
        expected = " ".join(message["content"] for message in examples if message["role"] == "assistant")
        self.assertTrue(all(gate in expected for gate in ("G3", "G4", "G6")))
        self.assertTrue(all(f'"total_score": {score}' in expected for score in (6, 8, 10)))
        messages = build_messages("rubric", records_from_assembled({"question": {"seed_id": "one", "choices": []}})[0])
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(len(messages), 16)
        self.assertIn("2점은 대표 추론", messages[-1]["content"])
        self.assertIn("target_score 2·3점은 가까운 오답 수만으로", messages[-1]["content"])
        self.assertIn("Gate 번호만으로 repair target을 고정하지 말고", messages[-1]["content"])
        self.assertIn("지문의 화자·시제·서술 형식이 일관되는지", messages[-1]["content"])
        self.assertIn("지시어의 대상 또는 행동 주체가 불명확하면 G2", messages[-1]["content"])
        self.assertIn("주어·서술어 호응, 조사·목적어 결합", messages[-1]["content"])
        self.assertIn("선지 원문 그대로 성립하는지를 판정", messages[-1]["content"])
        self.assertIn("오답 선지가 비문이면 해당 choice를 지정", messages[-1]["content"])
        self.assertNotIn("G2 또는 G3가 FAIL이면 선지가 아니라 question", messages[-1]["content"])

    @patch("ai.question_generation.evaluation.v18.OpenAI")
    def test_evaluator_marks_only_static_prefix_for_explicit_cache(self, openai: object) -> None:
        client = openai.return_value
        client.chat.completions.create.return_value.model_dump.return_value = {}
        messages = build_messages("rubric", records_from_assembled({"question": {"seed_id": "one", "choices": []}})[0])

        post_chat("key", "https://api.openai.com/v1", "gpt-5.6-terra", messages, 30, 0)

        payload = client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload["extra_body"], {"prompt_cache_options": {"mode": "explicit"}})
        self.assertTrue(payload["prompt_cache_key"].startswith("qgen-eval:"))
        self.assertEqual(messages[-2]["content"][0]["prompt_cache_breakpoint"], {"mode": "explicit"})
        self.assertIsInstance(messages[-1]["content"], str)

    def test_gate_pass_requires_and_totals_problem_score(self) -> None:
        parsed = normalize_gate({
            "gate_result": "PASS",
            "failed_gates": [],
            "gate": {gate: {"status": "PASS"} for gate in ("G1", "G2", "G3", "G4", "G5", "G6")},
            "choice_verification_summary": [
                {"choice": label, "historically_valid": "yes", "satisfies_stem_condition": "yes" if index == 0 else "no", "g5_should_fail": False}
                for index, label in enumerate(("①", "②", "③", "④", "⑤"))
            ],
            "g6_claim_equivalence_check": {"relation": "none", "can_answer_by_text_matching_without_history": False, "g6_should_fail": False},
            "problem_score": {"difficulty_score": 3, "choice_quality_score": 5, "revision_targets": []},
            "repair_targets": [],
            "target_feedback": {},
            "final_decision": "accept",
        })
        self.assertEqual(parsed["gate_result"], "PASS")
        self.assertEqual(parsed["problem_score"]["total_score"], 8)

    def test_malformed_evaluator_output_never_becomes_pass(self) -> None:
        parsed = normalize_gate({
            "gate_result": "PASS",
            "failed_gates": [],
            "gate": {gate: {"status": "PASS"} for gate in ("G1", "G2", "G3", "G4", "G5", "G6")},
            "choice_verification_summary": [
                {"choice": str(index), "historically_valid": "maybe", "satisfies_stem_condition": "maybe", "g5_should_fail": False}
                for index in range(1, 6)
            ],
            "g6_claim_equivalence_check": {"relation": "none", "can_answer_by_text_matching_without_history": False, "g6_should_fail": False},
            "problem_score": {"difficulty_score": 4, "choice_quality_score": 6, "revision_targets": []},
            "repair_targets": [],
            "target_feedback": {},
            "final_decision": "accept",
        })
        self.assertEqual(parsed["gate_result"], "uncertain")
        self.assertEqual(parsed["final_decision"], "needs_verification")
        self.assertEqual(parsed["repair_targets"], [])
        self.assertEqual(parsed["target_feedback"], {})

    def test_low_score_without_repair_target_requires_verification(self) -> None:
        parsed = normalize_gate({
            "gate_result": "PASS",
            "failed_gates": [],
            "gate": {gate: {"status": "PASS"} for gate in ("G1", "G2", "G3", "G4", "G5", "G6")},
            "choice_verification_summary": [
                {"choice": label, "historically_valid": "yes", "satisfies_stem_condition": "yes" if index == 0 else "no", "g5_should_fail": False}
                for index, label in enumerate(("①", "②", "③", "④", "⑤"))
            ],
            "g6_claim_equivalence_check": {"relation": "none", "can_answer_by_text_matching_without_history": False, "g6_should_fail": False},
            "problem_score": {"difficulty_score": 2, "choice_quality_score": 5, "revision_targets": []},
            "repair_targets": [],
            "target_feedback": {},
            "final_decision": "repair",
        })
        self.assertEqual(parsed["final_decision"], "needs_verification")
        self.assertIn("low_score_requires_repair_target", parsed["judge_output_errors"])

    def test_failed_evaluation_requires_target_specific_advice(self) -> None:
        parsed = normalize_gate({
            "gate_result": "FAIL",
            "failed_gates": ["G5"],
            "gate": {gate: {"status": "FAIL" if gate == "G5" else "PASS"} for gate in ("G1", "G2", "G3", "G4", "G5", "G6")},
            "choice_verification_summary": [
                {
                    "choice": label,
                    "historically_valid": "no" if label == "⑤" else "yes",
                    "satisfies_stem_condition": "yes" if label == "①" else "no",
                    "g5_should_fail": label == "⑤",
                }
                for label in ("①", "②", "③", "④", "⑤")
            ],
            "g6_claim_equivalence_check": {"relation": "none", "can_answer_by_text_matching_without_history": False, "g6_should_fail": False},
            "problem_score": None,
            "repair_targets": ["choice:⑤"],
            "target_feedback": {"choice:⑤": "⑤의 주체와 행위만 다시 생성한다."},
            "final_decision": "repair",
        })
        self.assertEqual(parsed["gate_result"], "FAIL")
        self.assertNotIn("judge_output_errors", parsed)

    def test_final_evaluation_rejects_gate_pass_below_eight(self) -> None:
        low = {"parsed": {"gate_result": "PASS", "problem_score": {"total_score": 7}}}
        accepted = {"parsed": {"gate_result": "PASS", "problem_score": {"total_score": 8}}}
        self.assertFalse(evaluation_accepted(low))
        self.assertTrue(evaluation_accepted(accepted))

    def test_question_shuffle_varies_by_seed_id(self) -> None:
        pack = basis_pack()
        item = generation_item(validate_pack(pack))
        state = new_state(pack, item)
        state["components"]["material"]["response"] = {"material": "<u>이 인물</u>은 개혁에 참여하였다."}
        state["components"]["correct"]["response"] = {
            "json": {"question": "밑줄 그은 '이 인물'의 활동으로 옳은 것은?", "answer_choice": "정답 사실을 시행하였다."}
        }
        for slot, component in state["components"]["distractors"].items():
            component["response"] = {"json": {"distractor_choice": f"오답 사실 {slot}을 시행하였다."}}
        answer_positions = set()
        for index in range(10):
            varied_item = {**item, "seed_id": f"shuffle-{index}"}
            question = assemble_question(varied_item, state["components"], 1)
            answer_positions.add(question["answer_number"])
        self.assertGreater(len(answer_positions), 1)

    def test_material_few_shot_prefers_same_route_and_difficulty(self) -> None:
        selection = {
            "seed_id": "pack:few-shot",
            "topic": "김홍집",
            "material_type": "자료 제시문",
            "question_task": "standard_select",
            "stem_pattern": "target_description",
            "difficulty_label": "어려움",
            "target_score": 3,
        }
        selected = choose_material_examples(load_json_dict(DEFAULT_MATERIAL_EXAMPLES), selection, 1)
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0]["difficulty_label"], "어려움")
        self.assertTrue(all(item["material_type"] == selection["material_type"] for item in selected))
        self.assertTrue(all(item["stem_pattern"] == "target_description" for item in selected))
        self.assertTrue(all(item["source_id"].startswith("advanced_") for item in selected))
        messages = material_few_shot_messages(selected)
        self.assertEqual([message["role"] for message in messages], ["user", "assistant", "user", "assistant"])
        self.assertIn(selected[0]["material"], messages[1]["content"])
        self.assertIn(selected[0]["question"], messages[1]["content"])
        with patch(
            "ai.question_generation.generation.material.chat_json",
            return_value={"material": "생성 지문", "question": "생성 발문", "used_evidence_ids": ["chunk"]},
        ) as chat:
            generate_material(
                selection=selection,
                sources=[{"chunk_id": "chunk", "title": "현재 근거", "snippet": "현재 문항의 식별 단서"}],
                material_example=selected,
                material_rules="형식 규칙",
                model="unused",
                base_url="https://example.invalid/v1",
                api_key="unused",
                temperature=0,
                timeout=1,
                max_retries=0,
            )
        sent = chat.call_args.kwargs["messages"]
        self.assertEqual([message["role"] for message in sent[:6]], ["system", "user", "assistant", "user", "assistant", "user"])
        self.assertIn("대상명·정확한 연도·대표 사건명을 한꺼번에 노출하지 않고", sent[-1]["content"])
        self.assertNotIn("<u>...</u>로 정확히 한 번", sent[-1]["content"])
        self.assertNotIn("빈칸 표시는 쓰지 않는다", sent[-1]["content"])
        for material_type, items in load_json_dict(DEFAULT_MATERIAL_EXAMPLES).items():
            for item in items:
                status = material_type_format_status(
                    {"material_type": material_type, "question_task": item["question_task"]}, item["material"]
                )
                self.assertEqual(status["status"], "ok", f"{item['source_id']}: {status['errors']}")

        rules = load_json_dict(Path("ai/question_generation/material_type_prompt_rules.json"))
        prompt_rules = material_type_rules_text(rules, "짧은 설명 자료")
        self.assertIn("110~170자를 목표", prompt_rules)
        self.assertIn("절대 허용 범위인 110~180자", prompt_rules)
        self.assertIn("절대 허용 범위인 120~240자", material_type_rules_text(rules, "자료 제시문"))
        self.assertIn("절대 허용 범위인 120~230자", material_type_rules_text(rules, "탐구 자료"))
        too_short = material_type_format_status(
            {"material_type": "짧은 설명 자료", "question_task": "standard_select"},
            "가" * 38 + ". " + "나" * 38 + ".",
            rules,
        )
        self.assertIn("material_below_type_min_chars", too_short["errors"])

        different_type_only = {
            "짧은 설명 자료": [{
                "topic": "다른 주제",
                "question_task": "standard_select",
                "stem_pattern": "target_description",
            }]
        }
        self.assertEqual(choose_material_examples(different_type_only, selection, 1), [])
        same_topic = {
            "자료 제시문": [{
                "topic": "김홍집",
                "question_task": "standard_select",
                "stem_pattern": "target_description",
            }]
        }
        self.assertEqual(choose_material_examples(same_topic, selection, 1), [])

    def test_image_choice_material_does_not_forbid_its_visual_clue(self) -> None:
        pack = basis_pack()
        item = generation_item(validate_pack(pack))
        item["choice_mode"] = "image"
        state = new_state(pack, item)
        args = Namespace(
            seed=1,
            max_stage_attempts=1,
            openai_model="unused",
            base_url="https://example.invalid/v1",
            request_timeout=1,
            transport_retries=0,
        )
        with (
            patch.dict("ai.question_generation.workflows.question_pipeline.os.environ", {"OPENAI_API_KEY": "unused"}),
            patch(
                "ai.question_generation.workflows.question_pipeline.generate_material",
                side_effect=RuntimeError("stop after inspecting request"),
            ) as generated,
            self.assertRaisesRegex(RuntimeError, "stop after inspecting request"),
        ):
            generate_material_stage(state, args, CallBudget(1, 10))
        self.assertEqual(generated.call_args.kwargs["answer_fact_hints"], [])

    def test_fill_blank_few_shot_and_material_contract(self) -> None:
        selection = {
            "seed_id": "pack:fill-blank",
            "topic": "동학운동",
            "topic_type": "사건",
            "material_type": "자료 제시문",
            "question_task": "standard_select",
            "stem_pattern": "fill_blank",
            "difficulty_label": "보통",
        }
        selected = choose_material_examples(load_json_dict(DEFAULT_MATERIAL_EXAMPLES), selection, 1)
        self.assertTrue(selected)
        self.assertEqual(selected[0]["stem_pattern"], "fill_blank")

        contract_selection = {**selection, "material_type": "", "topic": ""}
        valid = material_contract_status(contract_selection, "앞선 사건이 전개되었다. (가) 이후 지도자가 체포되었다.")
        self.assertEqual(valid["status"], "ok")
        missing = material_contract_status(contract_selection, "앞선 사건 뒤 지도자가 체포되었다.")
        self.assertIn("fill_blank_requires_single_marker", missing["errors"])
        underlined = material_contract_status(contract_selection, "앞선 사건이 전개되었다. (가) <u>이 사건</u> 뒤 지도자가 체포되었다.")
        self.assertIn("fill_blank_has_underlined_reference", underlined["errors"])

        standard = {**contract_selection, "stem_pattern": "target_description"}
        optional_marker = material_contract_status(standard, "(가)는 개혁을 추진하였다.")
        self.assertEqual(optional_marker["status"], "ok")
        self.assertEqual(clean_model_text("YMCA에서 한글(韓文)을 가르쳤다."), "YMCA에서 한글(韓文)을 가르쳤다.")

    def test_pack_builds_v41_generation_item(self) -> None:
        item = generation_item(validate_pack(basis_pack()))
        self.assertEqual(item["relation_axis_id"], "person.activity_achievement")
        self.assertEqual(len(item["distractors"]), 4)
        self.assertEqual(item["distractors"][0]["basis_item_id"], 101)
        self.assertEqual(item["material_contract"]["allowed_evidence_ids"], ["chunk:clue"])
        self.assertEqual(item["material_contract"]["forbidden_answer_evidence_ids"], ["chunk:0"])
        self.assertEqual(item["service_era"], "조선")
        self.assertEqual(item["service_topic"], "정치")

    def test_generation_item_never_sends_answer_chunk_as_material_source(self) -> None:
        pack = basis_pack()
        pack["material_evidence_chunks"].append({"chunk_id": "chunk:0", "article_id": "article:0", "exact_text": "정답 근거"})
        item = generation_item(validate_pack(pack))
        self.assertEqual([source["chunk_id"] for source in item["material_sources"]], ["chunk:clue"])

    def test_validate_pack_does_not_mutate_input(self) -> None:
        pack = basis_pack()
        pack["items"].reverse()
        original = copy.deepcopy(pack)
        validated = validate_pack(pack)
        self.assertEqual(pack, original)
        self.assertEqual([item["slot_no"] for item in validated["items"]], list(range(5)))

    def test_validate_pack_rejects_evidence_from_another_owner(self) -> None:
        pack = basis_pack()
        pack["items"][2]["evidence_chunks"][0]["article_id"] = "article:other"
        with self.assertRaisesRegex(ValueError, "evidence owner mismatch at slot 2"):
            validate_pack(pack)

    def test_invalid_semantic_pack_is_rejected(self) -> None:
        pack = basis_pack()
        pack["semantic_status"] = "fail"
        with self.assertRaises(ValueError):
            validate_pack(pack)

    def test_unclassified_stem_pack_is_rejected(self) -> None:
        pack = basis_pack()
        pack["stem_pattern"] = "standard_other"
        with self.assertRaises(ValueError):
            validate_pack(pack)

    def test_material_evidence_must_stay_inside_contract(self) -> None:
        contract = {"allowed_evidence_ids": ["clue"], "forbidden_answer_evidence_ids": ["answer"]}
        self.assertEqual(material_evidence_usage_status(contract, ["clue"])["status"], "ok")
        self.assertEqual(material_evidence_usage_status(contract, ["answer"])["status"], "needs_review")

    def test_validation_returns_only_failed_distractor_target(self) -> None:
        question = {
            "topic": "세종",
            "topic_type": "인물",
            "target_score": 2,
            "material": "<u>이 인물</u>은 조선 전기에 학문을 장려하였다.",
            "question": "밑줄 그은 '이 인물'의 활동으로 옳은 것은?",
            "choices": [
                {"text": "백성을 위한 문자를 창제하였다.", "is_answer": True},
                {"text": "노비안검법을 시행하였다.", "is_answer": False, "distractor_index": 1},
                {"text": "대동법을 처음 시행하였다.", "is_answer": False, "distractor_index": 2},
                {"text": "노비안검법을 시행하였다.", "is_answer": False, "distractor_index": 3},
                {"text": "균역법을 시행하였다.", "is_answer": False, "distractor_index": 4},
            ],
        }
        validation = validate_question(question)
        self.assertEqual(validation["gate_result"], "FAIL")
        self.assertEqual(validation["repair_targets"], ["distractor:3"])

    def test_hanja_is_preserved_until_the_local_gate_rejects_it(self) -> None:
        question = {
            "target_score": 2,
            "material": "자료에는 한글 교육 활동이 나타나 있다.",
            "question": "이 활동에 대한 설명으로 옳은 것은?",
            "choices": [
                {"text": "한글(韓文)을 가르쳤다.", "is_answer": True},
                *(
                    {"text": f"서로 다른 오답 {index}이다.", "is_answer": False, "distractor_index": index}
                    for index in range(1, 5)
                ),
            ],
        }
        validation = validate_question(question)
        self.assertIn("choice_has_hanja", validation["errors"])
        self.assertEqual(validation["gate_result"], "FAIL")

    def test_assembled_choices_keep_basis_sources(self) -> None:
        pack = basis_pack()
        item = generation_item(validate_pack(pack))
        state = new_state(pack, item)
        state["components"]["material"]["response"] = {"material": "<u>이 인물</u>은 개혁에 참여하였다."}
        state["components"]["correct"]["response"] = {
            "json": {"question": "밑줄 그은 '이 인물'의 활동으로 옳은 것은?", "answer_choice": "정답 사실을 시행하였다."}
        }
        for slot, component in state["components"]["distractors"].items():
            component["response"] = {"json": {"distractor_choice": f"오답 사실 {slot}을 시행하였다."}}
        question = assemble_question(item, state["components"], 1)
        self.assertEqual(question["material"], "<u>이 인물</u>은 개혁에 참여하였다.")
        self.assertEqual(question["material_source"]["basis"], [pack["material_clue_basis"]])
        self.assertEqual(question["material_source"]["evidence_chunk_ids"], ["chunk:clue"])
        self.assertTrue(all(choice.get("source", {}).get("owner_id") for choice in question["choices"]))
        record = records_from_assembled({"question": question})[0]
        self.assertEqual(len(record["verification_basis"]), 5)
        self.assertEqual(record["material_source"], question["material_source"])

    def test_source_repair_reopens_only_requested_distractor(self) -> None:
        pack = basis_pack()
        item = generation_item(validate_pack(pack))
        state = new_state(pack, item)
        state["status"] = "complete"
        state["question"] = {"seed_id": item["seed_id"]}
        for slot, component in state["components"]["distractors"].items():
            component["request"] = {"slot": slot}
            component["response"] = {"json": {"distractor_choice": f"기존 오답 {slot}"}}
            component["backend"] = "sllm"
        apply_override(
            state,
            {
                "target": "distractor:2",
                "basis": "인물 2의 새로 검수된 사실이다.",
                "evidence_chunk_ids": ["chunk:new"],
                "feedback": "이 근거로 두 번째 오답만 다시 생성한다.",
            },
            {"chunk:new": {"article_id": "article:2"}},
        )
        self.assertEqual(state["status"], "prepared")
        self.assertIsNone(state["question"])
        self.assertIsNone(state["components"]["distractors"]["2"]["response"])
        self.assertIsNotNone(state["components"]["distractors"]["1"]["response"])
        self.assertEqual(state["input"]["distractors"][1]["fact_basis"], "인물 2의 새로 검수된 사실이다.")
        self.assertEqual(state["repair_history"][-1]["target"], "distractor:2")

    def test_source_repair_requires_explicit_supporting_article(self) -> None:
        state = new_state(basis_pack(), generation_item(validate_pack(basis_pack())))
        owner_id = state["input"]["answer_basis"]["owner_id"]
        override = {
            "target": "correct",
            "basis": "검수한 보조 문서의 사실이다.",
            "evidence_chunk_ids": ["chunk:support"],
        }
        evidence = {"chunk:support": {"article_id": "article:support"}}
        with self.assertRaisesRegex(ValueError, "evidence owner mismatch"):
            apply_override(state, override, evidence)
        override["supporting_article_ids"] = ["article:support"]
        apply_override(state, override, evidence)
        self.assertEqual(state["input"]["answer_basis"]["owner_id"], owner_id)

    def test_call_budget_stops_extra_call(self) -> None:
        budget = CallBudget(max_calls=1, max_seconds=30)
        budget.claim("first")
        with self.assertRaises(PipelineLimitError):
            budget.claim("second")

    def test_resume_gets_new_budget_and_keeps_lifetime_usage(self) -> None:
        budget = CallBudget(max_calls=1, max_seconds=30, calls=28, elapsed=600)
        budget.claim("resumed")
        self.assertEqual(budget.total_calls(), 29)
        with self.assertRaises(PipelineLimitError):
            budget.claim("same_resume_over_limit")

    def test_failed_resume_reopens_only_failed_material(self) -> None:
        state = new_state(basis_pack(), generation_item(validate_pack(basis_pack())))
        component = state["components"]["material"]
        component.update({
            "attempts": 4,
            "response": {"material": "", "used_evidence_ids": []},
            "gate": {"status": "FAIL"},
            "feedback": "missing_material",
        })
        state["status"] = "generation_exhausted"
        state["error"] = "Stage attempt limit reached"
        state["total_llm_calls"] = 4

        prepare_failed_resume(state)

        self.assertEqual(state["status"], "prepared")
        self.assertEqual(state["components"]["material"]["attempts"], 0)
        self.assertIsNone(state["components"]["material"]["response"])
        self.assertEqual(state["components"]["material"]["feedback"], "missing_material")
        self.assertEqual(state["total_llm_calls"], 4)
        self.assertNotIn("error", state)

    @patch("ai.question_generation.workflows.question_pipeline.call_chat")
    def test_sllm_transport_timeout_retries_once(self, mocked_call) -> None:
        mocked_call.side_effect = [TimeoutError("cold start"), {"json": {}}]
        args = Namespace(
            transport_retries=1,
            endpoint_id="unused",
            api_key="unused",
            runpod_model="model",
            request_timeout=30,
        )
        with patch.dict("os.environ", {"RUNPOD_ENDPOINT_ID": "endpoint", "RUNPOD_API_KEY": "key"}):
            result = call_sllm({}, args, CallBudget(max_calls=2, max_seconds=30), "test")
        self.assertEqual(result, {"json": {}})
        self.assertEqual(mocked_call.call_count, 2)

    @patch("ai.question_generation.workflows.question_pipeline.call_sllm")
    def test_sllm_sends_each_distractor_basis_separately(self, mocked_call) -> None:
        pack = basis_pack()
        item = generation_item(validate_pack(pack))
        state = new_state(pack, item)
        state["components"]["material"]["response"] = {"material": "<u>이 인물</u>은 개혁에 참여하였다."}

        def output_for(record, *_args):
            if record["choice_role"] == "correct":
                return {"json": {"question": "이 인물의 활동으로 옳은 것은?", "answer_choice": "정답 사실이다."}}
            return {"json": {"distractor_choice": f"오답 사실 {record['distractor_index']}이다."}}

        mocked_call.side_effect = output_for
        generate_v41_question_stage(state, Namespace(max_stage_attempts=1), CallBudget(5, 30))

        records = [call.args[0] for call in mocked_call.call_args_list]
        self.assertEqual(len(records), 5)
        for index, record in enumerate(records[1:], start=1):
            basis = " ".join(record["input"]["distractor_fact_basis"])
            self.assertIn(f"인물 {index}", basis)
            self.assertTrue(all(f"인물 {other}" not in basis for other in range(1, 5) if other != index))
        self.assertEqual(state["components"]["correct"]["gate"]["status"], "PASS")

        mocked_call.reset_mock()
        state["question"] = {"validation": {"errors": ["duplicate_choice"]}}
        invalidate(state, ["distractor:3"], "최종 평가 오류")
        self.assertEqual(state["components"]["distractors"]["3"]["feedback"], "최종 평가 오류")
        generate_v41_question_stage(state, Namespace(max_stage_attempts=1), CallBudget(1, 30))
        self.assertEqual(mocked_call.call_count, 1)
        self.assertEqual(mocked_call.call_args.args[0]["distractor_index"], 3)
        self.assertIn("최종 평가 오류", mocked_call.call_args.args[0]["instruction"])
        self.assertNotIn("feedback", mocked_call.call_args.args[0]["input"])

    def test_question_selection_uses_only_structurally_valid_candidate_without_api(self) -> None:
        pack = basis_pack()
        state = new_state(pack, generation_item(validate_pack(pack)))
        state["components"]["material"]["response"] = {
            "material": "자료에는 별도의 위치 표시가 없다.",
            "question": "(가)의 활동으로 옳은 것은?",
        }
        state["components"]["correct"]["response"] = {
            "json": {"question": "이 인물의 활동으로 옳은 것은?", "answer_choice": "정답이다."}
        }
        args = Namespace()
        select_question_stage(state, args, CallBudget(0, 30))
        self.assertEqual(state["question_selection"]["selected_source"], "sllm")
        self.assertEqual(selected_question(state), "이 인물의 활동으로 옳은 것은?")

    @patch("ai.question_generation.workflows.question_pipeline.chat_json")
    def test_question_selection_prefers_valid_gpt_without_selector_call(self, mocked_chat) -> None:
        pack = basis_pack()
        state = new_state(pack, generation_item(validate_pack(pack)))
        state["components"]["material"]["response"] = {
            "material": "<u>이 인물</u>은 개혁에 참여하였다.",
            "question": "밑줄 그은 인물의 활동으로 옳은 것은?",
        }
        state["components"]["correct"]["response"] = {
            "json": {"question": "이 인물의 활동으로 옳은 것은?", "answer_choice": "정답이다."}
        }
        args = Namespace(
            base_url="https://example.invalid/v1",
            openai_model="test-model",
            request_timeout=30,
            transport_retries=0,
        )
        select_question_stage(state, args, CallBudget(0, 30))
        self.assertEqual(selected_question(state), "밑줄 그은 인물의 활동으로 옳은 것은?")
        mocked_chat.assert_not_called()

    @patch("ai.question_generation.workflows.question_pipeline.chat_json")
    def test_question_feedback_repairs_when_no_alternative_candidate_exists(self, mocked_chat) -> None:
        mocked_chat.return_value = {"question": "자료의 사건 직후에 일어난 사실로 옳은 것은?"}
        pack = basis_pack()
        state = new_state(pack, generation_item(validate_pack(pack)))
        state["components"]["material"]["response"] = {
            "material": "자료에는 개혁이 시작된 과정이 나타나 있다.",
            "question": "이후의 사실로 옳은 것은?",
        }
        state["components"]["correct"]["response"] = {
            "json": {"question": "이후의 사실로 옳은 것은?", "answer_choice": "정답이다."}
        }
        state["question_selection_feedback"] = "시기 범위를 직접 한정한다."
        args = Namespace(
            base_url="https://example.invalid/v1",
            openai_model="test-model",
            request_timeout=30,
            transport_retries=0,
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            select_question_stage(state, args, CallBudget(1, 30))
        self.assertEqual(state["question_selection"]["selected_source"], "llm_repair")
        self.assertEqual(selected_question(state), "자료의 사건 직후에 일어난 사실로 옳은 것은?")
        self.assertIn("시기 범위를 직접 한정한다.", mocked_chat.call_args.kwargs["messages"][-1]["content"])

    def test_question_selection_rejects_two_invalid_candidates(self) -> None:
        pack = basis_pack()
        state = new_state(pack, generation_item(validate_pack(pack)))
        state["components"]["material"]["response"] = {
            "material": "자료에는 표시가 없다.",
            "question": "(가)에 대한 설명으로 옳은 것은?",
        }
        state["components"]["correct"]["response"] = {
            "json": {"question": "밑줄 그은 대상에 대한 설명으로 옳은 것은?", "answer_choice": "정답이다."}
        }
        with self.assertRaisesRegex(PipelineLimitError, "question_repair"):
            select_question_stage(state, Namespace(), CallBudget(0, 30))

    @patch("ai.question_generation.workflows.question_pipeline.chat_json")
    def test_question_feedback_repairs_two_invalid_candidates(self, mocked_chat) -> None:
        mocked_chat.return_value = {"question": "다음 자료의 단체에 대한 설명으로 옳은 것은?"}
        pack = basis_pack()
        state = new_state(pack, generation_item(validate_pack(pack)))
        state["components"]["material"]["response"] = {
            "material": "자료에는 별도의 위치 표시가 없다.",
            "question": "(가)에 대한 설명으로 옳은 것은?",
        }
        state["components"]["correct"]["response"] = {
            "json": {"question": "밑줄 그은 단체에 대한 설명으로 옳은 것은?", "answer_choice": "정답이다."}
        }
        state["question_selection_feedback"] = "지문에 없는 표식을 발문에서 제거한다."
        args = Namespace(
            base_url="https://example.invalid/v1",
            openai_model="test-model",
            request_timeout=30,
            transport_retries=0,
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            select_question_stage(state, args, CallBudget(1, 30))
        self.assertEqual(state["question_selection"]["selected_source"], "llm_repair")
        self.assertEqual(selected_question(state), "다음 자료의 단체에 대한 설명으로 옳은 것은?")

    def test_final_evaluation_choice_maps_back_to_distractor_slot(self) -> None:
        question = {
            "choices": [
                {"number": 1, "is_answer": True, "source": {"role": "answer", "slot": 0}},
                {"number": 2, "is_answer": False, "source": {"role": "distractor", "slot": 4}},
            ]
        }
        row = {"parsed": {"final_decision": "repair", "repair_targets": ["choice:②"], "problem_score": {"revision_targets": []}, "target_feedback": {"choice:②": "④ 사실만 수정"}}}
        self.assertEqual(list(evaluation_repair_feedback(question, row)), ["distractor:4"])

    def test_failed_gate_does_not_override_explicit_repair_target(self) -> None:
        question = {
            "choices": [
                {"number": 1, "is_answer": True, "source": {"role": "answer", "slot": 0}},
                {"number": 2, "is_answer": False, "source": {"role": "distractor", "slot": 4}},
            ]
        }
        row = {
            "parsed": {
                "failed_gates": ["G2"],
                "final_decision": "repair",
                "repair_targets": ["choice:②"],
                "problem_score": None,
                "target_feedback": {"choice:②": "④ 사실만 수정"},
            }
        }
        self.assertEqual(list(evaluation_repair_feedback(question, row)), ["distractor:4"])

        row["parsed"] = {"failed_gates": ["G3"], "final_decision": "repair", "repair_targets": ["correct"], "problem_score": None, "target_feedback": {"correct": "정답만 수정"}}
        self.assertEqual(list(evaluation_repair_feedback(question, row)), ["correct"])

    def test_current_batch_repair_invalidates_only_evaluated_component(self) -> None:
        pack = basis_pack()
        state = new_state(pack, generation_item(validate_pack(pack)))
        state["status"] = "complete"
        state["components"]["correct"]["response"] = {"json": {"answer_choice": "정답이다."}}
        for component in state["components"]["distractors"].values():
            component["response"] = {"json": {"distractor_choice": "오답이다."}}
        question = {
            "choices": [
                {"number": 1, "is_answer": True, "source": {"role": "answer", "slot": 0}},
                {"number": 2, "is_answer": False, "source": {"role": "distractor", "slot": 4}},
            ]
        }
        row = {"parsed": {"final_decision": "repair", "repair_targets": ["choice:②"], "problem_score": {"revision_targets": []}, "target_feedback": {"choice:②": "④ 주체만 수정"}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(prepare_evaluation_repair(path, question, row), ["distractor:4"])
            repaired = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsNone(repaired["components"]["distractors"]["4"]["response"])
        self.assertEqual(repaired["components"]["distractors"]["4"]["feedback"], "④ 주체만 수정")
        self.assertIsNotNone(repaired["components"]["distractors"]["1"]["response"])
        self.assertIsNotNone(repaired["components"]["correct"]["response"])

    def test_question_invalidation_keeps_candidates_and_clears_distractors(self) -> None:
        pack = basis_pack()
        state = new_state(pack, generation_item(validate_pack(pack)))
        state["components"]["material"]["response"] = {"material": "자료", "question": "잘못된 발문"}
        state["components"]["correct"]["response"] = {"json": {"question": "SLLM 발문", "answer_choice": "정답"}}
        state["question_selection"] = {"selected_question": "잘못된 발문"}
        for component in state["components"]["distractors"].values():
            component["response"] = {"json": {"distractor_choice": "오답"}}
        invalidate(state, ["question"], "실제 평가 이유")
        self.assertIsNotNone(state["components"]["material"]["response"])
        self.assertIsNotNone(state["components"]["correct"]["response"])
        self.assertIsNone(state["question_selection"])
        self.assertTrue(all(component["response"] is None for component in state["components"]["distractors"].values()))

    def test_correct_invalidation_clears_selector_and_all_distractors(self) -> None:
        pack = basis_pack()
        state = new_state(pack, generation_item(validate_pack(pack)))
        state["components"]["correct"]["response"] = {"json": {"answer_choice": "정답"}}
        state["question_selection"] = {"selected_question": "발문"}
        for component in state["components"]["distractors"].values():
            component["response"] = {"json": {"distractor_choice": "오답"}}
        invalidate(state, ["correct"])
        self.assertIsNone(state["components"]["correct"]["response"])
        self.assertIsNone(state["question_selection"])
        self.assertTrue(all(component["response"] is None for component in state["components"]["distractors"].values()))

    def test_evaluation_material_repair_preserves_choices_and_records_history(self) -> None:
        pack = basis_pack()
        state = new_state(pack, generation_item(validate_pack(pack)))
        state["components"]["material"].update({"request": {"material": "input"}, "response": {"material": "old"}})
        state["components"]["correct"]["response"] = {"json": {"answer_choice": "정답"}}
        for component in state["components"]["distractors"].values():
            component["response"] = {"json": {"distractor_choice": "오답"}}
        correct = copy.deepcopy(state["components"]["correct"])
        distractors = copy.deepcopy(state["components"]["distractors"])
        invalidate(state, ["material"], {"material": "지문만 수정"}, evaluation=True)
        self.assertIsNone(state["components"]["material"]["response"])
        self.assertEqual(state["components"]["correct"], correct)
        self.assertEqual(state["components"]["distractors"], distractors)
        self.assertEqual(state["repair_history"][-1]["feedback"], "지문만 수정")
        self.assertEqual(state["repair_history"][-1]["request"], {"material": "input"})

    def test_current_workflows_do_not_import_legacy_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for path in (root / "workflows").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("ai.question_generation.legacy", text)
            self.assertNotIn("ai.question_generation.graph_path", text)

    def test_evaluation_feedback_stays_with_its_component(self) -> None:
        question = {
            "choices": [
                {"number": 1, "is_answer": True, "source": {"slot": 0}},
                {"number": 2, "is_answer": False, "source": {"slot": 4}},
            ]
        }
        row = {"parsed": {
            "final_decision": "repair",
            "repair_targets": ["correct", "choice:②"],
            "problem_score": None,
            "target_feedback": {"correct": "정답 주체 수정", "choice:②": "④ 행위 수정"},
        }}
        feedback = evaluation_repair_feedback(question, row)
        self.assertEqual(feedback, {"correct": "정답 주체 수정", "distractor:4": "④ 행위 수정"})
        state = new_state(basis_pack(), generation_item(validate_pack(basis_pack())))
        invalidate(state, list(feedback), feedback)
        self.assertEqual(state["components"]["correct"]["feedback"], "정답 주체 수정")
        self.assertEqual(state["components"]["distractors"]["4"]["feedback"], "④ 행위 수정")
        row["parsed"]["target_feedback"] = ["잘못된 종합 피드백"]
        self.assertEqual(evaluation_repair_feedback(question, row), {})

        row["parsed"] = {
            "final_decision": "needs_verification",
            "repair_targets": ["correct"],
            "target_feedback": {"correct": "수정하면 안 됨"},
        }
        self.assertEqual(evaluation_repair_feedback(question, row), {})

    def test_choice_model_uses_llm_after_two_evaluation_regenerations(self) -> None:
        component = {"evaluation_repairs": 3, "previous_response": {"json": {"distractor_choice": "실패 문장"}}}
        record = {
            "system": "출제자",
            "instruction": "피드백대로 고쳐라",
            "input": {"distractor_fact_basis": ["검증 근거"]},
        }
        args = Namespace(base_url="https://example.invalid/v1", openai_model="judge", request_timeout=10, transport_retries=0)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), patch(
            "ai.question_generation.workflows.question_pipeline.chat_json",
            return_value={"distractor_choice": "수정 문장"},
        ) as chat, patch("ai.question_generation.workflows.question_pipeline.call_sllm") as sllm:
            output = call_choice_model(record, component, args, CallBudget(1, 30), "distractor")
        self.assertEqual(output["json"]["distractor_choice"], "수정 문장")
        self.assertEqual(component["backend"], "llm_repair")
        self.assertFalse(sllm.called)
        self.assertIn("실패 문장", chat.call_args.kwargs["messages"][-1]["content"])

    def test_local_choice_repair_preserves_attempt_count_and_failed_output(self) -> None:
        state = new_state(basis_pack(), generation_item(validate_pack(basis_pack())))
        component = state["components"]["distractors"]["3"]
        component["response"] = {"json": {"distractor_choice": "한자(漢字) 오류"}}
        invalidate(state, ["distractor:3"], "choice_has_hanja")
        repaired = state["components"]["distractors"]["3"]
        self.assertEqual(repaired["evaluation_repairs"], 1)
        self.assertEqual(repaired["previous_response"], {"json": {"distractor_choice": "한자(漢字) 오류"}})

    def test_choice_model_falls_back_to_llm_after_bounded_sllm_transport_failure(self) -> None:
        component = {"evaluation_repairs": 1, "previous_response": {"json": {"distractor_choice": "실패 문장"}}}
        record = {"system": "출제자", "instruction": "피드백대로 고쳐라", "input": {"fact": "검증 근거"}}
        args = Namespace(base_url="https://example.invalid/v1", openai_model="repair", request_timeout=10, transport_retries=0)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), patch(
            "ai.question_generation.workflows.question_pipeline.call_sllm",
            side_effect=RuntimeError("RunPod transport failed after 2 attempts: timeout"),
        ), patch(
            "ai.question_generation.workflows.question_pipeline.chat_json",
            return_value={"distractor_choice": "수정 문장"},
        ):
            output = call_choice_model(record, component, args, CallBudget(2, 30), "distractor")
        self.assertEqual(output["json"]["distractor_choice"], "수정 문장")
        self.assertEqual(component["backend"], "llm_transport_fallback")
        self.assertIn("timeout", component["transport_error"])

    def test_basis_cleanup_preserves_hanja(self) -> None:
        self.assertEqual(clean_basis_text("한글(韓文)  교육"), "한글(韓文) 교육")

    def test_sllm_record_matches_v41_training_contract(self) -> None:
        item = generation_item(validate_pack(basis_pack()))
        record = correct_record(item, "<u>이 대상</u>은 여러 활동을 하였다.")
        self.assertEqual(record["system"], "당신은 한국사능력검정시험 심화 문항을 만드는 출제자입니다.")
        self.assertEqual(set(record), {"seed_id", "choice_role", "system", "instruction", "input"})
        self.assertEqual(
            set(record["input"]),
            {
                "task_type", "material", "answer_fact_basis", "topic_type", "topic", "material_type",
                "major_type", "minor_type", "question_task", "question_task_instruction", "difficulty_label",
            },
        )
        distractor = distractor_record(
            item,
            "<u>이 대상</u>은 여러 활동을 하였다.",
            item["distractors"][0],
            {"question": "이 대상에 대한 설명으로 옳은 것은?", "answer_choice": "정답이다."},
        )
        self.assertEqual(
            set(distractor["input"]),
            {
                "task_type", "material", "question", "answer_choice", "distractor_fact_basis",
                "distractor_type", "topic_type", "topic", "material_type", "major_type", "minor_type",
                "question_task", "difficulty_label",
            },
        )
        self.assertEqual(distractor["input"]["distractor_type"], "same_category_wrong_target")

        retried = correct_record(item, "<u>이 대상</u>은 여러 활동을 하였다.", "주체와 행위를 바로잡아라.")
        self.assertEqual(retried["input"], record["input"])
        self.assertNotEqual(retried["instruction"], record["instruction"])
        self.assertIn("주체와 행위를 바로잡아라.", retried["instruction"])

    def test_material_question_must_match_visible_markers(self) -> None:
        material = "<u>이 인물</u>은 개혁을 추진하였다."
        self.assertEqual(material_question_error(material, "밑줄 그은 인물의 활동으로 옳은 것은?"), "")
        self.assertEqual(material_question_error(material, "(가)의 활동으로 옳은 것은?"), "question_mentions_missing_marker")
        self.assertEqual(material_question_error(material, "<u>이 인물</u>의 활동으로 옳은 것은?"), "question_has_html_tag")

    def test_mock_exam_requires_exact_quota_and_local_gate(self) -> None:
        question = {
            "seed_id": "pack:1",
            "difficulty_label": "쉬움",
            "target_score": 1,
            "material": "자료",
            "question": "발문",
            "answer_number": 1,
            "choices": [
                {"number": number, "text": f"선지 {number}", "is_answer": number == 1}
                for number in range(1, 6)
            ],
            "validation": {"gate_result": "PASS"},
        }
        self.assertIn("1. 1", render_markdown([question]))
        question["image"] = {"original_image_url": "https://example.com/image.jpg", "title": "정답명"}
        rendered = render_markdown([question])
        self.assertIn("![문항 시각 자료](https://example.com/image.jpg)", rendered)
        self.assertNotIn("정답명", rendered)


if __name__ == "__main__":
    unittest.main()

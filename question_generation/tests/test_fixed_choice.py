from __future__ import annotations

import unittest
from argparse import Namespace
from unittest.mock import patch

from question_generation.evaluation.fixed_choice import normalize_gate
from question_generation.evaluation.v18 import build_messages, records_from_assembled
from question_generation.generation.assemble import assemble_question
from question_generation.workflows.closed_pack_batch import evaluation_accepted
from question_generation.workflows.question_pipeline import CallBudget, generate_v41_question_stage, new_state


def item(mode: str) -> dict:
    bases = [
        {
            "slot": slot,
            "role": "answer" if slot == 0 else "distractor",
            "basis_item_id": f"basis:{slot}",
            "owner_id": f"owner:{slot}",
            "owner_label": f"대상 {slot}",
            "fact_basis": f"대상 {slot}의 검증된 사실",
            "evidence_chunk_ids": [f"chunk:{slot}"],
        }
        for slot in range(5)
    ]
    if mode == "image":
        for slot, basis in enumerate(bases):
            basis["image"] = {
                "image_chunk_id": f"image:{slot}",
                "original_image_url": f"https://example.com/{slot}.jpg",
            }
    return {
        "seed_id": f"fixed:{mode}",
        "choice_mode": mode,
        "target_score": 2,
        "question_task": "order" if mode == "order" else "standard_select",
        "stem_pattern": "chronological_order" if mode == "order" else "target_description",
        "relation_axis_id": "event.chronology.order" if mode == "order" else "common.definition_feature",
        "question_task_instruction": "검증된 고정 선택지용 발문",
        "answer_basis": bases[0],
        "distractors": bases[1:],
        "material_clue_basis": ["검증된 지문 근거"],
        "material_clue_evidence": [{"chunk_id": "material:1"}],
        "chronology": {
            "event_labels": {"late": "(가)", "early": "(나)", "middle": "(다)"},
            "correct_order": ["early", "middle", "late"],
        } if mode == "order" else None,
    }


class FixedChoiceTest(unittest.TestCase):
    def make_question(self, mode: str) -> dict:
        generation_item = item(mode)
        state = new_state({"pack_id": generation_item["seed_id"]}, generation_item)
        state["components"]["material"]["response"] = {
            "material": "(가), (나), (다)의 사건을 살펴보자." if mode == "order" else "다음 설명에 해당하는 문화유산을 고르시오.",
            "question": "사건을 일어난 순서대로 옳게 나열한 것은?" if mode == "order" else "이에 해당하는 문화유산은?",
        }
        args = Namespace(base_url="", openai_model="", request_timeout=30, transport_retries=0)
        with patch("question_generation.workflows.question_pipeline.call_sllm") as sllm, patch(
            "question_generation.workflows.question_pipeline.chat_json"
        ) as judge:
            generate_v41_question_stage(state, args, CallBudget(2, 30))
        sllm.assert_not_called()
        judge.assert_not_called()
        self.assertEqual(set(state["components"]), {"material"})
        return assemble_question(generation_item, state["components"], 7, state["question_selection"]["selected_question"])

    def test_order_skips_choice_generation_and_uses_fixed_judge(self) -> None:
        question = self.make_question("order")
        self.assertEqual(len(question["choices"]), 5)
        self.assertEqual(next(row["text"] for row in question["choices"] if row["is_answer"]), "(나) - (다) - (가)")
        record = records_from_assembled({"question": question})[0]
        messages = build_messages("unused rubric", record)
        self.assertNotIn("choice_quality_score", messages[1]["content"])
        parsed = normalize_gate({
            "evaluation_profile": "fixed_choice",
            "gate_result": "PASS",
            "failed_gates": [],
            "gate": {gate: {"status": "PASS", "reason": "문제없음"} for gate in ("G2", "G3", "G4", "G6")},
            "problem_score": None,
            "repair_targets": [],
            "final_decision": "accept",
            "target_feedback": {},
        })
        self.assertTrue(evaluation_accepted({"parsed": parsed}))

    def test_image_choices_are_pack_images(self) -> None:
        question = self.make_question("image")
        self.assertEqual(question["validation"]["gate_result"], "PASS")
        self.assertEqual(len({row["image"]["image_chunk_id"] for row in question["choices"]}), 5)
        self.assertTrue(all(row["choice_image_path"] for row in question["choices"]))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import random
import unittest
from pathlib import Path

from ai.pack_generation.build_chronology_packs import validate_outcome_coverage
from ai.question_generation.generation.assemble import (
    replace_order_choices,
    replace_timeline_position_choices,
)
from ai.question_generation.generation.material_validation import material_contract_status


class ChronologyAssemblyTest(unittest.TestCase):
    def test_pack_gate_rejects_repeated_timeline_and_order_answers(self) -> None:
        timeline = [
            {
                "question_task": "timeline_position",
                "timeline_positions": list("ABCDE"),
                "correct_position": "A",
            }
            for _ in range(5)
        ]
        order = [
            {
                "question_task": "order",
                "event_ids": ["first", "second", "third"],
                "event_labels": {"first": "A", "second": "B", "third": "C"},
                "correct_order": ["first", "second", "third"],
            }
            for _ in range(6)
        ]
        with self.assertRaisesRegex(ValueError, "timeline position distribution is biased"):
            validate_outcome_coverage(timeline)
        with self.assertRaisesRegex(ValueError, "order distribution is biased"):
            validate_outcome_coverage(order)

    def test_production_patterns_cover_timeline_positions_and_order_permutations(self) -> None:
        plan = json.loads(
            (Path(__file__).parents[2] / "pack_generation" / "chronology_production_plan_10_20260723.json").read_text(
                encoding="utf-8"
            )
        )
        timeline_intervals = []
        for pattern in plan["frame_patterns"]["timeline_position"]:
            references = sorted(pattern["reference_indices"])
            timeline_intervals.append(
                next(
                    index
                    for index, (left, right) in enumerate(zip(references, references[1:]))
                    if left < pattern["target_index"] < right
                )
            )
        self.assertEqual(set(timeline_intervals), set(range(5)))

        orders = set()
        for pattern in plan["frame_patterns"]["order"]:
            display = pattern["display_indices"]
            labels = {event_index: label for event_index, label in zip(display, "가나다")}
            orders.add(tuple(labels[event_index] for event_index in sorted(display)))
        self.assertEqual(len(orders), 6)

    def test_order_uses_explicit_event_order(self) -> None:
        question = {
            "choice_mode": "order",
            "chronology": {
                "event_labels": {"late": "(가)", "early": "(나)", "middle": "(다)"},
                "correct_order": ["early", "middle", "late"],
            },
            "answer_fact_basis": ["문장에 표시된 순서는 정답으로 사용하면 안 된다: (가) - (나) - (다)"],
            "choices": [{"is_answer": True, "source": {"basis_item_id": "answer"}}],
        }
        replace_order_choices(question, random.Random(1))
        answer = next(choice for choice in question["choices"] if choice["is_answer"])
        self.assertEqual(answer["text"], "(나) - (다) - (가)")
        self.assertEqual(len(question["choices"]), 5)

    def test_timeline_uses_explicit_position(self) -> None:
        positions = ["(가)", "(나)", "(다)", "(라)", "(마)"]
        question = {
            "choice_mode": "timeline_position",
            "chronology": {"timeline_positions": positions, "correct_position": "(다)"},
            "choices": [{"is_answer": True, "source": {"basis_item_id": "answer"}}],
        }
        replace_timeline_position_choices(question, random.Random(1))
        answer = next(choice for choice in question["choices"] if choice["is_answer"])
        self.assertEqual(answer["text"], "(다)")
        self.assertEqual({choice["text"] for choice in question["choices"]}, set(positions))

    def test_timeline_material_requires_every_declared_position_once_and_in_order(self) -> None:
        selection = {
            "question_task": "timeline_position",
            "chronology": {"timeline_positions": ["(가)", "(나)", "(다)", "(라)", "(마)"]},
        }
        valid = material_contract_status(
            selection,
            "사건 1 - (가) - 사건 2 - (나) - 사건 3 - (다) - 사건 4 - (라) - 사건 5 - (마) - 사건 6",
        )
        self.assertEqual(valid["status"], "ok")

        missing = material_contract_status(
            selection,
            "사건 1 - (가) - 사건 2 - (나) - 사건 3 - (다) - 사건 4",
        )
        self.assertIn("timeline_position_marker_contract_mismatch", missing["errors"])

    def test_order_without_explicit_contract_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            replace_order_choices(
                {"choice_mode": "order", "choices": []},
                random.Random(1),
            )


if __name__ == "__main__":
    unittest.main()

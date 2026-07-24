from __future__ import annotations

import random
import unittest

from question_generation.generation.assemble import (
    replace_order_choices,
    replace_timeline_position_choices,
)


class ChronologyAssemblyTest(unittest.TestCase):
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

    def test_order_without_explicit_contract_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            replace_order_choices(
                {"choice_mode": "order", "choices": []},
                random.Random(1),
            )


if __name__ == "__main__":
    unittest.main()

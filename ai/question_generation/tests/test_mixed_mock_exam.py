import unittest

from ai.question_generation.workflows.mixed_mock_exam import allocate


class MixedMockExamTest(unittest.TestCase):
    def test_allocation_satisfies_type_and_cell_quotas(self) -> None:
        first = (1, "고대")
        second = (2, "조선")
        capacities = {
            ("standard", first): 2,
            ("standard", second): 2,
            ("chronology", first): 1,
            ("image", second): 1,
        }
        result = allocate(
            capacities,
            {"standard": 2, "chronology": 1, "image": 1},
            {first: 2, second: 2},
        )

        self.assertEqual(sum(result.values()), 4)
        self.assertEqual(sum(count for (kind, _), count in result.items() if kind == "standard"), 2)
        self.assertEqual(sum(count for (_, cell), count in result.items() if cell == first), 2)


if __name__ == "__main__":
    unittest.main()

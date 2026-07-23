import json
import sys
import tempfile
import unittest
from pathlib import Path


class ExamTextPreprocessingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root / "terms"))
        sys.path.insert(0, str(neo4j_root))

        from common import load_pipeline_policy
        from prep_json import prep_json

        cls.prep_json = staticmethod(prep_json)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def write_exam_json(self, directory: str, records: list[dict]) -> Path:
        exam_path = Path(directory) / "exam.json"
        exam_path.write_text(
            json.dumps(records, ensure_ascii=False),
            encoding="utf-8",
        )
        return exam_path

    def build_record(
        self,
        problem_id: str,
        material: str,
        question: str,
        input_text: str,
        choices: list[str] | None = None,
    ) -> dict:
        choice_contents = choices or ["정답 선지", "오답 선지"]
        return {
            "problem_id": problem_id,
            "material": material,
            "question": question,
            "input_text": input_text,
            "answer_choice": choice_contents[0],
            "distractor_choices": choice_contents[1:],
            "choices": [
                {
                    "is_answer": index == 0,
                    "content": content,
                }
                for index, content in enumerate(choice_contents)
            ],
        }

    def test_rows_are_preserved_and_audit_fields_are_recorded(self):
        records = [
            self.build_record("question-1", "자료", "질문", "자료\n질문"),
            self.build_record("question-2", "자료", "질문", "자료\n질문"),
            self.build_record("question-3", "자료", "질문", "자료 질문"),
            self.build_record("question-4", "자료", "질문", "다른 질문"),
            self.build_record("question-5", "", "질문", "원본 질문"),
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            exam_path = self.write_exam_json(temporary_directory, records)
            result = self.prep_json(
                str(exam_path),
                self.policy["text_preprocessing"],
            )

        self.assertEqual(len(result), len(records))
        status_by_id = dict(
            zip(result["problem_id"], result["input_text_match_status"])
        )
        self.assertEqual(status_by_id["question-1"], "EXACT")
        self.assertEqual(status_by_id["question-3"], "WHITESPACE_EQUIVALENT")
        self.assertEqual(status_by_id["question-4"], "CONTENT_CONFLICT")
        self.assertEqual(status_by_id["question-5"], "INPUT_COMPONENT_MISSING")

        first = result[result["problem_id"] == "question-1"].iloc[0]
        second = result[result["problem_id"] == "question-2"].iloc[0]
        fallback = result[result["problem_id"] == "question-5"].iloc[0]
        self.assertEqual(first["input_text_original"], "자료\n질문")
        self.assertEqual(first["reconstructed_stem"], "자료\n질문")
        self.assertEqual(first["extraction_text"], "자료\n질문\n정답 선지\n오답 선지")
        self.assertEqual(first["full_text"], first["extraction_text"])
        self.assertTrue(first["duplicate_text_group_id"])
        self.assertEqual(
            first["duplicate_text_group_id"],
            second["duplicate_text_group_id"],
        )
        self.assertEqual(fallback["extraction_text"], "원본 질문\n정답 선지\n오답 선지")
        self.assertEqual(
            fallback["text_policy_version"],
            self.policy["text_preprocessing"]["version"],
        )

    def test_choice_difference_keeps_texts_in_separate_groups(self):
        records = [
            self.build_record(
                "question-1",
                "자료",
                "질문",
                "자료\n질문",
                ["선지 A", "선지 B"],
            ),
            self.build_record(
                "question-2",
                "자료",
                "질문",
                "자료\n질문",
                ["선지 B", "선지 A"],
            ),
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            exam_path = self.write_exam_json(temporary_directory, records)
            result = self.prep_json(
                str(exam_path),
                self.policy["text_preprocessing"],
            )

        self.assertNotEqual(
            result.iloc[0]["extraction_text"],
            result.iloc[1]["extraction_text"],
        )
        self.assertEqual(set(result["duplicate_text_group_id"]), {""})

    def test_limit_is_applied_after_text_validation(self):
        records = [
            self.build_record(
                f"question-{index}",
                f"자료 {index}",
                "질문",
                f"자료 {index}\n질문",
            )
            for index in range(1, 4)
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            exam_path = self.write_exam_json(temporary_directory, records)
            result = self.prep_json(
                str(exam_path),
                self.policy["text_preprocessing"],
                limit=2,
            )

        self.assertEqual(
            list(result["problem_id"]),
            ["question-1", "question-2"],
        )

    def test_duplicate_problem_id_is_rejected(self):
        records = [
            self.build_record("question-1", "자료 1", "질문", "자료 1\n질문"),
            self.build_record("question-1", "자료 2", "질문", "자료 2\n질문"),
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            exam_path = self.write_exam_json(temporary_directory, records)
            with self.assertRaisesRegex(ValueError, "problem_id가 중복"):
                self.prep_json(
                    str(exam_path),
                    self.policy["text_preprocessing"],
                )


if __name__ == "__main__":
    unittest.main()

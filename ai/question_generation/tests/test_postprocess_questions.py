import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai.question_generation.postprocess_questions import (
    db_rows,
    explanation_messages,
    import_db,
    load_classifications,
    load_explanations,
    load_questions,
)


def sample_question() -> dict:
    choices = []
    for number in range(1, 6):
        choices.append(
            {
                "number": number,
                "text": f"{number}번 선지",
                "is_answer": number == 3,
                "source": {
                    "owner_label": f"{number}번 대상",
                    "fact_basis": f"{number}번 대상의 검수된 사실이다.",
                },
            }
        )
    return {
        "variant_key": "pack:answer:distractors",
        "target_score": 2,
        "era": "고려",
        "topic": "고려 광종",
        "topic_type": "인물",
        "minor_type": "자료 기반 시대·대상 추론",
        "material": "자료",
        "question": "옳은 것은?",
        "choices": choices,
        "answer_number": 3,
    }


class PostprocessQuestionsTest(unittest.TestCase):
    def test_load_and_build_db_rows_without_inference(self) -> None:
        question = sample_question()
        explanations = {str(number): f"{number}번 해설" for number in range(1, 6)}
        classification = {
            "service_era": "고려",
            "service_topic": "인물",
            "service_question_type": "역사 자료의 분석 및 해석",
            "service_question_subtype": "자료 기반 시대·대상 추론",
        }
        question_row, option_rows = db_rows(question, 1, classification, explanations)

        self.assertEqual(question_row[0], question["variant_key"])
        self.assertEqual(question_row[4], classification["service_topic"])
        self.assertEqual(question_row[5], "역사 자료의 분석 및 해석")
        self.assertEqual(question_row[12], explanations["3"])
        self.assertEqual(len(option_rows), 5)
        self.assertTrue(option_rows[2][3])

    def test_files_and_prompt_keep_exact_basis(self) -> None:
        question = sample_question()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "questions.json"
            explanations = root / "explanations.jsonl"
            classifications = root / "classifications.jsonl"
            source.write_text(
                json.dumps({"count": 1, "questions": [question]}, ensure_ascii=False),
                encoding="utf-8",
            )
            explanations.write_text(
                json.dumps(
                    {
                        "variant_key": question["variant_key"],
                        "choice_explanations": {str(number): f"{number}번 해설" for number in range(1, 6)},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            classifications.write_text(
                json.dumps(
                    {
                        "variant_key": question["variant_key"],
                        "service_era": "고려",
                        "service_topic": "인물",
                        "service_question_type": "역사 자료의 분석 및 해석",
                        "service_question_subtype": "자료 기반 시대·대상 추론",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(load_questions(source), [question])
            self.assertEqual(load_explanations(explanations)["pack:answer:distractors"]["5"], "5번 해설")
            self.assertEqual(load_classifications(classifications)["pack:answer:distractors"]["service_topic"], "인물")
            self.assertIn("1번 대상의 검수된 사실이다.", explanation_messages(question)[1]["content"])

    def test_replace_existing_updates_question_and_five_options_without_delete(self) -> None:
        question = sample_question()
        explanations = {str(number): f"{number}번 해설" for number in range(1, 6)}
        classification = {
            "service_era": "고려",
            "service_topic": "인물",
            "service_question_type": "역사 자료의 분석 및 해석",
            "service_question_subtype": "자료 기반 시대·대상 추론",
        }
        cursor = MagicMock()
        cursor.fetchall.return_value = [(question["variant_key"],)]
        cursor.fetchone.return_value = (42,)
        cursor.rowcount = 1
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        args = SimpleNamespace(
            input=Path("questions.json"),
            explanations=Path("explanations.jsonl"),
            classifications=Path("classifications.jsonl"),
            replace_existing=True,
            dry_run=False,
        )
        with (
            patch("ai.question_generation.postprocess_questions.load_questions", return_value=[question]),
            patch(
                "ai.question_generation.postprocess_questions.load_explanations",
                return_value={question["variant_key"]: explanations},
            ),
            patch(
                "ai.question_generation.postprocess_questions.load_classifications",
                return_value={question["variant_key"]: classification},
            ),
            patch("ai.question_generation.postprocess_questions.connect_db", return_value=connection),
        ):
            self.assertEqual(import_db(args), 0)

        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 7)
        self.assertIn("SET q_score", statements[1])
        self.assertNotIn("question_no =", statements[1])
        self.assertTrue(all("UPDATE question_options" in statement for statement in statements[2:]))
        self.assertFalse(any("DELETE" in statement for statement in statements))


if __name__ == "__main__":
    unittest.main()

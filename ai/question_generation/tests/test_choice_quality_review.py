from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai.question_generation.workflows.closed_pack_batch import run_choice_quality_review


class ChoiceQualityReviewTest(unittest.TestCase):
    @patch("ai.question_generation.workflows.closed_pack_batch.subprocess.run")
    def test_review_runs_last_as_report_and_excludes_image_choices(self, mocked_run) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "predict.py"
            model = root / "model"
            script.write_text("", encoding="utf-8")
            model.mkdir()
            for name in ("config.json", "model.safetensors", "tokenizer.json"):
                (model / name).write_text("", encoding="utf-8")

            def run(command: list[str], **_: object) -> SimpleNamespace:
                input_path = Path(command[command.index("--input") + 1])
                output_csv = Path(command[command.index("--output_csv") + 1])
                output_json = Path(command[command.index("--output_json") + 1])
                self.assertEqual([row["seed_id"] for row in json.loads(input_path.read_text("utf-8"))], ["text"])
                output_csv.write_text("검수상태\n통과\n", encoding="utf-8")
                output_json.write_text(
                    json.dumps(
                        {
                            "summary": {
                                "status_count": {"검수필요": 1, "통과": 4},
                                "error_code_count": {"ODD_DISTRACTOR": 1},
                                "total_choices": 5,
                            },
                            "rows": [
                                {
                                    "검수상태": "검수필요",
                                    "문항ID": "text",
                                    "선지번호": 1,
                                    "오류코드": "ODD_DISTRACTOR",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            mocked_run.side_effect = run
            choices = [{"number": number, "text": f"선지 {number}"} for number in range(1, 6)]
            result = run_choice_quality_review(
                root / "run",
                [
                    {"seed_id": "text", "choice_mode": "generated", "choices": choices},
                    {"seed_id": "image", "choice_mode": "image", "choices": choices},
                ],
                script=script,
                model_dir=model,
            )

            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["questions"], 1)
            self.assertEqual(result["excluded_image_questions"], 1)
            self.assertEqual(result["total_choices"], 5)
            self.assertEqual(result["warning_question_ids"], ["text"])
            tagged = json.loads(Path(result["tagged_questions"]).read_text(encoding="utf-8"))
            self.assertEqual(tagged["questions"][0]["review_tags"], ["ML주의"])
            self.assertNotIn("review_tags", tagged["questions"][1])


if __name__ == "__main__":
    unittest.main()

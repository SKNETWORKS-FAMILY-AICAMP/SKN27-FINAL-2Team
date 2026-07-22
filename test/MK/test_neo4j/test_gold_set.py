from copy import deepcopy
from json import loads
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


class GoldSetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))

        from common import load_pipeline_policy
        from goldset.build_gold_set import (
            build_candidate_annotations,
            build_gold_task_records,
            select_gold_tasks,
            write_gold_set,
        )
        from entity_resolution.semantic_review import write_jsonl

        cls.build_candidate_annotations = staticmethod(
            build_candidate_annotations
        )
        cls.build_gold_task_records = staticmethod(build_gold_task_records)
        cls.select_gold_tasks = staticmethod(select_gold_tasks)
        cls.write_gold_set = staticmethod(write_gold_set)
        cls.write_jsonl = staticmethod(write_jsonl)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def make_candidate(
        self,
        task_number: int,
        candidate_number: int,
        method: str,
    ) -> dict:
        candidate_id = f"candidate-{task_number}-{candidate_number}"
        alternative_id = f"alternative-{task_number}-{candidate_number}"
        return {
            "source_candidate_id": candidate_id,
            "source_record_id": f"AKS:ARTICLE:E{task_number}{candidate_number}:r1",
            "source": "AKS",
            "candidate_rank": candidate_number,
            "matched_name": f"용어-{task_number}",
            "matched_field": "name",
            "retrieval_method": method,
            "retrieval_score": 1.0,
            "category_compatibility": "COMPATIBLE",
            "normalized_names": [f"용어{task_number}"],
            "hanja": [],
            "era_values": ["조선"],
            "birth_year": "",
            "death_year": "",
            "bonkwan": [],
            "source_entity_type_proposal": "Concept",
            "code_proposed_role": "ALTERNATIVE_ENTITY",
            "code_canonical_alternative_id": alternative_id,
            "source_context": {"definition": "검수 문맥"},
        }

    def make_task(
        self,
        task_number: int,
        category: str,
        candidate_count: int,
        has_exact: bool,
    ) -> dict:
        candidates = []
        alternatives = []
        for candidate_number in range(1, candidate_count + 1):
            method = "name_ngram"
            if has_exact and candidate_number == 1:
                method = "exact"
            candidate = self.make_candidate(
                task_number,
                candidate_number,
                method,
            )
            candidates.append(candidate)
            alternatives.append(
                {
                    "canonical_alternative_id": candidate[
                        "code_canonical_alternative_id"
                    ],
                    "confidence_tier": "SINGLE_SOURCE_CANDIDATE",
                    "merge_signals": [],
                    "source_candidate_ids": [
                        candidate["source_candidate_id"]
                    ],
                }
            )
        return {
            "term_review_task_id": f"task-{task_number}",
            "resolution_case_id": f"case-{task_number}",
            "canonical_term": f"용어-{task_number}",
            "term_variants": [f"용어-{task_number}"],
            "category": category,
            "entity_type_proposal": "Concept",
            "problem_count": 1,
            "problem_context_samples": [
                {
                    "problem_id": f"problem-{task_number}",
                    "full_text": "검수할 기출 문항",
                }
            ],
            "source_candidates": candidates,
            "code_canonical_alternatives": alternatives,
            "relevant_pair_signals": [],
            "required_decision_status": "PROPOSED",
            "review_model": "test-model",
            "prompt_version": "test-prompt",
            "resolution_policy_version": "test-policy",
        }

    def make_policy(self) -> dict:
        policy = deepcopy(self.policy)
        gold_policy = policy["entity_resolution"]["semantic_review"][
            "gold_set"
        ]
        gold_policy["sample_size"] = 6
        gold_policy["minimum_cases_per_category"] = 1
        gold_policy["maximum_candidates_per_pilot_case"] = 3
        return policy

    def make_tasks(self) -> list[dict]:
        categories = ["인물", "사건", "제도"]
        tasks: list[dict] = []
        for task_number in range(1, 13):
            category = categories[(task_number - 1) % len(categories)]
            candidate_count = (task_number % 5) + 1
            tasks.append(
                self.make_task(
                    task_number,
                    category,
                    candidate_count,
                    task_number % 2 == 0,
                )
            )
        return tasks

    def test_selection_is_deterministic_and_covers_categories(self):
        tasks = self.make_tasks()
        policy = self.make_policy()

        selected = self.select_gold_tasks(tasks, policy)
        reversed_selected = self.select_gold_tasks(
            list(reversed(tasks)),
            policy,
        )

        selected_ids = [
            record["task"]["term_review_task_id"] for record in selected
        ]
        reversed_ids = [
            record["task"]["term_review_task_id"]
            for record in reversed_selected
        ]
        self.assertEqual(selected_ids, reversed_ids)
        self.assertEqual(len(selected_ids), 6)
        self.assertEqual(
            {record["profile"]["category"] for record in selected},
            {"인물", "사건", "제도"},
        )
        self.assertTrue(
            all(
                record["profile"]["candidate_count"] <= 3
                for record in selected
            )
        )

    def test_annotation_is_blind_and_preserves_candidate_evidence(self):
        policy = self.make_policy()
        selected = self.select_gold_tasks(self.make_tasks(), policy)
        gold_tasks = self.build_gold_task_records(selected, policy)

        annotations, baseline = self.build_candidate_annotations(gold_tasks)

        self.assertNotIn("code_proposed_role", annotations.columns)
        self.assertIn("code_proposed_role", baseline.columns)
        self.assertNotIn("reviewer", annotations.columns)
        self.assertNotIn("candidate_review_status", annotations.columns)
        self.assertTrue((annotations["gold_candidate_role"] == "").all())
        self.assertIn("gold_related_entity_key", annotations.columns)
        self.assertIn("gold_related_display_name", annotations.columns)
        self.assertIn("gold_related_entity_type", annotations.columns)
        self.assertTrue(
            annotations["candidate_pair_signals_json"].map(loads).map(
                lambda value: isinstance(value, list)
            ).all()
        )

    def test_write_gold_set_creates_auditable_outputs(self):
        tasks = self.make_tasks()
        policy = self.make_policy()
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "tasks.jsonl"
            output_dir = Path(temp_dir) / "gold"
            self.write_jsonl(tasks, str(input_path))

            paths = self.write_gold_set(
                tasks,
                str(input_path),
                str(output_dir),
                policy,
                generated_at="2026-07-21T00:00:00+00:00",
            )

            self.assertEqual(set(paths), set(
                policy["entity_resolution"]["semantic_review"]["gold_set"][
                    "output_files"
                ]
            ))
            self.assertTrue(all(Path(path).is_file() for path in paths.values()))
            manifest = loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["sample_case_count"], 6)
            self.assertGreater(manifest["sample_candidate_count"], 0)
            self.assertEqual(
                manifest["generated_at"],
                "2026-07-21T00:00:00+00:00",
            )

    def test_started_human_review_requires_explicit_overwrite(self):
        tasks = self.make_tasks()
        policy = self.make_policy()
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "tasks.jsonl"
            output_dir = Path(temp_dir) / "internal"
            review_dir = Path(temp_dir) / "review"
            self.write_jsonl(tasks, str(input_path))
            paths = self.write_gold_set(
                tasks,
                str(input_path),
                str(output_dir),
                policy,
                review_output_dir=str(review_dir),
            )
            case_path = Path(paths["case_annotations"])
            cases = pd.read_csv(
                case_path,
                encoding="utf-8-sig",
                dtype=str,
                keep_default_na=False,
            )
            cases.loc[0, "case_review_status"] = "IN_PROGRESS"
            cases.to_csv(
                case_path,
                index=False,
                encoding="utf-8-sig",
            )

            with self.assertRaises(FileExistsError):
                self.write_gold_set(
                    tasks,
                    str(input_path),
                    str(output_dir),
                    policy,
                    review_output_dir=str(review_dir),
                )

            self.write_gold_set(
                tasks,
                str(input_path),
                str(output_dir),
                policy,
                review_output_dir=str(review_dir),
                allow_review_overwrite=True,
            )


if __name__ == "__main__":
    unittest.main()

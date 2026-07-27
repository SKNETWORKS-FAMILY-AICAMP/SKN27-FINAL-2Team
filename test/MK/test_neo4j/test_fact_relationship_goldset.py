from copy import deepcopy
import tempfile
import sys
import unittest
from pathlib import Path

import pandas as pd


class FactRelationshipGoldsetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from goldset.build_fact_relationship_goldset import (
            build_goldset_tables,
            load_goldset_policy,
            select_fact_gold_sample,
            write_fact_relationship_goldset,
        )

        cls.build_tables = staticmethod(build_goldset_tables)
        cls.load_policy = staticmethod(load_goldset_policy)
        cls.select_sample = staticmethod(select_fact_gold_sample)
        cls.write_goldset = staticmethod(
            write_fact_relationship_goldset
        )
        cls.policy = load_goldset_policy(
            str(
                neo4j_root
                / "config"
                / "fact_relationship_goldset.json"
            )
        )

    def make_policy(self) -> dict:
        policy = deepcopy(self.policy)
        policy["sample_size"] = 6
        policy["minimum_cases_per_relation_type"] = 1
        return policy

    def make_facts(self) -> pd.DataFrame:
        rows: list[dict] = []
        fact_number = 0
        for relation_type, count in [
            ("AUTHORED", 5),
            ("HAS_TEACHER", 4),
            ("LOCATED_IN", 3),
            ("ASSOCIATED_WITH_POLITY", 4),
        ]:
            for index in range(count):
                fact_number += 1
                rows.append(
                    {
                        "canonical_relationship_id": (
                            f"CF-{fact_number}"
                        ),
                        "start_canonical_id": f"C-{fact_number}",
                        "end_canonical_id": f"C-{fact_number + 20}",
                        "relation_type": relation_type,
                        "evidence_sentences_json": '["근거 문장"]',
                        "evidence_urls_json": '["https://example.test"]',
                        "detail_urls_json": '["https://example.test"]',
                        "source_datasets_json": '["AKS_DESCRIPTION"]',
                        "raw_relation_types_json": '["RULE"]',
                        "extraction_methods_json": (
                            '["OFFICIAL_DESCRIPTION_PATTERN"]'
                        ),
                        "verification_statuses_json": (
                            '["PATTERN_ASSERTED"]'
                        ),
                        "evidence_count": "1",
                        "source_row_count": "1",
                        "verification_status": "PATTERN_ASSERTED",
                        "policy_version": "test",
                    }
                )
        return pd.DataFrame(rows)

    def make_registry(self, facts: pd.DataFrame) -> pd.DataFrame:
        canonical_ids = set(facts["start_canonical_id"]).union(
            facts["end_canonical_id"]
        )
        return pd.DataFrame(
            [
                {
                    "canonical_id": canonical_id,
                    "display_name": f"이름-{canonical_id}",
                    "entity_type": "Concept",
                }
                for canonical_id in canonical_ids
            ]
        )

    def test_selection_is_deterministic_and_excludes_classification(self):
        facts = self.make_facts()
        policy = self.make_policy()

        selected = self.select_sample(facts, policy)
        reversed_selected = self.select_sample(
            facts.iloc[::-1].reset_index(drop=True),
            policy,
        )

        self.assertEqual(
            list(selected["canonical_relationship_id"]),
            list(reversed_selected["canonical_relationship_id"]),
        )
        self.assertEqual(len(selected), 6)
        self.assertNotIn(
            "ASSOCIATED_WITH_POLITY",
            set(selected["relation_type"]),
        )
        self.assertEqual(
            set(selected["relation_type"]),
            {"AUTHORED", "HAS_TEACHER", "LOCATED_IN"},
        )

    def test_human_review_is_blind_and_has_empty_annotations(self):
        facts = self.make_facts()
        policy = self.make_policy()
        registry = self.make_registry(facts)

        tables = self.build_tables(facts, registry, policy)
        review = tables["human_review"]

        self.assertNotIn("verification_status", review.columns)
        self.assertNotIn("extraction_methods_json", review.columns)
        self.assertTrue(
            review["gold_relation_judgment"].eq("").all()
        )
        self.assertTrue(
            review["review_status"].eq("NOT_STARTED").all()
        )

    def test_started_review_is_not_overwritten(self):
        facts = self.make_facts()
        policy = self.make_policy()
        registry = self.make_registry(facts)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            facts_path = temp_path / "facts.csv"
            registry_path = temp_path / "registry.csv"
            output_directory = temp_path / "goldset"
            facts.to_csv(facts_path, index=False, encoding="utf-8-sig")
            registry.to_csv(
                registry_path,
                index=False,
                encoding="utf-8-sig",
            )
            manifest = self.write_goldset(
                facts_path,
                registry_path,
                output_directory,
                policy,
            )
            review_path = Path(
                manifest["output_paths"]["human_review"]
            )
            review = pd.read_csv(
                review_path,
                dtype=str,
                encoding="utf-8-sig",
                keep_default_na=False,
            )
            review.loc[0, "review_status"] = "IN_PROGRESS"
            review.to_csv(
                review_path,
                index=False,
                encoding="utf-8-sig",
            )

            with self.assertRaises(FileExistsError):
                self.write_goldset(
                    facts_path,
                    registry_path,
                    output_directory,
                    policy,
                )


if __name__ == "__main__":
    unittest.main()

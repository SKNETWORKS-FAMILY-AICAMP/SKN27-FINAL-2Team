from __future__ import annotations

from json import loads
from pathlib import Path
import sys
from unittest import TestCase

import pandas as pd


sys.path.append(
    str(
        Path(__file__).resolve().parents[3]
        / "etl"
        / "preprocessing"
        / "neo4j"
    )
)

from runners.run_merge_exam_term_nlp_relations import merge_candidate_tables


class MergeExamTermNlpRelationsTest(TestCase):
    def test_duplicate_relation_unions_evidence(self) -> None:
        first = pd.DataFrame(
            [
                {
                    "nlp_relation_candidate_id": "relation:1",
                    "start_node_id": "entity:1",
                    "start_display_name": "흥선 대원군",
                    "end_node_id": "entity:2",
                    "end_display_name": "경복궁",
                    "relation_family": "CREATE_OR_PRODUCE",
                    "relation_type": "RESTORED",
                    "evidence_count": "1",
                    "anchor_exam_term_count": "1",
                    "anchor_exam_term_ids_json": '["term:1"]',
                    "candidate_statuses_json": (
                        '["NLP_REVIEW_REGISTERED"]'
                    ),
                    "maximum_candidate_score": "8",
                    "source_datasets_json": '["AKS"]',
                    "evidence_ids_json": '["evidence:1"]',
                    "touches_open_entity": "False",
                    "policy_version": "policy:1",
                }
            ]
        )
        second = first.copy()
        second.loc[0, "evidence_count"] = "2"
        second.loc[0, "anchor_exam_term_ids_json"] = (
            '["term:1", "term:2"]'
        )
        second.loc[0, "candidate_statuses_json"] = (
            '["NLP_HIGH_CONFIDENCE_REGISTERED"]'
        )
        second.loc[0, "maximum_candidate_score"] = "12"
        second.loc[0, "source_datasets_json"] = (
            '["NEW_KOREAN_HISTORY"]'
        )
        second.loc[0, "evidence_ids_json"] = (
            '["evidence:2", "evidence:3"]'
        )

        merged, statistics = merge_candidate_tables(
            [first, second]
        )

        self.assertEqual(len(merged), 1)
        row = merged.iloc[0]
        self.assertEqual(int(row["evidence_count"]), 3)
        self.assertEqual(int(row["anchor_exam_term_count"]), 2)
        self.assertEqual(int(row["maximum_candidate_score"]), 12)
        self.assertEqual(
            loads(row["anchor_exam_term_ids_json"]),
            ["term:1", "term:2"],
        )
        self.assertEqual(
            loads(row["source_datasets_json"]),
            ["AKS", "NEW_KOREAN_HISTORY"],
        )
        self.assertEqual(
            loads(row["evidence_ids_json"]),
            ["evidence:1", "evidence:2", "evidence:3"],
        )
        self.assertEqual(
            row["relation_display"],
            "흥선 대원군 -[RESTORED]-> 경복궁",
        )
        self.assertEqual(
            statistics["input_relation_candidate_count"],
            2,
        )
        self.assertEqual(
            statistics["unique_relation_candidate_count"],
            1,
        )
        self.assertEqual(
            statistics["merged_duplicate_row_count"],
            1,
        )

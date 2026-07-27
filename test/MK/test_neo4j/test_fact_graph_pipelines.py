from __future__ import annotations

from json import load, loads
import os
from pathlib import Path
import sys
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pandas as pd


sys.path.append(
    str(
        Path(__file__).resolve().parents[3]
        / "etl"
        / "preprocessing"
        / "neo4j"
    )
)

from run_fact_graph_eda_pipeline import build_review_tables
from run_fact_graph_load_pipeline import build_load_tables, load_to_neo4j


class FactGraphPipelinesTest(TestCase):
    def setUp(self) -> None:
        config_path = (
            Path(__file__).resolve().parents[3]
            / "etl"
            / "preprocessing"
            / "neo4j"
            / "config"
            / "fact_graph_pipeline.json"
        )
        with config_path.open("r", encoding="utf-8") as input_file:
            self.policy = load(input_file)
        self.policy["review_routing"][
            "endpoint_priority_minimum_relation_count"
        ] = 1
        base = {
            "minimum_exam_anchor_hops": "1",
            "anchor_exam_term_ids_json": "[\"E1\"]",
            "evidence_count": "1",
            "evidence_ids_json": "[\"EV1\"]",
            "evidence_records_json": (
                "[{\"evidence_id\":\"EV1\","
                "\"source_record_id\":\"\","
                "\"source_document_id\":\"DOC1\","
                "\"source_dataset\":\"OFFICIAL\","
                "\"source_url\":\"https://example.test/1\","
                "\"source_text\":\"verified text\","
                "\"evidence_kind\":\"OFFICIAL_TEXT_CLAUSE\"}]"
            ),
            "evidence_metadata_complete": "True",
            "source_datasets_json": "[\"OFFICIAL\"]",
            "verification_status": "SOURCE_ASSERTED",
            "auto_load_eligible": "False",
            "llm_used": "False",
            "neo4j_load": "False",
            "policy_version": "test",
        }
        rows = [
            {
                **base,
                "fact_graph_candidate_id": "T1",
                "start_node_id": "C1",
                "start_node_kind": "CANONICAL",
                "start_display_name": "흥선 대원군",
                "start_entity_type": "Person",
                "relation_type": "RESTORED",
                "end_node_id": "C2",
                "end_node_kind": "CANONICAL",
                "end_display_name": "경복궁",
                "end_entity_type": "Heritage",
                "candidate_tier": "CANONICAL_FACT_ASSERTED",
                "relation_origin": "CANONICAL_FACT",
            },
            {
                **base,
                "fact_graph_candidate_id": "N1",
                "start_node_id": "C1",
                "start_node_kind": "CANONICAL",
                "start_display_name": "흥선 대원군",
                "start_entity_type": "Person",
                "relation_type": "OPPOSED",
                "end_node_id": "O1",
                "end_node_kind": "OPEN_ENTITY_CANDIDATE",
                "end_display_name": "왕",
                "end_entity_type": "Person",
                "candidate_tier": "NLP_STRICT",
                "relation_origin": "NLP",
            },
            {
                **base,
                "fact_graph_candidate_id": "N2",
                "start_node_id": "C2",
                "start_node_kind": "CANONICAL",
                "start_display_name": "경복궁",
                "start_entity_type": "Heritage",
                "relation_type": "RELATED_TO",
                "end_node_id": "O2",
                "end_node_kind": "OPEN_ENTITY_CANDIDATE",
                "end_display_name": " 왕 ",
                "end_entity_type": "Person",
                "candidate_tier": "NLP_ENDPOINT_TYPE_REVIEW",
                "relation_origin": "NLP",
            },
        ]
        self.candidates = pd.DataFrame(rows)

    def test_eda_separates_trusted_and_human_review_rows(self) -> None:
        tables, statistics = build_review_tables(
            self.candidates,
            self.policy,
        )

        self.assertEqual(statistics["trusted_load_candidate_count"], 1)
        self.assertEqual(statistics["duplicate_name_group_count"], 1)
        self.assertEqual(statistics["entity_review_node_count"], 2)
        self.assertEqual(len(tables["relation_review"]), 0)
        self.assertEqual(len(tables["deferred_relations"]), 2)
        self.assertEqual(
            statistics["relation_review_pending_count"],
            0,
        )

    def test_trusted_load_does_not_require_human_review(self) -> None:
        tables, statistics = build_load_tables(
            self.candidates,
            pd.DataFrame(),
            pd.DataFrame(),
            self.policy,
            "trusted_only",
        )

        self.assertEqual(statistics["load_relationship_count"], 1)
        self.assertEqual(statistics["load_node_count"], 2)
        self.assertEqual(
            set(tables["facts"]["predicate"]),
            {"RESTORED"},
        )
        self.assertEqual(statistics["load_evidence_count"], 1)
        self.assertEqual(
            set(tables["relationships"]["relation_type"]),
            {"SUBJECT", "OBJECT", "SUPPORTED_BY"},
        )

    def test_provisional_load_keeps_unresolved_endpoint_facts(self) -> None:
        tables, statistics = build_load_tables(
            self.candidates,
            pd.DataFrame(),
            pd.DataFrame(),
            self.policy,
            "trusted_and_provisional",
        )

        self.assertEqual(statistics["trusted_selected_count"], 1)
        self.assertEqual(statistics["provisional_selected_count"], 2)
        self.assertEqual(statistics["load_fact_count"], 3)
        self.assertEqual(statistics["verified_fact_count"], 1)
        self.assertEqual(statistics["provisional_fact_count"], 2)
        self.assertEqual(statistics["default_retrieval_fact_count"], 1)
        provisional_facts = tables["facts"][
            tables["facts"]["trust_status"].eq("PROVISIONAL")
        ]
        self.assertTrue(
            provisional_facts[
                "default_retrieval_eligible"
            ].eq(False).all()
        )
        provisional_nodes = tables["nodes"][
            tables["nodes"]["resolution_status"].eq("PROVISIONAL")
        ]
        self.assertEqual(
            set(provisional_nodes["node_id"]),
            {"O1", "O2"},
        )

    def test_reviewed_load_applies_entity_mapping(self) -> None:
        duplicate_relation_candidates = self.candidates.copy()
        duplicate_relation_candidates.loc[
            duplicate_relation_candidates[
                "fact_graph_candidate_id"
            ].eq("N2"),
            [
                "start_node_id",
                "start_node_kind",
                "start_display_name",
                "start_entity_type",
                "relation_type",
            ],
        ] = [
            "C1",
            "CANONICAL",
            "흥선 대원군",
            "Person",
            "OPPOSED",
        ]
        duplicate_relation_candidates.loc[
            duplicate_relation_candidates[
                "fact_graph_candidate_id"
            ].isin(["N1", "N2"]),
            "end_node_kind",
        ] = "OFFICIAL_SOURCE"
        reviews, _ = build_review_tables(
            duplicate_relation_candidates,
            self.policy,
        )
        entity_review = pd.DataFrame(
            [
                {
                    "entity_review_id": "ER1",
                    "node_id": "O1",
                    "review_decision": "KEEP_AS_IS",
                    "review_target_node_id": "",
                },
                {
                    "entity_review_id": "ER2",
                    "node_id": "O2",
                    "review_decision": "MERGE_INTO",
                    "review_target_node_id": "O1",
                },
            ]
        )
        relation_review = reviews["relation_review"].copy()
        relation_review["review_decision"] = "APPROVE"

        tables, statistics = build_load_tables(
            duplicate_relation_candidates,
            entity_review,
            relation_review,
            self.policy,
            "reviewed_all",
        )

        self.assertEqual(statistics["redirected_entity_count"], 1)
        self.assertNotIn("O2", set(tables["nodes"]["node_id"]))
        self.assertEqual(statistics["load_relationship_count"], 2)
        self.assertTrue(
            statistics["relationship_assertion_count_preserved"]
        )
        merged_relation = tables["facts"][
            tables["facts"]["subject_node_id"].eq("C1")
            & tables["facts"]["predicate"].eq("OPPOSED")
            & tables["facts"]["object_node_id"].eq("O1")
        ]
        self.assertEqual(len(merged_relation), 1)
        self.assertEqual(
            int(merged_relation.iloc[0]["assertion_count"]),
            2,
        )
        self.assertEqual(
            set(
                loads(
                    merged_relation.iloc[0][
                        "fact_graph_candidate_ids_json"
                    ]
                )
            ),
            {"N1", "N2"},
        )
        self.assertEqual(
            tables["facts"]["fact_id"].nunique(),
            2,
        )

    def test_self_relation_after_mapping_is_retained_for_review(
        self,
    ) -> None:
        self_relation = self.candidates.iloc[1].copy()
        self_relation["fact_graph_candidate_id"] = "N3"
        self_relation["start_node_id"] = "O1"
        self_relation["start_node_kind"] = "OFFICIAL_SOURCE"
        self_relation["start_display_name"] = "왕"
        self_relation["end_node_id"] = "O2"
        self_relation["end_node_kind"] = "OFFICIAL_SOURCE"
        self_relation["end_display_name"] = " 왕 "
        candidates = pd.concat(
            [
                self.candidates,
                pd.DataFrame([self_relation]),
            ],
            ignore_index=True,
        )
        reviews, _ = build_review_tables(candidates, self.policy)
        entity_review = pd.DataFrame(
            [
                {
                    "entity_review_id": "ER1",
                    "node_id": "O1",
                    "review_decision": "KEEP_AS_IS",
                    "review_target_node_id": "",
                },
                {
                    "entity_review_id": "ER2",
                    "node_id": "O2",
                    "review_decision": "MERGE_INTO",
                    "review_target_node_id": "O1",
                },
            ]
        )
        relation_review = reviews["relation_review"].copy()
        relation_review["review_decision"] = "APPROVE"

        tables, statistics = build_load_tables(
            candidates,
            entity_review,
            relation_review,
            self.policy,
            "reviewed_all",
        )

        self.assertEqual(statistics["load_relationship_count"], 1)
        self.assertEqual(
            statistics["self_relation_after_mapping_count"],
            1,
        )
        self.assertTrue(
            statistics["relationship_assertion_count_preserved"]
        )
        self_relation_rows = tables["mapping_review"][
            tables["mapping_review"]["fact_graph_candidate_id"].eq("N3")
        ]
        self.assertEqual(len(self_relation_rows), 1)
        self.assertEqual(
            self_relation_rows.iloc[0]["review_reason"],
            "SELF_RELATION_REVIEW_REQUIRED",
        )

    def test_reviewed_load_rejects_pending_relation_review(self) -> None:
        candidates = self.candidates.copy()
        candidates.loc[
            candidates["fact_graph_candidate_id"].eq("N1"),
            "end_node_kind",
        ] = "OFFICIAL_SOURCE"
        reviews, _ = build_review_tables(
            candidates,
            self.policy,
        )

        with self.assertRaisesRegex(ValueError, "Relation review is pending"):
            build_load_tables(
                candidates,
                reviews["entity_review"],
                reviews["relation_review"],
                self.policy,
                "reviewed_all",
            )

    def test_missing_evidence_is_excluded_from_fact_graph(self) -> None:
        candidates = self.candidates.iloc[[0]].copy()
        candidates["evidence_ids_json"] = "[]"
        candidates["evidence_records_json"] = "[]"
        candidates["evidence_metadata_complete"] = "False"

        tables, statistics = build_load_tables(
            candidates,
            pd.DataFrame(),
            pd.DataFrame(),
            self.policy,
            "trusted_only",
        )

        self.assertEqual(statistics["load_fact_count"], 0)
        self.assertEqual(
            statistics["missing_evidence_candidate_count"],
            1,
        )
        self.assertEqual(len(tables["evidence"]), 0)

    def test_neo4j_load_uses_fact_and_evidence_schema(self) -> None:
        tables, _ = build_load_tables(
            self.candidates,
            pd.DataFrame(),
            pd.DataFrame(),
            self.policy,
            "trusted_only",
        )
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value.consume.return_value = None

        with patch.dict(
            os.environ,
            {"FACT_NEO4J_PASSWORD": "test-password"},
        ), patch(
            "neo4j.GraphDatabase.driver",
            return_value=driver,
        ):
            statistics = load_to_neo4j(tables, self.policy)

        queries = [
            str(call.args[0])
            for call in session.run.call_args_list
        ]
        query_text = "\n".join(queries)
        self.assertIn("FOR (n:Fact)", query_text)
        self.assertIn("MERGE (f:Fact", query_text)
        self.assertIn("MERGE (e:EvidenceSpan", query_text)
        self.assertIn("[r:SUPPORTED_BY", query_text)
        self.assertIn(
            "f.default_retrieval_eligible = "
            "row.default_retrieval_eligible",
            query_text,
        )
        self.assertIn(
            "n.resolution_status = row.resolution_status",
            query_text,
        )
        self.assertEqual(statistics["loaded_fact_count"], 1)

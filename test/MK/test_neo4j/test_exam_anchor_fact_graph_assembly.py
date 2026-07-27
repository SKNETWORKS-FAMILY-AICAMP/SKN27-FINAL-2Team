from __future__ import annotations

from json import load
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

from runners.run_assemble_exam_anchor_fact_graph import (
    build_fact_graph_tables,
)


class ExamAnchorFactGraphAssemblyTest(TestCase):
    def test_keeps_structured_facts_within_configured_hops(
        self,
    ) -> None:
        config_path = (
            Path(__file__).resolve().parents[3]
            / "etl"
            / "preprocessing"
            / "neo4j"
            / "config"
            / "exam_anchor_fact_graph_assembly.json"
        )
        with config_path.open(
            "r",
            encoding="utf-8",
        ) as input_file:
            policy = load(input_file)
        exam_links = pd.DataFrame(
            [
                {
                    "exam_term_id": "E1",
                    "canonical_id": "C1",
                    "match_status": "ACCEPTED",
                }
            ]
        )
        source_links = pd.DataFrame(
            [
                {
                    "source_record_id": "S1",
                    "canonical_id": "C1",
                    "match_status": "ACCEPTED",
                }
            ]
        )
        source_nodes = pd.DataFrame(
            [
                {
                    "source_record_id": source_id,
                    "display_name": source_id,
                    "record_type": "PERSON",
                }
                for source_id in ["S1", "S2", "S3", "S4"]
            ]
        )
        source_relationships = pd.DataFrame(
            [
                {
                    "source_relationship_id": "R1",
                    "start_source_record_id": "S1",
                    "end_source_record_id": "S2",
                    "relation_type": "HAS_TEACHER",
                    "source_row_count": "1",
                    "source_dataset": "ITKC",
                    "verification_status": "SOURCE_ASSERTED",
                },
                {
                    "source_relationship_id": "R2",
                    "start_source_record_id": "S2",
                    "end_source_record_id": "S3",
                    "relation_type": "HAS_TEACHER",
                    "source_row_count": "1",
                    "source_dataset": "ITKC",
                    "verification_status": "SOURCE_ASSERTED",
                },
                {
                    "source_relationship_id": "R3",
                    "start_source_record_id": "S3",
                    "end_source_record_id": "S4",
                    "relation_type": "HAS_TEACHER",
                    "source_row_count": "1",
                    "source_dataset": "ITKC",
                    "verification_status": "SOURCE_ASSERTED",
                },
                {
                    "source_relationship_id": "RC",
                    "start_source_record_id": "S1",
                    "end_source_record_id": "S2",
                    "relation_type": "IN_TOP_CATEGORY",
                    "source_row_count": "1",
                    "source_dataset": "THESAURUS",
                    "verification_status": "SOURCE_ASSERTED",
                },
            ]
        )
        canonical_registry = pd.DataFrame(
            [
                {
                    "canonical_id": "C1",
                    "display_name": "C1",
                    "entity_type": "Person",
                },
                {
                    "canonical_id": "C2",
                    "display_name": "C2",
                    "entity_type": "Person",
                },
            ]
        )
        canonical_facts = pd.DataFrame(
            [
                {
                    "canonical_relationship_id": "CF1",
                    "start_canonical_id": "C1",
                    "end_canonical_id": "C2",
                    "relation_type": "HAS_TEACHER",
                    "evidence_count": "1",
                    "source_relationship_ids_json": "[]",
                    "description_mention_ids_json": "[\"D1\"]",
                    "source_datasets_json": "[\"ITKC\"]",
                    "verification_status": "SOURCE_ASSERTED",
                }
            ]
        )
        nlp_row = {
            "safe_relation_candidate_id": "N1",
            "start_node_id": "C1",
            "start_node_kind": "CANONICAL",
            "start_display_name": "C1",
            "start_entity_type": "Person",
            "relation_type": "AUTHORED",
            "end_node_id": "W1",
            "end_node_kind": "CANONICAL",
            "end_display_name": "W1",
            "end_entity_type": "Work",
            "anchor_exam_term_ids_json": "[\"E1\"]",
            "evidence_count": "1",
            "evidence_ids_json": "[\"NE1\"]",
            "source_datasets_json": "[\"AKS\"]",
            "gate_status": "GATE_PASSED_CORROBORATED",
        }
        tables, statistics = build_fact_graph_tables(
            exam_links,
            source_links,
            source_nodes,
            source_relationships,
            canonical_registry,
            canonical_facts,
            pd.DataFrame([nlp_row]),
            pd.DataFrame([nlp_row]),
            policy,
            description_mentions=pd.DataFrame(
                [
                    {
                        "description_mention_id": "D1",
                        "source_record_id": "S1",
                        "source": "AKS",
                        "source_release": "release-1",
                        "evidence_field": "definition",
                        "evidence_sentence": "C1 has teacher C2.",
                        "evidence_url": "https://example.test/D1",
                    }
                ]
            ),
            nlp_evidence=pd.DataFrame(
                [
                    {
                        "nlp_relation_evidence_id": "NE1",
                        "source_dataset": "AKS",
                        "source_document_id": "S1",
                        "atomic_clause_text": "C1 authored W1.",
                    }
                ]
            ),
        )

        self.assertEqual(
            set(
                tables["structured_source"][
                    "fact_graph_candidate_id"
                ]
            ),
            {"structured:R1", "structured:R2"},
        )
        self.assertEqual(
            statistics["all_fact_graph_candidate_count"],
            5,
        )
        self.assertEqual(
            statistics["both_nlp_endpoints_open_count"],
            0,
        )
        structured_r1 = tables["structured_source"][
            tables["structured_source"][
                "fact_graph_candidate_id"
            ].eq("structured:R1")
        ].iloc[0]
        self.assertEqual(structured_r1["start_node_id"], "C1")
        self.assertEqual(structured_r1["start_node_kind"], "CANONICAL")
        self.assertEqual(
            statistics["evidence_metadata_incomplete_candidate_count"],
            0,
        )
        self.assertEqual(
            int(
                tables["nlp"]["candidate_tier"]
                .eq("NLP_CORROBORATED_STABLE")
                .sum()
            ),
            1,
        )

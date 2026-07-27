from __future__ import annotations

import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path


class FactGraphReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(project_root))

        from etl.preprocessing.neo4j.fact_retrieval.fact_graph_release import (
            build_fact_graph_release,
            read_json,
        )

        config = read_json(neo4j_root / "config" / "fact_graph_release.json")
        cls.config = config
        cls.package = build_fact_graph_release(
            neo4j_root / "output",
            config,
        )

    def test_quarantined_identity_is_not_an_active_graph_endpoint(self) -> None:
        quarantined_node_id = (
            "ITKC:PERSON:P041198:sha256-2bfbf7f8ab4b1538"
        )
        active_entity_source_ids = {
            source_node_id
            for entity in self.package["entities"]
            for source_node_id in json.loads(
                entity["source_node_ids_json"]
            )
        }
        active_fact_source_ids = {
            source_node_id
            for fact in self.package["facts"]
            for source_node_id in (
                fact["subject_source_node_id"],
                fact["object_source_node_id"],
            )
        }

        self.assertNotIn(quarantined_node_id, active_entity_source_ids)
        self.assertNotIn(quarantined_node_id, active_fact_source_ids)
        self.assertTrue(
            any(
                quarantined_node_id
                in json.loads(
                    row["quarantined_source_node_ids_json"]
                )
                for row in self.package["quarantined_facts"]
            )
        )
        self.assertTrue(
            all(
                json.loads(row["evidence_records_json"])
                for row in self.package["quarantined_facts"]
            )
        )

    def test_quarantined_identity_remains_as_source_provenance(self) -> None:
        quarantined_node_id = (
            "ITKC:PERSON:P041198:sha256-2bfbf7f8ab4b1538"
        )
        source_record_by_id = {
            row["source_record_id"]: row
            for row in self.package["source_records"]
        }

        self.assertIn(quarantined_node_id, source_record_by_id)
        self.assertEqual(
            source_record_by_id[quarantined_node_id]["identity_status"],
            "SOURCE_CONFLICT",
        )

    def test_contextual_merge_never_crosses_predicates(self) -> None:
        fact_by_id = {
            row["fact_id"]: row
            for row in self.package["facts"]
        }
        for entity in self.package["entities"]:
            if entity["source_node_kind"] != "CONTEXTUAL_GROUP":
                continue
            related_predicates = {
                fact["predicate"]
                for fact in fact_by_id.values()
                if entity["entity_id"]
                in {
                    fact["subject_entity_id"],
                    fact["object_entity_id"],
                }
            }
            self.assertEqual(
                related_predicates,
                {entity["context_predicate"]},
            )

    def test_source_node_is_represented_by_at_most_one_entity(self) -> None:
        entity_ids_by_source_node_id: dict[str, set[str]] = defaultdict(set)
        for entity in self.package["entities"]:
            for source_node_id in json.loads(
                entity["source_node_ids_json"]
            ):
                entity_ids_by_source_node_id[source_node_id].add(
                    entity["entity_id"]
                )

        duplicated_source_nodes = {
            source_node_id: entity_ids
            for source_node_id, entity_ids
            in entity_ids_by_source_node_id.items()
            if len(entity_ids) > 1
        }
        self.assertFalse(duplicated_source_nodes)

    def test_same_evidence_and_predicate_has_one_endpoint_pair(self) -> None:
        endpoint_pairs_by_evidence: dict[
            tuple[str, str],
            set[tuple[str, str]],
        ] = defaultdict(set)
        for fact in self.package["facts"]:
            endpoint_pair = (
                fact["subject_entity_id"],
                fact["object_entity_id"],
            )
            for evidence_id in json.loads(fact["evidence_ids_json"]):
                endpoint_pairs_by_evidence[
                    (evidence_id, fact["predicate"])
                ].add(endpoint_pair)

        duplicated_evidence_relations = {
            key: endpoint_pairs
            for key, endpoint_pairs
            in endpoint_pairs_by_evidence.items()
            if len(endpoint_pairs) > 1
        }
        self.assertFalse(duplicated_evidence_relations)

    def test_projection_preserves_fact_evidence_links(self) -> None:
        expected_links = {
            (fact["fact_id"], evidence_id)
            for fact in self.package["facts"]
            for evidence_id in json.loads(fact["evidence_ids_json"])
        }
        actual_links = {
            (row["fact_id"], row["evidence_id"])
            for row in self.package["fact_evidence_links"]
        }
        evidence_ids = {
            row["evidence_id"]
            for row in self.package["evidence"]
        }

        self.assertEqual(actual_links, expected_links)
        self.assertTrue(
            {
                evidence_id
                for _, evidence_id in expected_links
            }.issubset(evidence_ids)
        )
        self.assertGreater(
            sum(
                fact["endpoint_projection_status"]
                == "CANONICAL_DUPLICATE_COLLAPSED"
                for fact in self.package["facts"]
            ),
            0,
        )

    def test_projected_source_endpoints_remain_in_provenance(self) -> None:
        provisional_source_node_kinds = set(
            self.config["provisional_source_node_kinds"]
        )
        expected_source_record_ids = {
            source_node_id
            for fact in self.package["facts"]
            for source_node_id, source_node_kind in (
                (
                    fact["subject_source_node_id"],
                    fact["subject_node_kind"],
                ),
                (
                    fact["object_source_node_id"],
                    fact["object_node_kind"],
                ),
            )
            if source_node_kind in provisional_source_node_kinds
        }
        actual_source_record_ids = {
            row["source_record_id"]
            for row in self.package["source_records"]
        }

        self.assertTrue(
            expected_source_record_ids.issubset(actual_source_record_ids)
        )

    def test_redirected_identity_uses_preferred_graph_endpoint(self) -> None:
        redirected_node_id = (
            "ITKC:PERSON:P006075:sha256-2bfbf7f8ab4b1538"
        )
        preferred_node_id = (
            "ITKC:PERSON:P008139:sha256-2bfbf7f8ab4b1538"
        )
        source_record_by_id = {
            row["source_record_id"]: row
            for row in self.package["source_records"]
        }

        self.assertEqual(
            source_record_by_id[redirected_node_id]["identity_status"],
            "REDIRECTED",
        )
        self.assertEqual(
            source_record_by_id[redirected_node_id][
                "preferred_source_node_id"
            ],
            preferred_node_id,
        )
        self.assertFalse(
            any(
                redirected_node_id
                in {
                    fact["subject_identity_node_id"],
                    fact["object_identity_node_id"],
                }
                for fact in self.package["facts"]
            )
        )
        self.assertTrue(
            any(
                preferred_node_id
                in {
                    fact["subject_identity_node_id"],
                    fact["object_identity_node_id"],
                }
                and redirected_node_id
                in {
                    fact["subject_source_node_id"],
                    fact["object_source_node_id"],
                }
                for fact in self.package["facts"]
            )
        )
        self.assertTrue(
            any(
                "IDENTITY_REDIRECT_SELF_RELATION"
                in json.loads(row["reason_codes_json"])
                for row in self.package["quarantined_facts"]
            )
        )

    def test_symmetric_semantic_relations_are_not_mirrored(self) -> None:
        symmetric_predicates = set(
            self.config["fact_projection_deduplication"][
                "symmetric_predicates"
            ]
        )
        relation_keys = {
            (
                row["subject_entity_id"],
                row["predicate"],
                row["object_entity_id"],
            )
            for row in self.package["semantic_relations"]
        }
        mirrored_relations = {
            key
            for key in relation_keys
            if key[1] in symmetric_predicates
            and (key[2], key[1], key[0]) in relation_keys
            and key[0] != key[2]
        }

        self.assertFalse(mirrored_relations)
        self.assertEqual(
            sum(
                int(row["fact_count"])
                for row in self.package["semantic_relations"]
            ),
            len(self.package["facts"]),
        )

    def test_terminal_retrieval_does_not_enable_provisional_traversal(
        self,
    ) -> None:
        entity_by_id = {
            row["entity_id"]: row
            for row in self.package["entities"]
        }
        terminal_relations = [
            row
            for row in self.package["semantic_relations"]
            if row["terminal_retrieval_eligible"] == "true"
        ]
        default_relations = [
            row
            for row in self.package["semantic_relations"]
            if row["retrieval_eligible"] == "true"
        ]

        self.assertGreater(len(terminal_relations), len(default_relations))
        for relation in terminal_relations:
            endpoint_statuses = {
                entity_by_id[relation["subject_entity_id"]][
                    "resolution_status"
                ],
                entity_by_id[relation["object_entity_id"]][
                    "resolution_status"
                ],
            }
            self.assertIn("RESOLVED", endpoint_statuses)
            if "UNRESOLVED" in endpoint_statuses:
                self.assertEqual(
                    relation["multi_hop_eligible"],
                    "false",
                )

        default_term_count = sum(
            row["fact_retrieval_eligible"] == "true"
            for row in self.package["exam_terms"]
        )
        terminal_term_count = sum(
            row["terminal_fact_retrieval_eligible"] == "true"
            for row in self.package["exam_terms"]
        )
        self.assertGreater(terminal_term_count, default_term_count)

    def test_exact_search_returns_at_most_one_canonical_candidate(
        self,
    ) -> None:
        eligible_ids_by_name: dict[str, set[str]] = defaultdict(set)
        for entity in self.package["entities"]:
            if entity["entity_kind"] != "CANONICAL":
                continue
            if entity["exact_search_eligible"] != "true":
                continue
            eligible_ids_by_name[
                entity["normalized_search_text"]
            ].add(entity["entity_id"])

        ambiguous_eligible_names = {
            name: entity_ids
            for name, entity_ids in eligible_ids_by_name.items()
            if len(entity_ids) > 1
        }
        self.assertFalse(ambiguous_eligible_names)
        self.assertTrue(
            all(
                row["exact_search_eligible"] == "false"
                and row["retrieval_eligible"] == "false"
                for row in self.package["exam_terms"]
                if row["target_resolution_status"] == "AMBIGUOUS"
            )
        )
        self.assertTrue(
            all(
                row["exact_search_eligible"] == "false"
                and row["retrieval_eligible"] == "false"
                for row in self.package["entity_names"]
                if row["target_resolution_status"] == "AMBIGUOUS"
            )
        )


if __name__ == "__main__":
    unittest.main()

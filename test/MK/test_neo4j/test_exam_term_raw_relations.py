import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


class ExamTermRawRelationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from choice_relation.exam_term_raw_relations import (
            build_raw_relation_eda_tables,
            load_exam_term_raw_relation_policy,
        )

        cls.build_tables = staticmethod(
            build_raw_relation_eda_tables
        )
        cls.policy = load_exam_term_raw_relation_policy(
            str(
                neo4j_root
                / "config"
                / "exam_term_raw_relation_eda.json"
            ),
            str(
                neo4j_root
                / "config"
                / "exam_relation_candidates.json"
            ),
            str(
                neo4j_root / "config" / "entity_resolution.json"
            ),
            str(
                neo4j_root
                / "config"
                / "source_first_fact_eda.json"
            ),
        )

    def make_registry(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-HEUNGSEON",
                    "display_name": "흥선 대원군",
                    "entity_type": "Person",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000001:test-release"]'
                    ),
                }
            ]
        )

    def make_exam_terms(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "exam_term_id": "EXAM-HEUNGSEON",
                    "term": "흥선 대원군",
                    "categories_json": '["인물"]',
                    "projected_canonical_ids_json": (
                        '["CAN-HEUNGSEON"]'
                    ),
                    "projected_source_link_status": "ACCEPTED",
                }
            ]
        )

    def make_source_nodes(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "source_record_id": "THESAURUS:TERM:1:release",
                    "source": "THESAURUS",
                    "record_type": "TERM",
                    "display_name": "경복궁",
                    "source_urls_json": "[]",
                }
            ]
        )

    def make_source_resolutions(self) -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "source_record_id",
                "canonical_id",
                "match_status",
            ]
        )

    def write_aks_list(self, directory: str) -> str:
        path = Path(directory) / "articles.jsonl"
        path.write_text(
            json.dumps(
                {
                    "eid": "E0000001",
                    "url": "https://example.test/heungseon",
                    "headword": "흥선 대원군",
                    "headwordOrigin": "흥선 대원군(興宣大院君)",
                    "primaryTypePartA": "인물",
                    "primaryType": "인물/전통 인물",
                    "articleAliases": [],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return str(path)

    def make_document(
        self,
        sentence: str,
        title: str = "흥선 대원군",
    ) -> dict:
        return {
            "source_dataset": "TEST_OFFICIAL",
            "source_document_id": "TEST:DOC:1",
            "source_record_key": "1",
            "source_title": title,
            "source_url": "https://example.test/doc",
            "source_path": "test.csv",
            "trust_tier": "OFFICIAL_NARRATIVE",
            "text_fields": {"body": sentence},
        }

    def test_explicit_non_exam_target_becomes_source_node_candidate(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                self.make_source_nodes(),
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군은 경복궁을 중건하였다."
                    )
                ],
                self.policy,
            )

        self.assertEqual(len(tables["relations"]), 1)
        relation = tables["relations"].iloc[0]
        self.assertEqual(
            relation["start_node_id"],
            "CAN-HEUNGSEON",
        )
        self.assertEqual(
            relation["end_node_id"],
            "THESAURUS:TERM:1:release",
        )
        self.assertEqual(relation["relation_type"], "CREATED")
        self.assertFalse(relation["auto_load_eligible"])

        self.assertEqual(len(tables["non_exam_nodes"]), 1)
        target_node = tables["non_exam_nodes"].iloc[0]
        self.assertEqual(
            target_node["node_action"],
            "CREATE_OFFICIAL_SOURCE_ANCHOR",
        )
        self.assertTrue(target_node["search_graph_node_eligible"])
        self.assertFalse(
            target_node["canonical_promotion_eligible"]
        )
        self.assertEqual(
            statistics["new_official_source_node_candidate_count"],
            1,
        )

    def test_unregistered_target_becomes_open_entity_candidate(self):
        source_nodes = self.make_source_nodes().iloc[0:0]
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                source_nodes,
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군은 새궁궐을 중건하였다."
                    )
                ],
                self.policy,
            )

        self.assertEqual(len(tables["relations"]), 1)
        relation = tables["relations"].iloc[0]
        self.assertEqual(
            relation["end_node_kind"],
            "OPEN_ENTITY_CANDIDATE",
        )
        self.assertEqual(relation["end_display_name"], "새궁궐")
        self.assertEqual(
            relation["candidate_statuses_json"],
            '["OPEN_ENDPOINT_RELATION_CANDIDATE"]',
        )
        self.assertEqual(
            tables["non_exam_nodes"].iloc[0]["node_action"],
            "CREATE_OPEN_ENTITY_CANDIDATE",
        )
        self.assertEqual(
            statistics["open_entity_node_candidate_count"],
            1,
        )

    def test_both_unregistered_endpoints_are_excluded(self):
        source_nodes = self.make_source_nodes().iloc[0:0]
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                source_nodes,
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군 때 김씨는 새기관을 "
                        "설립하였다."
                    )
                ],
                self.policy,
            )

        self.assertTrue(tables["relations"].empty)
        self.assertTrue(tables["non_exam_nodes"].empty)
        self.assertEqual(
            statistics["open_entity_node_candidate_count"],
            0,
        )
        self.assertEqual(
            statistics["exclusion_reason_counts"].get(
                "BOTH_ENDPOINTS_UNREGISTERED",
                0,
            ),
            1,
        )

    def test_unregistered_event_and_location_are_excluded(self):
        source_nodes = self.make_source_nodes().iloc[0:0]
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                source_nodes,
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군 기록에는 농민항쟁이 "
                        "단성에서 발생하였다."
                    )
                ],
                self.policy,
            )

        self.assertTrue(tables["relations"].empty)
        self.assertEqual(
            statistics["open_entity_node_candidate_count"],
            0,
        )
        self.assertEqual(
            statistics["exclusion_reason_counts"].get(
                "BOTH_ENDPOINTS_UNREGISTERED",
                0,
            ),
            1,
        )

    def test_adverb_particle_is_not_open_actor(self):
        source_nodes = self.make_source_nodes().iloc[0:0]
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                source_nodes,
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군에 관한 설명에서 더욱이 "
                        "새제도를 도입하였다."
                    )
                ],
                self.policy,
            )

        self.assertTrue(tables["evidence"].empty)
        self.assertEqual(
            statistics["relation_evidence_count"],
            0,
        )

    def test_attributive_passive_does_not_infer_open_actor(self):
        source_nodes = self.make_source_nodes().iloc[0:0]
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                source_nodes,
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군 문헌은 낙랑군이 설치된 "
                        "이후를 다룬다."
                    )
                ],
                self.policy,
            )

        self.assertTrue(tables["evidence"].empty)
        self.assertGreater(
            statistics["exclusion_reason_counts"].get(
                "UNSAFE_PREDICATE_MORPHOLOGY",
                0,
            ),
            0,
        )

    def test_document_title_is_not_inferred_as_subject(self):
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                self.make_source_nodes(),
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "경복궁을 중건하였다.",
                        title="흥선 대원군",
                    )
                ],
                self.policy,
            )

        self.assertTrue(tables["evidence"].empty)
        self.assertTrue(tables["relations"].empty)
        self.assertEqual(
            statistics["subject_inferred_relation_count"],
            0,
        )

    def test_intended_action_is_not_extracted_as_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                self.make_source_nodes(),
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군은 경복궁을 중건하기 위해 "
                        "계획을 세웠다."
                    )
                ],
                self.policy,
            )

        self.assertTrue(tables["evidence"].empty)
        self.assertGreater(
            statistics["exclusion_reason_counts"].get(
                "UNSAFE_PREDICATE_MORPHOLOGY",
                0,
            ),
            0,
        )

    def test_negated_possibility_is_not_extracted_as_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                self.make_source_nodes(),
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군이 경복궁을 중건했을 "
                        "가능성은 없다."
                    )
                ],
                self.policy,
            )

        self.assertTrue(tables["evidence"].empty)
        self.assertGreater(
            statistics["exclusion_reason_counts"].get(
                "UNCERTAIN_CLAUSE",
                0,
            ),
            0,
        )

    def test_causative_action_is_not_attributed_to_causer(self):
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                self.make_source_nodes(),
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군은 김씨에게 경복궁을 "
                        "중건하게 하였다."
                    )
                ],
                self.policy,
            )

        self.assertTrue(tables["evidence"].empty)
        self.assertGreater(
            statistics["exclusion_reason_counts"].get(
                "UNSAFE_PREDICATE_MORPHOLOGY",
                0,
            ),
            0,
        )

    def test_intervening_action_keeps_implicit_target_in_review(self):
        source_nodes = self.make_source_nodes()
        source_nodes.loc[0, "display_name"] = "대사간"
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                source_nodes,
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군이 즉위한 뒤 대사간에 "
                        "임명되었다."
                    )
                ],
                self.policy,
            )

        self.assertEqual(len(tables["evidence"]), 1)
        self.assertEqual(
            tables["evidence"].iloc[0]["candidate_status"],
            "REVIEW_ARGUMENT_STRUCTURE",
        )
        self.assertEqual(
            tables["non_exam_nodes"].iloc[0]["node_action"],
            "HOLD_FOR_REVIEW",
        )

    def test_passive_agent_relation_stays_in_review(self):
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                self.make_source_nodes(),
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군에 의해 경복궁이 "
                        "중건되었다."
                    )
                ],
                self.policy,
            )

        self.assertEqual(len(tables["evidence"]), 1)
        self.assertEqual(
            tables["evidence"].iloc[0]["candidate_status"],
            "REVIEW_ARGUMENT_STRUCTURE",
        )
        self.assertEqual(
            tables["non_exam_nodes"].iloc[0]["node_action"],
            "HOLD_FOR_REVIEW",
        )

    def test_subject_switch_relation_stays_in_review(self):
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                self.make_source_nodes(),
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군이 죽자 왕실에서 "
                        "경복궁을 중건하였다."
                    )
                ],
                self.policy,
            )

        self.assertEqual(len(tables["evidence"]), 1)
        self.assertEqual(
            tables["evidence"].iloc[0]["candidate_status"],
            "REVIEW_ARGUMENT_STRUCTURE",
        )

    def test_ambiguous_or_modal_predicates_are_blocked(self):
        cases = [
            ("사람", "흥선 대원군은 사람을 쓴 방향을 정했다."),
            (
                "김씨",
                "흥선 대원군은 김씨를 파견할 수 있었다.",
            ),
        ]
        for target_name, sentence in cases:
            with self.subTest(target_name=target_name):
                source_nodes = self.make_source_nodes()
                source_nodes.loc[0, "display_name"] = target_name
                with tempfile.TemporaryDirectory() as directory:
                    aks_path = self.write_aks_list(directory)
                    tables, statistics = self.build_tables(
                        self.make_registry(),
                        self.make_exam_terms(),
                        source_nodes,
                        self.make_source_resolutions(),
                        aks_path,
                        [self.make_document(sentence)],
                        self.policy,
                    )

                self.assertTrue(tables["evidence"].empty)

    def test_short_hanja_alias_does_not_match_grammar(self):
        exam_terms = pd.DataFrame(
            [
                {
                    "exam_term_id": "EXAM-APPEAL",
                    "term": "격문",
                    "categories_json": '["문화"]',
                    "projected_canonical_ids_json": "[]",
                    "projected_source_link_status": "PENDING",
                }
            ]
        )
        source_nodes = pd.DataFrame(
            [
                {
                    "source_record_id": "ITKC:PERSON:1:release",
                    "source": "ITKC_PERSON",
                    "record_type": "PERSON",
                    "display_name": "이라(李懶)",
                    "source_urls_json": "[]",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                exam_terms,
                source_nodes,
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "사회적 비판을 환기함이라는 격문을 "
                        "작성하였다."
                    )
                ],
                self.policy,
            )

        self.assertTrue(tables["evidence"].empty)
        self.assertEqual(
            statistics["relation_evidence_count"],
            0,
        )

    def test_aks_short_person_requires_entity_link(self):
        registry = self.make_registry()
        registry.loc[0, "canonical_id"] = "CAN-WIMAN"
        registry.loc[0, "display_name"] = "위만"
        exam_terms = self.make_exam_terms()
        exam_terms.loc[0, "exam_term_id"] = "EXAM-WIMAN"
        exam_terms.loc[0, "term"] = "위만"
        exam_terms.loc[
            0,
            "projected_canonical_ids_json",
        ] = '["CAN-WIMAN"]'
        documents = [
            (
                "위만은 경복궁을 중건하였다.",
                "REVIEW_UNLINKED_SHORT_PERSON",
            ),
            (
                "[위만](E0000001)은 경복궁을 중건하였다.",
                "SOURCE_NODE_RELATION_CANDIDATE",
            ),
        ]
        for sentence, expected_status in documents:
            with self.subTest(sentence=sentence):
                document = self.make_document(sentence)
                document["source_dataset"] = "AKS"
                document["supports_linked_entities"] = True
                with tempfile.TemporaryDirectory() as directory:
                    aks_path = self.write_aks_list(directory)
                    tables, statistics = self.build_tables(
                        registry,
                        exam_terms,
                        self.make_source_nodes(),
                        self.make_source_resolutions(),
                        aks_path,
                        [document],
                        self.policy,
                    )

                self.assertEqual(len(tables["evidence"]), 1)
                self.assertEqual(
                    tables["evidence"].iloc[0][
                        "candidate_status"
                    ],
                    expected_status,
                )

    def test_review_only_non_exam_target_is_held(self):
        source_nodes = self.make_source_nodes()
        source_nodes.loc[0, "record_type"] = "PERSON"
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                source_nodes,
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군은 경복궁을 중건하였다."
                    )
                ],
                self.policy,
            )

        self.assertEqual(len(tables["non_exam_nodes"]), 1)
        target_node = tables["non_exam_nodes"].iloc[0]
        self.assertEqual(
            target_node["node_action"],
            "HOLD_FOR_REVIEW",
        )
        self.assertFalse(
            target_node["search_graph_node_eligible"]
        )
        self.assertEqual(
            statistics["held_non_exam_node_review_count"],
            1,
        )

    def test_homonymous_target_becomes_unmerged_open_candidate(self):
        source_nodes = pd.concat(
            [
                self.make_source_nodes(),
                pd.DataFrame(
                    [
                        {
                            "source_record_id": (
                                "THESAURUS:TERM:2:release"
                            ),
                            "source": "THESAURUS",
                            "record_type": "TERM",
                            "display_name": "경복궁",
                            "source_urls_json": "[]",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            aks_path = self.write_aks_list(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_exam_terms(),
                source_nodes,
                self.make_source_resolutions(),
                aks_path,
                [
                    self.make_document(
                        "흥선 대원군은 경복궁을 중건하였다."
                    )
                ],
                self.policy,
            )

        self.assertEqual(len(tables["relations"]), 1)
        relation = tables["relations"].iloc[0]
        self.assertEqual(
            relation["end_node_kind"],
            "OPEN_ENTITY_CANDIDATE",
        )
        self.assertEqual(relation["end_source_record_id"], "")
        self.assertEqual(
            relation["candidate_statuses_json"],
            '["OPEN_ENDPOINT_RELATION_CANDIDATE"]',
        )
        self.assertEqual(len(tables["non_exam_nodes"]), 1)
        self.assertEqual(
            tables["non_exam_nodes"].iloc[0]["node_action"],
            "CREATE_OPEN_ENTITY_CANDIDATE",
        )
        self.assertGreater(
            statistics["exclusion_reason_counts"].get(
                "AMBIGUOUS_TARGET_ENDPOINT",
                0,
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()

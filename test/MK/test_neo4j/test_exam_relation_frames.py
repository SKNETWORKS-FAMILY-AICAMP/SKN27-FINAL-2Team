import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


class ExamRelationFrameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from choice_relation.relation_frames import (
            build_exam_relation_frame_tables,
            load_exam_relation_frame_policy,
        )
        from choice_relation.safe_disambiguation import (
            build_safe_aks_disambiguation,
        )

        cls.build_frames = staticmethod(
            build_exam_relation_frame_tables
        )
        cls.build_disambiguation = staticmethod(
            build_safe_aks_disambiguation
        )
        cls.policy = load_exam_relation_frame_policy(
            str(
                neo4j_root
                / "config"
                / "exam_relation_candidates.json"
            )
        )

    def make_candidate(
        self,
        candidate_id: str,
        truth_status: str = "CONTEXTUALLY_TRUE",
    ) -> dict:
        return {
            "exam_relation_candidate_id": candidate_id,
            "contextual_truth_status": truth_status,
        }

    def make_check(
        self,
        candidate_id: str,
        text: str,
    ) -> dict:
        return {
            "exam_relation_candidate_id": candidate_id,
            "claim_segment_id": f"CLAIM-{candidate_id}",
            "problem_id": f"PROBLEM-{candidate_id}",
            "search_status": "SUPPORTED_BY_AKS_TEXT_STRICT",
            "strict_support": True,
            "exam_evidence_text": text,
        }

    def make_evidence(
        self,
        candidate_id: str,
        start_id: str,
        end_id: str,
        family: str,
        pattern: str,
    ) -> dict:
        return {
            "exam_official_text_evidence_id": (
                f"EVIDENCE-{candidate_id}-{pattern}"
            ),
            "exam_relation_candidate_id": candidate_id,
            "start_canonical_id": start_id,
            "end_canonical_id": end_id,
            "shared_predicate_families_json": json.dumps(
                [family],
                ensure_ascii=False,
            ),
            "shared_predicate_patterns_json": json.dumps(
                [pattern],
                ensure_ascii=False,
            ),
            "source_url": "https://example.test",
            "official_evidence_sentence": "공식 근거 문장",
        }

    def make_registry(self, rows: list[tuple[str, str, str]]):
        return pd.DataFrame(
            [
                {
                    "canonical_id": canonical_id,
                    "display_name": display_name,
                    "entity_type": entity_type,
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": "[]",
                }
                for canonical_id, display_name, entity_type in rows
            ]
        )

    def test_actor_target_pair_is_ready_for_validation(self):
        candidate_id = "ATTACK"
        tables, statistics = self.build_frames(
            pd.DataFrame([self.make_candidate(candidate_id)]),
            pd.DataFrame(
                [
                    self.make_check(
                        candidate_id,
                        "장수왕이 한성을 공격하여 함락시켰다.",
                    )
                ]
            ),
            pd.DataFrame(
                [
                    self.make_evidence(
                        candidate_id,
                        "CAN-JANGSU",
                        "CAN-HANSEONG",
                        "CONFLICT_OR_SUPPRESS",
                        "공격",
                    )
                ]
            ),
            self.make_registry(
                [
                    ("CAN-JANGSU", "장수왕", "Person"),
                    ("CAN-HANSEONG", "한성", "Place"),
                ]
            ),
            self.policy,
        )
        frame = tables["frames"].iloc[0]

        self.assertEqual(frame["actor_names_json"], '["장수왕"]')
        self.assertEqual(frame["target_names_json"], '["한성"]')
        self.assertEqual(
            frame["pair_status"],
            "PAIR_READY_FOR_FACT_VALIDATION",
        )
        self.assertFalse(frame["direct_fact_projection_allowed"])
        self.assertEqual(statistics["pair_ready_count"], 1)

    def test_compound_claim_does_not_connect_independent_objects(self):
        candidate_id = "COMPOUND"
        tables, _ = self.build_frames(
            pd.DataFrame([self.make_candidate(candidate_id)]),
            pd.DataFrame(
                [
                    self.make_check(
                        candidate_id,
                        "관료전을 지급하고 녹읍을 폐지하였다.",
                    )
                ]
            ),
            pd.DataFrame(
                [
                    self.make_evidence(
                        candidate_id,
                        "CAN-NOGEUP",
                        "CAN-GWANRYO",
                        "IMPLEMENT_OR_ENACT",
                        "폐지",
                    )
                ]
            ),
            self.make_registry(
                [
                    ("CAN-NOGEUP", "녹읍", "Concept"),
                    ("CAN-GWANRYO", "관료전", "Concept"),
                ]
            ),
            self.policy,
        )
        frame = tables["frames"].iloc[0]
        participant_names = set(
            tables["participants"]["display_name"]
        )

        self.assertEqual(participant_names, {"녹읍"})
        self.assertEqual(frame["target_names_json"], '["녹읍"]')
        self.assertEqual(
            frame["pair_status"],
            "PAIR_NOT_AVAILABLE",
        )

    def test_nominal_action_is_blocked(self):
        candidate_id = "NOMINAL"
        tables, _ = self.build_frames(
            pd.DataFrame([self.make_candidate(candidate_id)]),
            pd.DataFrame(
                [
                    self.make_check(
                        candidate_id,
                        "중종 때 조광조가 실시를 주장하였다.",
                    )
                ]
            ),
            pd.DataFrame(
                [
                    self.make_evidence(
                        candidate_id,
                        "CAN-JO",
                        "CAN-JUNG",
                        "IMPLEMENT_OR_ENACT",
                        "실시",
                    )
                ]
            ),
            self.make_registry(
                [
                    ("CAN-JO", "조광조", "Person"),
                    ("CAN-JUNG", "중종", "Person"),
                ]
            ),
            self.policy,
        )
        frame = tables["frames"].iloc[0]

        self.assertEqual(
            frame["frame_status"],
            "ACTION_NOT_ASSERTED",
        )
        self.assertEqual(
            frame["pair_status"],
            "PAIR_BLOCKED_ACTION_NOT_ASSERTED",
        )

    def test_coactors_are_not_projected_as_direct_pair(self):
        candidate_id = "COACTORS"
        tables, _ = self.build_frames(
            pd.DataFrame([self.make_candidate(candidate_id)]),
            pd.DataFrame(
                [
                    self.make_check(
                        candidate_id,
                        "홍경래, 우군칙 등이 주도하였다.",
                    )
                ]
            ),
            pd.DataFrame(
                [
                    self.make_evidence(
                        candidate_id,
                        "CAN-HONG",
                        "CAN-WOO",
                        "PARTICIPATE_OR_ACT",
                        "주도",
                    )
                ]
            ),
            self.make_registry(
                [
                    ("CAN-HONG", "홍경래", "Person"),
                    ("CAN-WOO", "우군칙", "Person"),
                ]
            ),
            self.policy,
        )
        frame = tables["frames"].iloc[0]

        self.assertEqual(
            set(
                tables["participants"]["participant_role"]
            ),
            {"ACTOR"},
        )
        self.assertEqual(
            frame["pair_status"],
            "PAIR_NOT_AVAILABLE",
        )

    def test_contextually_false_candidate_is_rejected(self):
        candidate_id = "FALSE"
        with self.assertRaisesRegex(
            ValueError,
            "문맥상 참이 아닌 후보",
        ):
            self.build_frames(
                pd.DataFrame(
                    [
                        self.make_candidate(
                            candidate_id,
                            "CONTEXTUALLY_FALSE",
                        )
                    ]
                ),
                pd.DataFrame(
                    [
                        self.make_check(
                            candidate_id,
                            "장수왕이 한성을 공격하였다.",
                        )
                    ]
                ),
                pd.DataFrame(
                    [
                        self.make_evidence(
                            candidate_id,
                            "CAN-JANGSU",
                            "CAN-HANSEONG",
                            "CONFLICT_OR_SUPPRESS",
                            "공격",
                        )
                    ]
                ),
                self.make_registry(
                    [
                        ("CAN-JANGSU", "장수왕", "Person"),
                        ("CAN-HANSEONG", "한성", "Place"),
                    ]
                ),
                self.policy,
            )

    def test_light_verb_action_is_asserted(self):
        candidate_id = "LIGHT-VERB"
        tables, _ = self.build_frames(
            pd.DataFrame([self.make_candidate(candidate_id)]),
            pd.DataFrame(
                [
                    self.make_check(
                        candidate_id,
                        "보부상이 장시에서 상업 활동을 하였다.",
                    )
                ]
            ),
            pd.DataFrame(
                [
                    self.make_evidence(
                        candidate_id,
                        "CAN-BOBU",
                        "CAN-JANGSI",
                        "PARTICIPATE_OR_ACT",
                        "활동",
                    )
                ]
            ),
            self.make_registry(
                [
                    ("CAN-BOBU", "보부상", "Person"),
                    ("CAN-JANGSI", "장시", "Place"),
                ]
            ),
            self.policy,
        )
        frame = tables["frames"].iloc[0]

        self.assertTrue(frame["action_asserted"])
        self.assertNotEqual(
            frame["frame_status"],
            "ACTION_NOT_ASSERTED",
        )

    def test_coactor_with_phrase_can_supply_actor_role(self):
        candidate_id = "COACTOR-WITH"
        tables, _ = self.build_frames(
            pd.DataFrame([self.make_candidate(candidate_id)]),
            pd.DataFrame(
                [
                    self.make_check(
                        candidate_id,
                        "김원봉 등과 함께 민족 혁명당을 결성함.",
                    )
                ]
            ),
            pd.DataFrame(
                [
                    self.make_evidence(
                        candidate_id,
                        "CAN-KIM",
                        "CAN-PARTY",
                        "FOUND_OR_ESTABLISH",
                        "결성",
                    )
                ]
            ),
            self.make_registry(
                [
                    ("CAN-KIM", "김원봉", "Person"),
                    (
                        "CAN-PARTY",
                        "민족혁명당",
                        "Organization",
                    ),
                ]
            ),
            self.policy,
        )
        frame = tables["frames"].iloc[0]

        self.assertEqual(frame["actor_names_json"], '["김원봉"]')
        self.assertEqual(
            frame["target_names_json"],
            '["민족혁명당"]',
        )
        self.assertEqual(
            frame["pair_status"],
            "PAIR_READY_FOR_FACT_VALIDATION",
        )

    def test_homonym_requires_hanja_era_and_definition(self):
        official_checks = pd.DataFrame(
            [
                {
                    "existing_canonical_ids_json": (
                        '["CAN-EAST", "CAN-CLASSMATE"]'
                    ),
                    "recovered_mentions_json": "[]",
                }
            ]
        )
        registry = pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-EAST",
                    "display_name": "동학",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["THESAURUS:EAST"]'
                    ),
                },
                {
                    "canonical_id": "CAN-CLASSMATE",
                    "display_name": "동학",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["THESAURUS:CLASSMATE"]'
                    ),
                },
            ]
        )
        source_records = pd.DataFrame(
            [
                {
                    "source_record_id": "THESAURUS:EAST",
                    "source_metadata_json": json.dumps(
                        {
                            "hanja": "東學",
                            "era": "조선후기",
                            "description": (
                                "1860년 최제우가 창시한 종교."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "source_record_id": "THESAURUS:CLASSMATE",
                    "source_metadata_json": json.dumps(
                        {
                            "hanja": "同學",
                            "era": "통시대",
                            "description": (
                                "같은 곳에서 함께 공부하는 사람."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        article = {
            "eid": "E0016857",
            "headword": "동학",
            "origin": "東學",
            "era": "조선/조선 후기",
            "definition": "1860년 최제우가 창도한 종교.",
        }
        with tempfile.TemporaryDirectory() as directory:
            list_path = Path(directory) / "aks_list.jsonl"
            list_path.write_text(
                json.dumps(article, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            table, safe_map, statistics = (
                self.build_disambiguation(
                    official_checks,
                    registry,
                    source_records,
                    str(list_path),
                    self.policy,
                )
            )

        self.assertEqual(safe_map["E0016857"], "CAN-EAST")
        self.assertEqual(
            table.iloc[0]["disambiguation_status"],
            "SAFE_MATCH",
        )
        self.assertEqual(
            statistics["safe_disambiguation_count"],
            1,
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from json import load
from pathlib import Path
import sys
from unittest import TestCase


sys.path.append(
    str(
        Path(__file__).resolve().parents[3]
        / "etl"
        / "preprocessing"
        / "neo4j"
    )
)

from run_exam_term_nlp_relation_gate import (
    evaluate_gate_evidence,
)


class ExamTermNlpRelationGateTest(TestCase):
    def load_policy(self) -> dict[str, object]:
        config_path = (
            Path(__file__).resolve().parents[3]
            / "etl"
            / "preprocessing"
            / "neo4j"
            / "config"
            / "exam_term_nlp_relation_gate.json"
        )
        with config_path.open(
            "r",
            encoding="utf-8",
        ) as input_file:
            return load(input_file)

    def build_evidence(self) -> dict[str, str]:
        return {
            "candidate_status": "NLP_HIGH_CONFIDENCE_REGISTERED",
            "candidate_rank": "1",
            "candidate_score": "20",
            "explicit_role_evidence_count": "2",
            "type_contract_compatible": "True",
            "structural_conflict": "False",
            "relation_family": "IMPLEMENT_OR_ENACT",
            "predicate_pattern": "폐지",
            "start_role": "ACTOR",
            "end_role": "TARGET",
            "start_entity_type": "Organization",
            "end_entity_type": "Institution",
            "start_display_name": "개화파",
            "end_display_name": "향교",
            "start_mention_text": "개화파",
            "end_mention_text": "향교",
            "atomic_clause_text": "개화파는 향교를 폐지하였다.",
        }

    def test_maps_abolition_predicate_without_losing_polarity(
        self,
    ) -> None:
        relation_type, reasons = evaluate_gate_evidence(
            self.build_evidence(),
            self.load_policy(),
        )

        self.assertEqual(relation_type, "ABOLISHED")
        self.assertEqual(reasons, [])

    def test_review_registered_evidence_can_be_reassessed(
        self,
    ) -> None:
        evidence = self.build_evidence()
        evidence["candidate_status"] = "NLP_REVIEW_REGISTERED"

        relation_type, reasons = evaluate_gate_evidence(
            evidence,
            self.load_policy(),
        )

        self.assertEqual(relation_type, "ABOLISHED")
        self.assertEqual(reasons, [])

    def test_rejects_co_participant_without_subject_marker(
        self,
    ) -> None:
        evidence = self.build_evidence()
        evidence.update(
            {
                "relation_family": "CONFLICT_OR_SUPPRESS",
                "predicate_pattern": "물리",
                "start_entity_type": "Person",
                "end_entity_type": "Institution",
                "start_display_name": "곽재우",
                "end_display_name": "왜적",
                "start_mention_text": "곽재우",
                "end_mention_text": "왜적",
                "atomic_clause_text": (
                    "곽재우와 함께 왜적을 물리쳤다."
                ),
            }
        )

        _, reasons = evaluate_gate_evidence(
            evidence,
            self.load_policy(),
        )

        self.assertIn(
            "START_EXPLICIT_CASE_MARKER_MISSING",
            reasons,
        )

    def test_rejects_unstable_ascension_frame(self) -> None:
        evidence = self.build_evidence()
        evidence.update(
            {
                "relation_family": "APPOINT_OR_SERVE",
                "predicate_pattern": "즉위",
                "start_role": "ACTOR",
                "end_role": "TARGET",
                "start_entity_type": "Person",
                "end_entity_type": "Person",
                "start_display_name": "온달",
                "end_display_name": "영양왕",
                "start_mention_text": "온달",
                "end_mention_text": "영양왕",
                "atomic_clause_text": (
                    "온달은 영양왕이 즉위하자 출정을 자청하였다."
                ),
            }
        )

        _, reasons = evaluate_gate_evidence(
            evidence,
            self.load_policy(),
        )

        self.assertIn("PREDICATE_NOT_ALLOWED", reasons)

    def test_rejects_negated_historical_claim(self) -> None:
        evidence = self.build_evidence()
        evidence.update(
            {
                "start_entity_type": "Polity",
                "end_entity_type": "Concept",
                "start_display_name": "백제",
                "end_display_name": "율령",
                "start_mention_text": "백제",
                "end_mention_text": "율령",
                "predicate_pattern": "반포",
                "atomic_clause_text": (
                    "백제는 율령을 반포하였다는 기록은 "
                    "찾아볼 수 없으나"
                ),
            }
        )

        _, reasons = evaluate_gate_evidence(
            evidence,
            self.load_policy(),
        )

        self.assertIn(
            "NON_ASSERTIVE_OR_NEGATED_CLAUSE",
            reasons,
        )

    def test_rejects_source_location_as_move_destination(
        self,
    ) -> None:
        evidence = self.build_evidence()
        evidence.update(
            {
                "relation_family": "MOVE_OR_RETURN",
                "predicate_pattern": "천도",
                "start_role": "ACTOR",
                "end_role": "LOCATION",
                "start_entity_type": "Person",
                "end_entity_type": "Place",
                "start_display_name": "궁예",
                "end_display_name": "송악",
                "start_mention_text": "궁예",
                "end_mention_text": "송악",
                "atomic_clause_text": (
                    "궁예가 송악에서 철원으로 천도하였다."
                ),
            }
        )

        _, reasons = evaluate_gate_evidence(
            evidence,
            self.load_policy(),
        )

        self.assertIn(
            "END_EXPLICIT_CASE_MARKER_MISSING",
            reasons,
        )

    def test_rejects_passive_theme_as_actor(self) -> None:
        evidence = self.build_evidence()
        evidence.update(
            {
                "relation_family": "FOUND_OR_ESTABLISH",
                "predicate_pattern": "조직",
                "start_entity_type": "Institution",
                "end_entity_type": "Concept",
                "start_display_name": "야별초",
                "end_display_name": "초적",
                "start_mention_text": "야별초",
                "end_mention_text": "초적",
                "atomic_clause_text": (
                    "야별초는 초적을 막기 위해 조직되었다."
                ),
            }
        )

        _, reasons = evaluate_gate_evidence(
            evidence,
            self.load_policy(),
        )

        self.assertIn("PASSIVE_VOICE_NOT_ALLOWED", reasons)

    def test_rejects_same_surface_endpoint_relation(self) -> None:
        evidence = self.build_evidence()
        evidence["end_display_name"] = "개화파"
        evidence["end_mention_text"] = "개화파"
        evidence["atomic_clause_text"] = (
            "개화파는 개화파를 폐지하였다."
        )

        _, reasons = evaluate_gate_evidence(
            evidence,
            self.load_policy(),
        )

        self.assertIn(
            "SAME_NORMALIZED_ENDPOINT_SURFACE",
            reasons,
        )

    def test_maps_expanded_occupation_predicate(self) -> None:
        evidence = self.build_evidence()
        evidence.update(
            {
                "relation_family": "CONFLICT_OR_SUPPRESS",
                "predicate_pattern": "점령",
                "start_entity_type": "Polity",
                "end_entity_type": "Place",
                "start_display_name": "고구려",
                "end_display_name": "요동",
                "start_mention_text": "고구려",
                "end_mention_text": "요동",
                "atomic_clause_text": "고구려가 요동을 점령하였다.",
            }
        )

        relation_type, reasons = evaluate_gate_evidence(
            evidence,
            self.load_policy(),
        )

        self.assertEqual(relation_type, "OCCUPIED")
        self.assertEqual(reasons, [])

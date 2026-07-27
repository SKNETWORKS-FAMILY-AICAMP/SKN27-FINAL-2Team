import sys
import unittest
from json import dumps
from pathlib import Path

import pandas as pd


class SourceRelationshipPreprocessingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[3]
        cls.neo4j_root = (
            cls.project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(cls.neo4j_root))

        from source_relationships.build import (
            build_source_relationship_tables,
            load_source_relationship_policy,
        )
        from source_relationships.load import (
            build_source_relationship_load_plan,
            build_source_relationship_load_queries,
        )

        cls.build_source_relationship_tables = staticmethod(
            build_source_relationship_tables
        )
        cls.build_source_relationship_load_plan = staticmethod(
            build_source_relationship_load_plan
        )
        cls.build_source_relationship_load_queries = staticmethod(
            build_source_relationship_load_queries
        )
        cls.policy = load_source_relationship_policy(
            str(
                cls.neo4j_root
                / "config"
                / "source_relationships.json"
            )
        )

    def build_inputs(self) -> dict[str, pd.DataFrame]:
        people = pd.DataFrame(
            [
                {
                    "person_id": "P1",
                    "name": "갑(甲)",
                    "birth_year": "",
                    "death_year": "",
                    "bonkwan": "",
                    "ja": "",
                    "ho": "",
                    "father": "",
                    "related_count": "1명",
                    "detail_url": "https://example.test/people/P1",
                },
                {
                    "person_id": "P2",
                    "name": "을(乙)",
                    "birth_year": "",
                    "death_year": "",
                    "bonkwan": "",
                    "ja": "",
                    "ho": "",
                    "father": "",
                    "related_count": "1명",
                    "detail_url": "https://example.test/people/P2",
                },
            ]
        )
        person_relations = pd.DataFrame(
            [
                {
                    "person_id": "P1",
                    "person_name": "갑(甲)",
                    "relation_type": "스승",
                    "related_person_id": "P2",
                    "related_person_name": "을(乙)",
                    "related_birth_year": "",
                    "related_death_year": "",
                    "related_bonkwan": "",
                    "related_father": "",
                    "related_count": "",
                    "evidence_url": "https://example.test/evidence/1",
                    "detail_url": "https://example.test/relation/1",
                },
                {
                    "person_id": "P1",
                    "person_name": "갑(甲)",
                    "relation_type": "스승",
                    "related_person_id": "P2",
                    "related_person_name": "을(乙)",
                    "related_birth_year": "",
                    "related_death_year": "",
                    "related_bonkwan": "",
                    "related_father": "",
                    "related_count": "",
                    "evidence_url": "https://example.test/evidence/2",
                    "detail_url": "https://example.test/relation/1",
                },
            ]
        )
        events = pd.DataFrame(
            [
                {
                    "scope": "event_subject",
                    "event_id": "E1",
                    "event_name": "사건",
                    "subject_category": "정치",
                    "period": "조선",
                    "event_date": "",
                    "person_count": "1",
                    "related_event": "",
                    "detail_url": "https://example.test/event/subject/E1",
                },
                {
                    "scope": "event_period",
                    "event_id": "E1",
                    "event_name": "사건",
                    "subject_category": "정치",
                    "period": "조선",
                    "event_date": "",
                    "person_count": "1",
                    "related_event": "",
                    "detail_url": "https://example.test/event/period/E1",
                },
            ]
        )
        event_relations = pd.DataFrame(
            [
                {
                    "scope": "event_subject",
                    "event_id": "E1",
                    "event_name": "사건",
                    "relation_type": "사건인물",
                    "person_id": "P1",
                    "person_name": "갑(甲)",
                    "related_event_id": "",
                    "related_event_name": "",
                    "evidence_url": "https://example.test/event-evidence/1",
                    "detail_url": "https://example.test/event/subject/E1",
                },
                {
                    "scope": "event_period",
                    "event_id": "E1",
                    "event_name": "사건",
                    "relation_type": "사건인물",
                    "person_id": "P1",
                    "person_name": "갑(甲)",
                    "related_event_id": "",
                    "related_event_name": "",
                    "evidence_url": "https://example.test/event-evidence/1",
                    "detail_url": "https://example.test/event/period/E1",
                },
            ]
        )
        thesaurus = pd.DataFrame(
            [
                {
                    "term_id": "T0",
                    "topterm_id": "T0",
                    "term_name": "정치",
                    "term_kind": "0",
                    "term_ch": "",
                    "term_remark": "",
                    "term_attr": "",
                    "term_year": "",
                    "term_times": "",
                    "term_lk": "_NULL_",
                    "term_desc": "",
                    "term_user": "",
                    "term_created": "",
                    "term_reference": "",
                },
                {
                    "term_id": "T1",
                    "topterm_id": "T0",
                    "term_name": "의정부",
                    "term_kind": "2",
                    "term_ch": "議政府",
                    "term_remark": "",
                    "term_attr": "",
                    "term_year": "",
                    "term_times": "조선",
                    "term_lk": "정치>행정>중앙행정기구",
                    "term_desc": "조선의 최고 행정 기구",
                    "term_user": "",
                    "term_created": "",
                    "term_reference": "",
                },
            ]
        )
        return {
            "people": people,
            "person_relations": person_relations,
            "events": events,
            "event_relations": event_relations,
            "thesaurus": thesaurus,
        }

    def build_tables(
        self,
        canonical_resolutions: pd.DataFrame | None = None,
        canonical_registry: pd.DataFrame | None = None,
    ) -> dict[str, pd.DataFrame]:
        inputs = self.build_inputs()
        releases = {
            "itkc_people": "sha256-people",
            "itkc_person_relations": "sha256-person-relations",
            "itkc_events": "sha256-events",
            "itkc_event_relations": "sha256-event-relations",
            "thesaurus": "sha256-thesaurus",
        }
        return self.build_source_relationship_tables(
            inputs["people"],
            inputs["person_relations"],
            inputs["events"],
            inputs["event_relations"],
            inputs["thesaurus"],
            releases,
            self.policy,
            canonical_resolutions,
            canonical_registry,
        )

    def test_duplicate_source_rows_become_one_node_and_edge(self):
        tables = self.build_tables()

        self.assertEqual(len(tables["source_record_nodes"]), 5)
        source_edges = tables["source_record_relationships"]
        teacher_edges = source_edges[
            source_edges["relation_type"] == "HAS_TEACHER"
        ]
        event_edges = source_edges[
            source_edges["relation_type"] == "INVOLVES_PERSON"
        ]
        self.assertEqual(len(teacher_edges), 1)
        self.assertEqual(int(teacher_edges.iloc[0]["source_row_count"]), 2)
        self.assertIn(
            "evidence/2",
            teacher_edges.iloc[0]["evidence_urls_json"],
        )
        self.assertEqual(len(event_edges), 1)
        self.assertEqual(int(event_edges.iloc[0]["source_row_count"]), 2)
        self.assertTrue(
            tables["canonical_projection_exclusions"].empty
        )

    def test_thesaurus_topterm_and_category_path_are_separate(self):
        tables = self.build_tables()

        source_edges = tables["source_record_relationships"]
        top_category_edges = source_edges[
            source_edges["relation_type"] == "IN_TOP_CATEGORY"
        ]
        categories = tables["thesaurus_category_nodes"]
        category_edges = tables["thesaurus_category_relationships"]
        memberships = tables["source_category_relationships"]

        self.assertEqual(len(top_category_edges), 1)
        self.assertEqual(len(categories), 3)
        self.assertEqual(len(category_edges), 2)
        self.assertEqual(len(memberships), 1)
        self.assertEqual(
            set(categories["category_path"]),
            {"정치", "정치>행정", "정치>행정>중앙행정기구"},
        )

    def test_canonical_projection_requires_both_accepted_endpoints(self):
        resolutions = pd.DataFrame(
            [
                {
                    "source_record_id": (
                        "ITKC:PERSON:P1:sha256-people"
                    ),
                    "canonical_id": "C-P1",
                    "match_status": "ACCEPTED",
                },
                {
                    "source_record_id": (
                        "ITKC:PERSON:P2:sha256-people"
                    ),
                    "canonical_id": "C-P2",
                    "match_status": "ACCEPTED",
                },
                {
                    "source_record_id": (
                        "ITKC:EVENT:E1:sha256-events"
                    ),
                    "canonical_id": "C-E1",
                    "match_status": "PROPOSED",
                },
            ]
        )
        tables = self.build_tables(resolutions)

        canonical_edges = tables["canonical_entity_relationships"]
        exclusions = tables["canonical_projection_exclusions"]
        self.assertEqual(len(canonical_edges), 1)
        self.assertEqual(
            canonical_edges.iloc[0]["relation_type"],
            "HAS_TEACHER",
        )
        self.assertIn(
            "START_ENDPOINT_UNRESOLVED",
            set(exclusions["exclusion_reason"]),
        )

    def test_registry_identity_members_project_direct_fact_edges(self):
        registry = pd.DataFrame(
            [
                {
                    "canonical_id": "C-P1",
                    "entity_type": "Person",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": dumps(
                        ["ITKC:PERSON:P1:sha256-people"]
                    ),
                },
                {
                    "canonical_id": "C-P2",
                    "entity_type": "Person",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": dumps(
                        ["ITKC:PERSON:P2:sha256-people"]
                    ),
                },
                {
                    "canonical_id": "C-E1",
                    "entity_type": "Event",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": dumps(
                        ["ITKC:EVENT:E1:sha256-events"]
                    ),
                },
            ]
        )

        tables = self.build_tables(canonical_registry=registry)
        canonical_edges = tables["canonical_entity_relationships"]

        self.assertEqual(len(canonical_edges), 2)
        self.assertEqual(
            set(canonical_edges["relation_type"]),
            {"HAS_TEACHER", "INVOLVES_PERSON"},
        )
        self.assertTrue(
            canonical_edges["evidence_urls_json"]
            .str.contains("evidence")
            .all()
        )

    def test_neo4j_load_plan_is_ready_for_valid_tables(self):
        tables = self.build_tables()

        plan = self.build_source_relationship_load_plan(tables)
        queries = self.build_source_relationship_load_queries()

        self.assertEqual(plan["status"], "READY")
        self.assertFalse(plan["validation_errors"])
        self.assertIn(
            "SOURCE_RELATION",
            queries["source_record_relationships"],
        )
        self.assertIn(
            "FACT_RELATION",
            queries["canonical_entity_relationships"],
        )
        self.assertIn(
            "ON CREATE SET source.record_status = 'SOURCE_ASSERTED'",
            queries["source_record_nodes"],
        )

    def test_neo4j_load_plan_blocks_missing_endpoint(self):
        tables = self.build_tables()
        tables["source_record_relationships"].loc[
            0,
            "end_source_record_id",
        ] = "UNKNOWN"

        plan = self.build_source_relationship_load_plan(tables)

        self.assertEqual(plan["status"], "BLOCKED")
        self.assertTrue(plan["validation_errors"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType

from neo4j import Driver, GraphDatabase


@unittest.skipUnless(
    os.getenv("RUN_NEO4J_INTEGRATION", "").lower() == "true",
    "RUN_NEO4J_INTEGRATION=true 일 때만 실제 Neo4j 통합 테스트를 실행합니다.",
)
class GraphServiceNeo4jIntegrationTest(unittest.TestCase):
    driver: Driver
    graph_service: ModuleType
    database: str

    @classmethod
    def setUpClass(cls) -> None:
        project_root = Path(__file__).resolve().parents[3]
        app_directory = project_root / "app"
        sys.path.insert(0, str(app_directory))
        cls.graph_service = importlib.import_module("chatbot.graph_service")

        uri = os.environ["NEO4J_URI"]
        user = os.environ["NEO4J_USER"]
        password = os.environ["NEO4J_PASSWORD"]
        cls.database = os.getenv("NEO4J_DATABASE", "neo4j")
        cls.driver = GraphDatabase.driver(uri, auth=(user, password))
        cls.driver.verify_connectivity()

        with cls.driver.session(database=cls.database) as session:
            session.run(
                """
                MATCH (node:CiFixture)
                DETACH DELETE node
                """
            ).consume()
            session.run(
                """
                CREATE (period:Period:CiFixture {name: "조선"})
                CREATE (sejong:Term:CiFixture {
                    name: "세종대왕",
                    hanja: "世宗大王",
                    year_text: "1397~1450",
                    period_text: "조선",
                    category_text: "인물",
                    description: "훈민정음을 창제하고 과학 기술을 발전시켰다."
                })
                CREATE (hunmin:Term:CiFixture {
                    name: "훈민정음",
                    hanja: "訓民正音",
                    year_text: "1443",
                    period_text: "조선",
                    category_text: "문화",
                    description: "백성을 가르치는 바른 소리이다."
                })
                CREATE (sejong)-[:IN_PERIOD]->(period)
                CREATE (sejong)-[:RELATED_TO]->(hunmin)
                """
            ).consume()

    @classmethod
    def tearDownClass(cls) -> None:
        with cls.driver.session(database=cls.database) as session:
            session.run(
                """
                MATCH (node:CiFixture)
                DETACH DELETE node
                """
            ).consume()
        cls.driver.close()

    def test_build_graph_context_returns_seeded_relation(self) -> None:
        context = self.graph_service.build_graph_context(
            "세종대왕에 대해 설명해줘",
            limit=6,
            max_hop=1,
        )

        self.assertTrue(context["enabled"], context)
        self.assertEqual(context["reason"], "")

        terms_by_name = {
            term["term_name"]: term
            for term in context["terms"]
        }
        self.assertIn("세종대왕", terms_by_name)

        sejong = terms_by_name["세종대왕"]
        self.assertIn("조선", sejong["periods"])
        self.assertIn("훈민정음", sejong["related_terms"])
        self.assertIn("세종대왕", context["keywords"])
        self.assertIn("세종대왕", context["relation_summary"])


if __name__ == "__main__":
    unittest.main()

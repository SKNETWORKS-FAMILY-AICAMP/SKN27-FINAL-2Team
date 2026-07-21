import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


class CandidateRetrievalRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root / "terms"))
        sys.path.insert(0, str(neo4j_root))

        from candidate_retrieval import build_search_index, retrieve_candidates
        from common import load_pipeline_policy
        from match_names import build_encyclopedia_index, parse_problem_ids
        import get_history_terms as history_terms_module

        cls.retrieve_candidates = staticmethod(retrieve_candidates)
        cls.build_search_index = staticmethod(build_search_index)
        cls.build_encyclopedia_index = staticmethod(build_encyclopedia_index)
        cls.parse_problem_ids = staticmethod(parse_problem_ids)
        cls.history_terms_module = history_terms_module
        policy_path = neo4j_root / "config" / "resolution_policy.json"
        cls.policy = load_pipeline_policy(str(policy_path))
        records = [
            {
                "search_names": ["9성"],
                "search_text": "1107년 윤관이 여진을 정벌하고 쌓은 9개의 성",
                "payload": {"source_record_id": "THESAURUS:TERM:979"},
            },
            {
                "search_names": ["삼시협정"],
                "search_text": "1925년 미쓰야 미야마쓰와 체결한 협정",
                "payload": {"source_record_id": "THESAURUS:TERM:4039"},
            },
            {
                "search_names": ["8조법금"],
                "search_text": "고조선시대 사회 질서를 위한 8개조의 금법",
                "payload": {"source_record_id": "THESAURUS:TERM:8696"},
            },
            {
                "search_names": ["한반도 비핵화에 관한 공동선언"],
                "search_text": "1991년 12월 남한과 북한이 서명한 합의문",
                "payload": {"source_record_id": "AKS:ARTICLE:E0078904"},
            },
        ]
        cls.search_index = build_search_index(
            records,
            cls.policy["candidate_retrieval"],
        )

    def assert_top_candidate(self, query: str, expected_source_record_id: str):
        candidates = self.retrieve_candidates(
            term=query,
            search_index=self.search_index,
            retrieval_policy=self.policy["candidate_retrieval"],
            policy_version=self.policy["policy_version"],
        )
        self.assertTrue(candidates)
        self.assertEqual(
            candidates[0]["source_record_id"],
            expected_source_record_id,
        )
        self.assertEqual(candidates[0]["verification_status"], "PROPOSED")
        self.assertEqual(
            candidates[0]["retrieval_policy_version"],
            self.policy["policy_version"],
        )

    def test_bidirectional_short_name_retrieval(self):
        self.assert_top_candidate("동북 9성", "THESAURUS:TERM:979")

    def test_description_keyword_retrieval(self):
        self.assert_top_candidate("미쓰야 협정", "THESAURUS:TERM:4039")

    def test_reordered_lexical_variant_retrieval(self):
        self.assert_top_candidate("범금 8조", "THESAURUS:TERM:8696")

    def test_inserted_modifier_retrieval(self):
        self.assert_top_candidate(
            "한반도 비핵화 공동 선언",
            "AKS:ARTICLE:E0078904",
        )

    def test_object_alias_is_indexed(self):
        record = {
            "eid": "E_TEST",
            "headword": "대표 표기",
            "primaryType": "개념",
            "articleAliases": [{"word": "객체형 이칭", "aliasType": "일반"}],
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            jsonl_path = Path(temporary_directory) / "articles.jsonl"
            jsonl_path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            search_index = self.build_encyclopedia_index(
                str(jsonl_path),
                self.policy["candidate_retrieval"],
                "test-release",
            )

        candidates = self.retrieve_candidates(
            term="객체형 이칭",
            search_index=search_index,
            retrieval_policy=self.policy["candidate_retrieval"],
            policy_version=self.policy["policy_version"],
        )
        self.assertEqual(candidates[0]["eid"], "E_TEST")
        self.assertEqual(
            candidates[0]["source_record_id"],
            "AKS:ARTICLE:E_TEST:test-release",
        )
        self.assertEqual(candidates[0]["retrieval_method"], "exact")

    def test_exact_homonyms_are_not_truncated(self):
        records = [
            {
                "search_names": ["고종"],
                "search_text": "",
                "payload": {"source_record_id": f"AKS:ARTICLE:E{index}"},
            }
            for index in range(7)
        ]
        search_index = self.build_search_index(
            records,
            self.policy["candidate_retrieval"],
        )
        candidates = self.retrieve_candidates(
            term="고종",
            search_index=search_index,
            retrieval_policy=self.policy["candidate_retrieval"],
            policy_version=self.policy["policy_version"],
            max_candidates=3,
        )
        self.assertEqual(len(candidates), 7)
        self.assertTrue(
            all(candidate["retrieval_method"] == "exact" for candidate in candidates)
        )

    def test_problem_ids_support_json_and_legacy_list_text(self):
        expected = ["question-1", "question-2"]
        self.assertEqual(
            self.parse_problem_ids('["question-1", "question-2"]'),
            expected,
        )
        self.assertEqual(
            self.parse_problem_ids("['question-1', 'question-2']"),
            expected,
        )

    def test_same_term_with_different_categories_is_not_aggregated(self):
        exam_df = pd.DataFrame(
            [
                {"problem_id": "question-1", "full_text": "한성"},
                {"problem_id": "question-2", "full_text": "한성"},
            ]
        )
        extracted = [
            {
                "problem_id": "question-1",
                "terms": [
                    {
                        "raw_term": "한성",
                        "canonical_term": "한성",
                        "category": "지명",
                    }
                ],
            },
            {
                "problem_id": "question-2",
                "terms": [
                    {
                        "raw_term": "한성",
                        "canonical_term": "한성",
                        "category": "제도",
                    }
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkpoint_path = Path(temporary_directory) / "checkpoint.jsonl"
            with (
                patch.object(self.history_terms_module, "prep_json", return_value=exam_df),
                patch.object(
                    self.history_terms_module,
                    "get_history_terms",
                    return_value=extracted,
                ),
            ):
                result = self.history_terms_module.count_terms(
                    "exam.json",
                    batch_size=2,
                    checkpoint_path=str(checkpoint_path),
                    model_config={
                        "model": "test-model",
                        "temperature": 0,
                        "reasoning_effort": "none",
                    },
                    policy_version="test-policy",
                )

        self.assertEqual(len(result), 2)
        self.assertEqual(set(result["category"]), {"지명", "제도"})
        self.assertTrue(
            all(isinstance(json.loads(value), list) for value in result["problem_ids"])
        )


if __name__ == "__main__":
    unittest.main()

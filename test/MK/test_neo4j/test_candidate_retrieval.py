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
        from scan_body_mentions import (
            build_anchor_automaton,
            find_anchor_entry_indexes,
            scan_body_mentions,
        )
        from scan_definitions import collect_aks_enrichment_terms
        import get_history_terms as history_terms_module

        cls.retrieve_candidates = staticmethod(retrieve_candidates)
        cls.build_search_index = staticmethod(build_search_index)
        cls.build_encyclopedia_index = staticmethod(build_encyclopedia_index)
        cls.parse_problem_ids = staticmethod(parse_problem_ids)
        cls.scan_body_mentions = staticmethod(scan_body_mentions)
        cls.build_anchor_automaton = staticmethod(build_anchor_automaton)
        cls.find_anchor_entry_indexes = staticmethod(find_anchor_entry_indexes)
        cls.collect_aks_enrichment_terms = staticmethod(
            collect_aks_enrichment_terms
        )
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

    def test_exact_candidate_prunes_low_score_expansions(self):
        records = [
            {
                "search_names": ["민며느리제"],
                "search_text": "혼인 풍속",
                "payload": {"source_record_id": "THESAURUS:TERM:EXACT"},
            },
            {
                "search_names": ["민며느리"],
                "search_text": "민며느리 혼인 풍속",
                "payload": {"source_record_id": "THESAURUS:TERM:RELATED"},
            },
            {
                "search_names": ["살림잘하는며느리"],
                "search_text": "며느리의 살림 솜씨에 대한 설화",
                "payload": {"source_record_id": "THESAURUS:TERM:UNRELATED"},
            },
        ]
        search_index = self.build_search_index(
            records,
            self.policy["candidate_retrieval"],
        )

        candidates = self.retrieve_candidates(
            term="민며느리제",
            search_index=search_index,
            retrieval_policy=self.policy["candidate_retrieval"],
            policy_version=self.policy["policy_version"],
        )

        source_ids = {
            candidate["source_record_id"] for candidate in candidates
        }
        self.assertIn("THESAURUS:TERM:EXACT", source_ids)
        self.assertIn("THESAURUS:TERM:RELATED", source_ids)
        self.assertNotIn("THESAURUS:TERM:UNRELATED", source_ids)

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

    def test_weak_aks_candidate_does_not_block_enrichment(self):
        match_results = [
            {
                "canonical_term": "진묘수",
                "category": "유물",
                "is_noise": False,
                "encyclopedia": [
                    {
                        "retrieval_method": "name_ngram",
                        "retrieval_methods": ["name_ngram"],
                    }
                ],
                "thesaurus": [
                    {
                        "retrieval_method": "exact",
                        "retrieval_methods": ["exact"],
                    }
                ],
            },
            {
                "canonical_term": "정확 표제어",
                "category": "유물",
                "is_noise": False,
                "encyclopedia": [
                    {
                        "retrieval_method": "exact",
                        "retrieval_methods": ["exact"],
                    }
                ],
            },
        ]

        targets = self.collect_aks_enrichment_terms(
            match_results,
            self.policy["candidate_retrieval"],
        )

        self.assertEqual(
            targets,
            [{"canonical_term": "진묘수", "category": "유물"}],
        )

    def test_body_mention_scan_finds_jinmyosu_source_record(self):
        match_results = [
            {
                "canonical_term": "진묘수",
                "category": "유물",
                "is_noise": False,
                "encyclopedia": [
                    {
                        "retrieval_method": "name_ngram",
                        "retrieval_methods": ["name_ngram"],
                    }
                ],
            }
        ]
        article = {
            "eid": "E0028448",
            "headword": "무령왕릉 석수",
            "origin": "武寧王陵 石獸",
            "headwordOrigin": "무령왕릉 석수(武寧王陵 石獸)",
            "primaryTypePartA": "유적",
            "primaryType": "유적",
            "era": "고대/삼국/백제",
            "definition": "백제시대의 석수.",
            "body": "무령왕릉의 연도 중앙에 놓인 진묘수(鎭墓獸)의 하나이다.",
            "articleAliases": [],
        }
        mismatched_article = {
            "eid": "E_MISMATCH",
            "headword": "관련 없는 설화",
            "primaryTypePartA": "개념",
            "primaryType": "개념",
            "era": "미상",
            "definition": "관련 없는 설화.",
            "body": "이 설화의 문장에 진묘수가 언급된다.",
            "articleAliases": [],
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            match_path = temporary_root / "name_matches.json"
            article_path = temporary_root / "articles.jsonl"
            match_path.write_text(
                json.dumps(match_results, ensure_ascii=False),
                encoding="utf-8",
            )
            article_path.write_text(
                "\n".join(
                    [
                        json.dumps(article, ensure_ascii=False),
                        json.dumps(mismatched_article, ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            results = self.scan_body_mentions(
                str(match_path),
                str(article_path),
                self.policy,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["body_mention_hit_count"], 1)
        candidate = results[0]["candidates"][0]
        self.assertEqual(candidate["source_id"], "E0028448")
        self.assertEqual(candidate["headword"], "무령왕릉 석수")
        self.assertEqual(candidate["retrieval_method"], "body_mention")
        self.assertIn("진묘수", candidate["snippet"])

    def test_body_anchor_index_preserves_overlapping_terms(self):
        entries = [
            {"anchor": "진묘수"},
            {"anchor": "묘수"},
        ]
        automaton = self.build_anchor_automaton(entries)

        indexes = self.find_anchor_entry_indexes(
            "무령왕릉의진묘수이다",
            automaton,
        )

        self.assertEqual(indexes, {0, 1})


if __name__ == "__main__":
    unittest.main()

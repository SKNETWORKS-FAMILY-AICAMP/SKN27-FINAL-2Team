from build_golden_document_candidates import era_matches, label_question


def check_era_is_a_hard_gate() -> None:
    question = {"id": "GQ", "query": "대동법", "expected_era": "조선 후기", "expected_keywords": ["대동법"]}
    correct_era = {"document_id": "right", "title": "대동법", "chunk_text": "대동법", "metadata": {"era": "조선/조선 후기"}}
    wrong_era = {"document_id": "wrong", "title": "대동법", "chunk_text": "대동법", "metadata": {"era": "고려/고려 후기"}}
    wrong_period = {"document_id": "wrong-period", "title": "대동법", "chunk_text": "대동법", "metadata": {"era": "조선/조선 전기"}}
    assert era_matches(question["expected_era"], correct_era)
    assert not era_matches(question["expected_era"], wrong_era)
    assert not era_matches(question["expected_era"], wrong_period)
    assert label_question(question, [correct_era, wrong_era, wrong_period], 1)["answer_candidate_document_ids"] == ["right"]
    strict_question = {**question, "expected_keywords": ["대동법", "광해군"]}
    assert not label_question(strict_question, [correct_era], 2, require_all_keywords=True)["answer_candidate_document_ids"]


if __name__ == "__main__":
    check_era_is_a_hard_gate()

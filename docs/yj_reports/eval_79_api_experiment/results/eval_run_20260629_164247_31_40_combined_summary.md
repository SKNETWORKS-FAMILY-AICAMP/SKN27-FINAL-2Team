# Evaluation Summary: eval_run_20260629_164247_31_40_combined.jsonl

| 문항 | 배점 | Gate | 문제 | 해설 | 총점 | 판정 | 주요 감점/검증 |
|---:|---:|---|---:|---:|---:|---|---|
| 31 | 2 | FAIL | None | None | None | regenerate | G6: client answer exposure check: 정답 선지만 발문·자료 표현을 두드러지게 반복함; shared_tokens=['6조', '헌의']; shared_token_count_by_choice={'①': 0, '②': 0, '③': 0, '④': 2, '⑤': 0}; answer_lcs=4, max_other_lcs=2 |
| 32 | 1 | PASS | 10 | 5 | 15 | accept |  |
| 33 | 2 | FAIL | None | None | None | regenerate | G6: client answer exposure check: 정답 선지만 발문·자료 표현을 두드러지게 반복함; shared_tokens=['대성', '오산', '학교']; shared_token_count_by_choice={'①': 0, '②': 3, '③': 0, '④': 0, '⑤': 0}; answer_lcs=10, max_other_lcs=3 |
| 34 | 1 | FAIL | None | None | None | regenerate | G6: client answer exposure check: 정답 선지만 발문·자료 표현을 두드러지게 반복함; shared_tokens=['경찰', '태형', '헌병']; shared_token_count_by_choice={'①': 3, '②': 0, '③': 0, '④': 0, '⑤': 0}; answer_lcs=2, max_other_lcs=2 |
| 35 | 2 | FAIL | None | None | None | regenerate | G5 set by client consistency check / G5 set by gate consistency check / G5: client consistency check: gate_consistency_check가 역사 오류 오답을 보고함. invalid_choices=['②', '④', '⑤'] |
| 36 | 2 | PASS | 6 | 4 | 10 | revise | choice_quality 2: 선택지 모두 발문 응답 범주에 적합하고 중복 없음. 다만 유효 매력 오답은 0개로 2점 기준 1개 이상 필요하나, 2점 문항에서 0개는 감점 요인임. |
| 37 | 2 | FAIL | None | None | None | regenerate | G5 set by client consistency check / G5 set by gate consistency check / G5: client consistency check: gate_consistency_check가 역사 오류 오답을 보고함. invalid_choices=['②', '③', '④', '⑤'] |
| 38 | 2 | FAIL | None | None | None | regenerate | G6: client answer exposure check: 정답 선지만 발문·자료 표현을 두드러지게 반복함; shared_tokens=['독립군', '학교']; shared_token_count_by_choice={'①': 0, '②': 0, '③': 0, '④': 0, '⑤': 2}; answer_lcs=4, max_other_lcs=2 |
| 39 | 2 | PASS | 6 | 5 | 11 | revise | choice_quality 2: 선택지 모두 발문 응답 범주에 적합하며 중복 없음. 유효 매력 오답은 0개이나 target_score 2점에서 1개 이상 요구되나 0개로 감점 1점 발생 |
| 40 | 3 | FAIL | None | None | None | regenerate | G6: client answer exposure check: 정답 선지만 발문·자료 표현을 두드러지게 반복함; shared_tokens=['나운규', '영화']; shared_token_count_by_choice={'①': 0, '②': 0, '③': 2, '④': 0, '⑤': 0}; answer_lcs=3, max_other_lcs=1 |

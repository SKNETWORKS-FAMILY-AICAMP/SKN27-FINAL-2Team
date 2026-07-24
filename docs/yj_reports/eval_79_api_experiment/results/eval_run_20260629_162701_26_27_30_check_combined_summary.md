# Evaluation Summary: eval_run_20260629_162701_26_27_30_check_combined.jsonl

| 문항 | 배점 | Gate | 문제 | 해설 | 총점 | 판정 | 주요 감점/검증 |
|---:|---:|---|---:|---:|---:|---|---|
| 26 | 2 | FAIL | None | None | None | regenerate | G5 set by client consistency check / G5 set by gate consistency check / G5: client consistency check: gate_consistency_check가 역사 오류 오답을 보고함. invalid_choices=['②', '③'] / G6: client answer exposure check: 정답 선지만 발문·자료 표현을 두드러지게 반복함; shared_tokens=['서북', '차별']; shared_token_count_by_choice={'①': 2, '②': 0, '③': 0, '④': 0, '⑤': 0}; answer_lcs=5, max_other_lcs=3 |
| 27 | 2 | FAIL | None | None | None | regenerate | G5 set by client consistency check / G5 set by gate consistency check / G5: client consistency check: gate_consistency_check가 역사 오류 오답을 보고함. invalid_choices=['①', '②', '③', '⑤'] |
| 30 | 3 | FAIL | None | None | None | regenerate | G6: client answer exposure check: 정답 선지만 발문·자료 표현을 두드러지게 반복함; shared_tokens=['관군', '농민군']; shared_token_count_by_choice={'①': 2, '②': 0, '③': 0, '④': 0, '⑤': 0}; answer_lcs=4, max_other_lcs=4 |

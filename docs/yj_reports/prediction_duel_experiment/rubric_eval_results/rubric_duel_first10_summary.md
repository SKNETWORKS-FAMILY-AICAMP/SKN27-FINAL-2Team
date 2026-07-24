# Rubric Duel First 10

해설은 생성 대상에 없으므로 해설 5점은 제외하고, 우리 평가표의 Gate와 문제 10점 기준만 적용했다.
Gate FAIL은 평가표 원칙상 점수 채점을 중단하므로, 집계용 실효 점수에서는 0점으로 계산했다.

## Aggregate

| 생성자 | 문항 | Gate PASS | Gate FAIL | 평균 실효 점수(10) | PASS 문항 평균(10) |
|---|---:|---:|---:|---:|---:|
| SLLM | 10 | 7 | 3 | 6.2 | 8.9 |
| GPT | 10 | 4 | 6 | 3.2 | 8.0 |

## Pairwise

| 묶음 | 주제 | target | SLLM Gate/점수 | GPT Gate/점수 | 승자 | 핵심 사유 |
|---:|---|---:|---|---|---|---|
| 1 | 진흥왕 | 2 | FAIL / 0 (G3) | PASS / 6 | **GPT** | G3: client consistency check: gate_consistency_check의 정답 후보 수가 1개가 아님. count=2 |
| 2 | 가 후백제 | 2 | FAIL / 0 (G5) | FAIL / 0 (G6) | **tie** | G5: client consistency check: gate_consistency_check가 G5 FAIL 오답을 보고함. g5_fail_choices=['⑤'] |
| 3 | 거문도 | 1 | PASS / 10 | FAIL / 0 (G1, G2, G5) | **SLLM** | G1: 선택지가 5개이나 ②, ③, ④가 모두 '(가)'로 중복되어 있어 선택지 형식이 부적절함. 정답은 ①~⑤ 중 하나로 정확히 하나여야 하나, 선택지 내용이 중복되어 판독 및 구분이 불가능함. / G2: 선택지 ②, ③, ④가 모두 '(가)'로 동일하게 표기되어 있어 의미 판단이 불가능함. 발문과 … |
| 4 | 총사령관 | 2 | FAIL / 0 (G3) | FAIL / 0 (G3) | **tie** | G3: client consistency check: gate_consistency_check의 정답 후보 수가 1개가 아님. count=2 |
| 5 | 선언문 | 3 | PASS / 10 | FAIL / 0 (G6) | **SLLM** | G6: client consistency check: g6_claim_equivalence_check가 G6 FAIL 조건을 보고함. relation=partial_same_claim, g6_should_fail=False, can_answer_by_text_matching_without_history… |
| 6 | 김종직 | 3 | PASS / 10 | PASS / 9 | **SLLM** | target_difficulty_fit 4: 발문과 자료에서 3묶음 이상의 단서를 해석하고 사건 순서를 판단해야 하므로 3점 기준에 부합 / choice_quality 5: client normalized: 응답 범주 1/1, 중복·포함 관계 0/1, 유효 매력 오답 4개 -> 4/4 |
| 7 | 전황 화폐 부족 현상 | 2 | PASS / 8 | PASS / 9 | **GPT** | target_difficulty_fit 3: 발문은 조선 전기 화폐 유통 문제 상황을 제시하고, 정답은 과전법 수조권 관리로 시기와 상황을 비교해야 하므로 2점 기준에 부합함 / choice_quality 5: client normalized: 응답 범주 1/1, 중복·포함 관계 1/1, 유효 매력 오… |
| 8 | 아일랜드계 영국인 조지 루이스 쇼 | 3 | PASS / 8 | FAIL / 0 (G6) | **SLLM** | G6: client consistency check: g6_claim_equivalence_check가 G6 FAIL 조건을 보고함. relation=partial_same_claim, g6_should_fail=False, can_answer_by_text_matching_without_history… |
| 9 | 고조선 | 2 | PASS / 8 | FAIL / 0 (G5) | **SLLM** | G5: client claim integrity check: 선택지 원문 핵심 술어/관계가 historical_proposition에서 누락됨. choices=['②'] |
| 10 | 태조 왕건 | 2 | PASS / 8 | PASS / 8 | **tie** | target_difficulty_fit 3: 발문은 김부 임명 이후 상황을 묻고, 정답 도출에 2개 단서 묶음과 대표 추론 단서가 필요하며, 선택지 비교가 요구됨 / choice_quality 5: client normalized: 응답 범주 1/1, 중복·포함 관계 1/1, 유효 매력 오답 1개 ->… |

Pairwise result: SLLM 5 / GPT 2 / tie 3

## Detail

| QID | 묶음 | 생성자 | Gate | 문제점수 | 실효점수 | 실패 Gate | 사유 |
|---:|---:|---|---|---:|---:|---|---|
| 101 | 1 | SLLM | FAIL |  | 0 | G3 | G3: client consistency check: gate_consistency_check의 정답 후보 수가 1개가 아님. count=2 |
| 102 | 2 | SLLM | FAIL |  | 0 | G5 | G5: client consistency check: gate_consistency_check가 G5 FAIL 오답을 보고함. g5_fail_choices=['⑤'] |
| 103 | 3 | SLLM | PASS | 10 | 10 |  | target_difficulty_fit 4: 발문 단서 1묶음으로 답사 장소를 직접 식별하고 바로 선택 가능, target_score 1점 기준과 일치 / choice_quality 6: client normalized: 응답 범주 1/1, 중복·포함 관계 1/1, 유효 매력 오답 1개 -> 4/4 |
| 104 | 4 | SLLM | FAIL |  | 0 | G3 | G3: client consistency check: gate_consistency_check의 정답 후보 수가 1개가 아님. count=2 |
| 105 | 5 | SLLM | PASS | 10 | 10 |  | target_difficulty_fit 4: 발문과 자료에서 3개 이상의 단서 묶음을 해석하고, 선언문 이후 사실을 판단하는 복합적 사고가 요구되어 target_score 3점 기준에 부합함 / choice_quality 6: client normalized: 응답 범주 1/1, 중복·포함 관계 1/1… |
| 106 | 6 | SLLM | PASS | 10 | 10 |  | target_difficulty_fit 4: 발문은 3점형 기준에 부합하는 복합 판단형 문제임 / choice_quality 6: client normalized: 응답 범주 1/1, 중복·포함 관계 1/1, 유효 매력 오답 4개 -> 4/4 |
| 107 | 7 | SLLM | PASS | 8 | 8 |  | target_difficulty_fit 3: 발문은 조선 전기 화폐 유통 문제 상황을 제시하고, 정답은 과전법 수조권 관리로 시기와 상황을 비교해야 하므로 2점 기준에 부합함 / choice_quality 5: client normalized: 응답 범주 1/1, 중복·포함 관계 1/1, 유효 매력 오… |
| 108 | 8 | SLLM | PASS | 8 | 8 |  | target_difficulty_fit 4: 발문과 자료에서 3개 이상의 단서 묶음으로 정답 인물과 활동을 식별하고, 선택지에서 세부 차이를 판단해야 하므로 3점 기준에 부합 / choice_quality 4: client normalized: 응답 범주 1/1, 중복·포함 관계 1/1, 유효 매력 오… |
| 109 | 9 | SLLM | PASS | 8 | 8 |  | target_difficulty_fit 3: 자료에서 위만과 준왕 관련 2개 단서로 위만조선 식별 후 범금 8조 법 제정 사실을 선택지에서 고르는 2점형 난이도에 적합 / choice_quality 5: client normalized: 응답 범주 1/1, 중복·포함 관계 1/1, 유효 매력 오답 1개… |
| 110 | 10 | SLLM | PASS | 8 | 8 |  | target_difficulty_fit 3: 발문은 김부 임명 이후 상황을 묻고, 정답 도출에 2개 단서 묶음과 대표 추론 단서가 필요하며, 선택지 비교가 요구됨 / choice_quality 5: client normalized: 응답 범주 1/1, 중복·포함 관계 1/1, 유효 매력 오답 1개 ->… |
| 201 | 1 | GPT | PASS | 6 | 6 |  | target_difficulty_fit 4: 정답 도출에 필요한 단서 2묶음(이사부 건의, 거칠부 담당) 연결, 대표 추론 단서, 식별 후 비교 단계, 심화 표준 개념으로 target_score 2점 기준과 일치 / choice_quality 2: client normalized: 응답 범주 1/1, … |
| 202 | 2 | GPT | FAIL |  | 0 | G6 | G6: client consistency check: g6_claim_equivalence_check가 G6 FAIL 조건을 보고함. relation=partial_same_claim, g6_should_fail=False, can_answer_by_text_matching_without_history… |
| 203 | 3 | GPT | FAIL |  | 0 | G1, G2, G5 | G1: 선택지가 5개이나 ②, ③, ④가 모두 '(가)'로 중복되어 있어 선택지 형식이 부적절함. 정답은 ①~⑤ 중 하나로 정확히 하나여야 하나, 선택지 내용이 중복되어 판독 및 구분이 불가능함. / G2: 선택지 ②, ③, ④가 모두 '(가)'로 동일하게 표기되어 있어 의미 판단이 불가능함. 발문과 … |
| 204 | 4 | GPT | FAIL |  | 0 | G3 | G3: client consistency check: gate_consistency_check의 정답 후보 수가 1개가 아님. count=0 |
| 205 | 5 | GPT | FAIL |  | 0 | G6 | G6: client consistency check: g6_claim_equivalence_check가 G6 FAIL 조건을 보고함. relation=partial_same_claim, g6_should_fail=False, can_answer_by_text_matching_without_history… |
| 206 | 6 | GPT | PASS | 9 | 9 |  | target_difficulty_fit 4: 발문과 자료에서 3묶음 이상의 단서를 해석하고 사건 순서를 판단해야 하므로 3점 기준에 부합 / choice_quality 5: client normalized: 응답 범주 1/1, 중복·포함 관계 0/1, 유효 매력 오답 4개 -> 4/4 |
| 207 | 7 | GPT | PASS | 9 | 9 |  | target_difficulty_fit 3: 발문과 자료에서 화폐 유통 문제 단서 2묶음 이상을 연결해 정답을 식별하고, 선택지의 활동과 시기를 비교해야 하므로 target_score 2점 기준에 부합 / choice_quality 6: client normalized: 응답 범주 1/1, 중복·포함 … |
| 208 | 8 | GPT | FAIL |  | 0 | G6 | G6: client consistency check: g6_claim_equivalence_check가 G6 FAIL 조건을 보고함. relation=partial_same_claim, g6_should_fail=False, can_answer_by_text_matching_without_history… |
| 209 | 9 | GPT | FAIL |  | 0 | G5 | G5: client claim integrity check: 선택지 원문 핵심 술어/관계가 historical_proposition에서 누락됨. choices=['②'] |
| 210 | 10 | GPT | PASS | 8 | 8 |  | target_difficulty_fit 3: 발문 조건에 부합하는 단서 2묶음 이상 존재, 대표 추론 단서로 정답 도출, 식별 후 비교 단계, 심화 표준 개념 수준으로 target_score 2점 기준에 부합 / choice_quality 5: client normalized: 응답 범주 1/1, 중복… |

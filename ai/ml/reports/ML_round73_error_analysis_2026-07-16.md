# 73회차 Walk-Forward 오답 분석 - 2026-07-16

## 요약

- 전체 문항: 50개
- `era + topic_train` 조합 오답: 17개
- `era + topic_train` 조합 정확도: 0.66
- `era` 정확도: 0.98
- `topic_train` 정확도: 0.68
- `topic` 정확도: 0.78

## Topic_train 오분류 조합

| true_topic_train | pred_topic_train | count |
| --- | --- | --- |
| 정치 | 제도 | 3 |
| 정치 | 사건 | 3 |
| 제도 | 사건 | 2 |
| 인물 | 사건 | 2 |
| 정치 | 인물 | 2 |
| 문화 | 인물 | 1 |
| 인물 | 제도 | 1 |
| 사건 | 인물 | 1 |
| 사건 | 정치 | 1 |

## Era 오분류 조합

| true_era | pred_era | count |
| --- | --- | --- |
| 삼국 시대 | 초기 국가 | 1 |

## era + topic_train 조합 오답 문항

| question_no | problem_id | true_era | pred_era | true_topic_train | pred_topic_train | true_topic | pred_topic | is_correct_era | is_correct_topic_train | is_correct_topic |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | cj_v41_1243 | 삼국 시대 | 삼국 시대 | 정치 | 사건 | 외교 | 사건 | True | False | False |
| 3 | cj_v41_1244 | 삼국 시대 | 초기 국가 | 정치 | 정치 | 정치 | 정치 | False | True | True |
| 5 | cj_v41_1245 | 삼국 시대 | 삼국 시대 | 정치 | 인물 | 군사 | 인물 | True | False | False |
| 6 | cj_v41_img_73_06 | 삼국 시대 | 삼국 시대 | 문화 | 인물 | 문화 | 문화 | True | False | True |
| 11 | cj_v41_1250 | 고려 | 고려 | 정치 | 인물 | 정치 | 인물 | True | False | False |
| 12 | cj_v41_1251 | 고려 | 고려 | 정치 | 제도 | 경제 | 제도 | True | False | False |
| 13 | cj_v41_1252 | 고려 | 고려 | 사건 | 인물 | 사건 | 인물 | True | False | False |
| 14 | cj_v41_img_73_14 | 고려 | 고려 | 제도 | 사건 | 제도 | 사건 | True | False | False |
| 17 | cj_v41_1255 | 고려 | 고려 | 인물 | 사건 | 인물 | 인물 | True | False | True |
| 18 | cj_v41_1256 | 고려 | 고려 | 정치 | 사건 | 군사 | 군사 | True | False | True |
| 27 | cj_v41_img_73_27 | 조선 | 조선 | 인물 | 제도 | 인물 | 인물 | True | False | True |
| 32 | cj_v41_1268 | 개항기 | 개항기 | 정치 | 사건 | 외교 | 외교 | True | False | True |
| 37 | cj_v41_1273 | 일제 강점기 | 일제 강점기 | 정치 | 제도 | 경제 | 제도 | True | False | False |
| 38 | cj_v41_1274 | 일제 강점기 | 일제 강점기 | 인물 | 사건 | 인물 | 사건 | True | False | False |
| 42 | cj_v41_1278 | 일제 강점기 | 일제 강점기 | 사건 | 정치 | 사건 | 정치 | True | False | False |
| 47 | cj_v41_1283 | 현대 | 현대 | 제도 | 사건 | 제도 | 정치 | True | False | False |
| 49 | cj_v41_1285 | 현대 | 현대 | 정치 | 제도 | 정치 | 정치 | True | False | True |

## 해석

- 73회차의 조합 오답 대부분은 시대보다 `topic_train` 오분류에서 발생했다.
- `era`는 50문항 중 1개만 틀렸으므로, 73회차 성능 저하의 핵심 원인은 시대 분류가 아니라 통합 주제 분류다.
- 최종 트렌드 예측에서는 `era`보다 `topic_train` 오분류 개선이 우선이다.

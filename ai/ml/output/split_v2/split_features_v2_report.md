# Split Features v2

## 목적

- `ml_han_features_v2`를 기존 v1 구조와 같은 train/predict/answer/full 파일로 나눕니다.
- `predict_input_v2`에서는 `era`, `topic`, `topic_train`, `question_type` 정답 라벨을 제거합니다.
- `topic_train`은 GPT 추천 통합 라벨인 `topic_train_v2`를 최종 학습 라벨로 사용합니다.

## 파일별 행 수

| 파일 | 역할 | 행 수 |
|---|---|---:|
| train_features_v2 | 학습용 | 1200 |
| predict_input_v2 | 예측 입력용 | 400 |
| test_answer_v2 | 평가 정답용 | 400 |
| full_features_v2 | 원본 보관용 | 1600 |

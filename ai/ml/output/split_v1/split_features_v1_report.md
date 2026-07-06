# Split Features v1

## 목적

- `train_features_v1`: 모델 학습용 데이터입니다. 정답 라벨을 포함합니다.
- `predict_input_v1`: 모델 예측용 test 입력 데이터입니다. `era`, `topic`, `question_type`은 빈칸입니다.
- `test_answer_v1`: 평가용 정답 데이터입니다. 예측 단계에서는 사용하지 않습니다.
- `full_features_v1`: 원본 확인용 전체 피처 데이터입니다.

## 파일별 행 수

| 파일 | 역할 | 행 수 | 정답 라벨 포함 |
|---|---|---:|---|
| train_features_v1 | 학습용 | 1200 | 포함 |
| predict_input_v1 | 예측 입력용 | 400 | era/topic/question_type 빈칸 |
| test_answer_v1 | 평가 정답용 | 400 | 포함 |
| full_features_v1 | 원본 보관용 | 1600 | 포함 |

## 평가 흐름

```text
train_features_v1
-> 모델 학습

predict_input_v1
-> 모델 예측
-> pred_era / pred_topic / pred_question_type 생성

test_answer_v1
-> 예측 결과와 실제 정답 비교
-> Accuracy / Macro F1 / Weighted F1 계산
```

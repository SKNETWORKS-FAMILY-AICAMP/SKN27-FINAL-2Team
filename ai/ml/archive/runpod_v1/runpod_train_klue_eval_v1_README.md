# RunPod KLUE 평가 실행 가이드 v1

## 목적

- `era`, 원본 `topic`, 통합 `topic_train`을 같은 코드로 학습/평가합니다.
- `split_time_v1`은 47~70회 학습, 71~78회 평가용입니다.
- `split_era_topic_train_stratified_v1`은 모델 자체 분류 성능 확인용입니다.

## RunPod 폴더 구조

```text
/workspace/
  common/
    eval_splits_v1/
      split_time_v1/
        train.json
        test.json
      split_era_topic_train_stratified_v1/
        train.json
        test.json
  code/
    runpod_train_klue_eval_v1.py
  output/
    klue_eval_v1/
```

## 설치

```bash
pip install -q torch transformers accelerate scikit-learn matplotlib koreanize-matplotlib
```

RunPod PyTorch 템플릿이면 `torch`는 이미 설치되어 있을 수 있습니다.

## 1. 모델 자체 분류 성능 평가

통합 주제:

```bash
python /workspace/code/runpod_train_klue_eval_v1.py \
  --train-json /workspace/common/eval_splits_v1/split_era_topic_train_stratified_v1/train.json \
  --test-json /workspace/common/eval_splits_v1/split_era_topic_train_stratified_v1/test.json \
  --target topic_train \
  --output-dir /workspace/output/klue_eval_v1/topic_train_stratified
```

원본 주제:

```bash
python /workspace/code/runpod_train_klue_eval_v1.py \
  --train-json /workspace/common/eval_splits_v1/split_era_topic_train_stratified_v1/train.json \
  --test-json /workspace/common/eval_splits_v1/split_era_topic_train_stratified_v1/test.json \
  --target topic \
  --output-dir /workspace/output/klue_eval_v1/topic_stratified
```

시대:

```bash
python /workspace/code/runpod_train_klue_eval_v1.py \
  --train-json /workspace/common/eval_splits_v1/split_era_topic_train_stratified_v1/train.json \
  --test-json /workspace/common/eval_splits_v1/split_era_topic_train_stratified_v1/test.json \
  --target era \
  --output-dir /workspace/output/klue_eval_v1/era_stratified
```

## 2. 최신 회차 예측 평가

통합 주제:

```bash
python /workspace/code/runpod_train_klue_eval_v1.py \
  --train-json /workspace/common/eval_splits_v1/split_time_v1/train.json \
  --test-json /workspace/common/eval_splits_v1/split_time_v1/test.json \
  --target topic_train \
  --output-dir /workspace/output/klue_eval_v1/topic_train_time
```

원본 주제:

```bash
python /workspace/code/runpod_train_klue_eval_v1.py \
  --train-json /workspace/common/eval_splits_v1/split_time_v1/train.json \
  --test-json /workspace/common/eval_splits_v1/split_time_v1/test.json \
  --target topic \
  --output-dir /workspace/output/klue_eval_v1/topic_time
```

시대:

```bash
python /workspace/code/runpod_train_klue_eval_v1.py \
  --train-json /workspace/common/eval_splits_v1/split_time_v1/train.json \
  --test-json /workspace/common/eval_splits_v1/split_time_v1/test.json \
  --target era \
  --output-dir /workspace/output/klue_eval_v1/era_time
```

## 출력 파일

각 `--output-dir` 아래에 다음 파일이 생성됩니다.

```text
results.json
results.md
predictions.csv
loss.png
confusion_matrix.png
saved_model/
```

## 주요 옵션

```bash
--max-length 512
--max-epochs 8
--batch-size 8
--learning-rate 2e-5
--patience 2
--n-splits 3
```

class weight를 끄고 싶으면:

```bash
--no-class-weight
```

cross validation 없이 단일 validation split만 쓰고 싶으면:

```bash
--no-cv
```

모델 저장을 생략하고 싶으면:

```bash
--no-save-model
```

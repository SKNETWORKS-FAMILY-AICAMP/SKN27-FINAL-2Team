# 문제 2차 검수 데이터

## 파일

- `make_data.py`: `ai/ml/ML_han_v1.json`을 2차 검수 학습용 데이터로 변환하는 코드
- `make_choice_data.py`: 문항을 선지 단위 정답 판별 데이터로 변환하는 코드
- `review_data.json`: 전체 데이터
- `train.json`: 학습 데이터
- `valid.json`: 검증 데이터
- `test.json`: 테스트 데이터
- `choice_data.json`: 선지별 전체 데이터
- `choice_train.json`: 선지별 학습 데이터
- `choice_test.json`: 선지별 테스트 데이터
- `summary.json`: 생성 결과 요약

## 데이터 구조

데이터는 사람이 보기 쉽도록 `text` 하나로 합치지 않고 구조화해서 저장한다.

```json
{
  "id": "cj_v41_0001",
  "passage": "지문",
  "question": "질문",
  "choices": ["선지1", "선지2", "선지3", "선지4", "선지5"],
  "answer": 1,
  "target_score": 1,
  "label": 1,
  "error_types": []
}
```

학습할 때만 `make_data.py`의 `build_model_text()`처럼 지문, 질문, 선지, 정답을 하나의 입력 문장으로 합쳐서 tokenizer에 넣는다.

## 라벨

```text
0 = 이상 있음 / 재검수 필요
1 = 이상 없음 / 통과 가능
```

현재 데이터는 원본 정상 문항 1600개와 합성 오류 문항 1600개로 구성되어 있다.

## 왜 3200개인가?

원본 `ML_han_v1.json`에는 정상 문항 1600개가 있다.

2차 검수 모델은 `0 = 이상 있음`, `1 = 이상 없음`을 모두 배워야 하므로, 정상 문항마다 합성 오류 문항을 1개씩 추가했다.

```text
정상 문항 1600개(label=1)
+ 합성 오류 문항 1600개(label=0)
= 전체 3200개
```

현재 합성 오류는 아래 4가지가 순서대로 생성된다.

- `ANSWER_LEAKAGE`: 정답 선지를 지문에 직접 추가
- `ANSWER_UNIQUENESS_SUSPICIOUS`: 정답 선지를 다른 보기에도 복제
- `CHOICE_BIAS`: 정답 선지만 길고 구체적으로 변경
- `FORMAT_ERROR`: 선택지 1개 제거

## 다시 생성

```powershell
C:\Users\Playdata\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe ai\ml_question\make_data.py
```

## RunPod 학습

문제 단위 2차 검수 모델은 RunPod에 아래처럼 둔다.

```text
/workspace/
├─ train_runpod.ipynb
├─ common/
│  ├─ train.json
│  ├─ valid.json
│  └─ test.json
└─ output/
```

`output` 폴더는 학습 후 자동 생성된다.

RunPod에서 `/workspace/train_runpod.ipynb`를 열어 위에서부터 실행한다.

노트북에는 한글 주석과 셀별 설명을 넣어두었다.

노트북으로만 실행하면 `requirements.txt`는 없어도 된다. 첫 번째 셀에서 필요한 라이브러리를 직접 설치한다.

터미널 스크립트로 실행하고 싶으면 아래 명령을 사용한다.

이 경우 `/workspace`에 `train_runpod.py`, `requirements.txt`도 함께 올린다.

```bash
cd /workspace
pip install -r requirements.txt

python train_runpod.py \
  --train-json common/train.json \
  --valid-json common/valid.json \
  --test-json common/test.json \
  --output-dir output \
  --model-name klue/roberta-base \
  --epochs 5 \
  --batch-size 8 \
  --max-length 512
```

결과는 `output` 폴더에 저장된다.

- `output/model`: 저장된 모델과 tokenizer
- `output/results.json`: 성능 지표와 threshold
- `output/valid_predictions.csv`: 검증셋 예측 결과
- `output/test_predictions.csv`: 테스트셋 예측 결과

2차 검수 목적이므로 `accuracy`보다 `abnormal_recall`, 즉 `label=0`을 얼마나 잘 잡는지를 우선 확인한다.

학습에는 early stopping이 적용되어 있다.

```text
monitor = validation loss
patience = 2
min_delta = 0.0
```

검증 loss가 2 epoch 동안 개선되지 않으면 학습을 중단하고, 가장 검증 loss가 낮았던 모델 가중치를 사용한다.

## 선지별 정답 판별 모델

선지별 모델은 문항 1개를 선지 5개로 나눠서 학습한다.

```text
1600문항 * 5선지 = 8000개
정답 선지 1600개(label=1)
오답 선지 6400개(label=0)
```

현재 split은 문항 기준 약 8:2이다.

```text
choice_train.json = 6475개, 1295문항
choice_test.json = 1525개, 305문항
```

같은 `question_id`의 선지 5개는 항상 같은 split에 들어간다. 해시 기반 분할이라 정확히 6400/1600은 아니지만 8:2에 가깝게 나뉜다.

다시 생성:

```powershell
C:\Users\Playdata\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe ai\ml_question\make_choice_data.py
```

RunPod에는 아래처럼 둔다.

```text
/workspace/
├─ train_choice_runpod.ipynb
└─ common/
   ├─ choice_train.json
   └─ choice_test.json
```

학습 결과는 `/workspace/choice_output`에 저장된다.

이 모델은 문제 전체 품질 검수 모델이 아니라, 각 선지가 정답 후보인지 판단해서 G3 정답 유일성 검수를 보조하는 모델이다.

validation 데이터는 별도 파일로 올리지 않는다. 노트북 코드에서 `choice_train.json`을 다시 나누어 validation으로 사용한다. 이때 같은 `question_id`의 선지 5개가 train/validation에 섞이지 않도록 `GroupShuffleSplit`을 사용한다.

노트북에는 학습 후 생성 문제를 검수하는 함수도 포함되어 있다.

```text
review_generated_question(row)
```

반환되는 주요 오류 유형은 아래와 같다.

| 오류 유형 | 의미 |
|---|---|
| `ANSWER_LENGTH_BIAS` | 정답 선지가 다른 선지들에 비해 유독 길거나 짧음 |
| `ANSWER_IN_PASSAGE` | 정답 선지가 지문 또는 질문에 포함되어 있음 |
| `NO_ANSWER_CANDIDATE` | 정답 후보가 0개임 |
| `MULTIPLE_ANSWER_CANDIDATES` | 정답 후보가 2개 이상임 |
| `ANSWER_FORMAT_ERROR` | 정답 번호가 선택지 범위를 벗어남 |

생성 문제 파일을 `/workspace/common/generated_questions.json`에 넣으면 노트북 마지막 셀에서 자동 검수한다.

입력 예시:

```json
[
  {
    "id": "gen_0001",
    "passage": "지문",
    "question": "질문",
    "choices": ["선지1", "선지2", "선지3", "선지4", "선지5"],
    "answer": 1,
    "target_score": 2
  }
]
```

결과는 `/workspace/choice_output/generated_review_results.json`에 저장된다.

## 이상 문제 판단 기준

현재 2차 검수에서 이상 문제로 보는 기준은 아래 3가지이다.

| 기준 | 처리 방식 |
|---|---|
| 정답 선지가 다른 선지들에 비해 유독 길거나 짧음 | 규칙 기반 검사 |
| 정답 후보가 0개 또는 2개 이상임 | 선지별 BERT 모델 확률 기반 검사 |
| 정답 선지가 지문 또는 질문에 포함됨 | 규칙 기반 검사 |

정답 후보 개수 검사는 선지별 모델이 각 선지에 대해 계산한 정답 확률을 사용한다.

```text
선지 5개 각각의 정답 확률 계산
정답 확률이 threshold 이상인 선지를 정답 후보로 판단
정답 후보가 1개가 아니면 이상 문제
```

예시:

```text
① 0.91
② 0.08
③ 0.12
④ 0.05
⑤ 0.03
=> 정답 후보 1개, 정상 가능

① 0.88
② 0.82
③ 0.10
④ 0.06
⑤ 0.04
=> 정답 후보 2개, 이상 문제

① 0.21
② 0.18
③ 0.14
④ 0.11
⑤ 0.09
=> 정답 후보 0개, 이상 문제
```

규칙 기반 검사는 `review_rules.py`에 들어 있다.

```bash
python review_rules.py \
  --input generated_questions_with_probs.json \
  --output review_result.json \
  --threshold 0.5
```

입력 데이터에 `answer_probs`가 있으면 정답 후보 개수까지 검사한다.

```json
{
  "id": "gen_0001",
  "passage": "지문",
  "question": "질문",
  "choices": ["선지1", "선지2", "선지3", "선지4", "선지5"],
  "answer": 1,
  "answer_probs": [0.91, 0.08, 0.12, 0.05, 0.03]
}
```

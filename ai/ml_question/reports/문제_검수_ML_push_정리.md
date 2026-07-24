# 문제 검수 ML push 정리

## 1. 작업 목적

생성된 한국사 5지선다 문제를 배포하기 전에, 선지 단위로 이상 여부를 2차 검수하는 ML 모델을 구현하였다.

최종 목적은 문제를 자동 폐기하는 것이 아니라, 검수자가 먼저 확인해야 할 선지를 빠르게 추려내는 것이다.

```text
입력 X = 지문 + 질문 + 선지 1개 + 정답 여부
y = 이진 분류
label 0 = 오류 있음
label 1 = 오류 없음
```

## 2. 최종 버전

최종 버전은 `v15`이다.

v15의 핵심 변경점:

- `klue/roberta-base` 기반 선지 단위 이진 분류 모델 사용
- threshold를 `0.1`로 고정
- 모델 예측과 규칙 기반 오류 코드를 함께 사용
- `QUESTION_CHOICE_MISMATCH`는 자동 오류가 아니라 참고 코드로 처리
- `WEIRD_CHOICE` fallback 의존도를 줄이고 구체 오류 코드 중심으로 정리
- `ㄱ, ㄴ` 조합형과 `(가)-(나)-(다)` 순서형 선지는 선지 단위 검수 대상에서 제외

## 3. push에 포함할 주요 파일

| 파일 | 설명 |
|---|---|
| `.gitignore` | 중간 실험 산출물, 모델 weight, zip 파일 제외 규칙 추가 |
| `ai/ml_question/README.md` | v15 기준 최종 작업 설명 |
| `ai/ml_question/make_choice_quality_data_v10.py` | 최종 학습 데이터 생성 코드 |
| `ai/ml_question/make_choice_quality_runpod_notebook_v15.py` | v15 학습 노트북 생성 코드 |
| `ai/ml_question/train_choice_quality_runpod_v15.ipynb` | RunPod 최종 학습 노트북 |
| `ai/ml_question/choice_quality_train_v10.json` | RunPod 학습용 train 데이터 |
| `ai/ml_question/choice_quality_test_v10.json` | RunPod 평가용 test 데이터 |
| `ai/ml_question/choice_quality_data_v10.json` | train/test 포함 전체 선지 단위 데이터 |
| `ai/ml_question/choice_quality_summary_v10.json` | v10 전처리 데이터 요약 |
| `ai/ml_question/label_generated_errors_with_gpt.py` | 팀원 생성 문제 오류 라벨링 보조 코드 |
| `ai/ml_question/choice_quality_model/` | 팀원 공유용 추론 패키지 |
| `ai/ml_question/reports/문제_검수_ML_발표_정리.md` | 발표 자료용 ML 정리 |
| `ai/ml_question/reports/문제_검수_ML_push_정리.md` | push 전 최종 정리 문서 |

## 4. push에서 제외한 파일

아래 파일들은 `.gitignore`로 제외하였다.

```text
ai/ml_question/choice_quality_model.zip
ai/ml_question/choice_quality_model/model/model.safetensors
ai/ml_question/choice_quality_data_v3~v9.json
ai/ml_question/choice_quality_train_v3~v9.json
ai/ml_question/choice_quality_test_v3~v9.json
ai/ml_question/choice_quality_summary_v3~v9.json
ai/ml_question/choice_quality_skipped_*.json
ai/ml_question/choice_quality_rule_only_*.json
ai/ml_question/choice_quality_synthetic_test_*.json
ai/ml_question/train_choice_quality_runpod_v3~v14.ipynb
ai/ml_question/make_choice_quality_runpod_notebook_v3~v14.py
ai/ml_question/question_quality_*.json
ai/ml_question/train_question_quality_*.ipynb
```

제외 이유:

- 중간 실험 버전이 많아 push 파일이 지나치게 커짐
- 최종 버전은 v15이므로 과거 실험 산출물은 커밋 대상에서 제외
- `model.safetensors`는 약 442MB로 일반 GitHub push 제한을 넘음
- 모델 weight는 Git LFS나 외부 공유 폴더로 별도 관리하는 것이 안전함

## 5. 최종 v15 성능

모델 단독 성능:

```text
Accuracy: 98.37%
오류 Precision: 84.76%
오류 Recall: 85.58%
오류 F1: 85.17%
```

모델과 규칙을 함께 적용한 최종 성능:

```text
Accuracy: 98.53%
오류 Precision: 82.76%
오류 Recall: 92.31%
오류 F1: 87.27%
```

혼동 행렬:

| 구분 | 예측 오류 | 예측 정상 |
|---|---:|---:|
| 실제 오류 | 96 | 8 |
| 실제 정상 | 20 | 1,776 |

해석:

```text
실제 오류 선지 104개 중 96개 탐지
실제 오류 선지 8개 미탐
정상 선지 1,796개 중 20개 오탐
정상 선지 1,776개 정상 통과
```

## 6. v15가 최종인 이유

v14가 모델 단독 수치상으로는 더 높았지만, v15를 최종으로 선택하였다.

이유:

- v15는 선지 단위로 판단하기 어려운 `ㄱ, ㄴ` 조합형과 `(가)-(나)-(다)` 순서형 선지를 제외하였다.
- 이 유형은 선지 하나만 보고 오류 여부를 판단하기 어렵기 때문에 운영 기준상 제외하는 것이 맞다.
- 최종 목적은 단순 점수 최대화가 아니라 실제 검수 업무에 맞는 후보 추출이다.
- v15는 실제 팀원이 사용할 검수 범위와 가장 잘 맞는다.

## 7. 팀원 공유 방법

팀원에게는 아래 폴더를 공유한다.

```text
ai/ml_question/choice_quality_model/
```

팀원이 사용할 흐름:

```text
1. 생성한 문제를 JSON 형식으로 저장
2. predict_choice_quality.py 실행
3. review.csv 확인
4. 검수필요 선지 먼저 확인
5. 오류코드를 참고하여 문제 수정
```

실행 예시:

```bash
python predict_choice_quality.py \
  --model_dir ./model \
  --input ./generated_questions.json \
  --output_csv ./review.csv \
  --output_json ./review.json
```

주의:

```text
Git에는 model.safetensors를 올리지 않는다.
실제 추론 실행에는 model.safetensors가 필요하다.
따라서 팀원에게 모델 weight는 별도 파일로 전달해야 한다.
```

## 8. 추천 commit message

```text
feat: add choice-level question quality review model
```

조금 더 자세히 쓰면:

```text
feat: add v15 choice quality review model workflow
```

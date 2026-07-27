# 문제 2차 검수 ML

생성된 한국사 5지선다 문제를 배포하기 전에 **선지 단위로 2차 검수**하기 위한 ML 작업 폴더입니다.

최종 사용 버전은 **선지 단위 오류 유무 분류 v15**입니다.

```text
입력 X = 지문 + 질문 + 선지 1개 + 정답 여부
y = 이진 분류
label 0 = 오류 있음
label 1 = 오류 없음
```

이 모델은 문제의 정답 번호를 맞히는 모델이 아닙니다. 생성된 문제 안에서 검수자가 먼저 확인해야 할 선지를 찾아주는 보조 모델입니다.

## 최종 방향

- 모델: `klue/roberta-base`
- 학습 방식: 선지 단위 이진 분류
- 최종 버전: `v15`
- threshold: `0.1` 고정
- 오류 코드는 학습 y값이 아니라 결과 해석을 위한 보조 정보
- `ㄱ, ㄴ` 조합형 선지와 `(가)-(나)-(다)` 순서형 선지는 선지 단위 검수 대상에서 제외

## 최종 학습 파일

RunPod에서 최종 학습을 재현하려면 아래 파일을 사용합니다.

```text
/workspace/
├─ train_choice_quality_runpod_v15.ipynb
└─ common/
   ├─ choice_quality_train_v10.json
   └─ choice_quality_test_v10.json
```

v15 노트북은 v10 전처리 데이터를 읽은 뒤, 노트북 내부에서 조합형/순서형 선지를 제외하여 최종 v15 기준으로 학습합니다.

## 주요 파일

| 파일 | 설명 |
|---|---|
| `make_choice_quality_data_v10.py` | 최종 학습에 사용하는 v10 전처리 데이터 생성 코드 |
| `make_choice_quality_runpod_notebook_v15.py` | v15 RunPod 학습 노트북 생성 코드 |
| `train_choice_quality_runpod_v15.ipynb` | 최종 v15 학습 노트북 |
| `choice_quality_train_v10.json` | RunPod 학습용 train 데이터 |
| `choice_quality_test_v10.json` | RunPod 평가용 test 데이터 |
| `choice_quality_summary_v10.json` | v10 전처리 데이터 요약 |
| `review_rules.py` | 규칙 기반 보조 검수 코드 |
| `label_generated_errors_with_gpt.py` | 팀원 생성 문제 오류 라벨링 보조 스크립트 |
| `choice_quality_model/` | 팀원 공유용 추론 패키지 |
| `reports/문제_검수_ML_발표_정리.md` | 발표 자료용 정리 문서 |

## 최종 v15 성능

모델 단독 성능:

```text
Accuracy: 98.37%
오류 Precision: 84.76%
오류 Recall: 85.58%
오류 F1: 85.17%
```

모델 결과와 규칙 기반 검사를 함께 적용한 최종 성능:

```text
Accuracy: 98.53%
오류 Precision: 82.76%
오류 Recall: 92.31%
오류 F1: 87.27%
```

해석:

```text
실제 오류 선지 104개 중 96개 탐지
실제 오류 선지 8개 미탐
정상 선지 1,796개 중 20개 오탐
정상 선지 1,776개 정상 통과
```

## 팀원 사용 방법

팀원은 생성한 문제를 JSON 형식으로 저장한 뒤, `choice_quality_model/predict_choice_quality.py`를 실행하면 됩니다.

```bash
python predict_choice_quality.py \
  --model_dir ./model \
  --input ./generated_questions.json \
  --output_csv ./review.csv \
  --output_json ./review.json
```

결과는 `review.csv`에서 확인합니다.

주요 컬럼:

- `검수상태`: 검수필요, 참고검수, 통과, 검수제외
- `오류확률`: 모델이 계산한 오류 가능성
- `판단근거`: model, rule, model+rule, advisory, none
- `오류코드`: 오류로 판단한 이유
- `참고코드`: 참고용 코드
- `지문`, `질문`, `선지`: 사람이 최종 검수할 내용

## push 주의사항

최종 모델 weight 파일인 `model.safetensors`는 약 442MB로 일반 GitHub push 제한을 넘습니다.

따라서 Git에는 코드, 전처리 데이터, 노트북, 문서, 추론 패키지 구조만 올리고, 모델 weight는 다음 중 하나로 별도 공유하는 것을 권장합니다.

- Git LFS
- Google Drive / Notion / 사내 공유 폴더
- RunPod volume
- Hugging Face Hub private repository

## 폴더 구성

```text
ml_question/
├─ archive/
├─ reports/
├─ choice_quality_model/
├─ README.md
├─ requirements.txt
├─ review_rules.py
├─ make_choice_quality_data_v10.py
├─ make_choice_quality_runpod_notebook_v15.py
├─ train_choice_quality_runpod_v15.ipynb
├─ choice_quality_train_v10.json
├─ choice_quality_test_v10.json
└─ choice_quality_summary_v10.json
```

# 문제 품질검수 ML 모델 v15 공유 폴더

이 폴더는 팀원이 생성한 한국사 문제를 **선지 단위로 2차 검수**하기 위한 공유 패키지입니다.

## 폴더 구성

```text
choice_quality_model/
├─ model/
│  ├─ config.json
│  ├─ model.safetensors
│  ├─ tokenizer.json
│  └─ tokenizer_config.json
├─ docs/
│  ├─ 문제_품질검수(ML)_v15_공유내용.md
│  └─ 문제_품질검수_모델_예상비용.md
├─ sample/
│  └─ sample_questions.json
├─ v15_result_summary/
│  ├─ results.json
│  ├─ test_error_code_summary.csv
│  ├─ test_excluded_choices.csv
│  ├─ test_review.csv
│  ├─ test_predictions.csv
│  └─ threshold_report.csv
├─ predict_choice_quality.py
├─ requirements.txt
└─ reference_error_codes.json
```

## 설치

Python 환경에서 아래 패키지를 설치합니다.

```bash
pip install -r requirements.txt
```

GPU가 있으면 CUDA로 실행되고, 없으면 CPU로 실행됩니다.

## 실행 방법

샘플 파일로 실행하는 예시는 다음과 같습니다.

```bash
python predict_choice_quality.py ^
  --model_dir ./model ^
  --input ./sample/sample_questions.json ^
  --output_csv ./review.csv ^
  --output_json ./review.json
```

Linux 또는 RunPod에서는 다음처럼 실행합니다.

```bash
python predict_choice_quality.py \
  --model_dir ./model \
  --input ./sample/sample_questions.json \
  --output_csv ./review.csv \
  --output_json ./review.json
```

## 입력 JSON 형식

입력은 문항 list 형식입니다.

```json
[
  {
    "question_id": "q_001",
    "material": "지문 내용",
    "question": "질문 내용",
    "answer_number": 3,
    "choices": [
      {"number": 1, "text": "1번 선지"},
      {"number": 2, "text": "2번 선지"},
      {"number": 3, "text": "3번 선지"},
      {"number": 4, "text": "4번 선지"},
      {"number": 5, "text": "5번 선지"}
    ]
  }
]
```

`choices` 안에 `is_answer`가 있으면 그 값을 사용합니다. 없으면 `answer_number`로 정답 여부를 판단합니다.

## 결과 CSV 주요 컬럼

```text
검수상태
우선순위
오류확률
판단근거
오류코드
참고코드
문항ID
선지번호
정답여부
지문
질문
선지
```

## 검수상태 의미

```text
검수필요
모델이 오류 가능성이 높다고 본 선지입니다. 사람이 우선 확인해야 합니다.

참고검수
규칙상 확인해볼 만한 선지입니다. 모델이 강하게 오류라고 본 것은 아닙니다.

통과
모델과 규칙에서 큰 이상을 찾지 못한 선지입니다.

검수제외
"ㄱ, ㄴ" 조합형 선지나 "(가) - (나) - (다)" 순서형 선지입니다.
이 유형은 선지 하나만으로 오류 판단하기 어렵기 때문에 선지 오류 검수 대상에서 제외합니다.
```

## 주의사항

이 모델은 자동 폐기 모델이 아닙니다.

```text
모델 결과 = 검수 후보
최종 판단 = 사람
```

특히 `QUESTION_CHOICE_MISMATCH`는 참고코드로만 사용합니다. 이 코드 하나만으로 자동 오류 처리하지 않습니다.

`ㄱ, ㄴ` 조합형 선지와 `(가) - (나) - (다)` 순서형 선지는 `검수제외`로 표시합니다.
이 유형은 선지 단위 모델이 아니라 문항 전체 기준으로 확인하는 것이 적절합니다.

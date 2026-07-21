# 문제 2차 검수 ML

생성된 한국사 문제의 선지 품질을 2차 검수하기 위한 작업 폴더입니다.

현재 사용 방향은 **선지 단위 이상 여부 분류**입니다.

```text
입력 X = 지문 + 질문 + 선지 1개 + 정답 여부
출력 y = 선지 이상 여부 + 오류 코드
```

정답 번호를 맞히는 모델이 아닙니다.

## 현재 사용 파일

RunPod에 올릴 파일은 아래 3개입니다.

```text
/workspace/
├─ train_choice_quality_runpod_v2.ipynb
└─ common/
   ├─ choice_quality_train_v2.json
   └─ choice_quality_test_v2.json
```

루트에 남겨둔 주요 파일:

- `make_choice_quality_data_v2.py`: 기출 문제와 팀원 생성 문제를 선지 단위 학습 데이터로 변환
- `make_choice_quality_data.py`: v2 전처리에서 재사용하는 공통 변환 함수
- `make_choice_quality_runpod_notebook_v2.py`: RunPod 학습 노트북 생성기
- `train_choice_quality_runpod_v2.ipynb`: RunPod 학습 노트북
- `choice_quality_train_v2.json`: 학습 데이터
- `choice_quality_test_v2.json`: 테스트 데이터
- `choice_quality_summary_v2.json`: 데이터 생성 요약
- `review_rules.py`: 규칙 기반 검수 보조 코드
- `requirements.txt`: 기본 의존성

## v2 데이터 요약

```text
전체: 23,295개
train: 18,702개
test: 4,593개

정상 label=1: 13,685개
이상 label=0: 9,610개
```

팀원 생성 문제의 실제 오류 선지는 수가 적어서 v2 학습 데이터에서는 train에 우선 포함했습니다.
진짜 일반화 성능은 다음 팀원 생성 문제 파일을 별도 test로 받아 확인해야 합니다.

## 오류 처리 방식

BERT가 담당하는 오류:

- `ANSWER_IN_PASSAGE`: 정답 노출
- `ANSWER_LENGTH_BIAS`: 정답 선지 길이 편향
- `WEIRD_DISTRACTOR`: 이상한 오답 선지
- `CHOICE_STYLE_MISMATCH`: 선지 형식 불일치
- `CHOICE_TOO_VAGUE`: 선지 모호함
- `CHOICE_GRAMMAR_ERROR`: 선지 문장 오류

규칙/후처리로 잡는 오류:

- `ANSWER_FORMAT_ERROR`: 정답 형식 오류
- `DUPLICATE_OR_SIMILAR_CHOICE`: 선지 중복/유사
- `CHOICE_COUNT_ERROR`: 선지 개수 오류

## 폴더 구조

`ai/ml` 폴더 구조를 참고해 이전 실험 파일은 `archive` 아래로 정리했습니다.

```text
ml_question/
├─ archive/
│  ├─ problem_level_v1/
│  ├─ choice_answer_v1/
│  ├─ multiple_choice_v1/
│  ├─ choice_quality_v1/
│  └─ gpt_review_v1/
├─ reports/
├─ README.md
├─ make_choice_quality_data.py
├─ make_choice_quality_data_v2.py
├─ make_choice_quality_runpod_notebook_v2.py
├─ train_choice_quality_runpod_v2.ipynb
├─ choice_quality_train_v2.json
├─ choice_quality_test_v2.json
└─ choice_quality_summary_v2.json
```

## 다시 생성

로컬에서 v2 데이터를 다시 만들 때:

```powershell
C:\Users\Playdata\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe ai\ml_question\make_choice_quality_data_v2.py
```

RunPod 노트북을 다시 만들 때:

```powershell
C:\Users\Playdata\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe ai\ml_question\make_choice_quality_runpod_notebook_v2.py
```

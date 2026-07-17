# Colab baseline 노트북의 Markdown 설명만 한글로 다시 복원하는 스크립트입니다.
# split_v1 기준으로 수정된 코드 셀은 유지하고, 깨진 설명 셀만 교체합니다.
# PowerShell 인코딩 문제를 피하기 위해 한글 문구를 UTF-8 Python 파일에 직접 보관합니다.

from __future__ import annotations

import json
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parent / "colab_train_baseline_tfidf_v1.ipynb"


MARKDOWN_TEXTS = [
    """# TF-IDF Baseline v1

한능검 ML v1 데이터로 첫 baseline 성능을 확인하는 노트북입니다.

이번 노트북은 `split_v1` 구조를 사용합니다.

진행 흐름:
1. Google Drive 연결
2. 입력 파일 확인
3. train / predict_input / test_answer 데이터 로드
4. 라벨 분포와 라벨 제거 여부 확인
5. TF-IDF baseline 함수 정의
6. `era`, `topic`, `question_type` 순서로 학습/예측/평가
7. 결과 파일 저장

핵심 구조:

```text
train_features_v1      -> 학습용, 정답 라벨 있음
predict_input_v1       -> 예측용, era/topic/question_type 빈칸
test_answer_v1         -> 채점용, 정답 라벨 있음
```

이 baseline은 딥러닝이 아니므로 CPU 런타임으로 충분합니다.
""",
    """## 1. Google Drive 연결

`Final_project` 폴더가 있는 Google Drive를 Colab에 연결합니다.
""",
    """## 2. 경로 설정 및 파일 확인

`common/split_v1` 폴더 안의 세 파일과 `ml_han_class_weights_v1.json`이 모두 `exists = True`로 나와야 합니다.
""",
    """## 3. 라이브러리 불러오기

Colab에는 보통 `scikit-learn`이 기본 설치되어 있습니다. import 오류가 나면 주석 처리된 설치 명령을 실행하세요.
""",
    """## 4. 데이터 로드 함수 정의

JSON 파일을 읽는 함수입니다. 이 단계에서는 아직 학습을 하지 않습니다.
""",
    """## 5. 데이터 로드

정상이라면 다음 행 수가 나와야 합니다.

- train: 1200
- predict input: 400
- test answer: 400
""",
    """## 6. 샘플 데이터 확인

`train_features_v1`에는 정답 라벨이 있고, `predict_input_v1`에는 예측해야 할 라벨이 빈칸이어야 합니다.
""",
    """## 7. Train/Test 정답 라벨 분포 확인

학습용 train 라벨 분포와 채점용 answer 라벨 분포를 확인합니다. `predict_input_v1`의 예측 대상 라벨은 빈칸이어야 합니다.
""",
    """## 8. Class Weight 확인

소수 라벨일수록 weight가 크게 나와야 합니다. 이 weight는 Logistic Regression 학습에 사용됩니다.
""",
    """## 9. 모델 입력/라벨 추출 함수

학습에는 `train_features_v1`의 `text`와 정답 라벨을 사용합니다.
예측에는 `predict_input_v1`의 `text`만 사용합니다.
평가에는 `test_answer_v1`의 정답 라벨을 사용합니다.
""",
    """## 10. TF-IDF + Logistic Regression 모델 함수

- TF-IDF: 텍스트를 글자 n-gram 중요도 벡터로 변환합니다.
- Logistic Regression: 변환된 벡터로 라벨을 분류합니다.
- `class_weight`: 다수 라벨 쏠림을 줄이고 소수 라벨을 더 크게 반영합니다.
""",
    """## 11. 평가 함수 정의

Accuracy는 참고용이고, 라벨 인밸런스가 있으므로 `Macro F1`을 중요하게 봅니다.

이 함수는 다음 순서로 동작합니다.

```text
train_features_v1로 학습
predict_input_v1로 예측
test_answer_v1과 비교해서 평가
```
""",
    """## 12. era 모델 학습/평가

먼저 시대 분류 모델만 실행합니다. 결과가 나오면 `accuracy`, `macro_f1`, `weighted_f1`을 확인하세요.
""",
    """## 13. topic 모델 학습/평가

주제 분류 모델을 실행합니다.
""",
    """## 14. question_type 모델 학습/평가

문항 유형은 인밸런스가 가장 큰 타깃입니다. Accuracy보다 Macro F1과 라벨별 성능을 더 중요하게 확인해야 합니다.
""",
    """## 15. 전체 요약 확인

세 모델의 핵심 지표를 한 번에 확인합니다.
""",
    """## 16. Markdown 리포트 생성 함수

결과를 파일로 저장하기 전에 사람이 읽기 좋은 Markdown 리포트로 변환합니다.
""",
    """## 17. 결과 저장

JSON과 Markdown 결과를 `common/baseline_tfidf_v1` 폴더에 저장합니다.
""",
    """## 18. 저장된 Markdown 결과 확인

저장된 리포트 앞부분을 출력합니다. 이 파일을 내려받아 공유하면 다음 평가 문서에 반영할 수 있습니다.
""",
    """## 19. 행별 예측 결과 확인

`true_label`은 숨겨둔 정답이고, `pred_label`은 모델이 `predict_input_v1`의 text만 보고 예측한 값입니다.
""",
]


def main() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    markdown_cells = [cell for cell in notebook["cells"] if cell.get("cell_type") == "markdown"]
    if len(markdown_cells) != len(MARKDOWN_TEXTS):
        raise RuntimeError(f"markdown cell count mismatch: {len(markdown_cells)} != {len(MARKDOWN_TEXTS)}")

    for cell, text in zip(markdown_cells, MARKDOWN_TEXTS):
        cell["source"] = text.strip("\n").splitlines(keepends=True)

    NOTEBOOK.write_text(json.dumps(notebook, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(NOTEBOOK)


if __name__ == "__main__":
    main()

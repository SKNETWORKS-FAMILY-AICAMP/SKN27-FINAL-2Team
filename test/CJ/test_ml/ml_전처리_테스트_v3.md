# ML 전처리 테스트 v3

## 목표

v3에서는 코드 안에 직접 들어 있던 문항별 수동 보정 라벨을 제거하고, 별도 JSON 파일로 분리했다.

이 변경의 목적은 전처리 코드를 더 범용적으로 유지하고, 사람이 검수한 라벨 데이터는 코드가 아닌 데이터 파일로 관리하기 위함이다.

## 변경 전 문제

v2까지는 47회 테스트 과정에서 확인한 보정값이 `ML_feature_data.py` 안에 `ROUND_QUESTION_OVERRIDES` 형태로 들어 있었다.

예시는 다음과 같은 구조였다.

```python
(47, 13): {
    "era": "고려",
    "topic": "문화",
    "question_subtype": "사료",
    "core_concept": "이규보",
}
```

이 방식은 테스트 초반에는 빠르게 보정하기 좋지만, 전체 기출 전처리 단계에서는 좋지 않다.

이유는 다음과 같다.

```text
1. 코드가 특정 회차 보정값으로 길어진다.
2. 라벨 데이터와 전처리 로직이 섞인다.
3. 팀원이 라벨만 수정하고 싶어도 Python 코드를 건드려야 한다.
4. 48회, 49회, 76회, 77회처럼 회차가 늘어날수록 관리가 어려워진다.
```

## 변경 후 구조

수동 검수 라벨은 아래 JSON 파일에서 관리한다.

```text
test/CJ/test_ml/ml_label_overrides.json
```

형식은 회차 번호 아래에 문항 번호를 두는 구조다.

```json
{
  "47": {
    "13": {
      "era": "고려",
      "topic": "문화",
      "question_subtype": "사료",
      "core_concept": "이규보"
    }
  }
}
```

앞으로 사람이 직접 검수한 문항은 이 파일에 추가하면 된다.

## 코드 변경 사항

`ML_feature_data.py`에는 아래 상수를 추가했다.

```python
LABEL_OVERRIDES_PATH = Path(__file__).resolve().parent / "ml_label_overrides.json"
```

그리고 아래 함수들을 추가했다.

```python
@lru_cache(maxsize=1)
def load_label_overrides() -> dict:
    ...

def get_label_override(round_no: int, question_no: int) -> dict:
    ...
```

라벨 적용 흐름은 다음과 같다.

```text
문제지 Vision 결과
-> 해설지 Vision 보정
-> ml_label_overrides.json에 사람이 검수한 값이 있으면 최종 적용
-> ml_raw_data.csv 저장
```

## 검수 리포트와의 관계

`ml_label_review.csv`에서 검수 후보로 나온 문항을 사람이 확인한 뒤, 최종 라벨이 확정되면 `ml_label_overrides.json`에 추가한다.

이후 같은 회차를 다시 실행하면 override 값이 자동으로 반영된다.

즉, 앞으로의 관리 흐름은 아래처럼 가져간다.

```text
1. 전처리 실행
2. ml_label_review.csv 확인
3. 사람이 문항 검수
4. 확정 라벨을 ml_label_overrides.json에 추가
5. 같은 회차 재실행
6. ml_raw_data.csv 최종 라벨 갱신
```

## 실행 확인

문법 검사는 통과했다.

```powershell
uv --cache-dir .uv-cache run python -m py_compile test/CJ/test_ml/ML_feature_data.py
```

override 파일 로드도 확인했다.

```powershell
uv --cache-dir .uv-cache run python -c "from test.CJ.test_ml.ML_feature_data import get_label_override; print(get_label_override(47, 13))"
```

PowerShell 콘솔에서는 한글이 깨져 보일 수 있지만, 파일 인코딩은 UTF-8이고 Python 내부 로드는 정상이다.

## 판단

이번 변경은 전체 기출 전처리를 시작하기 전에 필요한 정리다.

전처리 로직은 범용으로 유지하고, 사람이 검수한 라벨은 `ml_label_overrides.json`에 누적하는 방식이 가장 안전하다.

앞으로 48~50회, 76~77회 테스트를 진행하면서 틀린 문항이 발견되면 Python 코드가 아니라 JSON 파일에 보정값을 추가하면 된다.

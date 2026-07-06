# ML_feature_data.py 전처리 흐름 정리

이 문서는 `test/CJ/test_ml/ML_feature_data.py`가 한능검 기출 PDF를 ML 학습용 CSV 데이터로 전처리하는 과정을 설명한다.

## 1. 목적

`ML_feature_data.py`는 한능검 기출 문제 PDF를 읽고, 각 문항을 ML 출제 경향 분석에 사용할 수 있는 라벨 데이터로 변환한다.

출력 파일:

```text
test/CJ/test_ml/output/ml_raw_data.csv
```

현재 코드 기준 CSV 컬럼:

```text
round_no, question_no, era, topic, question_type, question_subtype, core_concept
```

## 2. 사용하는 입력 데이터

### 2.1 기출 문제 PDF

경로:

```text
test/CJ/test_docs/1. 문제지
```

역할:

- 실제 전처리 대상
- 회차별 1~50번 문항을 읽어 라벨 생성
- 예: `47회`, `48회`, `49회`, `50회` 문제지

코드는 파일명에 다음 조건이 포함된 PDF를 찾는다.

- `{회차}회`
- `문제지`
- `답지` 제외
- `해설` 제외

### 2.2 시대/주제 기준 JSON

경로:

```text
test/CJ/test_ml/era_reference.json
```

역할:

- 시대별 핵심 키워드 제공
- 주제별 핵심 키워드 제공
- GPT가 시대와 주제를 분류할 때 참고하는 기준 자료

예상 역할:

```text
강화도조약 → 개항기
훈민정음 → 조선 전기
신간회 → 일제 강점기
```

### 2.3 시대별 인물 참고 JSON

경로:

```text
test/CJ/test_docs/3. 참고 자료/시대별_인물_정리_v2_1.json
```

역할:

- 인물명과 시대를 연결하는 참고자료
- `_entity_index`를 읽어 `{인물명: 시대}` 형태의 인덱스를 만든다
- 인물명이 지문/발문/core_concept에 등장하면 시대 보정에 사용한다

예:

```text
광종 → 고려
세종 → 조선 전기
정조 → 조선 후기
안창호 → 일제 강점기
```

### 2.4 수동 시대 보정값

코드 내부 상수:

```python
MANUAL_ERA_OVERRIDES
```

역할:

- 자주 틀리는 핵심 키워드를 강제로 특정 시대에 연결
- JSON 참고자료로도 애매한 경우를 보정

예:

```text
강화도조약 → 개항기
목민심서 → 조선 후기
팔만대장경 → 고려
창씨개명 → 일제 강점기
```

## 3. 출력 컬럼 설명

### `round_no`

기출 회차 번호.

예:

```text
47
48
49
50
```

ML에서는 시간 순서 기준으로 사용된다. 예를 들어 최근 5개 회차를 보고 다음 회차의 출제 경향을 예측할 때 기준 축이 된다.

### `question_no`

해당 회차 안의 문항 번호.

예:

```text
1
2
...
50
```

한능검은 문항 번호가 대체로 시대 순서 흐름을 가진다. 그래서 `question_no`는 시대 예측 보조 신호로도 사용할 수 있다.

### `era`

문항이 다루는 시대.

허용값:

```text
선사 시대
고조선
초기 국가
삼국 시대
남북국 시대
고려
조선 전기
조선 후기
개항기
일제 강점기
현대
```

주의:

- 선택지에 여러 시대가 섞여 있더라도 지문, 발문, 자료가 묻는 실제 시대를 기준으로 한다.
- `옳지 않은 것` 문제도 틀린 선택지의 시대가 아니라 문제의 중심 시대를 기준으로 한다.

### `topic`

문항의 주제 영역.

허용값:

```text
정치
경제
사회
문화
인물
군사
외교
사상·종교
제도
사건
```

예:

```text
훈민정음 → 문화
귀주대첩 → 군사
강화도조약 → 외교
노비안검법 → 제도
```

ML에서는 “어떤 주제가 자주 출제되는지”를 분석하는 기준이 된다.

### `question_type`

대유형. 문제를 풀 때 요구되는 핵심 행동을 의미한다.

허용값:

```text
역사 지식의 이해
연대기의 파악
역사 상황 및 쟁점의 인식
역사 자료의 분석 및 해석
역사 탐구의 설계 및 수행
결론의 도출 및 평가
```

예:

- 단순 개념 확인 → `역사 지식의 이해`
- 사건 순서 배열 → `연대기의 파악`
- 사료/지도/사진 해석 → `역사 자료의 분석 및 해석`

### `question_subtype`

소유형. 문제 자료나 풀이 방식의 세부 형태를 의미한다.

허용값:

```text
개념
사료
연표
인물
지역
지도
유물
제도
사건
```

예:

- 원문 사료를 읽고 판단 → `사료`
- 연도 순서 판단 → `연표`
- 지도 위치 판단 → `지도`
- 문화재 사진 판단 → `유물`
- 특정 인물 설명 → `인물`

### `core_concept`

문항이 실제로 묻는 핵심 역사 개념.

예:

```text
광종
세도정치
3·1운동
훈민정음
과전법
갑신정변
청산리대첩
강화도조약
```

중요한 점:

- `문제`, `자료`, `시기`, `상황`, `인물`, `정책`처럼 너무 일반적인 단어는 사용하지 않는다.
- 선택지에만 등장하는 오답 키워드보다 지문과 발문이 묻는 중심 개념을 우선한다.
- 이후 시대 보정과 ML feature 생성의 기준이 된다.

## 4. 전처리 전체 흐름

```text
기출 PDF 탐색
    ↓
PDF 텍스트 추출 시도
    ↓
텍스트가 충분하면 GPT 텍스트 분류
텍스트가 부족하면 PDF 페이지를 이미지로 렌더링 후 GPT Vision 분류
    ↓
GPT가 문항별 라벨 JSON 반환
    ↓
문항 번호, 시대, 주제, 유형, 핵심 개념 정규화
    ↓
인물 JSON / 수동 보정 / era_reference 기준으로 시대 재보정
    ↓
누락 문항이 있으면 보강 처리
    ↓
ml_raw_data.csv 저장
```

## 5. 단계별 설명

### 5.1 PDF 찾기

함수:

```python
find_pdf(round_no)
```

역할:

- `test/CJ/test_docs/1. 문제지`에서 회차에 맞는 문제지 PDF를 찾는다.
- 답지나 해설지는 제외한다.

### 5.2 PDF 텍스트 추출

함수:

```python
extract_text(pdf_path)
```

사용 라이브러리:

```python
pdfplumber
```

역할:

- PDF에서 텍스트를 직접 추출한다.
- 텍스트가 충분히 추출되면 GPT 텍스트 분류로 진행한다.

기준:

```python
TEXT_MIN_CHARS = 1000
```

즉, 추출 텍스트가 1000자 이상이면 텍스트 기반 분류를 우선 사용한다.

### 5.3 텍스트 기반 GPT 분류

함수:

```python
classify_text(round_no, text)
```

사용 데이터:

- 문제지 텍스트
- `era_reference.json`
- `시대별_인물_정리_v2_1.json`의 인물 인덱스
- 분류 기준 프롬프트

역할:

- 회차 전체 텍스트를 GPT에게 전달
- 1~50번 문항의 라벨을 JSON으로 반환받음

### 5.4 이미지 기반 GPT Vision 분류

함수:

```python
classify_vision(round_no, pdf_path)
classify_vision_page(round_no, pdf_path, page_index, q_hint)
```

사용 라이브러리:

```python
pypdfium2
```

역할:

- PDF 페이지를 이미지로 렌더링
- 이미지 형태의 문제지를 GPT Vision에 전달
- 텍스트 추출이 어려운 PDF나 이미지 중심 문제를 처리

특징:

- 페이지별로 호출한다.
- 각 페이지에 예상 문항 범위 힌트를 준다.
- API rate limit이 발생하면 재시도한다.

### 5.5 GPT 분류 기준 만들기

함수:

```python
build_classify_suffix()
```

역할:

- GPT에게 어떤 기준으로 분류해야 하는지 알려주는 공통 프롬프트를 만든다.

포함 내용:

- 시대 허용값
- 주제 허용값
- 대유형 허용값
- 소유형 허용값
- core_concept 작성 규칙
- 시대 분류 주의사항
- JSON 반환 형식

### 5.6 레퍼런스 로딩

함수:

```python
load_reference()
```

역할:

- `era_reference.json`의 시대/주제 키워드 로딩
- `시대별_인물_정리_v2_1.json`의 인물별 시대 정보 로딩
- GPT 프롬프트에 참고자료로 넣을 텍스트 생성

### 5.7 인물 인덱스 생성

함수:

```python
load_person_index()
```

역할:

- `시대별_인물_정리_v2_1.json`의 `_entity_index`를 읽는다.
- 인물명이 한 시대에만 속하면 `{인물명: 시대}`로 저장한다.
- 여러 시대에 걸치는 인물은 혼동 가능성이 있어 제외한다.

예:

```text
광종 → 고려
세종 → 조선 전기
정조 → 조선 후기
```

### 5.8 시대 보정

함수:

```python
reference_era_for_core(core_concept)
normalize_era(value, core_concept)
```

시대 보정 우선순위:

1. 인물 인덱스
2. 수동 오버라이드
3. `era_reference.json` 키워드
4. GPT가 반환한 era 값

예:

```text
core_concept = 광종
GPT era = 조선 전기
인물 인덱스 = 고려
최종 era = 고려
```

### 5.9 결과 정규화

함수:

```python
normalize(value, allowed, fallback)
build_rows(round_no, items)
```

역할:

- GPT가 반환한 값이 허용값과 정확히 맞지 않아도 가장 가까운 허용값으로 정리한다.
- 문항 번호가 1~50 범위를 벗어나면 제외한다.
- 같은 문항 번호가 중복되면 첫 번째 결과를 우선한다.

### 5.10 누락 문항 보강

함수:

```python
missing_question_numbers(rows)
classify_missing_text(round_no, text, question_numbers)
merge_rows(primary_rows, supplement_rows)
```

역할:

- GPT 결과에서 빠진 문항 번호를 찾는다.
- `--repair-missing` 옵션을 사용하면 누락 문항만 다시 GPT에 요청한다.
- 그래도 누락이 있으면 Vision 재분류로 한 번 더 보강한다.

### 5.11 CSV 저장

함수:

```python
append_rows(csv_path, rows)
delete_round_from_csv(csv_path, round_no)
load_done_rounds(csv_path)
```

역할:

- 처리 결과를 `ml_raw_data.csv`에 저장한다.
- 이미 처리한 회차는 기본적으로 스킵한다.
- `--force` 옵션을 주면 기존 회차 데이터를 삭제하고 다시 저장한다.

## 6. 실행 옵션

### 기본 실행

```powershell
python test/CJ/test_ml/ML_feature_data.py
```

47~78회 전체를 처리한다. 이미 처리된 회차는 스킵한다.

### 특정 회차만 실행

```powershell
python test/CJ/test_ml/ML_feature_data.py --rounds 47 48 49 50
```

47~50회만 처리한다.

### 기존 결과 삭제 후 재처리

```powershell
python test/CJ/test_ml/ML_feature_data.py --rounds 47 48 49 50 --force
```

기존 CSV에서 해당 회차 데이터를 삭제한 뒤 다시 처리한다.

### Vision 강제 사용

```powershell
python test/CJ/test_ml/ML_feature_data.py --rounds 47 --source vision
```

PDF 텍스트 추출 여부와 관계없이 페이지 이미지를 GPT Vision으로 분류한다.

이미지 문제, 표, 지도, 유물 사진이 많은 회차는 Vision 방식이 더 안정적일 수 있다.

### 누락 문항 보강

```powershell
python test/CJ/test_ml/ML_feature_data.py --rounds 47 --repair-missing
```

1~50번 중 누락된 문항이 있으면 추가 GPT 호출로 보강한다.

## 7. 현재 코드의 한계

현재 `ML_feature_data.py`의 CSV 출력은 7개 컬럼이다.

```text
round_no, question_no, era, topic, question_type, question_subtype, core_concept
```

이전에 논의한 다음 컬럼들은 현재 코드의 `CSV_FIELDNAMES`에는 포함되어 있지 않다.

```text
person_entities
event_entities
institution_entities
keyword_tags
reference_matches
```

따라서 인물/사건/제도 기반 ML 예측을 본격적으로 하려면 위 컬럼을 다시 추가하거나 별도 feature 생성 스크립트를 만들어야 한다.

## 8. ML 관점에서 현재 데이터가 의미하는 것

현재 CSV는 다음 분석에 적합하다.

- 회차별 시대 출제 비율
- 회차별 주제 출제 비율
- 대유형/소유형 출제 흐름
- 특정 core_concept 출현 빈도
- 최근 N회 기준 다음 회차 시대/주제 경향 예측

하지만 다음 분석에는 아직 부족하다.

- 특정 인물 출제 가능성
- 인물 묶음 예측
- 사건/제도/문화재 클러스터 예측
- “현종-강감찬-귀주대첩” 같은 개념 묶음 분석

이 분석을 하려면 `core_concept`만으로는 부족하고, 인물/사건/제도/키워드 컬럼을 추가하는 것이 좋다.

## 9. 추천 다음 단계

1. 현재 7개 컬럼 기준으로 47~50회차 라벨 정확도 검수
2. `core_concept`가 너무 넓은 값인지 확인
   - 예: `신라`, `고려`, `조선 후기`처럼 넓은 값은 개선 필요
3. 인물/사건/제도/키워드 컬럼 확장 여부 결정
4. 확장 컬럼을 추가한다면 `시대별_인물_전처리.json`을 기준으로 후처리 보강
5. 전체 47~78회차 재전처리

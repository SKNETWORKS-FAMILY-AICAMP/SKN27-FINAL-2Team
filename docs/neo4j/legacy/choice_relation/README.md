# 기출 정답–오답 관계 분석

> 레거시 문서: 별도 오답 그래프를 전제로 한 실험 문서다. 현재 사실 그래프 기준에서 제외한다.

## 목적

원문 선지를 보존하면서 정답과 각 오답이 역사적으로 무엇을 공유하고 무엇을
바꿨는지 구조화한다. 현재 Entity Resolution의 동명이인 판정과는 독립된 단계다.

## 현재 범위

- 입력: `ai/ml/ML_han_v1.json`
- 포함: `han_cj_v41`의 `standard_select`
- 제외: 이미지 OCR 원천, 부정 선택형, 순서형, 조합형, 연표·지도 위치형
- 일반 선택형 후보: 1,212문항
- 참조 불일치 제외: 116문항
- 현재 clean-only 대상: 1,096문항, 5,480선지, 정답–오답 쌍 4,384개

부정 선택형과 구조형 문항은 정답 표시와 역사적 사실성의 관계가 다르므로 후속
버전에서 별도 규칙으로 처리한다.

발문에서 요구하는 `(가)`, `㉠` 등의 표식이 자료에 없으면 모델 입력에서 제외하고
`choice_relation_input_integrity_issues.csv`에만 기록한다.

## 실행

API 호출 없이 전체 task와 실행 계획만 생성:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_choice_relation_analysis.py
```

소량의 task만 생성:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_choice_relation_analysis.py --limit 5
```

실제 LLM 호출은 `--execute`와 양수 `--execute-limit`을 함께 줘야 한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_choice_relation_analysis.py --execute --execute-limit 5
```

`--execute-all`은 전체 호출을 명시하는 옵션이다. 비용과 결과 검수 전에는 사용하지
않는다.

## 주요 산출물

| 파일 | 내용 |
|---|---|
| `choice_relation_tasks.jsonl` | LLM 입력용 문항·원문 선지 |
| `source_choices.csv` | 원문 선지와 정답 표시 |
| `choice_relation_input_integrity_issues.csv` | 발문 참조 표식이 자료에 없는 문항 |
| `choice_relation_decisions.jsonl` | 구조화된 모델 원본 결과 |
| `choice_claims.csv` | 선지별 문맥 명제와 실제 사실 |
| `distractor_relations.csv` | 정답–오답의 공유·변경 차원 |
| `choice_relation_validation_errors.csv` | ID·개수·허용값 검증 오류 |
| `choice_relation_evaluation_metrics.json` | seed 기준 관계 분류 지표 |
| `choice_relation_evaluation_comparison.csv` | 오답 쌍별 seed·예측 비교 |
| `choice_relation_manifest.json` | 입력 digest와 단계별 처리 건수 |

검증된 결과만 추후 `Choice`, `Claim`, `DISTRACTOR_OF` 관계의 Neo4j 적재 입력으로
사용한다. 현재 단계에서는 Neo4j를 변경하지 않는다.

## 검증 단계

1. 구조 검증: 선지 5개와 오답 관계 4개가 빠짐없이 원문 ID에 대응하는지 검사한다.
2. 상태 검증: 불확실성이나 낮은 신뢰도가 있으면 자동 승인하지 않는다.
3. seed 비교: 초기 5문항·20쌍의 예상 관계와 주 관계 및 전체 관계 집합을 비교한다.

seed 기준표는 아직 `INITIAL_SEED`이므로 평가 파일의
`official_evaluation_available`은 `false`다. 전문가 검수 후 `REVIEWED`로 바꾸기
전에는 공식 모델 정확도로 사용하지 않는다.

관계 라벨은 최소·비중복 원칙을 따른다. `TARGET_SWAP` 또는 `TIME_SHIFT`만으로
차이가 설명되면 그 결과로 따라오는 일반적인 활동 차이를 `ATTRIBUTE_SWAP`으로
반복하지 않는다.

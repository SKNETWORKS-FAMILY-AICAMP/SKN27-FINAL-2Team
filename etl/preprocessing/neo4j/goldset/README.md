# Entity Resolution 골든셋 안내

사람이 실제로 작성할 파일은 `human_review_csv` 안의 CSV 두 개다. Cursor에서 직접 열어
수정할 수 있다.

| 위치 | 의미 |
|---|---|
| `human_review_csv/human_review_cases.csv` | 용어 case 100건의 최종 상태 입력 |
| `human_review_csv/human_review_candidates.csv` | 원천 후보 606건의 역할·동일 실체 묶음 입력 |
| `internal/source_snapshot` | 표본 task, 빈 CSV 양식, 코드 baseline, 표본 생성 기록 |
| `internal/validation` | CSV 작성 상태·모순 검사와 사람 gold decision 변환 결과 |
| `internal/model_predictions` | 같은 100개 case에 대한 모델 판정과 checkpoint |
| `internal/evaluation` | 사람 정답과 모델 판정의 정확도·오병합 평가 |

`internal`은 직접 작성하지 않는 파이프라인 산출물이다. 특히 `internal/source_snapshot`은
원본 증거이므로 수정하지 않는다. 1차 사람 검수가 끝나기 전에는
`rule_based_baseline.csv`를 보지 않는다. 검수 중 확인은 다음 명령으로 수행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/import_gold_set.py `
  etl/preprocessing/neo4j/goldset/human_review_csv `
  etl/preprocessing/neo4j/goldset/internal/source_snapshot/gold_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/validation `
  --allow-partial
```

# Neo4j 전처리 설정 안내

`resolution_policy.json`은 실행 진입점이다. 실제 설정은 역할별 JSON을 순서대로
불러와 하나의 정책으로 합친다. 기존 실행 명령과 `--policy` 경로는 바꾸지 않는다.

| 파일 | 수정하는 경우 | 주요 내용 |
|---|---|---|
| `pipeline.json` | 모델·출력 위치·테스트 범위를 바꿀 때 | 정책 버전, 원천 release, 용어 추출 모델, 출력 레이아웃, 테스트 실행값 |
| `candidate_retrieval.json` | 후보 검색 재현율·정밀도를 조정할 때 | 이름 유사도, definition/body 보강, 커버리지, 노이즈, category 호환표 |
| `entity_resolution.json` | 원천 간 동일 실체 판정 규칙을 바꿀 때 | EntityType, 원천 필드, 시대 제외값, 병합 신호, ID·최종 파일 정책 |
| `review_goldset.json` | LLM 판별·골든셋 실행을 바꿀 때 | 검토 모델, task·decision 파일, 골든셋 표본·평가 설정 |
| `resolution_policy.json` | 설정 파일 구성을 바꿀 때만 | 위 파일들의 로드 순서 |

## 일반적으로 조정해도 되는 값

- `candidate_retrieval.minimum_score`, `max_candidates`
- `candidate_retrieval.enrichment_skip_retrieval_methods`
- `definition_scan`, `body_mention_scan`의 검색 범위와 후보 수
- `category_compatibility`
- `entity_resolution.source_feature_policy.era_excluded_values`
- LLM 모델·timeout·retry와 골든셋 표본 크기

## 골든셋 파일럿 설정

`review_goldset.json`의 기본값은 사람 검수 부담을 확인하기 위한 20개 case 파일럿이다.

- `sample_size`: 검수 case 수. 현재 `20`
- `minimum_cases_per_category`: 모집단에 존재하는 category별 우선 표본 수. 현재 `1`
- `maximum_candidates_per_pilot_case`: 파일럿에서 우선 선택할 case의 최대 후보 수. 현재 `10`
- `implicit_candidate_role`: 완료 case에서 역할이 빈 후보에 적용할 역할. 현재 `REJECTED`
- `initial_review_status`: 아직 검수를 시작하지 않은 상태. 현재 `NOT_STARTED`
- `overwrite_protection`: 사람 입력이 있는 CSV의 자동 덮어쓰기를 막을 컬럼 계약

후보별 검수자·완료 상태는 두지 않는다. 검수자와 완료 상태는 case CSV에서 한 번만
기록하고, 후보 CSV에는 정답·근거·애매 후보의 역할만 입력한다.

상태 어휘, ID prefix, 원천 필드명은 CSV·Neo4j 계약에 영향을 주므로 관련 코드와
테스트를 함께 바꿀 때만 수정한다.

## JSONL은 설정이 아니다

`*.jsonl`은 체크포인트·검토 task·판정 결과처럼 레코드 한 건을 한 줄에 저장하는
실행 데이터다. 설정 변경은 이 폴더의 JSON에서 하고, output이나 goldset의 JSONL을
설정처럼 수정하지 않는다.

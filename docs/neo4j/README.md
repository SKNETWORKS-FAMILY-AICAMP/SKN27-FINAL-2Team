# 한국사 사실 그래프 문서

> 상태: `CURRENT`
> 기준일: 2026-07-25

현재 Neo4j의 목표는 검증된 사실·분류·근거 그래프를 구축하여 RAG가
같거나 유사한 경로를 공유하는 다른 `CanonicalEntity`를 찾을 수 있게 하는 것이다.

별도의 오답 사실 그래프는 만들지 않는다. 후보 탐색과 문제 생성은 사실 그래프를
조회하는 RAG·문제 생성 계층의 책임이다.

## 현재 기준 문서

| 문서 | 역할 |
|---|---|
| [FACT_GRAPH_CURRENT_DESIGN.md](./FACT_GRAPH_CURRENT_DESIGN.md) | 목표, 노드·관계, 사실과 오답의 분리, 구현 순서 |
| [01_fact_graph_current_data_eda.md](./01_fact_graph_current_data_eda.md) | 현재 raw·용어·커버리지·ER·관계 데이터의 최신 EDA |
| [02_source_first_fact_eda.md](./02_source_first_fact_eda.md) | 공식 원천 중심 관계 규모, 안전 등급, 기출 anchor 교체 가능성 |

## 현재 운영 문서

| 문서 | 역할 |
|---|---|
| [PREPROCESSING_RUNBOOK.md](../../etl/preprocessing/neo4j/PREPROCESSING_RUNBOOK.md) | 전처리·ER·골든셋 실행 방법 |
| [ENTITY_RESOLUTION_TUNING_LOG.md](../../etl/preprocessing/neo4j/ENTITY_RESOLUTION_TUNING_LOG.md) | Entity Resolution 평가와 정책 조정 이력 |
| [goldset/README.md](../../etl/preprocessing/neo4j/goldset/README.md) | 현재 골든셋 구조와 산출물 |
| [output/README.md](../../etl/preprocessing/neo4j/output/README.md) | 현재 출력 디렉터리 계약 |

## 레거시

[legacy](./legacy/) 아래 문서는 2026-07-25 이전의 범용 Graph·오답 후보·고정
path pattern 설계와 과거 EDA 스냅샷이다.

현재 구현이나 수치 판단에는 사용하지 않는다. 필요한 배경과 결정 이력 확인 용도로만
보존한다.

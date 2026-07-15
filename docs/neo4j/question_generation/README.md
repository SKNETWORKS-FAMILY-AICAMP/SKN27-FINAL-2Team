# 한국사 문제 생성용 Neo4j 재설계 문서

> 문서 상태: `TARGET-DESIGN`
> 기준일: 2026-07-15
> 구현 상태: EDA와 목표 설계 문서화 완료, 애플리케이션·ETL·라이브 Neo4j 적용 전.

## 1. 이 문서 묶음의 전제

78회 한국사능력검정시험 기존 문항은 다음 생성 규칙을 역설계하기 위한 분석 표본이다.

- 어떤 역사 지식을 단서로 사용하는가
- 발문이 어떤 관계를 묻는가
- 정답과 오답이 어느 슬롯에서 달라지는가
- 같은 의미 경로에서 다른 대상을 어떻게 찾는가
- 유형과 난이도를 어떤 조건에서 선택할 수 있는가

실제 역사 지식은 `etl/raw_data`의 다음 세 데이터군에서 구축한다.

1. 한국민족문화대백과사전
2. 한국고전종합DB 관계망의 인물·사건·관계 데이터
3. 한국역사용어시소러스

전체 본문과 검색 임베딩은 RAG 저장소가 담당하고, Neo4j는 정답 경로와 오답 후보 경로를 탐색할 수 있는 정규화된 엔터티·사실·생성 패턴을 담당한다.

## 2. 목표 생성 흐름

```mermaid
flowchart LR
    material["문제 재료<br/>키워드·reference binding·발문의도"]
    eligible["가능한 유형·난이도 계산 후<br/>제약된 랜덤 선택"]
    answerRag["RAG<br/>reference·선택 답 근거 검색"]
    passage["외부 API<br/>근거 기반 지문 생성<br/>정답 생성 금지"]
    graph["Neo4j<br/>선택된 EligibilityProfile에서<br/>동일 PathPattern 후보 조회"]
    distractorRag["RAG<br/>option별 참인 문맥 근거 검색"]
    bundle["생성 번들<br/>지문·OptionBinding·truth·근거"]
    sllm["sLLM<br/>최종 문항 표현 생성"]
    validate["결정론적 검증<br/>정답 유일성·근거·누출·중복"]

    material --> eligible --> answerRag --> passage --> graph
    graph --> distractorRag --> bundle --> sllm --> validate
```

핵심 원칙은 다음과 같다.

- 선택 답은 LLM이 새로 만들지 않는다. 사전에 선택되고 검증된 `QuestionBlueprint`·`PathInstance`·mismatch proof의 `OptionBinding`이다.
- 지문 생성 API는 승인된 정답 근거를 표현만 바꾼다.
- FALSE option은 거짓 역사를 자유 생성하지 않는다. 다른 대상에 대해 참인 Fact와 현재 문맥의 결정론적 mismatch proof를 함께 사용한다.
- RAG는 근거 본문을 검색하고, Neo4j는 엔터티와 의미 경로를 탐색한다.
- sLLM은 확정된 재료를 문항 문장으로 조립하며 역사 사실을 추가할 권한이 없다.
- 유형과 난이도는 독립적인 완전 랜덤이 아니라, 생성 가능한 조합 중 가중 무작위로 선택한다.

## 3. 문서 읽는 순서

| 순서 | 문서 | 목적 |
|---:|---|---|
| 1 | [01_raw_data_eda.md](./01_raw_data_eda.md) | 세 원천의 전체 행 EDA와 실제 활용 가능 범위 |
| 2 | [02_exam_pattern_analysis.md](./02_exam_pattern_analysis.md) | 78회 문항에서 도출한 유형·발문의도·오답 슬롯 |
| 3 | [03_storage_and_material_contract.md](./03_storage_and_material_contract.md) | Neo4j·RAG·운영 DB의 책임과 문제 재료 계약 |
| 4 | [04_etl_and_entity_resolution.md](./04_etl_and_entity_resolution.md) | raw에서 canonical entity와 승인 Fact까지의 ETL |
| 5 | [05_neo4j_generation_schema.md](./05_neo4j_generation_schema.md) | 노드·관계·제약·인덱스의 의미 |
| 6 | [06_distractor_and_difficulty.md](./06_distractor_and_difficulty.md) | 같은 경로의 다른 대상 검색과 난이도 제어 |
| 7 | [07_runtime_generation_pipeline.md](./07_runtime_generation_pipeline.md) | RAG·외부 API·sLLM을 연결하는 런타임 절차 |
| 8 | [08_validation_and_roadmap.md](./08_validation_and_roadmap.md) | 품질 게이트, 테스트, 단계별 구현 순서 |

## 4. 가장 중요한 설계 결정

### 문제와 역사 지식을 분리한다

`Question` 노드를 78회 문항 수만큼 만드는 것이 이번 설계의 목적이 아니다. 78회에서 추출한 것은 `QuestionType`, `StemIntent`, `Modifier`, `PathPattern`, `DifficultyPolicy`와 같은 생성 규칙이다.

### 같은 경로는 같은 실제 노드가 아니라 같은 의미 패턴이다

예를 들어 다음 두 묶음은 구체적인 노드는 다르지만 같은 `PERSON_CREATED_WORK` 패턴이다.

```text
김정희 - CREATED -> 세한도
정선   - CREATED -> 인왕제색도
```

김정희 문제에서 `인왕제색도`를 오답으로 사용할 때 Neo4j는 두 사실이 같은 패턴임을 찾아낸다. RAG는 `인왕제색도는 정선의 작품`이라는 참인 근거를 가져오고, 최종 문항에서는 그 작품을 김정희의 작품 후보로 배치한다.

### 원문 부재는 거짓이 아니다

그래프에 관계가 없다는 사실만으로 오답을 확정하지 않는다. 후보가 다른 대상과 맺은 참인 관계는 후보의 역사적 타당성을 보이는 재료일 뿐 현재 문맥의 거짓을 단독으로 증명하지 못한다. 정확한 역할 범위를 덮는 `CompletenessAssertion`, 확정적인 시간·장소 불일치 또는 직접 반박 근거로 현재 문맥의 FALSE를 별도로 검증해야 한다.

### 긴 본문은 Neo4j에 넣지 않는다

대백과사전 본문, 문서 청크, 임베딩, 생성 지문은 RAG 또는 운영 저장소에 둔다. Neo4j에는 `chunk_id`, 출처 등급, 검토 상태 같은 `EvidenceRef`만 둔다.

## 5. 적용 상태

현재 산출물은 EDA와 목표 설계 문서다. 애플리케이션 코드, ETL 코드, 생성 CSV와 라이브 Neo4j 데이터에는 아직 적용되지 않았다.

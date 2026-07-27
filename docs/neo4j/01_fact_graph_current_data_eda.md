# 사실 그래프 구축용 현재 데이터 EDA

> 상태: `SOURCE-DATA-BASELINE`
> 기준일: 2026-07-27
> 범위: AKS·ITKC·한국역사용어시소러스, 기출 추출 용어, Entity Resolution staging,
> 원천 관계 staging

> 주의: 1~12절은 최종 registry 전 staging EDA다. 실제 Fact Graph release는
> 아래 13절과 `03_fact_graph_release_and_load.md`를 기준으로 한다.

## 1. 결론

현재 데이터만으로 첫 번째 사실 그래프 MVP를 만드는 것은 가능하다.

다만 현재 산출물은 다음 상태다.

- SourceRecord와 원천 관계 재료는 충분하다.
- AKS 유형·시대와 시소러스 분류 경로는 대부분의 ER case에 후보를 제공한다.
- ITKC 직접 관계는 인물과 사건 중심으로 충분한 양이 있다.
- 추출 용어의 승인된 `CanonicalEntity`는 아직 0개다.
- 제도·기관·문화재·문헌·장소의 원자 `Fact`와 `EvidenceSpan`은 아직 만들어지지 않았다.

따라서 현재 차단점은 raw 데이터 부족이 아니라 다음 두 가지다.

1. 5,412개 `AMBIGUOUS` case의 canonical 대상 확정
2. canonical 대상에 연결할 검증된 `SemanticClass`와 `Fact` 생성

---

## 2. 집계 분모

용어 수는 처리 단계마다 분모가 다르다.

| 단계 | 건수 | 의미 |
|---|---:|---|
| `unique_exam_terms.csv` 행 | 5,521 | category가 다른 같은 표기를 포함한 집계 행 |
| 고유 `canonical_term` 표기 | 5,268 | 공백·유니코드 정규화 전 문자열 기준 |
| 커버리지 모집단 | 5,211 | 노이즈 제거 후 정규화된 고유 이름 기준 |
| Entity Resolution case | 5,502 | 기출 용어 외 관계 seed와 정규화 case를 포함 |

따라서 `5,521`, `5,268`, `5,211`, `5,502`를 같은 의미의 “용어 수”로
혼용하면 안 된다.

---

## 3. 원천 데이터

### 3.1 한국민족문화대백과사전

| 파일 | 행 | 크기 | 용도 |
|---|---:|---:|---|
| `articles_list.jsonl` | 75,935 | 125,467,892 bytes | 표제어 목록과 기본 메타데이터 |
| `articles_detail.jsonl` | 75,835 | 633,096,076 bytes | 정의·시대·유형·상세 본문·URL |
| `articles_errors.jsonl` | 4 | 244 bytes | 수집·파싱 오류 감사 |

현재 후보 레코드에서 AKS 메타데이터의 `primary_type`, `era`, `definition`은
모두 채워져 있다. 이는 `EntityType`, `Era`, Fact 후보 추출의 주요 재료다.

AKS가 제공하는 관련 문서나 본문 언급을 바로 canonical 관계로 적재해서는 안 된다.
endpoint 해소와 근거 범위 검증이 선행돼야 한다.

### 3.2 한국고전종합DB 관계망

#### 노드 원천

| 입력 | 원본 행 | 고유 ID | 중복 행 |
|---|---:|---:|---:|
| 인물 | 65,389 | 65,303 | 86 |
| 사건 | 1,542 | 600 | 942 |

인물 중복은 0.13%로 작지만 사건은 같은 `event_id`가 scope와 URL에 따라 반복된다.
사건 노드는 `event_id` 기준으로 합치고 scope와 URL을 배열로 보존해야 한다.

#### 관계 원천

| 입력 | 원본 행 | 의미상 고유 관계 | 중복 행 | 중복률 |
|---|---:|---:|---:|---:|
| 인물 관계 | 206,764 | 206,507 | 257 | 0.12% |
| 사건–인물 관계 | 15,392 | 6,918 | 8,474 | 55.05% |

사건 관계는 `event_subject`와 `event_period` scope에 같은 관계가 반복되므로
원본 행을 그대로 적재하면 관계 수가 크게 부풀어 오른다.

관계 endpoint 무결성:

| 검사 | 누락 ID |
|---|---:|
| 인물 관계 시작 인물 | 0 |
| 인물 관계 대상 인물 | 0 |
| 사건 관계 사건 | 0 |
| 사건 관계 인물 | 0 |

#### 인물 관계 유형

| 관계 | 원본 행 |
|---|---:|
| 형제 | 36,951 |
| 자 | 30,497 |
| 부 | 30,495 |
| 조부 | 23,230 |
| 장인 | 22,890 |
| 사위 | 22,889 |
| 증조부 | 18,219 |
| 교유 | 8,014 |
| 스승 | 4,048 |
| 제자 | 4,044 |
| 생부 | 2,092 |
| 출자 | 2,089 |
| 아내 | 447 |
| 남편 | 446 |
| 모 | 411 |
| 생모 | 2 |

ITKC 직접 관계는 인물 계보·교유·사제 관계와 사건 참여 관계에 강하다. 제도,
기관, 문헌, 문화재, 지역 관계를 이 원천만으로 완성할 수는 없다.

### 3.3 한국역사용어시소러스

| 항목 | 건수 |
|---|---:|
| 전체 행 | 62,409 |
| 고유 `term_id` | 62,409 |
| 중복 `term_id` | 0 |
| 최상위 분류 `term_kind=0` | 17 |
| 분류 용어 `term_kind=1` | 794 |
| 일반 용어 `term_kind=2` | 61,598 |
| 분류 경로가 있는 행 | 61,599 |
| 분류 경로가 없는 행 | 810 |
| 원본 고유 분류 경로 | 382 |
| 누적 경로로 만든 분류 노드 | 515 |

`topterm_id` 무결성:

| 검사 | 건수 |
|---|---:|
| 존재하는 `term_id`를 가리킴 | 62,409 |
| 없는 `term_id`를 가리킴 | 0 |
| 자기 자신을 가리키는 최상위 분류 | 17 |

중요한 해석:

- `topterm_id`는 모든 하위 용어가 최상위 17개 분류 중 하나를 가리킨다.
- 따라서 `topterm_id`를 직속 부모 관계로 사용하면 안 된다.
- 세부 `SemanticClass` 계층은 `term_lk` 경로를 분해해 만든다.

---

## 4. 기출 용어 EDA

### 4.1 전체 규모

| 지표 | 값 |
|---|---:|
| 집계 행 | 5,521 |
| 고유 표기 | 5,268 |
| 포함 문항 | 1,599 |
| 전체 용어 출현 횟수 | 17,382 |
| 한 번만 나온 집계 행 | 2,940 |
| 한 번만 나온 비율 | 53.25% |

한 번만 나온 용어가 절반 이상이지만 이를 바로 노이즈로 볼 수는 없다. 한국사
기출에는 특정 사건·문헌·문화재처럼 실제로 드물게 출제되는 고유 용어가 많다.

### 4.2 category 분포

| category | 행 |
|---|---:|
| 인물 | 1,009 |
| 제도 | 941 |
| 기관 | 546 |
| 지명 | 507 |
| 문헌 | 451 |
| 유적 | 431 |
| 사건 | 371 |
| 문화재 | 364 |
| 단체 | 217 |
| 유물 | 214 |
| 사상 | 144 |
| 정책 | 141 |
| 국가 | 130 |
| 조약 | 40 |
| 왕조 | 9 |
| 운동 | 3 |
| 법령 | 2 |
| 관직 | 1 |

인물은 가장 크지만 전체의 18.3%에 불과하다. ITKC 인물·사건 관계만 적재하면
제도·기관·지명·문헌·유적·문화재 영역의 사실 경로가 비게 된다.

### 4.3 빈도 상위 용어

| 순위 | 용어 | 출현 |
|---:|---|---:|
| 1 | 고종 | 67 |
| 2 | 신라 | 65 |
| 3 | 고구려 | 55 |
| 4 | 고려 | 53 |
| 5 | 당 | 52 |
| 6 | 백제 | 47 |
| 7 | 청 | 45 |
| 8 | 개경 | 44 |
| 9 | 일본 | 40 |
| 10 | 강화도 | 38 |

`고종`, `당`, `청`, `원`, `왜`처럼 짧거나 동음이의 가능성이 큰 고빈도 용어는
문항 문맥 없이 이름만으로 canonical ID를 확정하면 위험하다.

---

## 5. 이름 기반 원천 커버리지

커버리지 분모는 노이즈 제거 후 정규화된 고유 이름 5,211개다.

| 상태 | 용어 | 분모 대비 |
|---|---:|---:|
| 시소러스 정확 일치 | 3,110 | 59.68% |
| 시소러스 부분 일치 | 1,157 | 22.20% |
| AKS 이름·이칭 보강 | 383 | 7.35% |
| 전체 커버 | 4,650 | 89.23% |
| 미커버 | 561 | 10.77% |
| 노이즈 제외 이름 | 26 | 분모 제외 |

설정 임계값은 90%이며 현재 결과는 0.77%p 부족하다.

이 수치는 이름 후보 회수율이다. 다음을 의미하지 않는다.

- 4,650개 용어의 canonical ID가 확정됐다는 뜻이 아니다.
- 부분 일치 후보가 동일 역사 대상이라는 뜻이 아니다.
- 561개 미커버 용어가 원천에 없다는 뜻이 아니다.

부분 일치에는 일반어·접사·짧은 문자열로 인한 오탐 가능성이 있다. 부분 일치는
candidate retrieval에만 사용하고 사실 관계 승인 근거로 사용하지 않는다.

---

## 6. Entity Resolution 후보 EDA

### 6.1 case 상태

| 상태 | case | 비율 |
|---|---:|---:|
| `AMBIGUOUS` | 5,412 | 98.36% |
| `UNRESOLVED` | 57 | 1.04% |
| `REJECTED` | 33 | 0.60% |
| canonical ID 확정 | 0 | 0% |

검토 사유:

| 사유 | case |
|---|---:|
| `CANDIDATE_VERIFICATION_REQUIRED` | 5,412 |
| `NO_SOURCE_CANDIDATE` | 57 |
| `EXTRACTION_NOISE` | 27 |
| `INVALID_EXTRACTION_CATEGORY` | 6 |

현재 SourceRecord 후보를 최종 CanonicalEntity로 간주해서는 안 된다.

### 6.2 후보 규모

| 지표 | 값 |
|---|---:|
| 전체 후보 행 | 48,171 |
| case당 후보 중앙값 | 8 |
| 75% 분위 | 12 |
| 90% 분위 | 16 |
| 95% 분위 | 18 |
| 최대 | 57 |

후보 수가 많으므로 이름 정확 일치만으로 자동 승인하면 동명이인과 동음이의어가
잘못 합쳐질 가능성이 높다.

### 6.3 원천별 후보

| 원천 | 후보 행 | 후보가 있는 case |
|---|---:|---:|
| AKS | 25,554 | 5,398 |
| 시소러스 | 20,869 | 5,299 |
| ITKC 인물 | 1,131 | 554 |
| ITKC 사건 | 617 | 448 |

ITKC 관계 endpoint에 연결될 가능성이 있는 case의 상한:

| 관계군 | case |
|---|---:|
| ITKC 인물 관계 | 531 |
| ITKC 사건–인물 관계 | 443 |

이는 후보 중 하나라도 관계 endpoint에 있다는 뜻이다. 해당 후보가 정답
canonical 대상이라는 뜻은 아니다.

### 6.4 제안 EntityType 분포

| EntityType | case |
|---|---:|
| Concept | 1,218 |
| Person | 1,008 |
| Heritage | 1,008 |
| Institution | 542 |
| Place | 507 |
| Work | 488 |
| Event | 369 |
| Organization | 217 |
| Polity | 139 |
| 미분류 | 6 |

`Heritage`에는 유적·문화재·유물 범주가 함께 들어간다. 후보 탐색에서 이들을
모두 같은 세부 분류로 취급하면 범위가 지나치게 넓어진다. 세부
`SemanticClass`가 반드시 필요하다.

---

## 7. 원천 관계 staging 결과

현재 원천 관계 전처리는 다음 파일을 만들었다.

| 출력 | 건수 |
|---|---:|
| SourceRecord 노드 | 128,312 |
| SourceRecord 관계 | 275,817 |
| 시소러스 분류 노드 | 515 |
| 용어–분류 관계 | 61,598 |
| 분류 계층 관계 | 498 |
| canonical 관계 | 0 |

중복 제거:

| 원천 | 제거 행 |
|---|---:|
| ITKC 인물 관계 | 257 |
| ITKC 사건–인물 관계 | 8,474 |

canonical 관계가 0인 이유는 관계가 없어서가 아니다. 최종
`neo4j_source_to_entity_relationships.csv`가 없어 SourceRecord endpoint를
CanonicalEntity로 투영할 수 없기 때문이다.

canonical 투영 대상이 될 수 있는 ITKC 관계는 213,425개지만, 현재 양쪽 endpoint가
모두 미해결이므로 canonical 사실 관계로 기록하지 않았다.

---

## 8. 사실 그래프 구성 요소별 준비 상태

| 구성 요소 | 상태 | 판단 |
|---|---|---|
| ExamTerm | 준비됨 | 노이즈 제외·정규화 표기 기준 5,211개를 원천 매칭과 무관하게 보존 |
| SourceRecord·release·원천 URL | 준비됨 | provenance staging에 사용 가능 |
| EntityName 후보 | 준비됨 | 승인 전이므로 production 검색 제외 |
| CanonicalEntity | 부분 검증 | 5개 용어 제한 실행에서 안전한 다원천 묶음 4건 자동 확정 |
| EntityType 후보 | 대부분 준비됨 | 6 case 미분류, 세부 유형 검토 필요 |
| SemanticClass 후보 | 부분 준비 | 시소러스 경로 변환 가능, canonical 연결 미완료 |
| Era 후보 | 대부분 준비됨 | AKS 원문 시대 표현의 표준화 필요 |
| Region·Polity·PersonRole | 부족 | 원천 필드와 본문에서 추가 추출 필요 |
| ITKC 인물 Fact 후보 | 준비됨 | 중복 제거 완료, canonical endpoint 미확정 |
| ITKC 사건–인물 Fact 후보 | 준비됨 | 중복 제거 완료, 관계 역할 세분화 부족 |
| AKS 기반 기타 Fact | 미구현 | 제도·기관·문화재·문헌·지역에 필요 |
| EvidenceSpan | 미구현 | URL만으로는 최종 Fact 근거가 부족 |
| RAG 후보 탐색 | 대기 | canonical 사실·분류 그래프 이후 단계 |

---

## 9. 주요 데이터 품질 문제

### 9.1 전체 canonical 확정 미완료

초기 staging에는 확정된 canonical이 0건이었다. 현재는 5개 용어 제한 실행으로
신라·고구려·고려·백제 4건을 자동 확정했다. 전체 5,412개 term LLM 실행은 아직
완료하지 않았으므로 전체 그래프 기준 확정률로 해석하면 안 된다.

현재 5,412개 case가 `AMBIGUOUS`다. 사실 그래프 적재 전에 반드시 해결해야 하는
최우선 문제다.

### 9.2 이름 커버리지와 identity 정확도의 혼동

89.23%는 후보를 찾은 비율이지 올바른 EID를 고른 비율이 아니다. 특히 부분
일치는 자동 승인 근거가 될 수 없다.

### 9.3 ITKC 사건 중복

사건 원본 행의 61.09%, 사건 관계 행의 55.05%가 ID·의미 기준 중복이다. 원본
행 단위 적재는 금지하고 ID·endpoint 단위로 합쳐야 한다.

### 9.4 시소러스 `topterm_id` 오해 가능성

`topterm_id`는 최상위 17개 분류를 가리킨다. 직속 부모로 사용하면
`SemanticClass` 계층이 한 단계로 납작해진다.

### 9.5 관계 원천의 유형 편향

ITKC 관계는 인물과 사건에 집중된다. 기출 용어의 다수를 차지하는 제도·기관·지명·
문헌·유산 영역은 AKS 본문 기반 Fact가 필요하다.

### 9.6 broad anchor 과연결

`Person`, `조선`, `정치`, `문화`처럼 degree가 큰 노드만 공유한 후보는 의미상
가깝다고 볼 수 없다. 부모 자격과 세부 근접도에 사용할 Anchor를 분리해야 한다.

### 9.7 근거 범위 부재

현재 원천 URL과 본문은 있지만 Fact별 정확한 `EvidenceSpan`은 없다. 최종
`VERIFIED` Fact에는 문서 ID와 근거 범위를 연결해야 한다.

---

## 10. 현재 데이터로 가능한 MVP

현재 데이터만으로 다음 범위의 MVP를 만들 수 있다.

1. 승인된 일부 canonical target을 기준으로 시작한다.
2. AKS 유형·시대와 시소러스 경로로 `SemanticClass`를 만든다.
3. ITKC 인물·사건 관계를 canonical endpoint가 모두 확정된 경우에만 Fact로 승격한다.
4. AKS 정의와 본문에서 부족한 관계 후보를 추출한다.
5. 근거가 확인된 Fact만 `EvidenceSpan`과 함께 적재한다.
6. RAG가 같은 부모·세부 분류·시대·역할 경로의 다른 대상을 조회한다.

전체 5,502 case를 한 번에 production Graph로 승격할 필요는 없다. 승인된
target과 충분한 Fact가 있는 범위부터 Graph release를 만들 수 있다.

---

## 11. 다음 작업 순서

### 1순위: CanonicalEntity 확정

- 문항 문맥으로 동명이인 구분
- 정확한 AKS EID 선택
- ITKC·시소러스 SourceRecord 병합 여부 확정
- 승인되지 않은 후보는 Graph 검색에서 제외

### 2순위: SemanticClass 정규화

- 시소러스 `term_lk` 누적 경로 변환
- 부모·세부 분류 구분
- AKS 유형·시대와 충돌 검사
- broad anchor와 후보 자격용 분류 분리

### 3순위: Fact와 EvidenceSpan 생성

- ITKC 직접 관계 변환
- AKS 본문 기반 부족 관계 보강
- endpoint 타입과 관계 방향 검사
- Fact별 근거 범위 저장

### 4순위: 사실 그래프 검증

- canonical endpoint 누락 0건
- 중복 Fact 0건
- 근거 없는 VERIFIED Fact 0건
- broad anchor 단독 후보 경로 차단

### 5순위: RAG 조회 계약 연결

- candidate canonical ID
- 실제 graph path
- hop count
- 공유 부모·세부 분류·시대·역할
- 후보 소유 Fact와 근거 ID

---

## 12. 근거 산출물

| 근거 | 경로 |
|---|---|
| 추출 용어 | `etl/preprocessing/neo4j/output/review/unique_exam_terms.csv` |
| 이름 커버리지 | `etl/preprocessing/neo4j/output/review/source_coverage_report.json` |
| ER case | `etl/preprocessing/neo4j/output/internal/entity_resolution/entity_cases.csv` |
| 원천 후보 | `etl/preprocessing/neo4j/output/internal/entity_resolution/candidate_source_records.csv` |
| 원천 관계 manifest | `etl/preprocessing/neo4j/output/source_relationships/source_relationship_manifest.json` |
| AKS 상세 원문 | `etl/raw_data/한국민족문화대백과사전/articles_detail.jsonl` |
| ITKC 관계 | `etl/raw_data/한국고전종합DB_관계망/` |
| 시소러스 | `etl/raw_data/교육부 국사편찬위원회_한국역사용어시소러스 정보_20211028 (1).csv` |

이 문서의 수치는 위 파일의 현재 상태를 다시 읽어 계산했다. 과거 EDA 문서의
수치와 다를 경우 이 문서를 현재 기준으로 사용한다.

---

## 13. 2026-07-27 최종 release 업데이트

초기 EDA의 `CanonicalEntity 0개`, `EvidenceSpan 미구현`, `RAG 후보 탐색 대기`
상태는 더 이상 현재 상태가 아니다.

| 항목 | 현재 수치 |
|---|---:|
| CanonicalEntity | 4,786 |
| ProvisionalEntity | 14,661 |
| GraphEntity | 19,447 |
| Fact assertion | 39,852 |
| 직접 의미 관계 | 39,745 |
| EvidenceSpan | 39,961 |
| 양 endpoint 해소 Fact | 623 |
| 미해소 endpoint 포함 Fact | 39,229 |

현재 차단점은 raw 관계 수가 아니라 endpoint identity 해소율이다. 미해소 관계는
보존하지만 이름 검색·기본 RAG·자동 다중 hop에서는 제외한다.

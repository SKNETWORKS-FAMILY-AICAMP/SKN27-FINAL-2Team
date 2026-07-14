# Neo4j 그래프 관계 정규화 점검 — 노드 속성에 들어간 관계 정보

점검일: 2026-07-14 / 대상: `storage/neo4j/schema/*.cypher`, `storage/neo4j/neo4j_import/` CSV,
`etl/preprocessing/neo4j/scripts/make_graph_csv.py`

## 원칙

노드 속성에는 **그 개체 자신의 사실**(이름, 한자, 연도, 원문 설명 등)만 둔다.
다른 개체를 가리키는 정보는 전부 **관계(엣지)**로 표현한다.
"A는 B의 아버지다"는 A의 속성이 아니라 `(A)<-[:HAS_FATHER]-(B)` 엣지여야 한다.

이유: 속성으로 저장된 참조는 ① 대상 개체와 어긋나도 감지할 수 없고(이름 문자열 매칭)
② 그래프 탐색(패턴 매칭·경로 질의)에 쓸 수 없으며 ③ 같은 사실이 두 곳에 존재해
갱신 시 불일치가 생긴다.

**위반이 아닌 것**: `Term.description` 같은 원문 텍스트 안에 "~의 아버지" 문장이
포함된 것은 위반이 아니다. 원문은 그 노드의 콘텐츠다. 문제는 **구조화된 관계 컬럼**이
노드 속성으로 들어간 경우다.

## 발견 사항 (심각도순)

### 1. 인물 관계가 단일 `RELATED_TO` 타입으로 뭉개짐 — 최우선

`relations/person_related_to_person.csv`에는 관계 유형이 정규화돼 있다:

| normalized_relation_type | 건수 |
|---|---|
| HAS_FATHER | 30,456 |
| HAS_CHILD | 30,456 |
| HAS_GRANDFATHER | 23,205 |
| HAS_SON_IN_LAW / HAS_FATHER_IN_LAW | 각 22,867 |
| SIBLING_OF | 18,455 |
| HAS_GREAT_GRANDFATHER | 18,198 |
| HAS_TEACHER / HAS_STUDENT | 4,031 / 4,030 |
| ASSOCIATED_WITH | 3,996 |
| LINEAGE_RELATED / HAS_BIOLOGICAL_FATHER | 각 2,089 |

그런데 `history_graph_import_relations.cypher`(250행 부근)는 이를 전부
`[:RELATED_TO]` 단일 타입으로 MERGE하고 유형을 엣지 **속성**으로만 넣는다
(`SET r += row`).

- 영향: "아버지 관계만 따라가기" 같은 질의가 타입 패턴 매칭이 아니라 속성 필터가 된다.
  관계 타입별 인덱싱·성능·가독성 등 그래프 DB의 핵심 이점을 잃는다.
- 데이터에 `direction_rule`, `is_symmetric`, `inverse_relation_type`이 이미 있으므로
  타입별 로드로 바꾸는 데 추가 전처리가 필요 없다.

### 2. `Person.father_name` — 관계의 노드 속성 중복

- `nodes/people.csv`의 `father_name`이 56,727명 중 **30,461명**에 채워져 있다.
- 같은 관계가 `HAS_FATHER` 엣지 30,456건으로 이미 존재한다 (건수 거의 일치 = 같은 원천).
- 이름 문자열이라 대상 인물 노드와의 정합을 보장할 수 없다 (동명이인·표기 차이).
- 처리: 노드 속성에서 제거. 표시 편의로 남겨야 한다면 "표시용 캐시이며 기준은
  엣지"임을 스키마 문서에 명시하고, 캐시 생성을 엣지에서 파생시킨다.

### 3. 엣지 자체가 없는 관계 속성 — 관계 누락

- `Event.related_event_name`: 이벤트 간 관계가 이름 문자열 속성으로만 존재.
  이벤트 간 엣지는 `PART_OF_EVENT_GROUP`뿐이고 직접 관계 CSV가 없다.
- `Event.start_reign_name` / `end_reign_name`: `Reign` 노드가 존재하는데
  이벤트→재위 엣지가 없다 (`DURING_REIGN`은 RoyalAction에만 있음).
  재위 문자열 속성으로만 존재.
- 처리: `make_graph_csv.py`에서 이름→ID 매칭으로 관계 CSV를 생성해야 한다.
  매칭 실패분은 속성으로 유지하되 `*_unmatched` 표시로 구분한다.

### 4. 엣지가 있는데 노드에도 중복된 표시용 속성 — 경미

| 노드 속성 | 대응 엣지 (존재함) |
|---|---|
| `RoyalAction.monarch_name` | `(Person)-[:ASSOCIATED_WITH_ACTION]->(RoyalAction)` |
| `RoyalAction.target_name`, `target_kind` | `(RoyalAction)-[:TARGETS]->(CanonicalEntity)` |
| `Term.category_text` | `(Term)-[:HAS_CATEGORY]->(CanonicalCategory)` |
| `Term.period_text` | `(Term)-[:IN_PERIOD]->(Period)` |

- 관계는 정상이므로 탐색은 깨지지 않는다. 다만 두 곳에 같은 사실이 있어
  갱신 불일치 위험이 있다.
- 처리: 제거가 원칙. 남기면 "표시용 캐시" 명시 (2번과 동일 규칙).

## 2차 전수 스캔 — 나머지 노드 26종

노드 CSV 전체(26종) 헤더를 관계성 컬럼 기준으로 점검한 결과.

### 추가 발견: 엣지 누락 (발견 3과 동일 유형)

- **`Period.parent_period_name`** — ✅ **반영 완료.** 29개 중 21개 채워짐, 기간 이름이
  유일해 이름 매칭으로 전건 해석 가능(매칭 실패 0건). 반영 내역:
  - `make_graph_csv.py`에 `build_period_subperiod_of()` 추가 + 관계 출력 등록
  - `relations/period_subperiod_of.csv` 생성 (21건, 예: 구석기시대→선사시대,
    삼국시대→고대, 고려전기→고려시대)
  - `history_graph_import_relations.cypher`에 `[:SUBPERIOD_OF]` 블록 추가
  - `history_graph_verify.cypher`에 검증 3종 추가: 속성 있는데 엣지 없음(0 기대),
    엣지-속성 정합(0 기대), 계층 순환(0 기대)
  - `parent_period_name` 속성은 발견 4(중복 표시용)로 강등 — 정리 대상

### 정정: `Term.topterm_id`는 엣지 누락이 아님 (검증 완료)

처음에는 term→term 계층 참조로 의심했으나 원천 데이터 검증 결과 아니다.

- 원천(한국역사용어시소러스)에서 `topterm_id`는 **17개 값**뿐이며, 각 그룹의
  용어들이 `term_lk` 루트 분류와 **17/17 그룹 모두 100% 일치**한다
  (8=정치·행정·법제 9,334개, 662=인명 8,874개, 665=문화재 5,181개 등).
- 즉 `topterm_id`는 계층 관계가 아니라 **원천의 최상위 대분류 코드**이고,
  같은 정보가 이미 `HAS_CATEGORY` → 카테고리 계층(`root_category_name`)으로
  그래프에 표현돼 있다. kind=1 상위 표제어(794개)의 term_id와는 매칭 0건 —
  별개 네임스페이스다.
- 분류: **중복 표시용 속성** (발견 4 유형). 제거하거나 원천 분류 코드 메타로
  유지한다. 검증 활용은 가능: `topterm_id` ↔ `HAS_CATEGORY` 루트가 어긋난
  Term은 분류 매핑 오류 후보다.

### 추가 발견: 엣지가 있는 중복 표시용 속성 (발견 4와 동일 유형)

| 노드 속성 | 대응 엣지 (존재함) |
|---|---|
| `CanonicalCategory.parent_category_id`, `parent_category_path`, `root_category_name` | `SUBCATEGORY_OF` |
| `Region.parent_region_id`, `parent_region_name` | `SUBREGION_OF` |
| `Country`/`EconomicDomain`/`Region`/`TaxonomyFacet`의 `canonical_category_id`, `canonical_category_path` | `(CanonicalCategory)-[:ABOUT_*]->()` |
| `CanonicalEntity.entity_type`, `entity_subtype` | `HAS_ENTITY_TYPE` |
| `SearchTag.source_node_type`, `source_node_id` | `HAS_SEARCH_TAG` (역참조 메타) |

### 허용으로 분류 (원칙의 예외 — 명문화 대상)

- **출처·근거 메타**: `anchor_source_id`, `anchor_source_eid`, `evidence_id`,
  `evidence_source_record_id`, `source`, `source_record_id` — 데이터 리니지 추적용.
  관계 아님(적재 계보)이므로 노드 속성 허용.
- **통계 캐시**: `term_count`, `event_count`, `direct_term_count`, `related_count` 등 —
  재계산 가능한 파생 집계. 허용하되 "캐시이며 기준은 그래프"임을 인지.
- **원문 텍스트**: `description`, `remark`, `year_text`, `era_text`,
  `reign_period_text` 등 — 노드 자신의 콘텐츠.
- 확인 필요 1건: `SourceImage.related_content` — 내용 성격(관계 참조인지 원문인지) 확인 후 분류.

### 이상 없음

`entity_types`, `eras`, `themes`, `event_groups`, `event_facets`,
`source_event_categories`, `source_urls`, `source_texts`, `source_articles` —
자기 사실·원문·통계 캐시·리니지 메타만 보유.

## 근본 원인

`history_graph_import_nodes.cypher`가 모든 노드를 `SET n += row`로 적재해
**CSV의 전 컬럼이 무조건 노드 속성이 된다.** CSV 생성 단계에서 관계성 컬럼을
걷어내지 않으면 그대로 노드에 유입되는 구조다.

## 수정 계획 (제안)

1. **관계 타입 분리 로드** — ✅ **반영 완료** (`history_graph_import_relations.cypher`).
   APOC 미사용 환경이라 같은 CSV를 유형별 WHERE 필터로 16회 로드하는 방식을 썼다.
   목록 밖 유형은 catch-all 블록이 `RELATED_TO`로 적재해 유실을 막는다
   (검증에서 RELATED_TO 건수 > 0이면 새 유형 블록 추가 신호).
   `history_graph_verify.cypher`에 검증 5종 추가: 유형별 분포, catch-all 잔존,
   타입-속성 불일치, relation_id 중복, father_name 캐시 정합.

   **재적재 주의**: 이 변경은 재적재 시에만 적용된다. 이미 `RELATED_TO`로 적재된
   기존 DB에 그대로 다시 돌리면 옛 `RELATED_TO` 엣지가 남은 채 타입 엣지가
   추가로 생긴다(MERGE 키가 타입별로 분리되므로). 재적재 전에
   `MATCH (:Person)-[r:RELATED_TO]->(:Person) DELETE r`로 기존 인물 관계를
   지우거나 그래프를 전체 재구축한다.
2. **누락 엣지 생성** — `related_event_name`, `start/end_reign_name`의
   이름→ID 매칭 파이프라인을 `make_graph_csv.py`에 추가하고 관계 CSV 2종 신설.
3. **중복 속성 정리** — `father_name`, `monarch_name`, `target_name`,
   `category_text`, `period_text`를 노드 적재에서 제외하거나 표시용 캐시로 명시.
   노드 CSV 생성부에서 관계성 컬럼 drop 목록을 상수 한 곳으로 관리.
4. **원칙 명문화** — `docs/neo4j/neo4j_설계_근거.md`에 "노드 속성 = 자기 사실만,
   타 개체 참조 = 엣지" 규칙과 표시용 캐시 예외 조건을 추가.

## 반영 후 검증 쿼리

```cypher
// 1. 관계 타입 분포 — RELATED_TO가 남아 있으면 미완
MATCH ()-[r]->() WHERE type(r) = 'RELATED_TO' RETURN count(r);

// 2. HAS_FATHER 타입 존재 확인
MATCH ()-[r:HAS_FATHER]->() RETURN count(r);  // 기대: 약 30,456

// 3. father_name 속성 잔존 확인 (제거 방침 시 0이어야 함)
MATCH (p:Person) WHERE p.father_name IS NOT NULL RETURN count(p);

// 4. 이벤트→재위 엣지 존재 확인 (누락 엣지 생성 후)
MATCH (:Event)-[r:DURING_REIGN]->(:Reign) RETURN count(r);

// 5. 속성-엣지 정합 표본 검사 (캐시 유지 방침 시)
MATCH (c:Person)-[:HAS_FATHER]->(f:Person)
WHERE c.father_name IS NOT NULL AND c.father_name <> f.name
RETURN count(c);  // 기대: 0 (불일치 발견 시 캐시가 어긋난 것)
```

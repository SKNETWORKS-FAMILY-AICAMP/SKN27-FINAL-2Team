# Neo4j 그래프 관계 정규화 점검 — 노드 속성에 들어간 관계 정보

> 문서 상태: `AUDIT-WIP`
> 점검일: 2026-07-14
> 코드 기준: commit `44fd60e` + working tree
> 대상: `storage/neo4j/schema/*.cypher`, `storage/neo4j/neo4j_import/` CSV,
> `etl/preprocessing/neo4j/scripts/*.py`
> 상태 기준: `SOURCE`(코드) / `GENERATED`(생성 CSV) / `LIVE`(Neo4j 적재)를 구분한다.
> 범위 주의: 이 문서는 현재 구현 감사다. 문제 생성 목표 스키마인 `SemanticClass`,
> `Fact`, `QuestionFacet`, `QuestionUse`가 현재 구현됐다는 뜻이 아니다. 목표 계약은
> [README.md](./README.md)를 따른다.

현재 `SOURCE`에는 이 문서의 신규 관계와 안전한 최종 import 후보 승격이 반영돼 있다.
최신 계약으로 node CSV 26개, relationship CSV 55개, `SourceUrl` 57,239건을 생성했고,
preload 114개와 golden 21개를 합친 QA 135/135가 통과했다. relation CSV 전체의 Cypher
`MERGE` identity 중복은 0건이다. 생성 완료 시각과 final import 승격 여부는 고정 문구를
복사하지 않고 `storage/neo4j/neo4j_import/.preprocessing_complete.json`을 단일 진실원으로
확인한다. `LIVE`에는 이 변경을 적재하지 않았고 기존 상태를 유지한다.

파생 `ABOUT_*` 관계가 여러 category mapping 근거를 합칠 때는
`canonical_category_id`, `canonical_category_path`, `match_type` 세 열을 동일 tuple 순서로
pipe 집계한다. preload QA는 세 열의 길이 일치를 확인하고, 각 Term/Event의
`HAS_CATEGORY`와 `CanonicalCategory-[:ABOUT_*]` 매핑을 조합해 source-target별 기대 tuple
set을 만든 뒤 실제 집계 set과 정확히 일치하는지 검증한다. 누락·초과 허용치는 0이다.

## 원칙

노드 속성에는 **그 개체 자신의 사실**(이름, 한자, 연도, 원문 설명 등)만 둔다.
다른 개체를 가리키는 단순 참조는 **typed 관계(엣지)**로 표현한다. 시기·지역·역할·단계와
원문 근거가 함께 필요한 복합 역사 명제는 목표 모델의 `Fact`로 표현한다.
"A는 B의 아버지다"는 A의 속성이 아니라 `(A)<-[:HAS_FATHER]-(B)` 엣지여야 한다.

이유: 속성으로 저장된 참조는 ① 대상 개체와 어긋나도 감지할 수 없고(이름 문자열 매칭)
② 그래프 탐색(패턴 매칭·경로 질의)에 쓸 수 없으며 ③ 같은 사실이 두 곳에 존재해
갱신 시 불일치가 생긴다.

**위반이 아닌 것**: `Term.description` 같은 원문 텍스트 안에 "~의 아버지" 문장이
포함된 것은 위반이 아니다. 원문은 그 노드의 콘텐츠다. 문제는 **구조화된 관계 컬럼**이
노드 속성으로 들어간 경우다.

## 발견 사항 (심각도순)

### 1. 인물 관계가 단일 `RELATED_TO` 타입으로 뭉개짐 — SOURCE 수정 완료, LIVE 미적용

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

발견 당시 `history_graph_import_relations.cypher`는 이를 전부 `[:RELATED_TO]` 단일
타입으로 MERGE하고 유형을 엣지 **속성**으로만 넣었다. 현재 SOURCE는
`relation_type_seed.csv.neo4j_rel_type`을 단일 기준으로 삼아 CSV의 `relation_type`을
만들고, Neo4j 5.26의 dynamic relationship type 문법으로 한 번에 typed load한다.
등록되지 않은 원천 유형만 `RELATED_TO` fallback으로 보존하며 QA 기대값은 0이다.

- 기존 영향: "아버지 관계만 따라가기" 같은 질의가 타입 패턴 매칭이 아니라 속성 필터가 된다.
  관계 타입별 인덱싱·성능·가독성 등 그래프 DB의 핵심 이점을 잃는다.
- 방향·역관계·대칭 규칙은 seed에서 생성된 `relation_type_dictionary.csv`가 소유한다.
  Cypher에 관계 타입 목록을 중복 하드코딩하지 않는다.
- 대칭 관계는 endpoint를 canonical 순서로 정렬해 한 관계로 합치고, 양방향 원천 행에
  서로 다른 evidence URL이 있으면 모두 정렬·중복 제거해 보존한다.
- 원천 대상 인물의 crawler 집계였던 `related_count`는 관계 강도가 아니므로 관계 CSV에서
  제거했다. `Person.core_relation_degree`는 최종 Person↔Person 관계와 `INVOLVED_IN`의
  incident edge 수를 합산한 파생값이다.
- 최신 GENERATED의 인물 관계는 184,044건이며 관련 계약 QA를 통과했다.
- LIVE에는 여전히 `RELATED_TO` 184,044건만 있으며 typed 인물 관계는 0건이다.

### 2. `Person.father_name` — 관계의 노드 속성 중복

- 직전 완전 생성 스냅샷의 `nodes/people.csv`에서 `father_name`이 56,727명 중
  **30,461명**에 채워져 있었다. 실행별 수치는 run manifest에서 다시 확인한다.
- 같은 관계가 `HAS_FATHER` 엣지 30,456건으로 이미 존재한다 (건수 거의 일치 = 같은 원천).
- 이름 문자열이라 대상 인물 노드와의 정합을 보장할 수 없다 (동명이인·표기 차이).
- 처리: **제거로 확정, SOURCE 반영 완료** — "중복 속성 제거 확정 내역" 참조.
  verify의 `removed_father_name_residue_count`(0 기대)로 재적재 후 잔존을 감지한다.

### 3. 엣지 자체가 없는 관계 속성 — 관계 누락

- `Event.related_event_name` — ✅ **SOURCE·GENERATED 검증 완료, LIVE 적용 전.** 기존의
  Event별 `Event-[:HAS_RELATED_EVENT]->Term` 140건은 같은 집단명을 Event마다 복제하므로
  폐기했다. 224개 Event는 기존 `PART_OF_EVENT_GROUP`으로 32개 EventGroup을 공유하고,
  그중 Term 이름과 exact unique 일치하는 EventGroup만
  `(EventGroup)-[:HAS_TERM_CANDIDATE]->(Term)`으로 연결한다. 최신 GENERATED는
  18건이며 `match_method=UNIQUE_TERM_NAME`, `review_status=AUTO_CANDIDATE`,
  `answer_eligible=N`을 기록한다. 승인 전에는 정답 근거나 canonical 사실로 사용하지 않는다.
- `Event.start_reign_name` / `end_reign_name` — ✅ **SOURCE·최신 GENERATED 반영, LIVE 미적용.** 왕호+연도 매칭으로
  started 444건(왕호 유일 272 + 연도 해소 172), ended 445건(왕호 유일 274 + 연도
  해소 171)을 만들었다. 사건 날짜의 왕호와 연도가 실제 재위 범위를 벗어난 1개 Event는
  억지 연결하지 않고 `event_reign_mapping_review.csv`에 시작·종료 각 1행, 총 2행을
  `YEAR_OUT_OF_RANGE`로 보존한다.
  `make_aks_reign_graph_csv.py`에 매칭 빌더 추가(재위 생성 이후 단계라 이 스크립트 소관),
  `event_started_during_reign.csv`·`event_ended_during_reign.csv` 생성,
  `[:STARTED_DURING_REIGN]`·`[:ENDED_DURING_REIGN]` 적재 블록과 검증 4종
  (건수·왕호 정합·연도 범위·미매칭) 추가. `match_method` 속성으로 매칭 근거 보존.
- 공통 원칙: 매칭 실패분은 관계를 만들지 않는다. 원천 문자열은 source/staging 또는
  검수 큐에 보존하고 canonical 사실처럼 사용하지 않는다(억지 연결 금지).

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

- **`Period.parent_period_name`** — ✅ **SOURCE·직전 GENERATED 반영, LIVE 미적용.** 29개 중 21개 채워짐, 기간 이름이
  유일해 이름 매칭으로 전건 해석 가능(매칭 실패 0건). 반영 내역:
  - `make_graph_csv.py`에 `build_period_subperiod_of()` 추가 + 관계 출력 등록
  - `relations/period_subperiod_of.csv` 생성 (21건, 예: 구석기시대→선사시대,
    삼국시대→고대, 고려전기→고려시대)
  - `history_graph_import_relations.cypher`에 `[:SUBPERIOD_OF]` 블록 추가
  - `history_graph_verify.cypher`에 검증 3종: 엣지 건수(21 기대), 계층 순환(0 기대),
    `removed_parent_period_name_residue_count` 잔존 검사(0 기대) — 속성 제거 확정에
    맞춰 정합 검사에서 잔존 검사로 교체됨
  - `parent_period_name` 속성은 제거 확정 (아래 "중복 속성 제거 확정 내역" 포함)

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
| `SearchTag.source_node_type`, `source_node_id` | `HAS_SEARCH_TAG` (역참조 메타) |

`CanonicalEntity.entity_type`, `entity_subtype`은 현재 `HAS_ENTITY_TYPE`의 중복이 아니다.
현행 `HAS_ENTITY_TYPE`은 `Term → EntityType`에만 존재한다. 두 컬럼은 CanonicalEntity
자체 분류 속성이며, 목표 온톨로지에서 `SemanticClass` 또는 승인된 분류 관계로 승격할지
별도로 결정한다.

### 허용으로 분류 (원칙의 예외 — 명문화 대상)

- **출처·근거 메타**: `anchor_source_id`, `anchor_source_eid`, `evidence_id`,
  `evidence_source_record_id`, `source`, `source_record_id` — 데이터 리니지 추적용.
  관계 아님(적재 계보)이므로 노드 속성 허용.
- **통계 캐시**: `term_count`, `event_count`, `direct_term_count` 같은 노드 자체의 파생
  집계는 허용하되 "캐시이며 기준은 그래프"임을 인지한다. 인물 관계의 기존
  `related_count`는 해당 edge의 강도가 아니므로 허용 목록에서 제외하고 제거했다.
- **원문 텍스트**: `description`, `remark`, `year_text`, `era_text`,
  `reign_period_text` 등 — 노드 자신의 콘텐츠.
- **`SourceImage.related_content` 판정 완료**: 이미지 자체의 내용이 아니라 다른 콘텐츠의
  제목·콘텐츠군·URL을 담은 구조화 참조다. 노드 속성에서 제거하고, 파싱된 427개 URL을
  `SourceUrl` 사전에 합친 뒤 1,720개 `HAS_RELATED_CONTENT` 관계로 표현한다. 이 관계는
  이미지가 역사 대상을 묘사한다는 `DEPICTS`와 완전히 별개다. 최신 GENERATED에서 고유
  URL 427개와 관계 1,720건을 확인했고 계약 QA를 통과했다.

### 이상 없음

`entity_types`, `eras`, `themes`, `event_groups`, `event_facets`,
`source_event_categories`, `source_urls`, `source_texts`, `source_articles` —
자기 사실·원문·통계 캐시·리니지 메타만 보유.

## 근본 원인

`history_graph_import_nodes.cypher`가 모든 노드를 `SET n += row`로 적재해
**CSV의 전 컬럼이 무조건 노드 속성이 된다.** CSV 생성 단계에서 관계성 컬럼을
걷어내지 않으면 그대로 노드에 유입되는 구조다.

## 수정 상태와 남은 작업

| 작업 | SOURCE | GENERATED | LIVE | 다음 조치 |
|---|---|---|---|---|
| seed 기반 인물 typed load + `RELATED_TO` fallback | 완료 | 184,044건·QA 통과 | 미적용 | 별도 LIVE 적용 판단 |
| `Period-[:SUBPERIOD_OF]->Period` | 완료 | 21건 확인 | 미적용 | 재적재 후 정합·순환 검증 |
| `EventGroup-[:HAS_TERM_CANDIDATE]->Term` | 완료 | 18건·QA 통과 | 미적용 | 별도 LIVE 적용 판단 |
| `Event-[:STARTED_DURING_REIGN]->Reign` | 완료 | 444건·범위 밖 검수 1행 | 미적용 | 재적재 후 왕호·연도 범위 검증 |
| `Event-[:ENDED_DURING_REIGN]->Reign` | 완료 | 445건·범위 밖 검수 1행 | 미적용 | 재적재 후 왕호·연도 범위 검증 |
| 관계성 중복 속성 정리 (**제거로 확정**) | 완료 | CSV 제거·QA 통과 | 기존 속성 유지 | LIVE 적용 후 verify의 `removed_*_residue_count` 11종이 0인지 확인 |
| `SourceImage.related_content` → `HAS_RELATED_CONTENT` | 완료 | 고유 URL 427개·관계 1,720건·QA 통과 | 미적용 | 별도 LIVE 적용 판단 |
| 실패 안전 전처리와 promotion | 완료 | manifest 생성·final 승격 완료 | 해당 없음 | LIVE 적재와 계속 분리 |
| 정규화 원칙 문서화 | 완료 | 해당 없음 | 해당 없음 | 문제 생성 목표 계약은 [README.md](./README.md)와 함께 유지 |

### 중복 속성 제거 확정 내역 (SOURCE 반영 완료)

빌더에서 제거된 컬럼 — 다음 전체 재생성부터 노드 CSV에 포함되지 않는다:

- `Person.father_name` (make_graph_csv)
- `Person.degree` (원천 행 기반 모호한 값이며 최종 관계 기반 `core_relation_degree`로 대체)
- `RoyalAction.monarch_name`, `target_name`, `target_kind` (make_aks_royal_action_csv —
  target_kind는 `TARGETS` 엣지 속성으로 보존됨)
- `CanonicalCategory.parent_category_id`, `parent_category_path`, `root_category_name`
- `Region.parent_region_id`, `parent_region_name`, `canonical_category_id`, `canonical_category_path`
- `Country`/`EconomicDomain`의 `canonical_category_id`, `canonical_category_path`
- `TaxonomyFacet.canonical_category_id` (`root_category_name`·`taxonomy_facet_path`는
  facet 자신의 경로 정보라 유지)
- `Term.topterm_id` (import_nodes.cypher의 `toIntegerOrNull` 캐스팅 라인도 제거)
- `Period.parent_period_name`
- `SourceImage.related_content` (`HAS_RELATED_CONTENT` 관계의 원천 staging으로만 사용)

**제거하지 않고 유지하는 예외** (사유 명시):

- `Term.category_text`, `Term.period_text`, `Event.period_text`,
  `Event.related_event_name` — **chatbot `graph_service.py`가 검색 스코어링·결과
  구성에 실사용 중.** 제거하면 챗봇 그래프 검색이 깨진다. 표시용 캐시로 지정하며,
  제거하려면 chatbot 질의를 엣지 기반으로 교체하는 협조가 선행돼야 한다.
- `Event.start_reign_name`, `end_reign_name` — 이벤트-재위 매칭 파이프라인
  (`make_aks_reign_graph_csv`)의 입력이자 verify 정합 검사의 기준. 유지.
- `CanonicalEntity.entity_type`, `entity_subtype` — 자체 분류 속성 (위 정정 항목 참조).
- `SearchTag.source_node_type`, `source_node_id` — 태그 생성 계보 메타. 판단 보류로
  이번 제거에서 제외.

verify에 잔존 검사 11종(`removed_*_residue_count`)을 두어 재생성·재적재 후
옛 속성이 남아 있으면 감지되게 했다.

현재 Neo4j 5.26은 dynamic relationship type을 지원하므로 인물 관계 CSV를 유형별로
16회 재읽지 않는다. `MERGE (start)-[r:$(row.relation_type)]->(target)` 형태로 한 번 읽고,
CSV의 `relation_type`은 검수 seed의 `neo4j_rel_type`에서만 생성한다. 미등록 원천 유형은
`RELATED_TO`로 유실 없이 보존하되, 검증에서 1건 이상이면 실패 신호로 취급한다.

관계 유형 사전의 소유 구조: `seed/relation_type_seed.csv`(사람이 관리하는 규칙 원천)
→ `make_base_dictionaries.py` → `dictionary/relation_type_dictionary.csv`(생성물).
typed load 전환에 맞춰 seed의 `neo4j_rel_type`을 구정책(`RELATED_TO` 고정)에서
`normalized_relation_type`과 동일 값으로 갱신했다 (16행). 미등록 raw 유형의 기본값
(`build_missing_relation_type_defaults`)은 `RELATED_TO` 유지 — catch-all 정책과 일치한다.
seed·생성 사전·관계 CSV 타입이 어긋나면 QA 실패로 취급한다.

대칭 관계의 endpoint를 정렬할 때 한쪽 행을 단순 삭제하지 않는다. 같은 canonical pair의
양방향 evidence URL을 합친 뒤 정렬·중복 제거한다. 또한 `person_related_to_person.csv`의
`related_count` 컬럼 부재와 모든 Person의 `core_relation_degree` 재계산 결과를 pre-load
계약 QA에서 확인한다.

전처리 runner의 원자적 보호 범위는 최종 `nodes/relations`뿐이다. 이 파일들을
`.neo4j_import.building`에 만들고 선언 artifact, endpoint·고유키·기대 건수, Cypher
`MERGE` identity, 인물·이미지 정규화 규칙과 golden case가 통과한 경우에만 completion
manifest를 기록해 최종 import로 승격한다. `normalized`, `dictionary`, `mapping`,
`staging`은 기존 위치에서 삭제·재생성되는 비원자적 중간 산출물이므로 실패 시 부분 상태가
남을 수 있다. 이 promotion은 LIVE 적재가 아니다.

승격 실패 전에 완료 marker까지 만들어진 후보는 삭제하지 않는다. Windows bind mount가
rename을 막으면 Neo4j 컨테이너를 중지한 뒤 runner를 `--promote-existing`으로 실행해
재생성 없이 후보를 다시 검증·승격한다. 최신 전체 실행에서는 preload 계약 114개와 golden
case 21개가 모두 통과해 135/135 PASS했고, relation `MERGE` identity 중복 0건을 확인한 뒤
`.preprocessing_complete.json` 생성과 final 승격을 완료했다.

**재적재 주의**: typed 관계 변경은 재적재 시에만 적용된다. 기존 DB에 그대로 import하면
옛 `RELATED_TO`와 typed edge가 함께 남을 수 있다. 재적재 전에 인물 간 기존
`RELATED_TO`만 범위를 한정해 제거하거나, 검증된 전체 그래프 재구축 절차를 사용한다.
실행 전에는 반드시 백업·대상 DB·삭제 범위를 확인한다.

**서비스 호환 차단 조건**: `app/chatbot/graph_service.py`의 현행 Person 조회는
`[:RELATED_TO]` 및 `[:RELATED_TO*1..2]`에 고정되어 있다. 최신 typed 관계를 LIVE에
적재하기 전에 서비스 담당자가 `person_relation_id` 기반의 1~2홉 bounded query로
전환하고 회귀 검증해야 한다. `graph_service`는 이 작업 범위가 아니므로 수정하지 않는다.

## 반영 후 검증 쿼리

```cypher
// 1. 인물 관계 타입 분포 — RELATED_TO 기대 0
MATCH (:Person)-[r]->(:Person)
RETURN type(r) AS relation_type, count(r) AS relation_count
ORDER BY relation_count DESC;

// 2. HAS_FATHER 타입 존재 확인
MATCH ()-[r:HAS_FATHER]->() RETURN count(r);  // 기대: 약 30,456

// 3. father_name 속성 잔존 확인 (제거 방침 시 0이어야 함)
MATCH (p:Person) WHERE p.father_name IS NOT NULL RETURN count(p);

// 4. 이벤트→재위 시작/종료 엣지 존재 확인
MATCH (:Event)-[r:STARTED_DURING_REIGN|ENDED_DURING_REIGN]->(:Reign)
RETURN type(r) AS relation_type, count(r) AS relation_count;

// 5. 제거된 중복 속성 잔존 검사 (제거 확정 — 전부 0이어야 함)
//    전체 목록은 history_graph_verify.cypher의 removed_*_residue_count 11종 참조
MATCH (p:Person) WHERE p.father_name IS NOT NULL RETURN count(p);
MATCH (t:Term) WHERE t.topterm_id IS NOT NULL RETURN count(t);

// 6. 관련 사건명은 EventGroup→Term 비정답 후보 링크임을 확인
MATCH (g:EventGroup)-[r:HAS_TERM_CANDIDATE]->(t:Term)
RETURN count(r) AS candidate_link_count,
       count(CASE WHEN r.review_status = 'AUTO_CANDIDATE'
                       AND r.answer_eligible = 'N' THEN 1 END) AS safe_candidate_count;

// 7. 기간 계층과 순환 여부
MATCH (:Period)-[r:SUBPERIOD_OF]->(:Period) RETURN count(r);
MATCH path=(p:Period)-[:SUBPERIOD_OF*1..]->(p) RETURN count(path);

// 8. 관련 콘텐츠와 묘사 관계는 서로 다른 의미로 적재됨
MATCH (:SourceImage)-[r:HAS_RELATED_CONTENT]->(:SourceUrl) RETURN count(r);
MATCH (:SourceImage)-[r:DEPICTS]->(:CanonicalEntity) RETURN count(r);

// 9. Person 파생 degree가 최종 incident edge 수와 일치함
MATCH (p:Person)
OPTIONAL MATCH (p)-[personRel]-(other:Person)
WITH p, count(personRel) AS person_degree
OPTIONAL MATCH (p)-[eventRel:INVOLVED_IN]-(:Event)
WITH p, person_degree + count(eventRel) AS expected_degree
WHERE coalesce(p.core_relation_degree, 0) <> expected_degree
RETURN count(p);  // 기대: 0
```

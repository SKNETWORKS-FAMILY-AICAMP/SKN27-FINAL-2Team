# Neo4j 문서 안내와 현재 상태

> 문서 상태: `CURRENT-INDEX`
> 확인 시각: 2026-07-14 18:14:27 KST
> 코드 기준: commit `44fd60e` + 아래에 명시한 working tree 변경
> 범위: `etl/preprocessing/neo4j`, `storage/neo4j`, `docs/neo4j`

이 문서는 Neo4j 관련 문서를 읽는 순서와 **코드·생성 CSV·라이브 DB의 서로 다른 상태**를 구분하는 기준점이다. 수치나 구현 여부가 문서마다 다르면 이 문서의 상태 구분을 먼저 적용한다.

## 1. 먼저 구분할 세 상태

| 상태 | 의미 | 2026-07-14 확인 결과 |
|---|---|---|
| `SOURCE` | Python·Cypher에 구현된 내용 | seed 기반 typed 인물 관계, EventGroup→Term 후보, 이미지 관련 콘텐츠 URL 관계, 안전한 후보 디렉터리 승격 반영 |
| `GENERATED` | `storage/neo4j/neo4j_import`에 한 번의 정상 실행으로 생성된 CSV | 최신 SOURCE 계약으로 node 26종, relationship 55종, `SourceUrl` 57,239건 생성. preload 114개+golden 21개=135/135 PASS, manifest 생성·final 승격 완료 |
| `LIVE` | 현재 실행 중인 Neo4j에 실제 적재된 내용 | 최신 관계 미적용. 물리 노드 505,121, 관계 1,308,298, 라벨 27종, 존재 관계 타입 32종 |

`SOURCE 반영`은 `LIVE 적용`을 뜻하지 않는다. 현재 라이브 DB에는 기존 `RELATED_TO` 인물 관계 184,044건이 남아 있고, 아래 신규 관계는 0건이다.

- typed 인물 관계 16종
- `SUBPERIOD_OF`
- `HAS_TERM_CANDIDATE`
- `HAS_RELATED_CONTENT`
- `STARTED_DURING_REIGN`
- `ENDED_DURING_REIGN`

관계 CSV 정상 생성과 계약 QA·golden case 통과까지 완료했다. 다음 단계인 LIVE 적재는
별도 절차와 승인이 필요하다. 전처리 성공은 LIVE 적용을 뜻하지 않으며, 현재 LIVE는 이
변경 전 상태로 유지한다.

LIVE 적재 전 서비스 조회 호환성도 먼저 해결해야 한다. 현재
`app/chatbot/graph_service.py`의 Person 조회는 `[:RELATED_TO]`와
`[:RELATED_TO*1..2]`만 탐색하므로 typed 인물 관계를 적재하면 관련 인물 결과가 비게
된다. 해당 앱은 이 작업의 수정 범위가 아니므로 직접 변경하지 않았다. 서비스 담당자는
1홉에서는 `person_relation_id`가 있는 Person 관계를, 2홉에서는 경로의 모든 관계가 해당
속성을 갖는지 확인하는 bounded query로 전환해야 한다. 이 호환 변경과 회귀 검증 전에는
현재 LIVE 전체 재구축을 진행하지 않는다.

## 2. 현재 확정된 설계 기준

1. `Term`은 한국역사용어시소러스의 **원천 레코드**이며 독립 보존한다.
2. `term_lk`는 leaf `CanonicalCategory` 연결과 `SUBCATEGORY_OF` 계층으로 표현한다.
3. `topterm_id`는 Term 간 계층 ID가 아니라 17개 원천 최상위 분류 코드다. 정규화 원천에는
   남지만 최신 `Term` node CSV에서는 제거한다. `HAS_CATEGORY` 루트 정합 검증에 활용할 수
   있으나, 현행 QA에는 그 검사가 구현되어 있지 않다.
4. 노드에는 그 개체 자신의 정보만 둔다. 다른 개체 참조는 typed edge, 시기·역할·근거가 필요한 명제는 `Fact`로 표현한다.
5. `REFERS_TO`는 동일 실체를 가리키는 강한 연결이고 `MENTIONS_PERSON`은 설명문 언급인 약한 연결이다.
6. 인물 관계 CSV의 `relation_type`은 `relation_type_seed.csv.neo4j_rel_type`을 단일
   기준으로 삼고 Neo4j 5.26 dynamic typed relationship load로 적재한다. 미등록 값만
   `RELATED_TO` fallback이며 QA 기대값은 0이다.
7. 대칭 인물 관계는 정규화된 한 방향으로 합치되 양방향 원천의 서로 다른 evidence URL을
   모두 보존한다. 관계와 무관한 `related_count`는 제거하고 `Person.core_relation_degree`는
   최종 Person↔Person 관계와 `INVOLVED_IN`의 incident edge 수로 계산한다.
8. `Event-[:HAS_RELATED_EVENT]->Term`은 폐기한다. 관련 사건명은
   `Event-[:PART_OF_EVENT_GROUP]->EventGroup-[:HAS_TERM_CANDIDATE]->Term`으로 표현하며,
   exact unique 이름 일치 후보만 `AUTO_CANDIDATE`, `answer_eligible=N`으로 저장한다.
9. `SourceImage.related_content`는 노드 속성이 아니다. 파싱한 427개 URL을 `SourceUrl`에
   통합하고 `HAS_RELATED_CONTENT`로 연결하며, 이미지가 대상을 묘사한다는 `DEPICTS`와
   의미를 분리한다.
10. 문화유산 실물, 비문·문헌 내용, 사진·그림·탁본은 서로 다른 역할의 노드로 분리한다.
11. 문제 출제 계약은 9개 `TopicType`과 54개 `QuestionFacet`을 유지한다.

## 3. 문서별 역할과 읽는 순서

| 순서 | 문서 | 상태 | 역할 |
|---:|---|---|---|
| 1 | [neo4j_지식그래프_재설계안.md](./neo4j_지식그래프_재설계안.md) | `TARGET-DRAFT` | 한국사 문제 생성용 목표 구조, 9/54 계약, Fact·Evidence·LLM·파일럿 설계 |
| 2 | [neo4j_설계_근거.md](./neo4j_설계_근거.md) | `CURRENT-ADR` | 현재 채택한 Term·분류·관계 설계 판단과 버린 대안 |
| 3 | [neo4j_관계_정규화_점검.md](./neo4j_관계_정규화_점검.md) | `AUDIT-WIP` | 노드 속성/관계 중복, 최신 코드·CSV·DB 적용 상태와 잔여 작업 |
| 4 | [neo4j_파이프라인_레퍼런스.md](./neo4j_파이프라인_레퍼런스.md) | `CURRENT-PARTIAL` | 실행·적재·검증 운영 기준. 상단 최신 보충을 우선 적용 |
| 5 | [neo4j_preprocessing_file_map.md](./neo4j_preprocessing_file_map.md) | `CURRENT-PARTIAL` | 폴더와 산출물 사전. 상단 최신 보충을 우선 적용 |
| 6 | [neo4j_implementation_mermaid_flow.md](./neo4j_implementation_mermaid_flow.md) | `CURRENT-PARTIAL` | 12단계 ETL 흐름. 상단 최신 다이어그램을 우선 적용 |
| 7 | [neo4j_그래프_스키마_mermaid.md](./neo4j_그래프_스키마_mermaid.md) | `CURRENT-SOURCE` | 최신 SOURCE 스키마와 LIVE 차이 |
| 8 | [neo4j_preprocessing_eda_notes.md](./neo4j_preprocessing_eda_notes.md) | `EVIDENCE-SNAPSHOT` | 초기 원천 EDA와 해석 근거. 현재 구현 수치 문서가 아님 |
| 9 | [neo4j_구축_결과_보고.md](./neo4j_구축_결과_보고.md) | `MVP-SNAPSHOT` | 초기 17노드/22관계 타입 시점의 역사적 결과 보고 |

`CURRENT-PARTIAL` 문서 안의 오래된 5단계·17종·39개 수치는 과거 기본 그래프 설명이다. 최신 전체 구조를 판단할 때는 해당 문서 상단의 최신 보충과 이 인덱스를 우선한다.

## 4. 최신 SOURCE 증분

최신 SOURCE 계약의 전체 실행 결과는 node CSV 26개, relationship CSV 55개,
`SourceUrl` 57,239건, 인물 관계 184,044건이다. 사전 적재 QA는 Cypher가 선언한 artifact
집합과 실제 파일의 일치, 관계 endpoint·고유키·기대 건수 계약, Cypher `MERGE` identity,
인물 관계 타입·대칭 evidence·degree, 이미지 관계 분리를 검사했다. preload 계약 114개와
golden case 21개가 모두 통과해 총 135/135 PASS다. 55개 relation CSV 전체에서
`MERGE` identity 중복은 0건이다.

파생 `ABOUT_*` 관계의 `canonical_category_id`, `canonical_category_path`, `match_type`은
같은 원본 category mapping tuple 순서로 각각 pipe 집계한다. preload QA는 세 열의 항목
수가 같은지 확인하고, 각 Term/Event의 `HAS_CATEGORY`와
`CanonicalCategory-[:ABOUT_*]` 매핑을 조합해 source-target별 기대 tuple 전체를 만든다.
이 기대 set과 실제 집계 tuple set이 정확히 같아 누락·초과가 0인지 검사한다.

현재 working tree가 추가한 핵심은 다음과 같다.

| 영역 | 생성물/관계 | 상태 |
|---|---|---|
| 인물 관계 | CSV `relation_type`=`seed.neo4j_rel_type`, Neo4j 5.26 dynamic typed load | GENERATED 184,044건·QA 통과, LIVE 미적용 |
| 시대 계층 | `period_subperiod_of.csv` → `SUBPERIOD_OF` | SOURCE 반영, 직전 완전 생성에서 21건 확인, LIVE 미적용 |
| 사건의 관련 사건명 | `event_group_has_term_candidate.csv` → `HAS_TERM_CANDIDATE` | GENERATED 18건·QA 통과, LIVE 미적용 |
| 사건 시작 재위 | `event_started_during_reign.csv` → `STARTED_DURING_REIGN` | GENERATED 444건, LIVE 미적용 |
| 사건 종료 재위 | `event_ended_during_reign.csv` → `ENDED_DURING_REIGN` | GENERATED 445건, LIVE 미적용 |
| 이미지 관련 콘텐츠 | `source_image_has_related_content.csv` → `HAS_RELATED_CONTENT` | GENERATED 고유 URL 427개·관계 1,720건·QA 통과, LIVE 미적용 |
| 안전한 전처리 | 최종 `nodes/relations`만 `.neo4j_import.building`에서 검증 후 atomic promotion | 18:14:27 KST manifest 생성·final 승격 완료, LIVE 적재와 분리 |

사건 날짜의 왕호와 연도가 실제 재위 범위를 벗어난 1개 Event는 시작·종료 관계를 만들지
않았다. `staging/event_reign_mapping_review.csv`에 `YEAR_OUT_OF_RANGE` 2행
(시작 1행, 종료 1행)으로 보존한다.

최신 의미 축 관계는 `term_about_country.csv` 1,619건,
`term_about_economic_domain.csv` 2,893건, `term_about_taxonomy_facet.csv` 22,894건,
`event_about_taxonomy_facet.csv` 691건이다.

관련 사건명은 224개 Event를 32개 EventGroup으로 먼저 공유시킨다. 그중 Term 이름과 exact
unique 일치하는 EventGroup 18건만 `HAS_TERM_CANDIDATE`로 연결한다. 후보에는
`review_status=AUTO_CANDIDATE`, `answer_eligible=N`을 기록하므로 승인 전 정답 근거나
canonical 사실로 사용하지 않는다.

## 5. 실행 결과를 문서화하는 규칙

앞으로 변동 수치를 본문 여러 곳에 복사하지 않는다. runner는 최종 import 폴더를 먼저
지우지 않고 sibling 후보 디렉터리에 최종 `nodes/relations`를 만든다. 12단계와 QA가
성공하면 completion manifest를 기록한 뒤 기존 최종 폴더를 previous로 이동하고 후보를
최종으로 승격한다. 이 원자적 보호 범위는 최종 import뿐이다. `normalized`, `dictionary`,
`mapping`, `staging`은 기존 위치에서 삭제·재생성되는 비원자적 중간 산출물이므로 실패 시
부분 상태가 남을 수 있다. 이 파일 승격은 LIVE DB 적재와 별도다.

완료 marker가 없는 불완전 후보는 다음 실행에서 정리한다. 완료 marker가 있는 후보는
자동 삭제하지 않고 보존한다. Windows에서 실행 중인 Neo4j 컨테이너의 bind mount가 디렉터리
승격을 막으면 컨테이너를 중지한 뒤 다음 명령으로 재생성 없이 검증·승격한다.

```powershell
.\.venv\Scripts\python.exe etl/preprocessing/neo4j/run_neo4j_preprocessing.py --promote-existing
```

현행 completion manifest는 `status`, 완료 시각, schema 파일과 선언 CSV 목록을 기록한다.
아래 실행 재현 정보는 이후 versioned run manifest로 확장하고 문서는 그 값을 참조한다.

- commit과 working tree 상태
- 실행 시각과 입력 데이터 fingerprint
- seed·node·relationship CSV 개수
- CSV별 행 수와 checksum
- QA 통과/실패 결과
- 라이브 적재 시각과 적재 대상 DB

최신 계약의 `.preprocessing_complete.json`은 2026-07-14 18:14:27 KST에 생성됐고,
QA 135/135 PASS 결과와 함께 final import 승격까지 완료했다. 위 수치는 승격된 final
import의 실측값이다.

runner는 검증된 최종 import 후보를 `neo4j_import` 경로로 교체한다. Linux 계열에서는
승격 뒤 bind mount가 이전 디렉터리를 계속 가리킬 수 있고, Windows에서는 실행 중인
컨테이너가 디렉터리 rename 자체를 막을 수 있다. `LOAD CSV` 전에 신규 파일 노출을 확인하고,
승격이 `PermissionError`로 실패하면 컨테이너를 중지한 뒤 `--promote-existing`으로 다시
승격한다. runner가 Docker를 자동으로 중지하거나 재시작하지는 않는다.

## 6. 갱신 원칙

- 목표 구조가 바뀌면 `neo4j_지식그래프_재설계안.md`를 먼저 갱신한다.
- 현행 구현 판단이 바뀌면 `neo4j_설계_근거.md`를 갱신한다.
- 구현 중 발견·처리 상태는 `neo4j_관계_정규화_점검.md`에 기록한다.
- 실제 DB 적용 여부는 반드시 `SOURCE / GENERATED / LIVE`로 나눠 쓴다.
- 초기 EDA와 MVP 결과는 삭제하지 않고 snapshot으로 보존한다.

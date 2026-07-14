# Neo4j 설계 근거

> 문서 상태: `CURRENT-ADR`
> 확인일: 2026-07-14
> 현행 구현과 목표 설계를 혼동하지 않는다. 현행 상태는 [README.md](./README.md),
> 목표 구조는 [neo4j_지식그래프_재설계안.md](./neo4j_지식그래프_재설계안.md)를 따른다.
> 최신 생성 검증: seed 31개, node CSV 26개, relationship CSV 55개,
> preload 114 + golden 21 = QA 135/135 PASS. completion manifest와 final 승격 완료,
> LIVE Neo4j 미적용.

노드·관계를 왜 그렇게 설계했는지(1부)와 사전·매핑 파일의 상세 설계(2부)를 한 문서로 관리한다.
(구 neo4j_design_decisions_detail.md + neo4j_dictionary_design.md 통합)

---

# 1부. 노드·관계 설계 판단

이 문서는 Neo4j 전처리에서 어떤 노드와 관계를 만들었는지보다, 왜 그렇게 만들었는지를 기록한다.
목표는 "관계 수를 많이 만드는 것"이 아니라 "검색, 문제 생성, RAG, 그래프 탐색에서 잘못된 근거가 퍼지지 않게 하는 것"이다.

원천 CSV는 사람이 읽는 데에는 충분하지만, 그래프가 직접 사용하기에는 의미가 섞여 있다.
용어 카테고리 문자열 안에는 계층과 복수 경로가 들어 있고, 사건 분류는 별도 수집 기준으로 붙어 있으며, 인물 이름은 동명이인이 많고, 시대 표기는 표기 변형과 범위 표현이 섞인다.
그래서 원천 문자열을 그대로 노드 속성으로만 넣지 않고, 반복해서 쓰는 의미 축은 노드와 관계로 분리했다.

이 설계의 기본 판단은 다음과 같다.

| 판단 | 의미 |
|---|---|
| 원본은 보존하고 표준화는 관계로 표현한다 | 원천 값을 덮어쓰지 않고, 표준 카테고리·표준 시대·주제 같은 해석 결과는 별도 노드와 관계로 둔다. |
| 강한 의미의 관계는 보수적으로 만든다 | `REFERS_TO`처럼 "같은 실체"를 뜻하는 관계는 이름만으로 만들지 않는다. 한자, 생몰년, 설명, 검수 seed를 함께 본다. |
| 자주 쓰는 조회 경로는 전처리에서 펼친다 | 시대, 주제, 검색 태그처럼 서비스가 자주 쓰는 축은 1-hop 관계로 물리화한다. |
| 파생 관계에는 근거를 남긴다 | SearchTag, Era, Theme 같은 파생 관계는 `match_source`, `source_detail` 등으로 왜 생겼는지 추적 가능해야 한다. |
| 애매한 연결은 공백으로 둔다 | 나중에 seed로 보강할 수 있는 누락보다, 한 번 들어가면 여러 파생 관계로 퍼지는 오연결이 더 위험하다. |
| 노드는 자기 사실만 소유한다 | 다른 개체를 가리키는 단순 참조는 typed edge, 시기·역할·지역·근거가 필요한 복합 명제는 `Fact`로 표현한다. 원문 텍스트와 리니지 메타는 예외로 보존한다. |

---

## 1. 핵심 노드 설계

### 1.1 Term

`Term`은 한국역사용어시소러스의 용어를 담는 출발 노드다.
용어명, 한자, 설명, 원문 시대, 원문 연도, 파싱된 시작/종료 연도, 출제 가능 여부 같은 값을 보존한다.
문제 생성과 검색에서 가장 많이 직접 조회되는 대상이므로 독립 노드로 둔다.

`Term`이 필요한 이유는 용어집의 설명문이 지문형 문제의 원천이기 때문이다.
예를 들어 `회사령`, `강감찬`, `위화도회군` 같은 항목은 각각 제도, 인물명, 사건명처럼 성격이 다르지만 모두 용어집에서는 하나의 용어로 관리된다.
이 항목들을 별도 노드로 두면 카테고리, 시대, 주제, 실체 유형, 관련 인물, 관련 사건을 모두 엣지로 붙일 수 있다.

`Term`이 없고 원문 CSV 행만 조회하면 매번 `term_lk`, `term_year`, `description` 문자열을 애플리케이션에서 다시 해석해야 한다.
그러면 같은 용어를 검색할 때와 문제 생성할 때 서로 다른 기준으로 처리될 수 있다.
또한 설명 길이, 시험 키워드 여부, 연도 파싱 결과처럼 전처리에서 한 번 계산하면 되는 값을 요청마다 다시 계산해야 한다.

버린 대안은 Term을 Person/Event에 바로 합치는 방식이다.
용어명 중에는 실제 인물이나 사건과 같은 이름이 있지만, 모든 Term이 Person/Event는 아니다.
`선조`처럼 인물명일 수도 있고 일반 명사일 수도 있으며, `권도`처럼 같은 이름으로 여러 설명이 섞일 수도 있다.
따라서 Term은 독립 노드로 유지하고, 실제 인물/사건을 가리키는 경우에만 `REFERS_TO`를 만든다.

`topterm_id`는 Term 계층의 부모 ID가 아니다. 원천에서 17개 값만 가지는 최상위
분류 코드이며, `term_lk`의 17개 루트 그룹과 대응한다. 따라서 Term→Term 엣지를 만들지
않고 정규화 원천에만 남기며 최신 `Term` node CSV에서는 제거한다. 실제 카테고리 계층은
`term_lk`를 분해한 leaf `HAS_CATEGORY`와 `SUBCATEGORY_OF`가 소유한다. 이 값은
`HAS_CATEGORY` 루트 정합 검증에 활용할 수 있지만 현행 QA에는 아직 구현되어 있지 않다.

### 1.2 Event

`Event`는 ITKC 관계망의 역사 사건 노드다.
사건명, 원천 사건 분류, 시대, 날짜 파싱 결과, 출처 URL, 관련 인물 관계의 중심이 된다.
사건은 인물과 시대와 분류가 만나는 허브이므로 Term과 별도 노드로 둔다.

`Event`가 필요한 이유는 사건이 단순 텍스트가 아니라 여러 인물이 참여하고, 여러 출처를 가지며, 사건군으로 묶일 수 있는 실체이기 때문이다.
예를 들어 어떤 인물이 어떤 사건에 참여했는지, 그 사건이 어느 시대인지, 사건 분류가 전쟁인지 반란인지, 출처 URL이 무엇인지를 모두 관계로 표현해야 한다.

`Event`가 없으면 사건 참여 관계가 Person 속성이나 설명문 문자열에 묻힌다.
그러면 "이 사건에 참여한 인물", "이 인물이 관련된 사건", "이 사건과 같은 시대의 다른 사건" 같은 그래프 탐색이 어려워진다.
문제 생성에서도 사건 중심 오답 후보를 만들 수 없고, RAG에서 사건별 출처를 수집하기도 어려워진다.

버린 대안은 Term 중 사건명과 같은 항목을 Event로 대체하는 방식이다.
하지만 Term의 사건명과 Event의 사건명은 원천과 범위가 다르다.
Term은 용어집 항목이고 Event는 관계망 사건이므로, 동일 이름이더라도 설명과 출처가 다를 수 있다.
따라서 둘을 합치지 않고, 유일하게 일치하는 경우에만 `Term - REFERS_TO - Event`로 연결한다.

### 1.3 Person

`Person`은 ITKC 사건 참여자와 인물 관계망에서 온 인물 노드다.
생몰년, 본관, 인물 상세 URL, 관계망 연결 정도를 가진다.
Person은 이름 문자열이 아니라 `person_id` 기준의 원천 식별자로 유지한다.

`Person`이 필요한 이유는 한국사 데이터에서 인물은 가장 많이 재사용되는 실체이기 때문이다.
한 인물은 사건에 참여하고, 다른 인물과 가족·교유·사제 관계를 가지며, Term 설명에서 언급되고, 특정 시대와 주제의 후보가 된다.
이 관계들을 한 노드에 모아야 "강감찬과 관련된 사건", "이 사건에 참여한 인물", "이 인물의 관계망" 같은 탐색이 가능하다.

`Person`이 없으면 인물명은 여러 CSV의 문자열로 흩어진다.
동명이인을 구분하기 어렵고, 인물 상세 URL과 관계 근거 URL도 따로 관리하기 어렵다.
특히 이름만으로 연결하면 같은 한글 이름을 가진 다른 시대 인물들이 하나처럼 보인다.

버린 대안은 이름과 한자가 같은 Person을 자동 병합하는 방식이다.
이 방식은 관계와 속성이 풍부한 서로 다른 인물을 하나의 노드로 합칠 위험이 있다.
현재 공식 전처리에서는 Person 병합 seed를 사용하지 않고, Term이 특정 Person을 가리키는 경우만 `term_id`, `person_id` 단위로 승인한다.

---

## 2. 분류 노드 설계

### 2.1 CanonicalCategory

`CanonicalCategory`는 `history_terms.term_lk`를 분해해 만든 표준 카테고리 노드다.
`정치·행정·법제>행정>중앙행정기구` 같은 경로를 depth별 노드로 만들고, `SUBCATEGORY_OF` 관계로 계층을 표현한다.

이 노드가 필요한 이유는 카테고리 경로 문자열을 매번 파싱하지 않기 위해서다.
상위 카테고리 기준 검색, 같은 카테고리 오답 후보 생성, 카테고리별 주제 매핑은 모두 이 노드를 기준으로 안정적으로 수행된다.

없으면 `term_lk` 문자열을 쿼리마다 split해야 한다.
복수 경로 `>>`와 계층 구분자 `>`를 매번 해석해야 하므로 쿼리와 애플리케이션 코드가 복잡해진다.
또한 상위 카테고리 전체 검색을 하려면 문자열 prefix 검색에 의존하게 되어 의미 축과 문자열 형식이 섞인다.

버린 대안은 Term을 모든 상위 카테고리에 직접 연결하는 방식이다.
그렇게 하면 조회는 쉬워지지만 관계 수가 늘고, 어떤 연결이 원본 leaf 연결인지 상위 확장인지 구분하기 어려워진다.
그래서 Term은 leaf category에만 직접 연결하고, 상위 탐색은 `SUBCATEGORY_OF`로 처리한다.

### 2.2 SourceEventCategory

`SourceEventCategory`는 ITKC 사건 원본 분류를 그대로 보존하는 노드다.
`전쟁`, `반란`, `옥사`, `고변/탄핵` 같은 원천 분류를 표준 카테고리와 바로 합치지 않고 별도 축으로 둔다.

이 노드가 필요한 이유는 원본 분류와 표준 카테고리의 성격이 다르기 때문이다.
`events.subject_category`는 사건 수집 과정에서 붙은 평면 분류이고, `terms.term_lk`는 계층형 용어 시소러스다.
둘을 직접 합치면 원본이 무엇이었고, 매핑이 무엇이었는지 구분할 수 없다.

없으면 이벤트 원본 분류를 잃거나, 표준 카테고리에 억지로 덮어써야 한다.
매핑이 틀렸을 때 원본 기준으로 되돌아가기 어렵고, 사건 분류 자체의 품질 검수도 힘들어진다.

버린 대안은 `SourceEventCategory` 없이 `Event - HAS_CATEGORY - CanonicalCategory`만 만드는 방식이다.
이 방식은 import 결과가 단순해 보이지만, crosswalk가 틀린 경우 오류 원인을 추적할 수 없다.
그래서 원본 분류는 `SourceEventCategory`로 보존하고, 표준화는 `MAPPED_TO_CATEGORY` 관계로 표현한다.

### 2.3 EventFacet

`EventFacet`은 사건 분류를 의미 축으로 재분류한 노드다.
원본 이벤트 분류는 수집 기준에 가까우므로, 서비스에서 바로 쓰기에는 의미가 고르지 않을 수 있다.
`EventFacet`은 사건을 전쟁, 정치, 제도, 사회 같은 의미 단위로 다시 볼 수 있게 한다.

이 노드가 필요한 이유는 원본 분류 보존과 서비스용 의미 축이 서로 다르기 때문이다.
원본은 원본대로 보존해야 하지만, 서비스는 "전쟁 성격의 사건", "정치 제도 관련 사건"처럼 더 안정적인 의미 필터가 필요하다.

없으면 사건 검색은 원본 분류 문자열에 직접 의존한다.
원본 분류가 세분화되거나 수집 기준이 흔들리면 서비스 쿼리도 같이 흔들린다.
반대로 `EventFacet`이 있으면 원본 분류가 바뀌어도 seed를 통해 의미 축을 유지할 수 있다.

버린 대안은 `SourceEventCategory`와 `EventFacet`을 하나로 합치는 방식이다.
일부 값이 같아 보여도 역할은 다르다.
`SourceEventCategory`는 원본 보존이고, `EventFacet`은 서비스 의미 축이다.

### 2.4 TaxonomyFacet

`TaxonomyFacet`은 표준 카테고리의 중간 경로를 검색·필터 축으로 승격한 노드다.
예를 들어 Term은 leaf category에만 직접 연결하지만, 서비스에서는 `정치·행정·법제>행정`처럼 중간 단위로도 검색해야 한다.

이 노드가 필요한 이유는 leaf 연결 원칙과 중간 단위 검색 요구를 동시에 만족하기 위해서다.
Term을 모든 상위 카테고리에 직접 연결하지 않으면서도, 중간 의미 축으로 빠르게 필터링하려면 별도 facet이 필요하다.

없으면 "행정 관련 용어 전체" 같은 쿼리는 `SUBCATEGORY_OF` 가변 깊이 탐색을 매번 수행해야 한다.
가능은 하지만 문제 생성과 필터 검색에서 반복되면 쿼리가 복잡해지고 성능 예측도 어려워진다.

버린 대안은 상위 카테고리마다 Term에 직접 `HAS_CATEGORY`를 붙이는 방식이다.
그 방식은 leaf와 ancestor를 같은 관계로 섞기 때문에 원천 연결의 의미가 흐려진다.
`TaxonomyFacet`은 중간 단위 검색이 목적이라는 점을 별도 노드로 분명히 한다.

### 2.5 Country, Region, EconomicDomain

`Country`, `Region`, `EconomicDomain`은 카테고리 경로 안에 섞여 있는 의미 축을 분리한 노드다.
`러시아`, `미국`, `북한`은 단순 하위 카테고리라기보다 국가 축이고, `동남아시아`, `유럽`은 권역 축이며, `수산업`, `광공업`은 경제 분야 축이다.

이 노드들이 필요한 이유는 카테고리 계층과 의미 필터가 다르기 때문이다.
국가를 카테고리 하위 항목으로만 두면 "러시아 관련 항목"을 찾을 때 카테고리 경로 구조에 의존해야 한다.
별도 노드로 두면 `ABOUT_COUNTRY`, `ABOUT_REGION`, `ABOUT_ECONOMIC_DOMAIN` 1-hop으로 필터링할 수 있다.

없으면 국가·권역·경제 분야 검색은 문자열 경로 검색이나 카테고리 ancestor 탐색으로 처리해야 한다.
그 결과 국가가 카테고리인지, 지역인지, 경제 분야인지 구분하기 어렵고, 서비스 필터의 의미도 흐려진다.

버린 대안은 이 값들을 모두 `CanonicalCategory` 안에만 두는 방식이다.
그렇게 하면 원본 계층은 단순해지지만, 사용자가 실제로 필요한 "국가별", "지역별", "경제 분야별" 필터가 불안정해진다.
그래서 category 구조는 보존하되, 의미 축은 별도 노드와 `ABOUT_*` 관계로 분리했다.

---

## 3. 시대와 주제 노드 설계

### 3.1 Period

`Period`는 원천 시대 표기를 보존하는 노드다.
`고려`, `고려시대`, `조선전기`, `대한제국기`처럼 원천에 나온 표현과 변형을 관리한다.

이 노드가 필요한 이유는 원천 표기를 지우지 않고 검수 가능성을 유지하기 위해서다.
서비스는 표준 시대 `Era`를 쓰지만, 원천 데이터가 실제로 어떤 시대 문자열을 가졌는지도 확인할 수 있어야 한다.

없으면 원천 시대 표기를 바로 `Era`로 덮어쓰게 된다.
그러면 어떤 용어가 왜 고려로 들어갔는지, 조선전기와 조선후기 같은 세부 표기가 어떻게 처리됐는지 추적하기 어렵다.

버린 대안은 `Era`만 두고 `Period`를 없애는 방식이다.
그 방식은 서비스 필터는 단순해지지만, 원천 표기와 표준화 결과를 구분할 수 없다.
그래서 `Period`를 원본 축으로 두고, `PART_OF_ERA`로 표준 시대에 연결한다.

### 3.2 Era

`Era`는 서비스에서 쓰는 표준 시대 노드다.
선사시대부터 현대까지 큰 시대 축을 10개로 고정하고, 시작/종료 연도 범위를 가진다.

이 노드가 필요한 이유는 사용자가 선택할 수 있는 시대 필터가 너무 세분화되면 서비스가 불편해지기 때문이다.
원천에는 30종 이상의 시대 표기가 있지만, 문제 생성과 검색에서는 "고려", "조선", "일제강점기" 같은 큰 축이 필요하다.
또한 `Era`의 연도 범위는 Person 생몰년으로 시대를 연결할 때 기준이 된다.

없으면 시대 검색은 `Period` 변형을 모두 알아야 한다.
예를 들어 고려 관련 항목을 찾기 위해 `고려`, `고려시대`, `고려전기`, `고려후기`를 모두 직접 나열해야 한다.
Person도 생몰년을 어떤 시대에 연결할지 기준이 없다.

버린 대안은 모든 시대를 문자열 속성으로만 두는 방식이다.
그 방식은 단순하지만 시대별 후보 조회가 매번 문자열 비교가 되고, 범위 시대나 생몰년 기반 연결을 처리하기 어렵다.
그래서 `Era`는 표준 시대 필터이자 생몰년 계산 기준으로 둔다.

### 3.3 Theme

`Theme`은 문제 출제와 서비스 필터를 위한 주제 노드다.
정치, 군사, 경제, 문화, 사회, 인물, 사건 같은 고정 주제 10개를 관리한다.

이 노드가 필요한 이유는 카테고리 400개를 사용자에게 그대로 노출할 수 없기 때문이다.
문제 출제에서는 "군사 주제", "경제 주제", "인물 주제"처럼 넓고 안정적인 주제 축이 필요하다.
`Theme`은 카테고리, Term, Event, Person을 서비스용 주제 축으로 묶는 역할을 한다.

없으면 주제 필터는 카테고리 경로에 직접 의존한다.
그러면 화면과 문제 생성 로직이 카테고리 세부 구조에 묶이고, 카테고리 변경 때 서비스 필터도 흔들린다.

버린 대안은 `Theme` 없이 `CanonicalCategory`만 쓰는 방식이다.
카테고리는 원천 분류에 가깝고, 주제는 서비스가 통제해야 하는 축이다.
두 축을 분리해야 "이순신은 실체로는 인물이고 주제로는 군사"처럼 복합 판단이 가능하다.

### 3.4 EntityType

`EntityType`은 Term이 무엇을 가리키는지 나타내는 실체 유형 노드다.
현재 인물, 문헌, 문화재, 장소 같은 유형을 관리한다.

이 노드가 필요한 이유는 "무슨 주제인가"와 "무엇인가"가 다른 질문이기 때문이다.
예를 들어 `이순신`은 실체 유형으로는 인물이고, 주제로는 군사와도 연결될 수 있다.
`훈민정음`은 문헌일 수 있고 문화 주제와도 연결될 수 있다.

없으면 주제와 실체 유형이 섞인다.
오답 후보를 만들 때 인물 문제에 문헌이나 장소가 섞일 수 있고, "인물 용어만" 같은 필터도 불안정해진다.

버린 대안은 `Theme`의 `인물` 주제로 실체 유형까지 대체하는 방식이다.
하지만 Theme는 내용 주제이고 EntityType은 대상의 종류다.
두 축을 분리해야 문제 생성과 검색 조건을 정밀하게 조합할 수 있다.

---

## 4. 검색, 출처, 묶음 노드 설계

### 4.1 SearchTag

`SearchTag`는 Term/Event/Person을 키워드 하나로 찾기 위한 비정규화 검색 노드다.
용어명, 사건명, 인물명, 인물 별칭, 카테고리, 시대, 주제, 국가, 권역, 경제 분야, taxonomy facet 등을 한 검색 축으로 모은다.

이 노드가 필요한 이유는 검색 쿼리를 단순하게 만들기 위해서다.
SearchTag가 없으면 사용자가 `전쟁`을 검색할 때 Term 이름, Event 이름, SourceEventCategory, CanonicalCategory, EventFacet, Theme, Era, TaxonomyFacet 등을 모두 `OR`로 조회해야 한다.
이는 쿼리가 길고 누락되기 쉬우며, 애플리케이션 코드에도 검색 대상 축이 하드코딩된다.

없으면 검색은 매번 여러 노드와 관계를 직접 탐색해야 한다.
새 검색 축이 추가될 때마다 쿼리도 바뀌고, Term/Event/Person을 같은 방식으로 찾기 어렵다.

버린 대안은 Event에만 SearchTag를 두는 방식이다.
초기에는 사건 검색 편의만 생각할 수 있지만, 실제 서비스에서는 용어·사건·인물을 같은 키워드로 찾아야 한다.
그래서 SearchTag는 Event만이 아니라 Term/Event/Person 전체에 연결한다.

SearchTag는 의도된 중복 레이어이므로 출처를 반드시 남긴다.
`HAS_SEARCH_TAG` 관계의 `source_node_type`, `source_node_id`, `source_relation`, `source_detail`은 이 태그가 어디에서 왔는지 추적하기 위한 속성이다.
Person 별칭은 `PersonAlias` 출처로 분리하고, Event/Term에서 Person으로 상속된 태그는 `source_detail`에 원천 `event_id` 또는 `term_id`를 보존한다.
같은 노드가 여러 태그 출처로 검색될 수 있으므로 조회 결과는 `RETURN DISTINCT`로 받아야 한다.

### 4.2 SourceUrl

`SourceUrl`은 사건 URL, 인물 상세 URL, 이미지 원천이 명시한 관련 콘텐츠 URL을 관리하는
출처 노드다.
URL 문자열, 출처 타입, RAG 수집 대상 여부, 수집 상태를 관리할 수 있다.

이 노드가 필요한 이유는 출처가 단순 속성이 아니라 RAG 수집과 답변 근거 표시의 대상이기 때문이다.
URL을 노드로 두면 여러 Event/Person에서 같은 출처를 공유할 수 있고, 수집 상태를 URL 단위로 관리할 수 있다.

없으면 URL은 Event/Person 속성 문자열로 흩어진다.
중복 수집을 막기 어렵고, 어떤 URL이 아직 수집되지 않았는지, 어떤 노드가 어떤 출처를 쓰는지 추적하기 어렵다.

버린 대안은 모든 URL을 SourceUrl 노드로 승격하는 방식이다.
`person_relations.evidence_url`은 많은 인물 관계가 같은 문헌/목록 URL을 공유할 수 있어 노드로 만들면 과도한 허브가 된다.
그래서 사건 URL, 인물 상세 URL, 이미지 관련 콘텐츠 URL은 `SourceUrl` 노드로 만들고,
이미지는 `HAS_RELATED_CONTENT`로 연결한다. 인물 관계 근거 URL은
typed 인물 관계 또는 catch-all `RELATED_TO`의 `evidence_url` 속성으로 보존한다.

`SourceImage.related_content` 원문은 이미지 자체의 사실이 아니라
`제목 (콘텐츠군) | URL` 형식의 외부 개체 참조다. 노드 속성으로 중복 보존하지 않고
staging에서 구조화해 관계로 승격한다. 이 관계는 “그 이미지가 무엇을 묘사하는가”라는
`DEPICTS`와 다르다. `DEPICTS`는 이미지 제목 또는 사람이 승인한 override만 근거로 삼고,
관련 콘텐츠 문자열을 실물·그림 판정에 사용하지 않는다.

### 4.3 EventGroup

`EventGroup`은 related_event 기준으로 여러 사건을 묶는 노드다.
고려거란전쟁처럼 여러 사건이 하나의 큰 흐름에 속하는 경우, 개별 Event 위에 묶음 단위를 제공한다.

이 노드가 필요한 이유는 역사 사건이 단일 행으로 끝나지 않는 경우가 많기 때문이다.
전쟁이나 정치 사건은 전후 사건이 이어지고, 문제 생성에서도 "이 사건 전후 흐름"을 묻는 경우가 있다.

없으면 사건 흐름을 찾기 위해 사건명 문자열 유사도나 수작업 목록에 의존해야 한다.
관련 사건을 연결하는 기준이 그래프 안에 없으므로, 흐름형 문제나 사건 묶음 탐색이 불안정해진다.

버린 대안은 Event끼리 직접 `RELATED_EVENT`로만 연결하는 방식이다.
직접 연결만 있으면 같은 사건군 전체를 찾기 위해 여러 edge를 따라야 하고, 사건군 자체의 이름과 속성을 붙이기 어렵다.
`EventGroup`을 두면 묶음 단위를 노드로 다룰 수 있다.

원천 `related_event_name`을 Term 후보로 해소할 때도 개별 Event마다 같은 연결을 반복하지
않는다. 먼저 `PART_OF_EVENT_GROUP`으로 사건군을 만든 뒤, 사건군 이름과 유일하게 일치한
Term만 `EventGroup - HAS_TERM_CANDIDATE -> Term`으로 연결한다. 이 관계는
`AUTO_CANDIDATE`, `answer_eligible=N`인 검수 후보이며 사건 동일성이나 정답 근거가 아니다.

---

## 5. 관계 설계 판단

관계는 노드 사이의 선을 많이 긋기 위해 만든 것이 아니라, 원천 의미와 서비스 조회 의미를 분리하기 위해 만들었다.
같은 두 노드를 연결하더라도 관계 타입이 다르면 의미 강도가 다르다.
예를 들어 `Term - REFERS_TO - Person`은 동일 실체 연결이고, `Term - MENTIONS_PERSON - Person`은 설명문 언급이다.
이 둘을 합치면 검색 결과는 늘어나지만 정답 근거의 신뢰도는 떨어진다.

관계 타입별 판단은 다음과 같다.

| 관계 | 역할 | 없으면 생기는 문제 | 설계 판단 |
|---|---|---|---|
| `HAS_CATEGORY` | Term/Event를 표준 카테고리에 연결 | 분류 검색과 같은 분류 오답 후보 생성이 문자열 파싱에 의존한다. | Term은 leaf category에만 직접 연결하고, 상위 탐색은 `SUBCATEGORY_OF`로 처리한다. |
| `SUBCATEGORY_OF` | CanonicalCategory 계층을 표현 | 상위·하위 카테고리 탐색을 문자열 prefix 검색으로 처리해야 한다. | 계층은 노드 관계로 표현하되, 국가·지역처럼 별도 의미 축으로 뺀 값은 카테고리 계층에서 제외한다. |
| `HAS_EVENT_CATEGORY` | Event와 원본 사건 분류를 연결 | 사건 원본 분류가 표준화 결과에 덮여 매핑 오류를 검수하기 어렵다. | 원본 분류는 `SourceEventCategory`로 보존한다. |
| `MAPPED_TO_CATEGORY` | SourceEventCategory와 CanonicalCategory의 crosswalk | 이벤트 분류와 용어 카테고리의 연결 기준이 코드나 쿼리에 흩어진다. | 서로 다른 분류 체계의 연결은 seed/mapping 관계로만 관리한다. |
| `HAS_EVENT_FACET` | Event를 사건 의미 facet에 연결 | 원본 사건 분류가 서비스 의미 축을 그대로 떠안아 검색 의미가 흔들린다. | 원본 분류와 의미 facet을 분리해 원본 보존과 서비스 필터를 동시에 만족한다. |
| `IN_PERIOD` | Term/Event의 원천 시대 표기를 보존 | 원천 시대가 무엇이었는지 추적할 수 없다. | 원천 시대 표기는 `Period`에 남기고, 표준 시대는 별도 `Era`로 연결한다. |
| `SUBPERIOD_OF` | 세부 Period를 상위 Period에 연결 | 고려전기·고려후기 같은 세부 시기가 평면 문자열로 남는다. | 원천 이름이 유일하게 해소될 때만 자식→부모 계층으로 연결하고 순환을 금지한다. |
| `PART_OF_ERA` | Period를 표준 Era로 통합 | 고려/고려시대/고려전기 같은 변형을 매번 쿼리에서 묶어야 한다. | 표기 변형 흡수는 seed 기반 관계로 관리한다. |
| `IN_ERA` | Term/Event/Person을 표준 시대로 직접 연결 | 서비스 시대 필터가 매번 2-hop 이상 경로를 타야 한다. | 원천 경로는 유지하고, 조회용 직통 관계를 파생 산출물로 만든다. |
| `HAS_THEME` | Term/Event/Person/Category를 서비스 주제에 연결 | 사용자에게 카테고리 400개를 직접 노출하거나 주제 필터를 코드에서 계산해야 한다. | Theme은 서비스가 통제하는 고정 축이고, 원천 매핑은 Category-Theme 관계로 남긴다. |
| `HAS_ENTITY_TYPE` | Term의 실체 유형을 연결 | 인물/문헌/문화재/장소 같은 오답 후보 유형을 구분하기 어렵다. | 내용 주제인 Theme과 대상 종류인 EntityType을 분리한다. |
| `ABOUT_COUNTRY` | 국가 의미 축을 연결 | 국가 검색이 카테고리 경로 문자열에 의존한다. | 국가는 카테고리 하위 항목이 아니라 독립 의미 축으로 둔다. |
| `ABOUT_REGION` | 권역 의미 축을 연결 | 지역/권역 필터가 국가나 카테고리와 섞인다. | 권역은 국가와 다른 축으로 보고 별도 노드와 관계로 둔다. |
| `ABOUT_ECONOMIC_DOMAIN` | 경제 분야 의미 축을 연결 | 경제·산업 하위 분야 검색이 중간 카테고리 문자열에 묻힌다. | 경제 분야는 서비스 필터가 될 수 있으므로 별도 축으로 분리한다. |
| `ABOUT_TAXONOMY_FACET` | 중간 카테고리 facet을 연결 | leaf category 원칙 때문에 중간 단위 검색이 비어버린다. | 중간 경로 검색은 `TaxonomyFacet`으로 보완한다. |
| `INVOLVED_IN` | Person과 Event 참여 관계를 연결 | 인물이 어떤 사건과 연결되는지 그래프 탐색이 불가능하다. | 원본 참여 행의 식별자를 보존해 같은 Person-Event라도 역할/근거가 다른 기록을 뭉개지 않는다. |
| typed 인물 관계 | Person 간 가족·혼인·사제·사회 관계를 연결 | 유형이 속성에만 있으면 타입 경로 질의와 의미별 hop이 불편하다. | `normalized_relation_type`의 승인 목록은 typed edge로 적재하고, 미등록 유형만 `RELATED_TO` catch-all로 보존한다. |
| `REFERS_TO` | Term이 실제 Person/Event를 가리키는 강한 연결 | 용어 세계와 인물/사건 세계가 이어지지 않는다. | 오연결 위험이 커서 이름만으로 만들지 않고 보수적으로 생성한다. |
| `MENTIONS_PERSON` | Term 설명문에 언급된 Person을 연결 | 설명문 맥락 확장과 관련 인물 후보 탐색이 어렵다. | `REFERS_TO`보다 약한 관계로 분리해 정답 실체 연결과 혼동하지 않게 한다. |
| `HAS_TERM_CANDIDATE` | EventGroup 이름을 유일 Term 후보에 연결 | 개별 Event마다 같은 사건군 이름을 Term에 반복 연결하면 분포가 부풀고 사건 동일성처럼 보인다. | EventGroup 단위 후보만 만들고 `answer_eligible=N`으로 두어 canonical 사건 사실이나 정답 근거로 사용하지 않는다. |
| `STARTED_DURING_REIGN` / `ENDED_DURING_REIGN` | 사건 시작·종료 시점을 Reign에 연결 | 왕호 문자열만으로는 재위·국가·연도를 따라갈 수 없다. | 왕호가 유일하거나 사건 연도로 동명 왕호가 하나로 해소될 때만 연결하고 `match_method`를 남긴다. |
| `PART_OF_EVENT_GROUP` | Event를 사건군에 연결 | 연속 사건이나 전쟁 흐름을 묶어 조회하기 어렵다. | 사건군 자체를 노드화해 전후 맥락과 흐름형 문제 생성을 지원한다. |
| `HAS_SOURCE_URL` | Event/Person을 출처 URL에 연결 | RAG 수집 상태와 출처 근거를 URL 단위로 관리하기 어렵다. | RAG 후보가 되는 URL만 노드로 승격한다. |
| `HAS_RELATED_CONTENT` | SourceImage를 원천이 안내한 별도 콘텐츠 URL에 연결 | 관련 콘텐츠가 이미지의 묘사 대상처럼 섞여 실물과 그림을 구분하기 어려워진다. | 구조화 URL 참조로만 표현하고 `DEPICTS`와 근거·의미를 분리한다. |
| `HAS_SEARCH_TAG` | Term/Event/Person을 통합 검색 태그에 연결 | 키워드 검색이 여러 축의 OR 조건으로 길어진다. | 의도된 비정규화이므로 출처 속성을 반드시 남기고 조회 시 `DISTINCT`를 쓴다. |

### 5.1 REFERS_TO와 MENTIONS_PERSON을 분리한 이유

`REFERS_TO`는 Term이 실제 Person/Event를 가리킨다는 강한 관계다.
반면 `MENTIONS_PERSON`은 Term 설명문 안에 어떤 인물명이 언급됐다는 약한 관계다.

둘을 분리해야 하는 이유는 의미 강도가 다르기 때문이다.
`강감찬` Term이 Person 강감찬을 가리키는 것은 `REFERS_TO`지만, 어떤 제도 설명 안에 강감찬이 언급되는 것은 동일 실체 연결이 아니다.
이 둘을 같은 관계로 합치면 설명에 등장한 인물이 마치 용어의 정답 실체처럼 보인다.

`REFERS_TO`를 잘못 만들면 영향이 크다.
Person SearchTag 상속, 주제 상속, 관련 노드 확장, 문제 후보 추천에서 모두 강한 근거처럼 쓰일 수 있다.
그래서 이름/한자/생몰년이 맞는 유일 후보, 관계망 단서가 있는 후보, 사람이 승인한 후보만 연결한다.

### 5.2 인물 관계를 typed edge로 적재하는 이유

초기 MVP는 인물 관계를 `RELATED_TO` 하나로 적재하고 실제 의미를 관계 속성에
보존했다. 원천 유형이 흔들려도 스키마를 유지하기 쉬운 장점은 있었지만,
"아버지 관계만 따라가기" 같은 질의가 타입 패턴이 아니라 속성 필터에 의존하고
문제 생성용 공유 경로도 납작해졌다.

현재 정책은 **원천 CSV는 하나로 유지하고 적재 타입만 분리**하는 방식이다.
`relation_type_seed.csv`의 `neo4j_rel_type`이 실제 관계 타입의 단일 기준이고,
`relation_type_dictionary.csv`가 이를 정규화명·방향·역관계·대칭 여부와 함께 전달한다.
승인된 유형은 `HAS_FATHER`, `HAS_CHILD`, `SIBLING_OF`,
`HAS_TEACHER`, `HAS_STUDENT`, `ASSOCIATED_WITH` 등의 typed edge로 적재한다.
`raw_relation_type`, `relation_group`, `direction_rule`, `is_symmetric`,
`inverse_relation_type`, `evidence_url`은 추적과 검증을 위해 관계 속성에도 보존한다.

목록 밖 유형은 유실시키지 않고 `RELATED_TO` catch-all로 적재한다. 다만 catch-all이
1건이라도 생기면 정상 완료로 보지 않고 seed와 import type을 검토하는 QA 신호로 쓴다.
대칭 관계는 무방향 쌍 기준 한 방향만 저장하고 조회할 때 무방향 패턴을 사용한다.
양방향 원천 행을 합칠 때 서로 다른 비어 있지 않은 `evidence_url`을 정렬·중복 제거해
모두 보존한다. `related_count`는 관계 강도가 아니라 대상 쪽 원천 집계일 가능성이 있어
최종 관계에서 제거한다. Person의 `core_relation_degree`는 원천 행 수가 아니라 최종
`INVOLVED_IN`과 typed Person-Person 관계 endpoint에서 다시 계산한다.
이 정책은 타입 폭발을 막으면서도 안정된 역사 관계를 그래프의 의미 타입으로 노출한다.

### 5.3 IN_PERIOD, PART_OF_ERA, IN_ERA를 함께 둔 이유

`IN_PERIOD`는 원천 시대 표기를 보존하는 관계다.
`PART_OF_ERA`는 Period를 표준 Era에 연결하는 관계다.
`IN_ERA`는 서비스 조회를 위해 Term/Event/Person에서 Era로 바로 가는 파생 관계다.

세 관계가 모두 필요한 이유는 원천 추적과 빠른 조회가 서로 다른 요구이기 때문이다.
원천 검수에서는 `IN_PERIOD`가 필요하고, 표준 시대 통합에는 `PART_OF_ERA`가 필요하며, 서비스 필터에서는 `IN_ERA`가 필요하다.

`IN_ERA`만 있으면 원천이 어떤 시대 표기였는지 알 수 없다.
반대로 `IN_PERIOD -> PART_OF_ERA`만 있으면 서비스에서 시대 후보를 찾을 때 매번 2-hop 이상을 타야 한다.
그래서 원천 경로는 유지하고, 조회용 직통 엣지를 전처리에서 만든다.

Person의 `IN_ERA`는 특히 보수적으로 만든다.
생몰년이 있으면 Era 범위와의 겹침을 사용하고, 생몰년이 없을 때만 참여 사건의 Era를 보조로 쓴다.
더 좁은 Era가 같은 생애 겹침 구간을 완전히 설명하면 넓은 Era 중복은 제외한다.
부분 연도 `15??`처럼 세기 해석이 애매한 값은 정확한 숫자처럼 쓰지 않는다.

### 5.4 HAS_THEME와 HAS_ENTITY_TYPE을 분리한 이유

`HAS_THEME`은 내용 주제이고, `HAS_ENTITY_TYPE`은 대상의 실체 유형이다.
둘 다 `인물`이라는 단어를 가질 수 있지만 역할은 다르다.

예를 들어 `이순신`은 실체 유형으로는 인물이고, 내용 주제로는 군사와도 관련된다.
`훈민정음`은 실체 유형으로는 문헌이고, 주제로는 문화나 제도와 연결될 수 있다.
두 축을 합치면 "인물 문제"와 "군사 주제 문제"를 구분하기 어렵다.

없으면 오답 후보 생성에서 유형이 다른 항목이 섞인다.
인물 문제에 문헌이 후보로 나오거나, 장소 문제에 사건이 섞일 수 있다.
그래서 Theme은 서비스 주제 필터로, EntityType은 같은 종류의 후보를 고르는 실체 유형 필터로 분리한다.

### 5.5 HAS_SEARCH_TAG가 보조 관계인 이유

SearchTag는 정규화 의미 모델이 아니라 검색 편의 레이어다.
빠르게 후보를 찾기 위한 중복 관계이므로, 정확한 의미 판단의 최종 근거로 쓰면 안 된다.

예를 들어 SearchTag로 `전쟁`을 찾으면 전쟁이라는 이름, 전쟁 category, 전쟁 facet, 전쟁 관련 Theme 등 여러 출처에서 같은 태그가 나올 수 있다.
그래서 SearchTag 조회는 후보 검색의 진입점으로 쓰고, 정확한 의미 검증은 `HAS_EVENT_FACET`, `HAS_CATEGORY`, `HAS_THEME`, `IN_ERA` 같은 원래 관계로 되돌아가 확인한다.

SearchTag가 없으면 검색 쿼리가 길어진다.
하지만 SearchTag만 있으면 의미가 납작해진다.
그래서 SearchTag는 두고, `source_*` 속성으로 원래 의미 축을 추적 가능하게 만든다.

### 5.6 HAS_SOURCE_URL과 인물 관계의 evidence_url을 분리한 이유

출처 URL 중 일부는 독립 노드가 되어야 하고, 일부는 관계 속성으로 남아야 한다.
Event의 source URL과 Person의 detail URL은 RAG 수집 후보이므로 `SourceUrl` 노드로 승격한다.
반면 Person 관계의 evidence URL은 동일 URL이 많은 관계에 반복될 수 있어 노드로 승격하면 과도한 허브가 된다.

그래서 다음 기준을 둔다.

| URL 종류 | 처리 |
|---|---|
| 사건 출처 URL | `Event - HAS_SOURCE_URL - SourceUrl` |
| 인물 상세 URL | `Person - HAS_SOURCE_URL - SourceUrl` |
| 이미지 관련 콘텐츠 URL | `SourceImage - HAS_RELATED_CONTENT - SourceUrl` |
| 인물 관계 근거 URL | typed 인물 관계(또는 catch-all `RELATED_TO`)의 `evidence_url` 속성 |

이 기준이 없으면 모든 URL을 노드로 만들어 그래프가 URL 허브 중심으로 왜곡되거나, 반대로 모든 URL을 속성으로만 묻어 RAG 수집 상태를 관리하지 못한다.

---

## 6. Term-Person 검수 설계 판단

Term-Person 연결은 이 그래프에서 가장 조심해야 하는 부분이다.
Term과 Person은 서로 다른 원천에서 왔고, 같은 이름·같은 한자를 가진 인물이 여러 명 있을 수 있다.
또 어떤 Term은 사람 이름이 아니라 왕호, 관직명, 책 이름, 제도명, 일반 용어일 수 있다.

그래서 공식 검수 흐름은 Person 병합이 아니라 Term-Person 엣지 승인으로 설계했다.
검수자는 `term_person_review.csv`에서 후보를 보고, 확실한 경우에만 `term_person_review_approved.csv`에 `term_id`, `person_id`, `review_status`, `note`를 기록한다.
이 승인 seed만 graph 생성에 반영된다.

제외한 것은 다음과 같다.

| 제외한 것 | 제외한 이유 |
|---|---|
| Person ID 자동 병합 | 이름/한자만으로 동일인이라고 보면 서로 다른 인물의 사건 참여와 관계망이 섞인다. |
| `person_duplicate_review_approved.csv` | 현재 공식 graph 생성은 Person 병합 seed를 읽지 않는다. 필요한 것은 canonical 선택이 아니라 Term이 어떤 Person을 가리키는지의 연결 승인이다. |
| `term_desc_preview` | 잘린 설명은 동명이인 판단 근거를 잃게 한다. 검수 파일에는 전체 `term_description`이 필요하다. |
| 생몰년 추론 | Term 설명의 연도는 생몰년이 아니라 활동 시기, 재위 기간, 사건 시기일 수 있다. 모르면 비워둔다. |

추가한 것은 다음과 같다.

| 추가한 것 | 필요한 이유 |
|---|---|
| `term_year_text`, `term_start_year`, `term_end_year` | Term의 원천 연도와 파싱 결과를 Person 생몰년과 직접 비교하게 한다. |
| 원천 `birth_year`, `death_year` 그대로 표시 | 불확실성을 숨기지 않고 검수자가 판단하게 한다. |
| `review_type` | 같은 파일 안에서 Term-Person 후보와 여러 Person 후보 중 선택해야 하는 상황을 구분한다. |
| 승인 seed 제외 처리 | 이미 승인한 연결은 다음 후보 재생성에서 제외해 반복 검수를 줄인다. |
| `match_type=MANUAL` | 사람이 승인한 연결을 자동 연결과 분리해 추적 가능하게 한다. |

이 기준을 지키지 않으면 오류가 빠르게 퍼진다.
잘못된 `REFERS_TO`는 Person의 SearchTag 상속, `MENTIONS_PERSON` 생성, 주제 상속, 그래프 관련 노드 확장에서 모두 강한 근거로 쓰일 수 있다.
따라서 연결이 부족한 상태는 seed로 나중에 보강하고, 불확실한 연결은 graph에 넣지 않는 것이 더 안전하다.

---

## 7. 이 설계에서 의도적으로 남긴 한계

이 구조는 모든 연결을 자동으로 해결하려는 구조가 아니다.
검수 가능한 자동화와 사람이 결정해야 하는 영역을 분리한다.

| 남긴 한계 | 이유 |
|---|---|
| 동명이인은 일부 staging 후보로 남는다 | 자동 연결보다 검수 정확도가 중요하다. |
| Person 병합은 하지 않는다 | 원천 ID와 관계망을 보존하는 것이 우선이다. |
| SearchTag는 중복이 많다 | 검색 편의를 위한 보조 레이어이므로 중복은 정상이다. 조회 시 `DISTINCT`를 쓴다. |
| SourceUrl은 일부 URL만 노드화한다 | RAG 수집 대상과 관계 근거 URL의 성격이 다르다. |
| 0행 optional 관계는 생성하지 않는다 | 없는 관계를 파일로 남기면 실제 graph에 있는 것처럼 오해할 수 있다. |

이 한계는 미완성이 아니라 설계상 선택이다.
원천을 보존하고, 검수 지점을 분리하고, 파생 관계의 근거를 남기기 위해 일부 자동화를 의도적으로 제한했다.

---

# 2부. 사전·매핑 상세 설계

## 1. 목적

이 문서는 한국사 Neo4j 그래프 구축 전에 필요한 사전(dictionary), 중간 관계 파일(staging relation), 매핑(mapping) 파일의 역할과 필요성을 정리한다.

Neo4j에 데이터를 넣기 전에 사전을 만드는 이유는 원본 CSV의 문자열 값이 그대로는 그래프 탐색에 적합하지 않기 때문이다. 원본에는 카테고리, 시대, 관계 유형, URL 등이 문자열로 들어 있지만, 이 값들은 다음 문제가 있다.

- 같은 의미가 다른 표현으로 반복될 수 있다.
- 하나의 문자열 안에 여러 값이 섞여 있다.
- 계층 구조가 문자열 기호로만 표현되어 있다.
- 관계 방향성이나 대칭성이 명확하지 않다.
- 출처 URL이 여러 테이블과 컬럼에 흩어져 있다.

따라서 전처리 단계에서 다음 세 가지 파일 유형을 분리한다.

| 구분 | 의미 | 예시 |
|---|---|---|
| Dictionary | 표준 목록을 정하는 파일 | `canonical_category_dictionary.csv`, `source_event_category_dictionary.csv`, `relation_type_dictionary.csv` |
| Staging relation | 원본 데이터를 Neo4j 관계로 넣기 좋게 펼친 중간 파일 | `term_canonical_category_relation.csv`, `event_source_category_relation.csv` |
| Mapping | 서로 다른 분류 체계를 연결하는 파일 | `taxonomy_crosswalk.csv` |

정리하면 dictionary는 기준을 만들고, staging은 원본을 그래프 관계로 펼치며, mapping은 서로 다른 기준을 연결한다.

노드와 관계를 왜 그런 단위로 나누었는지, 각 노드가 없으면 어떤 문제가 생기는지, 어떤 대안을 버렸는지는 이 문서 1부에 상세히 기록한다.

---
## 4. `canonical_category_dictionary.csv`

### 4.1 역할

`canonical_category_dictionary.csv`는 `history_terms.term_lk`에서 만든 표준 카테고리 사전이다.

원본 `term_lk`는 다음처럼 문자열 하나에 계층 정보가 들어 있다.

```text
정치·행정·법제>행정>중앙행정기구
교통·통신>교통시설>>교통·통신>교통로
```

여기서 `>`는 계층이고, `>>`는 복수 카테고리 경로다.

이 값을 그대로 `Term` 속성에만 넣으면 다음 질의가 불편해진다.

- 정치·행정·법제 아래의 모든 용어 찾기
- 행정 관련 용어 찾기
- 문화·예술 하위 카테고리 전체 탐색
- 같은 카테고리의 오답 후보 찾기

그래서 `term_lk`를 분해해서 `CanonicalCategory` 노드로 만들 기준표가 필요하다.

### 4.2 왜 필요한가

`canonical_category_dictionary.csv`가 필요한 이유는 다음과 같다.

1. `CanonicalCategory` 노드의 기준 목록이 된다.
2. `SUBCATEGORY_OF` 관계를 만들 수 있다.
3. 같은 카테고리에 속한 용어를 찾을 수 있다.
4. 문제 생성에서 같은 분류의 오답 후보를 찾을 수 있다.
5. 이벤트 카테고리와 매핑할 표준 기준점이 된다.

### 4.3 생성 방식

예를 들어 다음 경로가 있다.

```text
정치·행정·법제>행정>중앙행정기구
```

이 값은 다음 카테고리 row로 분해한다.

| depth | category_name | category_path | parent_category_path |
|---|---|---|---|
| 1 | 정치·행정·법제 | 정치·행정·법제 |  |
| 2 | 행정 | 정치·행정·법제>행정 | 정치·행정·법제 |
| 3 | 중앙행정기구 | 정치·행정·법제>행정>중앙행정기구 | 정치·행정·법제>행정 |

이렇게 하는 이유는 `category_name`만으로는 고유성을 보장할 수 없기 때문이다. 서로 다른 상위 카테고리 아래에 같은 이름의 하위 카테고리가 나올 수 있다. 따라서 고유 기준은 `category_path`가 된다.

### 4.4 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `category_id` | 카테고리 고유 ID |
| `category_name` | 현재 단계 이름 |
| `category_path` | 루트부터 현재 단계까지의 전체 경로 |
| `parent_category_id` | 상위 카테고리 ID |
| `parent_category_path` | 상위 카테고리 경로 |
| `depth` | 계층 깊이 |
| `root_category_name` | 최상위 카테고리 이름 |
| `term_count` | 해당 경로에 연결되는 용어 수 |
| `source` | 생성 근거 |
| `review_status` | 검수 상태 |

---

## 5. `term_canonical_category_relation.csv`

### 5.1 역할

`term_canonical_category_relation.csv`는 dictionary가 아니라 staging relation이다. `Term`과 `CanonicalCategory`를 연결하기 위한 중간 산출물이다.

`canonical_category_dictionary.csv`만 있으면 카테고리 목록은 알 수 있지만, 어떤 `term_id`가 어떤 카테고리에 속하는지는 알 수 없다. 그 연결 정보가 `term_canonical_category_relation.csv`다.

### 5.2 왜 필요한가

이 관계는 Cypher 쿼리로도 만들 수 있다. 하지만 `term_lk`에는 `>`, `>>` 파싱이 들어가고, 복수 카테고리 경로가 존재하므로 전처리에서 펼쳐두는 편이 안전하다.

필요한 이유는 다음과 같다.

1. Neo4j import가 단순해진다.
2. Cypher에서 문자열 파싱 로직을 반복하지 않아도 된다.
3. 복수 카테고리 연결을 명확하게 확인할 수 있다.
4. 원본 `term_lk`에서 어떤 관계가 만들어졌는지 검수할 수 있다.
5. `Term - HAS_CANONICAL_CATEGORY - CanonicalCategory` 관계를 안정적으로 만들 수 있다.

### 5.3 생성 방식

예를 들어 다음 원본이 있다.

```text
term_id = 5626
term_lk = 문화·예술>음악
```

관계 staging은 다음처럼 된다.

```text
5626 -> 문화·예술>음악
```

복수 경로가 있으면 한 용어가 여러 카테고리에 연결된다.

```text
term_lk = 교통·통신>교통시설>>교통·통신>교통로
```

위 값은 다음 두 관계로 펼쳐진다.

```text
Term -> 교통·통신>교통시설
Term -> 교통·통신>교통로
```

### 5.4 Term은 leaf category에만 연결

Term은 가장 구체적인 leaf category에만 직접 연결한다.

예를 들어 다음 경로가 있다.

```text
사회·생활>풍속·의례>매장
```

이때 Term을 `사회·생활`, `풍속·의례`, `매장`에 모두 연결하지 않는다. Term은 `매장`에만 연결하고, 상위 탐색은 `SUBCATEGORY_OF` 관계를 타고 올라가게 한다.

```text
Term -> 매장
매장 -> 풍속·의례
풍속·의례 -> 사회·생활
```

이 방식이 중복 관계를 줄이고, 그래프 탐색 구조도 더 명확하다.

### 5.5 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `term_id` | 용어 ID |
| `category_id` | 연결할 카테고리 ID |
| `category_path` | 사람이 확인하기 위한 카테고리 경로 |
| `source_term_lk` | 원본 `term_lk` 값 |

---

## 6. `source_event_category_dictionary.csv`

### 6.1 역할

`source_event_category_dictionary.csv`는 `itkc_events.csv.subject_category`에서 만든 이벤트 전용 카테고리 사전이다.

이벤트의 `subject_category`는 `history_terms.term_lk`와 성격이 다르다. `term_lk`는 계층형 용어 분류에 가깝고, `subject_category`는 사건 수집 과정에서 붙은 사건 분류에 가깝다.

예시는 다음과 같다.

```text
전쟁
반란
옥사
고변/탄핵
반란,\r\n\r\n정치인
인물기타,\r\n\r\n왜(변)란
```

### 6.2 왜 필요한가

이 사전이 필요한 이유는 다음과 같다.

1. 이벤트 원본 분류를 보존한다.
2. 복합 문자열을 토큰 단위로 정리한다.
3. 이벤트 분류의 빈도와 검수 대상을 확인할 수 있다.
4. `history_terms` 표준 카테고리와 바로 합치지 않고 매핑할 준비를 한다.
5. Event 질의에서 원본 이벤트 분류 기준 필터링을 지원한다.

### 6.3 분리 기준

`subject_category`는 쉼표와 줄바꿈으로 복수값이 들어갈 수 있다.

```text
반란,\r\n\r\n정치인
```

이 값은 다음 두 이벤트 카테고리로 분리한다.

```text
반란
정치인
```

단, `/`는 처음부터 무조건 분리하지 않는다. 예를 들어 `고변/탄핵`은 하나의 분류명일 수 있으므로 원형을 먼저 보존한다.

### 6.4 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `event_category_id` | 이벤트 카테고리 고유 ID |
| `event_category_name` | 분리된 이벤트 카테고리 이름 |
| `event_count` | 해당 카테고리를 가진 사건 수 |
| `source` | 원본 컬럼 |
| `review_status` | 검수 상태 |

---

## 7. `event_source_category_relation.csv`

### 7.1 역할

`event_source_category_relation.csv`는 dictionary가 아니라 staging relation이다. `Event`와 `SourceEventCategory`를 연결하기 위한 파일이다.

### 7.2 쿼리로 만들 수 있는가

가능은 하다. `term_lk`보다 파싱 난도는 낮다. 하지만 다음 이유 때문에 전처리 산출물로 만드는 편이 좋다.

- `subject_category`에 쉼표와 줄바꿈이 섞여 있다.
- 같은 `event_id`가 `event_subject`, `event_period` scope에서 중복 수집되어 있다.
- `detail_url`만 다른 중복 행이 존재한다.
- 이벤트 분류를 어떻게 분리했는지 검수할 수 있어야 한다.

따라서 이 파일은 `staging/`에 두는 것이 좋다.

### 7.3 왜 필요한가

1. `Event - HAS_EVENT_CATEGORY - SourceEventCategory` 관계를 명확히 만든다.
2. `subject_category` 복합값을 여러 관계로 펼친다.
3. 같은 `event_id` 중복을 정리한 뒤 관계를 생성할 수 있다.
4. 원본 `subject_category`를 보존해 검수할 수 있다.
5. 이후 `taxonomy_crosswalk.csv`를 통해 표준 카테고리와 연결할 수 있다.

### 7.4 생성 예시

단일값:

```text
event_id = ITKC_PH_1294A_0435
subject_category = 전쟁

Event -> 전쟁
```

복합값:

```text
subject_category = 반란,\r\n\r\n정치인

Event -> 반란
Event -> 정치인
```

### 7.5 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `event_id` | 사건 ID |
| `event_category_id` | 이벤트 카테고리 ID |
| `event_category_name` | 이벤트 카테고리 이름 |
| `source_subject_category` | 원본 `subject_category` 값 |

---

## 8. `taxonomy_crosswalk.csv`

### 8.1 역할

`taxonomy_crosswalk.csv`는 `source_event_category_dictionary.csv`와 `canonical_category_dictionary.csv`를 연결하는 매핑표다.

중요한 점은 이 파일이 두 사전을 대체하지 않는다는 것이다.

```text
source_event_category_dictionary.csv = 이벤트 원본 분류 사전
canonical_category_dictionary.csv = history_terms.term_lk 기반 표준 카테고리 사전
taxonomy_crosswalk.csv = 두 분류 체계를 연결하는 규칙표
```

### 8.2 왜 필요한가

이벤트 분류와 역사 용어 분류는 같은 체계가 아니다. 따라서 문자열이 비슷하다고 바로 합치면 의미가 섞일 수 있다.

예를 들어 이벤트 카테고리의 `전쟁`은 `history_terms`의 특정 카테고리 경로와 완전히 같은 문자열이 아닐 수 있다. 그러므로 다음처럼 명시적인 매핑표가 필요하다.

```text
전쟁 -> 국방·군사
옥사 -> 정치·행정·법제>사법
국왕 -> 인물
기관 -> 정치·행정·법제>행정>중앙행정기구
```

### 8.3 이 파일이 가능하게 하는 것

1. Event와 Term을 공통 카테고리 기준으로 연결한다.
2. 이벤트 기반 문제와 용어 기반 문제를 같은 분류 체계에서 다룬다.
3. 매핑이 애매한 항목을 검수 대상으로 분리한다.
4. 자동 매핑과 수동 매핑을 구분한다.
5. 원본 분류 체계와 표준 분류 체계를 모두 보존한다.

### 8.4 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `event_category_id` | 이벤트 카테고리 ID |
| `event_category_name` | 이벤트 카테고리 이름 |
| `mapped_category_id` | 표준 카테고리 ID |
| `mapped_category_path` | 표준 카테고리 경로 |
| `mapping_type` | `EXACT`, `PARTIAL`, `MANUAL`, `UNMAPPED` |
| `confidence` | 매핑 신뢰도 |
| `review_status` | 검수 상태 |
| `note` | 비고 |

---

## 9. `period_dictionary.csv`

### 9.1 역할

`period_dictionary.csv`는 시대명과 기간을 정규화하기 위한 사전이다. 최종적으로 `Period` 노드의 기준 목록이 된다.

원본에는 시대 정보가 여러 컬럼에 흩어져 있다.

```text
history_terms.term_times
history_terms.term_year
itkc_events.period
itkc_events.event_date
```

이 값들은 형태가 다르다.

```text
고려
고려전기
조선후기
삼국시대-조선시대
1218년(고종 5) 12월
```

### 9.2 왜 필요한가

1. `Period` 노드의 기준 목록이 된다.
2. 시대명을 표준화한다.
3. `IN_PERIOD` 관계 생성 기준이 된다.
4. 같은 시대 오답 후보를 찾을 수 있다.
5. 연도 범위 검색과 시대 검색을 함께 사용할 수 있다.

### 9.3 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `period_id` | 시대 고유 ID |
| `period_name` | 표준 시대명 |
| `period_level` | 시대 계층 수준 |
| `start_year` | 시작 연도 |
| `end_year` | 종료 연도 |
| `source` | 생성 근거 |
| `review_status` | 검수 상태 |
| `note` | 비고 |

---

## 10. `event_date_parse.csv`

### 10.1 역할

`event_date_parse.csv`는 dictionary가 아니라 날짜 정규화 staging이다. `event_date` 원문에서 연도, 월, 왕대 표현을 뽑아 Event 노드 속성과 Period 연결에 쓰기 위한 파일이다.

예를 들어 다음 원문이 있다.

```text
1218년(고종 5) 12월\r\n~ 1219년(고종 6) 1월
```

여기서 최소한 다음 정보를 뽑을 수 있다.

```text
start_year = 1218
end_year = 1219
start_month = 12
end_month = 1
start_reign_name = 고종
start_reign_year = 5
end_reign_name = 고종
end_reign_year = 6
date_precision = YEAR_MONTH_RANGE
```

### 10.2 지금 해야 하는 이유

날짜 정규화는 나중으로 미루면 Event와 Period 연결이 약해진다. 이 프로젝트에서는 MVP에서도 시대별 사건 검색, 같은 시기 오답 후보, 사건 정렬이 필요하므로 최소 파싱은 지금 하는 것이 좋다.

단, 지금 모든 것을 완벽하게 할 필요는 없다.

1차 목표는 다음 정도다.

- 원문 보존
- 시작 연도 추출
- 종료 연도 추출
- 월 추출
- 왕대 표현 문자열 보존
- 날짜 정밀도 부여
- 파싱 실패 항목 분리

음력/양력 변환이나 왕대 연도 역산은 1차 범위에서 제외해도 된다.

### 10.3 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `event_id` | 사건 ID |
| `date_text` | 원본 날짜 문자열 |
| `start_year` | 시작 연도 |
| `end_year` | 종료 연도 |
| `start_month` | 시작 월 |
| `end_month` | 종료 월 |
| `start_reign_name` | 시작 왕대명 |
| `start_reign_year` | 시작 왕대 연도 |
| `end_reign_name` | 종료 왕대명 |
| `end_reign_year` | 종료 왕대 연도 |
| `date_precision` | 날짜 정밀도 |
| `parse_status` | 파싱 상태 |

---

## 11. `relation_type_dictionary.csv`

### 11.1 역할

`relation_type_dictionary.csv`는 `itkc_person_relations.csv.relation_type`을 그래프에서 쓸 수 있는 의미 규칙으로 바꾸는 사전이다.

원본 관계 유형은 다음처럼 짧은 문자열이다.

```text
형제, 자, 부, 조부, 장인, 사위, 증조부, 교유, 스승, 제자, 생부, 출자, 아내, 남편, 모, 생모
```

사람은 `부`를 보면 아버지라는 것을 알지만, 코드와 DB는 다음 판단을 자동으로 알 수 없다.

- 관계 방향이 `person_id -> related_person_id`인지
- 대칭 관계인지
- 역관계가 무엇인지
- 가족 관계인지 사회 관계인지
- 동세대 관계인지 윗세대/아랫세대 관계인지
- 문제 생성이나 챗봇에서 어떤 그룹으로 묶어야 하는지

따라서 relation type 사전이 필요하다.

### 11.2 왜 필요한가

이 사전이 없으면 전처리, Neo4j import, 챗봇 쿼리, 문제 생성 로직에서 같은 판단을 반복해야 한다.

예를 들어 코드 곳곳에 다음과 같은 조건이 늘어난다.

```text
부이면 아버지
자이면 자식
형제이면 대칭 관계
교유이면 사회 관계
스승이면 사제 관계
```

이런 판단을 코드에 하드코딩하면 수정과 검수가 어려워진다. 사전으로 분리하면 원본 관계값을 표준 의미로 조인해서 사용할 수 있다.

### 11.3 관계 방향과 대칭성

예시는 다음과 같다.

| raw_relation_type | normalized_relation_type | neo4j_rel_type | relation_group | direction_rule | is_symmetric | inverse_relation_type |
|---|---|---|---|---|---|---|
| `부` | `HAS_FATHER` | `HAS_FATHER` | `FAMILY_PARENT` | `person_to_related` | `N` | `HAS_CHILD` |
| `자` | `HAS_CHILD` | `HAS_CHILD` | `FAMILY_CHILD` | `person_to_related` | `N` | `HAS_PARENT` |
| `형제` | `SIBLING_OF` | `SIBLING_OF` | `FAMILY_SIBLING` | `undirected` | `Y` | `SIBLING_OF` |
| `교유` | `ASSOCIATED_WITH` | `ASSOCIATED_WITH` | `SOCIAL` | `undirected` | `Y` | `ASSOCIATED_WITH` |
| `스승` | `HAS_TEACHER` | `HAS_TEACHER` | `SOCIAL_TEACHER` | `person_to_related` | `N` | `HAS_STUDENT` |
| `제자` | `HAS_STUDENT` | `HAS_STUDENT` | `SOCIAL_STUDENT` | `person_to_related` | `N` | `HAS_TEACHER` |
| `아내` | `HAS_WIFE` | `HAS_WIFE` | `SPOUSE` | `person_to_related` | `N` | `HAS_HUSBAND` |
| `남편` | `HAS_HUSBAND` | `HAS_HUSBAND` | `SPOUSE` | `person_to_related` | `N` | `HAS_WIFE` |

`HAS_FATHER`와 `SIBLING_OF`는 반대 관계가 아니다. `HAS_FATHER`는 세대 관계이고, `SIBLING_OF`는 동세대 관계다.

반대 관계 예시는 다음과 같다.

```text
HAS_FATHER <-> HAS_CHILD
HAS_TEACHER <-> HAS_STUDENT
HAS_WIFE <-> HAS_HUSBAND
SIBLING_OF <-> SIBLING_OF
```

### 11.4 Neo4j 적용 방식

초기 MVP는 아래처럼 `RELATED_TO` 하나와 속성으로 정규화 의미를 유지했다.
이 예시는 **과거 스냅샷**이며 현재 적재 정책은 5.2의 typed edge 방식이다.

```text
(:Person)-[:RELATED_TO {
  raw_relation_type: "부",
  normalized_relation_type: "HAS_FATHER",
  relation_group: "FAMILY_PARENT",
  is_symmetric: false,
  inverse_relation_type: "HAS_CHILD"
}]->(:Person)
```

현재는 같은 원천 row가 다음처럼 적재된다.

```text
(:Person)-[:HAS_FATHER {
  relation_id: "...",
  raw_relation_type: "부",
  normalized_relation_type: "HAS_FATHER",
  relation_group: "FAMILY_PARENT",
  is_symmetric: false,
  inverse_relation_type: "HAS_CHILD",
  evidence_url: "..."
}]->(:Person)
```

정규화 유형이 승인 목록에 없을 때만 `RELATED_TO`로 적재하며 QA가 이를 검출한다.
실제 import는 Neo4j 5.26의 동적 타입 문법
`MERGE (start)-[r:$(row.relation_type)]->(target)`을 사용하므로 관계 유형마다 Cypher
LOAD 블록을 복제하지 않는다. seed의 `neo4j_rel_type`과 CSV `relation_type`이 어긋나면
사전 생성 또는 preload QA에서 실패한다.
이렇게 하면 다음 질의가 관계 타입 패턴으로 명확해진다.

- 가족 관계만 찾기
- 형제 같은 동세대 관계 찾기
- 교유 같은 사회 관계 찾기
- 스승/제자 관계 찾기
- 윗세대/아랫세대 관계 구분하기

---

## 12. `source_url_dictionary.csv`

### 12.1 역할

`source_url_dictionary.csv`는 RAG와 출처 추적을 위한 URL 사전이다.

현재 URL 사전 대상은 다음 네 가지다.

```text
events.source_urls
event_relations.source_urls
person_relations.detail_url
source_images.related_content에서 파싱한 URL
```

`person_relations.evidence_url`은 URL 사전에 넣지 않고 typed 인물 관계 또는 catch-all
`RELATED_TO`의 `evidence_url` 속성으로 보존한다. 인물 관계 근거 URL을 `SourceUrl`
노드로 승격하면 같은 URL 하나가 많은 인물 관계를 묶는 허브가 될 수 있기 때문이다.

URL은 그래프 구조 자체에는 필수는 아니지만, 답변의 근거와 RAG 품질에는 중요하다.

### 12.2 왜 필요한가

1. 사건 URL, 인물 상세 URL, 이미지 관련 콘텐츠 URL의 중복을 제거한다.
2. URL이 어떤 테이블과 컬럼에서 왔는지 추적한다.
3. Tavily extract 대상 URL queue로 쓸 수 있다.
4. `Evidence`, `Source`, `DocumentChunk` 확장에 사용할 수 있다.
5. 챗봇 답변에 출처 URL을 붙일 수 있다.

### 12.3 Hybrid RAG에서의 역할

최종 RAG 구조는 다음처럼 볼 수 있다.

```text
Neo4j = 사실 관계의 뼈대
Vector index = 설명문과 URL 본문 검색
Tavily = URL 본문 추출과 외부 웹 근거 보강
LLM = 그래프 결과와 문서 근거를 종합한 답변 생성
```

Tavily는 그래프의 확정 관계를 만드는 주 데이터가 아니라, URL 기반 근거 수집과 답변 보강 레이어로 둔다.

### 12.4 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `source_url_id` | URL 고유 ID |
| `url` | 원본 URL |
| `source_tables` | URL이 나온 테이블 목록 |
| `source_columns` | URL이 나온 컬럼 목록 |
| `source_types` | EVENT_DETAIL, EVENT_RELATION_DETAIL, PERSON_DETAIL 같은 URL 출처 유형 |
| `source_count` | 같은 URL이 수집 대상 컬럼에서 등장한 횟수 |
| `use_for_rag` | RAG 수집 대상 여부 |
| `fetch_status` | Tavily 수집 상태 |
| `note` | 수집/검수 메모 |

---


## 15. 2026-07-03 정리 기준

### 15.1 dictionary와 mapping 분리

`dictionary/`에는 노드의 기준표가 되는 CSV만 둔다.

예시는 다음과 같다.

- `canonical_category_dictionary.csv`
- `source_event_category_dictionary.csv`
- `period_dictionary.csv`
- `relation_type_dictionary.csv`
- `source_url_dictionary.csv`
- `event_facet_dictionary.csv`
- `country_dictionary.csv`
- `region_dictionary.csv`
- `economic_domain_dictionary.csv`
- `taxonomy_facet_dictionary.csv`

`mapping/`에는 서로 다른 기준표를 연결하는 crosswalk CSV를 둔다.

예시는 다음과 같다.

- `taxonomy_crosswalk.csv`
- `source_event_category_facet_crosswalk.csv`
- `canonical_category_country_crosswalk.csv`
- `canonical_category_region_crosswalk.csv`
- `canonical_category_economic_domain_crosswalk.csv`
- `canonical_category_taxonomy_facet_crosswalk.csv`

이렇게 나누는 이유는 사전과 매핑표의 책임이 다르기 때문이다.

사전은 그래프에 들어갈 노드 후보를 정의한다. 매핑표는 이미 만들어진 노드 후보 사이의 연결 규칙을 정의한다. 두 종류가 같은 폴더에 있으면 파일 수가 많아 보이고, 어떤 파일을 검수해야 하는지 판단하기 어려워진다.

### 15.2 국가와 지역은 카테고리 하위 개념이 아니다

원본 `history_terms.term_lk`에는 다음과 같은 경로가 존재한다.

```text
외교·국제관계 > 러시아 > 경제·산업(러시아)
외교·국제관계 > 기타지역 > 동남아시아
```

이 경로는 원본 분류 체계에서 사용한 탐색 경로다. 하지만 Neo4j 의미 그래프에서는 `러시아`, `기타지역`, `동남아시아`를 `외교·국제관계`의 개념적 하위 카테고리로 해석하면 안 된다.

따라서 원본 경로는 `canonical_category_dictionary.csv`와 staging relation에 보존하되, 최종 그래프의 `SUBCATEGORY_OF` 관계에서는 국가/지역 facet으로 분리된 경로를 제외한다.

의미 그래프에서는 다음 관계를 사용한다.

```text
CanonicalCategory -[:ABOUT_COUNTRY]-> Country
CanonicalCategory -[:ABOUT_REGION]-> Region
Term -[:ABOUT_COUNTRY]-> Country
Term -[:ABOUT_REGION]-> Region
Event -[:ABOUT_COUNTRY]-> Country
Event -[:ABOUT_REGION]-> Region
```

즉, `러시아`는 `외교·국제관계`의 하위 카테고리가 아니라 해당 카테고리 경로가 다루는 국가 facet이다. `기타지역`은 실제 국가가 아니라 원본 taxonomy의 지역 묶음 버킷이며, `동남아시아`, `아메리카`, `유럽` 등은 별도 `Region` 노드로 다룬다.

### 15.3 시대 범위는 전처리에서 확장한다

`history_terms.term_times`에는 다음처럼 시작 시대와 끝 시대가 하나의 문자열로 들어간 경우가 있다.

```text
삼국시대-조선시대
고려후기-조선후기
개항기-현대
```

이 값은 쿼리 시점에 매번 해석하지 않는다. Cypher에서 문자열을 다시 파싱하면 쿼리가 복잡해지고, 시대 순서 기준이 여러 곳에 흩어진다.

따라서 시대 순서와 범위 확장 기준은 `seed/period_seed.csv`에 두고, 최종 관계 CSV를 만들 때 `term_in_period.csv`, `event_in_period.csv`에 미리 펼쳐 저장한다.

예를 들어 다음 원문은:

```text
삼국시대-조선시대
```

다음 관계로 확장된다.

| period_name | match_type |
|---|---|
| 삼국시대 | `RANGE_START` |
| 남북국시대 | `RANGE_MIDDLE` |
| 후삼국시대 | `RANGE_MIDDLE` |
| 고려시대 | `RANGE_MIDDLE` |
| 조선시대 | `RANGE_END` |

`match_type`은 다음 의미를 가진다.

| match_type | 의미 |
|---|---|
| `DIRECT` | 원문에 단일 시대가 직접 적힌 경우 |
| `RANGE_START` | 범위 표현의 시작 시대 |
| `RANGE_MIDDLE` | seed의 시대 순서로 추론한 중간 시대 |
| `RANGE_END` | 범위 표현의 끝 시대 |

범위 확장은 `range_group`이 같은 시대끼리만 수행한다. 예를 들어 `korean_major_period`는 `삼국시대`, `남북국시대`, `고려시대`, `조선시대` 같은 주요 한국사 시대를 확장하고, `archaeological_period`는 `구석기시대`, `신석기시대`, `청동기시대`, `초기철기시대`를 별도 순서로 확장한다.

이렇게 하면 조회 쿼리는 단순해진다.

```cypher
MATCH (t:Term)-[r:IN_PERIOD]->(p:Period {name: "고려시대"})
RETURN t, r.match_type
```

쿼리 결과에서 `match_type`을 보면 원문에 직접 있던 시대인지, 범위에서 추론된 중간 시대인지 구분할 수 있다.

---


## 15. 이벤트 분류와 용어 카테고리를 바로 합치지 않는 이유

`history_terms.term_lk`와 `events.subject_category`는 모두 카테고리처럼 보이지만 같은 성격의 데이터가 아니다.

`term_lk`는 시소러스 기반 계층형 분류다.

```text
정치·행정·법제>행정>중앙행정기구
문화·예술>음악
```

`>`는 depth를 뜻하고, `>>`는 한 용어가 여러 분류 경로에 속한다는 뜻이다. 그래서 `CanonicalCategory`와 `SUBCATEGORY_OF` 계층을 만들 수 있다.

반면 `events.subject_category`는 사건 수집 과정에서 붙은 평면 분류다.

```text
전쟁
반란
정치인
```

계층이 없고, 쉼표나 줄바꿈으로 복수값이 섞여 있으며, `term_lk`와 같은 명명 규칙을 쓰지 않는다. 따라서 `전쟁 = 국방·군사`처럼 의미상 연결되는 경우도 문자열만으로는 안전하게 합칠 수 없다.

그래서 다음 사전과 매핑을 둔다.

| 파일 | 필요한 이유 |
|---|---|
| `source_event_category_dictionary.csv` | 이벤트 원본 분류를 손실 없이 보존 |
| `canonical_category_dictionary.csv` | 용어집 기반 표준 카테고리 기준점 |
| `taxonomy_crosswalk.csv` | 서로 다른 두 분류 체계를 연결하는 검수 가능한 매핑표 |

이 방식은 원본 보존과 표준 검색을 동시에 만족한다. 매핑이 틀렸다면 원본 데이터를 고치지 않고 `taxonomy_crosswalk_seed.csv`만 수정하면 된다.


## 16. EventFacet과 SearchTag가 겹쳐도 둘 다 필요한 이유

`EventFacet`과 `SearchTag`에는 `전쟁`, `정치`, `제도`처럼 겹치는 값이 있을 수 있다. 하지만 둘의 목적은 다르다.

| 구분 | 목적 |
|---|---|
| `EventFacet` | 사건의 의미 성격을 정규화한 축 |
| `SearchTag` | 검색 쿼리를 단순하게 만들기 위한 통합 태그 축 |

`EventFacet`은 의미 모델이다. “이 사건은 전쟁 성격이다”처럼 사건 분류를 재분류한다.

`SearchTag`는 검색 최적화 레이어다. 사용자가 “전쟁 관련 항목”을 찾을 때 용어/사건/인물 이름, 원본 분류, 표준 카테고리, facet, 시대, 주제, 국가, 지역, taxonomy facet을 모두 `OR`로 탐색하면 쿼리가 길어진다. `SearchTag`를 두면 다음처럼 한 관계로 조회할 수 있다.

```cypher
MATCH (n)-[:HAS_SEARCH_TAG]->(:SearchTag {tag_name: "전쟁"})
RETURN DISTINCT n
```

대신 태그가 어디서 왔는지 잃지 않도록 `HAS_SEARCH_TAG` 관계에 `source_node_type`, `source_node_id`, `source_relation`, `source_detail`을 남긴다.
Person 별칭은 `source_node_type=PersonAlias`, `source_relation=person_alias`로 분리하고, Person이 Event/Term 태그를 상속받은 경우 `source_detail`에 원천 `event_id` 또는 `term_id` 묶음을 보존한다.
같은 노드가 여러 태그 출처로 조회될 수 있으므로 SearchTag 조회 결과는 `RETURN DISTINCT`로 받는다.

정리하면 다음과 같다.

- 정밀한 의미 분석: `SourceEventCategory`, `CanonicalCategory`, `EventFacet`
- 빠른 키워드 검색: `SearchTag`
- 매핑 기준 수정: seed/crosswalk 수정 후 CSV 재생성



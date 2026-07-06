# Term-Person 동명이인 검수 흐름

`term_person_review_approved.csv`는 검수 후보 목록이 아니라 검수 완료된 수동 승인 seed이다.
파일이 비어 있으면 아직 수동 승인된 Term-Person 연결이 없다는 뜻이다.

## Seed 파일의 역할

`etl/preprocessing/neo4j/seed` 아래 파일들은 원천 CSV에서 자동으로 안정적으로 알기 어려운 기준을 사람이 정리해 둔 입력값이다.
최종 graph CSV가 아니라, dictionary/mapping/staging/import CSV를 만들 때 참고하는 검수 기준표로 보면 된다.

Seed 파일을 만드는 이유는 다음과 같다.

- 원천 데이터의 표기가 흔들리는 값을 하나의 기준으로 정규화한다.
- 시대, 주제, 지역, 관계 유형처럼 검색/그래프에서 반복해서 쓰는 기준 노드를 고정한다.
- 자동 매칭이 위험한 동명이인, 분류 매핑, 시대 보정은 사람이 검수한 값만 반영한다.
- 데이터 재생성 시 같은 기준을 반복 적용해서 결과가 흔들리지 않게 한다.

| 파일 | 용도 | 왜 필요한가 |
| --- | --- | --- |
| `period_seed.csv` | Period 노드의 순서, 시작/종료 연도, 상위 period 기준 | 원천의 시대 문자열만으로는 정렬과 기간 계산이 어렵기 때문 |
| `reign_seed.csv` | 왕대/연호의 시작/종료 연도 기준 | `세종 28년` 같은 표현을 실제 연도로 보정하기 위해 필요 |
| `relation_type_seed.csv` | 인물 관계 원문 유형을 표준 관계 유형으로 정규화 | 부자, 모자, 배우자 같은 표현을 일관된 관계로 만들기 위해 필요 |
| `taxonomy_crosswalk_seed.csv` | 사건 카테고리를 canonical category로 매핑 | 자동 분류가 틀릴 수 있는 사건 분류를 검수 기준으로 보정 |
| `event_facet_seed.csv` | 사건 카테고리를 facet으로 분류 | 전쟁, 정치, 경제 같은 사건 속성 검색을 만들기 위해 필요 |
| `country_seed.csv` | 국가 사전과 aliases 기준 | 카테고리/용어를 국가 노드에 안정적으로 연결하기 위해 필요 |
| `region_seed.csv` | 지역 사전, 상하위 지역, aliases 기준 | 지역 검색과 지역 그래프 연결을 만들기 위해 필요 |
| `category_axis_seed.csv` | category path에서 국가/경제 도메인 등 축을 읽는 기준 | 카테고리 트리의 몇 번째 depth를 어떤 축으로 볼지 고정하기 위해 필요 |
| `theme_seed.csv` | Theme 노드 기준 | 문화, 정치, 경제 같은 주제 노드를 고정하기 위해 필요 |
| `category_theme_seed.csv` | canonical category를 Theme에 연결 | 문화재/서명처럼 주제로 묶어야 하는 카테고리를 보정하기 위해 필요 |
| `era_seed.csv` | Era 노드 기준 | 고조선, 삼국시대, 고려, 조선 같은 큰 시대 구분을 고정하기 위해 필요 |
| `period_era_seed.csv` | Period를 Era에 연결 | `후삼국시대 -> 남북국시대`처럼 세부 시대를 큰 시대에 묶기 위해 필요 |
| `keyword_era_seed.csv` | period 정보만으로 안 잡히는 용어의 Era override | 시험 키워드나 대표 용어를 특정 시대에 직접 보정하기 위해 필요 |
| `entity_type_seed.csv` | category root를 entity type으로 매핑 | Term/Person/Event 외에 문화재, 지명, 문헌 같은 타입 필터를 만들기 위해 필요 |
| `term_person_review_approved.csv` | 검수 완료된 Term-Person 수동 승인 연결 | 동명이인은 자동 연결이 위험하므로 승인된 `term_id`, `person_id`만 반영하기 위해 필요 |

Seed 파일을 수정한 뒤에는 해당 seed를 읽는 전처리 스크립트를 다시 실행해야 한다.
예를 들어 `term_person_review_approved.csv`를 수정하면 `make_graph_csv.py --save`를 다시 실행해야 하고,
`theme_seed.csv`, `category_theme_seed.csv`, `era_seed.csv`, `period_era_seed.csv`, `keyword_era_seed.csv`를 수정하면 `make_theme_era_csv.py --save`를 다시 실행해야 한다.

## 직접 검수가 필요한 이유

동명이인과 수동 seed는 자동 매칭 결과를 그대로 믿으면 안 된다.
그래프 관계는 한 번 생성되면 검색 순위, 관련 노드 확장, 그래프 시각화, 질의 응답 근거에 계속 사용되기 때문이다.
잘못된 한 개의 연결이 여러 화면에서 "관련 있음"처럼 보일 수 있으므로, 애매한 관계는 사람이 확인한 뒤 승인해야 한다.

주요 이유는 다음과 같다.

| 위험 | 설명 | 검수에서 확인할 것 |
| --- | --- | --- |
| 한글 이름이 같지만 한자가 다름 | `의조`, `정조`, `이순신`, `김치`처럼 한글 이름만 같고 실제 인물이 다른 경우가 있다. | `term_hanja`와 `person_hanja`가 둘 다 있으면 반드시 일치해야 한다. |
| 왕호/묘호가 여러 국가와 시대에서 반복됨 | `태조`, `태종`, `세조`, `인종`, `숙종` 같은 왕호는 조선만의 이름이 아니다. | 설명의 왕조, 시대, 생몰년, 관련 사건이 Person과 맞는지 본다. |
| 인명과 일반 용어가 섞임 | `선조`는 조선 왕 선조일 수도 있고 일반 명사 `조상` 의미일 수도 있다. | 설명 문맥이 실제 인물을 가리키는지 확인한다. |
| 관직, 시호, 호, 별칭이 사람 이름처럼 보임 | 어떤 용어는 인물명이 아니라 관직명, 책 이름, 사건명, 단체명일 수 있다. | Term의 category, description, remark를 함께 본다. |
| 설명이 짧거나 원천 데이터가 불완전함 | 원천 설명이 짧으면 자동 로직이 시대나 인물 단서를 충분히 알 수 없다. | 불확실하면 승인하지 않고 `note`에 사유를 남긴다. |
| 자동 점수는 대표성을 모름 | 동명이인 중 더 유명한 사람이 있어도 단순 문자열 매칭은 대표 인물을 알지 못한다. | 검색에서 기대하는 대표 인물인지가 아니라, 해당 Term이 실제로 그 Person인지 판단한다. |
| 잘못된 관계가 그래프 전체로 전파됨 | `REFERS_TO`, `MENTIONS_PERSON`, `HAS_THEME`, `IN_ERA` 관계는 관련 노드 조회와 검색 결과에 영향을 준다. | 승인 seed에는 확실한 연결만 넣는다. |

LLM이나 자동 규칙은 후보를 줄이는 데는 쓸 수 있지만, 최종 seed를 대신 확정하면 안 된다.
LLM은 문맥을 그럴듯하게 추론할 수 있지만 원천 데이터의 한자, 생몰년, 시대 기준과 충돌하는 경우를 보장해서 막지 못한다.
따라서 자동화는 `staging` 후보 생성까지만 담당하고, `seed`에 들어가는 값은 사람이 검수한 결정으로 유지한다.

검수 원칙은 보수적으로 잡는다.

- 확실히 같은 인물인 경우만 `APPROVED`로 기록한다.
- 한자가 다르면 승인하지 않는다.
- 한자가 없으면 설명, 시대, 생몰년, 관련 사건이 함께 맞을 때만 승인한다.
- 후보가 유명 인물처럼 보여도 해당 Term 설명이 그 사람을 말하지 않으면 승인하지 않는다.
- 판단 근거가 애매하면 승인하지 않고 `note`에 보류 사유를 남긴다.

## 1. 검수 후보 CSV 생성

동명이인 후보는 아래 명령으로 생성한다.

```powershell
.venv\Scripts\python.exe etl/preprocessing/neo4j/scripts/make_term_person_review.py --save
```

생성 파일:

```text
etl/preprocessing/neo4j/staging/term_person_review.csv
```

이 파일은 후보 검수용이다. 모든 행을 그대로 승인 seed에 넣으면 안 된다.
후보 생성 단계에서는 `person_hanja`와 `term_hanja`가 모두 있고 두 값이 같은 행만 남긴다.
한자가 없는 후보나 한자가 서로 다른 후보는 검수 CSV에 포함하지 않는다.
그래도 연호, 국가명, 작품명처럼 사람이 아닌 Term이 Person 후보와 같은 한자를 갖는 경우가 남을 수 있으므로 직접 검수는 필요하다.

## 2. 후보 판단 기준

`term_person_review.csv`에서 ID만 보고 판단하지 않는다.
아래 컬럼을 함께 보고 실제 같은 인물인지 확인한다.

- `name`: Term 이름과 Person 이름
- `term_hanja`, `person_hanja`: 둘 다 있으면 한자 일치 여부를 우선 확인
- `term_desc_preview`: Term 설명이 해당 인물을 가리키는지 확인
- `birth_year`, `death_year`: 설명의 시대와 생몰년이 맞는지 확인
- `term_id`, `person_id`: 승인 결과를 기록하기 위한 식별자

한자가 다르면 연결하지 않는다.
설명이나 생몰년으로도 같은 인물인지 판단할 수 없으면 승인하지 않는다.

## 3. 승인 seed 작성

검수 후 맞는 연결만 아래 파일에 기록한다.

```text
etl/preprocessing/neo4j/seed/term_person_review_approved.csv
```

필수 컬럼:

```csv
term_id,person_id,review_status,note
```

예시:

```csv
term_id,person_id,review_status,note
12345,P000001,APPROVED,한자와 설명 일치
```

`review_status`는 `APPROVED` 또는 `AUTO_APPROVED`만 그래프 생성에 반영된다.

## 4. 그래프 CSV 재생성

승인 seed를 수정한 뒤 graph import CSV를 다시 만든다.

```powershell
.venv\Scripts\python.exe etl/preprocessing/neo4j/scripts/make_graph_csv.py --save
```

반영 대상:

```text
storage/neo4j/neo4j_import/relations/term_refers_to_person.csv
```

이 단계는 CSV 재생성만 수행한다.
Neo4j DB에 실제 반영하려면 별도 DB 재적재가 필요하다.

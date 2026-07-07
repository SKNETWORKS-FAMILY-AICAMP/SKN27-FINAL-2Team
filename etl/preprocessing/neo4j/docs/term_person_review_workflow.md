# Term-Person 동명이인 검수 흐름

검수 후보 CSV는 `etl/preprocessing/neo4j/staging/term_person_review.csv` 하나만 사용한다.
Term-Person 연결 검토 후보는 같은 파일 안에서 `review_type` 값으로 구분한다.
별도 `staging/person_duplicate_review.csv`는 공식 검수 산출물이 아니다.
이 파일이 과거 실행 결과로 남아 있으면 삭제해도 되며, `make_graph_csv.py`는 이 staging 파일을 읽지 않는다.

공식 승인 seed는 `term_person_review_approved.csv` 하나만 사용한다.
`person_duplicate_review_approved.csv`는 공식 graph 생성 흐름에서 사용하지 않는다.
`term_person_review_approved.csv`가 비어 있으면 아직 수동 승인된 Term-Person 연결이 없다는 뜻이다.

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

## Person 중복 검수 흐름

같은 이름과 한자를 가진 Person ID가 여러 개일 때도 공식 검수는 `term_person_review.csv` 하나에서 한다.
별도 `person_duplicate_review.csv` 후보 파일을 만들지 않는다.
과거 보조 스크립트나 수동 실행으로 `staging/person_duplicate_review.csv`가 생겨도 공식 검수 대상은 아니다.
검수는 `make_term_person_review.py --save`로 다시 만든 `staging/term_person_review.csv`에서 진행한다.

- `TERM_PERSON`: 같은 이름을 가진 여러 Person 중 특정 Term이 어느 Person을 가리키는지 고르는 후보
- `PERSON_DUPLICATE`: 같은 `term_id`, `name`, `term_hanja`, `term_desc_preview`에 서로 다른 `person_id`가 붙어 있어 추가 판단이 필요한 후보

예를 들어 같은 `강로(姜㳣)` Term 설명에 `P000159`, `P000160`이 모두 붙으면 `PERSON_DUPLICATE`로 표시된다.
이 경우에는 Term 설명이 여러 Person 중 누구를 가리키는지 판단한다.
공식 흐름에서는 Person ID를 서로 병합하지 않는다.
Term이 특정 Person을 가리키는 것이 확실할 때만 `term_person_review_approved.csv`에 연결 승인으로 기록한다.

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
후보 생성 단계에서는 `person_hanja`와 `term_hanja`가 모두 있고 두 값이 같은 행만 1차 후보로 본다.
단, 이름과 한자만 같다고 같은 인물로 처리하지 않는다.
Person 관계망의 관련 인물 이름/한자 단서가 Term 설명에 실제로 등장하는 후보만 검토 후보로 남긴다.
예를 들어 Term 설명에 부, 배우자, 스승, 제자 등 관계 인물이 나오고 그 인물이 해당 Person 관계망에도 있으면 설명 근거가 있는 후보로 본다.
Term의 시대 범위와 Person 생몰년이 숫자로 명백히 겹치지 않으면 검토 후보에서 제외한다.
Term의 `start_year`, `end_year`와 Person의 `birth_year`, `death_year`가 숫자로 완전히 같은 후보가 해당 Term에서 1명뿐이어도, Term 설명에서 Person 관계망 단서가 함께 확인될 때만 graph 생성 단계에서 자동 `REFERS_TO` 관계로 붙인다.
정확한 연도와 설명 근거가 함께 확인되지 않는 후보는 자동 연결하지 않고, 설명 근거가 있는 경우에만 `term_person_review.csv`에 `PENDING` 검토 후보로 남긴다.
이때 Term 설명이나 연도 단서에서 생몰년을 추론해 채우지 않는다.
원천 Person 생몰년이 비어 있으면 `birth_year`, `death_year` 출력값은 빈 값으로 둔다.
원천에 `14??`, `?`, `1745(1730)`처럼 부분/불확실 연도가 들어 있으면 임의로 고치거나 지우지 않고 그대로 표시한다.
검토자가 비교할 수 있도록 Term의 원천 연도(`term_year_text`, `term_start_year`, `term_end_year`)와 Person 생몰년을 함께 표시한다.
그래도 연호, 국가명, 작품명처럼 사람이 아닌 Term이 Person 후보와 같은 한자를 갖는 경우가 남을 수 있으므로 직접 검수는 필요하다.

## 2. `term_person_review.csv` 컬럼 값

| 컬럼 | 들어갈 수 있는 값 | 의미 |
| --- | --- | --- |
| `review_type` | `TERM_PERSON`, `PERSON_DUPLICATE` | 검수 유형. `TERM_PERSON`은 Term이 어떤 Person을 가리키는지 고르는 후보이고, `PERSON_DUPLICATE`는 같은 Term 설명에 여러 Person ID가 붙어 추가 선택이 필요한 후보이다. |
| `name` | 한글 이름 문자열 | Term 이름과 Person 기본 이름이 같은 값이다. 빈 값은 후보에서 제외된다. |
| `term_id` | Term 식별자 | `terms.csv`의 `term_id` 값이다. 승인 seed에 그대로 사용한다. |
| `term_hanja` | 한자 문자열 | Term의 한자 값이다. 생성 후보에서는 비어 있지 않고 `person_hanja`와 같은 행만 남긴다. |
| `term_desc_preview` | Term 설명 앞 50자, 또는 빈 값 | 사람이 설명 문맥을 빠르게 확인하기 위한 미리보기이다. 원본 설명이 비어 있으면 빈 값일 수 있다. |
| `term_year_text` | 원천 Term 연도 문자열, 또는 빈 값 | `terms.csv`의 `year_text` 값이다. Person 생몰년과 비교하기 위한 검수 근거이다. |
| `term_start_year` | 파싱된 시작 연도, 또는 빈 값 | `terms.csv`의 `start_year` 값이다. 자동 연결은 이 값과 `birth_year`가 숫자로 같을 때만 가능하다. |
| `term_end_year` | 파싱된 종료 연도, 또는 빈 값 | `terms.csv`의 `end_year` 값이다. 자동 연결은 이 값과 `death_year`가 숫자로 같을 때만 가능하다. |
| `person_id` | Person 식별자 | `people.csv`의 `person_id` 값이다. Term-Person 연결 승인 seed에 사용한다. |
| `person_name` | 한글 이름 문자열 | Person 이름에서 추출한 기본 이름이다. 보통 `name`과 같다. |
| `person_hanja` | 한자 문자열 | Person의 한자 값이다. 생성 후보에서는 비어 있지 않고 `term_hanja`와 같은 행만 남긴다. |
| `birth_year` | 원천 연도 문자열, 또는 빈 값 | 원천 Person의 출생 연도이다. 원천이 비어 있으면 빈 값으로 두고, `14??` 같은 부분 연도는 그대로 표시한다. |
| `death_year` | 원천 연도 문자열, 또는 빈 값 | 원천 Person의 사망 연도이다. 원천이 비어 있으면 빈 값으로 두고, `?` 같은 불확실 표기도 원천값이면 그대로 표시한다. |
| `review_status` | 생성 직후 `PENDING` | staging 후보의 기본 상태이다. graph 반영 여부는 approved seed 파일의 `review_status`가 결정한다. |
| `note` | 빈 값, 또는 검수 메모 | 생성 직후에는 빈 값이다. 보류/승인 판단 근거를 남길 때 사용한다. |

## 3. 후보 판단 기준

`term_person_review.csv`에서 ID만 보고 판단하지 않는다.
아래 컬럼을 함께 보고 실제 같은 인물인지 확인한다.

- `review_type`: 단일 후보인지 여러 Person 중 선택이 필요한 후보인지 먼저 확인
- `name`: Term 이름과 Person 이름
- `term_hanja`, `person_hanja`: 둘 다 있으면 한자 일치 여부를 우선 확인
- `term_desc_preview`: Term 설명이 해당 인물을 가리키는지 확인
- `birth_year`, `death_year`: 설명의 시대와 생몰년이 맞는지 확인
- `term_id`, `person_id`: Term-Person 연결 승인에 필요한 식별자

한자가 다르면 연결하지 않는다.
설명이나 생몰년으로도 같은 인물인지 판단할 수 없으면 승인하지 않는다.

## 4. 승인 seed 작성

`term_person_review.csv`는 재생성되는 staging 후보 파일이므로 검수 결과를 여기에 직접 누적하지 않는다.
staging의 `review_status`와 `note`는 후보 상태와 참고용 컬럼이며, graph 반영 여부는 seed 파일이 결정한다.
검수 결과는 `term_person_review_approved.csv`에 새 행으로 기록한다.
Person ID 병합 seed는 공식 graph 생성 흐름에서 사용하지 않는다.

| 판단 결과 | 기록할 seed | 필요한 컬럼 |
| --- | --- | --- |
| 이 Term이 이 Person을 가리키는 것이 확실함 | `term_person_review_approved.csv` | `term_id`, `person_id`, `review_status`, `note` |
| 동명이인이거나 판단 불가 | 기록하지 않음 | seed에 넣지 않는다. 필요하면 별도 작업 메모에 보류 사유만 남긴다. |

### 4.0 실제 검토 순서

1. `etl/preprocessing/neo4j/staging/term_person_review.csv`를 연다.
2. `review_status`가 `PENDING`인 행을 본다.
3. `PERSON_DUPLICATE`는 같은 `term_id`, `name`, `term_hanja`, `term_desc_preview`끼리 묶어서 여러 `person_id` 중 어느 쪽이 맞는지 비교한다.
4. `TERM_PERSON`은 같은 `name` 또는 같은 `person_id`가 다른 후보에도 반복되는지 보고, 반복 맥락 안에서 어느 연결이 맞는지 비교한다.
5. 먼저 `term_year_text`, `term_start_year`, `term_end_year`와 `birth_year`, `death_year`가 맞는지 본다.
6. 연도가 비어 있거나 `14??`, `?`, `1745(1730)`처럼 불확실하면 추론해서 채우지 않고 설명/한자/문맥으로만 판단한다.
7. 연결이 확실하면 `term_person_review_approved.csv`에 `term_id`, `person_id`, `APPROVED`, 근거 `note`를 기록한다.
8. 같은 이름/한자를 가진 여러 Person이 실제 같은 사람처럼 보여도 이 공식 흐름에서는 Person ID 병합을 기록하지 않는다.
9. 동명이인인데 Term이 누구를 말하는지 모르거나 근거가 부족하면 어떤 seed에도 기록하지 않는다.

### 4.1 Term-Person 연결 승인

검수 후 Term이 특정 Person을 가리키는 것이 확실한 연결만 아래 파일에 기록한다.
`review_type=TERM_PERSON` 행뿐 아니라, `review_type=PERSON_DUPLICATE` 행에서 동명이인 중 올바른 Person을 고른 경우도 이 seed에 기록할 수 있다.

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
`PENDING`, `REJECTED`, 빈 값은 반영되지 않는다.

review type이 달라도 승인 seed에 쓰는 형식은 같다.
차이는 "무엇을 결정했는가"뿐이다.

| `term_person_review.csv`의 `review_type` | 검수자가 결정할 것 | `term_person_review_approved.csv`에 쓰는 행 |
| --- | --- | --- |
| `PERSON_DUPLICATE` | 같은 `term_id`, `name`, `term_hanja`, `term_desc_preview`에 붙은 여러 `person_id` 중 이 Term 설명이 실제로 가리키는 Person을 고른다. | 고른 후보의 `term_id`, `person_id`, `APPROVED`, 판단 근거 `note`를 1행으로 쓴다. 고르지 않은 후보는 쓰지 않는다. |
| `TERM_PERSON` | 같은 이름이나 같은 Person이 다른 Term 후보에도 반복될 때, 해당 `term_id`가 이 `person_id`를 가리키는 연결이 맞는지 판단한다. | 연결이 맞는 후보의 `term_id`, `person_id`, `APPROVED`, 판단 근거 `note`를 1행으로 쓴다. 틀리거나 애매한 후보는 쓰지 않는다. |

`PERSON_DUPLICATE`는 Person ID를 병합하라는 뜻이 아니다.
같은 Term 설명에 여러 Person 후보가 붙었으니, 그 Term 설명을 어느 Person에 붙일지만 고르는 검토 유형이다.
예를 들어 `term_person_review.csv`에 같은 `term_id=12345`와 같은 설명으로 `P000001`, `P000002`가 함께 나오고, 설명/한자/생몰년상 `P000002`가 맞다면 승인 seed에는 선택한 한 행만 쓴다.

```csv
term_id,person_id,review_status,note
12345,P000002,APPROVED,설명이 P000002의 한자와 활동 시기와 일치
```

`TERM_PERSON`인데 `person_id`만 다른 후보들이 보일 때도 승인 seed 작성법은 같다.
각 후보를 Person 병합 대상으로 보지 않고, Term과 Person의 연결 후보로 본다.
해당 `term_id`가 가리키는 Person이 `P000010`이라고 확정되면 그 연결만 쓴다.

```csv
term_id,person_id,review_status,note
67890,P000010,APPROVED,동명 인물 중 설명의 관직과 생몰년이 P000010과 일치
```

반대로 어떤 Person에 붙여야 할지 확정할 수 없으면 `term_person_review_approved.csv`에 아무 행도 추가하지 않는다.
`person_duplicate_review_approved.csv`에는 기록하지 않는다.

`term_person_review_approved.csv` 컬럼 값:

| 컬럼 | 들어갈 수 있는 값 | 의미 |
| --- | --- | --- |
| `term_id` | 승인할 Term 식별자 | `term_person_review.csv`의 `term_id`에서 가져온다. |
| `person_id` | 승인할 Person 식별자 | `term_person_review.csv`의 `person_id`에서 가져온다. |
| `review_status` | `APPROVED`, `AUTO_APPROVED` | 두 값만 graph 생성에 반영된다. |
| `note` | 검수 메모 | 한자/설명/생몰년 등 승인 근거를 남긴다. |

## 5. 그래프 CSV 재생성

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

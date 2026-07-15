# 08. 검증 기준과 단계별 구현 로드맵

## 1. 현재 준비도 판정

| 데이터·기능 | 현재 원천 준비도 | 다음 작업 |
|---|---|---|
| 대백과사전 표제어·별칭·유형·시대 | 높음 | SourceRecord와 Entity 해소 |
| 대백과사전 본문 RAG | 높음 | 섹션·문단 청킹과 메타데이터 연결 |
| 대백과사전 구조화 속성 | 중간 | 속성명 사전, 대상 엔터티 해소, 근거 검증 |
| 대백과사전 관련 문서 링크 | 낮음 | 의미 관계가 아니므로 추출 큐에만 사용 |
| ITKC 인물 관계 | 중상 | 중복·self-loop 격리, 방향·역관계 정규화 |
| ITKC 사건-인물 관계 | 낮음 | 역할 세분화와 근거 원문 보강 |
| 시소러스 분류·시대 | 높음 | Category·Era·TimeSpan 정규화 |
| 인물-업적·사건-결과·조약-조항 Fact | 낮음 | AKS 본문 기반 추출·검수 |
| 동일 PathPattern option 후보 검색 | 설계 완료 | 승인 Fact와 PathInstance 구축 후 구현 |
| option별 RAG 검증 | 설계 완료 | 검색 계약·평가 세트 구현 |

결론적으로 raw 적재만 끝내고 문제 생성을 시작하면 안 된다. 생성 품질을 결정하는 병목은 `Fact 정규화·근거 승인·PathInstance 컴파일`이다.

## 2. 단계별 구현 순서

### 단계 A. EDA와 원천 계약 고정

작업:

- 파일 해시·스키마·논리 행 수 manifest
- null sentinel, 다값 구분자, 중복 키 규칙 정의
- 원천별 authority grade와 사용 권한 확인
- EDA 재실행 스크립트와 기준 보고서

완료 조건:

- 원천 스키마 변경을 자동 감지한다.
- 모든 관계 endpoint 누락과 중복이 보고된다.
- raw 파일을 수정하지 않고 같은 결과를 재현한다.

### 단계 B. SourceRecord·NameVariant·분류·시간 구축

작업:

- AKS, ITKC, 시소러스 SourceRecord 생성
- 별칭·자·호·한자명 분리
- 시소러스 `term_lk` Category 계층 생성
- 시대와 자유 형식 연도의 TimeSpan 파싱
- 파싱 실패 review queue

완료 조건:

- source-scoped ID가 모두 유일하다.
- Category 계층에 순환이 없다.
- 원문 시간과 파생 시간이 함께 보존된다.
- 불명확한 시간을 임의의 연도로 바꾼 행이 없다.

### 단계 C. Canonical Entity 해소

작업:

- 이름·한자·타입·시대·생몰년·관계 이웃 기반 후보 생성
- `AUTO_ACCEPTED`, `REVIEW_ACCEPTED`, `CANDIDATE`, `CONFLICT` 상태
- 동명이인 golden set과 negative set
- 병합·분리 이력과 정책 버전 저장

완료 조건:

- 이름만으로 자동 병합된 Entity가 없다.
- generation query는 승인된 해소만 사용한다.
- 같은 SourceRecord가 동시에 여러 ACTIVE Entity에 연결되지 않는다.
- 병합 취소 시 연결된 Fact를 추적할 수 있다.

### 단계 D. Predicate·Fact·EvidenceRef 구축

작업:

- Predicate와 ArgumentRole 사전
- ITKC 인물 관계의 방향·역관계 변환
- AKS 구조화 속성의 통제 매핑
- AKS 본문에서 FactCandidate 추출
- RAG 청크와 EvidenceRef 연결
- 중복·충돌 검토 후 ACCEPTED Fact 적재

완료 조건:

- 생성용 Fact는 모두 승인 EvidenceRef를 가진다.
- Predicate별 역할 타입과 cardinality를 통과한다.
- ITKC `사건인물`이 근거 없이 세부 역할로 승격되지 않는다.
- AKS `relatedArticles`가 의미 Fact로 직접 사용되지 않는다.
- 동일 canonical_hash의 ACCEPTED Fact가 하나다.

### 단계 E. 생성 패턴과 PathInstance

초기 Pattern 후보:

1. `PERSON_CREATED_WORK`
2. `PERSON_AUTHORED_DOCUMENT`
3. `EVENT_ASSOCIATED_PERSON`
4. `ENTITY_ACTIVE_DURING_ERA`
5. `ENTITY_LOCATED_IN_PLACE`
6. `PERSON_SOCIAL_RELATION`
7. `EVENT_OCCURRED_DURING_TIME`
8. `EVENT_RELATIVE_TO_EVENT`
9. `EVENT_DURING_ADMINISTRATION`

초기에는 원천이 실제로 지지하는 패턴만 활성화한다. 시간형 세 패턴은 승인 TimeSpan을 바인딩하고 결정론적 범위 비교로 BEFORE·AFTER·DURING을 계산한다. 모든 사건 쌍의 BEFORE edge를 영구 저장하지 않는다. `EVENT_RESULT`, `EVENT_CAUSE`, `DOCUMENT_CLAUSE`, `ADMINISTRATION_POLICY`는 본문 Fact 추출과 검증이 충분해진 뒤 추가한다.

완료 조건:

- PatternSlot의 타입과 required binding이 검증된다.
- PathInstance가 ACCEPTED Fact만 사용한다.
- 각 PathInstance가 사용한 Fact와 Entity로 역추적된다.
- PathInstance 삭제·재생성 결과가 결정적이다.
- GraphBuildManifest의 snapshot ID와 재컴파일 결과의 PathInstance ID가 일치한다.

### 단계 F. 후보 검색과 난이도 정책

작업:

- 동일 PathPattern 후보 쿼리
- Predicate별 mismatch validator
- Blueprint·band·choice count·정책 버전별 EligibilityProfile 컴파일
- Category·시대·이웃 유사도 특징
- DifficultyPolicy와 band 데이터
- 제한된 가중 랜덤과 random seed 기록

완료 조건:

- 정답과 동일 Entity·별칭이 후보로 나오지 않는다.
- UNKNOWN 후보가 최종 option에 포함되지 않는다.
- 설정된 선지 수에 필요한 후보를 모두 검증한다.
- 후보 부족 시 유형 변경 또는 안전한 실패가 발생한다.
- 동일 입력·seed·graph snapshot에서 같은 선택을 재현한다.
- 추첨된 조합은 정확히 일치하는 ELIGIBLE EligibilityProfile을 가진다.

### 단계 G. RAG와 생성 모델 연결

작업:

- reference·FALSE alternative·TRUE companion RAG purpose 분리
- 외부 API의 stimulus-only 스키마
- 후보별 RAG 병렬 검증
- sLLM의 compose-only 스키마
- 지문 답 누출과 근거 충실성 검사

완료 조건:

- reference와 모든 option에 사용 근거 chunk ID가 있다.
- API와 sLLM이 OptionBinding이나 하위 binding을 바꿀 수 없다.
- 생성 문장의 모든 역사 주장을 Fact와 EvidenceRef로 추적한다.
- 최종 selection rule 적용 결과 정답이 정확히 하나다.

### 단계 H. 운영 캘리브레이션

작업:

- 생성 성공률과 단계별 실패율
- RAG 검색 품질과 근거 충실도
- 후보 검증 통과율
- 문항 정답률·풀이 시간·선지 선택률
- predicted difficulty와 measured difficulty 비교
- 유형·시대·주제 분포 모니터링

완료 조건:

- 난이도 정책 변경이 버전으로 추적된다.
- 배점과 실측 난이도가 분리된다.
- 특정 시대·유형·정답 위치로 편향되지 않는다.
- 품질 저하 정책을 이전 버전으로 되돌릴 수 있다.

## 3. 필수 QA 불변식

다음 항목은 비율 목표가 아니라 반드시 만족해야 하는 불변식이다.

```text
SourceRecord 고유 키 중복 = 0
승인 관계 endpoint 누락 = 0
ACCEPTED Fact canonical_hash 중복 = 0
생성 Fact의 승인 근거 누락 = 0
PathInstance required slot 누락 = 0
PathInstance의 비승인 Fact 참조 = 0
PathPattern.answer_slot_key가 가리키는 PatternSlot 수 = 1
같은 PathPattern 안의 slot_key 중복 = 0
같은 PathPattern 안의 step_key 중복 = 0
PathInstance당 OF_PATTERN 관계 수 = 1
PathInstance의 required slot별 BINDS 관계 수 = 1
PathInstance의 required step별 동일 step_id USES_FACT 관계 수 = 1
PathInstance.pattern_id와 OF_PATTERN 대상 pattern_id 불일치 = 0
PatternStep.expected_polarity와 USES_FACT Fact.polarity 불일치 = 0
동일 PathInstance·snapshot·feature policy의 ACTIVE PathFeatureProfile 수 = 1
동일 scope_hash의 ACCEPTED CompletenessAssertion 중복 = 0
동일 combination_hash의 ELIGIBLE EligibilityProfile 중복 = 0
최종 선지의 UNKNOWN Verdict = 0
최종 선지 TRUE/FALSE 수와 EligibilityProfile.target_true_count/target_false_count 불일치 = 0
동일 canonical Entity의 중복 선지 = 0
selection rule 적용 후 선택되는 선지 수 = 1
정답 근거 없는 생성 문항 = 0
```

성능, 커버리지, 자동 승인율, RAG score 같은 기준은 환경과 데이터에 따라 조정되므로 버전 있는 정책값으로 관리한다.

## 4. 테스트 계층

### 4.1 Raw·EDA 테스트

- 파일 해시와 스키마 확인
- JSONL·CSV 논리 행 수 확인
- BOM·인코딩 확인
- multiline CSV 파싱 확인
- null sentinel 정규화 확인
- 중복·self-loop·unresolved endpoint 격리 확인

### 4.2 엔터티 해소 테스트

Positive golden case:

- 동일 인물의 표제어·한자·호가 하나의 Entity로 연결
- 동일 사건의 AKS·ITKC·시소러스 레코드 연결

Negative golden case:

- 같은 `태조`를 서로 다른 국가의 왕으로 분리
- 같은 `수`를 국가·관직·일반 용어로 분리
- 생몰년과 본관이 충돌하는 동명이인 분리

### 4.3 Fact 테스트

- ITKC `부 ↔ 자`, `스승 ↔ 제자` 방향 확인
- 대칭 Predicate의 canonical_hash 중복 제거
- AKS `저자`, `제작 시기`, `소재지` 타입별 Predicate 매핑
- n-ary 조약 Fact의 모든 역할 보존
- 시간 범위 겹침을 UNKNOWN으로 처리

### 4.4 PathPattern 테스트

- 김정희-CREATED-세한도와 정선-CREATED-인왕제색도가 같은 패턴
- 갑신정변-RESULTED_IN-한성 조약의 방향 보존
- 강화도 조약 조항에서 grantor·beneficiary·right·scope 역할 보존
- 부산 장소형에서 행정 범위와 사건 장소 일치
- 공산 전투·고창 전투에서 확정 가능한 시간 선후

이 예시는 문항 전문을 적재하기 위한 것이 아니라 추상 패턴과 validator의 golden case다.

### 4.5 후보 검색 테스트

- 다른 PathPattern의 엔터티가 섞이지 않음
- reference와 병합·별칭인 중복 Entity option 제외
- 공동 제작 후보의 불완전성 차단
- 같은 Category지만 승인 Fact가 없는 후보 차단
- 현재 그래프에 관계가 없다는 이유만으로 FALSE 처리하지 않음
- 시간·장소가 겹치는 후보는 UNKNOWN 처리

### 4.6 RAG 테스트

- 정답 Fact 역할을 모두 포함한 청크 회수
- 후보 true Fact의 다른 anchor 근거 회수
- 관계가 아니라 단순 동시 언급인 청크 거절
- source grade 필터 동작
- 문서 재청킹 후 EvidenceRef 무효화·재연결
- 검색 문맥의 지시문을 모델 명령으로 실행하지 않음

### 4.7 생성 테스트

- 지문에 정답명과 고유 별칭이 노출되지 않음
- sLLM 출력 후보 집합이 입력과 동일
- 정답 위치 분포가 편향되지 않음
- 부정형에서 truth와 selected answer 분리
- 같은 문항 내 선지 길이·문체 불균형 검사
- 동일 random seed 재현

## 5. 단계별 검토 산출물

| 단계 | 반드시 검토할 문서·리포트 |
|---|---|
| A | source manifest, EDA report, schema diff |
| B | name/category/time parse QA |
| C | entity resolution candidates와 golden 결과 |
| D | predicate dictionary, fact evidence review |
| E | pattern catalog, path instance coverage |
| F | candidate pool·UNKNOWN·난이도 분포 |
| G | RAG retrieval 평가, 생성 validator 결과 |
| H | 운영 난이도·편향·실패율 보고서 |

## 6. MVP 범위

첫 버전은 모든 한국사 문항 유형을 동시에 만들지 않는다.

### MVP에 포함

- text 기반 단일 anchor 문항
- `PERSON_CREATED_WORK`
- `PERSON_AUTHORED_DOCUMENT`
- 근거가 보강된 `EVENT_ASSOCIATED_PERSON`
- 확정 시간 기반 `SELECT_BEFORE`, `SELECT_AFTER`, `SELECT_DURING`
- `EVENT_OCCURRED_DURING_TIME`, `EVENT_RELATIVE_TO_EVENT`, `EVENT_DURING_ADMINISTRATION`
- target truth 분포를 만족하는 같은 패턴의 Entity형 option 후보
- EASY와 MEDIUM의 정책 기반 난이도
- reference·모든 option의 RAG 근거 필수

### MVP에서 제외

- 근거가 약한 인과형
- 복잡한 조약 n-ary 조항형
- 여러 자료를 동시에 비교하는 유형
- 이미지·지도 기반 문항
- 부정형과 순서 배열형
- 자동 IRT 캘리브레이션

제외 항목은 스키마에서 막는 것이 아니라 validator와 Fact 커버리지가 준비된 뒤 단계적으로 활성화한다.

### MVP 이후 확장 순서

1. `STATEMENT` OptionBinding과 부정형을 활성화하고 하위 claim별 Verdict·mismatch proof, `SELECT_FALSE = TRUE(n-1)+FALSE(1)` 분포를 검증한다.
2. `SEQUENCE`, `MULTI_ANCHOR_COMPARE`, `MAPPING_MATCH`를 활성화하고 operand 전체 선후·pair 전체 진리표·정답 유일성을 검증한다.
3. `IMAGE`와 `MEDIA_REF`·`MAP`·`TABLE`·`TIMELINE` stimulus block을 활성화하고 권리, OCR·캡션 답 노출, 묘사 대상, 대체 텍스트를 검증한다.
4. 충분한 응답 데이터 뒤 DifficultyPolicy를 실측 난이도·변별도 기반으로 보정한다.

## 7. 운영 쓰기 권한

문제 생성 런타임은 Neo4j 읽기 전용으로 운영한다.

```text
읽기 가능
  GraphBuildManifest / Entity / Fact / CompletenessAssertion / EvidenceRef
  QuestionBlueprint / PathPattern / PathInstance / EligibilityProfile / Policy

쓰기 금지
  sLLM이 발견한 새 사실
  RAG 결과에서 추론한 관계
  생성된 지문·선지·해설
```

새 사실은 `FactCandidate` 검수 큐로 보내고 ETL·검수 작업이 승인한 뒤 다음 그래프 배포에 포함한다.

## 8. 배포 순서

1. 새 그래프를 별도 database 또는 namespace에 적재한다.
2. 제약·인덱스를 생성한다.
3. 원천 수·Entity 수·Fact 수·PathInstance 수와 `graph_content_manifest_hash`·`derived_artifact_hash`를 GraphBuildManifest와 대조한다.
4. golden Cypher와 validator 테스트를 실행한다.
5. RAG EvidenceRef의 chunk 존재를 확인한다.
6. 읽기 전용 생성 서비스로 shadow test를 수행한다.
7. 기존 그래프와 결과를 비교한다.
8. 승인 후 트래픽을 전환한다.
9. 이전 snapshot과 정책 버전을 롤백 가능 상태로 유지한다.

라이브 Neo4j에 raw 데이터를 직접 덧붙이는 방식은 사용하지 않는다. 엔터티 해소와 Fact 승인 규칙이 달라지므로 별도 빌드·검증·전환이 안전하다.

## 9. 최종 완료 정의

다음 상태가 되어야 “문제 생성용 그래프가 준비되었다”고 판단한다.

- keyword가 하나의 canonical Entity로 안전하게 해소된다.
- 발문의도에 맞는 승인 PathInstance가 존재한다.
- 정답은 LLM 생성 없이 Fact binding으로 확정된다.
- 정답 RAG 근거로 지문을 만들 수 있다.
- 동일 PathPattern의 다른 대상이 충분히 존재한다.
- 각 후보가 다른 문맥에서 참임을 RAG로 증명한다.
- 현재 문맥의 FALSE가 결정론적으로 검증된다.
- sLLM은 확정된 재료의 표현만 작성한다.
- 최종 문항은 정답 하나, UNKNOWN 없음, 모든 설명에 출처가 있다.
- 결정론적 선택 단계는 동일 snapshot·정책·seed로 재실행되며, 외부 모델 단계는 요청·응답 해시와 RAG·모델·프롬프트·파라미터 버전으로 감사·재생할 수 있다.

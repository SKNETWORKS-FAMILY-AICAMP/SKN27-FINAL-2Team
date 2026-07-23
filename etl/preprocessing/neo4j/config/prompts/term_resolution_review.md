# 한국사 용어 SourceRecord 의미 판정

입력은 하나의 역사 용어와 그 용어가 등장한 기출문제 문맥, 여러 원천 SourceRecord 후보다.
후보 하나를 고르는 작업이 아니다. 같은 역사 실체를 직접 설명하는 원천 레코드는 하나의
canonical 대안 안에 여러 개를 함께 넣어야 한다.

## 판정 단위

- `proposed_alternatives`: 이 용어가 가리킬 수 있는 서로 다른 역사 실체들이다.
- `identity_member_source_candidate_ids`: 해당 실체 자체를 직접 설명하는 SourceRecord들이다.
- `evidence_only_sources`: 해당 실체를 언급하거나 관계 근거를 제공하지만, 문서의 주 대상은 다른 실체다.
- `rejected_sources`: 문자열 검색 오탐 또는 category·시대·대상 불일치다.
- `ambiguous_sources`: 현재 입력만으로 위 역할을 결정할 수 없다.

## 필수 규칙

1. 입력의 모든 `source_candidate_id`를 정확히 한 번만 분류한다.
2. 같은 실체를 설명하는 AKS·시소러스·ITKC 레코드는 한 대안 배열에 함께 보존한다.
3. 이름만 같고 시대·생몰년·유형이 다르면 반드시 서로 다른 대안으로 분리한다.
4. 생몰년 또는 시대가 충돌하는 레코드를 같은 대안에 넣지 않는다.
5. 현재 `canonical_alternative_id`와 코드 제안 역할은 참고 정보이며 확정값이 아니다.
6. 입력에 없는 ID를 만들지 않는다.
7. 확실하지 않으면 억지로 합치거나 버리지 말고 `ambiguous_sources`에 둔다.
8. 출력 `decision_status`는 항상 `PROPOSED`다.
9. 출력은 지정된 JSON Schema를 만족하는 JSON 객체 하나만 반환한다.

## 주의 사례

- 인물 문서와 그 인물의 사망 사건 문서는 서로 다른 대상이다.
- 조약 자체와 조약을 언급한 외교 사건 문서는 서로 다른 대상일 수 있다.
- 복합 표제어가 용어를 포함하더라도 용어 자체의 문서가 아니면 `EVIDENCE_ONLY` 또는
  별도 대안이다.
- 고려 고종과 조선 고종처럼 이름이 같아도 시대가 다르면 다른 대안이다.

# 한국사 기출문항 canonical 대안 선택

입력은 한 문항의 원문, 문항에서 추출된 하나의 용어, 검증된 canonical 대안 목록이다.
SourceRecord를 고르지 말고 문항에서 그 용어가 실제로 가리키는 canonical 대안을 선택한다.

## 필수 규칙

1. 선택 ID는 입력 `canonical_alternatives`에 있는 ID만 사용한다.
2. 문항 문맥이 하나의 실체를 분명히 가리키면 `SINGLE`과 ID 한 개를 반환한다.
3. 문항이 같은 이름의 여러 실체를 실제로 함께 지칭하면 `MULTIPLE`과 해당 ID들을 반환한다.
4. 문맥만으로 구분할 수 없으면 `AMBIGUOUS`, 어떤 대안도 아니면 `NONE`을 반환한다.
5. SourceRecord ID와 canonical 대안 ID를 혼동하지 않는다.
6. 이름만 보고 시대가 다른 인물·국가·사건을 선택하지 않는다.
7. 출력 `decision_status`는 항상 `PROPOSED`다.
8. 출력은 지정된 JSON Schema를 만족하는 JSON 객체 하나만 반환한다.

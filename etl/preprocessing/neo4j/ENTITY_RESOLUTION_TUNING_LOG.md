# Entity Resolution LLM 튜닝 기록

이 문서는 사람이 확정한 골드셋을 기준으로 프롬프트와 자동 검증 게이트를 변경하고,
동일한 100개 case에서 변경 전후 점수를 비교한 기록이다.

## 평가 전제

- `gold_case_id`가 같다는 것은 같은 canonical term의 후보 묶음이라는 뜻이다.
- 같은 case의 모든 후보가 병합 대상인 것은 아니다.
- 실제 병합 정답은 같은 `gold_alternative_key`를 가진 `IDENTITY_MEMBER`끼리만 구성한다.
- 사람이 검수한 골드 라벨은 이번 튜닝에서 변경하지 않았다.
- 자동 병합에서는 recall보다 verified precision을 우선하되, recall 하락도 함께 기록한다.
- 현재 100건은 개발용 골드셋이므로 최종 배포 전에는 별도의 holdout 평가가 필요하다.

## 공통 실행 조건

| 항목 | 값 |
|---|---|
| 골드 case 수 | 100 |
| 모델 | `gpt-5.6-terra` |
| reasoning effort | `high` |
| resolution policy version | `entity-resolution-candidate-v2.4` |
| prediction coverage | `1.0` |
| 평가 기준 파일 | `goldset/internal/evaluation/human_gold_decisions.jsonl` |
| 모델 결과 파일 | `goldset/internal/model/term_identity_model_decisions.jsonl` |
| 평가 결과 파일 | `goldset/internal/evaluation/model_vs_gold_metrics.json` |

## Iteration 0: 변경 전 기준점

평가 실행 시각은 기존 `goldset_workflow_manifest.json`의
`2026-07-24T05:17:54.550119+00:00`이다.

| 지표 | v1 |
|---|---:|
| candidate role accuracy | 0.794545 |
| candidate role macro F1 | 0.594661 |
| role exact case rate | 0.53 |
| cluster exact case rate | 0.70 |
| proposal identity pair precision | 0.577320 |
| proposal identity pair recall | 0.949153 |
| proposal identity pair F1 | 0.717949 |
| proposal false merge pair | 41 |
| proposal false split pair | 3 |
| verified identity pair precision | 0.741935 |
| verified identity pair recall | 0.884615 |
| verified identity pair F1 | 0.807018 |
| verified false merge pair | 8 |
| verified false split pair | 3 |
| link status accuracy | 0.71 |
| problem review accuracy | 0.96 |

proposal pair는 TP 56, FP 41, FN 3이었다. 기존 게이트 적용 후에는
TP 23, FP 8, FN 3이었으며 proposal false merge 33개를 차단했다.

### 변경 전 오류 해석

낮은 precision의 원인은 골드셋이 아니라 모델의 과병합이었다. 모델은 같은 case에
들어온 동명이인, 동명 사건, 관련 개념까지 하나의 target identity로 포함하는 경향이
있었다. 특히 문제 문맥이 가리키는 대상이 아닌 후보를 이름·한자·시대의 유사성만으로
병합한 오류가 반복되었다.

## Iteration 1: target 중심 프롬프트와 멤버별 게이트

### 변경 내용

1. 프롬프트 버전을 `entity-semantic-review-v2`로 올렸다.
2. `problem_context_samples`가 가리키는 target만 `proposed_alternatives`에 포함하도록
   명시했다.
3. 같은 이름·한자·시대만으로는 병합하지 않고, 연도·지역·인물·사건·문헌의 정체성이
   일치해야 한다고 명시했다.
4. 동명이인, 동명 신문, 명령과 사건, 인물과 사건을 구분하는 반례를 추가했다.
5. 자동 허용 정렬 모드를 `normalized_exact`로 축소했다.
6. identity alternative에 포함된 일부 후보만 일치하면 통과하던 검증을 수정해,
   모든 identity member가 target term과 정렬되는지 개별 검사하도록 했다.
7. 저장된 gold task는 바꾸지 않고, API 요청 사본의 `review_model`과
   `prompt_version`을 현재 정책값으로 덮어써 실행 정책과 입력 메타데이터를 일치시켰다.

변경 파일:

- `config/prompts/term_resolution_review.md`
- `config/review_goldset.json`
- `entity_resolution/semantic_review.py`
- `entity_resolution/execute_term_review.py`
- `test/MK/test_neo4j/test_semantic_review.py`
- `test/MK/test_neo4j/test_term_review_executor.py`

### 실행 검증

- Python compile 통과
- 관련 단위 테스트 21건 통과
- `run_neo4j_preprocessing.py --goldset --dry-run` 결과 `READY`
- 골드 검증 오류 0건
- v2 LLM 실행 100건 성공, 최종 실패 0건
- 첫 1건 실행 후 동일 체크포인트로 나머지 99건 재개
- 실행 중 출력 구조 오류가 발생한 2개 task는 재시도로 정상 처리

실행 manifest:

`goldset/internal/model/term_identity_model_run_manifest.json`

### 변경 전후 점수

| 지표 | v1 | v2 | 증감 |
|---|---:|---:|---:|
| candidate role accuracy | 0.794545 | 0.740000 | -0.054545 |
| candidate role macro F1 | 0.594661 | 0.553856 | -0.040805 |
| role exact case rate | 0.53 | 0.52 | -0.01 |
| cluster exact case rate | 0.70 | 0.73 | +0.03 |
| proposal identity pair precision | 0.577320 | 0.688312 | +0.110992 |
| proposal identity pair recall | 0.949153 | 0.898305 | -0.050848 |
| proposal identity pair F1 | 0.717949 | 0.779412 | +0.061463 |
| proposal false merge pair | 41 | 24 | -17 |
| proposal false split pair | 3 | 6 | +3 |
| verified identity pair precision | 0.741935 | 0.833333 | +0.091398 |
| verified identity pair recall | 0.884615 | 0.833333 | -0.051282 |
| verified identity pair F1 | 0.807018 | 0.833333 | +0.026316 |
| verified false merge pair | 8 | 4 | -4 |
| verified false split pair | 3 | 4 | +1 |
| link status accuracy | 0.71 | 0.72 | +0.01 |
| problem review accuracy | 0.96 | 0.99 | +0.03 |

v2 proposal pair는 TP 53, FP 24, FN 6이다. 검증 게이트가 승인한 pair는
TP 20, FP 4, FN 4이며, proposal false merge 20개를 차단했다.

### v2 게이트 결과

| 상태 | case 수 |
|---|---:|
| `VERIFIED` | 54 |
| `NEEDS_MANUAL_REVIEW` | 34 |
| `INVALID` | 12 |

검증 오류는 한 case에 여러 개가 기록될 수 있다.

| 오류 코드 | 건수 |
|---|---:|
| `INSUFFICIENT_PAIR_EVIDENCE` | 29 |
| `TERM_SOURCE_ALIGNMENT_REVIEW_REQUIRED` | 25 |
| `CATEGORY_CONFLICT_IDENTITY_MEMBER` | 8 |
| `ENTITY_TYPE_REVIEW_REQUIRED` | 7 |
| `STRONG_PAIR_CONFLICT` | 5 |
| `AMBIGUOUS_SOURCE_REMAINS` | 4 |

게이트 통과 후에도 남은 false merge 4개:

| canonical term | false merge pair |
|---|---:|
| 양헌수 | 2 |
| 독립신문 | 1 |
| 단군 신앙 | 1 |

게이트 통과 후 남은 false split 4개:

| canonical term | false split pair |
|---|---:|
| 칠정산 외편 | 2 |
| 사택적덕 | 1 |
| 부여경 | 1 |

### Iteration 1 판단

- 과병합 억제 목표는 달성했다. proposal precision은 약 11.1%p, verified precision은
  약 9.1%p 상승했고 false merge도 각각 17개와 4개 감소했다.
- 모델이 보수적으로 변하면서 proposal recall은 약 5.1%p 하락했고 false split은
  3개 늘었다.
- verified precision 0.8333은 개선됐지만, 사람 개입 없는 자동 병합 기준으로 확정하기에는
  아직 낮다.
- 다음 수정은 전체 프롬프트를 다시 크게 바꾸기보다, 남은 verified false merge 4개를
  게이트가 차단하도록 근거 조건을 보강하고 false split 4개가 차단되지 않도록
  사례별 신호를 확인하는 순서가 적절하다.
- 34개 `NEEDS_MANUAL_REVIEW`와 12개 `INVALID`는 자동 병합하지 않는 것이 현재 정책이다.

## 다음 반복에서 확인할 항목

1. 양헌수, 독립신문, 단군 신앙이 어떤 pair signal로 `VERIFIED`됐는지 확인한다.
2. 칠정산 외편, 사택적덕, 부여경의 gold pair가 프롬프트에서 분리됐는지,
   게이트에서 누락됐는지 구분한다.
3. verified precision 목표치를 정한 뒤 게이트 threshold를 조정한다.
4. 같은 100건으로 회귀 확인 후 별도 holdout 골드셋에서 최종 평가한다.

## Iteration 2: 잔여 오류 감사

Iteration 1의 verified false merge 4개와 false split 4개를 대상으로 모델 판단,
문제 문맥, 후보 설명, pair signal, gold 라벨을 직접 비교했다.

### 감사 결론

잔여 8개 pair를 점수상 오류라는 이유만으로 프롬프트나 게이트에 반영하면 안 된다.
대부분은 모델 오류가 아니라 현재 gold 라벨과 target 중심 판정 정책의 충돌이다.

#### verified false merge 4개

| canonical term | pair 수 | 감사 결과 |
|---|---:|---|
| 양헌수 | 2 | gold가 `REJECTED`한 ITKC 후보는 한자, 1816~1888 생몰년, 남원 본관, 자 경보, 호 하거, 부친 양종임이 일치한다. 기존 두 identity 후보와 같은 인물로 보는 것이 타당하다. |
| 독립신문 | 1 | 문제 문맥 중 두 건이 1896년 서재필판을 직접 가리키고, 모델은 1896년판과 1919년 상하이판을 별도 alternative로 정확히 분리했다. gold는 1896년판 두 후보를 `EVIDENCE_ONLY`로 두었다. |
| 단군 신앙 | 1 | AKS와 THESAURUS 후보가 이름·한자·정의까지 같은 단군신앙을 직접 설명한다. 모델 병합이 타당하지만 gold는 THESAURUS 후보를 `REJECTED`로 두었다. |

관련 외부 근거:

- 양헌수의 한자, 생몰년, 본관, 자, 부친 정보:
  [한국민족문화대백과사전](https://encykorea.aks.ac.kr/Article/E0035852)
- 1896년 국내판과 1919년 상하이판은 같은 제호를 쓴 별도 신문:
  [대한민국역사박물관](https://archive.much.go.kr/archive/newspaper/release.do)

#### verified false split 4개

| canonical term | pair 수 | 감사 결과 |
|---|---:|---|
| 칠정산 외편 | 2 | 모델은 `칠정산외편` 두 후보를 병합하고, 표제어·한자가 다른 `칠정산` 후보를 분리했다. gold는 세 후보를 모두 하나로 묶었다. 문제 target이 외편이면 모델 판단이 더 엄격하고 일관적이다. |
| 사택적덕 | 1 | gold는 `사택적덕(沙宅積德)`과 `사택지적(砂宅智積)`을 병합하지만, 이름·한자·설명이 다르고 자동 pair evidence도 없다. 동일인 여부는 사람의 역사 해석이 필요한 항목이다. |
| 부여경 | 1 | 두 후보는 682년 이후 의자왕 증손 `부여경(扶餘敬)`을 설명한다. 문제 문맥은 475년에 사망한 개로왕을 `부여경`으로 부른다. target 중심 정책이면 후보를 제외한 모델 판단이 맞고, canonical term 중심 정책이면 gold 판단이 맞다. |

관련 외부 근거:

- `부여경(扶餘敬)` 후보는 의자왕의 증손이며 682년 이후 왕위를 계승:
  [한국민족문화대백과사전](https://encykorea.aks.ac.kr/Article/E0024371)
- `칠정산내편`과 `칠정산외편`은 서로 다른 원리에 기초한 별도 편:
  [한국민족문화대백과사전](https://encykorea.aks.ac.kr/Article/E0029857)

### Iteration 2 판단

- v2 프롬프트와 자동 게이트는 아직 변경하지 않았다.
- v2 점수에 남은 verified FP/FN을 그대로 모델 오류로 계산하면 품질 판단이 왜곡된다.
- 먼저 gold의 판정 단위를 다음 중 하나로 확정해야 한다.
  - 문제 문맥이 가리키는 target만 identity로 인정
  - 문제 문맥과 무관하게 canonical term과 일치하는 모든 source identity를 인정
- 현재 프롬프트 v2는 첫 번째 정책을 명시하고 있다.
- 정책을 확정한 뒤 위 6개 case를 재검수하고, 그 결과를 기준으로 v3 필요 여부를
  결정해야 한다.

## Iteration 3: gold 재검수 반영 기준점과 역할 경계 개선

Iteration 2에서 확인한 6개 case를 사람이 다시 검수해 gold에 반영했다. 모델 응답은
Iteration 1의 v2 결과를 그대로 두고, 수정된 gold를 기준으로 다시 평가했다.
평가 manifest 생성 시각은 `2026-07-24T06:51:16.336503+00:00`이다.

### v3 적용 전 기준점

| 지표 | 수정된 gold + v2 모델 |
|---|---:|
| candidate role accuracy | 0.752727 |
| candidate role macro F1 | 0.564101 |
| candidate role weighted F1 | 0.746136 |
| candidate role macro F1 without `AMBIGUOUS` | 0.752134 |
| role exact case rate | 0.53 |
| cluster exact case rate | 0.78 |
| proposal identity pair precision | 0.740260 |
| proposal identity pair recall | 0.966102 |
| proposal identity pair F1 | 0.838235 |
| proposal false merge pair | 20 |
| proposal false split pair | 2 |
| verified identity pair precision | 1.0 |
| verified identity pair recall | 1.0 |
| verified identity pair F1 | 1.0 |
| verified false merge pair | 0 |
| verified false split pair | 0 |

게이트 결과는 `VERIFIED` 54건, `NEEDS_MANUAL_REVIEW` 34건, `INVALID` 12건이다.
proposal false merge 20개는 모두 자동 승격 전에 차단됐고, 아직 게이트가 확정하지 않은
gold identity pair 35개는 `deferred_gold_identity_pair_count`로 기록됐다.

### 역할별 기준점

| 역할 | precision | recall | F1 | support |
|---|---:|---:|---:|---:|
| `IDENTITY_MEMBER` | 0.775510 | 0.966102 | 0.860377 | 118 |
| `EVIDENCE_ONLY` | 0.724138 | 0.538462 | 0.617647 | 156 |
| `REJECTED` | 0.765957 | 0.791209 | 0.778378 | 273 |
| `AMBIGUOUS` | 0.0 | 0.0 | 0.0 | 3 |

macro F1이 특히 낮은 이유는 두 가지다.

1. `EVIDENCE_ONLY` gold를 모델이 `REJECTED`로 판정한 후보가 63개이고, 반대 방향이
   32개로 두 역할의 경계 충돌이 95개다.
2. `AMBIGUOUS` support가 3개뿐인데 F1이 0이라, 각 역할을 동일 가중하는 macro F1에
   희소 역할 실패가 크게 반영된다.

원래 macro F1은 평가 기준으로 그대로 보존한다. 표본 불균형을 해석할 수 있도록
weighted F1, `AMBIGUOUS` 제외 macro F1, 역할별 support를 보조지표로 추가했다.

### v3 반영 내용

1. 프롬프트 버전을 `entity-semantic-review-v3`로 올렸다.
2. `EVIDENCE_ONLY`는 원천의 주 대상이 다르더라도 target과의 명시적이고 그래프에 유용한
   관계가 문맥에 있어야 한다고 명시했다.
3. 부분 문자열·동명·국가나 지역 수식어·다른 유형의 같은 제목처럼 관계 설명이 없는
   후보는 `REJECTED`로 판정하도록 명시했다.
4. `AMBIGUOUS`는 정보 부족으로 세 역할을 구분할 수 없을 때만 사용하고, 명확한 관련
   실체나 오탐을 보내지 않도록 했다.
5. 현재 v2 결과의 95개 역할 충돌을
   `goldset/human_review_csv/role_conflict_manual_review.csv`에 생성했다.
   이 파일은 사람의 재검토 결과를 기록하지만 gold를 자동 변경하지 않는다.
6. 평가 JSON에 `candidate_role_weighted_f1`,
   `candidate_role_macro_f1_without_excluded_roles`,
   `candidate_role_macro_f1_excluded_roles`, `candidate_role_support_counts`를 추가했다.

### v3 실행 결과

v3는 100개 task를 기존 checkpoint 재사용 없이 실행했고 100건 모두 성공했다.
workflow 완료 시각은 `2026-07-24T08:25:23.150036+00:00`이다.

| 지표 | v3 적용 전 | v3 | 증감 |
|---|---:|---:|---:|
| candidate role accuracy | 0.752727 | 0.798182 | +0.045455 |
| candidate role macro F1 | 0.564101 | 0.600115 | +0.036014 |
| candidate role weighted F1 | 0.746136 | 0.791826 | +0.045690 |
| macro F1 without `AMBIGUOUS` | 0.752134 | 0.800154 | +0.048020 |
| role exact case rate | 0.53 | 0.60 | +0.07 |
| cluster exact case rate | 0.78 | 0.85 | +0.07 |
| proposal pair precision | 0.740260 | 0.893939 | +0.153679 |
| proposal pair recall | 0.966102 | 1.0 | +0.033898 |
| proposal pair F1 | 0.838235 | 0.944000 | +0.105765 |
| proposal false merge pair | 20 | 7 | -13 |
| proposal false split pair | 2 | 0 | -2 |
| verified pair precision | 1.0 | 1.0 | 0 |
| verified pair recall | 1.0 | 1.0 | 0 |
| verified pair F1 | 1.0 | 1.0 | 0 |
| link status accuracy | 0.72 | 0.75 | +0.03 |

게이트 상태는 `VERIFIED` 64건, `NEEDS_MANUAL_REVIEW` 24건, `INVALID` 12건이다.
v3 proposal의 오병합 7쌍은 모두 게이트가 차단했고 오분리는 0쌍이다.

### pair recall 해석

proposal pair 기준으로 gold identity pair는 59쌍이다. v3가 59쌍을 모두 제안했으므로
`proposal pair recall = 59 / (59 + 0) = 1.0`이다. 이는 정답 pair를 누락하지 않았다는
뜻이지, 제안 전체가 정확하다는 뜻은 아니다. 정답이 아닌 7쌍도 함께 제안했기 때문에
proposal precision은 `59 / (59 + 7) = 0.893939`다.

현재 `verified pair recall = 1.0`은 게이트가 `VERIFIED`한 case 내부의 조건부 지표다.
게이트가 자동 승인한 정답 pair는 24쌍이고 그 범위의 FN이 0이어서 `24 / 24 = 1.0`이다.
`NEEDS_MANUAL_REVIEW` 또는 `INVALID` case의 gold pair 35쌍은 FN으로 계산하지 않고
`deferred_gold_identity_pair_count`로 분리한다.

전체 gold pair 중 자동 승인된 비율을 자동 병합 커버리지로 계산하면 다음과 같다.

```text
자동 승인 pair recall = 24 / 59 = 0.406780
보류 pair rate        = 35 / 59 = 0.593220
```

따라서 verified precision 1.0은 현재 자동 병합의 안전성을 보여 주지만, verified recall
1.0을 전체 자동처리 recall로 해석하면 안 된다. 현재 자동 병합은 안전하지만 gold pair의
약 40.7%만 무인 승인한다.

### 자동 승인 coverage 지표 구현

평가기와 `model_vs_gold_metrics.json`에 다음 값을 추가했다.

| 지표 | v3 값 | 의미 |
|---|---:|---|
| `gold_identity_pair_count` | 59 | 평가 대상 gold identity pair 전체 |
| `conditional_verified_identity_pair_recall` | 1.0 | `VERIFIED` case 내부의 기존 조건부 recall |
| `auto_accepted_identity_pair_count` | 24 | 게이트가 자동 승인한 pair 수 |
| `auto_accepted_identity_pair_precision` | 1.0 | 자동 승인된 pair의 정확도 |
| `auto_accepted_identity_pair_recall` | 0.406780 | 전체 gold pair 중 자동 승인된 비율 |
| `auto_accepted_identity_pair_f1` | 0.578313 | 자동 승인 precision과 전체 coverage recall의 조화평균 |
| `deferred_gold_identity_pair_rate` | 0.593220 | 전체 gold pair 중 보류된 비율 |
| `deferred_gold_pair_case_count` | 21 | gold pair가 보류된 case 수 |

기존 `verified_identity_pair_*`는 호환성을 위해 유지하고, 같은 조건부 의미를 명시한
`conditional_verified_identity_pair_*`를 함께 기록한다. 별도 산출물은 추가하지 않고 기존
metrics JSON만 확장했다.

### v3 역할별 결과와 충돌

| 역할 | precision | recall | F1 | support |
|---|---:|---:|---:|---:|
| `IDENTITY_MEMBER` | 0.875969 | 0.957627 | 0.914980 | 118 |
| `EVIDENCE_ONLY` | 0.777778 | 0.583333 | 0.666667 | 156 |
| `REJECTED` | 0.780731 | 0.860806 | 0.818815 | 273 |
| `AMBIGUOUS` | 0.0 | 0.0 | 0.0 | 3 |

`EVIDENCE_ONLY`·`REJECTED` 충돌은 95건에서 87건으로 감소했다.

- gold `EVIDENCE_ONLY` → 모델 `REJECTED`: 62건
- gold `REJECTED` → 모델 `EVIDENCE_ONLY`: 25건

macro F1 0.600115는 support 3인 `AMBIGUOUS`의 F1 0을 동일 가중한다.
이를 제외한 macro F1은 0.800154지만, `EVIDENCE_ONLY` recall 0.583333과 충돌 87건은
별도의 실제 개선 대상으로 남아 있다.

### 보류 pair 신호 감사

35개 보류 pair를 원본 task의 pair 신호와 대조했다.

| 구분 | pair 수 |
|---|---:|
| pair 신호 레코드 존재 | 19 |
| 현재 `merge_eligible=true` | 14 |
| 자동 병합 양성 신호 부족 | 16 |
| 강한 충돌 존재 | 5 |

원천 조합은 AKS·시소러스 27쌍, 시소러스·시소러스 6쌍,
AKS·ITKC 사건 1쌍, ITKC 사건·시소러스 1쌍이다.

보류 오류는 한 case에 여러 코드가 함께 기록될 수 있다.

| 오류 코드 | case 수 | 영향받은 gold pair 수 |
|---|---:|---:|
| `INSUFFICIENT_PAIR_EVIDENCE` | 13 | 27 |
| `TERM_SOURCE_ALIGNMENT_REVIEW_REQUIRED` | 9 | 15 |
| `STRONG_PAIR_CONFLICT` | 5 | 9 |
| `ENTITY_TYPE_REVIEW_REQUIRED` | 4 | 8 |
| `CATEGORY_CONFLICT_IDENTITY_MEMBER` | 4 | 6 |
| `AMBIGUOUS_SOURCE_REMAINS` | 1 | 1 |

3개 멤버 대안 중 AKS 후보가 두 시소러스 후보와 각각 `merge_eligible=true`지만,
시소러스끼리 직접 신호가 없어 전체가 보류되는 사례가 있다. `박종철 고문 치사 사건
은폐 조작`, `조일 통상 장정`, `비무장지대`가 이에 해당한다. 다음 게이트 실험은 모든
pair가 양성인 완전 그래프를 요구하는 현재 방식과, 강한 충돌이 없고 양성 edge로 모든
멤버가 연결되는 연결 그래프 방식을 비교하는 것이다.

다만 연결성 규칙만으로 즉시 자동 승인될 수 있는 것은 현재 다른 오류가 없는
`비무장지대` case다. 나머지는 term alignment 또는 EntityType 검토가 함께 남아 있어
별도 완화 없이 자동 승인되지 않는다. 강한 충돌 5개 pair와 category 충돌 case는 이
실험에서 완화하지 않는다.

## 다음 개선 우선순위

1. **연결 그래프 게이트를 별도 정책으로 실험한다.**
   - 모든 pair의 양성 근거를 요구하는 현재 정책과 결과를 비교한다.
   - 강한 pair 충돌은 연결 여부와 관계없이 계속 `INVALID`로 유지한다.
   - verified false merge 0을 유지하면서 자동 승인 recall이 증가하는지 확인한다.
2. **역할 충돌 87건을 재검수한다.**
   - 우선순위는 gold `EVIDENCE_ONLY`를 모델이 `REJECTED`한 62건이다.
   - gold 오류와 모델 오류를 분리한 뒤, 반복되는 관계 유형만 프롬프트 예시로 반영한다.
3. **`AMBIGUOUS` 표본을 확충한다.**
   - 현재 support 3으로 macro F1이 불안정하다.
   - gate 보류 사례에서 실제로 정보가 부족한 후보를 추가 검수해 희소 역할 표본을
     최소 20건 이상 확보한다.
4. **최종 단계에서 별도 holdout 평가를 한다.**
   - 현재 100건은 프롬프트 개선에 사용한 개발셋이다.
   - 개발에 사용하지 않은 별도 case에서 proposal precision, 자동 승인 coverage,
     verified false merge를 최종 확인한다.

## Iteration 4: 연결 그래프 identity pair 게이트

### 변경 목적

기존 게이트는 identity 대안의 모든 pair가 각각 `merge_eligible=true`인 완전 그래프를
요구했다. 3개 이상의 동일 실체 원천에서 A-B와 A-C의 독립 근거가 충분해도 B-C 직접
근거가 없으면 전체 대안을 보류했다.

`identity-pair-gate-v2`는 다음 규칙을 사용한다.

1. 강한 충돌이 있는 pair는 기존처럼 항상 `INVALID`다.
2. pair 행 자체가 누락된 경우도 기존처럼 `INVALID`다.
3. 강한 충돌이 없고 `merge_eligible=true`인 edge로 모든 identity 멤버가 연결되면
   pair evidence gate를 통과한다.
4. 멤버가 2개인 대안은 두 후보 사이 edge 하나가 필요하므로 기존 정책과 동일하다.
5. 정책의 `active_evidence_mode`를 `complete_graph`로 바꾸면 기존 방식으로 회귀할 수 있다.

### 동일 v3 decision 재평가 결과

LLM을 다시 호출하지 않고 같은 v3 decision 100건을 두 게이트 방식으로 비교했다.

| 지표 | 완전 그래프 | 연결 그래프 | 변화 |
|---|---:|---:|---:|
| `VERIFIED` case | 64 | 65 | +1 |
| `NEEDS_MANUAL_REVIEW` case | 24 | 23 | -1 |
| `INVALID` case | 12 | 12 | 0 |
| 자동 승인 pair | 24 | 27 | +3 |
| 자동 승인 precision | 1.0 | 1.0 | 0 |
| 자동 승인 recall | 0.406780 | 0.457627 | +0.050847 |
| 자동 승인 F1 | 0.578313 | 0.627907 | +0.049594 |
| verified false merge pair | 0 | 0 | 0 |
| verified false split pair | 0 | 0 | 0 |
| 보류 pair | 35 | 32 | -3 |
| 보류 pair rate | 0.593220 | 0.542373 | -0.050847 |
| 보류 pair case | 21 | 20 | -1 |
| `INSUFFICIENT_PAIR_EVIDENCE` case | 13 | 8 | -5 |
| `INSUFFICIENT_PAIR_EVIDENCE` 영향 pair | 27 | 12 | -15 |

새로 자동 승인된 case는 `비무장지대`다. identity 멤버 3개가 두 개의 양성 edge로
연결되고 강한 충돌이 없으며, gold pair 3개와 모델 cluster가 정확히 일치한다.

### Iteration 4 판단

- 자동 승인 precision 1.0과 verified false merge 0을 유지하면서 coverage가 약 5.1%p
  증가했다.
- 강한 충돌과 category 충돌에 대한 차단 결과는 변하지 않았다.
- 연결 그래프 방식은 `identity_pair_gate.active_evidence_mode=connected_graph`로
  활성화한다.
- 평가 JSON은 `identity_pair_gate_policy_version=identity-pair-gate-v2`와
  `identity_pair_gate_evidence_mode=connected_graph`를 함께 기록한다.
- 다음 개선은 연결 규칙을 더 완화하는 것이 아니라, 남은 term alignment 15쌍과
  insufficient evidence 12쌍을 case별로 감사하는 것이다.

# Independent Pack Validation

문제 생성 파이프라인과 연결되지 않은 JSON 입출력 전용 closed-pack 검증기입니다.

```powershell
.\.venv\Scripts\python.exe -m ai.pack_generation --input <input.json> --output <output.json>
```

기존 문제은행에 사실을 중복 사용하지 않으려면 `--existing-bank <bank.json>`을 함께 지정합니다.

입력은 검수자가 구성한 `packs` 배열을 사용합니다. 도구가 후보를 고르거나 pack을 자동 조합하지 않습니다.

검사는 서로 다른 owner 9개, 고유 frame 2개 이상, fact/material 근거 존재, 기존 문제은행을 포함한 `choice_fact_id` 및 공백 정규화 `fact_basis` 중복 금지까지입니다. 의미가 같은 의역은 상류 검수 단계에서 동일한 `fact_fingerprint`를 제공해 차단합니다.

## Fact Graph 자동 Pack

Fact Graph는 `CanonicalEntity` 후보 검색에만 사용하고, fact/material 내용과
실제 `evidence_chunk_ids`는 같은 owner의 민백 PostgreSQL RAG에서 읽습니다.
의미 검수는 Pack당 LLM 한 번만 호출합니다.

```powershell
.\.venv\Scripts\python.exe -m ai.pack_generation.graph_builder `
  --spec <graph-pack-spec.json> `
  --output <pack-bank.json> `
  --model <model>
```

spec에는 `anchor_node_id`, `topic_id`, `era_id`, `owner_type`, `difficulty`,
`question_frames` 등을 명시합니다. 후보 거리는 난이도와 동일하게 1·2·3홉을
정확히 사용합니다. 프레임·분류값을 추정하거나 `ProvisionalEntity`, 합성 근거
ID로 fallback하지 않습니다. fact와 material이 같은 청크를 공유하면 검수 응답이
`material_fact_semantically_distinct=true`를 명시해야만 Pack을 허용합니다.

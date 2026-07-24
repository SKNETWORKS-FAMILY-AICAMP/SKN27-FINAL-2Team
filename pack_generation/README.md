# Independent Pack Validation

문제 생성 파이프라인과 연결되지 않은 JSON 입출력 전용 closed-pack 검증기입니다.

```powershell
.\.venv\Scripts\python.exe -m pack_generation --input <input.json> --output <output.json>
```

기존 문제은행에 사실을 중복 사용하지 않으려면 `--existing-bank <bank.json>`을 함께 지정합니다.

입력은 검수자가 구성한 `packs` 배열을 사용합니다. 도구가 후보를 고르거나 pack을 자동 조합하지 않습니다.

검사는 서로 다른 owner 9개, 고유 frame 2개 이상, fact/material 근거 존재, 기존 문제은행을 포함한 `choice_fact_id` 및 공백 정규화 `fact_basis` 중복 금지까지입니다. 의미가 같은 의역은 상류 검수 단계에서 동일한 `fact_fingerprint`를 제공해 차단합니다.

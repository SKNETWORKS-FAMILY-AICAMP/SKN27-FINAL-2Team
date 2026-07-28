# Graph Pack 50문항

- `packs/graph_standard_10/pack_bank.json`: Fact Graph 후보와 owner-scoped RAG 근거로 검수한 10팩
- `questions/graph_standard_50/questions.json`: 최종 평가를 통과한 고유 50문항
- `questions/graph_standard_50/choice_explanations.jsonl`: 선지별 짧은 해설
- `questions/graph_standard_50/service_classifications.jsonl`: 앱 서비스 분류

서비스 DB 적재:

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation.postprocess_questions import-db `
  --input ai\question_generation\data\production_20260727\questions\graph_standard_50\questions.json `
  --explanations ai\question_generation\data\production_20260727\questions\graph_standard_50\choice_explanations.jsonl `
  --classifications ai\question_generation\data\production_20260727\questions\graph_standard_50\service_classifications.jsonl `
  --dry-run
```

검증 결과를 확인한 뒤 같은 명령에서 `--dry-run`만 제거합니다.

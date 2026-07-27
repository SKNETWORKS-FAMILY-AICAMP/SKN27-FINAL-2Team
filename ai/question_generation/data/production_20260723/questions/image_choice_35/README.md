# 이미지 선지형 35문항

서비스 DB 적재 전 dry-run으로 검증합니다.

```powershell
$data = "ai\question_generation\data\production_20260723\questions\image_choice_35"

.\.venv\Scripts\python.exe -m ai.question_generation.postprocess_questions import-db `
  --input "$data\questions.json" `
  --explanations "$data\choice_explanations.jsonl" `
  --classifications "$data\service_classifications.jsonl" `
  --dry-run
```

`count`가 35인지 확인한 뒤 같은 명령에서 `--dry-run`만 제거합니다.

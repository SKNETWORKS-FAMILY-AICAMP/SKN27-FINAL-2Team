# 왕 엔티티 metadata 보강 가이드

이미 임베딩된 `rag.document_chunks`의 본문과 임베딩은 그대로 두고, 왕 이름 식별용 metadata만 추가한다.

## 목적

`태조`, `성종`, `현종`, `숙종`처럼 여러 왕조에서 반복되는 시호를 안전하게 구분하기 위해 chunk metadata에 왕 엔티티 정보를 추가한다.

## 실행

테스트 실행:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\enrich_king_metadata.py --dry-run --limit 1000 --sample 10
```

전체 업데이트:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\enrich_king_metadata.py --sample 12
```

통계 갱신:

```sql
ANALYZE rag.document_chunks;
```

## 추가되는 metadata

```json
{
  "mentioned_kings": [
    {
      "entity_id": "goryeo_taejo",
      "display_name": "고려 태조",
      "dynasty": "고려",
      "posthumous_name": "태조",
      "personal_name": "왕건",
      "reign_start": 918,
      "reign_end": 943,
      "era": "고려 시대",
      "aliases": ["고려 태조", "태조 왕건", "왕건"],
      "matched_aliases": ["왕건"]
    }
  ],
  "king_aliases": ["왕건"],
  "king_dynasties": ["고려"]
}
```

왕조 단서가 부족한 시호는 확정하지 않고 `ambiguous_king_mentions`에 남긴다.

```json
{
  "ambiguous_king_mentions": [
    {
      "posthumous_name": "태조",
      "candidate_display_name": "조선 태조",
      "candidate_dynasty": "조선",
      "matched_aliases": ["태조"]
    }
  ]
}
```

## 조회 예시

왕건이 언급된 chunk:

```sql
SELECT title, metadata->'mentioned_kings'
FROM rag.document_chunks
WHERE jsonb_exists(metadata->'king_aliases', '왕건')
LIMIT 10;
```

조선 왕이 언급된 chunk:

```sql
SELECT title, metadata->'mentioned_kings'
FROM rag.document_chunks
WHERE jsonb_exists(metadata->'king_dynasties', '조선')
LIMIT 10;
```

모호한 시호가 남은 chunk:

```sql
SELECT title, metadata->'ambiguous_king_mentions'
FROM rag.document_chunks
WHERE jsonb_exists(metadata, 'ambiguous_king_mentions')
LIMIT 10;
```

## 주의

- 이 스크립트는 `metadata`만 수정하므로 재임베딩이 필요 없다.
- chunk 본문이나 제목을 바꾸는 작업이 아니므로 vector index는 그대로 사용한다.
- 왕 엔티티 사전이 부족하면 `KINGS` 목록에 alias를 추가한 뒤 다시 실행한다.

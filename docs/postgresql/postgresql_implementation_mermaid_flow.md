# PostgreSQL RAG Mermaid 흐름도

## 1. 전처리와 적재 흐름

```mermaid
flowchart LR
    raw["Raw Data\n사료 / 신편 한국사 / 이미지 / 연표"]
    preprocess["Preprocess\n정규화 / 번호 제거 / 참고문헌 제거 / 청킹"]
    processed["Processed Files\n*.documents.jsonl\n*.chunks.jsonl\nhistory_timeline_processed.csv"]
    postgres["PostgreSQL\nrag.document_chunks\nrag.history_timeline"]
    embedding["Embedding\ntext-embedding-3-small"]
    indexes["Indexes\nHNSW / trigram GIN / JSONB GIN"]

    raw --> preprocess
    preprocess --> processed
    processed --> postgres
    postgres --> embedding
    embedding --> indexes
```

## 2. 챗봇 검색 흐름

```mermaid
flowchart TD
    q["User Question"]
    route["Intent Routing\nconcept / image / problem / chat"]
    image["Image Search\nmetadata title/url only"]
    graph["Neo4j Context\nrelation keywords"]
    pg["PostgreSQL Hybrid Search\nvector + keyword + metadata"]
    llm["Answer Generator"]
    ui["Chatbot UI"]

    q --> route
    route -->|image request| image
    route -->|concept/problem| graph
    graph --> pg
    route -->|concept/problem| pg
    pg --> llm
    image --> llm
    llm --> ui
```

## 3. 검색 인덱스 사용 흐름

```mermaid
flowchart LR
    query["Query"]
    vector["Vector Search\nembedding <=> query_vector\nHNSW"]
    keyword["Keyword Search\ntrigram similarity"]
    metadata["Metadata Filter\nJSONB / source_type"]
    merge["Score Merge"]
    topk["Top-K Context"]

    query --> vector
    query --> keyword
    query --> metadata
    vector --> merge
    keyword --> merge
    metadata --> merge
    merge --> topk
```


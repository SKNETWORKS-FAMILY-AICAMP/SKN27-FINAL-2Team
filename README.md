# -SKN27-FINAL-2Team

# python 3.12

# 폴더구조

```text
SKN27-FINAL-2Team/
|-- README.md
|-- requirements.txt
|-- ai/
|   |-- llm/
|   |-- ml/
|   `-- models/
|-- app/
|   |-- README.md
|   |-- manage.py
|   |-- config/
|   |   |-- settings.py
|   |   |-- urls.py
|   |   |-- asgi.py
|   |   `-- wsgi.py
|   |-- user/
|   |-- chatbot/
|   |-- analytics/
|   |-- diagnosis/
|   `-- question/
|-- docs/
|   |-- README.md
|   |-- setup-guide.md
|   `-- image/
|-- etl/
|   |-- README.md
|   |-- crawling/
|   `-- exam_question_pipeline/
|-- storage/
|   |-- README.md
|   |-- postgre/
|   `-- neo4j/
`-- test/
    |-- README.md
    |-- CJ/
    |-- HS/
    |-- MK/
    `-- YJ/
```

- `ai/`: LLM, ML, 모델 파일 작업 공간
- `app/`: Django 프로젝트 및 서비스 앱
- `docs/`: 프로젝트 문서와 이미지 자료
- `etl/`: 크롤링 및 문제 데이터 파이프라인 작업 공간
- `storage/`: PostgreSQL, Neo4j 관련 저장소 작업 공간
- `test/`: 팀원별 테스트 작업 공간


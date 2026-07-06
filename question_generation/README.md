# Question Generation

한국사 문제 생성 파이프라인 실험 폴더입니다.

현재 범위:

- PDF 개념 요약 문서에서 topic 후보 빈도 집계
- 기존 교과서 용어 CSV와 교차해 이상한 토막 키워드 제거
- 선택적으로 Neo4j 메타를 붙여 `topic_type`, 시대, 주제 후보 생성

기본 실행 예:

```powershell
python question_generation/build_topic_keywords.py `
  --pdf "C:\Users\Playdata\Downloads\에듀윌 한국사능력검정시험_빈출개념요약집(저작권 에듀윌).pdf" `
  --pdf "C:\Users\Playdata\Downloads\벼락치기 한능검⚡2026 심화 필수개념 노트.pdf" `
  --pdf "C:\Users\Playdata\Downloads\한능검 심화1급 정리최종.pdf" `
  --top-n 300 `
  --neo4j
```

출력:

- `question_generation/outputs/topic_keywords_seed.csv`
- `question_generation/outputs/topic_keywords_seed_summary.json`

시대 보강:

```powershell
python question_generation/supplement_topic_keywords.py --per-era 80
```

출력:

- `question_generation/outputs/topic_keywords_seed_balanced.csv`
- `question_generation/outputs/topic_keywords_seed_balanced_summary.json`

SLLM 기준 첫 노드 샘플 생성:

```powershell
python question_generation/select_seed.py --seed 20260706 --n 5
```

역할:

- `topic_keywords_seed_balanced.csv`에서 출제 topic 후보를 고른다.
- v41 전체 학습 데이터에서 실제 SLLM input schema 조합을 자동 추출한다.
- 다음 노드가 채울 `material`, `answer_fact_basis` 자리를 남겨 둔 SLLM 입력 preview를 만든다.

출력:

- `question_generation/outputs/sllm_type_schema_seed.csv`
- `question_generation/outputs/select_seed_sample.json`

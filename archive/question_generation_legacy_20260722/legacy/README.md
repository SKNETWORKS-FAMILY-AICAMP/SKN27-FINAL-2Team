# Legacy Question Generation Paths

이 폴더는 closed-pack 현행 흐름 이전의 실행 경로를 보존합니다.

- `retrieval/choice_pool.py`: 실행 중 자유 ChoiceFact 후보 선택
- `retrieval/pack_repository.py`: PostgreSQL `qgen.basis_packs` 직접 조회
- `workflows/batch.py`: 기존 DB pack batch
- `workflows/mock_exam.py`: 기존 DB pack 모의고사 생성
- `workflows/finalize.py`: 기존 모의고사 평가·보충
- `interactive_cli.py`: 기존 DB pack 대화형 실행기

현행 코드에서 자동 import하지 않습니다. 재현이 필요할 때만 `question_generation.legacy...` 경로를 직접 사용합니다.

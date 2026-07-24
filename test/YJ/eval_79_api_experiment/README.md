# 제79회 모의 한능검 심화 API 평가 실험

이 폴더는 `제79회_모의_한능검_심화_문제.docx.pdf`와 `제79회_모의_한능검_심화_정답해설.docx.pdf`를 대상으로, 현재 평가지표 `docs/hanneung_sllm_eval_rubric_v1_8.md`를 OpenAI API judge에 넣어 문항을 평가하기 위한 실험 폴더입니다.

## 구조

| 경로 | 역할 |
|---|---|
| `extract_79.py` | PDF에서 문항, 선택지, 배점, 정답, 해설을 추출해 JSON/JSONL 생성 |
| `evaluate_with_openai.py` | 추출된 문항을 한 문항씩 OpenAI API로 평가 |
| `data/raw/` | PDF 원문 추출 텍스트 |
| `data/processed/questions.jsonl` | 평가 입력용 문항 데이터 |
| `results/` | API 평가 결과 |

## 실행

번들 Python 기준:

```powershell
$PY="C:\Users\Playdata\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $PY test\YJ\eval_79_api_experiment\extract_79.py
```

API 평가 드라이런:

```powershell
& $PY test\YJ\eval_79_api_experiment\evaluate_with_openai.py --limit 1 --dry-run
```

API로 1문항만 평가:

```powershell
& $PY test\YJ\eval_79_api_experiment\evaluate_with_openai.py --limit 1
```

특정 문항만 평가:

```powershell
& $PY test\YJ\eval_79_api_experiment\evaluate_with_openai.py --question-id 27
```

문항 범위 평가:

```powershell
& $PY test\YJ\eval_79_api_experiment\evaluate_with_openai.py --question-start 31 --question-end 40
```

전체 50문항 평가:

```powershell
& $PY test\YJ\eval_79_api_experiment\evaluate_with_openai.py --sleep 0.5
```

## API 키

`OPENAI_API_KEY`는 환경 변수 또는 프로젝트 루트의 `.env`에서 읽습니다. 키 값은 출력하지 않습니다.

모델은 기본값으로 `gpt-4.1-mini`를 사용합니다. 바꾸려면:

```powershell
& $PY test\YJ\eval_79_api_experiment\evaluate_with_openai.py --limit 1 --model "원하는_모델명"
```

또는 `.env`에 아래 값을 둘 수 있습니다.

```text
OPENAI_EVAL_MODEL=gpt-4.1-mini
```

## 평가 방식

- API 호출 1회에는 문항 1개만 넣습니다.
- Gate FAIL이면 점수 채점을 하지 않고, 실패 Gate에 따라 `repair` 또는 `regenerate`로 판정합니다.
- Gate uncertain이면 `needs_verification`으로 판정하도록 지시합니다.
- Gate PASS일 때만 문제 10점, 해설 5점을 채점합니다.
- 결과는 JSONL로 저장되며, 각 줄이 문항 1개 평가 결과입니다.

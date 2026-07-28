# 머신러닝 또는 딥러닝 파이튜닝하는 공간 

- llm : sllm 파인튜닝 작업 공간

- ml : 머신러닝 작업 공간

- question_generation : 문제 생성·평가·후처리 파이프라인

- pack_generation : 문제 생성용 pack 검증·구축 도구

- models : 모델 저장 공간

## Pack·문제 통합 CLI

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation.interactive_cli
```

GraphDB의 시대·주제·owner 유형과 pack 수를 선택해 일반 pack을 만들고 바로 회전 출제할 수 있습니다.
검수된 사건 계획과 이미지 pack은 기존 연표 builder·이미지 manifest 변환기로 연결합니다.
새 pack 생성에는 `.env`의 `QGEN_V41_VALIDATION`에 V41 validation JSONL 경로가 필요합니다.

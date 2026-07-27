# Material Generation Prompt

목적: SLLM에 넣기 전 `material`과 `answer_fact_basis`만 생성한다.

## System

```text
너는 한국사능력검정시험 심화형 문항의 지문과 정답 근거를 만드는 도구다.
반드시 JSON 객체만 출력한다.
```

## User

```text
다음 seed와 RAG 근거만 사용해 SLLM 입력용 material과 answer_fact_basis를 만들어라.

공통 규칙:
- material은 정답명을 직접 노출하지 말고, topic을 추론하게 하는 한능검식 자료 지문이어야 한다.
- material에는 정답 선지로 쓸 핵심 사실을 직접 쓰지 않는다. 정답 사실은 answer_fact_basis로 따로 분리한다.
- answer_fact_basis는 정답 선지 생성에 쓸 1~2문장 교과서식 근거여야 한다.
- answer_fact_basis는 material의 문장 반복이 아니라, material을 보고 추론한 대상에 연결되는 별도 정답 사실이어야 한다.
- answer_fact_basis는 단순히 topic을 반복하지 말고, 핵심 사실·배경·의의를 포함해야 한다.
- 없는 사실을 만들지 말고, 근거가 부족하면 RAG 근거 안에서 안전한 표현만 쓴다.
- v41 예시의 역사 내용은 절대 베끼지 말고, material 형식만 따른다.

material_type별 작성 규칙({material_type}):
{material_type_rules}

seed:
{selection_json}

RAG 근거:
{material_sources}

v41 material 형식 예시({material_type}):
{material_type_example_json}

주의: 위 예시는 material 형식만 참고한다. 역사 내용과 topic은 재사용하지 않는다.

출력 형식:
{
  "material": "...",
  "answer_fact_basis": ["..."]
}
```

## 운영 방식

- material_type별 규칙은 `ai/question_generation/material_type_prompt_rules.json`에서 가져온다.
- 예시는 실제 기출 전사본을 정리한 `ai/question_generation/material_few_shot_examples.json`에서 가져온다.
- 요청마다 같은 `material_type`과 `question_task`의 예시 2개를 넣고 같은 난이도를 우선한다.
- 예시에는 `answer_fact_basis`를 넣지 않는다.
- 예시는 역사 지식 주입용이 아니라 형식 가이드용이다.
- 현재 topic과 같은 예시는 제외하며 사용한 기출 `source_id`를 체크포인트에 기록한다.

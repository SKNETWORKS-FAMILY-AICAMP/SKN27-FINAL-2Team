from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "gpt-4.1-mini"
PROBLEM_SCORE_MAX = {
    "target_difficulty_fit": 4,
    "choice_quality": 6,
}
EXPLANATION_SCORE_KEYS = [
    "correct_answer_reason",
    "clue_usage",
    "distractor_elimination",
    "answer_explanation_match",
    "explanation_factuality",
]
TOKEN_STOPWORDS = {
    "것",
    "것은",
    "가장",
    "다음",
    "자료",
    "설명",
    "옳은",
    "적절한",
    "대한",
    "통해",
    "통하여",
    "이를",
    "이것",
    "하였다",
    "한다",
    "있다",
    "있었다",
    "아니다",
    "되었다",
    "사용",
    "시행",
    "설치",
    "제시",
    "내용",
    "관련",
}
DOCUMENT_ARTIFACT_MARKERS = [
    "[[PAGE",
    "한국사능력검정시험",
    "문제지",
    "정답해설",
]
RELATION_CLAIM_MARKERS = [
    "발전",
    "해체",
    "이어",
    "계승",
    "통합",
    "전환",
    "계기",
    "원인",
    "결과",
    "이후",
    "뒤",
    "후에",
    "지원을 받아",
    "탄압으로",
]
GENERIC_G6_SHARED_UNITS = {
    "정부",
    "단체",
    "인물",
    "사건",
    "제도",
    "정책",
    "활동",
    "운동",
    "시기",
    "시대",
    "주체",
    "대상",
    "배경",
}
GENERIC_G6_SHARED_SUFFIXES = (
    "정부",
    "단체",
    "인물",
    "기관",
    "국가",
)
PARTICLE_SUFFIXES = [
    "으로부터",
    "로부터",
    "에서는",
    "에게는",
    "이라는",
    "라는",
    "에서",
    "에게",
    "으로",
    "으로서",
    "로서",
    "과",
    "와",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "도",
    "만",
    "처럼",
    "부터",
    "까지",
]
def script_dir() -> Path:
    return Path(__file__).resolve().parent


def find_repo_root() -> Path:
    current = script_dir()
    for path in [current, *current.parents]:
        if (path / ".git").exists() or (path / "docs").exists():
            return path
    return current


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return records


def choice_text(record: dict[str, Any]) -> str:
    lines = []
    for choice in record["choices"]:
        lines.append(f'{choice["label"]} {choice["text"]}')
    return "\n".join(lines)


def build_messages(rubric: str, record: dict[str, Any]) -> list[dict[str, str]]:
    system_prompt = f"""
너는 한국사능력검정시험 심화 문항을 검수하는 LLM judge이다.
아래 평가지표를 반드시 따른다.

{rubric}

중요 규칙:
- 한 번에 입력된 문항 1개만 평가한다.
- Evidence Pack, target_period, target_topic, target_major_type, target_minor_type은 평가하지 않는다.
- G6는 정답명이 직접 나왔는지만 보는 항목이 아니다. 발문·자료와 정답 선지가 같은 역사 명제를 반복하면 정답명이 없어도 G6 FAIL이다.
- G6에서 표현이 달라도 발문·자료의 핵심 조건을 정답 선지가 같은 의미로 반복하면 G6 FAIL이다.
- 정답 선지가 새로운 수단·방법을 덧붙였더라도, 발문·자료에 이미 제시된 핵심 활동·결과를 정답 선지만 유일하게 재진술하면 G6 FAIL이다. 예: 발문에 "독립운동 자금을 모았다"가 있고 정답 선지에 "독립 공채를 발행하여 자금을 마련하였다"만 있으면 `독립 공채`는 새 정보라도 `자금 모집·마련` 의미가 반복되므로 G6-2를 검토한다.
- 발문·자료명에 정답 대상명은 보이지만 선택지에서 별도 업적·정책·결과를 골라야 하면 G6 FAIL이 아니라 G6-3 Gate PASS 후 난이도 감점이다.
- target_score 2점에서 정답 대상명이 직접 노출되면 target_difficulty_fit은 최대 2점이다. target_score 3점에서 정답 대상명이 직접 노출되면 target_difficulty_fit은 최대 1점이다.
- 정답 선지만 길이, 구체성, 발문 표현 반복, 문장 형식에서 두드러지는 외형 편향 신호가 2개 이상이면 G6-4 FAIL이다.
- G6를 PASS로 두려면 정답 선지의 핵심 명제와 발문·자료의 핵심 문장을 직접 대조하고, 왜 문장 매칭만으로 정답 확정이 불가능한지 설명해야 한다.
- G3는 표시 정답만 보는 항목이 아니다. 발문 조건을 문자 그대로 만족하는 역사적 선지가 2개 이상이면 "가장 적절"로 덮지 말고 G3 FAIL이다.
- 발문 조건을 만족하는 다른 선지가 "부분적으로 가능", "덜 대표적", "가장 부합하지 않음"이라도, 발문에 그 배제 기준이 명시되어 있지 않으면 조건 충족 후보로 센다.
- 발문·자료에 없는 숨은 배제 조건을 만들지 않는다. 예를 들어 발문이 단순히 "고려 시대 화폐사"를 요구하면 "자체 주조만 허용", "가장 대표적인 화폐만 허용", "유통 부진이 있어야 함" 같은 조건을 추가하지 않는다.
- 같은 시대·같은 주제에 속하는 역사적으로 성립하는 선지는 덜 대표적이어도 발문 조건 충족 후보로 센다.
- 특정 용어가 교과서에서 어느 나라·시대의 대표 키워드로 외워지는지만 보고 오답 처리하지 않는다. 선택지 원문 전체를 일반 역사 명제로 바꾼 뒤, 그 명제가 발문 대상에도 성립할 수 있으면 G3 정답 후보로 센다.
- 예: 발문이 부여를 가리키고 선택지에 "여러 가들이 제가 회의를 통해 국가의 중대사를 결정하였다"와 "순장의 풍습이 있었다"가 함께 있으면, `제가 회의=고구려`라는 암기 라벨만으로 ②를 배제하지 않는다. `여러 가들이 국가 중대사를 결정`이라는 의미가 부여의 여러 가 지배 구조와 겹치면 G3 복수 정답 위험으로 본다.
- 선택지 품질은 발문이 요구하는 응답 범주를 기준으로 판단한다. 발문이 경제 상황을 물으면 무역항, 화폐, 토지 제도, 작물, 상업 주체가 모두 경제 상황에 속할 수 있다.
- 오답 품질은 "같은 시대/같은 분류"만으로 판단하지 않는다. 발문 단서를 적용한 뒤에도 실제 후보로 남는 오답만 유효 매력 오답으로 센다.
- Gate PASS는 고득점 근거가 아니다. Gate PASS는 문항이 성립한다는 뜻일 뿐이며, 문제 점수는 target_score 대비 난이도와 선택지 품질을 별도로 박하게 평가한다.
- 정답이 맞고 역사 사실이 맞아도, 대표 단서 하나로 바로 풀리거나 오답이 시대·주제·조건 차이로 즉시 제거되면 낮은 점수를 준다.
- target_score 1점에서는 어려운 유효 매력 오답을 강제하지 않는다. 대신 같은 시대권 또는 같은 주제권의 정상 오답 수를 세고, 3~4개=4점, 2개=3점, 1개=2점, 0개=0점으로 채점한다.
- target_score 2점에서는 발문 단서 적용 후에도 비교 후보로 남는 유효 매력 오답이 2개 이상이면 4점, 1개면 3점, 0개면 0점이다.
- target_score 3점에서는 발문 단서 적용 후에도 비교 후보로 남는 유효 매력 오답이 3개 이상이면 4점, 2개면 3점, 1개면 2점, 0개면 0점이다.
- 오답이 역사적으로 참이라는 이유만으로 유효 매력 오답으로 세지 않는다. 발문 단서를 적용한 뒤 바로 제거되는 오답은 2점·3점 문항에서 유효 매력 오답이 아니다.
- target_score 2점인데 대표 단서 하나로 정답이 바로 확정되면 target_difficulty_fit은 최대 1점이다.
- target_score 3점인데 정답 대상 식별 후 바로 선택 가능하면 target_difficulty_fit은 최대 1점이다.
- target_score 2점 난이도는 다음 고정표를 따른다: 단서 2개 이상 식별 후 업적·정책·시기 비교 필요=4점, 대상 식별은 쉽지만 선택지 비교 1회 이상 필요=3점, 대상명·자료명 직접 노출 또는 너무 강한 대표 단서=2점, 대표 단서 하나만 보고 정답 선지까지 거의 바로 고름=1점, 2점 구조 아님=0점.
- 선택지 품질은 response_category_fit_score 0~1점 + no_duplicate_or_inclusion_score 0~1점 + effective_attractive_distractor_score 0~4점의 합이다. 비교 단위와 중복이 모두 좋고 오답 품질이 0점이면 선택지 품질은 2점이지 5~6점이 아니다.
- Gate FAIL이면 문제 점수와 해설 점수는 null로 둔다.
- Gate FAIL 중 G1/G2/G5/G6만 실패한 문항은 부분 수정 대상이므로 final_decision을 "repair"로 둘 수 있다.
- Gate FAIL 중 G3/G4가 실패한 문항은 정답 구조나 발문·자료 고증이 흔들린 것이므로 final_decision을 "regenerate"로 둔다.
- Gate uncertain이면 최종 판정은 needs_verification으로 둔다.
- Gate PASS일 때만 문제 10점, 해설 5점을 채점한다.
- 역사 사실성은 네 일반 한국사 지식으로 판단하되, 불확실하면 PASS로 단정하지 말고 uncertain으로 둔다.
- G5는 "정답 대상에 대입하면 틀린 오답"을 잡는 항목이 아니다. 오답 문장 자체가 한국사 세계 안에서 성립하는지 판단한다.
- 다른 인물·시대의 실제 정책·사건·업적으로 성립하는 오답은 G5 FAIL이 아니라 발문 조건 불충족 오답이다.
- G5 FAIL은 가짜 용어·가짜 사건·가짜 제도, 또는 서로 다른 주체·사건·결과를 한 문장 안에서 명백히 허위 결합한 경우에만 준다.
- choice_verification의 `original_choice_text`에는 입력 선택지 원문을 그대로 적는다.
- G5의 `historical_proposition`은 선택지 원문의 핵심 술어, 관계, 결과를 보존해야 한다. 원문 선지를 더 안전한 참 문장으로 바꿔 쓰지 않는다.
- 오답 선지가 "A로 발전", "A로 해체", "A로 이어짐", "A를 계승", "A와 통합"처럼 관계를 주장하면 그 관계 자체가 역사적으로 성립하는지 검증한다. 관계를 생략하고 A가 실제로 존재한다는 사실만 쓰면 안 된다.
- G5의 `historical_proposition`과 `whole_claim_validity`는 선택지 문장 자체를 기준으로 판단한다. 선택지에 명시되지 않은 발문 주어를 임의로 historical_proposition에 대입하지 않는다.
- 발문 주어·조건을 대입한 판단은 `stem_applied_claim`과 `satisfies_stem_condition`에서만 다룬다.
- 주체 식별형 문항에서 오답 선지가 생략 주어를 갖더라도, 그 선지의 행위·사건이 다른 실제 주체의 역사 사실이면 `g5_error_classification.type`은 "valid_other_fact"이고 G5 PASS이다.
- 주체가 생략된 오답의 `historical_proposition`에는 발문 정답 주체를 넣지 말고, 그 행위·사건을 실제로 수행한 역사 주체를 복원한다. 실제 주체를 모르겠으면 uncertain으로 둔다.
- 오답 선지를 발문 주체에 대입했을 때 틀린다는 이유만으로 G5 FAIL을 주지 않는다. 같은 서술이 다른 실제 주체의 사실로 성립하면 `valid_other_fact`, `satisfies_stem_condition=no`이다.
- 예를 들어 발문 주체 X를 묻는 문항에서 오답이 "B 선언서를 발표하였다"이면 `historical_proposition`은 "실제 발표 주체가 B 선언서를 발표하였다"가 되어야 한다. "X가 B 선언서를 발표하였다"라고 쓰면 안 된다.
- 예를 들어 발문 주체 X를 묻는 문항에서 오답이 "Y 전투에서 적군을 격파하였다"이면 `historical_proposition`은 "실제 전투 주체가 Y 전투에서 적군을 격파하였다"가 되어야 한다.
- 반대로 "A 단체로 발전적으로 해체되었다"처럼 전환·계승 관계를 말하는데 그 관계를 실제로 가진 역사 주체가 없으면 `fabricated_relation` 또는 `false_causality`로 본다.
- 오답 선지가 실제로 존재하지 않는 계승 관계, 인과 관계, 사건 순서, 주체-행위 결합을 만든 경우에만 `g5_error_classification.should_fail_g5`를 true로 둔다.
- `false_actor_action`, `false_time`은 신중하게 쓴다. 같은 문장이 다른 실제 역사 대상의 사실이면 `valid_other_fact`이고, 실제 대상이 불명확하지만 역사적 계열·맥락이 일부 있으면 `uncertain`이다.
- `g5_error_classification.type`이 `nonexistent_term_or_fact`, `mixed_fact_hybrid`, `fabricated_relation`, `false_causality`, `false_sequence`, `false_result` 중 하나이고 다른 실제 역사 대상의 사실 설명으로 성립하지 않으면 should_fail_g5는 true이다.
- 예: 이순신 문항에서 "칠천량 해전에서 전사하였다", "행주대첩을 지휘하였다"는 실제 역사 사실로 성립하므로 G5 PASS이다. "평평선을 타고 싸웠다", "자라선을 운용하였다"는 가짜 용어이므로 G5 FAIL이다. "척화비를 세운 뒤 갑신정변을 일으켰다", "만주를 정복하여 척화비를 세웠다", "훈민정음을 반포하기 위해 태학을 설립하였다"는 서로 다른 사실의 허위 결합이므로 G5 FAIL이다.
- Gate 판정 전에 반드시 `pre_gate_risk_scan`, `choice_verification.claim_parts`, `g6_claim_equivalence_check`, `gate_consistency_check`를 먼저 작성한다.
- `claim_parts`에서는 선택지 문장을 주체, 행위, 대상, 시기, 결과, 인과·순서로 분해한다. 부분 사실이 각각 맞아도 결합된 전체 문장이 틀리면 g5_error_classification.should_fail_g5는 true이다.
- `g6_claim_equivalence_check`에서는 발문 핵심 명제와 정답 선지 명제를 일반화된 한 문장으로 각각 환원한 뒤, 단순 키워드가 아니라 의미 단위가 같은지 판단한다.
- Gate 판정은 구조화 결과와 모순되면 안 된다. 예를 들어 오답의 g5_error_classification.should_fail_g5가 true이면 G5는 FAIL이어야 하고, g6_claim_equivalence_check.g6_should_fail이 true이면 G6는 FAIL이어야 한다.
- 반드시 JSON 객체만 출력한다. 마크다운 코드블록은 쓰지 않는다.
""".strip()

    user_prompt = f"""
다음 문항 1개를 평가하라.

question_id: {record["question_id"]}
target_score: {record["target_score"]}점

[발문/자료]
{record["stem"]}

[선택지]
{choice_text(record)}

[표시 정답]
{record.get("answer_label") or record.get("answer")}

[해설]
{record.get("explanation", "")}

출력 JSON 형식:
{{
  "question_id": {record["question_id"]},
  "target_score": {record["target_score"]},
  "gate": {{
    "G1": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G2": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G3": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G4": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G5": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G6": {{"status": "PASS|FAIL|uncertain", "reason": "..."}}
  }},
  "pre_gate_risk_scan": [
    {{
      "gate": "G3|G4|G5|G6",
      "risk": "가장 의심되는 탈락 가능성",
      "evidence": "발문/선지의 구체 표현",
      "if_true_consequence": "FAIL|uncertain|score_only"
    }}
  ],
  "choice_verification": [
    {{
      "choice": "①",
      "original_choice_text": "입력 선택지 원문",
      "historical_proposition": "발문 주어를 임의 대입하지 않은 선택지 자체의 독립 역사 명제",
      "stem_applied_claim": "발문 주어·조건을 대입했을 때의 문장",
      "claim_parts": {{
        "subject": "주체",
        "action": "행위",
        "object": "대상",
        "time": "시기",
        "result": "결과",
        "causal_or_sequence_relation": "인과·순서 관계"
      }},
      "whole_claim_validity": {{
        "status": "yes|no|uncertain",
        "true_parts": ["부분적으로 맞는 사실"],
        "false_or_unsupported_parts": ["틀리거나 결합 불가능한 부분"],
        "reason": "문장 전체가 역사 명제로 성립하는지 판단"
      }},
      "g5_error_classification": {{
        "type": "valid_other_fact|fabricated_relation|nonexistent_term_or_fact|mixed_fact_hybrid|false_actor_action|false_time|false_causality|false_sequence|false_result|uncertain",
        "should_fail_g5": false,
        "actual_subject_if_valid_other_fact": "다른 실제 주체가 있으면 적음",
        "valid_other_fact_status": "yes|no|uncertain",
        "is_fake_or_mixed_fact": false,
        "reason": "정상 오답인지, 없는 사실로 만든 오답인지 판단"
      }},
      "historically_valid": "yes|no|uncertain",
      "satisfies_stem_condition": "yes|no|uncertain",
      "note": "..."
    }}
  ],
  "stem_condition_check": {{
    "literal_stem_condition": "...",
    "material_constraints": ["시기/주제/범주 조건"],
    "satisfying_choices": ["④"],
    "satisfying_choice_count": 1,
    "multiple_answer_risk": false,
    "reason": "..."
  }},
  "g6_overlap_check": {{
    "correct_choice_core_claim": "...",
    "matching_stem_or_material_expression": "...",
    "overlap_type": "none|direct_name|target_name_exposure|same_proposition|weak_keyword|external_bias",
    "g6_should_fail": true
  }},
  "g6_claim_equivalence_check": {{
    "stem_core_claims": [
      {{
        "claim": "발문/자료의 핵심 명제를 일반화한 문장",
        "role": "정답 식별 단서|발문 조건|배경 정보"
      }}
    ],
    "correct_choice_claim": "정답 선지의 핵심 명제를 일반화한 문장",
    "shared_meaning_units": ["동일하거나 거의 같은 의미 단위"],
    "relation": "none|weak_keyword|target_name_exposure|partial_same_claim|same_core_claim|direct_copy|external_bias",
    "can_answer_by_text_matching_without_history": false,
    "g6_should_fail": false,
    "reason": "G6-1/G6-2/G6-3/G6-4 중 어느 판단인지 설명"
  }},
  "gate_consistency_check": {{
    "g5_fail_choices": ["g5_error_classification.should_fail_g5가 true인 오답 번호"],
    "satisfying_choice_count_from_choice_verification": 1,
    "g6_equivalence_requires_fail": false,
    "consistency_violations": ["구조화 결과와 Gate 판정이 충돌하면 적는다"],
    "reason": "Gate 최종 판정 전 자기검토"
  }},
  "choice_unit_analysis": {{
    "stem_response_category": "발문이 요구하는 응답 범주",
    "fits_response_category_by_choice": {{"①": true, "②": true, "③": true, "④": true, "⑤": true}},
    "fit_count": 5,
    "duplicate_or_inclusion_count": 0,
    "note": "..."
  }},
  "effective_distractor_analysis": {{
    "①": {{
      "historically_valid": true,
      "category_or_period_accessible": true,
      "remains_candidate_after_stem": false,
      "is_effective_attractive": false,
      "reason": "target_score 1점에서는 category_or_period_accessible이 정상 오답 수 계산에 쓰이고, 2점·3점에서는 is_effective_attractive가 유효 매력 오답 수 계산에 쓰인다."
    }}
  }},
  "gate_result": "PASS|FAIL|uncertain",
  "strongest_rejection_reasons": ["최대 3개"],
  "problem_score": null,
  "problem_score_detail": {{
    "target_difficulty_fit": {{
      "score": null,
      "clue_bundle_count": "...",
      "clue_type": "A|B|C",
      "solving_stage": "...",
      "knowledge_depth": "...",
      "matched_element_count": 0,
      "reason": "..."
    }},
    "choice_quality": {{
      "score": null,
      "response_category_fit_score": null,
      "no_duplicate_or_inclusion_score": null,
      "effective_attractive_distractor_score": null,
      "effective_count": 0,
      "fit_count": 0,
      "duplicate_or_inclusion_count": 0,
      "reason": "..."
    }}
  }},
  "explanation_score": null,
  "explanation_score_detail": {{
    "correct_answer_reason": {{"score": null, "reason": "..."}},
    "clue_usage": {{"score": null, "reason": "..."}},
    "distractor_elimination": {{"score": null, "reason": "..."}},
    "answer_explanation_match": {{"score": null, "reason": "..."}},
    "explanation_factuality": {{"score": null, "reason": "..."}}
  }},
  "total_score": null,
  "final_decision": "accept|accept_with_warning|revise|repair|regenerate|needs_verification",
  "revision_advice": ["수정 권고"]
}}

Gate PASS인 경우에는 문제 평가항목 score를 target_difficulty_fit 0~4점, choice_quality 0~6점으로 채우고 problem_score 합계를 계산하라.
choice_quality는 response_category_fit_score 0~1점, no_duplicate_or_inclusion_score 0~1점, effective_attractive_distractor_score 0~4점을 합산해 계산하라.
target_score 1점의 effective_count는 같은 시대권 또는 같은 주제권의 정상 오답 수로 적고, target_score 2점·3점의 effective_count는 발문 단서 적용 후에도 남는 유효 매력 오답 수로 적는다.
Gate PASS인 경우에는 각 해설 평가항목 score를 0 또는 1로 채우고 explanation_score 합계를 계산하라.
Gate FAIL 또는 uncertain이면 점수 항목 score는 null로 유지한다.
""".strip()

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def post_chat_completion(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    base_url: str,
    json_mode: bool,
    timeout: int,
    max_retries: int,
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries + 1):
        request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            retryable = exc.code in {408, 409, 429, 500, 502, 503, 504}
            if retryable and attempt < max_retries:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"OpenAI API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_retries:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

    raise RuntimeError("OpenAI API request failed after retries")


def parse_model_content(api_response: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    content = api_response["choices"][0]["message"]["content"]
    try:
        return json.loads(content), content
    except json.JSONDecodeError:
        return None, content


def normalize_status(value: Any) -> str:
    return str(value or "").strip().lower()


def score_or_none(value: Any, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and int(value) == value:
        score = int(value)
        if minimum <= score <= maximum:
            return score
    return None


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return normalize_status(value) in {"true", "yes", "pass"}


def decision_from_total(total: int) -> str:
    if total >= 14:
        return "accept"
    if total >= 12:
        return "accept_with_warning"
    if total >= 9:
        return "revise"
    return "regenerate"


def failed_gate_ids(parsed: dict[str, Any]) -> list[str]:
    gate = parsed.get("gate")
    if not isinstance(gate, dict):
        return []
    failed = []
    for key in sorted(gate):
        value = gate.get(key)
        if isinstance(value, dict) and normalize_status(value.get("status")) == "fail":
            failed.append(str(key))
    return failed


def repair_targets_from_gates(parsed: dict[str, Any]) -> list[str]:
    targets = []
    for gate_id in failed_gate_ids(parsed):
        if gate_id in {"G1", "G2"}:
            targets.append(f"{gate_id}: 형식/추출 텍스트 정리")
        elif gate_id == "G5":
            targets.append("G5: 역사 오류 오답 선지 교체")
        elif gate_id == "G6":
            targets.append("G6: 발문 단서 또는 정답 선지 표현 재작성")
        elif gate_id == "G3":
            targets.append("G3: 정답 후보 수 재설계")
        elif gate_id == "G4":
            targets.append("G4: 발문/자료 고증 재작성")
        else:
            targets.append(f"{gate_id}: 원인 확인")
    return targets


def decision_from_gate_failure(parsed: dict[str, Any]) -> str:
    failed = set(failed_gate_ids(parsed))
    if not failed:
        return "regenerate"
    if failed & {"G3", "G4"}:
        return "regenerate"
    if failed <= {"G1", "G2", "G5", "G6"}:
        return "repair"
    return "regenerate"


def effective_distractor_score(target_score: int, effective_count: int) -> int:
    if target_score == 1:
        if effective_count >= 3:
            return 4
        if effective_count == 2:
            return 3
        if effective_count == 1:
            return 2
        return 0
    if target_score == 2:
        if effective_count >= 2:
            return 4
        if effective_count == 1:
            return 3
        return 0
    if target_score == 3:
        if effective_count >= 3:
            return 4
        if effective_count == 2:
            return 3
        if effective_count == 1:
            return 2
        return 0
    return 0


def count_effective_distractors(parsed: dict[str, Any], target_score: int | None = None) -> int | None:
    detail = parsed.get("problem_score_detail") or {}
    choice_quality = detail.get("choice_quality")
    if isinstance(choice_quality, dict):
        count = score_or_none(choice_quality.get("effective_count"), 0, 4)
        if count is not None:
            return count

    analysis = parsed.get("effective_distractor_analysis")
    if isinstance(analysis, dict):
        if target_score == 1:
            return sum(
                1
                for item in analysis.values()
                if isinstance(item, dict)
                and boolish(item.get("historically_valid"))
                and boolish(item.get("category_or_period_accessible"))
            )
        return sum(1 for item in analysis.values() if isinstance(item, dict) and boolish(item.get("is_effective_attractive")))
    return None


def target_name_exposure_difficulty_cap(parsed: dict[str, Any]) -> int | None:
    target_score = score_or_none(parsed.get("target_score"), 1, 3)
    if target_score not in {2, 3}:
        return None

    g6_equivalence = parsed.get("g6_claim_equivalence_check")
    relation = normalize_status(g6_equivalence.get("relation")) if isinstance(g6_equivalence, dict) else ""
    g6_overlap = parsed.get("g6_overlap_check")
    overlap = normalize_status(g6_overlap.get("overlap_type")) if isinstance(g6_overlap, dict) else ""
    if "target_name_exposure" not in {relation, overlap}:
        return None
    return 2 if target_score == 2 else 1


def normalize_token(token: str) -> str:
    token = token.strip(".,;:!?()[]{}<>\"'“”‘’·")
    for suffix in PARTICLE_SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            return token[: -len(suffix)]
    return token


def content_tokens(text: str) -> set[str]:
    import re

    tokens: set[str] = set()
    for raw in re.findall(r"[0-9A-Za-z가-힣]+", text or ""):
        token = normalize_token(raw)
        if len(token) < 2 or token in TOKEN_STOPWORDS:
            continue
        tokens.add(token)
        if token.endswith("민") and len(token) >= 3:
            tokens.add(token[:-1])
    return tokens


def strip_document_artifacts(text: str) -> str:
    cleaned = str(text or "")
    for marker in DOCUMENT_ARTIFACT_MARKERS:
        index = cleaned.find(marker)
        if index >= 0:
            cleaned = cleaned[:index]
    return cleaned.strip()


def has_document_artifact(text: str) -> bool:
    return any(marker in str(text or "") for marker in DOCUMENT_ARTIFACT_MARKERS)


def has_relation_claim_marker(text: str) -> bool:
    return any(marker in str(text or "") for marker in RELATION_CLAIM_MARKERS)


def token_is_covered(token: str, candidates: set[str]) -> bool:
    for candidate in candidates:
        if token == candidate:
            return True
        if len(token) >= 3 and len(candidate) >= 3 and (token.startswith(candidate) or candidate.startswith(token)):
            return True
    return False


def missing_choice_core_tokens(choice_text: str, proposition: str) -> list[str]:
    original_tokens = content_tokens(strip_document_artifacts(choice_text))
    proposition_tokens = content_tokens(proposition)
    return sorted(token for token in original_tokens if not token_is_covered(token, proposition_tokens))


def claim_integrity_issue(choice_text: str, item: dict[str, Any]) -> dict[str, Any] | None:
    if not has_relation_claim_marker(choice_text):
        return None
    proposition = str(item.get("historical_proposition") or "")
    if not proposition.strip():
        return None
    original_tokens = content_tokens(strip_document_artifacts(choice_text))
    if len(original_tokens) < 3:
        return None
    missing = missing_choice_core_tokens(choice_text, proposition)
    if len(missing) >= 2 and len(missing) / max(len(original_tokens), 1) >= 0.4:
        return {
            "choice": str(item.get("choice")),
            "missing_tokens": missing,
            "original_choice_text": strip_document_artifacts(choice_text),
            "historical_proposition": proposition,
            "reason": "historical_proposition이 선택지 원문의 핵심 술어/관계를 보존하지 않음",
        }
    return None


def compact_text(text: str) -> str:
    import re

    return "".join(re.findall(r"[0-9A-Za-z가-힣]+", text or ""))


def longest_common_substring_len(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for i, left_char in enumerate(left, start=1):
        current = [0] * (len(right) + 1)
        for j, right_char in enumerate(right, start=1):
            if left_char == right_char:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def correct_choice(record: dict[str, Any]) -> dict[str, Any] | None:
    answer = record.get("answer")
    answer_label = record.get("answer_label")
    for choice in record.get("choices", []):
        if choice.get("number") == answer or choice.get("label") == answer_label:
            return choice
    return None


def choice_shared_token_counts(stem: str, choices: list[dict[str, Any]]) -> dict[str, int]:
    stem_tokens = content_tokens(stem)
    counts: dict[str, int] = {}
    for choice in choices:
        label = str(choice.get("label") or choice.get("number"))
        counts[label] = len(content_tokens(str(choice.get("text") or "")) & stem_tokens)
    return counts


def detect_answer_exposure(record: dict[str, Any]) -> dict[str, Any]:
    stem = str(record.get("stem") or "")
    choices = record.get("choices") or []
    answer_choice = correct_choice(record)
    if not answer_choice:
        return {"should_fail": False, "reason": "정답 선택지를 찾을 수 없음"}

    answer_label = str(answer_choice.get("label") or answer_choice.get("number"))
    answer_text = str(answer_choice.get("text") or "")
    counts = choice_shared_token_counts(stem, choices)
    answer_tokens = content_tokens(answer_text)
    stem_tokens = content_tokens(stem)
    shared_tokens = sorted(answer_tokens & stem_tokens)
    answer_count = counts.get(answer_label, 0)
    other_counts = [count for label, count in counts.items() if label != answer_label]
    max_other = max(other_counts, default=0)

    compact_stem = compact_text(stem)
    answer_lcs = longest_common_substring_len(compact_stem, compact_text(answer_text))
    other_lcs = [
        longest_common_substring_len(compact_stem, compact_text(str(choice.get("text") or "")))
        for choice in choices
        if str(choice.get("label") or choice.get("number")) != answer_label
    ]
    max_other_lcs = max(other_lcs, default=0)

    token_exposure = answer_count >= 2 and answer_count > max_other
    phrase_exposure = answer_lcs >= 5 and answer_lcs > max_other_lcs + 1

    return {
        "should_fail": token_exposure or phrase_exposure,
        "answer_label": answer_label,
        "shared_tokens": shared_tokens,
        "shared_token_count_by_choice": counts,
        "answer_lcs": answer_lcs,
        "max_other_lcs": max_other_lcs,
        "reason": (
            "정답 선지만 발문·자료 표현을 두드러지게 반복함"
            if token_exposure or phrase_exposure
            else "정답 선지만 두드러지는 문자열 반복이 감지되지 않음"
        ),
    }


def status_text(value: Any) -> str:
    return str(value or "").strip().lower()


def list_from_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def g5_classification_requires_fail(classification: Any, choice_text: str | None = None) -> bool:
    if not isinstance(classification, dict):
        return False
    kind = status_text(classification.get("type"))
    should_fail = bool(classification.get("should_fail_g5"))
    actual_subject = str(classification.get("actual_subject_if_valid_other_fact") or "").strip()
    valid_other_status = status_text(classification.get("valid_other_fact_status"))
    is_fake_or_mixed = bool(classification.get("is_fake_or_mixed_fact"))
    has_relation_marker = bool(choice_text and has_relation_claim_marker(choice_text))
    if kind in {"valid_other_fact", "uncertain"}:
        return False
    if actual_subject or valid_other_status in {"yes", "plausible", "uncertain"}:
        return False
    if kind in {"false_actor_action", "false_time"} and not has_relation_marker and not is_fake_or_mixed:
        return False
    hard_fail_kinds = {
        "nonexistent_term_or_fact",
        "mixed_fact_hybrid",
        "fabricated_relation",
        "false_causality",
        "false_sequence",
        "false_result",
    }
    if kind in hard_fail_kinds:
        return should_fail or is_fake_or_mixed
    if kind in {"false_actor_action", "false_time"}:
        return should_fail and (is_fake_or_mixed or has_relation_marker or valid_other_status == "no")
    return should_fail and is_fake_or_mixed


def relation_claim_has_unsupported_parts(item: dict[str, Any], choice_text: str | None = None) -> bool:
    if not choice_text or not has_relation_claim_marker(choice_text):
        return False
    classification = item.get("g5_error_classification")
    if isinstance(classification, dict):
        kind = status_text(classification.get("type"))
        actual_subject = str(classification.get("actual_subject_if_valid_other_fact") or "").strip()
        valid_other_status = status_text(classification.get("valid_other_fact_status"))
        is_fake_or_mixed = bool(classification.get("is_fake_or_mixed_fact"))
        if kind in {"valid_other_fact", "uncertain"}:
            return False
        if actual_subject or valid_other_status in {"yes", "plausible", "uncertain"}:
            return False
        if not is_fake_or_mixed and kind not in {
            "nonexistent_term_or_fact",
            "mixed_fact_hybrid",
            "fabricated_relation",
            "false_causality",
            "false_sequence",
            "false_result",
        }:
            return False
    validity = item.get("whole_claim_validity")
    if not isinstance(validity, dict):
        return False
    return bool(list_from_value(validity.get("false_or_unsupported_parts")))


def meaningful_partial_g6_shared_units(g6_equivalence: dict[str, Any]) -> list[str]:
    meaningful: list[str] = []
    for raw_unit in list_from_value(g6_equivalence.get("shared_meaning_units")):
        unit = str(raw_unit or "").strip()
        if not unit:
            continue
        compact = unit.replace(" ", "")
        if compact in GENERIC_G6_SHARED_UNITS:
            continue
        if "관련" in compact:
            continue
        if any(compact.endswith(suffix) for suffix in GENERIC_G6_SHARED_SUFFIXES):
            continue
        meaningful.append(unit)
    return meaningful


def has_partial_g6_text_anchor(parsed: dict[str, Any]) -> bool:
    exposure = parsed.get("_client_answer_exposure_check")
    if not isinstance(exposure, dict):
        return False
    shared_tokens = list_from_value(exposure.get("shared_tokens"))
    answer_lcs = score_or_none(exposure.get("answer_lcs"), 0, 999)
    max_other_lcs = score_or_none(exposure.get("max_other_lcs"), 0, 999)
    return bool(shared_tokens) and answer_lcs is not None and max_other_lcs is not None and answer_lcs > max_other_lcs


def recompute_gate_result(parsed: dict[str, Any]) -> None:
    gate = parsed.get("gate")
    if not isinstance(gate, dict):
        return
    statuses = [
        normalize_status(item.get("status"))
        for item in gate.values()
        if isinstance(item, dict)
    ]
    if "fail" in statuses:
        parsed["gate_result"] = "FAIL"
    elif "uncertain" in statuses:
        parsed["gate_result"] = "uncertain"
    elif statuses:
        parsed["gate_result"] = "PASS"


def apply_client_gate_checks(parsed: dict[str, Any], record: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    gate = parsed.setdefault("gate", {})

    if record is not None:
        choices = record.get("choices") or []
        answer_choice = correct_choice(record)
        format_errors = []
        text_artifact_errors = []
        if not str(record.get("stem") or "").strip():
            format_errors.append("발문이 비어 있음")
        if len(choices) != 5:
            format_errors.append(f"선택지 개수가 5개가 아님: {len(choices)}")
        if answer_choice is None:
            format_errors.append("표시 정답이 선택지 5개 중 하나와 대응하지 않음")
        if any(not str(choice.get("text") or "").strip() for choice in choices):
            format_errors.append("빈 선택지가 존재함")
        if has_document_artifact(record.get("stem") or ""):
            text_artifact_errors.append("발문에 문서 페이지/머리말/꼬리말 추출 흔적이 섞임")
        for choice in choices:
            if has_document_artifact(choice.get("text") or ""):
                label = str(choice.get("label") or choice.get("number"))
                text_artifact_errors.append(f"{label} 선택지에 문서 페이지/머리말/꼬리말 추출 흔적이 섞임")
        if format_errors:
            gate["G1"] = {"status": "FAIL", "reason": "client format check: " + " / ".join(format_errors)}
            parsed["gate_result"] = "FAIL"
            issues.append("G1 set by client format check")
        if text_artifact_errors:
            gate["G2"] = {"status": "FAIL", "reason": "client text artifact check: " + " / ".join(text_artifact_errors)}
            parsed["gate_result"] = "FAIL"
            issues.append("G2 set by client text artifact check")

        exposure = detect_answer_exposure(record)
        parsed["_client_answer_exposure_check"] = exposure
        if exposure["should_fail"]:
            gate["G6"] = {
                "status": "FAIL",
                "reason": "client answer exposure check: "
                + exposure["reason"]
                + f"; shared_tokens={exposure['shared_tokens']}; "
                + f"shared_token_count_by_choice={exposure['shared_token_count_by_choice']}; "
                + f"answer_lcs={exposure['answer_lcs']}, max_other_lcs={exposure['max_other_lcs']}",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G6 set by client answer exposure check")

    choice_text_by_label: dict[str, str] = {}
    if record is not None:
        for choice in record.get("choices", []):
            label = str(choice.get("label") or choice.get("number"))
            choice_text_by_label[label] = str(choice.get("text") or "")

    has_choice_verification = any(isinstance(item, dict) for item in list_from_value(parsed.get("choice_verification")))
    verified_g5_failure_detected = False
    g5_fail_choices = []
    claim_integrity_issues = []
    for item in list_from_value(parsed.get("choice_verification")):
        if not isinstance(item, dict):
            continue
        label = str(item.get("choice"))
        choice_text_for_label = choice_text_by_label.get(label) or str(item.get("original_choice_text") or "")
        classification = item.get("g5_error_classification")
        if g5_classification_requires_fail(classification, choice_text_for_label) or relation_claim_has_unsupported_parts(
            item, choice_text_for_label
        ):
            g5_fail_choices.append(label)
        if label in choice_text_by_label:
            issue = claim_integrity_issue(choice_text_by_label[label], item)
            if issue:
                claim_integrity_issues.append(issue)
    if g5_fail_choices:
        gate["G5"] = {
            "status": "FAIL",
            "reason": "client consistency check: choice_verification에서 G5 FAIL 오답이 보고됨. "
            f"g5_fail_choices={g5_fail_choices}",
        }
        parsed["gate_result"] = "FAIL"
        issues.append("G5 set by client consistency check")
        verified_g5_failure_detected = True
    if claim_integrity_issues:
        parsed["_client_choice_claim_integrity_check"] = claim_integrity_issues
        gate["G5"] = {
            "status": "FAIL",
            "reason": "client claim integrity check: 선택지 원문 핵심 술어/관계가 historical_proposition에서 누락됨. "
            f"choices={[item['choice'] for item in claim_integrity_issues]}",
        }
        parsed["gate_result"] = "FAIL"
        issues.append("G5 set by client claim integrity check")
        verified_g5_failure_detected = True

    condition_check = parsed.get("stem_condition_check")
    if isinstance(condition_check, dict):
        count = score_or_none(condition_check.get("satisfying_choice_count"), 0, 5)
        if count is not None and count != 1:
            gate["G3"] = {
                "status": "FAIL",
                "reason": "client consistency check: 발문 조건 충족 선택지 수가 1개가 아님. "
                f"satisfying_choice_count={count}, choices={condition_check.get('satisfying_choices')}",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G3 set by client consistency check")

    g6_equivalence = parsed.get("g6_claim_equivalence_check")
    if isinstance(g6_equivalence, dict):
        relation = status_text(g6_equivalence.get("relation"))
        should_fail = bool(g6_equivalence.get("g6_should_fail"))
        text_match = bool(g6_equivalence.get("can_answer_by_text_matching_without_history"))
        meaningful_shared = meaningful_partial_g6_shared_units(g6_equivalence)
        partial_text_anchor = has_partial_g6_text_anchor(parsed)
        if should_fail or relation in {"same_core_claim", "direct_copy", "external_bias"} or (
            relation == "partial_same_claim" and (text_match or (bool(meaningful_shared) and partial_text_anchor))
        ):
            gate["G6"] = {
                "status": "FAIL",
                "reason": "client consistency check: g6_claim_equivalence_check가 G6 FAIL 조건을 보고함. "
                f"relation={g6_equivalence.get('relation')}, "
                f"g6_should_fail={g6_equivalence.get('g6_should_fail')}, "
                f"can_answer_by_text_matching_without_history={g6_equivalence.get('can_answer_by_text_matching_without_history')}, "
                f"meaningful_shared_units={meaningful_shared}, "
                f"partial_text_anchor={partial_text_anchor}",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G6 set by client consistency check")

    gate_consistency = parsed.get("gate_consistency_check")
    if isinstance(gate_consistency, dict):
        reported_g5_fail = [str(item) for item in list_from_value(gate_consistency.get("g5_fail_choices"))]
        verified_reported_g5_fail = []
        if reported_g5_fail:
            by_label = {
                str(item.get("choice")): item
                for item in list_from_value(parsed.get("choice_verification"))
                if isinstance(item, dict)
            }
            for label in reported_g5_fail:
                item = by_label.get(label)
                if not item:
                    continue
                choice_text_for_label = choice_text_by_label.get(label) or str(item.get("original_choice_text") or "")
                if g5_classification_requires_fail(
                    item.get("g5_error_classification"), choice_text_for_label
                ) or relation_claim_has_unsupported_parts(item, choice_text_for_label):
                    verified_reported_g5_fail.append(label)
        reported_count = score_or_none(gate_consistency.get("satisfying_choice_count_from_choice_verification"), 0, 5)
        reported_g6 = bool(gate_consistency.get("g6_equivalence_requires_fail"))
        if verified_reported_g5_fail:
            gate["G5"] = {
                "status": "FAIL",
                "reason": "client consistency check: gate_consistency_check가 G5 FAIL 오답을 보고함. "
                f"g5_fail_choices={verified_reported_g5_fail}",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G5 set by gate consistency check")
            verified_g5_failure_detected = True
        elif reported_g5_fail:
            issues.append(f"ignored unverified G5 choices from gate consistency check: {reported_g5_fail}")
        if reported_count is not None and reported_count != 1:
            gate["G3"] = {
                "status": "FAIL",
                "reason": "client consistency check: gate_consistency_check의 정답 후보 수가 1개가 아님. "
                f"count={reported_count}",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G3 set by gate consistency check")
        if reported_g6:
            gate["G6"] = {
                "status": "FAIL",
                "reason": "client consistency check: gate_consistency_check가 G6 FAIL 필요를 보고함.",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G6 set by gate consistency check")

    g5_status = gate.get("G5")
    if (
        has_choice_verification
        and not verified_g5_failure_detected
        and isinstance(g5_status, dict)
        and normalize_status(g5_status.get("status")) == "fail"
    ):
        gate["G5"] = {
            "status": "PASS",
            "reason": "client consistency check: 선택지 구조화 검증에서 확인된 G5 FAIL 오답이 없어 모델의 G5 FAIL을 해제함.",
        }
        issues.append("G5 model fail cleared by client consistency check")

    recompute_gate_result(parsed)
    return parsed, issues


def normalize_parsed_result(parsed: dict[str, Any], record: dict[str, Any] | None = None) -> dict[str, Any]:
    issues: list[str] = []
    parsed, gate_check_issues = apply_client_gate_checks(parsed, record)
    issues.extend(gate_check_issues)
    gate_result = normalize_status(parsed.get("gate_result"))

    if gate_result == "fail":
        parsed["problem_score"] = None
        parsed["explanation_score"] = None
        parsed["total_score"] = None
        parsed["failed_gates"] = failed_gate_ids(parsed)
        parsed["repair_targets"] = repair_targets_from_gates(parsed)
        parsed["final_decision"] = decision_from_gate_failure(parsed)
    elif gate_result == "uncertain":
        parsed["problem_score"] = None
        parsed["explanation_score"] = None
        parsed["total_score"] = None
        parsed["final_decision"] = "needs_verification"
    elif gate_result == "pass":
        problem_detail = parsed.get("problem_score_detail") or {}
        difficulty = problem_detail.get("target_difficulty_fit")
        if isinstance(difficulty, dict):
            cap = target_name_exposure_difficulty_cap(parsed)
            raw_difficulty = score_or_none(difficulty.get("score"), 0, 4)
            if cap is not None and raw_difficulty is not None and raw_difficulty > cap:
                issues.append(f"target_difficulty_fit capped by target_name_exposure: {raw_difficulty} -> {cap}")
                difficulty["score"] = cap
                reason = str(difficulty.get("reason") or "").strip()
                suffix = f"client normalized: 대상명 직접 노출로 목표 난이도 최대 {cap}점 적용."
                difficulty["reason"] = f"{reason} {suffix}".strip()

        choice_quality = problem_detail.get("choice_quality")
        if isinstance(choice_quality, dict):
            response_score = score_or_none(choice_quality.get("response_category_fit_score"), 0, 1)
            duplicate_score = score_or_none(choice_quality.get("no_duplicate_or_inclusion_score"), 0, 1)
            target_score = score_or_none(parsed.get("target_score"), 1, 3)
            effective_count = count_effective_distractors(parsed, target_score)
            effective_score = None
            if effective_count is not None and target_score is not None:
                effective_score = effective_distractor_score(target_score, effective_count)
                if choice_quality.get("effective_attractive_distractor_score") != effective_score:
                    issues.append(
                        "effective_attractive_distractor_score recalculated: "
                        f"{choice_quality.get('effective_attractive_distractor_score')!r} -> {effective_score}"
                    )
                choice_quality["effective_attractive_distractor_score"] = effective_score
            if response_score is not None and duplicate_score is not None and effective_score is not None:
                recalculated_choice_quality = response_score + duplicate_score + effective_score
                if choice_quality.get("score") != recalculated_choice_quality:
                    issues.append(
                        "choice_quality recalculated: "
                        f"{choice_quality.get('score')!r} -> {recalculated_choice_quality}"
                    )
                choice_quality["score"] = recalculated_choice_quality
                choice_quality["reason"] = (
                    "client normalized: "
                    f"응답 범주 {response_score}/1, 중복·포함 관계 {duplicate_score}/1, "
                    f"오답 품질 기준 충족 {effective_count}개 -> {effective_score}/4"
                )

        problem_scores: list[int] = []
        for key, max_score in PROBLEM_SCORE_MAX.items():
            raw_score = (problem_detail.get(key) or {}).get("score")
            score = score_or_none(raw_score, 0, max_score)
            if score is None:
                issues.append(f"invalid problem score for {key}: {raw_score!r}")
            else:
                problem_scores.append(score)

        explanation_detail = parsed.get("explanation_score_detail") or {}
        explanation_scores: list[int] = []
        for key in EXPLANATION_SCORE_KEYS:
            raw_score = (explanation_detail.get(key) or {}).get("score")
            score = score_or_none(raw_score, 0, 1)
            if score is None:
                issues.append(f"invalid explanation score for {key}: {raw_score!r}")
            else:
                explanation_scores.append(score)

        if len(problem_scores) == len(PROBLEM_SCORE_MAX):
            recalculated_problem = sum(problem_scores)
            if parsed.get("problem_score") != recalculated_problem:
                issues.append(
                    f"problem_score recalculated: {parsed.get('problem_score')!r} -> {recalculated_problem}"
                )
            parsed["problem_score"] = recalculated_problem

        if len(explanation_scores) == len(EXPLANATION_SCORE_KEYS):
            recalculated_explanation = sum(explanation_scores)
            if parsed.get("explanation_score") != recalculated_explanation:
                issues.append(
                    "explanation_score recalculated: "
                    f"{parsed.get('explanation_score')!r} -> {recalculated_explanation}"
                )
            parsed["explanation_score"] = recalculated_explanation

        if isinstance(parsed.get("problem_score"), int) and isinstance(parsed.get("explanation_score"), int):
            recalculated_total = parsed["problem_score"] + parsed["explanation_score"]
            if parsed.get("total_score") != recalculated_total:
                issues.append(f"total_score recalculated: {parsed.get('total_score')!r} -> {recalculated_total}")
            parsed["total_score"] = recalculated_total
            expected_decision = decision_from_total(recalculated_total)
            if parsed.get("final_decision") != expected_decision:
                issues.append(
                    f"final_decision recalculated: {parsed.get('final_decision')!r} -> {expected_decision}"
                )
            parsed["final_decision"] = expected_decision
    else:
        issues.append(f"unknown gate_result: {parsed.get('gate_result')!r}")

    parsed["_client_validation"] = {
        "normalized": bool(issues),
        "issues": issues,
    }
    return parsed


def select_records(
    records: list[dict[str, Any]],
    question_id: int | None,
    question_start: int | None,
    question_end: int | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    if question_id is not None:
        if question_start is not None or question_end is not None:
            raise ValueError("--question-id cannot be used with --question-start/--question-end")
        records = [record for record in records if int(record["question_id"]) == question_id]
        if not records:
            raise ValueError(f"Question id not found: {question_id}")
    if question_start is not None:
        records = [record for record in records if int(record["question_id"]) >= question_start]
    if question_end is not None:
        records = [record for record in records if int(record["question_id"]) <= question_end]
    if (question_start is not None or question_end is not None) and not records:
        raise ValueError(f"No questions found in range: {question_start or '-inf'}..{question_end or 'inf'}")
    if limit is not None:
        records = records[:limit]
    return records


def main() -> int:
    repo_root = find_repo_root()
    load_env_file(repo_root / ".env")
    load_env_file(script_dir() / ".env")

    parser = argparse.ArgumentParser(description="Evaluate extracted questions with the OpenAI API.")
    parser.add_argument("--input", type=Path, default=script_dir() / "data" / "processed" / "questions.jsonl")
    parser.add_argument("--rubric", type=Path, default=repo_root / "docs" / "hanneung_sllm_eval_rubric_v1_8.md")
    parser.add_argument("--out-dir", type=Path, default=script_dir() / "results")
    parser.add_argument("--model", default=os.getenv("OPENAI_EVAL_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--question-id", type=int, default=None)
    parser.add_argument("--question-start", type=int, default=None)
    parser.add_argument("--question-end", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-json-mode", action="store_true")
    args = parser.parse_args()

    records = select_records(
        read_jsonl(args.input),
        args.question_id,
        args.question_start,
        args.question_end,
        args.limit,
    )
    rubric = args.rubric.read_text(encoding="utf-8")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.out_dir / f"eval_run_{timestamp}.jsonl"

    if args.dry_run:
        for record in records:
            messages = build_messages(rubric, record)
            dry_path = args.out_dir / f"dry_run_q{record['question_id']}_{timestamp}.json"
            dry_path.write_text(
                json.dumps(
                    {
                        "model": args.model,
                        "messages": messages,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"dry-run prompt saved: {dry_path}")
        return 0

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment or .env")

    with output_path.open("w", encoding="utf-8") as out:
        for index, record in enumerate(records, start=1):
            messages = build_messages(rubric, record)
            api_response = post_chat_completion(
                api_key=api_key,
                model=args.model,
                messages=messages,
                base_url=args.base_url,
                json_mode=not args.no_json_mode,
                timeout=args.timeout,
                max_retries=args.max_retries,
            )
            parsed, raw_content = parse_model_content(api_response)
            if isinstance(parsed, dict):
                parsed = normalize_parsed_result(parsed, record)
            result = {
                "question_id": record["question_id"],
                "target_score": record["target_score"],
                "model": args.model,
                "parsed": parsed,
                "raw_content": raw_content if parsed is None else None,
                "usage": api_response.get("usage"),
            }
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()

            final_decision = None
            if isinstance(parsed, dict):
                final_decision = parsed.get("final_decision")
            print(f"[{index}/{len(records)}] Q{record['question_id']} -> {final_decision or 'raw_saved'}")
            if args.sleep and index < len(records):
                time.sleep(args.sleep)

    print(f"saved: {output_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

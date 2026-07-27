"""generation item을 V41 학습 스키마의 SLLM 요청 5개로 변환한다.

이 모듈은 API를 호출하지 않는다. 정답용 record 1개와 오답용 record 4개를 만들며,
실제 RunPod 호출은 ``workflows.question_pipeline.generate_v41_question_stage``가 한다.
각 record는 V41 학습 데이터와 동일한 ``system``, ``instruction``, ``input``으로 구성된다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai.question_generation.core.text import normalize_era_markers

# 모델에 임의의 파이프라인 메타데이터가 들어가지 않도록 허용 필드만 복사한다.
CORRECT_INPUT_FIELDS = (
    "task_type",
    "material",
    "answer_fact_basis",
    "topic_type",
    "topic",
    "material_type",
    "major_type",
    "minor_type",
    "question_task",
    "question_task_instruction",
    "difficulty_label",
)

DISTRACTOR_INPUT_FIELDS = (
    "task_type",
    "material",
    "question",
    "answer_choice",
    "distractor_fact_basis",
    "distractor_type",
    "topic_type",
    "topic",
    "material_type",
    "major_type",
    "minor_type",
    "question_task",
    "difficulty_label",
)


V41_SYSTEM = "당신은 한국사능력검정시험 심화 문항을 만드는 출제자입니다."

CORRECT_INSTRUCTION = (
    "주어진 자료 지문, 정답 근거, 출제 조건만 사용해 한국사능력검정시험 심화 문항의 발문과 정답 선지 1개를 생성하세요. "
    "출력은 question, answer_choice 키를 가진 JSON 객체만 작성하세요. material은 이미 준비된 자료 지문이므로 새로 만들거나 요약하지 마세요. "
    "answer_fact_basis는 정답 선지를 만들 검증 근거입니다. answer_choice는 반드시 answer_fact_basis의 사실에 근거해야 합니다. "
    "오답 선지, 정답 번호, 해설은 작성하지 마세요."
)

DISTRACTOR_INSTRUCTION = (
    "주어진 자료 지문, 발문, 정답 선지, 오답 근거, 출제 조건을 사용해 한국사능력검정시험 심화 문항의 오답 선지 1개를 생성하세요. "
    "출력은 distractor_choice 키를 가진 JSON 객체만 작성하세요. 발문은 생성하지 마세요. "
    "material과 question은 문항 맥락과 선지 표현 방식을 맞추기 위한 참고 정보입니다. "
    "distractor_fact_basis는 오답 선지를 만들 검증 근거입니다. "
    "오답 선지의 역사 사실은 반드시 distractor_fact_basis에 근거해야 하며, answer_choice와 의미가 같은 선지를 만들지 마세요. "
    "정답 번호, 선택지 번호, 해설은 작성하지 마세요."
)

def parse_args() -> argparse.Namespace:
    """V41 record를 파일로 덤프할 입력·출력 경로를 읽는다."""
    parser = argparse.ArgumentParser(description="Build one correct and four distractor SLLM inputs from generation packs.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def clean_basis_text(text: str) -> str:
    """검수된 근거 내용은 바꾸지 않고 공백만 정규화한다."""
    return " ".join(str(text).split())


def normalize_payload(value: Any) -> Any:
    """dict/list/string을 재귀적으로 정리해 JSON 직렬화 가능한 payload로 만든다."""
    if isinstance(value, str):
        return normalize_era_markers(value)
    if isinstance(value, list):
        return [normalize_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_payload(item) for key, item in value.items()}
    return value


def pick_fields(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """V41 허용 목록에 있는 필드만 골라 정규화한다."""
    return {field: normalize_payload(source[field]) for field in fields if field in source}


def correct_input(item: dict[str, Any], material: str) -> dict[str, Any]:
    """불변 input과 생성된 material에서 V41 정답 입력을 만든다."""
    data = pick_fields(
        {
            **item,
            "task_type": "correct_choice_generation",
            "material": material,
            "answer_fact_basis": [item["answer_basis"]["fact_basis"]],
        },
        CORRECT_INPUT_FIELDS,
    )
    data["answer_fact_basis"] = [
        clean_basis_text(str(value))
        for value in data.get("answer_fact_basis", [])
        if str(value).strip()
    ]
    return data


def distractor_input(
    item: dict[str, Any], material: str, basis: dict[str, Any], question: str, answer_choice: str
) -> dict[str, Any]:
    """오답 후보 하나의 근거만 포함한 V41 오답 입력을 만든다."""
    data = {
        **item,
        "task_type": "distractor_choice_generation",
        "material": material,
        "question": question,
        "answer_choice": answer_choice,
        "distractor_fact_basis": [clean_basis_text(str(basis["fact_basis"]))],
        "distractor_type": item["distractor_type"],
    }
    return pick_fields(data, DISTRACTOR_INPUT_FIELDS)


def retry_instruction(instruction: str, feedback: str) -> str:
    """최초 V41 instruction은 보존하고 재시도 때만 평가 피드백을 덧붙인다."""
    feedback = str(feedback or "").strip()
    return instruction if not feedback else f"{instruction}\n\n이전 평가에서 지적된 오류만 수정하세요:\n{feedback}"


def correct_record(item: dict[str, Any], material: str, feedback: str = "") -> dict[str, Any]:
    """정답 SLLM 호출 한 건을 만든다."""
    data = correct_input(item, material)
    return {
        "seed_id": item.get("seed_id", ""),
        "choice_role": "correct",
        "system": V41_SYSTEM,
        "instruction": retry_instruction(CORRECT_INSTRUCTION, feedback),
        "input": data,
    }


def distractor_record(
    item: dict[str, Any], material: str, basis: dict[str, Any], correct_output: dict[str, Any], feedback: str = ""
) -> dict[str, Any]:
    """오답 SLLM 호출 한 건을 만든다."""
    slot = int(basis["slot"])
    return {
        "seed_id": item.get("seed_id", ""),
        "choice_role": "distractor",
        "distractor_index": slot,
        "system": V41_SYSTEM,
        "instruction": retry_instruction(DISTRACTOR_INSTRUCTION, feedback),
        "input": distractor_input(
            item,
            material,
            basis,
            str(correct_output.get("question") or ""),
            str(correct_output.get("answer_choice") or ""),
        ),
    }


def build_records(item: dict[str, Any], material: str) -> list[dict[str, Any]]:
    """API 호출 없이 확인할 정답 1개와 오답 4개 record를 만든다."""
    placeholder = {"question": "<from_correct_output.question>", "answer_choice": "<from_correct_output.answer_choice>"}
    return [
        correct_record(item, material),
        *(distractor_record(item, material, basis, placeholder) for basis in item["distractors"]),
    ]


def main() -> None:
    """API 호출 없이 V41 record JSON을 파일로 확인하는 디버그 CLI."""
    args = parse_args()
    item = json.loads(args.input.read_text(encoding="utf-8-sig"))
    output = {
        "schema_version": "sllm_choice_inputs_v1",
        "source": str(args.input),
        "items": build_records(item, str(item.get("material") or "")),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "count": len(output["items"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()

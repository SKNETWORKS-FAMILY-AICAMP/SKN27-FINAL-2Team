from __future__ import annotations

import json
from pathlib import Path


BASE = Path(__file__).resolve().parent / "archive" / "choice_quality_v1" / "train_choice_quality_runpod.ipynb"
OUT = Path(__file__).resolve().parent / "train_choice_quality_runpod_v2.ipynb"


def replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise ValueError(f"replace target not found: {old[:80]}")
    return text.replace(old, new, 1)


DATA_SPLIT_OLD = """# 실제 train 파일 안에서 validation을 나눈다.
groups = [row["question_id"] for row in all_train_rows]
splitter = GroupShuffleSplit(n_splits=1, test_size=VALID_SIZE, random_state=SEED)
train_idx, valid_idx = next(splitter.split(all_train_rows, groups=groups))
train_rows = [all_train_rows[idx] for idx in train_idx]
valid_rows = [all_train_rows[idx] for idx in valid_idx]
"""

DATA_SPLIT_NEW = """# 실제 팀원 생성 오류는 수가 적어서 validation으로 빠지지 않게 train에 고정한다.
# 진짜 일반화 성능은 다음 팀원 생성 파일을 별도 test로 받아 확인해야 한다.
force_train_rows = [
    row
    for row in all_train_rows
    if row.get("source_type") == "generated" and row.get("label") == 0
]
split_candidate_rows = [
    row
    for row in all_train_rows
    if not (row.get("source_type") == "generated" and row.get("label") == 0)
]

groups = [row["question_id"] for row in split_candidate_rows]
splitter = GroupShuffleSplit(n_splits=1, test_size=VALID_SIZE, random_state=SEED)
train_idx, valid_idx = next(splitter.split(split_candidate_rows, groups=groups))
train_rows = force_train_rows + [split_candidate_rows[idx] for idx in train_idx]
valid_rows = [split_candidate_rows[idx] for idx in valid_idx]
"""


REVIEW_CELL = r'''
def get_choice_text(choice: Any) -> str:
    if isinstance(choice, dict):
        return str(choice.get("text") or choice.get("content") or "").strip()
    return str(choice or "").strip()


def normalize_for_rule(text: Any) -> str:
    import re

    return re.sub(r"\s+", "", str(text or "")).lower()


def parse_answer(row: dict[str, Any]) -> int | None:
    value = row.get("answer")
    if value is None:
        value = row.get("answer_number")
    try:
        answer = int(value)
    except Exception:
        return None
    if 1 <= answer <= 5:
        return answer
    return None


def rule_check_question(row: dict[str, Any]) -> list[dict[str, Any]]:
    # BERT가 배우기 어려운 문제 단위/형식 오류를 먼저 잡는다.
    issues: list[dict[str, Any]] = []
    choices = row.get("choices") or []
    passage = row.get("passage") or row.get("material") or ""
    question = row.get("question") or ""

    if len(choices) != 5:
        issues.append(
            {
                "type": "CHOICE_COUNT_ERROR",
                "type_ko": ERROR_TYPE_KO.get("CHOICE_COUNT_ERROR", "선지 개수 오류"),
                "message": "선지가 정확히 5개가 아닙니다.",
            }
        )

    answer = parse_answer(row)
    if answer is None:
        issues.append(
            {
                "type": "ANSWER_FORMAT_ERROR",
                "type_ko": ERROR_TYPE_KO.get("ANSWER_FORMAT_ERROR", "정답 형식 오류"),
                "message": "정답 번호가 없거나 1~5 범위를 벗어났습니다.",
            }
        )

    if choices and all(isinstance(choice, dict) for choice in choices):
        marked_answer_count = sum(1 for choice in choices if bool(choice.get("is_answer")))
        if marked_answer_count != 1:
            issues.append(
                {
                    "type": "ANSWER_FORMAT_ERROR",
                    "type_ko": ERROR_TYPE_KO.get("ANSWER_FORMAT_ERROR", "정답 형식 오류"),
                    "message": f"is_answer=True인 선지가 {marked_answer_count}개입니다.",
                }
            )

    normalized_choices: dict[str, int] = {}
    duplicated: list[int] = []
    for idx, choice in enumerate(choices, start=1):
        norm = normalize_for_rule(get_choice_text(choice))
        if not norm:
            continue
        if norm in normalized_choices:
            duplicated.extend([normalized_choices[norm], idx])
        else:
            normalized_choices[norm] = idx
    if duplicated:
        issues.append(
            {
                "type": "DUPLICATE_OR_SIMILAR_CHOICE",
                "type_ko": ERROR_TYPE_KO.get("DUPLICATE_OR_SIMILAR_CHOICE", "선지 중복/유사"),
                "message": "동일한 선지가 2개 이상 있습니다.",
                "choice_numbers": sorted(set(duplicated)),
            }
        )

    if answer is not None and 1 <= answer <= len(choices):
        answer_text = get_choice_text(choices[answer - 1])
        answer_norm = normalize_for_rule(answer_text)
        body_norm = normalize_for_rule(str(passage) + " " + str(question))
        if answer_norm and len(answer_norm) >= 8 and answer_norm in body_norm:
            issues.append(
                {
                    "type": "ANSWER_IN_PASSAGE",
                    "type_ko": ERROR_TYPE_KO.get("ANSWER_IN_PASSAGE", "정답 노출"),
                    "message": "정답 선지가 지문 또는 질문에 그대로 포함되어 있습니다.",
                    "choice_no": answer,
                }
            )

        lengths = [len(get_choice_text(choice)) for choice in choices]
        if len(lengths) == 5:
            answer_len = lengths[answer - 1]
            other_lengths = [length for idx, length in enumerate(lengths, start=1) if idx != answer]
            avg_other = mean(other_lengths)
            too_long = answer_len >= avg_other * 1.5 and answer_len - avg_other >= 12
            too_short = answer_len * 1.5 <= avg_other and avg_other - answer_len >= 12
            if too_long or too_short:
                issues.append(
                    {
                        "type": "ANSWER_LENGTH_BIAS",
                        "type_ko": ERROR_TYPE_KO.get("ANSWER_LENGTH_BIAS", "정답 선지 길이 편향"),
                        "message": "정답 선지가 다른 선지보다 유독 길거나 짧습니다.",
                        "choice_no": answer,
                        "answer_length": answer_len,
                        "other_avg_length": round(float(avg_other), 2),
                    }
                )

    return issues


def review_choice(passage: str, question: str, choice: str, is_answer: int) -> dict[str, Any]:
    row = {
        "passage": passage,
        "question": question,
        "choice": choice,
        "is_answer": int(is_answer),
        "error_codes": [],
    }
    dataset = ChoiceQualityDataset([row], tokenizer, MAX_LENGTH)
    batch = next(iter(DataLoader(dataset, batch_size=1, shuffle=False)))
    batch.pop("labels", None)
    inputs = {key: value.to(device) for key, value in batch.items()}

    model.eval()
    with torch.no_grad():
        outputs = model(**inputs)
        probs = torch.sigmoid(outputs.logits)[0].detach().cpu().numpy()

    codes = predicted_codes(probs, best_threshold)
    return {
        "label": 0 if codes else 1,
        "error_codes": codes,
        "error_names_ko": [ERROR_TYPE_KO.get(code, code) for code in codes],
        "max_error_prob": round(float(probs.max()), 6),
        "error_probs": {code: round(float(probs[idx]), 6) for code, idx in ERROR_TO_ID.items()},
    }


def review_question(row: dict[str, Any]) -> dict[str, Any]:
    choices = row.get("choices") or []
    answer = parse_answer(row) or 0
    passage = row.get("passage") or row.get("material") or ""
    question = row.get("question") or ""

    rule_issues = rule_check_question(row)
    choice_results = []
    for idx, choice in enumerate(choices, start=1):
        text = get_choice_text(choice)
        result = review_choice(
            passage=passage,
            question=question,
            choice=text,
            is_answer=1 if idx == answer else 0,
        )
        result["choice_no"] = idx
        result["choice"] = text
        choice_results.append(result)

    bert_codes = sorted({code for item in choice_results for code in item["error_codes"]})
    rule_codes = sorted({issue["type"] for issue in rule_issues})
    all_codes = sorted(set(bert_codes + rule_codes))

    return {
        "id": row.get("id") or row.get("question_id") or row.get("seed_id"),
        "label": 0 if all_codes else 1,
        "error_codes": all_codes,
        "error_names_ko": [ERROR_TYPE_KO.get(code, code) for code in all_codes],
        "rule_issues": rule_issues,
        "choice_results": choice_results,
    }


print("하이브리드 검수 함수 준비 완료: 규칙 검사 + BERT 선지 분류")
'''


def main() -> None:
    nb = json.loads(BASE.read_text(encoding="utf-8"))

    for cell in nb["cells"]:
        src = "".join(cell.get("source", []))

        if cell.get("cell_type") == "markdown" and "정답 번호를 맞히는 모델이 아니다" in src:
            src = src.replace("# 선지 이상 여부 BERT 학습", "# 선지 이상 여부 BERT 학습 v2")
            src = src.replace(
                "문제 1개를 검수할 때는 선지 5개를 각각 이 모델에 넣고, 선지별 이상 여부를 확인한다.",
                "문제 1개를 검수할 때는 규칙 검사로 형식/중복 오류를 먼저 잡고, 선지 5개를 각각 BERT에 넣어 선지별 이상 여부를 확인한다.",
            )
            cell["source"] = src.splitlines(keepends=True)

        if "TRAIN_JSON = DATA_DIR" in src:
            src = src.replace('TRAIN_JSON = DATA_DIR / "choice_quality_train.json"', 'TRAIN_JSON = DATA_DIR / "choice_quality_train_v2.json"')
            src = src.replace('TEST_JSON = DATA_DIR / "choice_quality_test.json"', 'TEST_JSON = DATA_DIR / "choice_quality_test_v2.json"')
            src = src.replace('OUTPUT_DIR = WORKSPACE_DIR / "choice_quality_output"', 'OUTPUT_DIR = WORKSPACE_DIR / "choice_quality_output_v2"')
            src = src.replace(
                '    "ANSWER_FORMAT_ERROR": "정답 형식 오류",\n}',
                '    "ANSWER_FORMAT_ERROR": "정답 형식 오류",\n    "CHOICE_COUNT_ERROR": "선지 개수 오류",\n}',
            )
            cell["source"] = src.splitlines(keepends=True)

        if DATA_SPLIT_OLD in src:
            src = replace_once(src, DATA_SPLIT_OLD, DATA_SPLIT_NEW)
            src = src.replace(
                'print("train:", len(train_rows))\n',
                'print("forced generated error train:", len(force_train_rows))\nprint("train:", len(train_rows))\n',
            )
            cell["source"] = src.splitlines(keepends=True)

        if "def review_choice(passage" in src and "def review_question(row" in src:
            cell["source"] = REVIEW_CELL.strip("\n").splitlines(keepends=True)

    OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created: {OUT}")


if __name__ == "__main__":
    main()

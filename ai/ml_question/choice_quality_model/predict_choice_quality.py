from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer


THRESHOLD = 0.10
MAX_LENGTH = 512
HIGH_CONFIDENCE_ERROR_PROB = 0.80
ADVISORY_ONLY_CODES = {"QUESTION_CHOICE_MISMATCH"}
APPROVED_ACRONYMS = {"FTA", "IMF", "APEC", "UN", "OECD", "WHO", "GDP", "GNP", "WTO", "YH"}


ERROR_TYPE_KO = {
    "ANSWER_IN_PASSAGE": "정답 선지가 지문/질문에 노출됨",
    "ANSWER_RESTATEMENT_SUSPECT": "정답 선지가 지문 내용을 의미상 재진술한 의심",
    "ANSWER_LENGTH_BIAS": "정답 선지가 유독 길거나 짧음",
    "CHOICE_FORMAT_ERROR": "선지 문장/형식 오류",
    "QUESTION_CHOICE_MISMATCH": "질문 요구와 선지 내용/형식이 맞지 않음",
    "QUESTION_MARKER_MISMATCH": "지문에 없는 표식/밑줄/(가) 등을 참조함",
    "ORDER_CHOICE_CONTEXT_REQUIRED": "순서형/기호형 선지라 문항 단위 확인 필요",
    "ODD_DISTRACTOR": "오답 선지가 너무 어색하거나 쉽게 제거되는 의심",
    "DUPLICATE_OR_SIMILAR_CHOICE": "중복되거나 거의 같은 선지가 있음",
    "NO_OR_MULTI_ANSWER": "정답이 없거나 2개 이상임",
    "WEIRD_CHOICE": "문제 맥락상 너무 이상하거나 부적절한 선지",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pick_first(data: dict[str, Any], keys: list[str], default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def normalize_questions(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ["questions", "items", "data", "results"]:
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("입력 JSON은 문항 list이거나 questions/items/data/results list를 가진 dict여야 합니다.")


def normalize_choice(choice: Any, idx: int, answer_number: int | None) -> dict[str, Any]:
    if isinstance(choice, str):
        number = idx + 1
        return {"number": number, "text": choice, "is_answer": int(answer_number == number)}
    if not isinstance(choice, dict):
        raise ValueError(f"choices[{idx}] 형식을 이해할 수 없습니다: {choice!r}")

    number = int(choice.get("number") or choice.get("choice_no") or choice.get("no") or idx + 1)
    text = str(pick_first(choice, ["text", "choice", "content", "value"], ""))
    if "is_answer" in choice:
        is_answer = int(bool(choice.get("is_answer")))
    elif "label" in choice and str(choice.get("label")).lower() in {"answer", "correct", "1", "true"}:
        is_answer = 1
    else:
        is_answer = int(answer_number == number)
    return {"number": number, "text": text, "is_answer": is_answer}


def question_rule_codes(choices: list[dict[str, Any]]) -> list[str]:
    codes: list[str] = []
    answer_count = sum(int(choice.get("is_answer", 0)) for choice in choices)
    if answer_count != 1:
        codes.append("NO_OR_MULTI_ANSWER")

    normalized = [re.sub(r"\s+", "", str(choice.get("text", ""))).lower() for choice in choices]
    seen = set()
    for value in normalized:
        if value and value in seen:
            codes.append("DUPLICATE_OR_SIMILAR_CHOICE")
            break
        seen.add(value)
    return sorted(set(codes))


def flatten_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for q_idx, question_item in enumerate(questions):
        question_id = str(pick_first(question_item, ["question_id", "id", "seed_id"], f"question_{q_idx + 1}"))
        passage = str(pick_first(question_item, ["passage", "material", "context", "지문"], ""))
        question = str(pick_first(question_item, ["question", "question_text", "질문"], ""))
        answer_number_raw = pick_first(question_item, ["answer_number", "answer", "correct_answer", "정답"], None)
        try:
            answer_number = int(answer_number_raw) if answer_number_raw is not None else None
        except (TypeError, ValueError):
            answer_number = None

        raw_choices = question_item.get("choices") or question_item.get("options") or question_item.get("선지")
        if not isinstance(raw_choices, list) or not raw_choices:
            raise ValueError(f"{question_id}: choices/options/선지 list가 필요합니다.")

        choices = [normalize_choice(choice, idx, answer_number) for idx, choice in enumerate(raw_choices)]
        q_rule_codes = question_rule_codes(choices)
        all_choices = [
            {
                "number": int(choice["number"]),
                "text": str(choice["text"]),
                "is_answer": int(choice["is_answer"]),
                "length": len(str(choice["text"]).strip()),
            }
            for choice in choices
        ]

        for choice in choices:
            rows.append(
                {
                    "question_id": question_id,
                    "source_type": str(question_item.get("source_type") or "generated"),
                    "choice_no": int(choice["number"]),
                    "is_answer": int(choice["is_answer"]),
                    "passage": passage,
                    "question": question,
                    "choice": str(choice["text"]),
                    "context": {
                        "all_choices": all_choices,
                        "answer_count": sum(int(item["is_answer"]) for item in all_choices),
                        "answer_numbers": [int(item["number"]) for item in all_choices if int(item["is_answer"]) == 1],
                        "question_rule_codes": q_rule_codes,
                    },
                }
            )
    return rows


def make_input_text(row: dict[str, Any]) -> str:
    is_answer_text = "정답 선지" if int(row.get("is_answer", 0)) == 1 else "오답 선지"
    return (
        "[지문]\n"
        + str(row.get("passage", ""))
        + "\n\n[질문]\n"
        + str(row.get("question", ""))
        + "\n\n[선지]\n"
        + str(row.get("choice", ""))
        + "\n\n[정답 여부]\n"
        + is_answer_text
    )


def is_option_combo_text(text: Any) -> bool:
    return bool(re.fullmatch(r"\s*[ㄱ-ㅎ](\s*[,·ㆍ]\s*[ㄱ-ㅎ])+\s*", str(text or "")))


def is_marker_only_text(text: Any) -> bool:
    value = str(text or "").strip()
    return bool(re.fullmatch(r"\([가-힣A-Za-z]\)|[㉠-㉻]", value))


def is_order_combo_text(text: Any) -> bool:
    value = str(text or "").strip()
    marker = r"(?:\([가-힣A-Za-z]\)|[가-힣A-Za-z]|[ㄱ-ㅎ])"
    return bool(re.fullmatch(rf"{marker}\s*[-~→>]\s*{marker}(?:\s*[-~→>]\s*{marker})+", value))


def is_order_question(question: Any) -> bool:
    return bool(re.search(r"순서|나열|먼저|이후|이전|전개된 시기|시기를.*고른", str(question or "")))


def is_normal_short_choice(row: dict[str, Any]) -> bool:
    question = str(row.get("question", ""))
    text = str(row.get("choice", ""))
    if is_option_combo_text(text):
        return True
    if is_order_combo_text(text) and is_order_question(question):
        return True
    if is_marker_only_text(text) and re.search(r"연표|시기|지도|지역|찾은|고른|위치", question):
        return True
    if re.search(r"보기.*고른|<보기>|＜보기＞|퀴즈|들어갈 내용", question) and len(text.strip()) <= 8:
        return True
    return False


def is_excluded_choice_type(row: dict[str, Any]) -> bool:
    # "ㄱ, ㄴ" 조합형과 "(가) - (나) - (다)" 순서형은 선지 하나만으로 오류 판단하지 않습니다.
    text = str(row.get("choice", ""))
    question = str(row.get("question", ""))
    return is_option_combo_text(text) or (is_order_combo_text(text) and is_order_question(question))


def has_disallowed_foreign_text(text: str) -> bool:
    alpha_tokens = re.findall(r"[A-Za-z]+", str(text or ""))
    if alpha_tokens and all(token.upper() in APPROVED_ACRONYMS for token in alpha_tokens):
        return False
    return bool(re.search(r"[A-Za-zА-Яа-я]", str(text or "")))


def has_choice_format_error(row: dict[str, Any]) -> bool:
    text = str(row.get("choice", "")).strip()
    if not text:
        return True
    if has_disallowed_foreign_text(text):
        return True
    if re.search(r"[一-龥]", text) and not re.search(r"[가-힣]", text):
        return True
    if re.search(r"근거로|풀이|정답|오답|선택지|자료를 보면|해야 한다", text):
        return True
    if re.search(r"[\[\]{}]", text):
        return True
    unnatural_patterns = [
        r"착수이다\.$",
        r"수록이다\.$",
        r"배향이다\.$",
        r"바뀜이다\.$",
        r"지칭이다\.$",
        r"조약임이다\.$",
        r"경부터이다\.$",
        r"하나이었다\.$",
        r"칭호이었다\.$",
        r"국가로이다\.$",
        r"나라로이다\.$",
        r"체에 참여하였다\.$",
        r"사회주의로대치",
    ]
    return any(re.search(pattern, text) for pattern in unnatural_patterns)


def has_question_choice_mismatch(row: dict[str, Any]) -> bool:
    question = str(row.get("question", ""))
    text = str(row.get("choice", "")).strip()
    if is_order_question(question) and not is_order_combo_text(text):
        if re.search(r"순서대로|나열", question):
            return True
    if re.search(r"시기에 볼 수 있는 모습|시기에 있었던 사실", question):
        definition_like = bool(re.search(r"은 |는 |이다\.|이었다\.|단체|제도|법전|군대|문화유산", text))
        scene_like = bool(re.search(r"공포|실시|설치|반포|전개|출범|창설|활동|볼 수|시행|파견", text))
        if definition_like and not scene_like:
            return True
    return False


def keyword_terms(value: Any) -> list[str]:
    stopwords = {
        "다음",
        "자료",
        "설명",
        "옳은",
        "것은",
        "대한",
        "으로",
        "에서",
        "이다",
        "있다",
        "하였다",
        "되었다",
        "통해",
        "관련",
        "대표적",
        "대상",
        "문화유산",
        "제도",
    }
    terms = re.findall(r"[가-힣0-9]{2,}", str(value or ""))
    return [term for term in terms if term not in stopwords]


def is_answer_mostly_exposed(row: dict[str, Any]) -> bool:
    if int(row.get("is_answer", 0)) != 1:
        return False
    if is_normal_short_choice(row):
        return False
    text = str(row.get("choice", ""))
    if len(text.strip()) > 45:
        return False
    terms = keyword_terms(text)
    if len(terms) < 2:
        return False
    passage_question = str(row.get("passage", "")) + " " + str(row.get("question", ""))
    hit_count = sum(1 for term in terms if term in passage_question)
    return hit_count / max(len(terms), 1) >= 0.75


def choice_length_stats(row: dict[str, Any]) -> dict[str, float]:
    choice_no = int(row.get("choice_no") or 0)
    choice_length = len(str(row.get("choice", "")).strip())
    all_choices = ((row.get("context") or {}).get("all_choices") or [])
    other_lengths = [
        int(item.get("length") or len(str(item.get("text") or "")))
        for item in all_choices
        if int(item.get("number") or 0) != choice_no and item.get("text")
    ]
    if not other_lengths:
        return {
            "choice_length": float(choice_length),
            "other_avg_length": 0.0,
            "other_median_length": 0.0,
            "avg_length_diff": 0.0,
            "median_length_ratio": 0.0,
        }
    avg_length = sum(other_lengths) / len(other_lengths)
    median_length = sorted(other_lengths)[len(other_lengths) // 2]
    ratio = choice_length / median_length if median_length else 0.0
    return {
        "choice_length": float(choice_length),
        "other_avg_length": float(avg_length),
        "other_median_length": float(median_length),
        "avg_length_diff": float(choice_length - avg_length),
        "median_length_ratio": float(ratio),
    }


def has_answer_length_bias(row: dict[str, Any]) -> bool:
    if int(row.get("is_answer", 0)) != 1:
        return False
    if is_normal_short_choice(row):
        return False
    stats = choice_length_stats(row)
    text_length = stats["choice_length"]
    avg_length = stats["other_avg_length"]
    median_length = stats["other_median_length"]
    if avg_length <= 0 or median_length <= 0:
        return False
    too_short = text_length <= median_length * 0.60 and avg_length - text_length >= 15
    too_long = text_length >= median_length * 1.80 and text_length - avg_length >= 12
    return bool(too_short or too_long)


def infer_error_codes(row: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    text = str(row.get("choice", ""))
    passage_question = re.sub(r"\s+", "", str(row.get("passage", "")) + " " + str(row.get("question", "")))
    choice_norm = re.sub(r"\s+", "", text)
    if int(row.get("is_answer", 0)) == 1 and choice_norm and choice_norm in passage_question and not is_normal_short_choice(row):
        codes.append("ANSWER_IN_PASSAGE")
    if int(row.get("is_answer", 0)) == 1 and is_answer_mostly_exposed(row):
        codes.append("ANSWER_IN_PASSAGE")
    if int(row.get("is_answer", 0)) == 1 and has_answer_length_bias(row):
        codes.append("ANSWER_LENGTH_BIAS")
    if has_choice_format_error(row):
        codes.append("CHOICE_FORMAT_ERROR")
    if has_question_choice_mismatch(row):
        codes.append("QUESTION_CHOICE_MISMATCH")
    if (
        re.search(r"\([가-힣A-Za-z]\)|밑줄|표식|표지", text)
        and not re.search(r"\([가-힣A-Za-z]\)|밑줄|표식|표지", str(row.get("passage", "")))
        and not is_normal_short_choice(row)
    ):
        codes.append("QUESTION_MARKER_MISMATCH")
    return sorted(set(codes))


def infer_rule_error_codes(row: dict[str, Any]) -> list[str]:
    codes = set(infer_error_codes(row))
    context = row.get("context") or {}
    codes.update(context.get("question_rule_codes") or [])
    return sorted(codes)


def blocking_codes(codes: list[str]) -> list[str]:
    return sorted(code for code in set(codes) if code not in ADVISORY_ONLY_CODES)


def advisory_codes(codes: list[str]) -> list[str]:
    return sorted(code for code in set(codes) if code in ADVISORY_ONLY_CODES)


def explain_model_only_error(row: dict[str, Any], model_codes: list[str]) -> list[str]:
    if model_codes:
        return model_codes
    text = str(row.get("choice", ""))
    question = str(row.get("question", ""))
    if is_order_combo_text(text) and is_order_question(question):
        return ["ORDER_CHOICE_CONTEXT_REQUIRED"]
    if has_choice_format_error(row):
        return ["CHOICE_FORMAT_ERROR"]
    if has_question_choice_mismatch(row):
        return ["QUESTION_CHOICE_MISMATCH"]
    if int(row.get("is_answer", 0)) == 1:
        return ["ANSWER_RESTATEMENT_SUSPECT"]
    if int(row.get("is_answer", 0)) == 0:
        return ["ODD_DISTRACTOR"]
    return ["WEIRD_CHOICE"]


def predict_error_probs(
    rows: list[dict[str, Any]],
    model_dir: Path,
    batch_size: int,
    max_length: int,
    device_name: str,
) -> list[float]:
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir)
    device = torch.device(device_name if device_name != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    model.to(device)
    model.eval()

    error_probs: list[float] = []
    for start in range(0, len(rows), batch_size):
        batch_rows = rows[start : start + batch_size]
        encoded = tokenizer(
            [make_input_text(row) for row in batch_rows],
            truncation=True,
            padding=True,
            max_length=max_length,
            return_tensors="pt",
        )
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with torch.no_grad():
            logits = model(**encoded).logits
            probs = torch.softmax(logits, dim=-1)
        error_probs.extend(probs[:, 0].detach().cpu().tolist())
    return error_probs


def build_review_rows(rows: list[dict[str, Any]], error_probs: list[float], threshold: float) -> list[dict[str, Any]]:
    review_rows: list[dict[str, Any]] = []
    for row, error_prob in zip(rows, error_probs):
        stats = choice_length_stats(row)
        if is_excluded_choice_type(row):
            review_rows.append(
                {
                    "검수상태": "검수제외",
                    "우선순위": "LOW",
                    "오류확률": round(float(error_prob), 6),
                    "판단근거": "excluded_choice_type",
                    "오류코드": "",
                    "참고코드": "EXCLUDED_COMBO_OR_ORDER_CHOICE",
                    "문항ID": row.get("question_id"),
                    "선지번호": row.get("choice_no"),
                    "정답여부": "정답" if int(row.get("is_answer", 0)) == 1 else "오답",
                    "선지길이": int(stats["choice_length"]),
                    "다른선지평균길이": round(float(stats["other_avg_length"]), 2),
                    "다른선지중앙값길이": round(float(stats["other_median_length"]), 2),
                    "평균대비길이차이": round(float(stats["avg_length_diff"]), 2),
                    "중앙값대비길이비율": round(float(stats["median_length_ratio"]), 3),
                    "지문": row.get("passage", ""),
                    "질문": row.get("question", ""),
                    "선지": row.get("choice", ""),
                    "model_pred_label": 1,
                    "rule_error_codes": "",
                    "blocking_rule_codes": "",
                    "advisory_rule_codes": "EXCLUDED_COMBO_OR_ORDER_CHOICE",
                }
            )
            continue

        model_pred_label = 0 if float(error_prob) >= threshold else 1
        model_codes = explain_model_only_error(row, infer_error_codes(row)) if model_pred_label == 0 else []
        rule_codes = infer_rule_error_codes(row)
        blocking_rule_codes = blocking_codes(rule_codes)
        advisory_rule_codes = advisory_codes(rule_codes)

        if model_pred_label == 0:
            review_status = "검수필요"
            decision_source = "model+rule" if blocking_rule_codes else "model"
            error_codes = sorted(set(model_codes) | set(blocking_rule_codes))
            priority = "HIGH" if float(error_prob) >= HIGH_CONFIDENCE_ERROR_PROB else "MEDIUM"
        elif blocking_rule_codes:
            review_status = "참고검수"
            decision_source = "rule"
            error_codes = blocking_rule_codes
            priority = "LOW"
        else:
            review_status = "통과"
            decision_source = "advisory" if advisory_rule_codes else "none"
            error_codes = []
            priority = "LOW"

        review_rows.append(
            {
                "검수상태": review_status,
                "우선순위": priority,
                "오류확률": round(float(error_prob), 6),
                "판단근거": decision_source,
                "오류코드": "|".join(error_codes),
                "참고코드": "|".join(advisory_rule_codes),
                "문항ID": row.get("question_id"),
                "선지번호": row.get("choice_no"),
                "정답여부": "정답" if int(row.get("is_answer", 0)) == 1 else "오답",
                "선지길이": int(stats["choice_length"]),
                "다른선지평균길이": round(float(stats["other_avg_length"]), 2),
                "다른선지중앙값길이": round(float(stats["other_median_length"]), 2),
                "평균대비길이차이": round(float(stats["avg_length_diff"]), 2),
                "중앙값대비길이비율": round(float(stats["median_length_ratio"]), 3),
                "지문": row.get("passage", ""),
                "질문": row.get("question", ""),
                "선지": row.get("choice", ""),
                "model_pred_label": model_pred_label,
                "rule_error_codes": "|".join(rule_codes),
                "blocking_rule_codes": "|".join(blocking_rule_codes),
                "advisory_rule_codes": "|".join(advisory_rule_codes),
            }
        )
    return review_rows


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "검수상태",
        "우선순위",
        "오류확률",
        "판단근거",
        "오류코드",
        "참고코드",
        "문항ID",
        "선지번호",
        "정답여부",
        "선지길이",
        "다른선지평균길이",
        "다른선지중앙값길이",
        "평균대비길이차이",
        "중앙값대비길이비율",
        "지문",
        "질문",
        "선지",
        "model_pred_label",
        "rule_error_codes",
        "blocking_rule_codes",
        "advisory_rule_codes",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_count: dict[str, int] = {}
    code_count: dict[str, int] = {}
    for row in rows:
        status_count[row["검수상태"]] = status_count.get(row["검수상태"], 0) + 1
        for code in str(row.get("오류코드") or "").split("|"):
            if code:
                code_count[code] = code_count.get(code, 0) + 1
    return {"status_count": status_count, "error_code_count": code_count, "total_choices": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description="선지 단위 문제 품질 검수 v15 추론 코드")
    parser.add_argument("--model_dir", type=Path, default=Path("model"), help="학습된 model 폴더 경로")
    parser.add_argument("--input", type=Path, required=True, help="검수할 문제 JSON")
    parser.add_argument("--output_csv", type=Path, default=Path("review.csv"), help="검수 결과 CSV 경로")
    parser.add_argument("--output_json", type=Path, default=None, help="검수 결과 JSON 경로")
    parser.add_argument("--threshold", type=float, default=THRESHOLD, help="오류 확률 threshold")
    parser.add_argument("--batch_size", type=int, default=32, help="추론 batch size")
    parser.add_argument("--max_length", type=int, default=MAX_LENGTH, help="token max length")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], help="추론 장치")
    args = parser.parse_args()

    questions = normalize_questions(read_json(args.input))
    rows = flatten_questions(questions)
    error_probs = predict_error_probs(rows, args.model_dir, args.batch_size, args.max_length, args.device)
    review_rows = build_review_rows(rows, error_probs, args.threshold)

    write_review_csv(args.output_csv, review_rows)
    if args.output_json:
        write_json(args.output_json, {"summary": summarize(review_rows), "rows": review_rows})

    summary = summarize(review_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"CSV 저장 완료: {args.output_csv}")
    if args.output_json:
        print(f"JSON 저장 완료: {args.output_json}")


if __name__ == "__main__":
    main()

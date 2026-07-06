from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import pdfplumber


CIRCLED = ["①", "②", "③", "④", "⑤"]
CHOICE_TO_NUM = {symbol: idx + 1 for idx, symbol in enumerate(CIRCLED)}


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def find_default_pdf(kind: str) -> Path:
    downloads = Path.home() / "Downloads"
    if not downloads.exists():
        raise FileNotFoundError(f"Downloads folder not found: {downloads}")

    candidates = []
    for path in downloads.glob("*.pdf"):
        name = path.name
        if "79" not in name:
            continue
        if kind == "question" and "문제" in name and "정답" not in name:
            candidates.append(path)
        if kind == "answer" and ("정답" in name or "해설" in name):
            candidates.append(path)

    if not candidates:
        raise FileNotFoundError(f"No default {kind} PDF found in {downloads}")

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def extract_pdf_text(path: Path) -> str:
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    pages = []
    with pdfplumber.open(path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            pages.append(f"\n\n[[PAGE {idx}]]\n{text}")
    return "\n".join(pages)


def normalize_lines(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def compact_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def question_start_candidates(text: str) -> list[tuple[int, int]]:
    pattern = re.compile(r"(?m)^(?P<qid>[1-9]|[1-4]\d|50)\.\s+")
    candidates: list[tuple[int, int]] = []
    for match in pattern.finditer(text):
        nearby = text[match.start() : match.start() + 500]
        if re.search(r"\[[123]점\]", nearby):
            candidates.append((match.start(), int(match.group("qid"))))
    return candidates


def filter_sequential_starts(candidates: list[tuple[int, int]]) -> list[tuple[int, int]]:
    sequential: list[tuple[int, int]] = []
    expected = 1
    for start, qid in candidates:
        if qid == expected:
            sequential.append((start, qid))
            expected += 1
        if expected > 50:
            break

    if len(sequential) >= 45:
        return sequential

    first_by_qid: dict[int, int] = {}
    for start, qid in candidates:
        first_by_qid.setdefault(qid, start)
    return [(first_by_qid[qid], qid) for qid in sorted(first_by_qid)]


def parse_question_block(block: str, qid: int) -> dict[str, Any]:
    score_match = re.search(r"\[([123])점\]", block)
    if not score_match:
        raise ValueError(f"Q{qid}: target score not found")
    target_score = int(score_match.group(1))

    positions = []
    cursor = 0
    for symbol in CIRCLED:
        idx = block.find(symbol, cursor)
        if idx < 0:
            raise ValueError(f"Q{qid}: choice {symbol} not found")
        positions.append((symbol, idx))
        cursor = idx + len(symbol)

    stem = block[: positions[0][1]]
    stem = re.sub(r"^\s*\d+\.\s*", "", stem)
    stem = re.sub(r"\[[123]점\]", "", stem)
    stem = normalize_lines(stem)

    choices = []
    for i, (symbol, start) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(block)
        choice_text = block[start + len(symbol) : end]
        choices.append(
            {
                "number": CHOICE_TO_NUM[symbol],
                "label": symbol,
                "text": compact_space(choice_text),
            }
        )

    return {
        "question_id": qid,
        "target_score": target_score,
        "stem": stem,
        "choices": choices,
    }


def parse_questions(text: str) -> list[dict[str, Any]]:
    text = normalize_lines(text)
    starts = filter_sequential_starts(question_start_candidates(text))
    questions: list[dict[str, Any]] = []
    for idx, (start, qid) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(text)
        block = text[start:end].strip()
        questions.append(parse_question_block(block, qid))
    return questions


def parse_answer_key(text: str) -> dict[int, str]:
    answer_key: dict[int, str] = {}
    for match in re.finditer(r"(?<!\d)([1-9]|[1-4]\d|50)\.\s*([①②③④⑤])", text):
        qid = int(match.group(1))
        answer_key.setdefault(qid, match.group(2))
    return answer_key


def explanation_section(text: str) -> str:
    marker_positions = [pos for pos in [text.find("문항별 해설"), text.find("해설")] if pos >= 0]
    if marker_positions:
        return text[min(marker_positions) :]
    return text


def parse_explanation_blocks(text: str) -> dict[int, dict[str, str]]:
    section = normalize_lines(explanation_section(text))
    starts = filter_sequential_starts(question_start_candidates(section))
    explanations: dict[int, dict[str, str]] = {}

    for idx, (start, qid) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(section)
        block = section[start:end].strip()
        answer_match = re.search(r"정답\s*([①②③④⑤])", block)
        explanation_match = re.search(r"해설\s*(.*)", block, flags=re.S)
        explanations[qid] = {
            "answer_label": answer_match.group(1) if answer_match else "",
            "explanation": normalize_lines(explanation_match.group(1)) if explanation_match else "",
            "raw_block": block,
        }
    return explanations


def merge_dataset(
    questions: list[dict[str, Any]],
    answer_key: dict[int, str],
    explanations: dict[int, dict[str, str]],
) -> list[dict[str, Any]]:
    records = []
    for question in questions:
        qid = int(question["question_id"])
        answer_label = explanations.get(qid, {}).get("answer_label") or answer_key.get(qid, "")
        answer_number = CHOICE_TO_NUM.get(answer_label)
        records.append(
            {
                **question,
                "answer_label": answer_label,
                "answer": answer_number,
                "explanation": explanations.get(qid, {}).get("explanation", ""),
            }
        )
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Korean history mock exam PDFs for API evaluation.")
    parser.add_argument("--question-pdf", type=Path, default=None)
    parser.add_argument("--answer-pdf", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=script_dir() / "data")
    args = parser.parse_args()

    question_pdf = args.question_pdf or find_default_pdf("question")
    answer_pdf = args.answer_pdf or find_default_pdf("answer")
    out_dir = args.out_dir
    raw_dir = out_dir / "raw"
    processed_dir = out_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    question_text = extract_pdf_text(question_pdf)
    answer_text = extract_pdf_text(answer_pdf)
    (raw_dir / "questions.txt").write_text(question_text, encoding="utf-8")
    (raw_dir / "answers.txt").write_text(answer_text, encoding="utf-8")

    questions = parse_questions(question_text)
    answer_key = parse_answer_key(answer_text)
    explanations = parse_explanation_blocks(answer_text)
    records = merge_dataset(questions, answer_key, explanations)

    (processed_dir / "questions.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_jsonl(processed_dir / "questions.jsonl", records)

    missing_answer = [r["question_id"] for r in records if not r.get("answer")]
    missing_explanation = [r["question_id"] for r in records if not r.get("explanation")]
    summary = {
        "question_pdf": str(question_pdf),
        "answer_pdf": str(answer_pdf),
        "question_count": len(questions),
        "answer_key_count": len(answer_key),
        "explanation_count": len(explanations),
        "dataset_count": len(records),
        "missing_answer": missing_answer,
        "missing_explanation": missing_explanation,
    }
    (processed_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if len(records) != 50 or missing_answer or missing_explanation:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

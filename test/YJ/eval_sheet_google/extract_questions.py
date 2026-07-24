from __future__ import annotations

import json
import re
from pathlib import Path

import pdfplumber


CHOICE_RE = re.compile(r"^([①②③④⑤])\s*(.*)")
QUESTION_RE = re.compile(r"(?m)^(\d{1,2})\.\s+")
ANSWER_RE = re.compile(r"(\d{1,2})\s+([①②③④⑤])")


def find_pdf() -> Path:
    downloads = Path.home() / "Downloads"
    candidates = [p for p in downloads.glob("*.pdf") if "50" in p.name]
    if not candidates:
        raise FileNotFoundError("No 50-question PDF found in Downloads")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<<PAGE"):
            continue
        if line.startswith("한국사능력검정시험 심화 모의고사 50문항"):
            continue
        if line in {"문제", "정답표", "문항 번호와 정답만 확인할 수 있도록 구성했습니다."}:
            continue
        if line.startswith("※"):
            continue
        lines.append(line)
    return lines


def parse_question(number: int, block: str, answers: dict[int, str]) -> dict:
    lines = clean_lines(block)
    score_match = re.search(r"\[(\d)점\]", lines[0] if lines else "")
    score = int(score_match.group(1)) if score_match else None

    stem_lines: list[str] = []
    choices: dict[str, str] = {}
    current_label: str | None = None

    for line in lines:
        m = CHOICE_RE.match(line)
        if m:
            current_label = m.group(1)
            choices[current_label] = m.group(2).strip()
        elif current_label:
            choices[current_label] = (choices[current_label] + " " + line).strip()
        else:
            stem_lines.append(line)

    stem = " ".join(stem_lines).strip()
    stem = re.sub(r"^\d{1,2}\.\s*", "", stem)
    stem = re.sub(r"\s*\[\d점\]\s*", " ", stem).strip()

    labels = ["①", "②", "③", "④", "⑤"]
    return {
        "number": number,
        "target_score": score,
        "answer_label": answers.get(number, ""),
        "stem_material": stem,
        "choice_1": choices.get("①", ""),
        "choice_2": choices.get("②", ""),
        "choice_3": choices.get("③", ""),
        "choice_4": choices.get("④", ""),
        "choice_5": choices.get("⑤", ""),
        "choice_count": sum(1 for label in labels if choices.get(label)),
    }


def main() -> None:
    pdf = find_pdf()
    outdir = Path(__file__).resolve().parent

    pages: list[str] = []
    with pdfplumber.open(pdf) as doc:
        for i, page in enumerate(doc.pages, 1):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            pages.append(f"\n<<PAGE {i}>>\n{text}")

    full = "\n".join(pages)
    (outdir / "extracted_pdf_text.txt").write_text(full, encoding="utf-8")

    before_answers, _, answer_text = full.rpartition("정답표")
    answers = {int(n): label for n, label in ANSWER_RE.findall(answer_text)}

    matches = list(QUESTION_RE.finditer(before_answers))
    questions = []
    for idx, match in enumerate(matches):
        number = int(match.group(1))
        if not 1 <= number <= 50:
            continue
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(before_answers)
        questions.append(parse_question(number, before_answers[start:end], answers))

    questions.sort(key=lambda row: row["number"])
    if len(questions) != 50:
        raise RuntimeError(f"Expected 50 questions, got {len(questions)}")

    output = {
        "source_pdf": str(pdf),
        "question_count": len(questions),
        "questions": questions,
    }
    (outdir / "questions.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(questions)} questions from {pdf.name}")


if __name__ == "__main__":
    main()

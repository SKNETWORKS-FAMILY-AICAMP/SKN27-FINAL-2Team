"""Keep only golden questions that have at least one strict matched document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
DEFAULT_GOLDEN = ROOT / "etl" / "preprocessing" / "history" / "embedding" / "golden_questions.jsonl"
DEFAULT_DOCUMENTS = HERE / "golden_matched_documents_444.json"
DEFAULT_OUTPUT = HERE / "golden_questions_strict_matched_444.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    matched_ids = {golden_id for document in json.loads(args.documents.read_text(encoding="utf-8")) for golden_id in document["matched_golden_ids"]}
    rows = [json.loads(line) for line in args.golden.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = [row for row in rows if row["id"] in matched_ids]
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected), encoding="utf-8")
    print(f"{len(selected)} questions saved to {args.output}")


if __name__ == "__main__":
    main()

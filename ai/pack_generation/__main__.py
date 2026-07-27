"""독립 closed-pack 생성 CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai.pack_generation.builder import validate_pack_bank


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate independently prepared closed packs.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--existing-bank", type=Path)
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    packs = list(data.get("packs") or [])
    existing_packs = []
    if args.existing_bank:
        existing_data = json.loads(args.existing_bank.read_text(encoding="utf-8-sig"))
        existing_packs = list(existing_data.get("packs") or []) if isinstance(existing_data, dict) else list(existing_data)
    validate_pack_bank([*existing_packs, *packs])
    result = {"pack_count": len(packs), "packs": packs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "packs": len(packs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

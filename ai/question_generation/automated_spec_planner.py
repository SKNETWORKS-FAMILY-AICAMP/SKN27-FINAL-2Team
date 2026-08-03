"""Plan reviewed Graph Pack specifications without interactive prompts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ai.pack_generation.graph_builder import candidate_hops_for_difficulty, read_graph_candidates
from ai.question_generation.interactive_cli import (
    build_planned_spec,
    distractor_types_for_frame,
    graph_options,
    instructions_for_frame,
    plan_pack_spec,
    planning_contracts,
)


def read_reviewed_generation_contracts(path: Path) -> dict[str, list[dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    packs = data.get("packs") if isinstance(data, dict) else data
    if not isinstance(packs, list) or not packs:
        raise ValueError("contract pack bank must contain a non-empty packs array")

    distractors: set[tuple[str, str, str, str, str]] = set()
    instructions: set[tuple[str, str, str, str, str, str]] = set()
    for pack in packs:
        topic_type = str(pack.get("topic_type") or "").strip()
        for frame in pack.get("question_frames") or []:
            material_type = str(frame.get("material_type") or "").strip()
            major_type = str(frame.get("major_type") or "").strip()
            minor_type = str(frame.get("minor_type") or "").strip()
            distractor_type = str(frame.get("distractor_type") or "").strip()
            question_task = str(frame.get("question_task") or "").strip()
            instruction = str(frame.get("question_task_instruction") or "").strip()
            if all((topic_type, material_type, major_type, minor_type, distractor_type)):
                distractors.add(
                    (topic_type, material_type, major_type, minor_type, distractor_type)
                )
            if all((topic_type, question_task, material_type, major_type, minor_type, instruction)):
                instructions.add(
                    (
                        topic_type,
                        question_task,
                        material_type,
                        major_type,
                        minor_type,
                        instruction,
                    )
                )

    if not distractors or not instructions:
        raise ValueError("reviewed pack bank does not contain generation contracts")
    return {
        "distractors": [
            dict(
                zip(
                    ("topic_type", "material_type", "major_type", "minor_type", "distractor_type"),
                    row,
                )
            )
            for row in sorted(distractors)
        ],
        "instructions": [
            dict(
                zip(
                    (
                        "topic_type",
                        "question_task",
                        "material_type",
                        "major_type",
                        "minor_type",
                        "question_task_instruction",
                    ),
                    row,
                )
            )
            for row in sorted(instructions)
        ],
    }


def plan_automated_specs(
    pack_count: int,
    model: str,
    contract_bank: Path,
    seed: int,
    base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    if pack_count < 1:
        raise ValueError("pack_count must be a positive integer")
    if not model or not api_key:
        raise ValueError("pack planning requires a model and OPENAI_API_KEY")

    options = sorted(
        graph_options(),
        key=lambda row: (row["era_id"], row["topic_id"], row["owner_type"]),
    )
    if not options:
        raise ValueError("Fact Graph does not contain eligible era and topic combinations")

    generation_contracts = read_reviewed_generation_contracts(contract_bank)
    specs: list[dict[str, Any]] = []
    used_selections: set[tuple[int, str, str, str]] = set()
    used_specs: set[tuple[Any, ...]] = set()

    for pack_index in range(pack_count):
        difficulty = (pack_index % 3) + 1
        start_index = (seed + pack_index) % len(options)
        ordered_options = options[start_index:] + options[:start_index]
        selected_spec: dict[str, Any] | None = None

        for option in ordered_options:
            selection_key = (
                difficulty,
                option["era_id"],
                option["topic_id"],
                option["owner_type"],
            )
            if selection_key in used_selections:
                continue

            selection = {
                "anchor_node_id": option["topic_id"],
                "candidate_hops": candidate_hops_for_difficulty(difficulty),
                "era_id": option["era_id"],
                "era_name": option["era_name"],
                "topic_id": option["topic_id"],
                "topic_name": option["topic_name"],
                "owner_type": option["owner_type"],
                "difficulty": difficulty,
            }
            candidates = read_graph_candidates(selection)
            if len(candidates) < 9:
                continue

            try:
                contracts = planning_contracts(
                    [row["article_id"] for row in candidates],
                    generation_contracts,
                )
                plan = plan_pack_spec(
                    selection,
                    contracts,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                )
                contracts_by_index = {
                    row["contract_index"]: row for row in contracts["frame_contracts"]
                }
                for frame_index, frame in enumerate(plan.get("question_frames") or []):
                    contract = contracts_by_index.get(frame.get("contract_index"))
                    if not contract:
                        raise ValueError("spec planner selected an unknown frame contract")
                    instructions = sorted(instructions_for_frame(plan, contract, contracts))
                    distractors = sorted(distractor_types_for_frame(plan, contract, contracts))
                    if not instructions or not distractors:
                        raise ValueError("selected frame does not have reviewed generation contracts")
                    frame["question_task_instruction"] = instructions[
                        (seed + pack_index + frame_index) % len(instructions)
                    ]
                    frame["distractor_type"] = distractors[
                        (seed + pack_index + frame_index) % len(distractors)
                    ]
                spec = build_planned_spec(selection, contracts, plan)
            except ValueError as exc:
                print(
                    "Skipping Graph Pack candidate "
                    f"{option['era_id']}/{option['topic_id']}/{option['owner_type']}: {exc}",
                    file=sys.stderr,
                )
                continue

            spec_key = (
                spec["anchor_node_id"],
                spec["candidate_hops"],
                spec["era_id"],
                spec["owner_type"],
                spec["relation_axis_id"],
                spec["topic_type"],
                tuple(
                    (
                        frame["stem_pattern"],
                        frame["material_type"],
                        frame["major_type"],
                        frame["minor_type"],
                    )
                    for frame in spec["question_frames"]
                ),
            )
            if spec_key in used_specs:
                continue

            used_selections.add(selection_key)
            used_specs.add(spec_key)
            selected_spec = spec
            break

        if selected_spec is None:
            raise ValueError(
                f"could not plan pack {pack_index + 1}/{pack_count} for difficulty {difficulty}"
            )
        specs.append(selected_spec)

    return specs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan Graph Pack specs without interactive input.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pack-count", type=int, required=True)
    parser.add_argument(
        "--contract-bank",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--model",
        default=(
            os.getenv("OPENAI_PACK_PLAN_MODEL")
            or os.getenv("OPENAI_PACK_MODEL")
            or os.getenv("OPENAI_CHAT_MODEL")
        ),
    )
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--seed", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")
    args = parse_args()
    specs = plan_automated_specs(
        args.pack_count,
        args.model,
        args.contract_bank.expanduser().resolve(),
        args.seed,
        args.base_url,
        os.getenv("OPENAI_API_KEY", ""),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"packs": specs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Planned {len(specs)} Graph Pack specifications: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

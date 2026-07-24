"""검수 완료 문항 풀에서 유형·난이도·시대 quota를 맞춘 모의고사를 편성한다."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from question_generation.postprocess_questions import load_questions
from question_generation.workflows.closed_pack_batch import load_image_packs, render_markdown


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "question_generation" / "data" / "production_20260723"
DEFAULT_QUESTIONS = [
    DATA_ROOT / "questions" / "standard_305" / "questions.json",
    DATA_ROOT / "questions" / "chronology_and_image_75" / "questions.json",
]
DEFAULT_METADATA = [
    DATA_ROOT / "packs" / "standard_50.json",
    DATA_ROOT / "packs" / "chronology_10.json",
    DATA_ROOT / "packs" / "image_passage_10" / "manifest.json",
    DATA_ROOT / "packs" / "image_choice_27" / "manifest.json",
]
DEFAULT_QUOTA_PACK = DATA_ROOT / "packs" / "standard_50.json"
CHRONOLOGY_TASKS = {"period_between", "timeline_position", "order"}


def question_type(question: dict[str, Any]) -> str:
    if question.get("choice_mode") == "image":
        return "image"
    if question.get("question_task") in CHRONOLOGY_TASKS:
        return "chronology"
    if question.get("question_task") == "standard_select":
        return "standard"
    raise ValueError(f"지원하지 않는 question_task: {question.get('question_task')!r}")


def pack_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = data.get("packs") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"Pack 파일에 packs 배열이 없습니다: {path}")
    if data.get("version") == "image_generation_pack_manifest_v2":
        return load_image_packs(path)
    return rows


def metadata_indexes(paths: list[Path]) -> tuple[dict[str, str], dict[str, str]]:
    by_variant: dict[str, str] = {}
    by_family: dict[str, str] = {}
    for path in paths:
        for pack in pack_rows(path):
            era = str(pack.get("era") or "")
            variant = str(pack.get("variant_key") or "")
            family = str(pack.get("family_id") or "")
            if not era:
                raise ValueError(f"Pack 시대가 없습니다: {path}")
            if variant:
                if variant in by_variant and by_variant[variant] != era:
                    raise ValueError(f"variant_key 시대가 충돌합니다: {variant}")
                by_variant[variant] = era
            if family:
                if family in by_family and by_family[family] != era:
                    raise ValueError(f"family_id 시대가 충돌합니다: {family}")
                by_family[family] = era
    return by_variant, by_family


def allocate(
    capacities: dict[tuple[str, tuple[int, str]], int],
    type_quotas: dict[str, int],
    cell_quotas: dict[tuple[int, str], int],
) -> dict[tuple[str, tuple[int, str]], int]:
    source, sink = ("source",), ("sink",)
    residual: dict[tuple[Any, Any], int] = {}

    def edge(start: Any, end: Any, capacity: int) -> None:
        residual[start, end] = capacity
        residual.setdefault((end, start), 0)

    for kind, quota in type_quotas.items():
        edge(source, ("type", kind), quota)
    for (kind, cell), capacity in capacities.items():
        edge(("type", kind), ("cell", cell), capacity)
    for cell, quota in cell_quotas.items():
        edge(("cell", cell), sink, quota)

    total = sum(type_quotas.values())
    flow = 0
    while flow < total:
        queue = deque([source])
        parent = {source: None}
        while queue and sink not in parent:
            current = queue.popleft()
            for (start, end), capacity in residual.items():
                if start == current and capacity > 0 and end not in parent:
                    parent[end] = current
                    queue.append(end)
        if sink not in parent:
            raise ValueError("요청한 유형 수와 난이도·시대 quota를 동시에 채울 수 없습니다.")
        amount = total - flow
        node = sink
        while parent[node] is not None:
            amount = min(amount, residual[parent[node], node])
            node = parent[node]
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            residual[previous, node] -= amount
            residual[node, previous] += amount
            node = previous
        flow += amount

    return {
        (kind, cell): capacities[kind, cell] - residual[("type", kind), ("cell", cell)]
        for kind, cell in capacities
    }


def build_exam(
    questions: list[dict[str, Any]],
    pack_metadata: tuple[dict[str, str], dict[str, str]],
    quota_packs: list[dict[str, Any]],
    type_quotas: dict[str, int],
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    keys = [str(question.get("variant_key") or "") for question in questions]
    if "" in keys or len(set(keys)) != len(keys):
        raise ValueError("문항 variant_key가 없거나 중복됩니다.")

    cell_quotas = Counter((int(pack["difficulty"]), str(pack["era"])) for pack in quota_packs)
    if sum(type_quotas.values()) != sum(cell_quotas.values()):
        raise ValueError("유형별 문항 수 합계와 quota Pack 수가 다릅니다.")

    by_variant, by_family = pack_metadata
    buckets: dict[tuple[str, tuple[int, str]], list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        key = str(question["variant_key"])
        era = by_variant.get(key) or by_family.get(str(question.get("family_id") or ""))
        if not era:
            raise ValueError(f"문항의 검증된 Pack 시대를 찾을 수 없습니다: {key}")
        cell = (int(question["target_score"]), era)
        buckets[question_type(question), cell].append(question)

    for (kind, cell), bucket in buckets.items():
        random.Random(f"{seed}:{kind}:{cell}").shuffle(bucket)
    capacities = {key: len(bucket) for key, bucket in buckets.items()}
    allocation = allocate(capacities, type_quotas, dict(cell_quotas))
    selected = [
        question
        for key, count in allocation.items()
        for question in buckets[key][:count]
    ]
    random.Random(seed).shuffle(selected)
    metadata = {
        "count": len(selected),
        "seed": seed,
        "type_quotas": type_quotas,
        "difficulty_quotas": dict(Counter(score for score, _ in cell_quotas.elements())),
        "era_quotas": dict(Counter(era for _, era in cell_quotas.elements())),
    }
    return selected, metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append")
    parser.add_argument("--metadata-pack", type=Path, action="append")
    parser.add_argument("--quota-pack", type=Path, default=DEFAULT_QUOTA_PACK)
    parser.add_argument("--standard", type=int, required=True)
    parser.add_argument("--chronology", type=int, required=True)
    parser.add_argument("--image", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260724)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    type_quotas = {"standard": args.standard, "chronology": args.chronology, "image": args.image}
    if any(value < 0 for value in type_quotas.values()):
        raise ValueError("유형별 문항 수는 0 이상이어야 합니다.")
    inputs = args.input or DEFAULT_QUESTIONS
    metadata_paths = args.metadata_pack or DEFAULT_METADATA
    questions = [question for path in inputs for question in load_questions(path)]
    quota_packs = pack_rows(args.quota_pack)
    selected, metadata = build_exam(
        questions,
        metadata_indexes(metadata_paths),
        quota_packs,
        type_quotas,
        args.seed,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "mock_exam.json").write_text(
        json.dumps({**metadata, "questions": selected}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "mock_exam.md").write_text(render_markdown(selected), encoding="utf-8")
    print(json.dumps({"status": "complete", "output_dir": str(args.output_dir), **metadata}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

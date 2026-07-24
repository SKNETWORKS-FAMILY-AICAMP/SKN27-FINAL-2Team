"""Closed-pack 변형 또는 공식 분포 기반 모의고사를 생성한다."""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from question_generation.core.difficulty import target_score_from_difficulty
from question_generation.core.exam_distribution import ERA_ORDER, apportion, official_distribution
from question_generation.evaluation.v18 import DEFAULT_MIN_ACCEPT_SCORE
from question_generation.generation.material_rules import DEFAULT_MATERIAL_PROMPT_RULES, load_json_dict
from question_generation.retrieval.closed_pack_input import plan_variants
from question_generation.workflows.question_pipeline import invalidate, main as run_question


LABELS = "①②③④⑤"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluation_accepted(row: dict[str, Any]) -> bool:
    parsed = row.get("parsed") or {}
    if parsed.get("evaluation_profile") == "fixed_choice":
        return parsed.get("gate_result") == "PASS"
    score = parsed.get("problem_score") or {}
    return parsed.get("gate_result") == "PASS" and int(score.get("total_score") or 0) >= DEFAULT_MIN_ACCEPT_SCORE


def evaluation_repair_feedback(question: dict[str, Any], row: dict[str, Any]) -> dict[str, str]:
    """평가기 target별 수정 지시를 현재 체크포인트 컴포넌트에 연결한다."""
    parsed = row.get("parsed") or {}
    if parsed.get("final_decision") not in {"repair", "regenerate"}:
        return {}
    requested = [*(parsed.get("repair_targets") or []), *((parsed.get("problem_score") or {}).get("revision_targets") or [])]
    target_feedback = parsed.get("target_feedback")
    if not isinstance(target_feedback, dict):
        return {}
    feedback: dict[str, list[str]] = {}
    for target in requested:
        component = ""
        if target in {"material", "question", "correct"}:
            component = target
        elif str(target).startswith("choice:"):
            label = str(target).split(":", 1)[1]
            number = LABELS.index(label) + 1 if label in LABELS else 0
            choice = next((item for item in question.get("choices", []) if int(item.get("number") or 0) == number), None)
            if choice:
                if choice.get("is_answer"):
                    component = "correct"
                else:
                    slot = (choice.get("source") or {}).get("slot") or choice.get("distractor_index")
                    component = f"distractor:{slot}" if slot else ""
        value = str(target_feedback.get(target) or "").strip()
        if component and value:
            feedback.setdefault(component, []).append(value)
    return {target: "\n".join(dict.fromkeys(values)) for target, values in feedback.items()}


def render_markdown(questions: list[dict[str, Any]]) -> str:
    lines = ["# 한능검 심화 모의고사", ""]
    for index, question in enumerate(questions, 1):
        image = question.get("image") or {}
        image_url = image.get("original_image_url") or image.get("thumbnail_url")
        lines.extend([f"## {index}. [{question['target_score']}점]", ""])
        if image_url:
            lines.extend([f"![문항 시각 자료]({image_url})", ""])
        lines.extend([str(question.get("material") or ""), "", str(question.get("question") or ""), ""])
        for choice in sorted(question["choices"], key=lambda row: int(row["number"])):
            label = LABELS[int(choice["number"]) - 1]
            if choice.get("choice_image_path"):
                lines.append(f"{label} ![선지 {label}]({choice['choice_image_path']})")
            else:
                lines.append(f"{label} {choice['text']}")
        lines.append("")
    lines.extend(["# 정답", "", " | ".join(f"{index}. {question['answer_number']}" for index, question in enumerate(questions, 1)), ""])
    return "\n".join(lines)


def render_plan_markdown(plan: list[dict[str, Any]], metadata: dict[str, Any]) -> str:
    """API 없는 생성 계획을 사람이 검수할 표로 렌더링한다."""
    lines = ["# Closed-pack 50문항 Dry-run 계획", ""]
    for name in ("difficulty_quotas", "material_type_target", "material_type_actual"):
        lines.extend([f"## {name}", "", json.dumps(metadata.get(name, {}), ensure_ascii=False), ""])
    lines.extend([
        "## 문항 계획",
        "",
        "| 번호 | 난이도 | 시대 | family_id | 정답 owner | 오답 owner | material | frame |",
        "|---:|---:|---|---|---|---|---|---:|",
    ])
    for index, item in enumerate(plan, 1):
        lines.append(
            f"| {index} | {item['difficulty']} | {item['era']} | {item['family_id']} | "
            f"{item.get('answer_owner_id', '-')} | {', '.join(item.get('distractor_owner_ids') or []) or '-'} | "
            f"{item.get('material_type', '')} | {item.get('frame_index', '')} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate reviewed closed-pack variants.")
    parser.add_argument("--pack-input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--variants-per-pack", type=int, default=1)
    parser.add_argument("--mock-exam", action="store_true")
    parser.add_argument("--official-data", type=Path)
    parser.add_argument("--image-pack-manifest", type=Path)
    parser.add_argument("--image-count", type=int, default=0)
    parser.add_argument("--image-only", action="store_true")
    parser.add_argument("--min-images", type=int, default=1)
    parser.add_argument("--max-images", type=int, default=3)
    parser.add_argument("--easy", type=int, default=10)
    parser.add_argument("--medium", type=int, default=30)
    parser.add_argument("--hard", type=int, default=10)
    parser.add_argument("--usage-manifest", type=Path)
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--eval-model", default="")
    parser.add_argument("--evaluation-repair-cycles", type=int, default=3)
    parser.add_argument("--repair-plan", type=Path)
    parser.add_argument("--max-total-calls", type=int, default=28)
    parser.add_argument("--max-seconds", type=int, default=600)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_repair_actions(path: Path | None, question_ids: list[str]) -> dict[str, str]:
    rows = read_json(path).get("items", []) if path else []
    if any(row.get("action") not in {"component_repair", "source_repair", "keep"} for row in rows):
        raise ValueError("repair plan contains an invalid action")
    actions = {str(row["question_id"]): str(row["action"]) for row in rows}
    if len(actions) != len(rows):
        raise ValueError("repair plan contains duplicate question_id")
    if set(actions) - set(question_ids):
        raise ValueError("repair plan contains unknown question_id")
    return actions


def completed_checkpoint(path: Path, variant_key: str) -> bool:
    if not path.is_file():
        return False
    state = read_json(path)
    return state.get("status") == "complete" and (state.get("input") or {}).get("variant_key") == variant_key


def exam_era_quotas(official_records: list[dict[str, Any]], totals: dict[int, int]) -> dict[int, dict[str, int]]:
    """공식 기출의 관측 시대 비율을 요청한 난이도별 문항 수에 배분한다."""
    distribution, _ = official_distribution(official_records)
    quotas = {score: apportion(distribution[score], total) for score, total in totals.items()}
    if any(total and not sum(quotas[score].values()) for score, total in totals.items()):
        raise ValueError("official data has no classified era distribution for a requested difficulty")
    return quotas


def select_exam_packs(
    packs: list[dict[str, Any]], era_quotas: dict[int, dict[str, int]], seed: int
) -> list[dict[str, Any]]:
    """난이도·시대 quota를 채우며 family가 겹치지 않는 pack을 고른다."""
    selected = []
    used_families: set[str] = set()
    shortages = []
    for score, quotas in era_quotas.items():
        for era in ERA_ORDER:
            requested = int(quotas.get(era, 0))
            candidates = [
                pack for pack in packs
                if int(pack.get("difficulty") or 0) == score
                and pack.get("era") == era
                and pack.get("family_id") not in used_families
            ]
            random.Random(f"{seed}:{score}:{era}").shuffle(candidates)
            chosen = candidates[:requested]
            selected.extend(chosen)
            used_families.update(str(pack["family_id"]) for pack in chosen)
            if len(chosen) != requested:
                shortages.append({"difficulty": score, "era": era, "requested": requested, "available": len(chosen)})
    if shortages:
        raise ValueError(f"insufficient unique closed packs for mock exam: {shortages}")
    selected.sort(key=lambda pack: (ERA_ORDER.index(pack["era"]), str(pack["family_id"])))
    return selected


def material_type_targets(ratios: dict[str, int], total: int) -> dict[str, int]:
    """설정 비율을 합계 ``total``인 결정론적 정수 quota로 바꾼다."""
    denominator = sum(ratios.values())
    if not denominator:
        raise ValueError("material type distribution is empty")
    raw = {name: weight * total / denominator for name, weight in ratios.items()}
    targets = {name: int(value) for name, value in raw.items()}
    ranked = sorted(ratios, key=lambda name: raw[name] - targets[name], reverse=True)
    for name in ranked[:total - sum(targets.values())]:
        targets[name] += 1
    return targets


def assign_material_frames(
    plan: list[dict[str, Any]], packs: list[dict[str, Any]], targets: dict[str, int], seed: int
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """실제 pack frame만 사용해 material type quota를 배정한다."""
    packs_by_family = {str(pack["family_id"]): pack for pack in packs}
    available = {
        index: {
            str(frame["material_type"]): frame_index
            for frame_index, frame in enumerate(packs_by_family[str(item["family_id"])]["question_frames"])
        }
        for index, item in enumerate(plan)
        if item.get("source_kind") == "closed"
    }
    assignments: dict[int, tuple[str, int]] = {}
    shortages = []
    order = sorted(targets, key=lambda name: (sum(name in values for values in available.values()), targets[name]))
    for material_type in order:
        candidates = [index for index, frames in available.items() if index not in assignments and material_type in frames]
        random.Random(f"{seed}:material:{material_type}").shuffle(candidates)
        chosen = candidates[:targets[material_type]]
        for index in chosen:
            assignments[index] = (material_type, available[index][material_type])
        if len(chosen) != targets[material_type]:
            shortages.append({"material_type": material_type, "requested": targets[material_type], "available": len(chosen)})
    for index, (material_type, frame_index) in assignments.items():
        frame = packs_by_family[str(plan[index]["family_id"])]["question_frames"][frame_index]
        plan[index]["frame_index"] = frame_index
        plan[index]["material_type"] = material_type
        plan[index]["stem_pattern"] = frame["stem_pattern"]
    return dict(Counter(
        item.get("material_type")
        for item in plan
        if item.get("source_kind") == "closed" and item.get("material_type")
    )), shortages


def replace_closed_with_images(
    plan: list[dict[str, Any]], images: list[dict[str, Any]], seed: int
) -> None:
    """난이도·시대 셀을 유지하며 closed 문항을 검수된 이미지 문항으로 치환한다."""
    for image in images:
        candidates = [
            index for index, item in enumerate(plan)
            if item.get("source_kind") == "closed"
            and item.get("difficulty") == image.get("difficulty")
            and item.get("era") == image.get("era")
        ]
        if not candidates:
            raise ValueError(f"no closed-plan cell for image pack: {image['family_id']}")
        random.Random(f"{seed}:image-replace:{image['family_id']}").shuffle(candidates)
        index = candidates[0]
        plan[index] = image


def load_image_packs(path: Path) -> list[dict[str, Any]]:
    """검수된 단일 이미지 generation pack 목록을 manifest에서 읽는다."""
    rows = read_json(path).get("packs") or []
    packs = []
    for row in rows:
        source = (path.parent / str(row.get("path") or "")).resolve()
        pack = read_json(source)
        score = target_score_from_difficulty(pack.get("difficulty_label"))
        era = str(row.get("era") or "")
        if (
            not source.is_file()
            or not (pack.get("image") or pack.get("choice_mode") == "image")
            or len(pack.get("items") or []) != 5
        ):
            raise ValueError(f"invalid image generation pack: {source}")
        if score not in (1, 2, 3) or era not in ERA_ORDER:
            raise ValueError(f"image pack requires valid difficulty and era: {source}")
        packs.append({
            "family_id": str(pack["pack_id"]),
            "difficulty": score,
            "era": era,
            "pack_input": str(source),
            "source_kind": "image",
            "variant_key": f"image:{pack['pack_id']}",
            "material_type": pack["material_type"],
            "stem_pattern": pack["stem_pattern"],
        })
    if len({row["family_id"] for row in packs}) != len(packs):
        raise ValueError("image pack manifest contains duplicate pack IDs")
    return packs


def select_image_packs(
    packs: list[dict[str, Any]], era_quotas: dict[int, dict[str, int]], minimum: int, maximum: int, seed: int
) -> list[dict[str, Any]]:
    """난이도·시대 quota를 유지하며 이미지 문항을 seed 기반으로 1~3개 고른다."""
    if minimum < 0 or maximum < minimum:
        raise ValueError("image count range is invalid")
    requested = random.Random(f"{seed}:image-count").randint(minimum, maximum)
    candidates = [row for row in packs if era_quotas[row["difficulty"]].get(row["era"], 0) > 0]
    random.Random(f"{seed}:image-packs").shuffle(candidates)
    selected = []
    remaining = {score: dict(quotas) for score, quotas in era_quotas.items()}
    for row in candidates:
        if remaining[row["difficulty"]].get(row["era"], 0) <= 0:
            continue
        selected.append(row)
        remaining[row["difficulty"]][row["era"]] -= 1
        if len(selected) == requested:
            return selected
    raise ValueError(f"insufficient image packs for requested quota: {len(selected)}/{requested}")


def load_used_keys(path: Path | None) -> set[str]:
    if not path or not path.exists():
        return set()
    data = read_json(path)
    return {str(value) for value in data.get("variant_keys", [])}


def write_used_keys(path: Path | None, keys: set[str]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {"variant_keys": sorted(keys)})


def write_json_atomic(path: Path, value: Any) -> None:
    """중단 시 기존 JSON을 보존하도록 같은 디렉터리의 임시 파일을 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    """복구 큐와 평가 결과 JSONL을 원자적으로 갱신한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def question_args(
    args: argparse.Namespace, item: dict[str, Any], output: Path, index: int, resume: bool = False
) -> list[str]:
    command = [
        "--pack-input", str(item.get("pack_input") or args.pack_input),
        "--output", str(output),
        "--seed", str(args.seed + index - 1),
        "--variant-key", str(item["variant_key"]),
        "--max-total-calls", str(getattr(args, "max_total_calls", 28)),
        "--max-seconds", str(getattr(args, "max_seconds", 600)),
    ]
    if item.get("source_kind") != "image":
        command.extend((
            "--family-id", item["family_id"],
            "--answer-owner-id", item["answer_owner_id"],
            "--frame-index", str(item["frame_index"]),
        ))
        for owner_id in item["distractor_owner_ids"]:
            command.extend(("--distractor-owner-id", owner_id))
    if resume:
        command.append("--resume")
    if args.dry_run:
        command.append("--dry-run")
    return command


def evaluate_questions(args: argparse.Namespace, questions: list[dict[str, Any]], cycle: int) -> list[dict[str, Any]]:
    evaluation_dir = args.output_dir / "evaluation"
    evaluation_dir.mkdir(exist_ok=True)
    input_path = evaluation_dir / f"cycle_{cycle}_input.json"
    prefix = evaluation_dir / f"cycle_{cycle}"
    input_path.write_text(json.dumps({"questions": questions}, ensure_ascii=False, indent=2), encoding="utf-8")
    command = [
        sys.executable, "-m", "question_generation.evaluation.v18",
        "--input", str(input_path),
        "--output-prefix", str(prefix),
    ]
    if args.eval_model:
        command.extend(("--model", args.eval_model))
    if args.resume:
        command.append("--resume")
        final = evaluation_dir / "final.jsonl"
        if cycle == 0 and final.exists():
            command.extend(("--resume-from", str(final)))
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        raise RuntimeError(f"v1.8 evaluation failed: cycle={cycle}, returncode={completed.returncode}")
    return read_jsonl(prefix.with_suffix(".jsonl"))


def next_repair_cycle(evaluation_dir: Path) -> int:
    cycles = [
        int(path.stem.split("_", 1)[1])
        for path in evaluation_dir.glob("cycle_*.jsonl")
        if path.stem.split("_", 1)[1].isdigit()
    ]
    return max(cycles, default=0) + 1


def prepare_evaluation_repair(path: Path, question: dict[str, Any], row: dict[str, Any]) -> list[str]:
    """평가기가 지목한 현재 체크포인트 컴포넌트만 재생성 가능 상태로 되돌린다."""
    feedback = evaluation_repair_feedback(question, row)
    if not feedback:
        return []
    state = read_json(path)
    if state.get("status") != "complete":
        return []
    invalidate(state, list(feedback), feedback, evaluation=True)
    state["status"] = "prepared"
    state["assembly_attempts"] = 0
    state.pop("error", None)
    write_json_atomic(path, state)
    return list(feedback)


def build_plan(args: argparse.Namespace, data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    packs = data.get("packs")
    if not isinstance(packs, list) or not packs:
        raise ValueError("closed-pack collection must contain a non-empty packs array")
    family_ids = [str(pack.get("family_id") or "") for pack in packs]
    if any(not family_id for family_id in family_ids) or len(set(family_ids)) != len(family_ids):
        raise ValueError("every closed pack must have a unique family_id")
    if args.variants_per_pack < 1:
        raise ValueError("variants-per-pack must be positive")
    if getattr(args, "image_only", False):
        if args.mock_exam or not args.image_pack_manifest or args.image_count < 1:
            raise ValueError("--image-only requires a positive --image-count and --image-pack-manifest")
        candidates = load_image_packs(args.image_pack_manifest)
        if args.image_count > len(candidates):
            raise ValueError(f"image-count is outside manifest capacity: {args.image_count}/{len(candidates)}")
        used_keys = load_used_keys(args.usage_manifest)
        candidates = [row for row in candidates if row["variant_key"] not in used_keys]
        random.Random(f"{args.seed}:image-only").shuffle(candidates)
        plan = candidates[:args.image_count]
        if len(plan) != args.image_count:
            raise ValueError(f"insufficient unused image packs: {len(plan)}/{args.image_count}")
        plan.sort(key=lambda row: (ERA_ORDER.index(row["era"]), str(row["family_id"])))
        return plan, {
            "image_count": len(plan),
            "image_families": [row["family_id"] for row in plan],
            "seed": args.seed,
        }

    metadata: dict[str, Any] = {}
    if args.mock_exam:
        if args.variants_per_pack != 1:
            raise ValueError("mock exam uses exactly one variant per family")
        if not args.official_data:
            raise ValueError("--official-data is required for a mock exam")
        totals = {1: args.easy, 2: args.medium, 3: args.hard}
        if any(value < 0 for value in totals.values()) or not sum(totals.values()):
            raise ValueError("mock-exam difficulty quotas must be non-negative and non-empty")
        official_records = read_json(args.official_data)
        era_quotas = exam_era_quotas(official_records, totals)
        _, unresolved = official_distribution(official_records)
        image_packs = select_image_packs(
            load_image_packs(args.image_pack_manifest), era_quotas, args.min_images, args.max_images, args.seed
        ) if args.image_pack_manifest else []
        remaining_quotas = {score: dict(quotas) for score, quotas in era_quotas.items()}
        for row in image_packs:
            remaining_quotas[row["difficulty"]][row["era"]] -= 1
        packs = select_exam_packs(packs, remaining_quotas, args.seed)
        metadata = {
            "difficulty_quotas": totals,
            "era_quotas": era_quotas,
            "official_unresolved": dict(unresolved),
            "image_count": len(image_packs),
            "image_families": [row["family_id"] for row in image_packs],
        }
    else:
        image_packs = []
        image_candidates = load_image_packs(args.image_pack_manifest) if args.image_pack_manifest else []
        if args.image_count < 0 or args.image_count > len(image_candidates):
            raise ValueError(f"image-count is outside manifest capacity: {args.image_count}/{len(image_candidates)}")
        if args.image_count and not image_candidates:
            raise ValueError("--image-count requires --image-pack-manifest")

    used_keys = load_used_keys(args.usage_manifest)
    plan = []
    for pack_index, pack in enumerate(packs):
        count = 1 if args.mock_exam else args.variants_per_pack
        for variant in plan_variants(pack, count, args.seed + pack_index, used_keys):
            used_keys.add(variant["variant_key"])
            plan.append({
                "family_id": pack["family_id"],
                "difficulty": pack["difficulty"],
                "era": pack["era"],
                "source_kind": "closed",
                **variant,
            })
    if args.mock_exam:
        plan.extend(image_packs)
    elif args.image_count:
        cell_counts = {
            score: dict(Counter(item["era"] for item in plan if item["difficulty"] == score))
            for score in (1, 2, 3)
        }
        image_packs = select_image_packs(image_candidates, cell_counts, args.image_count, args.image_count, args.seed)
        replace_closed_with_images(plan, image_packs, args.seed)
        metadata.update({
            "image_count": len(image_packs),
            "image_families": [row["family_id"] for row in image_packs],
        })
    closed_count = sum(item.get("source_kind") == "closed" for item in plan)
    if closed_count:
        tasks = {
            str(frame.get("question_task") or "")
            for pack in packs
            for frame in pack.get("question_frames") or []
        }
        if tasks == {"standard_select"}:
            ratios = (load_json_dict(DEFAULT_MATERIAL_PROMPT_RULES).get("_distribution") or {}).get("standard_select") or {}
            if not ratios:
                raise ValueError("material distribution is not configured for question_task=standard_select")
            targets = material_type_targets(ratios, closed_count)
            actual, shortages = assign_material_frames(plan, packs, targets, args.seed)
            metadata.update({
                "material_type_target": targets,
                "material_type_actual": actual,
                "material_type_shortage": shortages,
            })
        elif "standard_select" in tasks:
            raise ValueError(f"standard and fixed-choice packs must run in separate batches: {sorted(tasks)}")
    era_order = {era: index for index, era in enumerate(ERA_ORDER)}
    plan.sort(key=lambda row: (era_order.get(row["era"], len(era_order)), str(row["era"]), str(row["family_id"])))
    if len({row["variant_key"] for row in plan}) != len(plan):
        raise ValueError("duplicate variant in generation plan")
    if args.mock_exam and len({row["family_id"] for row in plan}) != len(plan):
        raise ValueError("mock exam contains duplicate family_id")
    metadata["seed"] = args.seed
    return plan, metadata


def main() -> int:
    args = parse_args()
    if args.evaluation_repair_cycles < 0:
        raise ValueError("evaluation-repair-cycles must be non-negative")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = args.output_dir / "plan.json"
    if args.resume and plan_path.exists():
        plan_data = read_json(plan_path)
        plan, metadata = plan_data["items"], plan_data.get("metadata", {})
        args.seed = int(metadata.get("seed", args.seed))
    else:
        plan, metadata = build_plan(args, read_json(args.pack_input))
    if metadata.get("material_type_shortage"):
        raise ValueError(f"insufficient compatible material frames: {metadata['material_type_shortage']}")
    if not (args.resume and plan_path.exists()):
        write_json_atomic(plan_path, {"metadata": metadata, "items": plan})
        (args.output_dir / "plan.md").write_text(render_plan_markdown(plan, metadata), encoding="utf-8")

    items_dir = args.output_dir / "items"
    items_dir.mkdir(exist_ok=True)
    results = []
    for index, item in enumerate(plan, 1):
        output = items_dir / f"{index:04d}.json"
        if args.resume and completed_checkpoint(output, str(item["variant_key"])):
            returncode = 0
        else:
            returncode = run_question(question_args(args, item, output, index, args.resume))
        results.append({"index": index, **item, "output": str(output), "returncode": returncode})

    summary = {
        "pack_input": str(args.pack_input),
        "requested": len(results),
        "succeeded": sum(result["returncode"] == 0 for result in results),
        "failed": sum(result["returncode"] != 0 for result in results),
        "metadata": metadata,
        "results": results,
    }
    if not args.dry_run:
        used_keys = load_used_keys(args.usage_manifest)
        used_keys.update(result["variant_key"] for result in results if result["returncode"] == 0)
        write_used_keys(args.usage_manifest, used_keys)

    questions: list[dict[str, Any]] = []
    if not args.dry_run:
        questions = [
            read_json(Path(result["output"]))["question"]
            for result in results
            if result["returncode"] == 0 and completed_checkpoint(Path(result["output"]), str(result["variant_key"]))
        ]
        if args.mock_exam and not summary["failed"]:
            initial = {"difficulty_quotas": metadata["difficulty_quotas"], "era_quotas": metadata["era_quotas"], "questions": questions}
            (args.output_dir / "mock_exam_initial_local_gate.json").write_text(
                json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (args.output_dir / "mock_exam_initial_local_gate.md").write_text(render_markdown(questions), encoding="utf-8")

    evaluation_failed = False
    if args.evaluate and questions:
        try:
            question_order = [str(question["seed_id"]) for question in questions]
            repair_actions = load_repair_actions(args.repair_plan, question_order)
            questions_by_id = {str(question["seed_id"]): question for question in questions}
            results_by_id = {
                str(read_json(Path(result["output"]))["question"]["seed_id"]): result
                for result in results
                if result["returncode"] == 0
                and completed_checkpoint(Path(result["output"]), str(result["variant_key"]))
            }
            evaluations = {row["question_id"]: row for row in evaluate_questions(args, questions, 0)}
            repair_log = []
            first_repair_cycle = next_repair_cycle(args.output_dir / "evaluation") if args.resume else 1
            for cycle in range(first_repair_cycle, first_repair_cycle + args.evaluation_repair_cycles):
                rejected = [
                    question_id for question_id in question_order
                    if question_id not in evaluations or not evaluation_accepted(evaluations[question_id])
                    if not repair_actions or repair_actions.get(question_id) == "component_repair"
                ]
                repaired_questions = []
                for question_id in rejected:
                    row = evaluations.get(question_id)
                    if not row:
                        continue
                    result = results_by_id[question_id]
                    output = Path(result["output"])
                    targets = prepare_evaluation_repair(output, questions_by_id[question_id], row)
                    if not targets:
                        continue
                    returncode = run_question(
                        question_args(args, result, output, int(result["index"]), resume=True)
                    )
                    repair_log.append({
                        "cycle": cycle,
                        "question_id": question_id,
                        "targets": targets,
                        "returncode": returncode,
                    })
                    if returncode:
                        continue
                    repaired = read_json(output).get("question")
                    if isinstance(repaired, dict):
                        questions_by_id[question_id] = repaired
                        repaired_questions.append(repaired)
                if not repaired_questions:
                    break
                evaluations.update({row["question_id"]: row for row in evaluate_questions(args, repaired_questions, cycle)})

            questions = [questions_by_id[question_id] for question_id in question_order]
            final_rows = [evaluations[question_id] for question_id in question_order if question_id in evaluations]
            rejected = [
                question_id for question_id in question_order
                if question_id not in evaluations or not evaluation_accepted(evaluations[question_id])
                if repair_actions.get(question_id) != "keep"
            ]
            evaluation_failed = bool(rejected)
            evaluation_dir = args.output_dir / "evaluation"
            write_jsonl_atomic(evaluation_dir / "final.jsonl", final_rows)
            queue_rows = []
            for question_id in rejected:
                result = results_by_id[question_id]
                row = evaluations.get(question_id) or {}
                queue_rows.append({
                    "status": "pending",
                    "question_id": question_id,
                    "checkpoint": result["output"],
                    "reason": repair_actions.get(question_id, "final_evaluation_rejected"),
                    "evaluation": row.get("parsed"),
                })
            write_jsonl_atomic(args.output_dir / "repair_queue.jsonl", queue_rows)
            summary["evaluation"] = {
                "status": "FAIL" if evaluation_failed else "PASS",
                "accepted": len(question_order) - len(rejected),
                "rejected": rejected,
                "repair_attempts": repair_log,
                "final": str(evaluation_dir / "final.jsonl"),
            }
        except (RuntimeError, ValueError, KeyError) as exc:
            evaluation_failed = True
            summary["evaluation"] = {"status": "ERROR", "error": str(exc)}
    elif args.evaluate and args.dry_run:
        summary["evaluation"] = {"status": "DRY_RUN"}
    elif args.evaluate:
        evaluation_failed = True
        summary["evaluation"] = {"status": "SKIPPED", "error": "generation did not produce a complete question set"}

    generation_queue = [
        {
            "status": "pending",
            "question_id": result["variant_key"],
            "checkpoint": result["output"],
            "reason": "generation_failed",
            "returncode": result["returncode"],
        }
        for result in results
        if result["returncode"] != 0
    ]
    if generation_queue:
        existing = read_jsonl(args.output_dir / "repair_queue.jsonl") if (args.output_dir / "repair_queue.jsonl").exists() else []
        write_jsonl_atomic(args.output_dir / "repair_queue.jsonl", [*existing, *generation_queue])

    if args.mock_exam and questions and (not args.evaluate or not evaluation_failed):
        exam = {"difficulty_quotas": metadata["difficulty_quotas"], "era_quotas": metadata["era_quotas"], "questions": questions}
        if args.evaluate:
            exam["final_evaluation_gate"] = "PASS"
        (args.output_dir / "mock_exam.json").write_text(json.dumps(exam, ensure_ascii=False, indent=2), encoding="utf-8")
        (args.output_dir / "mock_exam.md").write_text(render_markdown(questions), encoding="utf-8")

    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**summary, "summary": str(summary_path)}, ensure_ascii=False))
    return 0 if summary["failed"] == 0 and not evaluation_failed else 2


if __name__ == "__main__":
    raise SystemExit(main())

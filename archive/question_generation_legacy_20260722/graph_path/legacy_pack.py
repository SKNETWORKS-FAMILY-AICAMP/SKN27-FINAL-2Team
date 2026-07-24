"""토픽 선택부터 Graph/RAG 검색까지 수행하는 구형 실험용 pack 생성기.

현재 운영 경로인 ``question_pipeline``은 DB에 검증·저장된 basis pack을 입력으로 받는다.
이 파일은 GraphDB를 다시 실험할 때만 사용하며 ChoiceFact 배치에는 호출되지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever
from question_generation.graph_path.select_seed import (
    DEFAULT_TOPIC_POOL,
    build_seed_item,
    choose_schema,
    discover_schema_source,
    filter_schema,
    load_topics,
    load_type_schema,
    topic_key,
)
from question_generation.graph_path.fact_constraints import answer_fact_hint_sentences
from question_generation.graph_path.query_plan import retrieval_plan
from question_generation.graph_path.graph import (
    distractor_basis_status,
    graph_anchor_status,
    graph_anchor_year,
    graph_context,
    graph_driver_or_none,
    graph_topic_type_status,
    retrieve_distractor_basis,
    retrieve_encykorea_sources,
)
from question_generation.graph_path.material import (
    build_material_sources,
    fallback_identity_material,
    structured_timeline_material,
)
from question_generation.generation.material import generate_material
from question_generation.generation.material_rules import (
    choose_material_example,
    load_json_dict,
    load_material_examples,
    material_type_rules_text,
)
from question_generation.graph_path.material_validation import (
    answer_choice_viability_status,
    answer_fact_overlap_status,
    difficulty_generation_status,
    material_answer_leak_status,
    material_retry_feedback,
)
from question_generation.generation.material_validation import material_contract_status
from question_generation.graph_path.validation import material_source_status


OUTPUT_DIR = PROJECT_ROOT / "question_generation" / "outputs"
DEFAULT_OUTPUT = OUTPUT_DIR / "generation_pack_sample.json"
DEFAULT_SCHEMA_CACHE = OUTPUT_DIR / "sllm_type_schema_seed.csv"
DEFAULT_MATERIAL_EXAMPLES = OUTPUT_DIR / "material_type_examples_v41.json"
DEFAULT_MATERIAL_PROMPT_RULES = PROJECT_ROOT / "question_generation" / "material_type_prompt_rules.json"


def build_correct_choice_input(selection: dict[str, Any], material_obj: dict[str, Any]) -> dict[str, Any]:
    """격리된 Graph 실험 산출물의 구형 V41 입력 형태를 보존한다."""
    return {
        "task_type": "correct_choice_generation",
        "material": material_obj["material"],
        "answer_fact_basis": material_obj["answer_fact_basis"],
        **{
            key: selection[key]
            for key in (
                "topic_type", "topic", "material_type", "major_type", "minor_type",
                "question_task", "question_task_instruction", "difficulty_label",
            )
        },
    }


def parse_args() -> argparse.Namespace:
    """구형 seed·Graph·RAG·GPT pack 생성 옵션을 읽는다."""
    parser = argparse.ArgumentParser(description="Build pre-SLLM generation packs.")
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260706)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--topic-pool", type=Path, default=DEFAULT_TOPIC_POOL)
    parser.add_argument("--schema-source", type=Path, default=None)
    parser.add_argument("--schema-cache", type=Path, default=DEFAULT_SCHEMA_CACHE)
    parser.add_argument("--material-examples", type=Path, default=DEFAULT_MATERIAL_EXAMPLES)
    parser.add_argument("--material-prompt-rules", type=Path, default=DEFAULT_MATERIAL_PROMPT_RULES)
    parser.add_argument("--no-material-example", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--material-top-k", type=int, default=0)
    parser.add_argument("--distractor-target-count", type=int, default=4)
    parser.add_argument("--distractor-basis-top-k", type=int, default=0)
    parser.add_argument("--encykorea-api-key", default=os.getenv("ENCYKOREA_API_KEY", ""))
    parser.add_argument("--no-encykorea", action="store_true")
    parser.add_argument("--era", default=None)
    parser.add_argument("--difficulty", default=None)
    parser.add_argument("--major-type", default=None)
    parser.add_argument("--question-task", default=None)
    parser.add_argument("--material-type", default=None)
    parser.add_argument("--exclude-topics-file", type=Path, default=None)
    parser.add_argument("--allow-non-graph-topics", action="store_true")
    parser.add_argument("--schema-sampling", choices=("weighted", "uniform"), default="weighted")
    parser.add_argument("--seed-oversample-factor", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true", help="Skip OpenAI material generation.")
    return parser.parse_args()


def load_env() -> None:
    """프로젝트 .env를 구형 실행 경로에 로드한다."""
    load_dotenv(PROJECT_ROOT / ".env")



































































































































































































def select_seeds(args: argparse.Namespace) -> list[dict[str, Any]]:
    """토픽과 V41 스키마를 조합해 Graph/RAG 검색 전 seed 목록을 만든다."""
    rng = random.Random(args.seed)
    schema_source = args.schema_source or discover_schema_source()
    topics = load_topics(args.topic_pool, args.era, require_graph_anchor=False)
    schema_rows = filter_schema(load_type_schema(schema_source), args)
    selections: list[dict[str, Any]] = []
    attempts = max(args.n * 20, args.n)
    index = 1
    used_topics: set[str] = load_excluded_topic_keys(getattr(args, "exclude_topics_file", None))
    for _ in range(attempts):
        if len(selections) >= args.n:
            break
        topic = rng.choice(topics)
        key = topic_key(topic)
        if key in used_topics:
            continue
        topic_schema_rows = [row for row in schema_rows if row["topic_type"] == topic["topic_type"]]
        if not topic_schema_rows:
            continue
        seed = build_seed_item(index, topic, choose_schema(rng, topic_schema_rows, args.schema_sampling), args.seed)
        selections.append(
            {
                **seed["selection"],
                "seed_id": seed["seed_id"],
                "topic_source": seed["topic_source"],
                "schema_source": seed["schema_source"],
            }
        )
        used_topics.add(key)
        index += 1
    if len(selections) < args.n and not getattr(args, "allow_partial_selection", False):
        raise ValueError(f"Only selected {len(selections)} seeds for requested n={args.n}")
    return selections


def load_excluded_topic_keys(path: Path | None) -> set[str]:
    """이전 실행에서 사용한 topic 키를 읽어 중복 생성을 막는다."""
    if not path or not path.exists():
        return set()
    values = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(values, dict):
        values = [item.get("topic") for item in values.get("items", []) if isinstance(item, dict)]
    if not isinstance(values, list):
        return set()
    return {re.sub(r"\s+", "", str(value)) for value in values if str(value).strip()}


def main() -> None:
    """구형 seed 선택, Graph/RAG 검색, GPT 지문 생성을 순서대로 실행한다."""
    args = parse_args()
    load_env()
    if not args.encykorea_api_key:
        args.encykorea_api_key = os.getenv("ENCYKOREA_API_KEY", "")
    model = args.model or os.getenv("OPENAI_CHAT_MODEL", "gpt-5.4-mini")
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not args.dry_run and not api_key:
        raise RuntimeError("OPENAI_API_KEY is required unless --dry-run is set.")

    retriever = PgVectorHybridRetriever()
    graph_driver = graph_driver_or_none()
    material_examples = {} if args.no_material_example else load_material_examples(args.material_examples)
    material_prompt_rules = load_json_dict(args.material_prompt_rules)
    items: list[dict[str, Any]] = []
    skipped_seeds: list[dict[str, Any]] = []
    seed_args = argparse.Namespace(**vars(args))
    seed_args.n = max(args.n * args.seed_oversample_factor, args.n)
    seed_args.allow_partial_selection = True

    try:
        for selection in select_seeds(seed_args):
            if len(items) >= args.n:
                break
            context = graph_context(graph_driver, selection)
            anchor_status = graph_anchor_status(selection, context)
            if anchor_status["status"] != "ok":
                skipped_seeds.append(
                    {
                        "seed_id": selection["seed_id"],
                        "topic": selection["topic"],
                        "topic_type": selection["topic_type"],
                        "question_task": selection["question_task"],
                        "minor_type": selection["minor_type"],
                        "reason": "graph_anchor_mismatch",
                        "graph_anchor_status": anchor_status,
                    }
                )
                continue
            raw_encykorea_sources = (
                retrieve_encykorea_sources(selection["topic"], args.encykorea_api_key, args.timeout)
                if not args.no_encykorea and args.encykorea_api_key and not args.dry_run
                else []
            )
            if raw_encykorea_sources:
                context = graph_context(graph_driver, selection, raw_encykorea_sources)
                anchor_status = graph_anchor_status(selection, context)
                if anchor_status["status"] != "ok":
                    skipped_seeds.append(
                        {
                            "seed_id": selection["seed_id"],
                            "topic": selection["topic"],
                            "topic_type": selection["topic_type"],
                            "reason": "graph_anchor_source_mismatch",
                            "graph_anchor_status": anchor_status,
                        }
                    )
                    continue
            topic_type_status = graph_topic_type_status(selection, context)
            plan = retrieval_plan(selection)
            if plan.get("needs_timeline") and graph_anchor_year(context) is None:
                skipped_seeds.append(
                    {
                        "seed_id": selection["seed_id"],
                        "topic": selection["topic"],
                        "topic_type": selection["topic_type"],
                        "question_task": selection["question_task"],
                        "minor_type": selection["minor_type"],
                        "reason": "missing_graph_temporal_anchor",
                    }
                )
                continue
            required_clues = context.get("required_clues", [])
            material_sources = build_material_sources(
                driver=graph_driver,
                retriever=retriever,
                selection=selection,
                context=context,
                plan=plan,
                top_k=args.material_top_k,
                encykorea_api_key="" if args.no_encykorea else args.encykorea_api_key,
                timeout=args.timeout,
                encykorea_sources=raw_encykorea_sources,
            )
            aligned_encykorea_sources = [
                source for source in material_sources if source.get("source_type") == "encykorea_article"
            ]
            if not args.dry_run and not args.no_encykorea and args.encykorea_api_key and not aligned_encykorea_sources:
                skipped_seeds.append(
                    {
                        "seed_id": selection["seed_id"],
                        "topic": selection["topic"],
                        "topic_type": selection["topic_type"],
                        "reason": "missing_aligned_encykorea_source",
                        "graph_anchor_status": anchor_status,
                    }
                )
                continue
            material_sources_status = material_source_status(plan, material_sources)
            if not args.dry_run and material_sources_status["status"] != "ok":
                skipped_seeds.append(
                    {
                        "seed_id": selection["seed_id"],
                        "topic": selection["topic"],
                        "topic_type": selection["topic_type"],
                        "question_task": selection["question_task"],
                        "minor_type": selection["minor_type"],
                        "reason": "material_sources_need_review",
                        "material_source_status": material_sources_status,
                    }
                )
                continue
            structured_material = structured_timeline_material(material_sources, plan)
            if plan.get("intent") in {"period_between", "timeline_position", "timeline_order"} and not structured_material:
                skipped_seeds.append(
                    {
                        "seed_id": selection["seed_id"],
                        "topic": selection["topic"],
                        "topic_type": selection["topic_type"],
                        "question_task": selection["question_task"],
                        "minor_type": selection["minor_type"],
                        "reason": "missing_structured_timeline_material",
                    }
                )
                continue
            if not args.dry_run:
                precheck_targets, precheck_basis_list = retrieve_distractor_basis(
                    graph_driver,
                    retriever,
                    selection,
                    context,
                    args.distractor_target_count,
                    args.distractor_basis_top_k,
                )
                precheck_status = distractor_basis_status(
                    precheck_targets,
                    precheck_basis_list,
                    args.distractor_target_count,
                    selection,
                )
                if precheck_status["status"] != "ok":
                    skipped_seeds.append(
                        {
                            "seed_id": selection["seed_id"],
                            "topic": selection["topic"],
                            "topic_type": selection["topic_type"],
                            "question_task": selection["question_task"],
                            "minor_type": selection["minor_type"],
                            "reason": "distractor_basis_precheck_needs_review",
                            "distractor_basis_status": precheck_status,
                        }
                    )
                    continue
            answer_fact_hints = answer_fact_hint_sentences(selection, material_sources, plan, limit=1)
            material_obj = (
                structured_material or {"material": "", "answer_fact_basis": []}
                if args.dry_run
                else structured_material
                or generate_material(
                    selection=selection,
                    sources=material_sources,
                    material_example=choose_material_example(material_examples, selection, args.seed),
                    material_rules=material_type_rules_text(material_prompt_rules, selection["material_type"]),
                    model=model,
                    base_url=args.base_url,
                    api_key=api_key,
                    temperature=args.temperature,
                    timeout=args.timeout,
                    max_retries=args.max_retries,
                    answer_fact_hints=answer_fact_hints,
                )
            )
            answer_fact_status = answer_fact_overlap_status(
                material_obj["material"],
                material_obj["answer_fact_basis"],
                selection["topic"],
            )
            material_leak_status = material_answer_leak_status(selection, material_obj["material"], plan)
            material_contract = material_contract_status(selection, material_obj["material"], plan)
            difficulty_status = difficulty_generation_status(
                selection,
                material_obj["material"],
                material_obj["answer_fact_basis"],
                plan,
            )
            if not args.dry_run and (
                material_leak_status["status"] != "ok"
                or material_contract["status"] != "ok"
            ):
                fallback_material = fallback_identity_material(selection, context, plan)
                if fallback_material:
                    material_obj["material"] = fallback_material
                    material_leak_status = material_answer_leak_status(selection, material_obj["material"], plan)
                    material_contract = material_contract_status(selection, material_obj["material"], plan)
                    difficulty_status = difficulty_generation_status(
                        selection,
                        material_obj["material"],
                        material_obj["answer_fact_basis"],
                        plan,
                    )
            if not args.dry_run and answer_fact_status["status"] != "ok":
                material_obj = generate_material(
                    selection=selection,
                    sources=material_sources,
                    material_example=choose_material_example(material_examples, selection, args.seed),
                    material_rules=material_type_rules_text(material_prompt_rules, selection["material_type"]),
                    model=model,
                    base_url=args.base_url,
                    api_key=api_key,
                    temperature=args.temperature,
                    timeout=args.timeout,
                    max_retries=args.max_retries,
                    answer_fact_hints=answer_fact_hints,
                )
                answer_fact_status = answer_fact_overlap_status(
                    material_obj["material"],
                    material_obj["answer_fact_basis"],
                    selection["topic"],
                )
                material_leak_status = material_answer_leak_status(selection, material_obj["material"], plan)
                material_contract = material_contract_status(selection, material_obj["material"], plan)
                difficulty_status = difficulty_generation_status(
                    selection,
                    material_obj["material"],
                    material_obj["answer_fact_basis"],
                    plan,
                )
            if not args.dry_run and answer_fact_status["status"] != "ok":
                fallback_material = fallback_identity_material(selection, context, plan)
                if fallback_material:
                    material_obj["material"] = fallback_material
                    answer_fact_status = answer_fact_overlap_status(
                        material_obj["material"],
                        material_obj["answer_fact_basis"],
                        selection["topic"],
                    )
                    material_leak_status = material_answer_leak_status(selection, material_obj["material"], plan)
                    material_contract = material_contract_status(selection, material_obj["material"], plan)
                    difficulty_status = difficulty_generation_status(
                        selection,
                        material_obj["material"],
                        material_obj["answer_fact_basis"],
                        plan,
                    )
            if not args.dry_run and (
                answer_fact_status["status"] == "ok"
                and (
                    material_leak_status["status"] != "ok"
                    or material_contract["status"] != "ok"
                    or difficulty_status["status"] != "ok"
                )
            ):
                material_obj = generate_material(
                    selection=selection,
                    sources=material_sources,
                    material_example=choose_material_example(material_examples, selection, args.seed),
                    material_rules=material_type_rules_text(material_prompt_rules, selection["material_type"]),
                    model=model,
                    base_url=args.base_url,
                    api_key=api_key,
                    temperature=args.temperature,
                    timeout=args.timeout,
                    max_retries=args.max_retries,
                    answer_fact_hints=answer_fact_hints,
                    retry_feedback=material_retry_feedback(material_contract, material_leak_status, difficulty_status, selection),
                )
                answer_fact_status = answer_fact_overlap_status(
                    material_obj["material"],
                    material_obj["answer_fact_basis"],
                    selection["topic"],
                )
                material_leak_status = material_answer_leak_status(selection, material_obj["material"], plan)
                material_contract = material_contract_status(selection, material_obj["material"], plan)
                difficulty_status = difficulty_generation_status(
                    selection,
                    material_obj["material"],
                    material_obj["answer_fact_basis"],
                    plan,
                )
            if not args.dry_run and (
                answer_fact_status["status"] != "ok"
                or material_leak_status["status"] != "ok"
                or material_contract["status"] != "ok"
                or difficulty_status["status"] != "ok"
            ):
                skipped_seeds.append(
                    {
                        "seed_id": selection["seed_id"],
                        "topic": selection["topic"],
                        "topic_type": selection["topic_type"],
                        "question_task": selection["question_task"],
                        "minor_type": selection["minor_type"],
                        "reason": "material_answer_basis_needs_review",
                        "material": material_obj["material"],
                        "answer_fact_basis": material_obj["answer_fact_basis"],
                        "answer_fact_hints": answer_fact_hints,
                        "answer_fact_status": answer_fact_status,
                        "material_leak_status": material_leak_status,
                        "material_contract_status": material_contract,
                        "difficulty_generation_status": difficulty_status,
                    }
                )
                continue
            answer_choice_status = answer_choice_viability_status(
                material_obj["material"],
                material_obj["answer_fact_basis"],
                selection["topic"],
            )
            if not args.dry_run and answer_choice_status["status"] != "ok":
                skipped_seeds.append(
                    {
                        "seed_id": selection["seed_id"],
                        "topic": selection["topic"],
                        "topic_type": selection["topic_type"],
                        "question_task": selection["question_task"],
                        "minor_type": selection["minor_type"],
                        "reason": "answer_choice_basis_needs_review",
                        "answer_choice_status": answer_choice_status,
                    }
                )
                continue
            distractor_selection = {
                **selection,
                "material": material_obj["material"],
                "answer_fact_basis": material_obj["answer_fact_basis"],
            }
            distractor_targets, distractor_fact_basis_list = retrieve_distractor_basis(
                graph_driver,
                retriever,
                distractor_selection,
                context,
                args.distractor_target_count,
                args.distractor_basis_top_k,
                "" if args.no_encykorea else args.encykorea_api_key,
                args.timeout,
            )
            distractor_status = distractor_basis_status(
                distractor_targets,
                distractor_fact_basis_list,
                args.distractor_target_count,
                distractor_selection,
            )
            if not args.dry_run and distractor_status["status"] != "ok":
                skipped_seeds.append(
                    {
                        "seed_id": selection["seed_id"],
                        "topic": selection["topic"],
                        "topic_type": selection["topic_type"],
                        "question_task": selection["question_task"],
                        "minor_type": selection["minor_type"],
                        "reason": "distractor_basis_needs_review",
                        "distractor_basis_status": distractor_status,
                    }
                )
                continue
            if not material_obj["answer_fact_basis"]:
                answer_fact_status = {"status": "needs_review", "overlap_terms": [], "errors": ["missing_answer_fact_basis"]}

            items.append(
                {
                    "seed_id": selection["seed_id"],
                    "topic": selection["topic"],
                    "topic_type": selection["topic_type"],
                    "era": selection["era"],
                    "material_type": selection["material_type"],
                    "major_type": selection["major_type"],
                    "minor_type": selection["minor_type"],
                    "question_task": selection["question_task"],
                    "question_task_instruction": selection["question_task_instruction"],
                    "difficulty_label": selection["difficulty_label"],
                    "topic_source": selection["topic_source"],
                    "graph_anchor_status": anchor_status,
                    "topic_type_status": topic_type_status,
                    "retrieval_plan": plan,
                    "graph_context": context,
                    "answer_fact_hints": answer_fact_hints,
                    "required_clues": required_clues,
                    "material_sources": material_sources,
                    "material_source_status": material_sources_status,
                    "material": material_obj["material"],
                    "answer_fact_basis": material_obj["answer_fact_basis"],
                    "answer_fact_status": answer_fact_status,
                    "answer_choice_status": answer_choice_status,
                    "material_leak_status": material_leak_status,
                    "material_contract_status": material_contract,
                    "difficulty_generation_status": difficulty_status,
                    "distractor_targets": distractor_targets,
                    "distractor_fact_basis_list": distractor_fact_basis_list,
                    "distractor_basis_status": distractor_status,
                    "correct_choice_input": build_correct_choice_input(selection, material_obj),
                }
            )
    finally:
        if graph_driver is not None:
            graph_driver.close()

    build_error = "" if len(items) >= args.n else f"Only built {len(items)} items for requested n={args.n}; skipped={len(skipped_seeds)}"
    output = {
        "schema_version": "generation_pack_v1",
        "model": model if not args.dry_run else "",
        "dry_run": args.dry_run,
        "requested_count": args.n,
        "build_status": "ok" if not build_error else "needs_more_items",
        "build_error": build_error,
        "skipped_seed_count": len(skipped_seeds),
        "skipped_seeds": skipped_seeds[:50],
        "items": items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

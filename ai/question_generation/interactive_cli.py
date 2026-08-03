"""현행 closed-pack 모의고사 생성기를 실행하는 대화형 CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase

from ai.pack_generation.graph_builder import (
    candidate_hops_for_difficulty,
    read_graph_candidates,
    validate_spec,
)
from ai.question_generation.core.contracts import V41_TOPIC_TYPES
from ai.question_generation.core.exam_distribution import ERA_ORDER
from ai.question_generation.generation.material import chat_json
from ai.question_generation.retrieval.closed_pack_bank import AXIS_STEMS
from ai.question_generation.retrieval.closed_pack_input import MATERIAL_TARGET_SCOPE
from storage.fact_neo4j.load_fact_graph import load_connection_config
from storage.postgresql.connection import connect_db

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_OPTIONS_QUERY = """
MATCH (owner:CanonicalEntity)-[:IN_ERA]->(era:Era)
MATCH (owner)-[:HAS_TOPIC]->(topic:Topic)
MATCH (:SourceRecord {source: 'AKS'})-[:RESOLVES_TO]->(owner)
WHERE owner.retrieval_eligible = true
WITH era, topic, owner.entity_type AS owner_type,
     count(DISTINCT owner) AS owner_count
WHERE owner_count >= 9
RETURN era.era_id AS era_id, era.name AS era_name,
       topic.topic_id AS topic_id, topic.name AS topic_name,
       owner_type, owner_count
ORDER BY era_name, topic_name, owner_type
"""


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip().lstrip("\ufeff").strip('"') or default


def yes(prompt: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{marker}] ").strip().lower()
    return default if not value else value in {"y", "yes", "ㅇ"}


def path_value(prompt: str, default: Path | None = None, *, must_exist: bool = False) -> Path:
    while True:
        value = ask(prompt, str(default or ""))
        if not value:
            print("경로를 입력해 주세요.")
            continue
        path = Path(value).expanduser().resolve()
        if not must_exist or path.exists():
            return path
        print(f"파일을 찾을 수 없습니다: {path}")


def latest_run(run_dir: Path) -> Path | None:
    runs = [path for path in run_dir.iterdir() if path.is_dir() and (path / "plan.json").is_file()]
    return max(runs, key=lambda path: (path / "plan.json").stat().st_mtime, default=None)


def number(prompt: str, default: int) -> int:
    while True:
        try:
            value = int(ask(prompt, str(default)))
            if value >= 0:
                return value
        except ValueError:
            pass
        print("0 이상의 정수를 입력해 주세요.")


def positive_number(prompt: str, default: int) -> int:
    while True:
        value = number(prompt, default)
        if value:
            return value
        print("1 이상의 정수를 입력해 주세요.")


def choose(prompt: str, options: list[dict[str, Any]], label) -> dict[str, Any]:
    for index, option in enumerate(options, 1):
        print(f"{index}. {label(option)}")
    while True:
        selected = number(prompt, 1)
        if 1 <= selected <= len(options):
            return options[selected - 1]
        print(f"1~{len(options)} 사이 번호를 선택해 주세요.")


def graph_options() -> list[dict[str, Any]]:
    config = load_connection_config(PROJECT_ROOT)
    with GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"])) as driver:
        return [dict(record) for record in driver.execute_query(GRAPH_OPTIONS_QUERY, routing_="r").records]


def v41_generation_contracts(path: Path) -> dict[str, list[dict[str, str]]]:
    distractors = set()
    instructions = set()
    decoder = json.JSONDecoder()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        record = json.loads(line)
        prompt = str(record.get("prompt") or "")
        start = prompt.find("{")
        if start < 0:
            continue
        try:
            payload, _ = decoder.raw_decode(prompt[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("distractor_type"):
            distractors.add(
                (
                    str(payload.get("topic_type") or ""),
                    str(payload.get("material_type") or ""),
                    str(payload.get("major_type") or ""),
                    str(payload.get("minor_type") or ""),
                    str(payload["distractor_type"]),
                )
            )
        if isinstance(payload, dict) and payload.get("question_task_instruction"):
            instructions.add(
                (
                    str(payload.get("topic_type") or ""),
                    str(payload.get("question_task") or ""),
                    str(payload.get("material_type") or ""),
                    str(payload.get("major_type") or ""),
                    str(payload.get("minor_type") or ""),
                    str(payload["question_task_instruction"]),
                )
            )
    if not distractors or not instructions:
        raise ValueError("V41 validation에서 생성 계약을 읽지 못했습니다.")
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


def planning_contracts(
    article_ids: list[str],
    generation_contracts: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    conn = connect_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT era FROM questions WHERE NULLIF(BTRIM(era), '') IS NOT NULL ORDER BY era")
            service_eras = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT DISTINCT topic FROM questions WHERE NULLIF(BTRIM(topic), '') IS NOT NULL ORDER BY topic")
            service_topics = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT DISTINCT SPLIT_PART(contents_type, '/', 1)
                FROM rag.encykorea_articles
                WHERE article_id = ANY(%s) AND NULLIF(BTRIM(contents_type), '') IS NOT NULL
                ORDER BY 1
                """,
                (article_ids,),
            )
            rag_owner_types = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT pack.relation_axis_id, pack.topic_type, pack.stem_pattern,
                       pack.material_type, pack.major_type, pack.minor_type,
                       COUNT(*) AS usage_count
                FROM qgen.choice_facts fact
                JOIN qgen.choice_fact_sources source USING (choice_fact_id)
                JOIN qgen.basis_items item ON item.basis_item_id = source.basis_item_id
                JOIN qgen.basis_packs pack ON pack.pack_id = item.pack_id
                WHERE fact.article_id = ANY(%s)
                  AND pack.status = 'rag_ready'
                  AND pack.semantic_status = 'pass'
                  AND pack.question_task = 'standard_select'
                GROUP BY pack.relation_axis_id, pack.topic_type, pack.stem_pattern,
                         pack.material_type, pack.major_type, pack.minor_type
                ORDER BY usage_count DESC
                """,
                (article_ids,),
            )
            rows = [
                {
                    "relation_axis_id": row[0],
                    "topic_type": row[1],
                    "stem_pattern": row[2],
                    "material_type": row[3],
                    "major_type": row[4],
                    "minor_type": row[5],
                    "usage_count": row[6],
                }
                for row in cursor.fetchall()
                if row[1] in V41_TOPIC_TYPES
                and row[2] in AXIS_STEMS.get(row[0], set())
                and row[3] != "시각 자료 설명"
            ]
    finally:
        conn.close()

    viable_profiles = {
        profile
        for profile in {(row["relation_axis_id"], row["topic_type"]) for row in rows}
        if len({(
            row["stem_pattern"],
            row["material_type"],
            row["major_type"],
            row["minor_type"],
        ) for row in rows if (row["relation_axis_id"], row["topic_type"]) == profile}) >= 2
    }
    frame_contracts = [
        {**row, "contract_index": index}
        for index, row in enumerate(
            (
                row
                for row in rows
                if (row["relation_axis_id"], row["topic_type"]) in viable_profiles
            ),
            1,
        )
    ]
    if not all(
        (
            service_eras,
            service_topics,
            rag_owner_types,
            frame_contracts,
            generation_contracts.get("distractors"),
            generation_contracts.get("instructions"),
        )
    ):
        raise ValueError("선택한 Graph 후보에서 안전한 출제 계약을 구성할 수 없습니다.")
    return {
        "service_eras": service_eras,
        "service_topics": service_topics,
        "rag_owner_types": rag_owner_types,
        "frame_contracts": frame_contracts,
        "distractor_contracts": generation_contracts["distractors"],
        "instruction_contracts": generation_contracts["instructions"],
    }


def build_planned_spec(
    selection: dict[str, Any],
    contracts: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    relation_axis_id = str(plan.get("relation_axis_id") or "")
    requested_frames = plan.get("question_frames")
    if plan.get("era") not in ERA_ORDER:
        raise ValueError("spec planner가 허용되지 않은 대시대를 반환했습니다.")
    if plan.get("service_era") not in contracts["service_eras"]:
        raise ValueError("spec planner가 허용되지 않은 서비스 시대를 반환했습니다.")
    if selection.get("topic_name") not in contracts["service_topics"]:
        raise ValueError("Graph 주제가 서비스 주제 계약에 없습니다.")
    if plan.get("rag_owner_type") not in contracts["rag_owner_types"]:
        raise ValueError("spec planner가 후보와 다른 RAG owner 유형을 반환했습니다.")
    if plan.get("topic_type") not in V41_TOPIC_TYPES:
        raise ValueError("spec planner가 허용되지 않은 V41 topic_type을 반환했습니다.")
    if not isinstance(requested_frames, list) or len(requested_frames) != 2:
        raise ValueError("spec planner는 정확히 두 개의 question frame을 반환해야 합니다.")

    by_index = {row["contract_index"]: row for row in contracts["frame_contracts"]}
    frames = []
    used_indices = set()
    for requested in requested_frames:
        index = requested.get("contract_index")
        contract = by_index.get(index)
        if (
            not contract
            or index in used_indices
            or contract["relation_axis_id"] != relation_axis_id
            or contract["topic_type"] != plan["topic_type"]
        ):
            raise ValueError("spec planner가 선택한 frame 계약이 관계축과 일치하지 않습니다.")
        instruction = str(requested.get("question_task_instruction") or "").strip()
        distractor_type = str(requested.get("distractor_type") or "").strip()
        allowed_distractor_types = distractor_types_for_frame(plan, contract, contracts)
        allowed_instructions = instructions_for_frame(plan, contract, contracts)
        if instruction not in allowed_instructions or distractor_type not in allowed_distractor_types:
            raise ValueError("spec planner frame에 출제 지시 또는 distractor_type이 없습니다.")
        used_indices.add(index)
        frames.append(
            {
                "question_task": "standard_select",
                **{
                    key: contract[key]
                    for key in ("stem_pattern", "material_type", "major_type", "minor_type")
                },
                "answer_owner_scope": MATERIAL_TARGET_SCOPE,
                "question_task_instruction": instruction,
                "distractor_type": distractor_type,
            }
        )

    spec = {
        "anchor_node_id": selection["topic_id"],
        "candidate_hops": candidate_hops_for_difficulty(int(selection["difficulty"])),
        "topic_id": selection["topic_id"],
        "era_id": selection["era_id"],
        "era": plan["era"],
        "service_era": plan["service_era"],
        "era_criteria": str(plan.get("era_criteria") or "").strip(),
        "owner_type": selection["owner_type"],
        "rag_owner_type": plan["rag_owner_type"],
        "relation_axis_id": relation_axis_id,
        "topic_type": plan["topic_type"],
        "service_topic": selection["topic_name"],
        "difficulty": selection["difficulty"],
        "question_frames": frames,
    }
    validate_spec(spec)
    return spec


def distractor_types_for_frame(
    plan: dict[str, Any],
    contract: dict[str, Any],
    contracts: dict[str, Any],
) -> set[str]:
    return {
        row["distractor_type"]
        for row in contracts["distractor_contracts"]
        if row["topic_type"] == plan.get("topic_type")
        and all(
            row[key] == contract[key]
            for key in ("material_type", "major_type", "minor_type")
        )
    }


def instructions_for_frame(
    plan: dict[str, Any],
    contract: dict[str, Any],
    contracts: dict[str, Any],
) -> set[str]:
    return {
        row["question_task_instruction"]
        for row in contracts["instruction_contracts"]
        if row["topic_type"] == plan.get("topic_type")
        and row["question_task"] == "standard_select"
        and all(
            row[key] == contract[key]
            for key in ("material_type", "major_type", "minor_type")
        )
    }


def plan_pack_spec(
    selection: dict[str, Any],
    contracts: dict[str, Any],
    *,
    model: str,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    plan = chat_json(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=0,
        timeout=180,
        max_retries=1,
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 한국사 closed-pack의 출제 계약 설계자다. "
                    "역사 사실이나 후보를 만들지 말고 제공된 허용값만 사용해 JSON 객체만 출력한다."
                ),
            },
            {
                "role": "user",
                "content": f"""
사람이 선택한 Graph 조건:
{json.dumps(selection, ensure_ascii=False)}

허용 대시대: {json.dumps(ERA_ORDER, ensure_ascii=False)}
허용 서비스 시대: {json.dumps(contracts["service_eras"], ensure_ascii=False)}
선택한 서비스 주제: {selection["topic_name"]}
허용 V41 topic_type: {json.dumps(sorted(V41_TOPIC_TYPES), ensure_ascii=False)}
후보에서 확인된 RAG owner 유형: {json.dumps(contracts["rag_owner_types"], ensure_ascii=False)}
현재 qgen DB에서 검증된 frame 계약:
{json.dumps(contracts["frame_contracts"], ensure_ascii=False)}

규칙:
- 선택한 시대·주제·owner 유형에 가장 직접적으로 맞는 관계축 하나를 고른다.
- 같은 relation_axis_id에 속한 서로 다른 contract_index 두 개만 고른다.
- 시대·topic_type·RAG owner 유형은 반드시 위 허용값 중 하나를 그대로 쓴다.
- topic_type은 선택한 두 frame 계약의 topic_type과 같아야 한다.

출력:
{{
  "era": "허용 대시대",
  "service_era": "허용 서비스 시대",
  "era_criteria": "선택 시대에 포함할 사실의 판정 기준",
  "rag_owner_type": "허용 RAG owner 유형",
  "relation_axis_id": "frame 계약에 존재하는 관계축",
  "topic_type": "허용 V41 topic_type",
  "question_frames": [
    {{"contract_index": 1}},
    {{"contract_index": 2}}
  ]
}}
""".strip(),
            },
        ],
    )
    return plan


def plan_graph_pack_spec(
    options: list[dict[str, Any]],
    generation_contracts: dict[str, list[dict[str, str]]],
    model: str,
) -> dict[str, Any] | None:
    difficulty = choose(
        "난이도",
        [{"value": 1, "label": "1점"}, {"value": 2, "label": "2점"}, {"value": 3, "label": "3점"}],
        lambda row: row["label"],
    )["value"]
    eras = sorted(
        {row["era_id"]: {"era_id": row["era_id"], "era_name": row["era_name"]} for row in options}.values(),
        key=lambda row: row["era_name"],
    )
    era = choose("시대", eras, lambda row: row["era_name"])
    era_rows = [row for row in options if row["era_id"] == era["era_id"]]
    topics = sorted(
        {row["topic_id"]: {"topic_id": row["topic_id"], "topic_name": row["topic_name"]} for row in era_rows}.values(),
        key=lambda row: row["topic_name"],
    )
    topic = choose("주제", topics, lambda row: row["topic_name"])
    topic_rows = [row for row in era_rows if row["topic_id"] == topic["topic_id"]]
    owner = choose(
        "owner 유형",
        topic_rows,
        lambda row: f"{row['owner_type']} (직접 연결 owner {row['owner_count']}개)",
    )
    selection = {
        **era,
        **topic,
        "anchor_node_id": topic["topic_id"],
        "candidate_hops": candidate_hops_for_difficulty(difficulty),
        "owner_type": owner["owner_type"],
        "difficulty": difficulty,
    }
    candidates = read_graph_candidates(selection)
    if len(candidates) < 9:
        raise ValueError(
            f"선택 조건의 {selection['candidate_hops']}홉 Graph+AKS 후보가 "
            f"{len(candidates)}개뿐입니다."
        )
    selection["candidate_count"] = len(candidates)
    contracts = planning_contracts(
        [row["article_id"] for row in candidates],
        generation_contracts,
    )
    if not yes(f"Graph+AKS 후보 {len(candidates)}개를 확인했습니다. spec 계획 LLM 1회를 호출할까요?"):
        return None
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or not model:
        raise ValueError("OPENAI_API_KEY와 OPENAI_PACK_MODEL 또는 OPENAI_CHAT_MODEL이 필요합니다.")
    plan = plan_pack_spec(
        selection,
        contracts,
        model=model,
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        api_key=api_key,
    )
    by_index = {row["contract_index"]: row for row in contracts["frame_contracts"]}
    for frame in plan.get("question_frames") or []:
        contract = by_index.get(frame.get("contract_index"))
        if (
            not contract
            or contract["relation_axis_id"] != plan.get("relation_axis_id")
            or contract["topic_type"] != plan.get("topic_type")
        ):
            raise ValueError("spec planner가 선택한 frame 계약이 관계축과 일치하지 않습니다.")
        allowed = sorted(distractor_types_for_frame(plan, contract, contracts))
        if not allowed:
            raise ValueError("선택한 frame과 일치하는 V41 distractor_type이 없습니다.")
        instructions = sorted(instructions_for_frame(plan, contract, contracts))
        if not instructions:
            raise ValueError("선택한 frame과 일치하는 V41 question_task_instruction이 없습니다.")
        frame["question_task_instruction"] = choose(
            f"{contract['stem_pattern']} 출제 지시",
            [{"value": value} for value in instructions],
            lambda row: row["value"],
        )["value"]
        frame["distractor_type"] = choose(
            f"{contract['stem_pattern']} 오답 유형",
            [{"value": value} for value in allowed],
            lambda row: row["value"],
        )["value"]
    spec = build_planned_spec(selection, contracts, plan)
    print("\n=== LLM이 계획한 spec ===")
    print(json.dumps(spec, ensure_ascii=False, indent=2))
    return spec if yes("이 spec을 pack 생성 목록에 추가할까요?") else None


def create_graph_packs(
    run_dir: Path,
    existing_bank: Path | None,
    model: str,
    v41_validation: Path,
    count: int,
) -> Path | None:
    options = graph_options()
    generation_contracts = v41_generation_contracts(v41_validation)
    specs = []
    identities = set()
    for index in range(1, count + 1):
        print(f"\n=== Graph pack {index}/{count} 계획 ===")
        spec = plan_graph_pack_spec(options, generation_contracts, model)
        if spec is None:
            return None
        identity = (
            spec["anchor_node_id"],
            spec["candidate_hops"],
            spec["era_id"],
            spec["owner_type"],
            spec["relation_axis_id"],
            spec["topic_type"],
            tuple(
                (frame["stem_pattern"], frame["material_type"], frame["major_type"], frame["minor_type"])
                for frame in spec["question_frames"]
            ),
        )
        if identity in identities:
            raise ValueError("같은 spec을 두 번 선택했습니다.")
        identities.add(identity)
        specs.append(spec)
    if not yes(f"{count}개 pack의 후보 의미 검수 LLM을 실행할까요?"):
        return None
    output_dir = run_dir / f"graph_pack_{datetime.now():%Y%m%d_%H%M%S}"
    spec_path = output_dir / "spec.json"
    output_path = output_dir / "pack_bank.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps({"packs": specs}, ensure_ascii=False, indent=2), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "ai.pack_generation.graph_builder",
        "--spec",
        str(spec_path),
        "--output",
        str(output_path),
    ]
    if existing_bank:
        command.extend(("--existing-bank", str(existing_bank)))
    print(f"\n[실행] {subprocess.list2cmdline(command)}\n")
    code = subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode
    print(f"\n종료 코드: {code}\n출력: {output_path}")
    return output_path if code == 0 else None


def create_chronology_packs(run_dir: Path, plan: Path) -> Path | None:
    output_dir = run_dir / f"chronology_pack_{datetime.now():%Y%m%d_%H%M%S}"
    output = output_dir / "pack_bank.json"
    report = output_dir / "report.json"
    command = [
        sys.executable,
        "-m",
        "ai.pack_generation.build_chronology_packs",
        "--plan",
        str(plan),
        "--output",
        str(output),
        "--report",
        str(report),
    ]
    print(f"\n[실행] {subprocess.list2cmdline(command)}\n")
    code = subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode
    print(f"\n종료 코드: {code}\n출력: {output}\n검증 보고서: {report}")
    return output if code == 0 else None


def create_image_pack_manifest(run_dir: Path, reviewed_packs: Path) -> Path | None:
    output_dir = run_dir / f"image_pack_{datetime.now():%Y%m%d_%H%M%S}"
    manifest = output_dir / "image_generation_pack_manifest.json"
    command = [
        sys.executable,
        "-m",
        "ai.question_generation.retrieval.image_pack_input",
        "--input",
        str(reviewed_packs),
        "--output-dir",
        str(output_dir),
    ]
    print(f"\n[실행] {subprocess.list2cmdline(command)}\n")
    code = subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode
    print(f"\n종료 코드: {code}\nmanifest: {manifest}")
    return manifest if code == 0 else None


def run_pack_variants(
    pack_input: Path,
    output_dir: Path,
    usage_manifest: Path,
    variants_per_pack: int,
    seed: int,
    *,
    evaluate: bool,
    dry_run: bool,
) -> int:
    command = [
        sys.executable,
        "-m",
        "ai.question_generation.workflows.closed_pack_batch",
        "--pack-input",
        str(pack_input),
        "--output-dir",
        str(output_dir),
        "--usage-manifest",
        str(usage_manifest),
        "--variants-per-pack",
        str(variants_per_pack),
        "--seed",
        str(seed),
    ]
    if evaluate:
        command.append("--evaluate")
    if dry_run:
        command.append("--dry-run")
    print(f"\n[실행] {subprocess.list2cmdline(command)}\n")
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode


def run_image_packs(
    reviewed_packs: Path,
    manifest: Path,
    output_dir: Path,
    usage_manifest: Path,
    count: int,
    seed: int,
    *,
    evaluate: bool,
    dry_run: bool,
) -> int:
    command = [
        sys.executable,
        "-m",
        "ai.question_generation.workflows.closed_pack_batch",
        "--pack-input",
        str(reviewed_packs),
        "--image-pack-manifest",
        str(manifest),
        "--image-count",
        str(count),
        "--image-only",
        "--output-dir",
        str(output_dir),
        "--usage-manifest",
        str(usage_manifest),
        "--seed",
        str(seed),
    ]
    if evaluate:
        command.append("--evaluate")
    if dry_run:
        command.append("--dry-run")
    print(f"\n[실행] {subprocess.list2cmdline(command)}\n")
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode


def run_batch(
    pack_input: Path,
    official_data: Path,
    output_dir: Path,
    usage_manifest: Path,
    quotas: tuple[int, int, int],
    image_pack_manifest: Path | None,
    seed: int,
    *,
    evaluate: bool,
    dry_run: bool,
    resume: bool,
) -> int:
    easy, medium, hard = quotas
    command = [
        sys.executable,
        "-m",
        "ai.question_generation.workflows.closed_pack_batch",
        "--pack-input",
        str(pack_input),
        "--official-data",
        str(official_data),
        "--output-dir",
        str(output_dir),
        "--usage-manifest",
        str(usage_manifest),
        "--mock-exam",
        "--easy",
        str(easy),
        "--medium",
        str(medium),
        "--hard",
        str(hard),
        "--seed",
        str(seed),
    ]
    if evaluate:
        command.append("--evaluate")
    if image_pack_manifest:
        command.extend(("--image-pack-manifest", str(image_pack_manifest)))
    if dry_run:
        command.append("--dry-run")
    if resume:
        command.append("--resume")
    print(f"\n[실행] {subprocess.list2cmdline(command)}\n")
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode


def show_results(run_dir: Path) -> None:
    summaries = sorted(run_dir.glob("*/summary.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not summaries:
        print("생성 결과가 없습니다.")
        return
    for path in summaries[:20]:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            evaluation = (data.get("evaluation") or {}).get("status", "미평가")
            print(
                f"- {path.parent.name}: 성공 {data.get('succeeded', 0)}/{data.get('requested', 0)}, "
                f"평가 {evaluation}"
            )
        except (OSError, json.JSONDecodeError):
            print(f"- {path.parent.name}: summary 읽기 실패")


def doctor() -> None:
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_PACK_PLAN_MODEL",
        "OPENAI_PACK_MODEL",
        "OPENAI_CHAT_MODEL",
        "OPENAI_EVAL_MODEL",
        "QGEN_V41_VALIDATION",
        "RUNPOD_ENDPOINT_ID",
        "RUNPOD_API_KEY",
    ):
        print(f"- {key}: {'설정됨' if os.getenv(key) else '없음'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Graph pack 생성과 기존 pack 출제를 잇는 통합 CLI")
    parser.add_argument("--pack-input", type=Path, default=os.getenv("QGEN_CLOSED_PACK_INPUT"))
    parser.add_argument(
        "--pack-plan-model",
        default=(
            os.getenv("OPENAI_PACK_PLAN_MODEL")
            or os.getenv("OPENAI_PACK_MODEL")
            or os.getenv("OPENAI_CHAT_MODEL")
        ),
    )
    parser.add_argument("--v41-validation", type=Path, default=os.getenv("QGEN_V41_VALIDATION"))
    parser.add_argument("--official-data", type=Path, default=os.getenv("QGEN_OFFICIAL_DATA"))
    parser.add_argument("--image-pack-manifest", type=Path, default=os.getenv("QGEN_IMAGE_PACK_MANIFEST"))
    parser.add_argument("--run-dir", type=Path, default=os.getenv("QGEN_RUN_DIR") or Path.home() / "qgen_runs")
    return parser.parse_args()


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    pack_input = args.pack_input
    official_data = args.official_data
    image_pack_manifest = args.image_pack_manifest

    try:
        while True:
            print("\n=== 한능검 Pack·문제 통합 CLI ===")
            print(f"저장 폴더: {run_dir}")
            print(
                "1. 환경 확인\n"
                "2. pack 만들기·변환\n"
                "3. 기존 pack에서 출제\n"
                "4. 중단된 출제 재개\n"
                "5. 생성 결과 보기\n"
                "0. 종료"
            )
            action = ask("선택")
            if action == "0":
                return 0
            if action == "1":
                doctor()
                continue
            if action == "2":
                pack_kind = choose(
                    "pack 유형",
                    [
                        {"value": "graph", "label": "GraphDB 일반 선택형"},
                        {"value": "chronology", "label": "검수된 사건 계획으로 연표형"},
                        {"value": "image", "label": "검수된 이미지 pack을 출제 manifest로 변환"},
                    ],
                    lambda row: row["label"],
                )["value"]
                created_pack = None
                created_image_manifest = None
                reviewed_image_packs = None
                try:
                    if pack_kind == "graph":
                        default_bank = Path(pack_input).expanduser().resolve() if pack_input else None
                        if default_bank and default_bank.is_file():
                            existing_bank = (
                                default_bank
                                if yes(f"기존 pack bank와 중복 검사할까요? ({default_bank})", default=True)
                                else None
                            )
                        else:
                            existing_value = ask("중복 검사할 기존 pack bank (비우면 생략)")
                            existing_bank = Path(existing_value).expanduser().resolve() if existing_value else None
                            if existing_bank and not existing_bank.is_file():
                                raise ValueError(f"파일을 찾을 수 없습니다: {existing_bank}")
                        if not args.v41_validation or not args.v41_validation.is_file():
                            raise ValueError("--v41-validation 또는 QGEN_V41_VALIDATION 파일이 필요합니다.")
                        created_pack = create_graph_packs(
                            run_dir,
                            existing_bank,
                            args.pack_plan_model,
                            args.v41_validation,
                            positive_number("만들 pack 수", 1),
                        )
                    elif pack_kind == "chronology":
                        created_pack = create_chronology_packs(
                            run_dir,
                            path_value("검수된 연표 사건 계획 JSON", must_exist=True),
                        )
                    else:
                        reviewed_image_packs = path_value("검수된 이미지 pack JSON", must_exist=True)
                        created_image_manifest = create_image_pack_manifest(
                            run_dir,
                            reviewed_image_packs,
                        )
                except Exception as exc:
                    print(f"\npack 생성 실패: {exc}")
                    continue
                if created_image_manifest:
                    image_pack_manifest = created_image_manifest
                    print("이미지 manifest가 현재 출제 입력으로 설정됐습니다.")
                    if yes("방금 만든 이미지 pack에서 바로 출제할까요?"):
                        capacity = int(
                            json.loads(created_image_manifest.read_text(encoding="utf-8"))["pack_count"]
                        )
                        while True:
                            image_count = positive_number("만들 이미지 문항 수", capacity)
                            if image_count <= capacity:
                                break
                            print(f"이미지 pack 수를 초과했습니다: {image_count}/{capacity}")
                        output_dir = run_dir / f"image_questions_{datetime.now():%Y%m%d_%H%M%S}"
                        actual = yes("실제 API를 호출할까요?")
                        evaluate = actual and yes("고정선지 평가도 실행할까요?", default=True)
                        code = run_image_packs(
                            reviewed_image_packs,
                            created_image_manifest,
                            output_dir,
                            run_dir / "closed_pack_usage.json",
                            image_count,
                            int(datetime.now().strftime("%Y%m%d%H%M%S")),
                            evaluate=evaluate,
                            dry_run=not actual,
                        )
                        print(f"\n종료 코드: {code}\n출력 폴더: {output_dir}")
                if created_pack:
                    pack_input = created_pack
                    if yes("방금 만든 pack에서 바로 회전 출제할까요?"):
                        output_dir = run_dir / f"pack_questions_{datetime.now():%Y%m%d_%H%M%S}"
                        actual = yes("실제 API를 호출할까요?")
                        evaluate = actual and yes(
                            "v1.8.6 평가와 SLLM 2회·GPT 1회 부분 수리도 실행할까요?",
                            default=True,
                        )
                        code = run_pack_variants(
                            created_pack,
                            output_dir,
                            run_dir / "closed_pack_usage.json",
                            positive_number("pack당 만들 문항 수", 1),
                            int(datetime.now().strftime("%Y%m%d%H%M%S")),
                            evaluate=evaluate,
                            dry_run=not actual,
                        )
                        print(f"\n종료 코드: {code}\n출력 폴더: {output_dir}")
                continue
            if action == "5":
                show_results(run_dir)
                continue
            if action not in {"3", "4"}:
                print("메뉴 번호를 선택해 주세요.")
                continue

            pack_input = path_value("closed-pack JSON", pack_input, must_exist=True)
            official_data = path_value("공식 기출 JSON", official_data, must_exist=True)
            image_value = ask("이미지 pack manifest (비우면 제외)", str(image_pack_manifest or ""))
            image_pack_manifest = Path(image_value).expanduser().resolve() if image_value else None
            if image_pack_manifest and not image_pack_manifest.is_file():
                print(f"파일을 찾을 수 없습니다: {image_pack_manifest}")
                continue
            if action == "4":
                output_dir = path_value("재개할 출력 폴더", latest_run(run_dir), must_exist=True)
            else:
                output_dir = run_dir / f"mock_exam_{datetime.now():%Y%m%d_%H%M%S}"
            quotas = (number("1점 문항 수", 10), number("2점 문항 수", 30), number("3점 문항 수", 10))
            seed = int(datetime.now().strftime("%Y%m%d%H%M%S"))
            actual = yes("실제 API를 호출할까요?")
            evaluate = actual and yes("v1.8.6 평가와 SLLM 2회·GPT 1회 부분 수리도 실행할까요?", default=True)
            code = run_batch(
                pack_input,
                official_data,
                output_dir,
                run_dir / "closed_pack_usage.json",
                quotas,
                image_pack_manifest,
                seed,
                evaluate=evaluate,
                dry_run=not actual,
                resume=action == "4",
            )
            print(f"\n종료 코드: {code}\n출력 폴더: {output_dir}")
            if (output_dir / "mock_exam.md").exists():
                print(f"문제지: {output_dir / 'mock_exam.md'}")
    except (EOFError, KeyboardInterrupt):
        print("\n종료합니다.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

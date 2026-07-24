"""사람이 문제은행 파이프라인을 실행하고 상태를 확인하는 대화형 CLI."""

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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIFFICULTIES = {"1": [], "2": ["쉬움"], "3": ["보통"], "4": ["어려움"]}


def ask(prompt: str) -> str:
    return input(prompt).strip().lstrip("\ufeff")


def yes(prompt: str) -> bool:
    return ask(f"{prompt} [y/N] ").lower() in {"y", "yes", "ㅇ"}


def choose(prompt: str, maximum: int) -> int | None:
    value = ask(prompt)
    if value in {"", "0"}:
        return None
    try:
        number = int(value)
    except ValueError:
        print("숫자를 입력해 주세요.")
        return None
    if not 1 <= number <= maximum:
        print("목록에 있는 번호를 입력해 주세요.")
        return None
    return number


def run_module(module: str, *arguments: str) -> int:
    command = [sys.executable, "-m", module, *arguments]
    print(f"\n[실행] {subprocess.list2cmdline(command)}\n")
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode


def checkpoint_paths(run_dir: Path) -> list[Path]:
    paths = []
    for path in run_dir.rglob("*.json") if run_dir.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("schema_version") == "question_bank_generation_run_v2":
            paths.append(path)
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def component_line(name: str, component: dict[str, Any]) -> str:
    gate = component.get("gate") or {}
    status = gate.get("status") or ("완료" if component.get("response") else "대기")
    errors = ", ".join(gate.get("errors") or []) or "-"
    return f"- {name}: {status} / 시도 {component.get('attempts', 0)} / 오류 {errors}"


def state_summary(state: dict[str, Any]) -> str:
    components = state.get("components") or {}
    lines = [
        f"상태: {state.get('status', 'unknown')}",
        f"pack: {state.get('pack_id', '-')}",
        f"topic: {(state.get('input') or {}).get('topic', '-')}",
        f"호출: {state.get('total_llm_calls', 0)}회 / {state.get('elapsed_seconds', 0)}초",
        component_line("material", components.get("material") or {}),
        component_line("correct", components.get("correct") or {}),
    ]
    for slot, component in sorted((components.get("distractors") or {}).items(), key=lambda row: int(row[0])):
        lines.append(component_line(f"distractor:{slot}", component))
    if state.get("error"):
        lines.append(f"오류: {state['error']}")
    question = state.get("question") or {}
    if question:
        lines.extend(["", "[지문]", str(question.get("material") or ""), "", "[발문]", str(question.get("question") or ""), ""])
        for choice in sorted(question.get("choices") or [], key=lambda row: int(row.get("number") or 0)):
            mark = " (정답)" if choice.get("is_answer") else ""
            lines.append(f"{choice.get('number')}. {choice.get('text')}{mark}")
    return "\n".join(lines)


def select_checkpoint(run_dir: Path, *, incomplete_only: bool = False) -> Path | None:
    rows = []
    for path in checkpoint_paths(run_dir):
        state = json.loads(path.read_text(encoding="utf-8-sig"))
        if incomplete_only and state.get("status") == "complete":
            continue
        rows.append((path, state))
    if not rows:
        print("해당하는 실행 기록이 없습니다.")
        return None
    for index, (path, state) in enumerate(rows, start=1):
        print(f"{index:>2}. [{state.get('status', 'unknown')}] {(state.get('input') or {}).get('topic', '-')} — {path.name}")
    selected = choose("번호 선택 (0: 취소): ", len(rows))
    return rows[selected - 1][0] if selected else None


def select_pack() -> dict[str, str] | None:
    from question_generation.legacy.workflows.batch import eligible_packs

    print("\n1. 전체  2. 쉬움  3. 보통  4. 어려움")
    difficulty = ask("난이도 [1]: ") or "1"
    if difficulty not in DIFFICULTIES:
        print("난이도 번호가 올바르지 않습니다.")
        return None
    try:
        packs = eligible_packs(DIFFICULTIES[difficulty], [], 20)
    except Exception as exc:
        print(f"DB pack 조회 실패: {exc}")
        return None
    if not packs:
        print("생성 가능한 승인 pack이 없습니다.")
        return None
    print()
    for index, pack in enumerate(packs, start=1):
        print(
            f"{index:>2}. [{pack['difficulty_label']}] {pack['topic']} / "
            f"{pack['question_task']} / {pack['material_type']}"
        )
    selected = choose("pack 선택 (0: 취소): ", len(packs))
    return packs[selected - 1] if selected else None


def generate(run_dir: Path) -> None:
    pack = select_pack()
    if not pack:
        return
    from question_generation.legacy.workflows.mock_exam import safe_id

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = run_dir / f"{stamp}_{safe_id(pack['pack_id'])}.json"
    execute = yes("실제 API를 호출해 문항을 생성할까요?")
    arguments = ["--pack-id", pack["pack_id"], "--output", str(output)]
    if not execute:
        arguments.append("--dry-run")
    code = run_module("question_generation.workflows.question_pipeline", *arguments)
    if output.exists():
        print(f"\n{state_summary(json.loads(output.read_text(encoding='utf-8-sig')))}")
        print(f"\n저장: {output}")
    if code == 0 and execute and yes("최종 v1.8.5 평가도 실행할까요?"):
        prefix = output.with_name(f"{output.stem}_evaluation")
        run_module(
            "question_generation.evaluation.v18",
            "--input", str(output),
            "--output-prefix", str(prefix),
        )


def resume(run_dir: Path) -> None:
    path = select_checkpoint(run_dir, incomplete_only=True)
    if not path or not yes("이 실행을 이어서 실제 API로 생성할까요?"):
        return
    state = json.loads(path.read_text(encoding="utf-8-sig"))
    run_module(
        "question_generation.workflows.question_pipeline",
        "--pack-id", str(state["pack_id"]),
        "--output", str(path),
        "--resume",
    )
    print(f"\n{state_summary(json.loads(path.read_text(encoding='utf-8-sig')))}")


def show_result(run_dir: Path) -> None:
    path = select_checkpoint(run_dir)
    if path:
        print(f"\n{state_summary(json.loads(path.read_text(encoding='utf-8-sig')))}\n\n파일: {path}")


def doctor(run_dir: Path) -> None:
    keys = ("OPENAI_API_KEY", "OPENAI_CHAT_MODEL", "RUNPOD_ENDPOINT_ID", "RUNPOD_API_KEY")
    for key in keys:
        print(f"- {key}: {'설정됨' if os.getenv(key) else '없음'}")
    print(f"- 실행 저장 폴더: {run_dir}")
    try:
        from storage.postgresql.connection import connect_db

        conn = connect_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            print("- PostgreSQL: 연결됨")
        finally:
            conn.close()
    except Exception as exc:
        print(f"- PostgreSQL: 연결 실패 ({exc})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="한능검 문제은행 대화형 생성기")
    parser.add_argument("--run-dir", type=Path, default=os.getenv("QGEN_RUN_DIR") or Path.home() / "qgen_runs")
    return parser.parse_args()


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        while True:
            print("\n=== 한능검 문제 생성기 ===")
            print(f"저장 폴더: {run_dir}")
            print("1. 환경 확인\n2. 승인 pack으로 문항 생성\n3. 중단된 실행 재개\n4. 생성 결과 보기\n0. 종료")
            action = ask("선택: ")
            if action == "0":
                return 0
            {"1": doctor, "2": generate, "3": resume, "4": show_result}.get(action, lambda _path: print("메뉴 번호를 선택해 주세요."))(run_dir)
    except (EOFError, KeyboardInterrupt):
        print("\n종료합니다.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

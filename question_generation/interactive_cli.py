"""현행 closed-pack 모의고사 생성기를 실행하는 대화형 CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
        "question_generation.workflows.closed_pack_batch",
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
    for key in ("OPENAI_API_KEY", "OPENAI_CHAT_MODEL", "OPENAI_EVAL_MODEL", "RUNPOD_ENDPOINT_ID", "RUNPOD_API_KEY"):
        print(f"- {key}: {'설정됨' if os.getenv(key) else '없음'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="현행 closed-pack 대화형 모의고사 생성기")
    parser.add_argument("--pack-input", type=Path, default=os.getenv("QGEN_CLOSED_PACK_INPUT"))
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
            print("\n=== 한능검 Closed-Pack 모의고사 생성기 ===")
            print(f"저장 폴더: {run_dir}")
            print("1. 환경 확인\n2. 새 모의고사 생성\n3. 중단된 생성 재개\n4. 생성 결과 보기\n0. 종료")
            action = ask("선택")
            if action == "0":
                return 0
            if action == "1":
                doctor()
                continue
            if action == "4":
                show_results(run_dir)
                continue
            if action not in {"2", "3"}:
                print("메뉴 번호를 선택해 주세요.")
                continue

            pack_input = path_value("closed-pack JSON", pack_input, must_exist=True)
            official_data = path_value("공식 기출 JSON", official_data, must_exist=True)
            image_value = ask("이미지 pack manifest (비우면 제외)", str(image_pack_manifest or ""))
            image_pack_manifest = Path(image_value).expanduser().resolve() if image_value else None
            if image_pack_manifest and not image_pack_manifest.is_file():
                print(f"파일을 찾을 수 없습니다: {image_pack_manifest}")
                continue
            if action == "3":
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
                resume=action == "3",
            )
            print(f"\n종료 코드: {code}\n출력 폴더: {output_dir}")
            if (output_dir / "mock_exam.md").exists():
                print(f"문제지: {output_dir / 'mock_exam.md'}")
    except (EOFError, KeyboardInterrupt):
        print("\n종료합니다.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

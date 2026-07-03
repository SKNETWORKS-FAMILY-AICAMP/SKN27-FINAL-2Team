"""
Neo4j CSV 전처리 파이프라인을 한 번에 실행한다.

각 단계는 실제 CSV 저장 모드로 실행된다.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Neo4j 전처리 CSV 생성 스크립트를 순서대로 실행한다."
    )
    parser.add_argument(
        "--python-path",
        type=Path,
        default=None,
        help="실행에 사용할 Python 경로. 지정하지 않으면 현재 Python을 사용한다.",
    )

    return parser.parse_args()


def resolve_project_root(start_path):
    for parent_path in [start_path, *start_path.parents]:
        if (parent_path / ".git").exists() and (parent_path / "etl").exists():
            return parent_path

    raise FileNotFoundError(f"프로젝트 루트를 찾을 수 없습니다: {start_path}")


def build_import_output_dirs(project_root):
    import_dir = project_root / "storage" / "neo4j" / "neo4j_import"

    return {
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
    }


def build_pipeline_steps(script_dir, project_root):
    step_dir = script_dir / "scripts"
    import_output_dirs = build_import_output_dirs(project_root)

    return [
        {
            "step_name": "1. raw 데이터 정규화",
            "script_path": step_dir / "normalize_raw_data.py",
        },
        {
            "step_name": "2. 1차 사전 생성",
            "script_path": step_dir / "make_base_dictionaries.py",
        },
        {
            "step_name": "3. mapping/staging 생성",
            "script_path": step_dir / "make_mapping_tables.py",
        },
        {
            "step_name": "4. 최종 graph node/relation 생성",
            "script_path": step_dir / "make_graph_csv.py",
            "extra_args": [
                "--nodes-dir",
                import_output_dirs["nodes_dir"],
                "--relations-dir",
                import_output_dirs["relations_dir"],
            ],
        },
    ]


def build_step_command(python_path, script_path, extra_args):
    command = [str(python_path), str(script_path), "--save"]
    command.extend([str(arg) for arg in extra_args])

    return command


def run_pipeline_step(step, python_path):
    script_path = step["script_path"]
    extra_args = step.get("extra_args", [])
    command = build_step_command(python_path, script_path, extra_args)

    print("", flush=True)
    print(f"[START] {step['step_name']}", flush=True)
    print(f"script: {script_path}", flush=True)

    completed_process = subprocess.run(
        command,
        cwd=script_path.parent,
        check=False,
    )

    if completed_process.returncode != 0:
        raise SystemExit(
            f"[FAILED] {step['step_name']} failed with exit code "
            f"{completed_process.returncode}"
        )

    print(f"[DONE] {step['step_name']}", flush=True)


def resolve_python_path(args):
    python_path = args.python_path

    if python_path is None:
        python_path = Path(sys.executable)

    return python_path


def main():
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    project_root = resolve_project_root(script_dir)
    python_path = resolve_python_path(args)
    pipeline_steps = build_pipeline_steps(script_dir, project_root)

    print("Neo4j preprocessing pipeline", flush=True)
    print(f"python: {python_path}", flush=True)
    print(f"project_root: {project_root}", flush=True)
    print("mode: save", flush=True)

    for step in pipeline_steps:
        run_pipeline_step(step, python_path)

    print("", flush=True)
    print("[DONE] all Neo4j preprocessing CSV steps completed", flush=True)


if __name__ == "__main__":
    main()

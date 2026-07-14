"""
Neo4j CSV 전처리 파이프라인을 한 번에 실행한다.

각 단계는 실제 CSV 저장 모드로 실행된다.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from neo4j_common import resolve_import_dir, resolve_project_root


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


def build_import_output_dirs(project_root):
    import_dir = resolve_import_dir(project_root)

    return {
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
    }


def build_generated_csv_dirs(script_dir, project_root):
    import_output_dirs = build_import_output_dirs(project_root)

    return [
        script_dir / "normalized",
        script_dir / "dictionary",
        script_dir / "staging",
        script_dir / "mapping",
        import_output_dirs["nodes_dir"],
        import_output_dirs["relations_dir"],
    ]


def build_preserved_staging_csv_names():
    return {
        "term_era_candidate.csv",
    }


def should_preserve_generated_csv(csv_path, generated_csv_dir, script_dir):
    staging_dir = (script_dir / "staging").resolve()

    if generated_csv_dir.resolve() != staging_dir:
        return False

    return csv_path.name in build_preserved_staging_csv_names()


def resolve_project_path(target_path, project_root):
    resolved_path = target_path.resolve()
    resolved_root = project_root.resolve()

    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"CSV cleanup target is outside project root: {resolved_path}"
        ) from exc

    return resolved_path


def remove_existing_csv_outputs(script_dir, project_root):
    deleted_count = 0
    generated_csv_dirs = build_generated_csv_dirs(script_dir, project_root)

    print("", flush=True)
    print("[CLEAN] removing existing generated Neo4j CSV files", flush=True)

    for generated_csv_dir in generated_csv_dirs:
        resolved_dir = resolve_project_path(generated_csv_dir, project_root)

        if not resolved_dir.exists():
            continue

        for csv_path in sorted(resolved_dir.glob("*.csv")):
            if should_preserve_generated_csv(csv_path, generated_csv_dir, script_dir):
                print(f"preserved: {csv_path.resolve()}", flush=True)
                continue

            resolved_csv_path = resolve_project_path(csv_path, project_root)
            resolved_csv_path.unlink()
            deleted_count += 1
            print(f"removed: {resolved_csv_path}", flush=True)

    print(f"[CLEAN] removed {deleted_count} CSV files", flush=True)


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
        {
            "step_name": "5. Theme/Era/EntityType 상위 레이어 생성",
            "script_path": step_dir / "make_theme_era_csv.py",
            "extra_args": [
                "--nodes-dir",
                import_output_dirs["nodes_dir"],
                "--relations-dir",
                import_output_dirs["relations_dir"],
            ],
        },
        {
            "step_name": "6. AKS canonical/source graph data",
            "script_path": step_dir / "make_aks_graph_csv.py",
            "extra_args": [
                "--nodes-dir",
                import_output_dirs["nodes_dir"],
                "--relations-dir",
                import_output_dirs["relations_dir"],
            ],
        },
        {
            "step_name": "7. AKS polity/reign graph data",
            "script_path": step_dir / "make_aks_reign_graph_csv.py",
            "extra_args": [
                "--nodes-dir",
                import_output_dirs["nodes_dir"],
                "--relations-dir",
                import_output_dirs["relations_dir"],
            ],
        },
        {
            "step_name": "8. AKS curated royal action graph data",
            "script_path": step_dir / "make_aks_royal_action_csv.py",
            "extra_args": [
                "--nodes-dir",
                import_output_dirs["nodes_dir"],
                "--relations-dir",
                import_output_dirs["relations_dir"],
            ],
        },
        {
            "step_name": "9. AKS cultural heritage classification data",
            "script_path": step_dir / "make_aks_heritage_csv.py",
            "extra_args": [
                "--nodes-dir",
                import_output_dirs["nodes_dir"],
            ],
        },
        {
            "step_name": "10. Source image nodes and depicts relations",
            "script_path": step_dir / "make_source_image_csv.py",
            "extra_args": [
                "--nodes-dir",
                import_output_dirs["nodes_dir"],
                "--relations-dir",
                import_output_dirs["relations_dir"],
            ],
        },
        {
            "step_name": "11. Inscription content and source text graph data",
            "script_path": step_dir / "make_inscription_content_csv.py",
            "extra_args": [
                "--nodes-dir",
                import_output_dirs["nodes_dir"],
                "--relations-dir",
                import_output_dirs["relations_dir"],
            ],
        },
        {
            "step_name": "12. Positive and negative graph QA",
            "script_path": step_dir / "validate_graph_qa.py",
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

    remove_existing_csv_outputs(script_dir, project_root)

    for step in pipeline_steps:
        run_pipeline_step(step, python_path)

    print("", flush=True)
    print("[DONE] all Neo4j preprocessing CSV steps completed", flush=True)


if __name__ == "__main__":
    main()

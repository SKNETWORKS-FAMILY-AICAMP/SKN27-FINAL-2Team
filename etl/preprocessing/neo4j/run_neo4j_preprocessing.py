"""
Neo4j CSV 전처리 파이프라인을 한 번에 실행한다.

모든 단계는 후보 import 디렉터리에서 실행하며, 검증을 통과한 결과만
최종 import 디렉터리로 안전하게 승격한다.
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from neo4j_common import resolve_default_import_dir, resolve_project_root


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
    parser.add_argument(
        "--promote-existing",
        action="store_true",
        help=(
            "완료 marker가 있는 기존 후보 import를 재생성 없이 검증하고 "
            "최종 import로 승격한다. bind mount 사용자는 컨테이너를 먼저 중지해야 한다."
        ),
    )

    return parser.parse_args()


def build_import_output_dirs(import_dir):
    return {
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
    }


def build_generated_csv_dirs(script_dir, import_dir):
    import_output_dirs = build_import_output_dirs(import_dir)

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


def remove_generated_directory(directory_path, project_root):
    resolved_path = resolve_project_path(directory_path, project_root)

    if resolved_path.exists():
        shutil.rmtree(resolved_path)


@contextmanager
def hold_runner_lock(lock_path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+b")
    lock_module = None
    lock_kind = None

    if lock_path.stat().st_size == 0:
        lock_handle.write(b"0")
        lock_handle.flush()

    lock_handle.seek(0)

    try:
        import msvcrt

        lock_module = msvcrt
        lock_kind = "msvcrt"
        lock_module.locking(lock_handle.fileno(), lock_module.LK_NBLCK, 1)
    except ImportError:
        try:
            import fcntl

            lock_module = fcntl
            lock_kind = "fcntl"
            lock_module.flock(
                lock_handle.fileno(),
                lock_module.LOCK_EX | lock_module.LOCK_NB,
            )
        except ImportError as exc:
            lock_handle.close()
            raise RuntimeError(
                "No supported file-locking module is available on this platform."
            ) from exc
        except OSError as exc:
            lock_handle.close()
            raise RuntimeError(
                "Another Neo4j preprocessing runner is already active."
            ) from exc
    except OSError as exc:
        lock_handle.close()
        raise RuntimeError(
            "다른 Neo4j 전처리 runner가 실행 중입니다. 완료 후 다시 실행하세요."
        ) from exc

    try:
        yield
    finally:
        if lock_kind == "msvcrt":
            lock_handle.seek(0)
            lock_module.locking(lock_handle.fileno(), lock_module.LK_UNLCK, 1)
        elif lock_kind == "fcntl":
            lock_module.flock(lock_handle.fileno(), lock_module.LOCK_UN)

        lock_handle.close()


def recover_interrupted_promotion(
    final_import_dir,
    candidate_import_dir,
    backup_import_dir,
    project_root,
):
    manifest_name = ".preprocessing_complete.json"

    if backup_import_dir.exists() and not final_import_dir.exists():
        print(
            f"[RECOVER] restoring previous import: {backup_import_dir}",
            flush=True,
        )
        backup_import_dir.rename(final_import_dir)

    if backup_import_dir.exists() and final_import_dir.exists():
        final_manifest_path = final_import_dir / manifest_name

        if not final_manifest_path.exists():
            raise RuntimeError(
                "최종 import와 이전 백업이 함께 남아 있지만 완료 marker가 없습니다. "
                "자동 삭제하지 않고 중단합니다."
            )

        try:
            final_manifest = json.loads(
                final_manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "최종 import의 완료 marker를 읽을 수 없어 이전 백업을 "
                "자동 삭제하지 않습니다."
            ) from exc

        if final_manifest.get("status") != "complete":
            raise RuntimeError(
                "최종 import의 완료 marker 상태가 complete가 아니어서 이전 "
                "백업을 자동 삭제하지 않습니다."
            )

        print(f"[RECOVER] removing completed backup: {backup_import_dir}", flush=True)
        remove_generated_directory(backup_import_dir, project_root)

    candidate_is_complete = False

    if candidate_import_dir.exists():
        candidate_manifest_path = candidate_import_dir / manifest_name

        if candidate_manifest_path.exists():
            try:
                candidate_manifest = json.loads(
                    candidate_manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "후보 import의 완료 marker를 읽을 수 없어 자동 삭제하지 않습니다."
                ) from exc

            if candidate_manifest.get("status") != "complete":
                raise RuntimeError(
                    "후보 import의 완료 marker 상태가 complete가 아니어서 "
                    "자동 삭제하지 않습니다."
                )

            candidate_is_complete = True
            print(
                f"[RECOVER] preserving completed candidate: {candidate_import_dir}",
                flush=True,
            )

    if candidate_import_dir.exists() and not candidate_is_complete:
        print(
            f"[RECOVER] removing incomplete candidate: {candidate_import_dir}",
            flush=True,
        )
        remove_generated_directory(candidate_import_dir, project_root)

    return candidate_is_complete


def remove_existing_csv_outputs(script_dir, project_root, candidate_import_dir):
    deleted_count = 0
    generated_csv_dirs = build_generated_csv_dirs(script_dir, candidate_import_dir)

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


def build_pipeline_steps(script_dir, import_dir):
    step_dir = script_dir / "scripts"
    import_output_dirs = build_import_output_dirs(import_dir)

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


def run_pipeline_step(step, python_path, child_environment):
    script_path = step["script_path"]
    extra_args = step.get("extra_args", [])
    command = build_step_command(python_path, script_path, extra_args)

    print("", flush=True)
    print(f"[START] {step['step_name']}", flush=True)
    print(f"script: {script_path}", flush=True)

    completed_process = subprocess.run(
        command,
        cwd=script_path.parent,
        env=child_environment,
        check=False,
    )

    if completed_process.returncode != 0:
        raise RuntimeError(
            f"[FAILED] {step['step_name']} failed with exit code "
            f"{completed_process.returncode}"
        )

    print(f"[DONE] {step['step_name']}", flush=True)


def resolve_python_path(args):
    python_path = args.python_path

    if python_path is None:
        python_path = Path(sys.executable)

    return python_path


def extract_declared_import_csv_paths(project_root):
    schema_dir = project_root / "storage" / "neo4j" / "schema"
    schema_paths = [
        schema_dir / "history_graph_import_nodes.cypher",
        schema_dir / "history_graph_import_relations.cypher",
    ]
    csv_pattern = re.compile(r"file:///((?:nodes|relations)/[^'\"\r\n]+\.csv)")
    declared_csv_paths = set()

    for schema_path in schema_paths:
        if not schema_path.exists():
            raise FileNotFoundError(f"Neo4j import Cypher가 없습니다: {schema_path}")

        schema_text = schema_path.read_text(encoding="utf-8-sig")
        declared_csv_paths.update(csv_pattern.findall(schema_text))

    if len(declared_csv_paths) == 0:
        raise RuntimeError("Neo4j import Cypher에서 CSV 선언을 찾지 못했습니다.")

    return sorted(declared_csv_paths), schema_paths


def validate_candidate_import(candidate_import_dir, declared_csv_paths):
    resolved_candidate_dir = candidate_import_dir.resolve()
    validation_errors = []

    for relative_csv_path in declared_csv_paths:
        csv_path = (candidate_import_dir / Path(relative_csv_path)).resolve()

        try:
            csv_path.relative_to(resolved_candidate_dir)
        except ValueError:
            validation_errors.append(f"허용 범위를 벗어난 CSV 선언: {relative_csv_path}")
            continue

        if not csv_path.exists():
            validation_errors.append(f"CSV 없음: {relative_csv_path}")
            continue

        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            header = next(csv.reader(csv_file), None)

        if header is None or len(header) == 0:
            validation_errors.append(f"CSV header 없음: {relative_csv_path}")
            continue

        if any(column_name.strip() == "" for column_name in header):
            validation_errors.append(f"CSV header 빈 컬럼명: {relative_csv_path}")

    if len(validation_errors) > 0:
        error_text = "\n".join(f"- {error}" for error in validation_errors)
        raise RuntimeError(f"후보 import 검증 실패:\n{error_text}")

    print(
        f"[VALIDATE] {len(declared_csv_paths)} declared CSV files passed",
        flush=True,
    )


def write_completion_manifest(candidate_import_dir, declared_csv_paths, schema_paths):
    manifest_path = candidate_import_dir / ".preprocessing_complete.json"
    manifest = {
        "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "schema_files": [str(path.name) for path in schema_paths],
        "declared_csv_files": declared_csv_paths,
    }

    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, ensure_ascii=False, indent=2)
        manifest_file.write("\n")
        manifest_file.flush()
        os.fsync(manifest_file.fileno())

    print(f"[VALIDATE] completion marker: {manifest_path}", flush=True)


def promote_candidate_import(
    final_import_dir,
    candidate_import_dir,
    backup_import_dir,
    project_root,
):
    if backup_import_dir.exists():
        raise RuntimeError(f"이전 import 백업이 아직 남아 있습니다: {backup_import_dir}")

    previous_import_moved = False

    try:
        if final_import_dir.exists():
            final_import_dir.rename(backup_import_dir)
            previous_import_moved = True

        candidate_import_dir.rename(final_import_dir)
    except Exception as exc:
        if previous_import_moved and not final_import_dir.exists():
            backup_import_dir.rename(final_import_dir)

        if isinstance(exc, PermissionError):
            raise RuntimeError(
                "검증 완료 후보를 최종 import로 승격할 수 없습니다. "
                "실행 중인 Neo4j 컨테이너가 final import를 bind mount 중이면 "
                "컨테이너를 중지한 뒤 --promote-existing으로 다시 실행하세요. "
                f"후보는 보존했습니다: {candidate_import_dir}"
            ) from exc

        raise

    if backup_import_dir.exists():
        try:
            remove_generated_directory(backup_import_dir, project_root)
        except OSError:
            print(
                f"[WARN] 승격은 완료됐지만 이전 백업을 삭제하지 못했습니다: "
                f"{backup_import_dir}",
                flush=True,
            )

    print(f"[PROMOTE] candidate -> final: {final_import_dir}", flush=True)
    print(
        "[NOTICE] Neo4j가 final import 디렉터리를 bind mount 중이었다면 "
        "LOAD CSV 전에 새 파일 노출을 확인하고, 보이지 않으면 컨테이너를 재시작하세요.",
        flush=True,
    )


def main():
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    project_root = resolve_project_root(script_dir)
    python_path = resolve_python_path(args)
    final_import_dir = resolve_default_import_dir(project_root)
    candidate_import_dir = final_import_dir.with_name(
        f".{final_import_dir.name}.building"
    )
    backup_import_dir = final_import_dir.with_name(
        f".{final_import_dir.name}.previous"
    )
    lock_path = final_import_dir.parent / ".neo4j_preprocessing.lock"

    print("Neo4j preprocessing pipeline", flush=True)
    print(f"python: {python_path}", flush=True)
    print(f"project_root: {project_root}", flush=True)
    print(f"candidate_import: {candidate_import_dir}", flush=True)
    print(f"final_import: {final_import_dir}", flush=True)
    print("mode: safe candidate build", flush=True)

    with hold_runner_lock(lock_path):
        candidate_is_complete = recover_interrupted_promotion(
            final_import_dir,
            candidate_import_dir,
            backup_import_dir,
            project_root,
        )

        if args.promote_existing:
            if not candidate_is_complete:
                raise RuntimeError(
                    "승격할 검증 완료 후보 import가 없습니다: "
                    f"{candidate_import_dir}"
                )

            declared_csv_paths, _ = extract_declared_import_csv_paths(project_root)
            validate_candidate_import(candidate_import_dir, declared_csv_paths)
            promote_candidate_import(
                final_import_dir,
                candidate_import_dir,
                backup_import_dir,
                project_root,
            )
            print("", flush=True)
            print("[DONE] completed candidate promoted", flush=True)
            return

        if candidate_is_complete:
            raise RuntimeError(
                "검증 완료 후보 import가 보존되어 있습니다. 재생성하지 않고 "
                "승격하려면 final import의 bind mount를 해제한 뒤 "
                "--promote-existing으로 실행하세요."
            )

        candidate_import_dir.mkdir(parents=True, exist_ok=False)
        pipeline_steps = build_pipeline_steps(script_dir, candidate_import_dir)
        child_environment = os.environ.copy()
        child_environment["NEO4J_IMPORT_DIR"] = str(candidate_import_dir.resolve())

        remove_existing_csv_outputs(
            script_dir,
            project_root,
            candidate_import_dir,
        )

        for step in pipeline_steps:
            run_pipeline_step(step, python_path, child_environment)

        declared_csv_paths, schema_paths = extract_declared_import_csv_paths(
            project_root
        )
        validate_candidate_import(candidate_import_dir, declared_csv_paths)
        write_completion_manifest(
            candidate_import_dir,
            declared_csv_paths,
            schema_paths,
        )
        promote_candidate_import(
            final_import_dir,
            candidate_import_dir,
            backup_import_dir,
            project_root,
        )

    print("", flush=True)
    print("[DONE] all Neo4j preprocessing CSV steps completed", flush=True)


if __name__ == "__main__":
    main()

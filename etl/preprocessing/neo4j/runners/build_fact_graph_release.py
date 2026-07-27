from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_arguments(neo4j_root: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "검증된 관계와 LLM 승인 관계를 신규 Neo4j 적재 패키지로 생성합니다. "
            "이 명령은 Neo4j에 적재하지 않습니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(neo4j_root / "config" / "fact_graph_release.json"),
    )
    parser.add_argument(
        "--output-root",
        default=str(neo4j_root / "output"),
    )
    parser.add_argument(
        "--release-output",
        default="",
    )
    return parser.parse_args()


def main() -> None:
    neo4j_root = Path(__file__).resolve().parent.parent
    project_root = neo4j_root.parents[2]
    sys.path.insert(0, str(project_root))

    from etl.preprocessing.neo4j.fact_retrieval.fact_graph_release import (
        build_fact_graph_release,
        read_json,
        write_fact_graph_release,
    )

    args = parse_arguments(neo4j_root)
    config = read_json(Path(args.config))
    output_root = Path(args.output_root)
    release_output = Path(args.release_output)
    if not args.release_output:
        release_output = output_root / config["output_directory"]

    package = build_fact_graph_release(output_root, config)
    manifest = write_fact_graph_release(package, release_output, config)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""CLI wrapper for graph_path.legacy_pack: 구형 Graph/RAG pack 실험을 실행한다."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from question_generation.graph_path.legacy_pack import main


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI wrapper for graph_path.select_seed: 구형 Graph/RAG seed를 선택한다."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from question_generation.graph_path.select_seed import main


if __name__ == "__main__":
    raise SystemExit(main())

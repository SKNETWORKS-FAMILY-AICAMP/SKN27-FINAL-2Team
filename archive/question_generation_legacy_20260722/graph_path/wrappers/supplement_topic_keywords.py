"""CLI wrapper for graph_path.topic_keywords: 구형 토픽 CSV를 보충한다."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from question_generation.graph_path.topic_keywords import main


if __name__ == "__main__":
    raise SystemExit(main())

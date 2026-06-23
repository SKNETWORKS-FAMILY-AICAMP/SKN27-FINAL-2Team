from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.chatbot.rag.rag_prototype.concept_chat import ConceptChatbotService
from app.chatbot.rag.rag_prototype.config import RagPaths
from app.chatbot.rag.rag_prototype.retriever import HybridRagRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="HS RAG concept chatbot prototype")
    parser.add_argument("question", help="한국사 개념 질문")
    parser.add_argument("--follow-up", action="store_true", help="첫 질문 이후 설명형 답변으로 생성")
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=None,
        help="processed JSONL 폴더 경로. 기본값은 storage/postgre/processed",
    )
    args = parser.parse_args()

    paths = RagPaths(processed_dir=args.processed_dir) if args.processed_dir else RagPaths()
    retriever = HybridRagRetriever(paths=paths)
    service = ConceptChatbotService(retriever=retriever)
    result = service.answer(args.question, is_first_turn=not args.follow_up)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

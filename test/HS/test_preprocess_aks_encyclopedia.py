from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

from etl.preprocessing.history.preprocess_aks_encyclopedia import build_document, iter_chunks


row = {
    "eid": "E0000001",
    "headword": "영정법",
    "origin": "永定法",
    "headwordOrigin": "영정법(永定法)",
    "definition": "전세를 고정한 제도.",
    "summary": "조선 후기 세제 개혁.",
    "body": "영정법의 본문입니다.",
}
document = build_document(row)
assert document and document["aliases"] == ["영정법", "永定法"]
chunk = next(iter_chunks(document, 1200, 150))
assert "별칭: 영정법, 永定法" in chunk["chunk_text"]
assert chunk["metadata"]["aliases"] == ["영정법", "永定法"]

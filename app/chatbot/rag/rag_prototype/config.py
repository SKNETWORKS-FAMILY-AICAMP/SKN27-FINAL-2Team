from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "storage" / "postgre" / "processed"


@dataclass(frozen=True)
class RagPaths:
    processed_dir: Path = DEFAULT_PROCESSED_DIR

    @property
    def historical_chunks(self) -> Path:
        return self.processed_dir / "historical_sources.chunks.jsonl"

    @property
    def new_history_chunks(self) -> Path:
        return self.processed_dir / "new_history.chunks.jsonl"

    @property
    def image_material_chunks(self) -> Path:
        return self.processed_dir / "image_materials.chunks.jsonl"

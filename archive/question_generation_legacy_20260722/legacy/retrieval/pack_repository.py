"""PostgreSQL 문제은행에서 고정 basis pack 한 개를 읽는 저장소 함수."""

from __future__ import annotations

from typing import Any

from app.chatbot.rag.pgvector_retriever import connect_db


def read_pack(pack_id: str) -> dict[str, Any]:
    """pack과 5개 basis item을 한 번의 SQL로 읽어 생성기 입력 형태로 반환한다."""
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.pack_id, p.target_label, p.topic_type, p.question_task, p.stem_pattern,
                       p.relation_axis_id, p.material_type, p.major_type, p.minor_type,
                       p.difficulty_label, p.material_clue_basis, p.material_evidence_chunks,
                       p.status, p.semantic_status,
                       json_agg(json_build_object(
                           'basis_item_id', i.basis_item_id, 'slot_no', i.slot_no,
                           'role', i.role, 'article_id', i.article_id,
                           'truth_owner_label', i.truth_owner_label, 'fact_basis', i.fact_basis,
                           'evidence_chunks', i.evidence_chunks, 'status', i.status,
                           'semantic_status', i.semantic_status
                       ) ORDER BY i.slot_no)
                FROM qgen.basis_packs p
                JOIN qgen.basis_items i USING (pack_id)
                WHERE p.pack_id = %s
                GROUP BY p.pack_id
                """,
                [pack_id],
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Unknown basis pack: {pack_id}")
            keys = (
                "pack_id", "target_label", "topic_type", "question_task", "stem_pattern",
                "relation_axis_id", "material_type", "major_type", "minor_type",
                "difficulty_label", "material_clue_basis", "material_evidence_chunks",
                "status", "semantic_status", "items",
            )
            return dict(zip(keys, row))
    finally:
        conn.close()

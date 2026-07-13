"""
Load preprocessed past exam rows from ML_han_v1.json into PostgreSQL exam_data.

This script prepares the initial exam_data source table from the current ML file.
It does not touch service tables such as questions, question_options, or solve_records.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_JSON = ROOT_DIR / "ai" / "ml" / "ML_han_v1.json"
DEFAULT_FEATURE_CSV = ROOT_DIR / "ai" / "ml" / "output" / "ml_han_features_v1.csv"
SCHEMA_SQL = ROOT_DIR / "storage" / "postgresql" / "schema" / "alter_apply_latest.sql"


CREATE_EXAM_DATA_SQL = """
CREATE TABLE IF NOT EXISTS exam_data (
    id                         BIGSERIAL       PRIMARY KEY,
    round_no                   INT             NOT NULL,
    question_no                INT             NOT NULL,
    question_text              TEXT            NOT NULL,
    material_text              TEXT            NULL,
    choices_json               JSONB           NOT NULL DEFAULT '[]'::jsonb,
    distractor_choices_json    JSONB           NOT NULL DEFAULT '[]'::jsonb,
    answer_choice              TEXT            NULL,
    answer_no                  INT             NULL,
    era                        VARCHAR(50)     NULL,
    topic                      VARCHAR(50)     NULL,
    question_type              VARCHAR(50)     NULL,
    question_subtype           VARCHAR(50)     NULL,
    q_score                    INT             NULL,
    has_image                  BOOLEAN         NOT NULL DEFAULT FALSE,
    image_meta_json            JSONB           NOT NULL DEFAULT '{}'::jsonb,
    created_at                 TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMP       NOT NULL DEFAULT NOW(),
    answer_explanation         TEXT            NULL,
    choice_explanations_json   JSONB           NOT NULL DEFAULT '{}'::jsonb,
    explanation_source         VARCHAR(50)     NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS exam_data_round_question_uidx
    ON exam_data(round_no, question_no);

CREATE INDEX IF NOT EXISTS exam_data_classification_idx
    ON exam_data(era, topic, question_type, question_subtype);
"""


UPSERT_EXAM_DATA_SQL = """
INSERT INTO exam_data (
    round_no,
    question_no,
    question_text,
    material_text,
    choices_json,
    distractor_choices_json,
    answer_choice,
    answer_no,
    era,
    topic,
    question_type,
    question_subtype,
    q_score,
    has_image,
    image_meta_json,
    answer_explanation,
    choice_explanations_json,
    explanation_source
)
VALUES (
    %(round_no)s,
    %(question_no)s,
    %(question_text)s,
    %(material_text)s,
    %(choices_json)s,
    %(distractor_choices_json)s,
    %(answer_choice)s,
    %(answer_no)s,
    %(era)s,
    %(topic)s,
    %(question_type)s,
    %(question_subtype)s,
    %(q_score)s,
    %(has_image)s,
    %(image_meta_json)s,
    %(answer_explanation)s,
    %(choice_explanations_json)s,
    %(explanation_source)s
)
ON CONFLICT (round_no, question_no)
DO UPDATE SET
    question_text = EXCLUDED.question_text,
    material_text = EXCLUDED.material_text,
    choices_json = EXCLUDED.choices_json,
    distractor_choices_json = EXCLUDED.distractor_choices_json,
    answer_choice = EXCLUDED.answer_choice,
    answer_no = EXCLUDED.answer_no,
    era = EXCLUDED.era,
    topic = EXCLUDED.topic,
    question_type = EXCLUDED.question_type,
    question_subtype = EXCLUDED.question_subtype,
    q_score = EXCLUDED.q_score,
    has_image = EXCLUDED.has_image,
    image_meta_json = EXCLUDED.image_meta_json,
    updated_at = NOW(),
    answer_explanation = EXCLUDED.answer_explanation,
    choice_explanations_json = EXCLUDED.choice_explanations_json,
    explanation_source = EXCLUDED.explanation_source;
"""


# .env 파일을 읽어 POSTGRES_* 접속 정보를 환경변수에 채웁니다.
# python-dotenv 없이도 실행할 수 있도록 단순 key=value 형식만 처리합니다.
# 이미 환경변수에 값이 있으면 그 값을 우선 사용합니다.
def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# 공백과 줄바꿈을 정리해 DB에 넣기 쉬운 문자열로 바꿉니다.
# None 값은 빈 문자열로 처리하지 않고, 호출부에서 필요한 경우 NULL로 바꿀 수 있게 합니다.
# 문제 본문, 지문, 선지 텍스트를 동일한 기준으로 정리합니다.
def clean_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# JSON 파일을 UTF-8로 읽어 Python 객체로 반환합니다.
# exam_data 초기 적재 대상인 ML_han_v1.json은 list[dict] 구조를 기대합니다.
# 구조가 다르면 명확한 오류를 내서 잘못된 파일 적재를 막습니다.
def read_ml_rows(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"Expected list JSON: {path}")
    return rows


# ML feature CSV에서 학습에 사용한 era/topic/type 라벨을 읽습니다.
# round_no + question_no를 키로 사용해 ML_han_v1.json row와 연결합니다.
# exam_data의 초기 라벨 기준을 ML 학습 데이터와 맞추기 위한 목적입니다.
def read_feature_labels(path: Path) -> dict[tuple[int, int], dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Feature CSV not found: {path}")

    labels: dict[tuple[int, int], dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            round_no = int(row.get("round_no") or 0)
            question_no = int(row.get("question_no") or 0)
            if not round_no or not question_no:
                continue
            labels[(round_no, question_no)] = {
                "era": clean_text(row.get("era")),
                "topic": clean_text(row.get("topic")),
                "question_type": clean_text(row.get("question_type")),
                "question_subtype": clean_text(row.get("question_subtype")),
            }
    return labels


# ML_han_v1 선택지 목록에서 정답 번호를 추정합니다.
# choices[].is_answer가 있으면 그 위치를 사용하고, 없으면 answer_choice 텍스트와 비교합니다.
# 둘 다 실패하면 NULL로 두어 잘못된 정답 번호를 강제로 만들지 않습니다.
def extract_answer_no(row: dict[str, Any], choices: list[dict[str, Any]]) -> int | None:
    for index, choice in enumerate(choices, start=1):
        if bool(choice.get("is_answer")):
            return index

    answer_choice = clean_text(row.get("answer_choice"))
    if answer_choice:
        for index, choice in enumerate(choices, start=1):
            if clean_text(choice.get("content")) == answer_choice:
                return index
    return None


# difficulty_label이나 q_score 값을 exam_data.q_score 숫자로 정리합니다.
# ML_han_v1에는 q_score가 없을 수 있으므로 없으면 NULL로 둡니다.
# 값이 '상/중/하'처럼 들어오면 3/2/1로 보수적으로 매핑합니다.
def normalize_q_score(row: dict[str, Any]) -> int | None:
    raw_score = row.get("q_score")
    if raw_score not in (None, ""):
        try:
            return int(raw_score)
        except (TypeError, ValueError):
            pass

    difficulty = clean_text(row.get("difficulty_label"))
    score_map = {
        "하": 1,
        "쉬움": 1,
        "중": 2,
        "보통": 2,
        "상": 3,
        "어려움": 3,
    }
    return score_map.get(difficulty)


# 이미지 관련 메타데이터를 exam_data 컬럼 형태로 정리합니다.
# 현재 ML_han_v1에는 별도 이미지 메타가 없을 수 있으므로 data_source와 기존 키를 보조로 사용합니다.
# 실제 이미지 추출 파이프라인이 생기면 image_meta_json에 더 많은 정보를 넣으면 됩니다.
def build_image_meta(row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    meta = {}
    for key in ("image_meta", "image_meta_json", "question_image_path", "choice_image_paths"):
        if row.get(key):
            meta[key] = row[key]

    data_source = clean_text(row.get("data_source"))
    has_image = bool(meta) or "image" in data_source.lower()
    if data_source:
        meta["data_source"] = data_source
    return has_image, meta


# ML_han_v1 row 한 건을 exam_data INSERT 파라미터로 변환합니다.
# 시대/주제/유형은 ml_han_features_v1.csv의 확정 라벨을 사용합니다.
# 해설 컬럼은 아직 원천 파일에 없으므로 NULL/빈 JSON으로 둡니다.
def transform_row(row: dict[str, Any], labels: dict[str, str] | None) -> dict[str, Any]:
    choices = row.get("choices") or []
    if not isinstance(choices, list):
        choices = []

    distractors = row.get("distractor_choices") or []
    if not isinstance(distractors, list):
        distractors = []

    has_image, image_meta = build_image_meta(row)
    labels = labels or {}

    return {
        "round_no": int(row.get("round_no") or 0),
        "question_no": int(row.get("question_no") or 0),
        "question_text": clean_text(row.get("question")) or clean_text(row.get("input_text")),
        "material_text": clean_text(row.get("material")) or None,
        "choices_json": choices,
        "distractor_choices_json": distractors,
        "answer_choice": clean_text(row.get("answer_choice")) or None,
        "answer_no": extract_answer_no(row, choices),
        "era": clean_text(labels.get("era")) or None,
        "topic": clean_text(labels.get("topic")) or None,
        "question_type": clean_text(labels.get("question_type")) or clean_text(row.get("major_type")) or None,
        "question_subtype": clean_text(labels.get("question_subtype")) or clean_text(row.get("minor_type")) or None,
        "q_score": normalize_q_score(row),
        "has_image": has_image,
        "image_meta_json": image_meta,
        "answer_explanation": None,
        "choice_explanations_json": {},
        "explanation_source": None,
    }


# .env의 POSTGRES_* 값을 기반으로 psycopg2 접속 설정을 만듭니다.
# 기본값은 로컬 개발 Docker 설정에 맞춰 둡니다.
# 실제 연결은 --import-db 실행 시에만 수행합니다.
def postgres_config() -> dict[str, str]:
    return {
        "dbname": os.getenv("POSTGRES_DB", "history_rag"),
        "user": os.getenv("POSTGRES_USER", "himate"),
        "password": os.getenv("POSTGRES_PASSWORD", "himate1234"),
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
    }


# 변환된 exam_data row 목록을 PostgreSQL에 적재합니다.
# 기본은 round_no/question_no 기준 upsert이며, --truncate를 주면 exam_data만 비우고 다시 넣습니다.
# questions/question_options 같은 서비스 테이블은 건드리지 않습니다.
def import_exam_data(records: list[dict[str, Any]], truncate: bool = False) -> int:
    try:
        import psycopg2
        from psycopg2.extras import Json
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary is required. Install it before running import.") from exc

    conn = psycopg2.connect(**postgres_config())
    try:
        with conn, conn.cursor() as cur:
            cur.execute(CREATE_EXAM_DATA_SQL)
            if truncate:
                cur.execute("TRUNCATE TABLE exam_data RESTART IDENTITY")

            for record in records:
                payload = dict(record)
                payload["choices_json"] = Json(payload["choices_json"])
                payload["distractor_choices_json"] = Json(payload["distractor_choices_json"])
                payload["image_meta_json"] = Json(payload["image_meta_json"])
                payload["choice_explanations_json"] = Json(payload["choice_explanations_json"])
                cur.execute(UPSERT_EXAM_DATA_SQL, payload)
    finally:
        conn.close()
    return len(records)


# CLI 인자를 정의합니다.
# 기본 입력은 ai/ml/ML_han_v1.json 전체 1600건이며, --rounds로 일부 회차만 제한할 수 있습니다.
# --dry-run을 먼저 실행하면 DB 연결 없이 변환 건수와 샘플만 확인할 수 있습니다.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import ML_han_v1.json rows into PostgreSQL exam_data.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_JSON, help="Input ML_han_v1.json path")
    parser.add_argument(
        "--features-csv",
        type=Path,
        default=DEFAULT_FEATURE_CSV,
        help="Feature CSV path used for era/topic/question_type/question_subtype labels",
    )
    parser.add_argument("--rounds", nargs="+", type=int, help="Optional round_no filter")
    parser.add_argument("--truncate", action="store_true", help="Truncate exam_data before import")
    parser.add_argument("--dry-run", action="store_true", help="Only print converted row count and sample")
    return parser.parse_args()


# 스크립트 진입점입니다.
# DB 적재 전에 .env를 읽고, JSON을 exam_data row로 변환합니다.
# dry-run이면 DB를 건드리지 않고 샘플만 출력합니다.
def main() -> None:
    args = parse_args()
    load_dotenv_file(ROOT_DIR / ".env")

    source_rows = read_ml_rows(args.input)
    feature_labels = read_feature_labels(args.features_csv)
    if args.rounds:
        allowed_rounds = set(args.rounds)
        source_rows = [row for row in source_rows if int(row.get("round_no") or 0) in allowed_rounds]

    records = []
    missing_feature_count = 0
    for row in source_rows:
        round_no = int(row.get("round_no") or 0)
        question_no = int(row.get("question_no") or 0)
        if not round_no or not question_no:
            continue

        labels = feature_labels.get((round_no, question_no))
        if labels is None:
            missing_feature_count += 1
        records.append(transform_row(row, labels))

    records.sort(key=lambda item: (item["round_no"], item["question_no"]))

    if args.dry_run:
        print(f"source rows: {len(source_rows)}")
        print(f"converted rows: {len(records)}")
        print(f"missing feature labels: {missing_feature_count}")
        if records:
            print(json.dumps(records[0], ensure_ascii=False, indent=2))
        return

    imported_count = import_exam_data(records, truncate=args.truncate)
    print(f"imported exam_data rows: {imported_count}")


if __name__ == "__main__":
    main()

import hashlib
import re
import unicodedata
from json import load

import pandas as pd


def clean_text(text: object) -> str:
    """텍스트를 NFC로 정규화하고 공백·줄바꿈 형식을 정리한다."""
    if text is None or pd.isna(text):
        return ""

    normalized = unicodedata.normalize("NFC", str(text))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\t", " ")
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r" {2,}", " ", normalized)
    normalized = re.sub(r"\n{2,}", "\n", normalized)
    return normalized.strip()


def build_text_fields(row: pd.Series, text_policy: dict) -> pd.Series:
    """구조화 필드로 추출 본문을 만들고 원본 input_text와의 일치 상태를 판정한다."""
    separator = text_policy["field_separator"]
    statuses = text_policy["input_text_match_status"]
    material = row["material"]
    question = row["question"]
    reconstructed_stem = separator.join(
        component for component in (material, question) if component
    )

    has_required_components = bool(material and question)
    extraction_stem = reconstructed_stem
    if not has_required_components:
        extraction_stem = row["input_text"]

    choice_text = separator.join(
        choice["content"] for choice in row["choices"]
    )
    extraction_text = separator.join(
        component for component in (extraction_stem, choice_text) if component
    )

    if not has_required_components:
        match_status = statuses["component_missing"]
    elif row["input_text"] == reconstructed_stem:
        match_status = statuses["exact"]
    elif re.sub(r"\s+", "", row["input_text"]) == re.sub(
        r"\s+", "", reconstructed_stem
    ):
        match_status = statuses["whitespace_equivalent"]
    elif row["input_text"] != reconstructed_stem:
        match_status = statuses["content_conflict"]

    return pd.Series(
        {
            "reconstructed_stem": reconstructed_stem,
            "extraction_text": extraction_text,
            "full_text": extraction_text,
            "input_text_match_status": match_status,
            "text_policy_version": text_policy["version"],
        }
    )


def assign_duplicate_text_groups(
    df: pd.DataFrame,
    text_policy: dict,
) -> pd.Series:
    """완전히 같은 extraction_text에만 안정적인 중복 그룹 ID를 부여한다."""
    duplicate_mask = df["extraction_text"].duplicated(keep=False)
    group_prefix = text_policy["duplicate_group_prefix"]
    hash_algorithm = text_policy["duplicate_group_hash_algorithm"]
    digest_length = int(text_policy["duplicate_group_digest_length"])

    def create_group_id(extraction_text: str) -> str:
        digest = hashlib.new(
            hash_algorithm,
            extraction_text.encode("utf-8"),
        ).hexdigest()
        return f"{group_prefix}{digest[:digest_length]}"

    group_ids = pd.Series("", index=df.index, dtype="object")
    group_ids.loc[duplicate_mask] = df.loc[
        duplicate_mask,
        "extraction_text",
    ].map(create_group_id)
    return group_ids


def prep_json(
    json_path: str,
    text_policy: dict,
    limit: int = 0,
) -> pd.DataFrame:
    """
    기출문제 JSON을 읽어 문항 행을 보존한 추출·검증용 DataFrame을 만든다.

    material과 question이 모두 있으면 두 필드로 stem을 재구성하고, 하나라도
    없으면 input_text를 추출 stem으로 사용한다. 선지는 항상 원래 순서대로
    extraction_text에 포함한다. 텍스트 중복은 행을 삭제하지 않고 그룹만 기록한다.
    """
    with open(json_path, "r", encoding="utf-8") as source_file:
        raw = load(source_file)
    df = pd.DataFrame(raw)

    if "problem_id" not in df.columns:
        raise ValueError("기출문제 JSON에 problem_id 필드가 없습니다.")
    invalid_problem_id_mask = (
        df["problem_id"].isna()
        | df["problem_id"].astype(str).str.strip().eq("")
    )
    if invalid_problem_id_mask.any():
        raise ValueError("problem_id가 비어 있는 문항이 있습니다.")
    duplicate_problem_ids = df.loc[
        df["problem_id"].duplicated(keep=False),
        "problem_id",
    ]
    if not duplicate_problem_ids.empty:
        duplicate_ids = ", ".join(
            sorted({str(problem_id) for problem_id in duplicate_problem_ids})
        )
        raise ValueError(f"problem_id가 중복됩니다: {duplicate_ids}")

    required_columns = {
        "material",
        "question",
        "input_text",
        "answer_choice",
        "distractor_choices",
        "choices",
    }
    missing_columns = required_columns.difference(df.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"기출문제 JSON 필수 필드가 없습니다: {missing_text}")

    df["input_text_original"] = df["input_text"].fillna("").astype(str)
    text_columns = ["material", "question", "input_text", "answer_choice"]
    for column in text_columns:
        df[column] = df[column].map(clean_text)

    df["distractor_choices"] = df["distractor_choices"].map(
        lambda choices: [clean_text(choice) for choice in choices]
    )
    df["choices"] = df["choices"].map(
        lambda choices: [
            {
                "is_answer": choice["is_answer"],
                "content": clean_text(choice["content"]),
            }
            for choice in choices
        ]
    )

    text_fields = df.apply(
        build_text_fields,
        axis=1,
        text_policy=text_policy,
    )
    for column in text_fields.columns:
        df[column] = text_fields[column]
    df["duplicate_text_group_id"] = assign_duplicate_text_groups(
        df,
        text_policy,
    )
    if limit < 0:
        raise ValueError("문항 제한 개수는 0 이상이어야 합니다.")
    if limit > 0:
        df = df.head(limit)
    return df.reset_index(drop=True)

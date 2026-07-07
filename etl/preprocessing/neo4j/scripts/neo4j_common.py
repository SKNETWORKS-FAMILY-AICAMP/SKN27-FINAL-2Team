"""
Neo4j 전처리 스크립트에서 함께 쓰는 작은 유틸리티 함수 모음.

각 전처리 파일의 도메인 로직은 해당 파일에 두고, 여러 파일에서 반복되는
입출력/문자열 정리/토큰 분해 함수만 이 파일에서 관리한다.
"""

import re

import pandas as pd


def build_sequential_ids(prefix, row_count, width):
    return [f"{prefix}_{idx:0{width}d}" for idx in range(1, row_count + 1)]


def build_invalid_category_values():
    return {"", "_NULL_", "NULL", "None", "nan"}


def clean_value(value):
    if pd.isna(value):
        return pd.NA

    clean_text = str(value).strip()

    if clean_text == "":
        return pd.NA

    if clean_text.lower() == "nan":
        return pd.NA

    return clean_text


def select_existing_columns(data_frame, columns):
    existing_columns = [column for column in columns if column in data_frame.columns]
    return data_frame.loc[:, existing_columns].copy()


def first_value(values):
    for value in values:
        clean_text = clean_value(value)

        if pd.notna(clean_text):
            return clean_text

    return pd.NA


def unique_join(values):
    clean_values = []

    for value in values:
        clean_text = clean_value(value)

        if pd.notna(clean_text):
            clean_values.append(clean_text)

    unique_values = sorted(set(clean_values))

    if len(unique_values) == 0:
        return pd.NA

    return "|".join(unique_values)


def join_unique_values(values):
    joined_value = unique_join(values)

    if pd.isna(joined_value):
        return ""

    return joined_value


def split_pipe_values(value):
    if pd.isna(value):
        return []

    tokens = []

    for raw_token in str(value).split("|"):
        clean_token = clean_value(raw_token)

        if pd.notna(clean_token):
            tokens.append(clean_token)

    return tokens


def normalize_keyword_series(value_series):
    # 키워드 매칭에서는 띄어쓰기, 가운뎃점, 마침표 표기 차이를 무시한다.
    return (
        value_series.fillna("")
        .astype(str)
        .str.strip()
        .str.replace(r"[\s·.]+", "", regex=True)
    )


def split_category_paths(term_lk):
    if pd.isna(term_lk):
        return []

    category_paths = []
    invalid_values = build_invalid_category_values()

    for raw_path in str(term_lk).split(">>"):
        path_parts = []

        for path_part in raw_path.split(">"):
            clean_path_part = path_part.strip()

            if clean_path_part not in invalid_values:
                path_parts.append(clean_path_part)

        if len(path_parts) > 0:
            category_paths.append(path_parts)

    return category_paths


def split_event_category_tokens(subject_category):
    if pd.isna(subject_category):
        return []

    cleaned_text = str(subject_category).replace("\r", "\n")
    raw_tokens = re.split(r",|\n+", cleaned_text)
    tokens = []

    for raw_token in raw_tokens:
        clean_token = raw_token.strip()

        if clean_token != "":
            tokens.append(clean_token)

    return tokens


def split_period_tokens(period_text):
    if pd.isna(period_text):
        return []

    tokens = []

    for raw_token in re.split(r"-|,|~|∼", str(period_text)):
        clean_token = clean_value(raw_token)

        if pd.isna(clean_token):
            continue

        # "?"는 미상 표기라 시대명이 아니다. Period 노드로 만들지 않는다.
        if clean_token == "?":
            continue

        tokens.append(clean_token)

    return tokens


def require_file(input_path, purpose, guidance=""):
    if not input_path.exists():
        message = f"{purpose} 파일이 없습니다: {input_path}"

        if guidance != "":
            message = f"{message}\n{guidance}"

        raise FileNotFoundError(message)


def read_csv(input_path, purpose):
    require_file(input_path, purpose)
    return pd.read_csv(input_path, dtype=str)


def read_optional_csv(input_path, purpose):
    # 검수 후보 파일처럼 아직 없을 수 있는 입력은 빈 DataFrame으로 대체한다.
    if input_path.exists():
        return read_csv(input_path, purpose)

    return pd.DataFrame()


def save_csv(data_frame, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_frame.to_csv(output_path, index=False, encoding="utf-8-sig")


def remove_stale_output_file(output_path):
    if output_path.exists():
        output_path.unlink()


def build_discontinued_relation_output_names():
    # 더 이상 생성하지 않는 관계 CSV 이름. graph/theme 스크립트가 함께 사용한다.
    return {
        "person_has_evidence_url",
    }


def print_summary(file_name, data_frame):
    print(f"{file_name}: {len(data_frame)} rows, {len(data_frame.columns)} columns")


def resolve_neo4j_dir(script_path):
    script_dir = script_path.parent
    neo4j_dir = script_dir

    if script_dir.name == "scripts":
        neo4j_dir = script_dir.parent

    return neo4j_dir


def resolve_import_dir(project_root):
    # Neo4j 적재용 최종 CSV가 모이는 폴더. 경로 규칙은 여기 한 곳에서만 관리한다.
    return project_root / "storage" / "neo4j" / "neo4j_import"


def resolve_project_root(start_path):
    for parent_path in [start_path, *start_path.parents]:
        if (parent_path / ".git").exists() and (parent_path / "etl").exists():
            return parent_path

    raise FileNotFoundError(f"프로젝트 루트를 찾을 수 없습니다: {start_path}")

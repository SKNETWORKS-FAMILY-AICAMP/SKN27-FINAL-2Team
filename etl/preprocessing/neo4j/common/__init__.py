from __future__ import annotations

import re
import unicodedata
from hashlib import new as new_hash
from json import load
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI


def load_pipeline_policy(policy_path: str) -> dict:
    """버전이 명시된 전처리·판별 정책 JSON을 읽고 필수 구성을 검증한다."""
    path = Path(policy_path)
    if not path.is_file():
        raise FileNotFoundError(f"판별 정책 파일을 찾을 수 없습니다: {path}")

    with path.open("r", encoding="utf-8") as policy_file:
        policy = load(policy_file)

    required_sections = {
        "policy_version",
        "normalization_policy_version",
        "source_release",
        "term_extraction",
        "output_layout",
        "candidate_retrieval",
        "definition_scan",
        "entity_resolution",
        "coverage",
        "noise",
        "category_compatibility",
    }
    missing_sections = required_sections.difference(policy)
    if missing_sections:
        missing_text = ", ".join(sorted(missing_sections))
        raise ValueError(f"판별 정책에 필수 구성이 없습니다: {missing_text}")
    return policy


def calculate_source_release(source_path: str, release_policy: dict) -> str:
    """원천 파일 내용 해시로 재실행 가능한 release 식별자를 만든다."""
    algorithm = release_policy["hash_algorithm"]
    digest_length = release_policy["digest_length"]
    chunk_size = release_policy["chunk_size_bytes"]
    source_file = Path(source_path)
    if not source_file.is_file():
        raise FileNotFoundError(f"release를 계산할 원천 파일이 없습니다: {source_file}")

    hasher = new_hash(algorithm)
    with source_file.open("rb") as input_file:
        chunk = input_file.read(chunk_size)
        while chunk:
            hasher.update(chunk)
            chunk = input_file.read(chunk_size)
    return f"{algorithm}-{hasher.hexdigest()[:digest_length]}"


def get_historyterm_llm(model_config: dict) -> ChatOpenAI:
    """기출문제에서 역사 용어를 추출하는 LLM을 반환한다."""
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    load_dotenv()
    required_fields = {"model", "temperature", "reasoning_effort"}
    missing_fields = required_fields.difference(model_config)
    if missing_fields:
        missing_text = ", ".join(sorted(missing_fields))
        raise ValueError(f"용어 추출 모델 설정이 없습니다: {missing_text}")

    llm = ChatOpenAI(
        model=model_config["model"],
        temperature=model_config["temperature"],
        reasoning_effort=model_config["reasoning_effort"],
    )
    return llm


def normalize_history_term(term: str) -> str:
    """용어 비교용으로 유니코드와 공백 차이를 제거한다."""
    normalized = unicodedata.normalize("NFC", str(term)).casefold()
    return re.sub(r"\s+", "", normalized)

from __future__ import annotations

from collections import Counter, defaultdict
from csv import DictReader, field_size_limit
from hashlib import new as new_hash
from itertools import product
from json import JSONDecodeError, dumps, load, loads
from pathlib import Path
import re
import unicodedata
from typing import Iterable, Iterator

import pandas as pd

from choice_relation.source_first_fact_eda import (
    collect_asserted_actions,
)
from choice_relation.relation_frames import classify_mention_role
from fact_retrieval.build import parse_json_list


class IndexedSurfaceMatcher:
    """문자 트라이로 여러 역사 용어를 한 번에 찾는다."""

    def __init__(
        self,
        surface_keys: Iterable[str],
        following_particles: list[str],
    ) -> None:
        trie: dict[str, object] = {}
        for surface_key in sorted(set(surface_keys)):
            if not surface_key:
                continue
            node = trie
            for character in surface_key:
                next_node = node.get(character)
                if not isinstance(next_node, dict):
                    next_node = {}
                    node[character] = next_node
                node = next_node
            node[""] = surface_key
        self.trie = trie
        self.following_particles = sorted(
            {
                compact_surface(value)
                for value in following_particles
                if compact_surface(value)
            },
            key=lambda value: (-len(value), value),
        )

    def find(
        self,
        text: str,
        span_start: int = 0,
        span_end: int | None = None,
    ) -> list[dict]:
        """지정 구간에서 경계가 안전한 최장 용어 언급을 찾는다."""
        effective_end = len(text)
        if span_end is not None:
            effective_end = span_end
        compact_characters: list[str] = []
        original_positions: list[int] = []
        for position in range(span_start, effective_end):
            character = text[position]
            if character.isspace():
                continue
            compact_characters.append(
                unicodedata.normalize("NFC", character).casefold()
            )
            original_positions.append(position)
        compact_text = "".join(compact_characters)
        candidates: list[dict] = []
        for compact_start in range(len(compact_text)):
            node = self.trie
            compact_end = compact_start
            while compact_end < len(compact_text):
                next_node = node.get(compact_text[compact_end])
                if not isinstance(next_node, dict):
                    break
                node = next_node
                compact_end += 1
                surface_key = node.get("")
                if not isinstance(surface_key, str):
                    continue
                original_start = original_positions[compact_start]
                original_end = (
                    original_positions[compact_end - 1] + 1
                )
                if not self._has_safe_start_boundary(
                    text,
                    compact_text,
                    original_positions,
                    compact_start,
                    original_start,
                ):
                    continue
                if not self._has_safe_end_boundary(
                    text,
                    compact_text,
                    original_positions,
                    compact_end,
                    original_end,
                ):
                    continue
                candidates.append(
                    {
                        "surface_key": surface_key,
                        "mention_start": original_start,
                        "mention_end": original_end,
                        "mention_text": text[
                            original_start:original_end
                        ],
                    }
                )
        candidates.sort(
            key=lambda row: (
                int(row["mention_start"]),
                -(
                    int(row["mention_end"])
                    - int(row["mention_start"])
                ),
                str(row["surface_key"]),
            )
        )
        matches: list[dict] = []
        for candidate in candidates:
            overlaps = any(
                int(candidate["mention_start"])
                < int(observed["mention_end"])
                and int(candidate["mention_end"])
                > int(observed["mention_start"])
                for observed in matches
            )
            if overlaps:
                continue
            matches.append(candidate)
        return matches

    def _has_safe_start_boundary(
        self,
        text: str,
        compact_text: str,
        original_positions: list[int],
        compact_start: int,
        original_start: int,
    ) -> bool:
        if compact_start == 0:
            return True
        previous_position = original_positions[compact_start - 1]
        if original_start > previous_position + 1:
            return True
        return not compact_text[compact_start - 1].isalnum()

    def _has_safe_end_boundary(
        self,
        text: str,
        compact_text: str,
        original_positions: list[int],
        compact_end: int,
        original_end: int,
    ) -> bool:
        if compact_end >= len(compact_text):
            return True
        next_position = original_positions[compact_end]
        if next_position > original_end:
            return True
        next_character = compact_text[compact_end]
        if not next_character.isalnum():
            return True
        suffix = compact_text[compact_end:]
        return any(
            suffix.startswith(particle)
            for particle in self.following_particles
        )


def compact_surface(value: object) -> str:
    """공백 차이를 제거한 용어 매칭 키를 만든다."""
    normalized = unicodedata.normalize(
        "NFC",
        str(value or ""),
    ).casefold()
    return re.sub(r"\s+", "", normalized)


def load_exam_term_raw_relation_policy(
    eda_policy_path: str,
    relation_policy_path: str,
    resolution_policy_path: str,
    source_first_policy_path: str,
) -> dict:
    """원문 관계 EDA에 필요한 정책을 하나로 묶는다."""
    with open(eda_policy_path, "r", encoding="utf-8") as input_file:
        eda_policy = load(input_file)
    with open(
        relation_policy_path,
        "r",
        encoding="utf-8",
    ) as input_file:
        relation_policy = load(input_file)
    with open(
        resolution_policy_path,
        "r",
        encoding="utf-8",
    ) as input_file:
        resolution_policy = load(input_file)
    with open(
        source_first_policy_path,
        "r",
        encoding="utf-8",
    ) as input_file:
        source_first_policy = load(input_file)
    return {
        "exam_term_raw_relation_eda": eda_policy,
        "exam_relation_candidates": relation_policy[
            "exam_relation_candidates"
        ],
        "exam_relation_frames": relation_policy[
            "exam_relation_frames"
        ],
        "entity_resolution": resolution_policy[
            "entity_resolution"
        ],
        "auto_accept_role_type_contracts": source_first_policy[
            "auto_accept_role_type_contracts"
        ],
    }


def create_identifier(
    prefix: str,
    values: list[str],
    policy: dict,
) -> str:
    """설정된 해시 규칙으로 재실행 가능한 ID를 만든다."""
    identifier_policy = policy["exam_term_raw_relation_eda"][
        "identifier"
    ]
    hasher = new_hash(str(identifier_policy["hash_algorithm"]))
    hasher.update("\u241f".join(values).encode("utf-8"))
    return (
        f"{prefix}"
        f"{hasher.hexdigest()[:int(identifier_policy['digest_length'])]}"
    )


def parse_json_object(value: object) -> dict:
    """CSV의 JSON 객체를 안전하게 읽는다."""
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = loads(text)
    except (JSONDecodeError, TypeError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def strip_parenthetical_hanja(
    value: object,
    policy: dict,
) -> str:
    """공식 이름의 괄호 한자 병기를 제거한 별칭을 만든다."""
    pattern = str(
        policy["exam_term_raw_relation_eda"][
            "parenthetical_hanja_pattern"
        ]
    )
    return re.sub(pattern, "", str(value or "")).strip()


def infer_aks_source_release(
    canonical_registry: pd.DataFrame,
    policy: dict,
) -> str:
    """Canonical identity member에서 현재 AKS release를 확인한다."""
    releases: Counter = Counter()
    source_prefix = str(
        policy["exam_term_raw_relation_eda"][
            "aks_source_record_prefix"
        ]
    )
    for row in canonical_registry.to_dict("records"):
        for source_record_id in parse_json_list(
            row.get("identity_member_source_ids_json", "[]")
        ):
            if not source_record_id.startswith(source_prefix):
                continue
            parts = source_record_id.split(":")
            if len(parts) >= 4:
                releases[parts[3]] += 1
    if not releases:
        raise ValueError(
            "Canonical registry에서 AKS source release를 찾지 못했습니다."
        )
    return str(releases.most_common(1)[0][0])


def build_source_resolution_index(
    canonical_registry: pd.DataFrame,
    source_resolutions: pd.DataFrame,
    policy: dict,
) -> dict[str, str]:
    """승인된 SourceRecord→CanonicalEntity 인덱스를 만든다."""
    eda_policy = policy["exam_term_raw_relation_eda"]
    accepted_status = str(eda_policy["accepted_exam_status"])
    canonical_by_source_id: dict[str, str] = {}
    for row in source_resolutions.to_dict("records"):
        if str(row["match_status"]) != accepted_status:
            continue
        source_record_id = str(row["source_record_id"])
        canonical_id = str(row["canonical_id"])
        previous_id = canonical_by_source_id.get(source_record_id)
        if previous_id and previous_id != canonical_id:
            raise ValueError(
                "하나의 SourceRecord가 여러 CanonicalEntity에 "
                f"승인됐습니다: {source_record_id}"
            )
        canonical_by_source_id[source_record_id] = canonical_id
    for row in canonical_registry.to_dict("records"):
        if (
            str(row["lifecycle_status"])
            != str(eda_policy["accepted_registry_status"])
        ):
            continue
        canonical_id = str(row["canonical_id"])
        for source_record_id in parse_json_list(
            row.get("identity_member_source_ids_json", "[]")
        ):
            previous_id = canonical_by_source_id.get(source_record_id)
            if previous_id and previous_id != canonical_id:
                raise ValueError(
                    "Canonical registry와 source resolution이 충돌합니다: "
                    f"{source_record_id}"
                )
            canonical_by_source_id[source_record_id] = canonical_id
    return canonical_by_source_id


def build_exam_endpoint_groups(
    exam_term_matches: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    policy: dict,
) -> tuple[dict[str, dict], dict[str, int]]:
    """기출 용어를 Canonical 또는 미해결 기출 노드 endpoint로 만든다."""
    eda_policy = policy["exam_term_raw_relation_eda"]
    entity_type_mapping = policy["entity_resolution"][
        "entity_type_mapping"
    ]
    registry_by_id = {
        str(row["canonical_id"]): row
        for row in canonical_registry.to_dict("records")
        if str(row["lifecycle_status"])
        == str(eda_policy["accepted_registry_status"])
    }
    groups: dict[str, dict] = {}
    excluded_short_count = 0
    ambiguous_surface_count = 0
    for row in exam_term_matches.to_dict("records"):
        term = str(row["term"]).strip()
        surface_key = compact_surface(term)
        if len(surface_key) < int(
            eda_policy["minimum_surface_length"]
        ):
            excluded_short_count += 1
            continue
        categories = parse_json_list(row["categories_json"])
        entity_types = {
            str(entity_type_mapping[category])
            for category in categories
            if category in entity_type_mapping
        }
        entity_type = "Unknown"
        if len(entity_types) == 1:
            entity_type = next(iter(entity_types))
        projected_ids = parse_json_list(
            row["projected_canonical_ids_json"]
        )
        node_kind = str(
            eda_policy["target_node_kinds"]["exam_term"]
        )
        endpoint_id = str(row["exam_term_id"])
        resolution_status = str(
            row["projected_source_link_status"]
        )
        canonical_id = ""
        if (
            resolution_status
            == str(eda_policy["accepted_exam_status"])
            and len(projected_ids) == 1
            and projected_ids[0] in registry_by_id
        ):
            canonical_id = projected_ids[0]
            endpoint_id = canonical_id
            node_kind = str(
                eda_policy["target_node_kinds"]["canonical"]
            )
            entity_type = str(
                registry_by_id[canonical_id]["entity_type"]
            )
        endpoint = {
            "endpoint_id": endpoint_id,
            "node_kind": node_kind,
            "canonical_id": canonical_id,
            "source_record_id": "",
            "display_name": term,
            "entity_type": entity_type,
            "resolution_status": resolution_status,
            "source": "EXAM_TERM",
            "source_url": "",
            "is_exam_term": True,
            "exam_term_id": str(row["exam_term_id"]),
        }
        observed = groups.get(surface_key)
        if observed is None:
            groups[surface_key] = {
                "surface_key": surface_key,
                "display_surface": term,
                "endpoints": [endpoint],
                "endpoint_count": 1,
            }
            continue
        endpoint_ids = {
            str(value["endpoint_id"])
            for value in observed["endpoints"]
        }
        if endpoint_id not in endpoint_ids:
            observed["endpoints"].append(endpoint)
            observed["endpoint_count"] = len(
                observed["endpoints"]
            )
            ambiguous_surface_count += 1
    return groups, {
        "input_exam_term_count": len(exam_term_matches),
        "matchable_exam_surface_count": len(groups),
        "excluded_short_exam_term_count": excluded_short_count,
        "ambiguous_exam_surface_count": ambiguous_surface_count,
    }


def add_target_surface(
    records_by_surface: dict[str, dict[str, dict]],
    surface: object,
    endpoint: dict,
    policy: dict,
) -> None:
    """하나의 공식 이름 변형을 target lexicon에 추가한다."""
    surface_text = str(surface or "").strip()
    surface_key = compact_surface(surface_text)
    if len(surface_key) < int(
        policy["exam_term_raw_relation_eda"][
            "minimum_surface_length"
        ]
    ):
        return
    identity_key = (
        f"CANONICAL:{endpoint['canonical_id']}"
        if str(endpoint["canonical_id"])
        else f"SOURCE:{endpoint['source_record_id']}"
    )
    records_by_surface[surface_key][identity_key] = {
        **endpoint,
        "matched_surface": surface_text,
    }


def build_target_endpoint_groups(
    canonical_registry: pd.DataFrame,
    source_nodes: pd.DataFrame,
    source_resolutions: pd.DataFrame,
    aks_articles_list_path: str,
    aks_source_release: str,
    policy: dict,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Canonical과 전체 공식 SourceRecord 이름으로 target 사전을 만든다."""
    eda_policy = policy["exam_term_raw_relation_eda"]
    canonical_by_source_id = build_source_resolution_index(
        canonical_registry,
        source_resolutions,
        policy,
    )
    registry_by_id = {
        str(row["canonical_id"]): row
        for row in canonical_registry.to_dict("records")
        if str(row["lifecycle_status"])
        == str(eda_policy["accepted_registry_status"])
    }
    records_by_surface: dict[
        str,
        dict[str, dict],
    ] = defaultdict(dict)
    for canonical_id, row in registry_by_id.items():
        endpoint = {
            "endpoint_id": canonical_id,
            "node_kind": str(
                eda_policy["target_node_kinds"]["canonical"]
            ),
            "canonical_id": canonical_id,
            "source_record_id": "",
            "display_name": str(row["display_name"]),
            "entity_type": str(row["entity_type"]),
            "resolution_status": "RESOLVED_CANONICAL",
            "source": "CANONICAL_REGISTRY",
            "source_url": "",
            "is_exam_term": False,
            "exam_term_id": "",
        }
        add_target_surface(
            records_by_surface,
            row["display_name"],
            endpoint,
            policy,
        )
    source_type_mapping = eda_policy[
        "source_record_type_to_entity_type"
    ]
    for row in source_nodes.to_dict("records"):
        source_record_id = str(row["source_record_id"])
        canonical_id = canonical_by_source_id.get(
            source_record_id,
            "",
        )
        node_kind = str(
            eda_policy["target_node_kinds"]["official_source"]
        )
        endpoint_id = source_record_id
        resolution_status = "UNRESOLVED_OFFICIAL_SOURCE"
        entity_type = str(
            source_type_mapping.get(
                str(row["record_type"]),
                "Concept",
            )
        )
        display_name = str(row["display_name"]).strip()
        if canonical_id in registry_by_id:
            node_kind = str(
                eda_policy["target_node_kinds"]["canonical"]
            )
            endpoint_id = canonical_id
            resolution_status = "RESOLVED_CANONICAL"
            entity_type = str(
                registry_by_id[canonical_id]["entity_type"]
            )
            display_name = str(
                registry_by_id[canonical_id]["display_name"]
            )
        endpoint = {
            "endpoint_id": endpoint_id,
            "node_kind": node_kind,
            "canonical_id": canonical_id,
            "source_record_id": source_record_id,
            "display_name": display_name,
            "entity_type": entity_type,
            "resolution_status": resolution_status,
            "source": str(row["source"]),
            "source_url": str(row["source_urls_json"]),
            "is_exam_term": False,
            "exam_term_id": "",
        }
        add_target_surface(
            records_by_surface,
            row["display_name"],
            endpoint,
            policy,
        )
        base_name = strip_parenthetical_hanja(
            row["display_name"],
            policy,
        )
        if (
            base_name != str(row["display_name"]).strip()
            and len(compact_surface(base_name))
            >= int(eda_policy["minimum_derived_alias_length"])
        ):
            add_target_surface(
                records_by_surface,
                base_name,
                endpoint,
                policy,
            )
    aks_type_mapping = policy["entity_resolution"][
        "source_feature_policy"
    ]["sources"]["AKS"]["type_mapping"]
    with open(
        aks_articles_list_path,
        "r",
        encoding="utf-8",
    ) as input_file:
        for line in input_file:
            text = line.strip()
            if not text:
                continue
            article = loads(text)
            eid = str(article["eid"])
            source_record_id = (
                f"{eda_policy['aks_source_record_prefix']}"
                f"{eid}:{aks_source_release}"
            )
            canonical_id = canonical_by_source_id.get(
                source_record_id,
                "",
            )
            node_kind = str(
                eda_policy["target_node_kinds"]["official_source"]
            )
            endpoint_id = source_record_id
            resolution_status = "UNRESOLVED_OFFICIAL_SOURCE"
            primary_type_part = str(
                article.get("primaryTypePartA")
                or article.get("primaryType", "").split("/", 1)[0]
            )
            entity_type = str(
                aks_type_mapping.get(
                    primary_type_part,
                    "Concept",
                )
            )
            display_name = str(article.get("headword") or "").strip()
            if canonical_id in registry_by_id:
                node_kind = str(
                    eda_policy["target_node_kinds"]["canonical"]
                )
                endpoint_id = canonical_id
                resolution_status = "RESOLVED_CANONICAL"
                entity_type = str(
                    registry_by_id[canonical_id]["entity_type"]
                )
                display_name = str(
                    registry_by_id[canonical_id]["display_name"]
                )
            endpoint = {
                "endpoint_id": endpoint_id,
                "node_kind": node_kind,
                "canonical_id": canonical_id,
                "source_record_id": source_record_id,
                "display_name": display_name,
                "entity_type": entity_type,
                "resolution_status": resolution_status,
                "source": "AKS",
                "source_url": str(article.get("url") or ""),
                "is_exam_term": False,
                "exam_term_id": "",
            }
            add_target_surface(
                records_by_surface,
                article.get("headword", ""),
                endpoint,
                policy,
            )
            add_target_surface(
                records_by_surface,
                article.get("headwordOrigin", ""),
                endpoint,
                policy,
            )
            for alias in article.get("articleAliases") or []:
                if isinstance(alias, dict):
                    add_target_surface(
                        records_by_surface,
                        alias.get("word", ""),
                        endpoint,
                        policy,
                    )
    groups: dict[str, dict] = {}
    unique_canonical_count = 0
    unique_source_count = 0
    ambiguous_count = 0
    overloaded_count = 0
    maximum_ambiguous_records = int(
        eda_policy["maximum_ambiguous_target_records"]
    )
    for surface_key, identity_records in records_by_surface.items():
        endpoints = list(identity_records.values())
        resolution_status = "AMBIGUOUS_OFFICIAL_NAME"
        if len(endpoints) == 1:
            resolution_status = str(
                endpoints[0]["resolution_status"]
            )
            if endpoints[0]["canonical_id"]:
                unique_canonical_count += 1
            elif endpoints[0]["source_record_id"]:
                unique_source_count += 1
        elif len(endpoints) > maximum_ambiguous_records:
            overloaded_count += 1
        elif len(endpoints) > 1:
            ambiguous_count += 1
        groups[surface_key] = {
            "surface_key": surface_key,
            "display_surface": str(
                endpoints[0]["matched_surface"]
            ),
            "endpoints": endpoints,
            "endpoint_count": len(endpoints),
            "resolution_status": resolution_status,
            "match_enabled": (
                len(endpoints) <= maximum_ambiguous_records
            ),
        }
    return groups, {
        "target_surface_count": len(groups),
        "unique_canonical_target_surface_count": (
            unique_canonical_count
        ),
        "unique_official_source_target_surface_count": (
            unique_source_count
        ),
        "ambiguous_target_surface_count": ambiguous_count,
        "overloaded_target_surface_count": overloaded_count,
    }


def iter_dataset_documents(
    raw_data_root: str,
    dataset_policy: dict,
    aks_source_release: str,
    maximum_csv_field_size: int,
    document_limit: int | None = None,
) -> Iterator[dict]:
    """설정된 JSONL·CSV 원천을 문서 레코드로 스트리밍한다."""
    if not bool(dataset_policy["enabled"]):
        return
    raw_root = Path(raw_data_root)
    source_format = str(dataset_policy["format"])
    configured_path = str(dataset_policy["path"])
    paths: list[Path] = []
    if source_format == "csv_glob":
        paths = sorted(raw_root.glob(configured_path))
    elif source_format in {"csv", "jsonl"}:
        paths = [raw_root / configured_path]
    emitted_count = 0
    for source_path in paths:
        if not source_path.is_file():
            raise FileNotFoundError(
                f"원문 데이터 파일이 없습니다: {source_path}"
            )
        if source_format == "jsonl":
            with source_path.open(
                "r",
                encoding="utf-8",
            ) as input_file:
                for line in input_file:
                    text = line.strip()
                    if not text:
                        continue
                    row = loads(text)
                    yield build_source_document(
                        row,
                        source_path,
                        dataset_policy,
                        aks_source_release,
                    )
                    emitted_count += 1
                    if (
                        document_limit is not None
                        and emitted_count >= document_limit
                    ):
                        return
            continue
        previous_limit = field_size_limit()
        try:
            field_size_limit(
                maximum_csv_field_size
            )
            with source_path.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as input_file:
                for row in DictReader(input_file):
                    yield build_source_document(
                        row,
                        source_path,
                        dataset_policy,
                        aks_source_release,
                    )
                    emitted_count += 1
                    if (
                        document_limit is not None
                        and emitted_count >= document_limit
                    ):
                        return
        finally:
            field_size_limit(previous_limit)


def build_source_document(
    row: dict,
    source_path: Path,
    dataset_policy: dict,
    aks_source_release: str,
) -> dict:
    """원천별 필드를 공통 문서 구조로 변환한다."""
    record_id = str(
        row.get(str(dataset_policy["record_id_field"])) or ""
    ).strip()
    source_document_id = (
        f"{dataset_policy['source_id_prefix']}{record_id}"
    )
    if str(dataset_policy["name"]) == "AKS":
        source_document_id = (
            f"{source_document_id}:{aks_source_release}"
        )
    url_field = str(dataset_policy["url_field"])
    source_url = ""
    if url_field:
        source_url = str(row.get(url_field) or "")
    return {
        "source_dataset": str(dataset_policy["name"]),
        "source_document_id": source_document_id,
        "source_record_key": record_id,
        "source_title": str(
            row.get(str(dataset_policy["title_field"])) or ""
        ),
        "source_url": source_url,
        "source_path": str(source_path),
        "trust_tier": str(dataset_policy["trust_tier"]),
        "supports_linked_entities": bool(
            dataset_policy.get("supports_linked_entities", False)
        ),
        "text_fields": {
            str(field): str(row.get(str(field)) or "")
            for field in dataset_policy["text_fields"]
        },
    }


def extract_document_sentences(
    document: dict,
    policy: dict,
) -> list[dict]:
    """문서 텍스트를 제목 추론 없이 명시 문장으로만 분리한다."""
    eda_policy = policy["exam_term_raw_relation_eda"]
    whitespace_pattern = re.compile(
        str(eda_policy["whitespace_pattern"])
    )
    sentence_pattern = re.compile(
        str(eda_policy["sentence_split_pattern"])
    )
    linked_pattern = re.compile(
        str(eda_policy["linked_entity_pattern"])
    )
    maximum_length = int(eda_policy["maximum_sentence_length"])
    sentences: list[dict] = []
    observed: set[str] = set()
    for field_name, field_text in document["text_fields"].items():
        normalized_text = whitespace_pattern.sub(
            " ",
            str(field_text),
        ).strip()
        if not normalized_text:
            continue
        for value in sentence_pattern.split(normalized_text):
            linked_surface_keys: set[str] = set()
            for linked_label in linked_pattern.findall(value):
                linked_surface_keys.add(
                    compact_surface(linked_label)
                )
                linked_surface_keys.add(
                    compact_surface(
                        strip_parenthetical_hanja(
                            linked_label,
                            policy,
                        )
                    )
                )
            cleaned_value = linked_pattern.sub(r"\1", value)
            sentence = cleaned_value.strip(" #\t")
            if not sentence or len(sentence) > maximum_length:
                continue
            normalized_key = compact_surface(sentence)
            if normalized_key in observed:
                continue
            observed.add(normalized_key)
            sentences.append(
                {
                    "source_field": str(field_name),
                    "sentence": sentence,
                    "linked_surface_keys": sorted(
                        linked_surface_keys
                    ),
                }
            )
    return sentences


def endpoint_from_group(
    group: dict,
) -> dict | None:
    """이름이 하나의 공식 identity를 가리킬 때만 endpoint를 반환한다."""
    if int(group["endpoint_count"]) != 1:
        return None
    return dict(group["endpoints"][0])


def build_role_mentions(
    sentence: str,
    clause_span: tuple[int, int],
    action: dict,
    exam_matches: list[dict],
    target_matches: list[dict],
    exam_groups: dict[str, dict],
    target_groups: dict[str, dict],
    linked_surface_keys: set[str],
    supports_linked_entities: bool,
    policy: dict,
) -> tuple[list[dict], list[dict]]:
    """절 안의 명시 endpoint를 역할과 함께 만들고 모호성을 분리한다."""
    clause_start, clause_end = clause_span
    mentions: list[dict] = []
    ambiguous_mentions: list[dict] = []
    exam_spans = {
        (
            int(match["mention_start"]),
            int(match["mention_end"]),
        )
        for match in exam_matches
    }
    for match in exam_matches:
        group = exam_groups[str(match["surface_key"])]
        endpoint = endpoint_from_group(group)
        if endpoint is None:
            ambiguous_mentions.append(
                {
                    **match,
                    "reason": "AMBIGUOUS_EXAM_ENDPOINT",
                    "candidate_count": int(
                        group["endpoint_count"]
                    ),
                }
            )
            continue
        suffix = sentence[
            int(match["mention_end"]):clause_end
        ]
        role, role_basis = classify_mention_role(
            suffix,
            str(endpoint["entity_type"]),
            str(action["voice"]),
            policy,
        )
        mentions.append(
            {
                **match,
                **endpoint,
                "participant_role": role,
                "role_basis": role_basis,
                "source_link_verified": (
                    str(match["surface_key"])
                    in linked_surface_keys
                ),
                "linked_annotations_available": (
                    supports_linked_entities
                ),
            }
        )
    for match in target_matches:
        match_span = (
            int(match["mention_start"]),
            int(match["mention_end"]),
        )
        if any(
            match_span[0] < exam_span[1]
            and match_span[1] > exam_span[0]
            for exam_span in exam_spans
        ):
            continue
        group = target_groups[str(match["surface_key"])]
        endpoint = endpoint_from_group(group)
        if endpoint is None:
            ambiguous_mentions.append(
                {
                    **match,
                    "reason": "AMBIGUOUS_TARGET_ENDPOINT",
                    "candidate_count": int(
                        group["endpoint_count"]
                    ),
                }
            )
            continue
        suffix = sentence[
            int(match["mention_end"]):clause_end
        ]
        role, role_basis = classify_mention_role(
            suffix,
            str(endpoint["entity_type"]),
            str(action["voice"]),
            policy,
        )
        mentions.append(
            {
                **match,
                **endpoint,
                "participant_role": role,
                "role_basis": role_basis,
                "source_link_verified": (
                    str(match["surface_key"])
                    in linked_surface_keys
                ),
                "linked_annotations_available": (
                    supports_linked_entities
                ),
            }
        )
    mentions = [
        mention
        for mention in mentions
        if int(mention["mention_start"]) >= clause_start
        and int(mention["mention_end"]) <= int(action["action_start"])
    ]
    deduplicated: dict[tuple[str, int, int], dict] = {}
    for mention in mentions:
        key = (
            str(mention["endpoint_id"]),
            int(mention["mention_start"]),
            int(mention["mention_end"]),
        )
        deduplicated[key] = mention
    return list(deduplicated.values()), ambiguous_mentions


def normalize_open_endpoint_surface(
    raw_surface: str,
    policy: dict,
) -> str:
    """조사 앞에서 잡힌 명사구를 출처 보존형 endpoint 이름으로 정리한다."""
    extraction_policy = policy["exam_term_raw_relation_eda"][
        "open_endpoint_extraction"
    ]
    fragments = re.split(
        str(extraction_policy["phrase_left_boundary_pattern"]),
        raw_surface,
    )
    surface = ""
    for fragment in reversed(fragments):
        candidate = str(fragment).strip()
        if candidate:
            surface = candidate
            break
    bracket_pairs = [
        ("(", ")"),
        ("（", "）"),
        ("[", "]"),
        ("『", "』"),
        ("「", "」"),
        ("《", "》"),
        ("≪", "≫"),
    ]
    if any(
        surface.count(opening) != surface.count(closing)
        for opening, closing in bracket_pairs
    ):
        return ""
    surface = re.sub(
        str(extraction_policy["phrase_leading_noise_pattern"]),
        "",
        surface,
    )
    surface = re.sub(
        str(extraction_policy["phrase_trailing_noise_pattern"]),
        "",
        surface,
    )
    surface = strip_parenthetical_hanja(surface, policy)
    surface = surface.strip(
        str(extraction_policy["phrase_edge_strip_characters"])
    )
    surface = re.sub(r"\s+", " ", surface).strip()
    compact = compact_surface(surface)
    if len(compact) < int(
        extraction_policy["minimum_surface_length"]
    ):
        return ""
    if len(compact) > int(
        extraction_policy["maximum_surface_length"]
    ):
        return ""
    if len(surface.split()) > int(
        extraction_policy["maximum_surface_word_count"]
    ):
        return ""
    if not re.search(r"[가-힣A-Za-z一-龥]", surface):
        return ""
    if any(
        re.fullmatch(str(pattern), surface)
        for pattern in extraction_policy[
            "blocked_surface_patterns"
        ]
    ):
        return ""
    return surface


def extract_open_role_mention(
    sentence: str,
    search_span: tuple[int, int],
    role: str,
    predicate_family: str,
    pattern: str,
    document: dict,
    source_field: str,
    linked_surface_keys: set[str],
    supports_linked_entities: bool,
    occupied_spans: list[tuple[int, int]],
    policy: dict,
) -> dict | None:
    """조사로 역할이 명시된 미등록 명사구를 출처별 후보 노드로 만든다."""
    span_start, span_end = search_span
    if span_end <= span_start:
        return None
    segment = sentence[span_start:span_end]
    matches = list(re.finditer(str(pattern), segment))
    extraction_policy = policy["exam_term_raw_relation_eda"][
        "open_endpoint_extraction"
    ]
    for match in reversed(matches):
        raw_surface = str(match.group("surface"))
        surface = normalize_open_endpoint_surface(
            raw_surface,
            policy,
        )
        if not surface:
            continue
        relative_start = raw_surface.rfind(surface)
        if relative_start < 0:
            continue
        mention_start = (
            span_start
            + int(match.start("surface"))
            + relative_start
        )
        mention_end = mention_start + len(surface)
        if any(
            mention_start < occupied_end
            and mention_end > occupied_start
            for occupied_start, occupied_end in occupied_spans
        ):
            continue
        type_key = f"{predicate_family}:{role}"
        entity_type = str(
            extraction_policy["entity_type_by_family_role"].get(
                type_key,
                extraction_policy["default_entity_type_by_role"][
                    role
                ],
            )
        )
        endpoint_id = create_identifier(
            str(
                policy["exam_term_raw_relation_eda"][
                    "identifier"
                ]["open_entity_prefix"]
            ),
            [
                str(document["source_document_id"]),
                source_field,
                compact_surface(surface),
                entity_type,
            ],
            policy,
        )
        return {
            "surface_key": compact_surface(surface),
            "mention_start": mention_start,
            "mention_end": mention_end,
            "mention_text": surface,
            "endpoint_id": endpoint_id,
            "node_kind": str(
                policy["exam_term_raw_relation_eda"][
                    "target_node_kinds"
                ]["open_entity"]
            ),
            "canonical_id": "",
            "source_record_id": "",
            "display_name": surface,
            "entity_type": entity_type,
            "resolution_status": "UNRESOLVED_OPEN_ENTITY",
            "source": str(document["source_dataset"]),
            "source_url": str(document["source_url"]),
            "is_exam_term": False,
            "exam_term_id": "",
            "participant_role": role,
            "role_basis": f"OPEN_EXPLICIT_{role}",
            "source_link_verified": (
                compact_surface(surface) in linked_surface_keys
            ),
            "linked_annotations_available": (
                supports_linked_entities
            ),
        }
    return None


def add_open_endpoint_mentions(
    mentions: list[dict],
    sentence: str,
    clause_span: tuple[int, int],
    action: dict,
    document: dict,
    source_field: str,
    linked_surface_keys: set[str],
    supports_linked_entities: bool,
    policy: dict,
) -> list[dict]:
    """기존 사전에 없는 역할 endpoint를 명시 조사에서 보충한다."""
    extraction_policy = policy["exam_term_raw_relation_eda"][
        "open_endpoint_extraction"
    ]
    if not bool(extraction_policy["enabled"]):
        return mentions
    predicate_family = str(action["predicate_family"])
    role_sets = policy["exam_relation_frames"][
        "pair_role_sets_by_family"
    ].get(predicate_family, [])
    output = list(mentions)
    for role_set in role_sets:
        roles = [str(role) for role in role_set]
        if len(roles) != 2:
            continue
        end_role = roles[1]
        attachment_pattern = policy[
            "exam_term_raw_relation_eda"
        ]["direct_action_attachment_patterns"].get(end_role)
        end_mentions = [
            mention
            for mention in output
            if str(mention["participant_role"]) == end_role
            and attachment_pattern
            and re.fullmatch(
                str(attachment_pattern),
                sentence[
                    int(mention["mention_end"]):
                    int(action["action_start"])
                ],
            )
        ]
        if not end_mentions:
            end_pattern = extraction_policy[
                "end_role_pattern_overrides"
            ].get(f"{action['voice']}:{end_role}")
            if not end_pattern:
                end_pattern = extraction_policy[
                    "end_role_pattern_overrides"
                ].get(f"{predicate_family}:{end_role}")
            if not end_pattern:
                end_pattern = extraction_policy[
                    "end_role_patterns"
                ].get(end_role)
            if end_pattern:
                occupied_spans = [
                    (
                        int(mention["mention_start"]),
                        int(mention["mention_end"]),
                    )
                    for mention in output
                ]
                open_end = extract_open_role_mention(
                    sentence,
                    (
                        int(clause_span[0]),
                        int(action["action_start"]),
                    ),
                    end_role,
                    predicate_family,
                    str(end_pattern),
                    document,
                    source_field,
                    linked_surface_keys,
                    supports_linked_entities,
                    occupied_spans,
                    policy,
                )
                if open_end is not None:
                    output.append(open_end)
                    end_mentions = [open_end]
        if not end_mentions:
            continue
        start_role = roles[0]
        closest_end = max(
            end_mentions,
            key=lambda mention: int(mention["mention_start"]),
        )
        start_mentions = [
            mention
            for mention in output
            if str(mention["participant_role"]) == start_role
            and int(mention["mention_end"])
            <= int(closest_end["mention_start"])
        ]
        if start_mentions:
            continue
        if (
            start_role == "ACTOR"
            and str(action["voice"]) == "PASSIVE"
        ):
            continue
        start_pattern = extraction_policy[
            "start_role_pattern_overrides"
        ].get(
            f"{action['predicate_pattern']}:{start_role}"
        )
        if not start_pattern:
            start_pattern = extraction_policy[
                "start_role_patterns"
            ].get(start_role)
        if not start_pattern:
            continue
        occupied_spans = [
            (
                int(mention["mention_start"]),
                int(mention["mention_end"]),
            )
            for mention in output
        ]
        open_start = extract_open_role_mention(
            sentence,
            (
                int(clause_span[0]),
                int(closest_end["mention_start"]),
            ),
            start_role,
            predicate_family,
            str(start_pattern),
            document,
            source_field,
            linked_surface_keys,
            supports_linked_entities,
            occupied_spans,
            policy,
        )
        if open_start is not None:
            output.append(open_start)
    return output


def resolve_explicit_role_pairs(
    predicate_family: str,
    mentions: list[dict],
    sentence: str,
    action: dict,
    policy: dict,
) -> list[dict]:
    """기출 문맥에서 관계별 역할 계약을 만족하는 endpoint 쌍을 만든다."""
    mentions_by_role: dict[str, list[dict]] = defaultdict(list)
    for mention in mentions:
        role = str(mention["participant_role"])
        if role in {"UNKNOWN", "COORDINATED"}:
            continue
        mentions_by_role[role].append(mention)
    pairs: dict[tuple[str, str, str, str], dict] = {}
    for role_set in policy["exam_relation_frames"][
        "pair_role_sets_by_family"
    ].get(predicate_family, []):
        roles = [str(role) for role in role_set]
        if len(roles) != 2:
            continue
        if not mentions_by_role.get(roles[0]):
            continue
        if not mentions_by_role.get(roles[1]):
            continue
        for start_mention, end_mention in product(
            mentions_by_role[roles[0]],
            mentions_by_role[roles[1]],
        ):
            if (
                str(start_mention["endpoint_id"])
                == str(end_mention["endpoint_id"])
            ):
                continue
            attachment_pattern = policy[
                "exam_term_raw_relation_eda"
            ]["direct_action_attachment_patterns"].get(roles[1])
            if not attachment_pattern:
                continue
            complement_to_action = sentence[
                int(end_mention["mention_end"]):
                int(action["action_start"])
            ]
            if not re.fullmatch(
                str(attachment_pattern),
                complement_to_action,
            ):
                continue
            argument_span = (
                int(action["action_start"])
                - int(start_mention["mention_start"])
            )
            if argument_span > int(
                policy["exam_term_raw_relation_eda"][
                    "maximum_argument_span_characters"
                ]
            ):
                continue
            start_to_complement = sentence[
                int(start_mention["mention_end"]):
                int(end_mention["mention_start"])
            ]
            intervening_subject = bool(
                re.search(
                    str(
                        policy["exam_term_raw_relation_eda"][
                            "intervening_subject_pattern"
                        ]
                    ),
                    start_to_complement,
                )
            )
            intervening_predicate = bool(
                re.search(
                    str(
                        policy["exam_term_raw_relation_eda"][
                            "intervening_predicate_pattern"
                        ]
                    ),
                    start_to_complement,
                )
                or re.search(
                    str(
                        policy["exam_term_raw_relation_eda"][
                            "intervening_argument_boundary_pattern"
                        ]
                    ),
                    start_to_complement,
                )
            )
            unsafe_start_role_basis = str(
                start_mention["role_basis"]
            ) in {
                str(value)
                for value in policy[
                    "exam_term_raw_relation_eda"
                ]["unsafe_start_role_bases"]
            }
            pair_key = (
                str(start_mention["endpoint_id"]),
                roles[0],
                str(end_mention["endpoint_id"]),
                roles[1],
            )
            pairs[pair_key] = {
                "start": start_mention,
                "start_role": roles[0],
                "end": end_mention,
                "end_role": roles[1],
                "intervening_subject": intervening_subject,
                "intervening_predicate": intervening_predicate,
                "unsafe_start_role_basis": (
                    unsafe_start_role_basis
                ),
            }
    return list(pairs.values())


def pair_matches_type_contract(
    predicate_family: str,
    pair: dict,
    policy: dict,
) -> bool:
    """역할별 엔티티 유형 계약이 맞는지 확인한다."""
    contracts = policy["auto_accept_role_type_contracts"].get(
        predicate_family,
        [],
    )
    return any(
        str(contract["start_role"]) == str(pair["start_role"])
        and str(pair["start"]["entity_type"])
        in {str(value) for value in contract["start_types"]}
        and str(contract["end_role"]) == str(pair["end_role"])
        and str(pair["end"]["entity_type"])
        in {str(value) for value in contract["end_types"]}
        for contract in contracts
    )


def classify_relation_candidate(
    pair: dict,
    pair_count: int,
    type_contract_match: bool,
    policy: dict,
) -> str:
    """자동 적재가 아닌 후속 검증 우선순위로 관계 후보를 분류한다."""
    statuses = policy["exam_term_raw_relation_eda"][
        "candidate_statuses"
    ]
    if pair_count > 1:
        return str(statuses["multiple_pairs"])
    if bool(pair["intervening_subject"]):
        return str(statuses["intervening_subject"])
    if (
        bool(pair["intervening_predicate"])
        or bool(pair["unsafe_start_role_basis"])
    ):
        return str(statuses["argument_structure"])
    short_unlinked_person = any(
        str(mention["entity_type"]) == "Person"
        and bool(mention["linked_annotations_available"])
        and not bool(mention["source_link_verified"])
        and len(
            compact_surface(
                strip_parenthetical_hanja(
                    mention["mention_text"],
                    policy,
                )
            )
        )
        <= int(
            policy["exam_term_raw_relation_eda"][
                "maximum_unlinked_person_surface_length"
            ]
        )
        for mention in [pair["start"], pair["end"]]
    )
    if short_unlinked_person:
        return str(statuses["unlinked_short_person"])
    endpoint_kinds = {
        str(pair["start"]["node_kind"]),
        str(pair["end"]["node_kind"]),
    }
    open_entity_kind = str(
        policy["exam_term_raw_relation_eda"][
            "target_node_kinds"
        ]["open_entity"]
    )
    if open_entity_kind in endpoint_kinds:
        return str(statuses["open_endpoint"])
    if not type_contract_match:
        return str(statuses["type_contract"])
    exam_term_kind = str(
        policy["exam_term_raw_relation_eda"][
            "target_node_kinds"
        ]["exam_term"]
    )
    if exam_term_kind in endpoint_kinds:
        return str(statuses["unresolved_exam"])
    source_kind = str(
        policy["exam_term_raw_relation_eda"][
            "target_node_kinds"
        ]["official_source"]
    )
    if source_kind in endpoint_kinds:
        return str(statuses["official_source"])
    return str(statuses["canonical"])


def append_exclusion(
    exclusions: list[dict],
    exclusion_counts: Counter,
    reason: str,
    document: dict,
    source_field: str,
    sentence: str,
    details: dict,
    policy: dict,
) -> None:
    """전체 건수는 세고 이유별 제한된 진단 표본만 보관한다."""
    exclusion_counts[reason] += 1
    maximum_rows = int(
        policy["exam_term_raw_relation_eda"]["audit"][
            "maximum_exclusion_rows_per_reason"
        ]
    )
    if exclusion_counts[reason] > maximum_rows:
        return
    exclusions.append(
        {
            "reason": reason,
            "source_dataset": str(document["source_dataset"]),
            "source_document_id": str(
                document["source_document_id"]
            ),
            "source_title": str(document["source_title"]),
            "source_field": source_field,
            "sentence": sentence,
            "details_json": dumps(details, ensure_ascii=False),
            "policy_version": str(
                policy["exam_term_raw_relation_eda"][
                    "policy_version"
                ]
            ),
        }
    )


def scan_exam_term_raw_relations(
    documents: Iterable[dict],
    exam_groups: dict[str, dict],
    target_groups: dict[str, dict],
    policy: dict,
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """원문을 한 번 순회해 명시적 기출용어 관계 근거를 수집한다."""
    eda_policy = policy["exam_term_raw_relation_eda"]
    exam_matcher = IndexedSurfaceMatcher(
        exam_groups.keys(),
        eda_policy["following_particles"],
    )
    enabled_target_surfaces = [
        surface_key
        for surface_key, group in target_groups.items()
        if bool(group["match_enabled"])
    ]
    target_matcher = IndexedSurfaceMatcher(
        enabled_target_surfaces,
        eda_policy["following_particles"],
    )
    uncertainty_patterns = [
        str(value) for value in eda_policy["uncertainty_patterns"]
    ]
    negation_patterns = [
        str(value) for value in eda_policy["negation_patterns"]
    ]
    mention_sentences: list[dict] = []
    evidence_rows: list[dict] = []
    exclusions: list[dict] = []
    dataset_statistics: dict[str, Counter] = defaultdict(Counter)
    exclusion_counts: Counter = Counter()
    maximum_mentions = int(
        eda_policy["maximum_mentions_per_clause"]
    )
    open_entity_kind = str(
        eda_policy["target_node_kinds"]["open_entity"]
    )
    minimum_registered_endpoint_count = int(
        eda_policy[
            "minimum_registered_endpoint_count_per_relation"
        ]
    )
    for document in documents:
        dataset = str(document["source_dataset"])
        dataset_statistics[dataset]["document_count"] += 1
        for sentence_row in extract_document_sentences(
            document,
            policy,
        ):
            source_field = str(sentence_row["source_field"])
            sentence = str(sentence_row["sentence"])
            linked_surface_keys = {
                str(value)
                for value in sentence_row["linked_surface_keys"]
            }
            dataset_statistics[dataset]["sentence_count"] += 1
            exam_matches = exam_matcher.find(sentence)
            if not exam_matches:
                continue
            dataset_statistics[dataset][
                "exam_mention_sentence_count"
            ] += 1
            actions = collect_asserted_actions(sentence, policy)
            if not actions:
                continue
            dataset_statistics[dataset][
                "action_sentence_count"
            ] += 1
            mention_sentence_id = create_identifier(
                "raw-mention-sentence:",
                [
                    str(document["source_document_id"]),
                    source_field,
                    sentence,
                    str(eda_policy["policy_version"]),
                ],
                policy,
            )
            mention_sentences.append(
                {
                    "mention_sentence_id": mention_sentence_id,
                    "source_dataset": dataset,
                    "source_document_id": str(
                        document["source_document_id"]
                    ),
                    "source_title": str(
                        document["source_title"]
                    ),
                    "source_field": source_field,
                    "source_url": str(document["source_url"]),
                    "trust_tier": str(document["trust_tier"]),
                    "linked_annotations_available": bool(
                        document.get(
                            "supports_linked_entities",
                            False,
                        )
                    ),
                    "sentence": sentence,
                    "linked_surfaces_json": dumps(
                        sorted(linked_surface_keys),
                        ensure_ascii=False,
                    ),
                    "exam_surfaces_json": dumps(
                        sorted(
                            {
                                str(
                                    exam_groups[
                                        str(match["surface_key"])
                                    ]["display_surface"]
                                )
                                for match in exam_matches
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "predicate_families_json": dumps(
                        sorted(
                            {
                                str(action["predicate_family"])
                                for action in actions
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "subject_inference_used": False,
                    "policy_version": str(
                        eda_policy["policy_version"]
                    ),
                }
            )
            target_matches = target_matcher.find(sentence)
            for asserted_action in actions:
                action = dict(asserted_action)
                if re.search(
                    str(
                        eda_policy[
                            "passive_action_suffix_pattern"
                        ]
                    ),
                    str(action["action_suffix"]),
                ):
                    action["voice"] = "PASSIVE"
                clause_start, clause_end = action["clause_span"]
                clause_text = sentence[clause_start:clause_end]
                predicate_family = str(
                    action["predicate_family"]
                )
                predicate_pattern = str(
                    action["predicate_pattern"]
                )
                blocked_predicates = {
                    str(value)
                    for value in eda_policy[
                        "blocked_predicate_patterns_by_family"
                    ].get(predicate_family, [])
                }
                if predicate_pattern in blocked_predicates:
                    append_exclusion(
                        exclusions,
                        exclusion_counts,
                        "AMBIGUOUS_PREDICATE_SEMANTICS",
                        document,
                        source_field,
                        sentence,
                        {
                            "predicate_family": predicate_family,
                            "predicate_pattern": predicate_pattern,
                            "clause": clause_text,
                        },
                        policy,
                    )
                    continue
                if re.search(
                    str(eda_policy["blocked_action_suffix_pattern"]),
                    str(action["action_suffix"]),
                ):
                    append_exclusion(
                        exclusions,
                        exclusion_counts,
                        "UNSAFE_PREDICATE_MORPHOLOGY",
                        document,
                        source_field,
                        sentence,
                        {
                            "predicate_family": str(
                                action["predicate_family"]
                            ),
                            "predicate_pattern": str(
                                action["predicate_pattern"]
                            ),
                            "action_suffix": str(
                                action["action_suffix"]
                            ),
                            "clause": clause_text,
                        },
                        policy,
                    )
                    continue
                if any(
                    re.search(pattern, clause_text)
                    for pattern in uncertainty_patterns
                ):
                    append_exclusion(
                        exclusions,
                        exclusion_counts,
                        "UNCERTAIN_CLAUSE",
                        document,
                        source_field,
                        sentence,
                        {"clause": clause_text},
                        policy,
                    )
                    continue
                if any(
                    re.search(pattern, clause_text)
                    for pattern in negation_patterns
                ):
                    append_exclusion(
                        exclusions,
                        exclusion_counts,
                        "NEGATED_CLAUSE",
                        document,
                        source_field,
                        sentence,
                        {"clause": clause_text},
                        policy,
                    )
                    continue
                clause_exam_matches = [
                    match
                    for match in exam_matches
                    if int(match["mention_start"]) >= clause_start
                    and int(match["mention_end"]) <= clause_end
                ]
                if not clause_exam_matches:
                    continue
                clause_target_matches = [
                    match
                    for match in target_matches
                    if int(match["mention_start"]) >= clause_start
                    and int(match["mention_end"]) <= clause_end
                ]
                mentions, ambiguous_mentions = build_role_mentions(
                    sentence,
                    (clause_start, clause_end),
                    action,
                    clause_exam_matches,
                    clause_target_matches,
                    exam_groups,
                    target_groups,
                    linked_surface_keys,
                    bool(
                        document.get(
                            "supports_linked_entities",
                            False,
                        )
                    ),
                    policy,
                )
                mentions = add_open_endpoint_mentions(
                    mentions,
                    sentence,
                    (clause_start, clause_end),
                    action,
                    document,
                    source_field,
                    linked_surface_keys,
                    bool(
                        document.get(
                            "supports_linked_entities",
                            False,
                        )
                    ),
                    policy,
                )
                for ambiguous in ambiguous_mentions:
                    append_exclusion(
                        exclusions,
                        exclusion_counts,
                        str(ambiguous["reason"]),
                        document,
                        source_field,
                        sentence,
                        {
                            "mention_text": str(
                                ambiguous["mention_text"]
                            ),
                            "candidate_count": int(
                                ambiguous["candidate_count"]
                            ),
                            "clause": clause_text,
                        },
                        policy,
                    )
                if len(mentions) > maximum_mentions:
                    append_exclusion(
                        exclusions,
                        exclusion_counts,
                        "TOO_MANY_ENDPOINT_MENTIONS",
                        document,
                        source_field,
                        sentence,
                        {
                            "mention_count": len(mentions),
                            "clause": clause_text,
                        },
                        policy,
                    )
                    continue
                resolved_pairs = resolve_explicit_role_pairs(
                    str(action["predicate_family"]),
                    mentions,
                    sentence,
                    action,
                    policy,
                )
                pairs: list[dict] = []
                for pair in resolved_pairs:
                    registered_endpoint_count = sum(
                        str(endpoint["node_kind"])
                        != open_entity_kind
                        for endpoint in [
                            pair["start"],
                            pair["end"],
                        ]
                    )
                    if (
                        registered_endpoint_count
                        >= minimum_registered_endpoint_count
                    ):
                        pairs.append(pair)
                        continue
                    append_exclusion(
                        exclusions,
                        exclusion_counts,
                        "BOTH_ENDPOINTS_UNREGISTERED",
                        document,
                        source_field,
                        sentence,
                        {
                            "predicate_family": str(
                                action["predicate_family"]
                            ),
                            "predicate_pattern": str(
                                action["predicate_pattern"]
                            ),
                            "start_name": str(
                                pair["start"]["display_name"]
                            ),
                            "end_name": str(
                                pair["end"]["display_name"]
                            ),
                            "clause": clause_text,
                        },
                        policy,
                    )
                if resolved_pairs and not pairs:
                    continue
                if not resolved_pairs:
                    append_exclusion(
                        exclusions,
                        exclusion_counts,
                        "EXPLICIT_ROLE_PAIR_NOT_FOUND",
                        document,
                        source_field,
                        sentence,
                        {
                            "predicate_family": str(
                                action["predicate_family"]
                            ),
                            "predicate_pattern": str(
                                action["predicate_pattern"]
                            ),
                            "mentions": [
                                {
                                    "name": str(
                                        mention["display_name"]
                                    ),
                                    "role": str(
                                        mention[
                                            "participant_role"
                                        ]
                                    ),
                                    "is_exam_term": bool(
                                        mention["is_exam_term"]
                                    ),
                                }
                                for mention in mentions
                            ],
                            "clause": clause_text,
                        },
                        policy,
                    )
                    continue
                for pair in pairs:
                    type_contract_match = (
                        pair_matches_type_contract(
                            str(action["predicate_family"]),
                            pair,
                            policy,
                        )
                    )
                    candidate_status = (
                        classify_relation_candidate(
                            pair,
                            len(pairs),
                            type_contract_match,
                            policy,
                        )
                    )
                    relation_type = str(
                        eda_policy[
                            "relation_type_by_predicate"
                        ].get(
                            str(action["predicate_pattern"]),
                            eda_policy[
                                "relation_type_by_family"
                            ][str(action["predicate_family"])],
                        )
                    )
                    evidence_id = create_identifier(
                        str(
                            eda_policy["identifier"][
                                "evidence_prefix"
                            ]
                        ),
                        [
                            str(pair["start"]["endpoint_id"]),
                            relation_type,
                            str(pair["end"]["endpoint_id"]),
                            str(document["source_document_id"]),
                            source_field,
                            clause_text,
                            str(action["predicate_pattern"]),
                            str(eda_policy["policy_version"]),
                        ],
                        policy,
                    )
                    evidence_rows.append(
                        {
                            "raw_relation_evidence_id": evidence_id,
                            "start_node_id": str(
                                pair["start"]["endpoint_id"]
                            ),
                            "start_node_kind": str(
                                pair["start"]["node_kind"]
                            ),
                            "start_canonical_id": str(
                                pair["start"]["canonical_id"]
                            ),
                            "start_source_record_id": str(
                                pair["start"]["source_record_id"]
                            ),
                            "start_mention_text": str(
                                pair["start"]["mention_text"]
                            ),
                            "start_endpoint_source": str(
                                pair["start"]["source"]
                            ),
                            "start_endpoint_source_url": str(
                                pair["start"]["source_url"]
                            ),
                            "start_display_name": str(
                                pair["start"]["display_name"]
                            ),
                            "start_entity_type": str(
                                pair["start"]["entity_type"]
                            ),
                            "start_role": str(pair["start_role"]),
                            "start_source_link_verified": bool(
                                pair["start"][
                                    "source_link_verified"
                                ]
                            ),
                            "start_is_exam_term": bool(
                                pair["start"]["is_exam_term"]
                            ),
                            "start_is_open_entity": (
                                str(pair["start"]["node_kind"])
                                == str(
                                    eda_policy[
                                        "target_node_kinds"
                                    ]["open_entity"]
                                )
                            ),
                            "end_node_id": str(
                                pair["end"]["endpoint_id"]
                            ),
                            "end_node_kind": str(
                                pair["end"]["node_kind"]
                            ),
                            "end_canonical_id": str(
                                pair["end"]["canonical_id"]
                            ),
                            "end_source_record_id": str(
                                pair["end"]["source_record_id"]
                            ),
                            "end_mention_text": str(
                                pair["end"]["mention_text"]
                            ),
                            "end_endpoint_source": str(
                                pair["end"]["source"]
                            ),
                            "end_endpoint_source_url": str(
                                pair["end"]["source_url"]
                            ),
                            "end_display_name": str(
                                pair["end"]["display_name"]
                            ),
                            "end_entity_type": str(
                                pair["end"]["entity_type"]
                            ),
                            "end_role": str(pair["end_role"]),
                            "end_source_link_verified": bool(
                                pair["end"][
                                    "source_link_verified"
                                ]
                            ),
                            "end_is_exam_term": bool(
                                pair["end"]["is_exam_term"]
                            ),
                            "end_is_open_entity": (
                                str(pair["end"]["node_kind"])
                                == str(
                                    eda_policy[
                                        "target_node_kinds"
                                    ]["open_entity"]
                                )
                            ),
                            "relation_family": str(
                                action["predicate_family"]
                            ),
                            "relation_type": relation_type,
                            "predicate_pattern": str(
                                action["predicate_pattern"]
                            ),
                            "type_contract_match": (
                                type_contract_match
                            ),
                            "intervening_subject_detected": bool(
                                pair["intervening_subject"]
                            ),
                            "intervening_predicate_detected": bool(
                                pair["intervening_predicate"]
                            ),
                            "unsafe_start_role_basis": bool(
                                pair["unsafe_start_role_basis"]
                            ),
                            "candidate_status": candidate_status,
                            "source_dataset": dataset,
                            "source_document_id": str(
                                document["source_document_id"]
                            ),
                            "source_title": str(
                                document["source_title"]
                            ),
                            "source_field": source_field,
                            "source_url": str(
                                document["source_url"]
                            ),
                            "trust_tier": str(
                                document["trust_tier"]
                            ),
                            "linked_annotations_available": bool(
                                document.get(
                                    "supports_linked_entities",
                                    False,
                                )
                            ),
                            "evidence_sentence": sentence,
                            "atomic_clause_text": clause_text,
                            "subject_inference_used": False,
                            "llm_used": False,
                            "neo4j_load": False,
                            "policy_version": str(
                                eda_policy["policy_version"]
                            ),
                        }
                    )
                    dataset_statistics[dataset][
                        "relation_evidence_count"
                    ] += 1
    statistics = {
        "datasets": {
            dataset: dict(counts)
            for dataset, counts in sorted(
                dataset_statistics.items()
            )
        },
        "exclusion_reason_counts": dict(exclusion_counts),
    }
    return mention_sentences, evidence_rows, exclusions, statistics


def aggregate_relation_candidates(
    evidence: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """동일 endpoint·관계의 여러 공식 문장 근거를 하나로 묶는다."""
    columns = [
        "raw_relation_candidate_id",
        "start_node_id",
        "start_node_kind",
        "start_canonical_id",
        "start_source_record_id",
        "start_endpoint_source",
        "start_endpoint_source_url",
        "start_mention_texts_json",
        "start_display_name",
        "start_entity_type",
        "end_node_id",
        "end_node_kind",
        "end_canonical_id",
        "end_source_record_id",
        "end_endpoint_source",
        "end_endpoint_source_url",
        "end_mention_texts_json",
        "end_display_name",
        "end_entity_type",
        "relation_family",
        "relation_type",
        "candidate_statuses_json",
        "evidence_count",
        "source_dataset_count",
        "source_datasets_json",
        "source_document_ids_json",
        "evidence_ids_json",
        "both_exam_terms",
        "touches_non_exam_target",
        "touches_open_entity",
        "subject_inference_used",
        "auto_load_eligible",
        "llm_used",
        "neo4j_load",
        "policy_version",
    ]
    if evidence.empty:
        return pd.DataFrame(columns=columns)
    eda_policy = policy["exam_term_raw_relation_eda"]
    rows: list[dict] = []
    group_columns = [
        "start_node_id",
        "relation_type",
        "end_node_id",
    ]
    for _, group in evidence.groupby(
        group_columns,
        sort=True,
        dropna=False,
    ):
        representative = group.iloc[0]
        relation_id = create_identifier(
            str(
                eda_policy["identifier"]["relation_prefix"]
            ),
            [
                str(representative["start_node_id"]),
                str(representative["relation_type"]),
                str(representative["end_node_id"]),
                str(eda_policy["policy_version"]),
            ],
            policy,
        )
        start_is_exam = bool(
            group["start_is_exam_term"].eq(True).any()
        )
        end_is_exam = bool(
            group["end_is_exam_term"].eq(True).any()
        )
        rows.append(
            {
                "raw_relation_candidate_id": relation_id,
                "start_node_id": str(
                    representative["start_node_id"]
                ),
                "start_node_kind": str(
                    representative["start_node_kind"]
                ),
                "start_canonical_id": str(
                    representative["start_canonical_id"]
                ),
                "start_source_record_id": str(
                    representative["start_source_record_id"]
                ),
                "start_endpoint_source": str(
                    representative["start_endpoint_source"]
                ),
                "start_endpoint_source_url": str(
                    representative["start_endpoint_source_url"]
                ),
                "start_mention_texts_json": dumps(
                    sorted(
                        {
                            str(value)
                            for value in group["start_mention_text"]
                        }
                    ),
                    ensure_ascii=False,
                ),
                "start_display_name": str(
                    representative["start_display_name"]
                ),
                "start_entity_type": str(
                    representative["start_entity_type"]
                ),
                "end_node_id": str(
                    representative["end_node_id"]
                ),
                "end_node_kind": str(
                    representative["end_node_kind"]
                ),
                "end_canonical_id": str(
                    representative["end_canonical_id"]
                ),
                "end_source_record_id": str(
                    representative["end_source_record_id"]
                ),
                "end_endpoint_source": str(
                    representative["end_endpoint_source"]
                ),
                "end_endpoint_source_url": str(
                    representative["end_endpoint_source_url"]
                ),
                "end_mention_texts_json": dumps(
                    sorted(
                        {
                            str(value)
                            for value in group["end_mention_text"]
                        }
                    ),
                    ensure_ascii=False,
                ),
                "end_display_name": str(
                    representative["end_display_name"]
                ),
                "end_entity_type": str(
                    representative["end_entity_type"]
                ),
                "relation_family": str(
                    representative["relation_family"]
                ),
                "relation_type": str(
                    representative["relation_type"]
                ),
                "candidate_statuses_json": dumps(
                    sorted(
                        {
                            str(value)
                            for value in group["candidate_status"]
                        }
                    ),
                    ensure_ascii=False,
                ),
                "evidence_count": len(group),
                "source_dataset_count": int(
                    group["source_dataset"].nunique()
                ),
                "source_datasets_json": dumps(
                    sorted(
                        {
                            str(value)
                            for value in group["source_dataset"]
                        }
                    ),
                    ensure_ascii=False,
                ),
                "source_document_ids_json": dumps(
                    sorted(
                        {
                            str(value)
                            for value in group[
                                "source_document_id"
                            ]
                        }
                    ),
                    ensure_ascii=False,
                ),
                "evidence_ids_json": dumps(
                    sorted(
                        {
                            str(value)
                            for value in group[
                                "raw_relation_evidence_id"
                            ]
                        }
                    ),
                    ensure_ascii=False,
                ),
                "both_exam_terms": (
                    start_is_exam and end_is_exam
                ),
                "touches_non_exam_target": not (
                    start_is_exam and end_is_exam
                ),
                "touches_open_entity": bool(
                    group["start_is_open_entity"].eq(True).any()
                    or group["end_is_open_entity"].eq(True).any()
                ),
                "subject_inference_used": False,
                "auto_load_eligible": False,
                "llm_used": False,
                "neo4j_load": False,
                "policy_version": str(
                    eda_policy["policy_version"]
                ),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_non_exam_node_candidates(
    evidence: pd.DataFrame,
    relations: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """관계에 등장한 비기출 endpoint를 검색 그래프 노드 후보로 만든다."""
    columns = [
        "node_id",
        "node_kind",
        "canonical_id",
        "source_record_id",
        "display_name",
        "entity_type",
        "source",
        "source_url",
        "candidate_cluster_key",
        "source_document_ids_json",
        "observed_mentions_json",
        "resolution_status",
        "evidence_count",
        "safe_evidence_count",
        "relation_candidate_count",
        "support_statuses_json",
        "node_action",
        "search_graph_node_eligible",
        "canonical_promotion_eligible",
        "neo4j_load",
        "policy_version",
    ]
    if evidence.empty:
        return pd.DataFrame(columns=columns)
    eda_policy = policy["exam_term_raw_relation_eda"]
    open_entity_kind = str(
        eda_policy["target_node_kinds"]["open_entity"]
    )
    endpoint_rows: dict[str, dict] = {}
    observed_mentions_by_node: dict[str, set[str]] = defaultdict(set)
    source_document_ids_by_node: dict[str, set[str]] = defaultdict(set)
    for row in evidence.to_dict("records"):
        for side in ["start", "end"]:
            if bool(row[f"{side}_is_exam_term"]):
                continue
            node_id = str(row[f"{side}_node_id"])
            observed_mentions_by_node[node_id].add(
                str(row[f"{side}_mention_text"])
            )
            source_document_ids_by_node[node_id].add(
                str(row["source_document_id"])
            )
            node_kind = str(row[f"{side}_node_kind"])
            resolution_status = "UNRESOLVED_OFFICIAL_SOURCE"
            if str(row[f"{side}_canonical_id"]):
                resolution_status = "RESOLVED_CANONICAL"
            elif node_kind == open_entity_kind:
                resolution_status = "UNRESOLVED_OPEN_ENTITY"
            display_name = str(row[f"{side}_display_name"])
            entity_type = str(row[f"{side}_entity_type"])
            endpoint_rows[node_id] = {
                "node_id": node_id,
                "node_kind": node_kind,
                "canonical_id": str(
                    row[f"{side}_canonical_id"]
                ),
                "source_record_id": str(
                    row[f"{side}_source_record_id"]
                ),
                "display_name": display_name,
                "entity_type": entity_type,
                "source": str(
                    row[f"{side}_endpoint_source"]
                ),
                "source_url": str(
                    row[f"{side}_endpoint_source_url"]
                ),
                "candidate_cluster_key": (
                    f"{compact_surface(display_name)}|{entity_type}"
                ),
                "resolution_status": resolution_status,
            }
    evidence_count_by_node: Counter = Counter()
    safe_evidence_count_by_node: Counter = Counter()
    support_statuses_by_node: dict[str, set[str]] = defaultdict(set)
    safe_statuses = {
        str(eda_policy["candidate_statuses"]["canonical"]),
        str(eda_policy["candidate_statuses"]["official_source"]),
    }
    for row in evidence.to_dict("records"):
        for side in ["start", "end"]:
            node_id = str(row[f"{side}_node_id"])
            if node_id in endpoint_rows:
                evidence_count_by_node[node_id] += 1
                candidate_status = str(row["candidate_status"])
                support_statuses_by_node[node_id].add(
                    candidate_status
                )
                if candidate_status in safe_statuses:
                    safe_evidence_count_by_node[node_id] += 1
    relation_count_by_node: Counter = Counter()
    for row in relations.to_dict("records"):
        for side in ["start", "end"]:
            node_id = str(row[f"{side}_node_id"])
            if node_id in endpoint_rows:
                relation_count_by_node[node_id] += 1
    output_rows: list[dict] = []
    for node_id, endpoint in sorted(endpoint_rows.items()):
        safe_evidence_count = int(
            safe_evidence_count_by_node[node_id]
        )
        search_graph_node_eligible = safe_evidence_count > 0
        node_action = "HOLD_FOR_REVIEW"
        if endpoint["canonical_id"]:
            node_action = "REUSE_CANONICAL"
        elif endpoint["node_kind"] == open_entity_kind:
            node_action = "CREATE_OPEN_ENTITY_CANDIDATE"
        elif search_graph_node_eligible:
            node_action = "CREATE_OFFICIAL_SOURCE_ANCHOR"
        output_rows.append(
            {
                **endpoint,
                "source_document_ids_json": dumps(
                    sorted(source_document_ids_by_node[node_id]),
                    ensure_ascii=False,
                ),
                "observed_mentions_json": dumps(
                    sorted(observed_mentions_by_node[node_id]),
                    ensure_ascii=False,
                ),
                "evidence_count": int(
                    evidence_count_by_node[node_id]
                ),
                "safe_evidence_count": safe_evidence_count,
                "relation_candidate_count": int(
                    relation_count_by_node[node_id]
                ),
                "support_statuses_json": dumps(
                    sorted(support_statuses_by_node[node_id]),
                    ensure_ascii=False,
                ),
                "node_action": node_action,
                "search_graph_node_eligible": (
                    search_graph_node_eligible
                ),
                "canonical_promotion_eligible": False,
                "neo4j_load": False,
                "policy_version": str(
                    eda_policy["policy_version"]
                ),
            }
        )
    return pd.DataFrame(output_rows, columns=columns)


def build_raw_relation_eda_tables(
    canonical_registry: pd.DataFrame,
    exam_term_matches: pd.DataFrame,
    source_nodes: pd.DataFrame,
    source_resolutions: pd.DataFrame,
    aks_articles_list_path: str,
    documents: Iterable[dict],
    policy: dict,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """기출 용어 중심 원문 관계 EDA의 모든 표와 통계를 만든다."""
    aks_source_release = infer_aks_source_release(
        canonical_registry,
        policy,
    )
    exam_groups, exam_statistics = build_exam_endpoint_groups(
        exam_term_matches,
        canonical_registry,
        policy,
    )
    target_groups, target_statistics = (
        build_target_endpoint_groups(
            canonical_registry,
            source_nodes,
            source_resolutions,
            aks_articles_list_path,
            aks_source_release,
            policy,
        )
    )
    (
        mention_rows,
        evidence_rows,
        exclusion_rows,
        scan_statistics,
    ) = scan_exam_term_raw_relations(
        documents,
        exam_groups,
        target_groups,
        policy,
    )
    mention_columns = [
        "mention_sentence_id",
        "source_dataset",
        "source_document_id",
        "source_title",
        "source_field",
        "source_url",
        "trust_tier",
        "linked_annotations_available",
        "sentence",
        "linked_surfaces_json",
        "exam_surfaces_json",
        "predicate_families_json",
        "subject_inference_used",
        "policy_version",
    ]
    evidence_columns = [
        "raw_relation_evidence_id",
        "start_node_id",
        "start_node_kind",
        "start_canonical_id",
        "start_source_record_id",
        "start_mention_text",
        "start_endpoint_source",
        "start_endpoint_source_url",
        "start_display_name",
        "start_entity_type",
        "start_role",
        "start_source_link_verified",
        "start_is_exam_term",
        "start_is_open_entity",
        "end_node_id",
        "end_node_kind",
        "end_canonical_id",
        "end_source_record_id",
        "end_mention_text",
        "end_endpoint_source",
        "end_endpoint_source_url",
        "end_display_name",
        "end_entity_type",
        "end_role",
        "end_source_link_verified",
        "end_is_exam_term",
        "end_is_open_entity",
        "relation_family",
        "relation_type",
        "predicate_pattern",
        "type_contract_match",
        "intervening_subject_detected",
        "intervening_predicate_detected",
        "unsafe_start_role_basis",
        "candidate_status",
        "source_dataset",
        "source_document_id",
        "source_title",
        "source_field",
        "source_url",
        "trust_tier",
        "linked_annotations_available",
        "evidence_sentence",
        "atomic_clause_text",
        "subject_inference_used",
        "llm_used",
        "neo4j_load",
        "policy_version",
    ]
    exclusion_columns = [
        "reason",
        "source_dataset",
        "source_document_id",
        "source_title",
        "source_field",
        "sentence",
        "details_json",
        "policy_version",
    ]
    mention_sentences = pd.DataFrame(
        mention_rows,
        columns=mention_columns,
    ).drop_duplicates(subset=["mention_sentence_id"])
    evidence = pd.DataFrame(
        evidence_rows,
        columns=evidence_columns,
    ).drop_duplicates(subset=["raw_relation_evidence_id"])
    exclusions = pd.DataFrame(
        exclusion_rows,
        columns=exclusion_columns,
    )
    relations = aggregate_relation_candidates(evidence, policy)
    non_exam_nodes = build_non_exam_node_candidates(
        evidence,
        relations,
        policy,
    )
    maximum_audit_rows = int(
        policy["exam_term_raw_relation_eda"]["audit"][
            "maximum_rows"
        ]
    )
    audit_sample = evidence.copy()
    if len(audit_sample) > maximum_audit_rows:
        group_columns = [
            "source_dataset",
            "relation_family",
            "candidate_status",
        ]
        audit_sample = (
            audit_sample.sort_values(
                [
                    *group_columns,
                    "raw_relation_evidence_id",
                ]
            )
            .groupby(group_columns, group_keys=False)
            .head(2)
            .head(maximum_audit_rows)
            .reset_index(drop=True)
        )
    eda_policy = policy["exam_term_raw_relation_eda"]
    configured_datasets = {
        str(dataset["name"]): {
            "enabled": bool(dataset["enabled"]),
            "skip_reason": str(dataset.get("skip_reason") or ""),
        }
        for dataset in eda_policy["datasets"]
    }
    statistics = {
        **exam_statistics,
        **target_statistics,
        **scan_statistics,
        "configured_datasets": configured_datasets,
        "mention_sentence_count": len(mention_sentences),
        "relation_evidence_count": len(evidence),
        "relation_candidate_count": len(relations),
        "relation_candidate_status_counts": dict(
            Counter(
                str(value) for value in evidence["candidate_status"]
            )
        ),
        "relation_family_counts": dict(
            Counter(
                str(value) for value in evidence["relation_family"]
            )
        ),
        "relation_source_dataset_counts": dict(
            Counter(
                str(value) for value in evidence["source_dataset"]
            )
        ),
        "both_exam_relation_candidate_count": int(
            relations["both_exam_terms"].eq(True).sum()
        ),
        "non_exam_relation_candidate_count": int(
            relations["touches_non_exam_target"].eq(True).sum()
        ),
        "open_endpoint_relation_candidate_count": int(
            relations["touches_open_entity"].eq(True).sum()
        ),
        "non_exam_target_node_count": len(non_exam_nodes),
        "new_official_source_node_candidate_count": int(
            non_exam_nodes["node_action"]
            .eq("CREATE_OFFICIAL_SOURCE_ANCHOR")
            .sum()
        ),
        "open_entity_node_candidate_count": int(
            non_exam_nodes["node_action"]
            .eq("CREATE_OPEN_ENTITY_CANDIDATE")
            .sum()
        ),
        "held_non_exam_node_review_count": int(
            non_exam_nodes["node_action"]
            .eq("HOLD_FOR_REVIEW")
            .sum()
        ),
        "reused_canonical_non_exam_node_count": int(
            non_exam_nodes["node_action"]
            .eq("REUSE_CANONICAL")
            .sum()
        ),
        "audit_sample_count": len(audit_sample),
        "subject_inferred_relation_count": 0,
        "auto_load_eligible_relation_count": 0,
        "llm_used": False,
        "neo4j_load": False,
    }
    return {
        "mention_sentences": mention_sentences,
        "evidence": evidence,
        "relations": relations,
        "non_exam_nodes": non_exam_nodes,
        "exclusions": exclusions,
        "audit_sample": audit_sample,
    }, statistics

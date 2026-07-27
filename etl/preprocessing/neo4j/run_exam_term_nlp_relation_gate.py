from __future__ import annotations

from argparse import ArgumentParser, Namespace
import csv
from hashlib import sha256
from json import dump, dumps, load, loads
from pathlib import Path
import re
from typing import Iterable

import pandas as pd


def parse_arguments() -> Namespace:
    """Read relation gate input and output paths."""
    neo4j_root = Path(__file__).resolve().parent
    parser = ArgumentParser(
        description=(
            "Apply a deterministic evidence gate to registered NLP relation "
            "candidates. This command does not use an LLM or load Neo4j."
        )
    )
    parser.add_argument(
        "--config",
        default=str(
            neo4j_root
            / "config"
            / "exam_term_nlp_relation_gate.json"
        ),
    )
    parser.add_argument(
        "--candidate-csv",
        default=str(
            neo4j_root
            / "output"
            / "exam_term_nlp_relations_full"
            / "exam_term_nlp_relation_full_candidates.csv"
        ),
    )
    parser.add_argument(
        "--evidence-root",
        default=str(neo4j_root / "output"),
    )
    parser.add_argument(
        "--evidence-directory-pattern",
        default="exam_term_nlp_relations_full_*",
    )
    parser.add_argument(
        "--evidence-filename",
        default="exam_term_nlp_relation_evidence.csv",
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root
            / "output"
            / "exam_term_nlp_relation_gate"
        ),
    )
    return parser.parse_args()


def collect_marked_positions(
    clause: str,
    surface: str,
    marker_pattern: str,
    maximum_tail_length: int,
) -> list[tuple[int, int]]:
    """Return mention and marker end offsets for explicit arguments."""
    positions: list[tuple[int, int]] = []
    for match in re.finditer(re.escape(surface), clause):
        tail = clause[
            match.end():match.end() + maximum_tail_length
        ]
        marker_match = re.search(marker_pattern, tail)
        if marker_match:
            positions.append(
                (
                    match.start(),
                    match.end() + marker_match.end(),
                )
            )
    return positions


def normalize_surface(surface: str) -> str:
    """Normalize an endpoint label for conservative mention alignment."""
    return re.sub(
        r"[^0-9A-Za-z가-힣]",
        "",
        surface,
    ).casefold()


def evaluate_gate_evidence(
    evidence: dict[str, str],
    policy: dict[str, object],
) -> tuple[str, list[str]]:
    """Validate one evidence row and return its refined relation type."""
    reasons: list[str] = []
    allowed_statuses = {
        str(value)
        for value in policy["allowed_evidence_statuses"]
    }
    if evidence["candidate_status"] not in allowed_statuses:
        reasons.append("EVIDENCE_STATUS_NOT_ALLOWED")
    if bool(policy["require_rank_one"]) and int(
        evidence["candidate_rank"]
    ) != 1:
        reasons.append("CANDIDATE_RANK_NOT_ONE")
    if int(evidence["candidate_score"]) < int(
        policy["minimum_candidate_score"]
    ):
        reasons.append("CANDIDATE_SCORE_TOO_LOW")
    if int(evidence["explicit_role_evidence_count"]) < int(
        policy["minimum_explicit_role_evidence_count"]
    ):
        reasons.append("EXPLICIT_ROLE_EVIDENCE_INSUFFICIENT")
    if bool(policy["require_type_contract_compatible"]) and (
        evidence["type_contract_compatible"].casefold() != "true"
    ):
        reasons.append("TYPE_CONTRACT_NOT_COMPATIBLE")
    if bool(policy["require_no_structural_conflict"]) and (
        evidence["structural_conflict"].casefold() == "true"
    ):
        reasons.append("STRUCTURAL_CONFLICT")

    family = evidence["relation_family"]
    predicate = evidence["predicate_pattern"]
    predicate_rule: dict[str, object] | None = None
    for configured_rule in policy["predicate_rules"]:
        if (
            str(configured_rule["family"]) == family
            and str(configured_rule["predicate"]) == predicate
        ):
            predicate_rule = configured_rule
            break
    if predicate_rule is None:
        reasons.append("PREDICATE_NOT_ALLOWED")
        return "", reasons

    family_contract = policy["family_contracts"].get(family)
    if family_contract is None:
        reasons.append("FAMILY_CONTRACT_MISSING")
        return "", reasons
    role_pair = [
        evidence["start_role"],
        evidence["end_role"],
    ]
    allowed_role_pairs = [
        [str(role) for role in pair]
        for pair in family_contract["allowed_role_pairs"]
    ]
    if role_pair not in allowed_role_pairs:
        reasons.append("ROLE_PAIR_NOT_ALLOWED")
    allowed_start_entity_types = predicate_rule.get(
        "allowed_start_entity_types",
        family_contract["allowed_start_entity_types"],
    )
    allowed_end_entity_types = predicate_rule.get(
        "allowed_end_entity_types",
        family_contract["allowed_end_entity_types"],
    )
    if evidence["start_entity_type"] not in {
        str(value)
        for value in allowed_start_entity_types
    }:
        reasons.append("START_ENTITY_TYPE_NOT_ALLOWED")
    if evidence["end_entity_type"] not in {
        str(value)
        for value in allowed_end_entity_types
    }:
        reasons.append("END_ENTITY_TYPE_NOT_ALLOWED")
    blocked_entity_types = {
        str(value) for value in policy["blocked_entity_types"]
    }
    if (
        evidence["start_entity_type"] in blocked_entity_types
        or evidence["end_entity_type"] in blocked_entity_types
    ):
        reasons.append("BLOCKED_ENTITY_TYPE")

    minimum_surface_length = int(
        policy["minimum_endpoint_surface_length"]
    )
    for side in ("start", "end"):
        surface = evidence[f"{side}_display_name"].strip()
        mention = evidence[f"{side}_mention_text"].strip()
        if len(surface) < minimum_surface_length:
            reasons.append(
                f"{side.upper()}_SURFACE_TOO_SHORT"
            )
        if any(
            re.search(str(pattern), surface)
            for pattern in policy[
                "blocked_endpoint_surface_patterns"
            ]
        ):
            reasons.append(
                f"{side.upper()}_SURFACE_BLOCKED"
            )
        if bool(
            policy["require_endpoint_mention_display_alignment"]
        ):
            normalized_surface = normalize_surface(surface)
            normalized_mention = normalize_surface(mention)
            minimum_alias_length = int(
                policy["minimum_alias_containment_length"]
            )
            aligned = normalized_surface == normalized_mention
            if (
                not aligned
                and min(
                    len(normalized_surface),
                    len(normalized_mention),
                )
                >= minimum_alias_length
            ):
                aligned = (
                    normalized_surface in normalized_mention
                    or normalized_mention in normalized_surface
                )
            if not aligned:
                reasons.append(
                    f"{side.upper()}_MENTION_DISPLAY_MISMATCH"
                )
    if normalize_surface(
        evidence["start_display_name"]
    ) == normalize_surface(evidence["end_display_name"]):
        reasons.append("SAME_NORMALIZED_ENDPOINT_SURFACE")

    clause = evidence["atomic_clause_text"]
    if any(
        re.search(str(pattern), clause)
        for pattern in policy["blocked_assertion_patterns"]
    ):
        reasons.append("NON_ASSERTIVE_OR_NEGATED_CLAUSE")
    required_clause_pattern = str(
        predicate_rule.get("required_clause_pattern", "")
    )
    if required_clause_pattern and not re.search(
        required_clause_pattern,
        clause,
    ):
        reasons.append("REQUIRED_CLAUSE_PATTERN_MISSING")
    allow_passive_voice = bool(
        family_contract.get("allow_passive_voice", False)
    )
    if (
        bool(policy["block_passive_voice_by_default"])
        and not allow_passive_voice
        and re.search(
            re.escape(predicate)
            + str(policy["passive_predicate_tail_pattern"]),
            clause,
        )
    ):
        reasons.append("PASSIVE_VOICE_NOT_ALLOWED")

    marker_overrides = policy[
        "case_marker_pattern_overrides"
    ]
    marker_patterns = policy["case_marker_patterns_by_role"]
    marker_patterns_by_side: dict[str, str] = {}
    for side in ("START", "END"):
        role = evidence[f"{side.casefold()}_role"]
        override_key = f"{family}:{side}:{role}"
        marker_pattern = str(
            marker_overrides.get(
                override_key,
                marker_patterns.get(role, ""),
            )
        )
        marker_patterns_by_side[side] = marker_pattern
        if not marker_pattern:
            reasons.append(
                f"{side}_CASE_MARKER_POLICY_MISSING"
            )

    if (
        marker_patterns_by_side["START"]
        and marker_patterns_by_side["END"]
    ):
        maximum_tail_length = int(
            policy["maximum_case_marker_tail_length"]
        )
        start_positions = collect_marked_positions(
            clause,
            evidence["start_mention_text"],
            marker_patterns_by_side["START"],
            maximum_tail_length,
        )
        end_positions = collect_marked_positions(
            clause,
            evidence["end_mention_text"],
            marker_patterns_by_side["END"],
            maximum_tail_length,
        )
        predicate_positions = [
            match.start()
            for match in re.finditer(
                re.escape(predicate),
                clause,
            )
        ]
        if not start_positions:
            reasons.append("START_EXPLICIT_CASE_MARKER_MISSING")
        if not end_positions:
            reasons.append("END_EXPLICIT_CASE_MARKER_MISSING")
        if not predicate_positions:
            reasons.append("PREDICATE_NOT_FOUND_IN_CLAUSE")
        valid_orders = [
            (
                start_position,
                start_marker_end,
                end_position,
                end_marker_end,
                predicate_position,
            )
            for start_position, start_marker_end in start_positions
            for end_position, end_marker_end in end_positions
            for predicate_position in predicate_positions
            if start_position
            < end_position
            < predicate_position
        ]
        if (
            bool(
                policy[
                    "require_start_before_end_before_predicate"
                ]
            )
            and start_positions
            and end_positions
            and predicate_positions
            and not valid_orders
        ):
            reasons.append("EXPLICIT_ARGUMENT_ORDER_NOT_CONFIRMED")
        if valid_orders:
            selected_order = min(
                valid_orders,
                key=lambda value: (
                    value[4] - value[2],
                    value[4] - value[0],
                ),
            )
            (
                _,
                start_marker_end,
                end_position,
                end_marker_end,
                predicate_position,
            ) = selected_order
            if (
                predicate_position - start_marker_end
                > int(policy["maximum_explicit_argument_span"])
            ):
                reasons.append("EXPLICIT_ARGUMENT_SPAN_TOO_LONG")
            between_start_and_end = clause[
                start_marker_end:end_position
            ]
            if re.search(
                str(policy["competing_subject_pattern"]),
                between_start_and_end,
            ):
                reasons.append("COMPETING_SUBJECT_BEFORE_TARGET")
            end_role = evidence["end_role"]
            competing_argument_pattern = str(
                policy[
                    "competing_argument_patterns_by_role"
                ].get(end_role, "")
            )
            if (
                competing_argument_pattern
                and re.search(
                    competing_argument_pattern,
                    clause[end_marker_end:predicate_position],
                )
            ):
                reasons.append(
                    "COMPETING_ARGUMENT_BEFORE_PREDICATE"
                )

    return str(predicate_rule["relation_type"]), reasons


def build_relation_rows(
    passed_evidence: Iterable[dict[str, str]],
    policy: dict[str, object],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    """Group explicit evidence into strict and type-review tiers."""
    grouped: dict[
        tuple[str, str, str],
        list[dict[str, str]],
    ] = {}
    for evidence in passed_evidence:
        key = (
            evidence["start_node_id"],
            evidence["refined_relation_type"],
            evidence["end_node_id"],
        )
        grouped.setdefault(key, []).append(evidence)

    passed_rows: list[dict[str, object]] = []
    review_rows: list[dict[str, object]] = []
    type_review_rows: list[dict[str, object]] = []
    for key, group in grouped.items():
        start_node_id, relation_type, end_node_id = key
        strict_group = [
            evidence
            for evidence in group
            if not loads(
                evidence["gate_review_reasons_json"]
            )
        ]
        requires_type_review = not strict_group
        selected_group = group
        if strict_group:
            selected_group = strict_group
        best_evidence = max(
            selected_group,
            key=lambda row: int(row["candidate_score"]),
        )
        evidence_ids = sorted(
            {
                row["nlp_relation_evidence_id"]
                for row in selected_group
            }
        )
        source_datasets = sorted(
            {row["source_dataset"] for row in selected_group}
        )
        support_keys = {
            (
                row["source_dataset"],
                row["source_document_id"],
                row["source_field"],
                row["atomic_clause_text"],
            )
            for row in selected_group
        }
        anchor_exam_term_ids = sorted(
            {
                row["anchor_exam_term_id"]
                for row in selected_group
            }
        )
        predicate_patterns = sorted(
            {
                row["predicate_pattern"]
                for row in selected_group
            }
        )
        gate_review_reasons = sorted(
            {
                reason
                for row in selected_group
                for reason in loads(
                    row["gate_review_reasons_json"]
                )
            }
        )
        corroborated = (
            len(support_keys)
            >= int(policy["minimum_distinct_support_count"])
            or len(source_datasets)
            >= int(policy["minimum_distinct_source_count"])
        )
        gate_status = str(
            policy["statuses"]["single_evidence_review"]
        )
        if requires_type_review:
            gate_status = str(policy["statuses"]["type_review"])
        elif corroborated:
            gate_status = str(policy["statuses"]["passed"])
        identifier_source = "|".join(
            [
                str(policy["policy_version"]),
                start_node_id,
                relation_type,
                end_node_id,
            ]
        )
        relation_id = (
            str(policy["identifier_prefix"])
            + sha256(
                identifier_source.encode("utf-8")
            ).hexdigest()[:20]
        )
        row: dict[str, object] = {
            "safe_relation_candidate_id": relation_id,
            "start_node_id": start_node_id,
            "start_node_kind": best_evidence["start_node_kind"],
            "start_display_name": (
                best_evidence["start_display_name"]
            ),
            "start_entity_type": (
                best_evidence["start_entity_type"]
            ),
            "relation_family": best_evidence["relation_family"],
            "relation_type": relation_type,
            "relation_display": (
                best_evidence["start_display_name"]
                + " -["
                + relation_type
                + "]-> "
                + best_evidence["end_display_name"]
            ),
            "end_node_id": end_node_id,
            "end_node_kind": best_evidence["end_node_kind"],
            "end_display_name": best_evidence["end_display_name"],
            "end_entity_type": best_evidence["end_entity_type"],
            "predicate_patterns_json": dumps(
                predicate_patterns,
                ensure_ascii=False,
            ),
            "evidence_count": len(evidence_ids),
            "distinct_support_count": len(support_keys),
            "source_count": len(source_datasets),
            "source_datasets_json": dumps(
                source_datasets,
                ensure_ascii=False,
            ),
            "anchor_exam_term_count": len(anchor_exam_term_ids),
            "anchor_exam_term_ids_json": dumps(
                anchor_exam_term_ids,
                ensure_ascii=False,
            ),
            "evidence_ids_json": dumps(
                evidence_ids,
                ensure_ascii=False,
            ),
            "maximum_candidate_score": max(
                int(value["candidate_score"])
                for value in selected_group
            ),
            "representative_source_dataset": (
                best_evidence["source_dataset"]
            ),
            "representative_source_document_id": (
                best_evidence["source_document_id"]
            ),
            "representative_source_title": (
                best_evidence["source_title"]
            ),
            "representative_source_url": (
                best_evidence["source_url"]
            ),
            "representative_predicate": (
                best_evidence["predicate_pattern"]
            ),
            "representative_atomic_clause": (
                best_evidence["atomic_clause_text"]
            ),
            "representative_evidence_sentence": (
                best_evidence["evidence_sentence"]
            ),
            "gate_status": gate_status,
            "gate_review_reasons_json": dumps(
                gate_review_reasons,
                ensure_ascii=False,
            ),
            "auto_load_eligible": False,
            "llm_used": False,
            "neo4j_load": False,
            "policy_version": str(policy["policy_version"]),
        }
        if requires_type_review:
            type_review_rows.append(row)
        elif corroborated:
            passed_rows.append(row)
        elif not corroborated:
            review_rows.append(row)
    return passed_rows, review_rows, type_review_rows


def run_exam_term_nlp_relation_gate(
    cli_args: Namespace,
) -> dict[str, object]:
    """Run the deterministic relation gate and save its audit outputs."""
    with Path(cli_args.config).open(
        "r",
        encoding="utf-8",
    ) as input_file:
        policy = load(input_file)

    allowed_candidate_statuses = {
        str(value)
        for value in policy["allowed_candidate_statuses"]
    }
    blocked_node_kinds = {
        str(value) for value in policy["blocked_node_kinds"]
    }
    output_directory = Path(cli_args.output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    candidate_rows: dict[str, dict[str, str]] = {}
    evidence_to_candidate: dict[str, str] = {}
    input_candidate_count = 0
    registered_candidate_gate_scope_count = 0
    open_endpoint_candidate_gate_scope_count = 0
    with Path(cli_args.candidate_csv).open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as input_file:
        for candidate in csv.DictReader(input_file):
            input_candidate_count += 1
            statuses = {
                str(value)
                for value in loads(
                    candidate["candidate_statuses_json"]
                )
            }
            if not statuses.intersection(
                allowed_candidate_statuses
            ):
                continue
            if (
                candidate["start_node_kind"] in blocked_node_kinds
                or candidate["end_node_kind"] in blocked_node_kinds
            ):
                continue
            relation_id = candidate[
                "nlp_relation_candidate_id"
            ]
            touches_open_endpoint = (
                candidate["start_node_kind"]
                == "OPEN_ENTITY_CANDIDATE"
                or candidate["end_node_kind"]
                == "OPEN_ENTITY_CANDIDATE"
            )
            if touches_open_endpoint:
                open_endpoint_candidate_gate_scope_count += 1
            elif not touches_open_endpoint:
                registered_candidate_gate_scope_count += 1
            candidate_rows[relation_id] = {
                "relation_display": candidate[
                    "relation_display"
                ],
                "start_node_kind": candidate[
                    "start_node_kind"
                ],
                "end_node_kind": candidate["end_node_kind"],
            }
            for evidence_id in loads(
                candidate["evidence_ids_json"]
            ):
                evidence_to_candidate[str(evidence_id)] = (
                    relation_id
                )

    passed_evidence: list[dict[str, str]] = []
    passed_original_candidate_ids: set[str] = set()
    evidence_audit_count = 0
    rejection_reasons_by_candidate: dict[
        str,
        set[str],
    ] = {}
    evidence_root = Path(cli_args.evidence_root)
    evidence_paths: list[Path] = []
    for directory in sorted(
        evidence_root.glob(
            cli_args.evidence_directory_pattern
        )
    ):
        evidence_path = directory / cli_args.evidence_filename
        if evidence_path.is_file():
            evidence_paths.append(evidence_path)
    evidence_audit_path = (
        output_directory
        / str(policy["outputs"]["evidence_audit"])
    )
    evidence_audit_columns = [
        "nlp_relation_candidate_id",
        "nlp_relation_evidence_id",
        "relation_display",
        "start_node_kind",
        "start_entity_type",
        "start_role",
        "end_node_kind",
        "end_entity_type",
        "end_role",
        "refined_relation_type",
        "predicate_pattern",
        "candidate_score",
        "source_dataset",
        "source_document_id",
        "atomic_clause_text",
        "gate_pass",
        "gate_quality_status",
        "gate_review_reasons_json",
        "gate_blocking_reasons_json",
        "gate_reasons_json",
    ]
    with evidence_audit_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as audit_file:
        audit_writer = csv.DictWriter(
            audit_file,
            fieldnames=evidence_audit_columns,
        )
        audit_writer.writeheader()
        for evidence_path in evidence_paths:
            with evidence_path.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as input_file:
                for evidence in csv.DictReader(input_file):
                    evidence_id = evidence[
                        "nlp_relation_evidence_id"
                    ]
                    relation_id = evidence_to_candidate.get(
                        evidence_id
                    )
                    if relation_id is None:
                        continue
                    relation_type, reasons = (
                        evaluate_gate_evidence(
                            evidence,
                            policy,
                        )
                    )
                    review_only_reasons = {
                        str(value)
                        for value in policy[
                            "review_only_reasons"
                        ]
                    }
                    gate_review_reasons = sorted(
                        set(reasons).intersection(
                            review_only_reasons
                        )
                    )
                    blocking_reasons = sorted(
                        set(reasons).difference(
                            review_only_reasons
                        )
                    )
                    gate_pass = not blocking_reasons
                    gate_quality_status = "GATE_REJECTED"
                    if gate_pass and gate_review_reasons:
                        gate_quality_status = str(
                            policy["statuses"]["type_review"]
                        )
                    elif gate_pass:
                        gate_quality_status = (
                            "GATE_PASSED_STRICT"
                        )
                    evidence[
                        "refined_relation_type"
                    ] = relation_type
                    evidence[
                        "gate_review_reasons_json"
                    ] = dumps(
                        gate_review_reasons,
                        ensure_ascii=False,
                    )
                    audit_writer.writerow(
                        {
                            "nlp_relation_candidate_id": (
                                relation_id
                            ),
                            "nlp_relation_evidence_id": (
                                evidence_id
                            ),
                            "relation_display": (
                                candidate_rows[relation_id][
                                    "relation_display"
                                ]
                            ),
                            "start_node_kind": evidence[
                                "start_node_kind"
                            ],
                            "start_entity_type": evidence[
                                "start_entity_type"
                            ],
                            "start_role": evidence[
                                "start_role"
                            ],
                            "end_node_kind": evidence[
                                "end_node_kind"
                            ],
                            "end_entity_type": evidence[
                                "end_entity_type"
                            ],
                            "end_role": evidence[
                                "end_role"
                            ],
                            "refined_relation_type": (
                                relation_type
                            ),
                            "predicate_pattern": evidence[
                                "predicate_pattern"
                            ],
                            "candidate_score": evidence[
                                "candidate_score"
                            ],
                            "source_dataset": evidence[
                                "source_dataset"
                            ],
                            "source_document_id": evidence[
                                "source_document_id"
                            ],
                            "atomic_clause_text": evidence[
                                "atomic_clause_text"
                            ],
                            "gate_pass": gate_pass,
                            "gate_quality_status": (
                                gate_quality_status
                            ),
                            "gate_review_reasons_json": dumps(
                                gate_review_reasons,
                                ensure_ascii=False,
                            ),
                            "gate_blocking_reasons_json": dumps(
                                blocking_reasons,
                                ensure_ascii=False,
                            ),
                            "gate_reasons_json": dumps(
                                reasons,
                                ensure_ascii=False,
                            ),
                        }
                    )
                    evidence_audit_count += 1
                    if gate_pass:
                        passed_evidence.append(evidence)
                        passed_original_candidate_ids.add(
                            relation_id
                        )
                    elif not gate_pass:
                        rejection_reasons_by_candidate.setdefault(
                            relation_id,
                            set(),
                        ).update(blocking_reasons)

    (
        passed_rows,
        review_rows,
        type_review_rows,
    ) = build_relation_rows(passed_evidence, policy)
    exclusions_path = (
        output_directory
        / str(policy["outputs"]["exclusions"])
    )
    excluded_candidate_count = 0
    with exclusions_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as exclusions_file:
        exclusions_writer = csv.DictWriter(
            exclusions_file,
            fieldnames=[
                "nlp_relation_candidate_id",
                "relation_display",
                "gate_reasons_json",
            ],
        )
        exclusions_writer.writeheader()
        for relation_id, candidate in candidate_rows.items():
            if relation_id in passed_original_candidate_ids:
                continue
            exclusions_writer.writerow(
                {
                    "nlp_relation_candidate_id": relation_id,
                    "relation_display": candidate[
                        "relation_display"
                    ],
                    "gate_reasons_json": dumps(
                        sorted(
                            rejection_reasons_by_candidate.get(
                                relation_id,
                                {"NO_GATE_PASSED_EVIDENCE"},
                            )
                        ),
                        ensure_ascii=False,
                    ),
                }
            )
            excluded_candidate_count += 1

    strict_rows = passed_rows + review_rows
    all_explicit_rows = strict_rows + type_review_rows
    registered_rows = [
        row
        for row in strict_rows
        if (
            row["start_node_kind"]
            != "OPEN_ENTITY_CANDIDATE"
            and row["end_node_kind"]
            != "OPEN_ENTITY_CANDIDATE"
        )
    ]
    open_endpoint_rows = [
        row
        for row in strict_rows
        if (
            row["start_node_kind"]
            == "OPEN_ENTITY_CANDIDATE"
            or row["end_node_kind"]
            == "OPEN_ENTITY_CANDIDATE"
        )
    ]
    output_tables = {
        "safe_candidates": pd.DataFrame(strict_rows),
        "all_explicit_candidates": pd.DataFrame(
            all_explicit_rows
        ),
        "corroborated_candidates": pd.DataFrame(passed_rows),
        "registered_candidates": pd.DataFrame(registered_rows),
        "open_endpoint_candidates": pd.DataFrame(
            open_endpoint_rows
        ),
        "single_evidence_review": pd.DataFrame(review_rows),
        "type_review_candidates": pd.DataFrame(
            type_review_rows
        ),
    }
    output_paths: dict[str, str] = {
        "exclusions": str(exclusions_path),
        "evidence_audit": str(evidence_audit_path),
    }
    for output_name, table in output_tables.items():
        output_path = (
            output_directory
            / str(policy["outputs"][output_name])
        )
        table.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )
        output_paths[output_name] = str(output_path)

    statistics = {
        "input_relation_candidate_count": input_candidate_count,
        "registered_candidate_gate_scope_count": (
            registered_candidate_gate_scope_count
        ),
        "open_endpoint_candidate_gate_scope_count": (
            open_endpoint_candidate_gate_scope_count
        ),
        "candidate_gate_input_count": len(candidate_rows),
        "evidence_audit_count": evidence_audit_count,
        "gate_passed_evidence_count": len(passed_evidence),
        "safe_relation_candidate_count": len(strict_rows),
        "all_explicit_relation_candidate_count": len(
            all_explicit_rows
        ),
        "type_review_relation_candidate_count": len(
            type_review_rows
        ),
        "corroborated_relation_candidate_count": (
            len(passed_rows)
        ),
        "registered_relation_candidate_count": len(
            registered_rows
        ),
        "open_endpoint_relation_candidate_count": len(
            open_endpoint_rows
        ),
        "single_evidence_review_count": len(review_rows),
        "excluded_candidate_count": excluded_candidate_count,
        "llm_used": False,
        "neo4j_load": False,
    }
    summary_rows = [
        {"metric": key.upper(), "count": value}
        for key, value in statistics.items()
    ]
    summary_path = (
        output_directory
        / str(policy["outputs"]["summary"])
    )
    pd.DataFrame(summary_rows).to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )
    output_paths["summary"] = str(summary_path)
    manifest = {
        "status": "COMPLETED",
        "stage": "EXAM_TERM_NLP_RELATION_GATE",
        "policy_version": str(policy["policy_version"]),
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = output_directory / "manifest.json"
    with manifest_path.open(
        "w",
        encoding="utf-8",
    ) as output_file:
        dump(
            manifest,
            output_file,
            ensure_ascii=False,
            indent=2,
        )
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """Run the gate from the command line."""
    result = run_exam_term_nlp_relation_gate(
        parse_arguments()
    )
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

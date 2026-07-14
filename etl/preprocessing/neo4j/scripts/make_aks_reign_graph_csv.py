"""AKS 왕 문서에서 국가·재위 구조와 출처 근거 CSV를 생성한다.

국가 문서는 기존 CanonicalEntity에 Polity 라벨을 추가하고, 왕과 국가 사이의
기간 정보는 Reign 노드로 분리한다. 복위처럼 재위가 여러 구간이면 구간별 Reign
노드를 생성한다. 자동 판단이 불가능한 결과는 staging 검토 CSV에 남긴다.
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from neo4j_common import require_file, resolve_import_dir, resolve_project_root


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)

    return {
        "source_seed": neo4j_dir / "seed" / "aks_source_seed.csv",
        "polity_seed": neo4j_dir / "seed" / "aks_polity_seed.csv",
        "override_seed": neo4j_dir / "seed" / "aks_reign_override_seed.csv",
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
        "review_path": neo4j_dir / "staging" / "aks_reign_review.csv",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="AKS 왕 문서에서 Polity/Reign graph CSV를 생성한다."
    )
    parser.add_argument(
        "--source-seed-path",
        type=Path,
        default=default_paths["source_seed"],
    )
    parser.add_argument(
        "--polity-seed-path",
        type=Path,
        default=default_paths["polity_seed"],
    )
    parser.add_argument(
        "--override-seed-path",
        type=Path,
        default=default_paths["override_seed"],
    )
    parser.add_argument("--articles-path", type=Path, default=None)
    parser.add_argument("--nodes-dir", type=Path, default=default_paths["nodes_dir"])
    parser.add_argument(
        "--relations-dir",
        type=Path,
        default=default_paths["relations_dir"],
    )
    parser.add_argument(
        "--review-path",
        type=Path,
        default=default_paths["review_path"],
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="지정하지 않으면 원문과 seed를 검증하고 CSV는 저장하지 않는다.",
    )

    return parser.parse_args()


def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def read_single_source_config(source_seed_path):
    require_file(source_seed_path, "AKS source seed")

    with source_seed_path.open("r", encoding="utf-8-sig", newline="") as seed_file:
        rows = list(csv.DictReader(seed_file))

    if len(rows) != 1:
        raise ValueError("AKS source seed에는 source 설정이 정확히 한 행이어야 합니다.")

    source_config = {key: clean_text(value) for key, value in rows[0].items()}
    required_columns = {
        "source_id",
        "source_record_id_prefix",
        "canonical_id_prefix",
        "reign_id_prefix",
        "raw_relative_path",
    }
    missing_columns = sorted(
        column_name
        for column_name in required_columns
        if source_config.get(column_name, "") == ""
    )

    if len(missing_columns) > 0:
        raise ValueError(
            "AKS source seed 필수값이 비어 있습니다: " + ", ".join(missing_columns)
        )

    return source_config


def read_seed_rows(seed_path, purpose, required_columns):
    require_file(seed_path, purpose)

    with seed_path.open("r", encoding="utf-8-sig", newline="") as seed_file:
        reader = csv.DictReader(seed_file)
        fieldnames = set(reader.fieldnames or [])
        missing_columns = sorted(required_columns - fieldnames)

        if len(missing_columns) > 0:
            raise ValueError(
                f"{purpose} 필수 컬럼이 없습니다: " + ", ".join(missing_columns)
            )

        return [
            {key: clean_text(value) for key, value in row.items()}
            for row in reader
        ]


def read_polity_seed(polity_seed_path):
    rows = read_seed_rows(
        polity_seed_path,
        "AKS polity seed",
        {
            "polity_id",
            "canonical_eid",
            "name",
            "context_aliases",
            "polity_kind",
            "review_status",
        },
    )
    polity_ids = [row["polity_id"] for row in rows]
    canonical_eids = [row["canonical_eid"] for row in rows]

    if len(polity_ids) != len(set(polity_ids)):
        raise ValueError("AKS polity seed의 polity_id가 중복되었습니다.")

    if len(canonical_eids) != len(set(canonical_eids)):
        raise ValueError("AKS polity seed의 canonical_eid가 중복되었습니다.")

    for row in rows:
        aliases = [
            alias.strip()
            for alias in row["context_aliases"].split("|")
            if alias.strip() != ""
        ]

        if len(aliases) == 0:
            raise ValueError(
                f"AKS polity seed의 context_aliases가 비어 있습니다: {row['polity_id']}"
            )

        row["aliases"] = aliases

    return rows


def read_override_seed(override_seed_path, polity_by_id):
    rows = read_seed_rows(
        override_seed_path,
        "AKS reign override seed",
        {
            "source_eid",
            "override_action",
            "polity_id",
            "succession_order",
            "role_title",
            "reign_period_text",
            "evidence_field",
            "evidence_text",
            "review_status",
        },
    )
    allowed_actions = {"ADD", "REPLACE"}

    for row in rows:
        if row["override_action"] not in allowed_actions:
            raise ValueError(
                "AKS reign override action은 ADD 또는 REPLACE여야 합니다: "
                f"{row['source_eid']}"
            )

        if row["polity_id"] not in polity_by_id:
            raise ValueError(
                "AKS reign override에 알 수 없는 polity_id가 있습니다: "
                f"{row['polity_id']}"
            )

        if not row["succession_order"].isdigit():
            raise ValueError(
                "AKS reign override의 succession_order가 정수가 아닙니다: "
                f"{row['source_eid']}"
            )

    return rows


def resolve_articles_path(args, source_config, project_root):
    if args.articles_path is not None:
        return args.articles_path.resolve()

    return (project_root / source_config["raw_relative_path"]).resolve()


def normalize_match_text(value):
    return re.sub(r"[\s,.·ㆍ()]+", "", clean_text(value))


def build_monarch_definition_pattern():
    role_pattern = "국왕|임금|황제|마립간|이사금|차차웅|거서간|왕"
    return re.compile(
        rf"^(?P<context>.*?)제\s*(?P<order>\d+)\s*대"
        rf"(?:\s*\((?P<date_before>[^()]*)\))?\s*"
        rf"(?P<title>{role_pattern})"
        rf"(?:\s*\((?P<date_after>[^()]*)\))?"
        rf"\s*(?:이다)?\s*[.]?\s*$"
    )


def match_polity(context_text, polity_rows):
    normalized_context = normalize_match_text(context_text)
    matches = []

    for polity_row in polity_rows:
        for alias in polity_row["aliases"]:
            normalized_alias = normalize_match_text(alias)

            if normalized_alias in normalized_context:
                matches.append((len(normalized_alias), polity_row))

    if len(matches) == 0:
        return None

    matches.sort(key=lambda match: (-match[0], match[1]["polity_id"]))
    longest_length = matches[0][0]
    longest_matches = {
        match[1]["polity_id"]: match[1]
        for match in matches
        if match[0] == longest_length
    }

    if len(longest_matches) > 1:
        matched_ids = ", ".join(sorted(longest_matches))
        raise ValueError(
            f"같은 길이의 국가 alias가 충돌합니다: {context_text} -> {matched_ids}"
        )

    return next(iter(longest_matches.values()))


def clean_reign_period_text(value):
    clean_value = clean_text(value)
    clean_value = re.sub(r"^재위\s*:\s*", "", clean_value)
    clean_value = re.sub(r"(?:이다|이며|이고|이다\.)\s*$", "", clean_value)
    return clean_value.strip(" .")


def extract_summary_reign_period(summary_text, succession_order):
    summary = clean_text(summary_text)
    summary_excerpt = summary[:600]
    year_endpoint = (
        r"(?:BCE|CE|기원전|서기전|서기)?\.?\s*"
        r"(?:\d{1,4}\s*(?:년|세기)?|\?|미상)"
    )
    year_interval = (
        rf"{year_endpoint}"
        rf"(?:\s*(?:~|∼|-|부터)\s*{year_endpoint})?"
    )
    reign_period = rf"{year_interval}(?:\s*[,;]\s*{year_interval})*"
    patterns = [
        re.compile(
            rf"제\s*{succession_order}\s*대\s*"
            rf"\(\s*재위\s*:\s*(?P<date>{reign_period})\s*\)"
        ),
        re.compile(rf"재위\s*기간은\s*(?P<date>{reign_period})"),
        re.compile(rf"재위는\s*(?P<date>{reign_period})"),
        re.compile(
            rf"(?P<date>{year_interval})"
            r"\s*에\s*재위한"
        ),
        re.compile(
            rf"\d+\s*년간\s*\(\s*(?P<date>{reign_period})\s*\)\s*재위"
        ),
    ]

    for pattern in patterns:
        match = pattern.search(summary_excerpt)

        if match is not None:
            return clean_reign_period_text(match.group("date"))

    return ""


def split_reign_periods(reign_period_text):
    clean_period = clean_reign_period_text(reign_period_text)

    if clean_period == "":
        return [""]

    periods = [period.strip() for period in re.split(r"[,;]", clean_period)]
    return [period for period in periods if period != ""]


def parse_year_endpoint(endpoint_text):
    normalized = clean_text(endpoint_text).replace(" ", "")

    if normalized in {"", "?", "미상"}:
        return None, "UNKNOWN"

    era_type = "CE"

    if re.search(r"BCE|기원전|서기전", normalized, flags=re.IGNORECASE):
        era_type = "BCE"
    elif re.search(r"CE|서기", normalized, flags=re.IGNORECASE):
        era_type = "CE"

    year_match = re.search(r"\d+", normalized)

    if year_match is None:
        return None, era_type

    year_value = int(year_match.group())

    if year_value == 0:
        raise ValueError(f"역사 연도에는 0년을 사용할 수 없습니다: {endpoint_text}")

    if era_type == "BCE":
        year_value *= -1

    return year_value, era_type


def parse_reign_period(period_text):
    normalized = clean_text(period_text)
    normalized = normalized.replace("∼", "~").replace("부터", "~")

    if normalized == "":
        return {
            "start_year": "",
            "end_year": "",
            "date_precision": "UNKNOWN",
        }

    if "세기" in normalized:
        precision = "CENTURY"

        if "~" in normalized or "-" in normalized:
            precision = "CENTURY_RANGE"

        return {
            "start_year": "",
            "end_year": "",
            "date_precision": precision,
        }

    range_parts = re.split(r"~|(?<=\d)\s*-\s*(?=\d|\?)", normalized, maxsplit=1)

    if len(range_parts) == 1:
        year_value, _ = parse_year_endpoint(range_parts[0])

        if year_value is None:
            return {
                "start_year": "",
                "end_year": "",
                "date_precision": "UNKNOWN",
            }

        return {
            "start_year": str(year_value),
            "end_year": str(year_value),
            "date_precision": "EXACT_YEAR",
        }

    start_year, start_era = parse_year_endpoint(range_parts[0])
    end_year, end_era = parse_year_endpoint(range_parts[1])
    end_has_explicit_era = re.search(
        r"BCE|CE|기원전|서기", range_parts[1], flags=re.IGNORECASE
    )

    if (
        start_year is not None
        and start_era == "BCE"
        and end_year is not None
        and end_era == "CE"
        and end_has_explicit_era is None
    ):
        end_year *= -1

    if start_year is not None and end_year is not None:
        return {
            "start_year": str(start_year),
            "end_year": str(end_year),
            "date_precision": "YEAR_RANGE",
        }

    if start_year is not None or end_year is not None:
        start_year_text = ""
        end_year_text = ""

        if start_year is not None:
            start_year_text = str(start_year)

        if end_year is not None:
            end_year_text = str(end_year)

        return {
            "start_year": start_year_text,
            "end_year": end_year_text,
            "date_precision": "PARTIAL",
        }

    return {
        "start_year": "",
        "end_year": "",
        "date_precision": "UNKNOWN",
    }


def build_claim_intervals(
    article,
    polity_row,
    succession_order,
    role_title,
    reign_period_text,
    evidence_field,
    evidence_text,
    match_method,
    review_status,
    note="",
):
    intervals = []

    for period_text in split_reign_periods(reign_period_text):
        parsed_period = parse_reign_period(period_text)
        intervals.append(
            {
                "source_eid": clean_text(article.get("eid")),
                "monarch_name": clean_text(article.get("headword")),
                "polity_id": polity_row["polity_id"],
                "polity_name": polity_row["name"],
                "succession_order": str(succession_order),
                "role_title": role_title,
                "reign_period_text": period_text,
                "evidence_field": evidence_field,
                "evidence_text": evidence_text,
                "match_method": match_method,
                "review_status": review_status,
                "note": note,
                **parsed_period,
            }
        )

    return intervals


def build_override_claims(article, override_rows, polity_by_id):
    claims = []

    for override_row in override_rows:
        polity_row = polity_by_id[override_row["polity_id"]]
        claims.extend(
            build_claim_intervals(
                article=article,
                polity_row=polity_row,
                succession_order=override_row["succession_order"],
                role_title=override_row["role_title"],
                reign_period_text=override_row["reign_period_text"],
                evidence_field=override_row["evidence_field"],
                evidence_text=override_row["evidence_text"],
                match_method="CURATED_OVERRIDE",
                review_status=override_row["review_status"],
                note=override_row.get("note", ""),
            )
        )

    return claims


def build_automatic_claim(article, definition_match, polity_rows):
    source_eid = clean_text(article.get("eid"))
    monarch_name = clean_text(article.get("headword"))
    succession_order = definition_match.group("order")
    context_text = definition_match.group("context")
    polity_row = match_polity(context_text, polity_rows)

    if polity_row is None:
        return [], {
            "source_eid": source_eid,
            "name": monarch_name,
            "issue_code": "UNMAPPED_POLITY",
            "details": clean_text(article.get("definition")),
            "polity_id": "",
            "succession_order": succession_order,
            "reign_period_text": "",
        }

    definition_period = (
        definition_match.group("date_before")
        or definition_match.group("date_after")
        or ""
    )
    reign_period_text = clean_reign_period_text(definition_period)
    evidence_field = "definition"
    evidence_text = clean_text(article.get("definition"))

    if reign_period_text == "":
        summary_period_text = extract_summary_reign_period(
            article.get("summary"),
            succession_order,
        )

        if summary_period_text != "":
            reign_period_text = summary_period_text
            evidence_field = "summary"
            evidence_text = clean_text(article.get("summary"))[:600]

    claims = build_claim_intervals(
        article=article,
        polity_row=polity_row,
        succession_order=succession_order,
        role_title=definition_match.group("title"),
        reign_period_text=reign_period_text,
        evidence_field=evidence_field,
        evidence_text=evidence_text,
        match_method="STRUCTURED_AKS_TEXT",
        review_status="SOURCE_DERIVED",
    )

    return claims, None


def process_articles(
    articles_path,
    polity_rows,
    override_rows,
):
    require_file(articles_path, "AKS articles_detail")
    polity_by_eid = {row["canonical_eid"]: row for row in polity_rows}
    polity_by_id = {row["polity_id"]: row for row in polity_rows}
    overrides_by_eid = defaultdict(list)
    replace_eids = set()

    for override_row in override_rows:
        source_eid = override_row["source_eid"]
        overrides_by_eid[source_eid].append(override_row)

        if override_row["override_action"] == "REPLACE":
            replace_eids.add(source_eid)

    monarch_pattern = build_monarch_definition_pattern()
    found_polity_eids = set()
    found_override_eids = set()
    polity_articles = {}
    claims = []
    review_rows = []

    with articles_path.open("r", encoding="utf-8-sig") as articles_file:
        for line_number, raw_line in enumerate(articles_file, 1):
            if raw_line.strip() == "":
                continue

            try:
                article = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"AKS JSONL parsing failed at line {line_number}: {exc}"
                ) from exc

            source_eid = clean_text(article.get("eid"))

            if source_eid in polity_by_eid:
                found_polity_eids.add(source_eid)
                polity_articles[source_eid] = article

            if source_eid in overrides_by_eid:
                found_override_eids.add(source_eid)
                claims.extend(
                    build_override_claims(
                        article,
                        overrides_by_eid[source_eid],
                        polity_by_id,
                    )
                )

            definition = clean_text(article.get("definition"))
            definition_match = monarch_pattern.search(definition)

            if definition_match is None:
                continue

            if source_eid in replace_eids:
                continue

            automatic_claims, review_row = build_automatic_claim(
                article,
                definition_match,
                polity_rows,
            )
            claims.extend(automatic_claims)

            if review_row is not None:
                review_rows.append(review_row)

    missing_polity_eids = sorted(set(polity_by_eid) - found_polity_eids)
    missing_override_eids = sorted(set(overrides_by_eid) - found_override_eids)

    if len(missing_polity_eids) > 0:
        raise ValueError(
            "AKS 원문에서 polity seed 문서를 찾지 못했습니다: "
            + ", ".join(missing_polity_eids)
        )

    if len(missing_override_eids) > 0:
        raise ValueError(
            "AKS 원문에서 reign override 문서를 찾지 못했습니다: "
            + ", ".join(missing_override_eids)
        )

    return polity_articles, claims, review_rows


def add_claim_review_results(claims, review_rows):
    claim_groups = defaultdict(list)

    for claim in claims:
        group_key = (claim["polity_id"], claim["succession_order"])
        claim_groups[group_key].append(claim)

        if claim["date_precision"] in {
            "UNKNOWN",
            "PARTIAL",
            "CENTURY",
            "CENTURY_RANGE",
        }:
            if claim["review_status"] == "SOURCE_DERIVED":
                claim["review_status"] = "REVIEW_REQUIRED"

            review_rows.append(
                {
                    "source_eid": claim["source_eid"],
                    "name": claim["monarch_name"],
                    "issue_code": f"DATE_{claim['date_precision']}",
                    "details": "정확한 연도 범위를 자동 확정할 수 없습니다.",
                    "polity_id": claim["polity_id"],
                    "succession_order": claim["succession_order"],
                    "reign_period_text": claim["reign_period_text"],
                }
            )

    for (polity_id, succession_order), group_claims in claim_groups.items():
        source_eids = sorted({claim["source_eid"] for claim in group_claims})

        if len(source_eids) < 2:
            continue

        duplicate_details = "동일 국가·왕위 순번 후보: " + ", ".join(source_eids)

        for claim in group_claims:
            if claim["review_status"] == "SOURCE_DERIVED":
                claim["review_status"] = "REVIEW_REQUIRED"

            review_rows.append(
                {
                    "source_eid": claim["source_eid"],
                    "name": claim["monarch_name"],
                    "issue_code": "DUPLICATE_POLITY_ORDER",
                    "details": duplicate_details,
                    "polity_id": polity_id,
                    "succession_order": succession_order,
                    "reign_period_text": claim["reign_period_text"],
                }
            )


def assign_reign_ids(claims, source_config):
    def reign_sort_key(claim):
        start_year = 0

        if claim["start_year"] != "":
            start_year = int(claim["start_year"])

        return (
            claim["source_eid"],
            claim["polity_id"],
            claim["start_year"] == "",
            start_year,
            claim["reign_period_text"],
        )

    claims.sort(
        key=reign_sort_key
    )
    interval_counts = Counter()

    for claim in claims:
        group_key = (claim["source_eid"], claim["polity_id"])
        interval_counts[group_key] += 1
        interval_index = interval_counts[group_key]
        claim["interval_index"] = str(interval_index)
        claim["reign_id"] = (
            f"{source_config['reign_id_prefix']}:{claim['source_eid']}:"
            f"{claim['polity_id']}:{interval_index:02d}"
        )


def build_output_rows(
    polity_rows,
    polity_articles,
    claims,
    source_config,
):
    polity_output = []

    for polity_row in polity_rows:
        article = polity_articles[polity_row["canonical_eid"]]
        polity_output.append(
            {
                "canonical_id": (
                    f"{source_config['canonical_id_prefix']}:"
                    f"{polity_row['canonical_eid']}"
                ),
                "polity_id": polity_row["polity_id"],
                "name": polity_row["name"],
                "polity_kind": polity_row["polity_kind"],
                "anchor_source_id": source_config["source_id"],
                "anchor_source_eid": polity_row["canonical_eid"],
                "review_status": polity_row["review_status"],
                "source_headword": clean_text(article.get("headword")),
            }
        )

    reign_output = []
    held_reign_output = []
    of_polity_output = []
    evidence_output = []

    for claim in claims:
        monarch_canonical_id = (
            f"{source_config['canonical_id_prefix']}:{claim['source_eid']}"
        )
        source_record_id = (
            f"{source_config['source_record_id_prefix']}:{claim['source_eid']}"
        )
        reign_output.append(
            {
                "reign_id": claim["reign_id"],
                "name": f"{claim['monarch_name']}의 {claim['polity_name']} 재위",
                "succession_order": claim["succession_order"],
                "interval_index": claim["interval_index"],
                "start_year": claim["start_year"],
                "end_year": claim["end_year"],
                "date_precision": claim["date_precision"],
                "reign_period_text": claim["reign_period_text"],
                "role_title": claim["role_title"],
                "anchor_source_id": source_config["source_id"],
                "anchor_source_eid": claim["source_eid"],
                "match_method": claim["match_method"],
                "review_status": claim["review_status"],
                "note": claim["note"],
            }
        )
        held_reign_output.append(
            {
                "start_canonical_id": monarch_canonical_id,
                "end_reign_id": claim["reign_id"],
                "relation_type": "HELD_REIGN",
                "role_title": claim["role_title"],
                "match_method": claim["match_method"],
                "review_status": claim["review_status"],
            }
        )
        of_polity_output.append(
            {
                "start_reign_id": claim["reign_id"],
                "end_polity_id": claim["polity_id"],
                "relation_type": "OF_POLITY",
                "match_method": claim["match_method"],
                "review_status": claim["review_status"],
            }
        )
        evidence_output.append(
            {
                "start_source_record_id": source_record_id,
                "end_reign_id": claim["reign_id"],
                "relation_type": "EVIDENCE_FOR",
                "evidence_field": claim["evidence_field"],
                "evidence_text": claim["evidence_text"],
                "reign_period_text": claim["reign_period_text"],
                "match_method": claim["match_method"],
                "review_status": claim["review_status"],
            }
        )

    return {
        "polities": polity_output,
        "reigns": reign_output,
        "monarch_held_reign": held_reign_output,
        "reign_of_polity": of_polity_output,
        "source_article_evidence_for_reign": evidence_output,
    }


def build_output_specs(args, output_rows, review_rows):
    return {
        "polities": {
            "path": args.nodes_dir / "polities.csv",
            "rows": output_rows["polities"],
            "fieldnames": [
                "canonical_id",
                "polity_id",
                "name",
                "polity_kind",
                "anchor_source_id",
                "anchor_source_eid",
                "review_status",
                "source_headword",
            ],
        },
        "reigns": {
            "path": args.nodes_dir / "reigns.csv",
            "rows": output_rows["reigns"],
            "fieldnames": [
                "reign_id",
                "name",
                "succession_order",
                "interval_index",
                "start_year",
                "end_year",
                "date_precision",
                "reign_period_text",
                "role_title",
                "anchor_source_id",
                "anchor_source_eid",
                "match_method",
                "review_status",
                "note",
            ],
        },
        "monarch_held_reign": {
            "path": args.relations_dir / "monarch_held_reign.csv",
            "rows": output_rows["monarch_held_reign"],
            "fieldnames": [
                "start_canonical_id",
                "end_reign_id",
                "relation_type",
                "role_title",
                "match_method",
                "review_status",
            ],
        },
        "reign_of_polity": {
            "path": args.relations_dir / "reign_of_polity.csv",
            "rows": output_rows["reign_of_polity"],
            "fieldnames": [
                "start_reign_id",
                "end_polity_id",
                "relation_type",
                "match_method",
                "review_status",
            ],
        },
        "source_article_evidence_for_reign": {
            "path": args.relations_dir / "source_article_evidence_for_reign.csv",
            "rows": output_rows["source_article_evidence_for_reign"],
            "fieldnames": [
                "start_source_record_id",
                "end_reign_id",
                "relation_type",
                "evidence_field",
                "evidence_text",
                "reign_period_text",
                "match_method",
                "review_status",
            ],
        },
        "aks_reign_review": {
            "path": args.review_path,
            "rows": review_rows,
            "fieldnames": [
                "source_eid",
                "name",
                "issue_code",
                "details",
                "polity_id",
                "succession_order",
                "reign_period_text",
            ],
        },
    }


def save_output_specs(output_specs):
    temporary_paths = []

    try:
        for output_spec in output_specs.values():
            output_path = output_spec["path"]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
            temporary_paths.append(temporary_path)

            with temporary_path.open(
                "w", encoding="utf-8-sig", newline=""
            ) as output_file:
                writer = csv.DictWriter(
                    output_file,
                    fieldnames=output_spec["fieldnames"],
                    extrasaction="raise",
                )
                writer.writeheader()
                writer.writerows(output_spec["rows"])

        for output_spec in output_specs.values():
            output_path = output_spec["path"]
            temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
            temporary_path.replace(output_path)
    except Exception:
        for temporary_path in temporary_paths:
            if temporary_path.exists():
                temporary_path.unlink()

        raise


def print_summary(articles_path, output_specs, save_outputs):
    print(f"articles_path: {articles_path}")

    for output_name, output_spec in output_specs.items():
        print(f"{output_name}.csv: {len(output_spec['rows'])} rows")
        print(f"output_path: {output_spec['path']}")

    if not save_outputs:
        print("dry_run: no files saved. Use --save to write CSV files.")


def main():
    script_path = Path(__file__).resolve()
    project_root = resolve_project_root(script_path)
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    source_config = read_single_source_config(args.source_seed_path)
    polity_rows = read_polity_seed(args.polity_seed_path)
    polity_by_id = {row["polity_id"]: row for row in polity_rows}
    override_rows = read_override_seed(args.override_seed_path, polity_by_id)
    articles_path = resolve_articles_path(args, source_config, project_root)
    polity_articles, claims, review_rows = process_articles(
        articles_path,
        polity_rows,
        override_rows,
    )
    add_claim_review_results(claims, review_rows)
    assign_reign_ids(claims, source_config)
    output_rows = build_output_rows(
        polity_rows,
        polity_articles,
        claims,
        source_config,
    )
    output_specs = build_output_specs(args, output_rows, review_rows)

    if args.save:
        save_output_specs(output_specs)

    print_summary(articles_path, output_specs, args.save)


if __name__ == "__main__":
    main()

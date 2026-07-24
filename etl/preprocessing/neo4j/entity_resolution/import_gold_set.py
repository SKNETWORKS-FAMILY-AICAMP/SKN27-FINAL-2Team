from argparse import ArgumentParser
from collections import defaultdict
from csv import DictReader
from datetime import datetime, timezone
from json import dumps
from pathlib import Path, PurePosixPath
import posixpath
from zipfile import ZipFile
import sys
import xml.etree.ElementTree as ElementTree

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import load_pipeline_policy
from entity_resolution.build_gold_set import calculate_file_sha256
from entity_resolution.semantic_review import load_jsonl, write_jsonl


def load_shared_strings(archive: ZipFile) -> list[str]:
    """XLSX sharedStrings를 일반 문자열 목록으로 읽는다."""
    shared_string_path = "xl/sharedStrings.xml"
    if shared_string_path not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read(shared_string_path))
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    strings: list[str] = []
    for item in root.iter(f"{namespace}si"):
        text = "".join(
            element.text or "" for element in item.iter(f"{namespace}t")
        )
        strings.append(text)
    return strings


def resolve_sheet_xml_path(archive: ZipFile, sheet_name: str) -> str:
    """workbook 관계를 따라 시트 이름에 대응하는 XML 경로를 찾는다."""
    workbook_root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationship_root = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    workbook_namespace = (
        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    )
    relationship_attribute = (
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    )
    relationship_namespace = (
        "{http://schemas.openxmlformats.org/package/2006/relationships}"
    )
    relationship_id = ""
    for sheet in workbook_root.iter(f"{workbook_namespace}sheet"):
        if sheet.attrib.get("name") == sheet_name:
            relationship_id = str(sheet.attrib.get(relationship_attribute, ""))
            break
    if not relationship_id:
        raise ValueError(f"XLSX에 '{sheet_name}' 시트가 없습니다.")

    target = ""
    for relationship in relationship_root.iter(
        f"{relationship_namespace}Relationship"
    ):
        if relationship.attrib.get("Id") == relationship_id:
            target = str(relationship.attrib.get("Target", ""))
            break
    if not target:
        raise ValueError(f"'{sheet_name}' 시트 XML 관계를 찾을 수 없습니다.")

    resolved_path = target.lstrip("/")
    if not target.startswith("/"):
        resolved_path = posixpath.normpath(
            str(PurePosixPath("xl") / target)
        )
    return str(PurePosixPath(resolved_path))


def cell_column_index(cell_reference: str) -> int:
    """A1 형식 셀 주소의 열을 0부터 시작하는 정수로 바꾼다."""
    column_text = "".join(
        character for character in cell_reference if character.isalpha()
    )
    if not column_text:
        raise ValueError(f"유효하지 않은 셀 주소입니다: {cell_reference}")
    column_index = 0
    for character in column_text.upper():
        column_index = column_index * 26 + ord(character) - ord("A") + 1
    return column_index - 1


def read_cell_text(
    cell: ElementTree.Element,
    shared_strings: list[str],
) -> str:
    """문자열·수치·불리언 셀을 검수용 문자열로 정규화한다."""
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(
            element.text or "" for element in cell.iter(f"{namespace}t")
        )
    value_element = cell.find(f"{namespace}v")
    if value_element is None or value_element.text is None:
        return ""
    raw_value = value_element.text
    if cell_type == "s":
        shared_index = int(raw_value)
        if shared_index >= len(shared_strings):
            raise ValueError(f"shared string index 범위를 벗어났습니다: {shared_index}")
        return shared_strings[shared_index]
    if cell_type == "b":
        bool_value = "FALSE"
        if raw_value == "1":
            bool_value = "TRUE"
        return bool_value
    return raw_value


def read_xlsx_sheet_records(
    workbook_path: str,
    sheet_name: str,
) -> list[dict[str, str]]:
    """외부 Excel 엔진 없이 XLSX 시트를 header 기반 레코드로 읽는다."""
    with ZipFile(workbook_path) as archive:
        shared_strings = load_shared_strings(archive)
        sheet_path = resolve_sheet_xml_path(archive, sheet_name)
        sheet_root = ElementTree.fromstring(archive.read(sheet_path))
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    matrix: list[list[str]] = []
    maximum_column_count = 0
    for row in sheet_root.iter(f"{namespace}row"):
        values_by_column: dict[int, str] = {}
        for cell in row.findall(f"{namespace}c"):
            column_index = cell_column_index(str(cell.attrib.get("r", "")))
            values_by_column[column_index] = read_cell_text(
                cell,
                shared_strings,
            )
            maximum_column_count = max(
                maximum_column_count,
                column_index + 1,
            )
        row_values = [
            values_by_column.get(column_index, "")
            for column_index in range(maximum_column_count)
        ]
        matrix.append(row_values)
    if not matrix:
        raise ValueError(f"'{sheet_name}' 시트가 비어 있습니다.")
    headers = [str(value).strip() for value in matrix[0]]
    if not all(headers):
        raise ValueError(f"'{sheet_name}' 시트 header에 빈 값이 있습니다.")
    if len(headers) != len(set(headers)):
        raise ValueError(f"'{sheet_name}' 시트 header가 중복되었습니다.")
    records: list[dict[str, str]] = []
    for row_values in matrix[1:]:
        padded_values = row_values + [""] * (len(headers) - len(row_values))
        record = {
            header: str(padded_values[index]).strip()
            for index, header in enumerate(headers)
        }
        if any(record.values()):
            records.append(record)
    return records


def read_csv_records(
    csv_path: str,
    table_name: str,
) -> list[dict[str, str]]:
    """UTF-8 CSV를 header 기반 검수 레코드로 읽는다."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as csv_file:
        reader = DictReader(csv_file)
        headers = [str(header).strip() for header in reader.fieldnames or []]
        if not headers or not all(headers):
            raise ValueError(f"'{table_name}' CSV header가 비어 있습니다.")
        if len(headers) != len(set(headers)):
            raise ValueError(f"'{table_name}' CSV header가 중복되었습니다.")
        records: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(
                    f"'{table_name}' CSV {row_number}행의 컬럼 수가 맞지 않습니다."
                )
            record = {
                header: str(row.get(header) or "").strip()
                for header in headers
            }
            if any(record.values()):
                records.append(record)
    return records


def load_annotation_records(
    annotation_input: str,
    policy: dict,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """XLSX 파일 또는 두 CSV가 든 폴더에서 검수 레코드를 읽는다."""
    importer_policy = policy["entity_resolution"]["semantic_review"][
        "gold_set"
    ]["importer"]
    input_path = Path(annotation_input)
    if input_path.is_file() and input_path.suffix.lower() == ".xlsx":
        case_rows = read_xlsx_sheet_records(
            str(input_path),
            importer_policy["case_sheet"],
        )
        candidate_rows = read_xlsx_sheet_records(
            str(input_path),
            importer_policy["candidate_sheet"],
        )
        return case_rows, candidate_rows
    elif input_path.is_dir():
        csv_files = importer_policy["csv_input_files"]
        case_path = input_path / csv_files["case_annotations"]
        candidate_path = input_path / csv_files["candidate_annotations"]
        if not case_path.is_file():
            raise FileNotFoundError(f"case 검수 CSV를 찾을 수 없습니다: {case_path}")
        if not candidate_path.is_file():
            raise FileNotFoundError(
                f"candidate 검수 CSV를 찾을 수 없습니다: {candidate_path}"
            )
        case_rows = read_csv_records(
            str(case_path),
            importer_policy["case_sheet"],
        )
        candidate_rows = read_csv_records(
            str(candidate_path),
            importer_policy["candidate_sheet"],
        )
        return case_rows, candidate_rows
    raise ValueError(
        "검수 입력은 XLSX 파일 또는 설정된 두 CSV가 있는 폴더여야 합니다: "
        f"{input_path}"
    )


def describe_annotation_input(
    annotation_input: str,
    policy: dict,
) -> dict[str, object]:
    """manifest에 기록할 검수 입력 파일과 해시를 만든다."""
    importer_policy = policy["entity_resolution"]["semantic_review"][
        "gold_set"
    ]["importer"]
    input_path = Path(annotation_input)
    if input_path.is_file() and input_path.suffix.lower() == ".xlsx":
        return {
            "annotation_input_type": "XLSX",
            "annotation_input_path": str(input_path.resolve()),
            "annotation_files": {
                input_path.name: calculate_file_sha256(str(input_path))
            },
        }
    elif input_path.is_dir():
        csv_files = importer_policy["csv_input_files"]
        input_files = {
            input_name: input_path / filename
            for input_name, filename in csv_files.items()
        }
        return {
            "annotation_input_type": "CSV_DIRECTORY",
            "annotation_input_path": str(input_path.resolve()),
            "annotation_files": {
                input_name: {
                    "path": str(file_path.resolve()),
                    "sha256": calculate_file_sha256(str(file_path)),
                }
                for input_name, file_path in input_files.items()
            },
        }
    raise ValueError(f"검수 입력 경로를 읽을 수 없습니다: {input_path}")


def require_columns(
    records: list[dict[str, str]],
    required_columns: list[str],
    sheet_name: str,
) -> None:
    """검수 계약에 필요한 시트 컬럼이 모두 있는지 확인한다."""
    if not records:
        raise ValueError(f"'{sheet_name}' 시트에 데이터 행이 없습니다.")
    missing_columns = set(required_columns).difference(records[0])
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"'{sheet_name}' 필수 컬럼이 없습니다: {missing_text}")


def add_import_error(
    errors: list[dict],
    gold_case_id: str,
    row_scope: str,
    row_key: str,
    severity: str,
    error_code: str,
    message: str,
) -> None:
    """골든셋 검증 오류를 감사 가능한 표준 행으로 추가한다."""
    errors.append(
        {
            "gold_case_id": gold_case_id,
            "row_scope": row_scope,
            "row_key": row_key,
            "severity": severity,
            "error_code": error_code,
            "message": message,
        }
    )


def validate_immutable_fields(
    row: dict[str, str],
    expected: dict[str, str],
    fields: list[str],
    errors: list[dict],
    gold_case_id: str,
    row_scope: str,
    row_key: str,
) -> None:
    """검수 입력의 수정 금지 식별·문맥 필드가 task와 같은지 검사한다."""
    for field_name in fields:
        observed_value = str(row.get(field_name, ""))
        expected_value = str(expected.get(field_name, ""))
        if observed_value != expected_value:
            add_import_error(
                errors,
                gold_case_id,
                row_scope,
                row_key,
                "ERROR",
                "IMMUTABLE_FIELD_MISMATCH",
                f"{field_name}: expected={expected_value}, observed={observed_value}",
            )


def validate_candidate_annotation(
    row: dict[str, str],
    gold_policy: dict,
    errors: list[dict],
) -> None:
    """후보 역할과 대안 입력값의 완결성·일관성을 검사한다."""
    gold_case_id = row["gold_case_id"]
    candidate_id = row["source_candidate_id"]
    annotation_vocabulary = gold_policy["annotation_vocabulary"]
    importer_policy = gold_policy["importer"]
    required_status = str(importer_policy["required_completion_status"])
    review_status = row["candidate_review_status"]
    if review_status != required_status:
        add_import_error(
            errors,
            gold_case_id,
            "CANDIDATE",
            candidate_id,
            "INCOMPLETE",
            "CANDIDATE_REVIEW_NOT_COMPLETE",
            review_status or "EMPTY",
        )
        return
    role = row["gold_candidate_role"]
    allowed_roles = set(annotation_vocabulary["candidate_roles"])
    if role not in allowed_roles:
        add_import_error(
            errors,
            gold_case_id,
            "CANDIDATE",
            candidate_id,
            "ERROR",
            "INVALID_GOLD_CANDIDATE_ROLE",
            role or "EMPTY",
        )
    if not row["gold_reason"]:
        add_import_error(
            errors,
            gold_case_id,
            "CANDIDATE",
            candidate_id,
            "ERROR",
            "MISSING_GOLD_REASON",
            "후보 판정 근거가 필요합니다.",
        )
    if not row["reviewer"]:
        add_import_error(
            errors,
            gold_case_id,
            "CANDIDATE",
            candidate_id,
            "ERROR",
            "MISSING_CANDIDATE_REVIEWER",
            "후보 검수자 식별자가 필요합니다.",
        )
    alternative_fields = [
        "gold_alternative_key",
        "gold_display_name",
        "gold_entity_type",
    ]
    if role == "IDENTITY_MEMBER":
        for field_name in alternative_fields:
            if not row[field_name]:
                add_import_error(
                    errors,
                    gold_case_id,
                    "CANDIDATE",
                    candidate_id,
                    "ERROR",
                    "MISSING_IDENTITY_ALTERNATIVE_FIELD",
                    field_name,
                )
        if row["gold_entity_type"] not in set(
            annotation_vocabulary["entity_types"]
        ):
            add_import_error(
                errors,
                gold_case_id,
                "CANDIDATE",
                candidate_id,
                "ERROR",
                "INVALID_GOLD_ENTITY_TYPE",
                row["gold_entity_type"] or "EMPTY",
            )
    elif role in allowed_roles:
        populated_fields = [
            field_name
            for field_name in alternative_fields
            if row[field_name]
        ]
        if populated_fields:
            add_import_error(
                errors,
                gold_case_id,
                "CANDIDATE",
                candidate_id,
                "ERROR",
                "NON_IDENTITY_ALTERNATIVE_FIELDS_PRESENT",
                ",".join(populated_fields),
            )


def build_case_decision(
    case_row: dict[str, str],
    candidate_rows: list[dict[str, str]],
    gold_policy: dict,
    errors: list[dict],
) -> tuple[dict, dict]:
    """검증 완료된 후보 행을 term decision과 case outcome으로 변환한다."""
    importer_policy = gold_policy["importer"]
    gold_case_id = case_row["gold_case_id"]
    grouped_identity_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    role_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidate_rows:
        role = row["gold_candidate_role"]
        role_rows[role].append(row)
        if role == "IDENTITY_MEMBER":
            grouped_identity_rows[row["gold_alternative_key"]].append(row)

    proposed_alternatives: list[dict] = []
    for alternative_key in sorted(grouped_identity_rows):
        members = sorted(
            grouped_identity_rows[alternative_key],
            key=lambda row: row["source_candidate_id"],
        )
        display_names = {row["gold_display_name"] for row in members}
        entity_types = {row["gold_entity_type"] for row in members}
        if len(display_names) != 1:
            add_import_error(
                errors,
                gold_case_id,
                "ALTERNATIVE",
                alternative_key,
                "ERROR",
                "INCONSISTENT_ALTERNATIVE_DISPLAY_NAME",
                dumps(sorted(display_names), ensure_ascii=False),
            )
        if len(entity_types) != 1:
            add_import_error(
                errors,
                gold_case_id,
                "ALTERNATIVE",
                alternative_key,
                "ERROR",
                "INCONSISTENT_ALTERNATIVE_ENTITY_TYPE",
                dumps(sorted(entity_types), ensure_ascii=False),
            )
        reasons = sorted({row["gold_reason"] for row in members})
        display_name = ""
        entity_type = ""
        if display_names:
            display_name = sorted(display_names)[0]
        if entity_types:
            entity_type = sorted(entity_types)[0]
        proposed_alternatives.append(
            {
                "display_name": display_name,
                "entity_type": entity_type,
                "identity_member_source_candidate_ids": [
                    row["source_candidate_id"] for row in members
                ],
                "reason": str(
                    importer_policy["alternative_reason_separator"]
                ).join(reasons),
            }
        )

    link_status = case_row["gold_link_status"]
    ambiguous_count = len(role_rows["AMBIGUOUS"])
    if link_status == "ACCEPTED" and not proposed_alternatives:
        add_import_error(
            errors,
            gold_case_id,
            "CASE",
            gold_case_id,
            "ERROR",
            "ACCEPTED_WITHOUT_IDENTITY_ALTERNATIVE",
            "ACCEPTED case에는 identity 대안이 하나 이상 필요합니다.",
        )
    if link_status in {"UNRESOLVED", "REJECTED"} and proposed_alternatives:
        add_import_error(
            errors,
            gold_case_id,
            "CASE",
            gold_case_id,
            "ERROR",
            "NON_ACCEPTED_CASE_HAS_IDENTITY_ALTERNATIVE",
            link_status,
        )
    if link_status == "AMBIGUOUS" and ambiguous_count == 0:
        add_import_error(
            errors,
            gold_case_id,
            "CASE",
            gold_case_id,
            "ERROR",
            "AMBIGUOUS_CASE_WITHOUT_AMBIGUOUS_CANDIDATE",
            "AMBIGUOUS 역할 후보가 필요합니다.",
        )
    if link_status != "AMBIGUOUS" and ambiguous_count > 0:
        add_import_error(
            errors,
            gold_case_id,
            "CASE",
            gold_case_id,
            "ERROR",
            "AMBIGUOUS_CANDIDATE_STATUS_MISMATCH",
            link_status,
        )
    if (
        len(proposed_alternatives) > 1
        and case_row["requires_problem_review"] != "YES"
    ):
        add_import_error(
            errors,
            gold_case_id,
            "CASE",
            gold_case_id,
            "ERROR",
            "MULTIPLE_ALTERNATIVES_REQUIRE_PROBLEM_REVIEW",
            case_row["requires_problem_review"],
        )

    classified_fields = {
        "EVIDENCE_ONLY": "evidence_only_sources",
        "REJECTED": "rejected_sources",
        "AMBIGUOUS": "ambiguous_sources",
    }
    classified_sources: dict[str, list[dict]] = {
        field_name: [] for field_name in classified_fields.values()
    }
    for role, field_name in classified_fields.items():
        classified_sources[field_name] = [
            {
                "source_candidate_id": row["source_candidate_id"],
                "reason": row["gold_reason"],
            }
            for row in sorted(
                role_rows[role],
                key=lambda item: item["source_candidate_id"],
            )
        ]
    decision = {
        "term_review_task_id": case_row["term_review_task_id"],
        "resolution_case_id": case_row["resolution_case_id"],
        "decision_status": importer_policy["decision_status"],
        "review_model": importer_policy["review_model"],
        "prompt_version": importer_policy["prompt_version"],
        "proposed_alternatives": proposed_alternatives,
        **classified_sources,
        "decision_reason": case_row["gold_decision_reason"],
    }
    outcome = {
        "gold_case_order": case_row["gold_case_order"],
        "gold_case_id": gold_case_id,
        "term_review_task_id": case_row["term_review_task_id"],
        "resolution_case_id": case_row["resolution_case_id"],
        "gold_link_status": link_status,
        "requires_problem_review": case_row["requires_problem_review"],
        "alternative_count": len(proposed_alternatives),
        "reviewer": case_row["reviewer"],
        "case_review_status": case_row["case_review_status"],
        "gold_decision_reason": case_row["gold_decision_reason"],
    }
    return decision, outcome


def import_gold_annotations(
    case_rows: list[dict[str, str]],
    candidate_rows: list[dict[str, str]],
    gold_tasks: list[dict],
    policy: dict,
) -> dict[str, object]:
    """검수 입력 전체를 task snapshot과 대조하고 완결된 gold decision만 만든다."""
    gold_policy = policy["entity_resolution"]["semantic_review"]["gold_set"]
    importer_policy = gold_policy["importer"]
    required_case_columns = [
        "gold_case_order",
        "gold_case_id",
        "term_review_task_id",
        "resolution_case_id",
        "canonical_term",
        "category",
        "candidate_count",
        "gold_link_status",
        "requires_problem_review",
        "gold_decision_reason",
        "reviewer",
        "case_review_status",
    ]
    required_candidate_columns = [
        "gold_case_order",
        "gold_case_id",
        "term_review_task_id",
        "resolution_case_id",
        "canonical_term",
        "category",
        "source_candidate_id",
        "source_record_id",
        "source",
        "gold_candidate_role",
        "gold_alternative_key",
        "gold_display_name",
        "gold_entity_type",
        "gold_reason",
        "reviewer",
        "candidate_review_status",
    ]
    require_columns(case_rows, required_case_columns, importer_policy["case_sheet"])
    require_columns(
        candidate_rows,
        required_candidate_columns,
        importer_policy["candidate_sheet"],
    )

    task_by_gold_case_id = {
        task["gold_set_metadata"]["gold_case_id"]: task for task in gold_tasks
    }
    case_by_id: dict[str, dict[str, str]] = {}
    candidate_rows_by_case: dict[str, list[dict[str, str]]] = defaultdict(list)
    errors: list[dict] = []
    for row in case_rows:
        gold_case_id = row["gold_case_id"]
        if gold_case_id in case_by_id:
            add_import_error(
                errors,
                gold_case_id,
                "CASE",
                gold_case_id,
                "ERROR",
                "DUPLICATE_GOLD_CASE_ROW",
                "Case Review에 같은 gold_case_id가 중복되었습니다.",
            )
            continue
        case_by_id[gold_case_id] = row
        if gold_case_id not in task_by_gold_case_id:
            add_import_error(
                errors,
                gold_case_id,
                "CASE",
                gold_case_id,
                "ERROR",
                "UNKNOWN_GOLD_CASE",
                "gold task snapshot에 없는 case입니다.",
            )
    observed_candidate_ids: set[str] = set()
    for row in candidate_rows:
        gold_case_id = row["gold_case_id"]
        candidate_id = row["source_candidate_id"]
        candidate_rows_by_case[gold_case_id].append(row)
        if candidate_id in observed_candidate_ids:
            add_import_error(
                errors,
                gold_case_id,
                "CANDIDATE",
                candidate_id,
                "ERROR",
                "DUPLICATE_SOURCE_CANDIDATE_ROW",
                "Candidate Labels에 같은 candidate ID가 중복되었습니다.",
            )
        elif candidate_id not in observed_candidate_ids:
            observed_candidate_ids.add(candidate_id)
        if gold_case_id not in task_by_gold_case_id:
            add_import_error(
                errors,
                gold_case_id,
                "CANDIDATE",
                candidate_id,
                "ERROR",
                "UNKNOWN_CANDIDATE_GOLD_CASE",
                "gold task snapshot에 없는 case입니다.",
            )

    candidate_case_fields = [
        "term_review_task_id",
        "resolution_case_id",
        "canonical_term",
        "category",
    ]
    decisions: list[dict] = []
    outcomes: list[dict] = []
    for gold_case_id, task in task_by_gold_case_id.items():
        case_row = case_by_id.get(gold_case_id)
        if case_row is None:
            add_import_error(
                errors,
                gold_case_id,
                "CASE",
                gold_case_id,
                "INCOMPLETE",
                "MISSING_GOLD_CASE_ROW",
                "Case Review 행이 없습니다.",
            )
            continue
        task_expected = {
            "term_review_task_id": task["term_review_task_id"],
            "resolution_case_id": task["resolution_case_id"],
            "canonical_term": task["canonical_term"],
            "category": task["category"],
        }
        validate_immutable_fields(
            case_row,
            task_expected,
            candidate_case_fields,
            errors,
            gold_case_id,
            "CASE",
            gold_case_id,
        )
        required_status = str(importer_policy["required_completion_status"])
        if case_row["case_review_status"] != required_status:
            add_import_error(
                errors,
                gold_case_id,
                "CASE",
                gold_case_id,
                "INCOMPLETE",
                "CASE_REVIEW_NOT_COMPLETE",
                case_row["case_review_status"] or "EMPTY",
            )
        if case_row["case_review_status"] == required_status:
            if case_row["gold_link_status"] not in set(
                gold_policy["annotation_vocabulary"]["link_statuses"]
            ):
                add_import_error(
                    errors,
                    gold_case_id,
                    "CASE",
                    gold_case_id,
                    "ERROR",
                    "INVALID_GOLD_LINK_STATUS",
                    case_row["gold_link_status"] or "EMPTY",
                )
            if case_row["requires_problem_review"] not in set(
                importer_policy["requires_problem_review_values"]
            ):
                add_import_error(
                    errors,
                    gold_case_id,
                    "CASE",
                    gold_case_id,
                    "ERROR",
                    "INVALID_PROBLEM_REVIEW_VALUE",
                    case_row["requires_problem_review"] or "EMPTY",
                )
            if not case_row["gold_decision_reason"]:
                add_import_error(
                    errors,
                    gold_case_id,
                    "CASE",
                    gold_case_id,
                    "ERROR",
                    "MISSING_GOLD_DECISION_REASON",
                    "case 판정 근거가 필요합니다.",
                )
            if not case_row["reviewer"]:
                add_import_error(
                    errors,
                    gold_case_id,
                    "CASE",
                    gold_case_id,
                    "ERROR",
                    "MISSING_CASE_REVIEWER",
                    "case 검수자 식별자가 필요합니다.",
                )

        expected_candidates = {
            candidate["source_candidate_id"]: candidate
            for candidate in task["source_candidates"]
        }
        observed_rows = candidate_rows_by_case.get(gold_case_id, [])
        observed_ids = {row["source_candidate_id"] for row in observed_rows}
        missing_candidate_ids = set(expected_candidates).difference(observed_ids)
        unknown_candidate_ids = observed_ids.difference(expected_candidates)
        for candidate_id in sorted(missing_candidate_ids):
            add_import_error(
                errors,
                gold_case_id,
                "CANDIDATE",
                candidate_id,
                "INCOMPLETE",
                "MISSING_SOURCE_CANDIDATE_ROW",
                "Candidate Labels 행이 없습니다.",
            )
        for candidate_id in sorted(unknown_candidate_ids):
            add_import_error(
                errors,
                gold_case_id,
                "CANDIDATE",
                candidate_id,
                "ERROR",
                "UNKNOWN_SOURCE_CANDIDATE",
                "해당 gold task에 없는 후보입니다.",
            )
        expected_candidate_count = len(expected_candidates)
        if str(case_row["candidate_count"]) != str(expected_candidate_count):
            add_import_error(
                errors,
                gold_case_id,
                "CASE",
                gold_case_id,
                "ERROR",
                "CANDIDATE_COUNT_MISMATCH",
                f"expected={expected_candidate_count}, observed={case_row['candidate_count']}",
            )
        for candidate_row in observed_rows:
            candidate_id = candidate_row["source_candidate_id"]
            expected_candidate = expected_candidates.get(candidate_id)
            if expected_candidate is None:
                continue
            expected_row = {
                **task_expected,
                "source_record_id": expected_candidate["source_record_id"],
                "source": expected_candidate["source"],
            }
            validate_immutable_fields(
                candidate_row,
                expected_row,
                [*candidate_case_fields, "source_record_id", "source"],
                errors,
                gold_case_id,
                "CANDIDATE",
                candidate_id,
            )
            validate_candidate_annotation(
                candidate_row,
                gold_policy,
                errors,
            )

        case_error_count_before_decision = sum(
            1 for error in errors if error["gold_case_id"] == gold_case_id
        )
        if case_error_count_before_decision == 0:
            decision, outcome = build_case_decision(
                case_row,
                observed_rows,
                gold_policy,
                errors,
            )
            case_error_count_after_decision = sum(
                1 for error in errors if error["gold_case_id"] == gold_case_id
            )
            if case_error_count_after_decision == 0:
                decisions.append(decision)
                outcomes.append(outcome)

    error_columns = [
        "gold_case_id",
        "row_scope",
        "row_key",
        "severity",
        "error_code",
        "message",
    ]
    outcome_columns = [
        "gold_case_order",
        "gold_case_id",
        "term_review_task_id",
        "resolution_case_id",
        "gold_link_status",
        "requires_problem_review",
        "alternative_count",
        "reviewer",
        "case_review_status",
        "gold_decision_reason",
    ]
    errors_table = pd.DataFrame(errors, columns=error_columns)
    outcomes_table = pd.DataFrame(outcomes, columns=outcome_columns)
    return {
        "gold_decisions": decisions,
        "gold_case_outcomes": outcomes_table,
        "validation_errors": errors_table,
    }


def write_gold_import_outputs(
    outputs: dict[str, object],
    annotation_input: str,
    task_path: str,
    output_dir: str,
    policy: dict,
    generated_at: str = "",
) -> dict[str, str]:
    """gold decision·case outcome·오류·감사 manifest를 저장한다."""
    gold_policy = policy["entity_resolution"]["semantic_review"]["gold_set"]
    importer_policy = gold_policy["importer"]
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_paths = {
        name: output_directory / filename
        for name, filename in importer_policy["output_files"].items()
    }
    decisions = outputs["gold_decisions"]
    outcomes = outputs["gold_case_outcomes"]
    validation_errors = outputs["validation_errors"]
    write_jsonl(decisions, str(output_paths["gold_decisions"]))
    outcomes.to_csv(
        output_paths["gold_case_outcomes"],
        index=False,
        encoding="utf-8-sig",
    )
    validation_errors.to_csv(
        output_paths["validation_errors"],
        index=False,
        encoding="utf-8-sig",
    )
    creation_time = generated_at
    if not creation_time:
        creation_time = datetime.now(timezone.utc).isoformat()
    severity_counts: dict[str, int] = {}
    if not validation_errors.empty:
        severity_counts = {
            str(severity): int(count)
            for severity, count in validation_errors["severity"]
            .value_counts()
            .items()
        }
    annotation_descriptor = describe_annotation_input(
        annotation_input,
        policy,
    )
    manifest = {
        "selection_policy_version": gold_policy["selection_policy_version"],
        "annotation_prompt_version": importer_policy["prompt_version"],
        "resolution_policy_version": policy["policy_version"],
        **annotation_descriptor,
        "task_path": str(Path(task_path).resolve()),
        "task_sha256": calculate_file_sha256(task_path),
        "valid_decision_count": len(decisions),
        "validation_error_count": len(validation_errors),
        "validation_severity_counts": severity_counts,
        "ready_for_evaluation": validation_errors.empty,
        "generated_at": creation_time,
        "output_files": {
            name: str(path.resolve()) for name, path in output_paths.items()
        },
    }
    with output_paths["import_manifest"].open(
        "w",
        encoding="utf-8",
    ) as output_file:
        output_file.write(dumps(manifest, ensure_ascii=False, indent=2))
    return {name: str(path) for name, path in output_paths.items()}


if __name__ == "__main__":
    parser = ArgumentParser(
        description="검수된 Entity Resolution 골든셋 XLSX·CSV를 decision JSONL로 변환"
    )
    parser.add_argument(
        "annotations",
        help="검수가 끝난 XLSX 또는 case·candidate CSV가 든 폴더",
    )
    parser.add_argument("gold_tasks", help="골든셋 task JSONL 경로")
    parser.add_argument("output_dir", help="검증 결과와 gold decision 출력 폴더")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="미완료·오류 case가 있어도 검증된 일부 decision 파일은 유지",
    )
    parser.add_argument(
        "--policy",
        default=str(
            Path(__file__).resolve().parent.parent
            / "config"
            / "resolution_policy.json"
        ),
        help="Entity Resolution 정책 JSON 경로",
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    case_annotations, candidate_annotations = load_annotation_records(
        cli_args.annotations,
        pipeline_policy,
    )
    task_records = load_jsonl(cli_args.gold_tasks)
    import_outputs = import_gold_annotations(
        case_annotations,
        candidate_annotations,
        task_records,
        pipeline_policy,
    )
    written_paths = write_gold_import_outputs(
        import_outputs,
        cli_args.annotations,
        cli_args.gold_tasks,
        cli_args.output_dir,
        pipeline_policy,
    )
    validation_errors = import_outputs["validation_errors"]
    print(dumps(written_paths, ensure_ascii=False, indent=2))
    if not validation_errors.empty and not cli_args.allow_partial:
        print(
            "골든셋 검증이 완료되지 않았습니다: "
            f"{len(validation_errors)}건의 오류·미완료 항목"
        )
        raise SystemExit(1)

from csv import DictWriter
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


class GoldSetImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))

        from common import load_pipeline_policy
        from entity_resolution.import_gold_set import (
            describe_annotation_input,
            import_gold_annotations,
            load_annotation_records,
            read_xlsx_sheet_records,
        )

        cls.describe_annotation_input = staticmethod(
            describe_annotation_input
        )
        cls.import_gold_annotations = staticmethod(import_gold_annotations)
        cls.load_annotation_records = staticmethod(load_annotation_records)
        cls.read_xlsx_sheet_records = staticmethod(read_xlsx_sheet_records)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def make_task(self) -> dict:
        candidates = []
        for candidate_number in range(1, 5):
            candidates.append(
                {
                    "source_candidate_id": f"candidate-{candidate_number}",
                    "source_record_id": f"AKS:ARTICLE:E{candidate_number}:r1",
                    "source": "AKS",
                }
            )
        return {
            "term_review_task_id": "task-1",
            "resolution_case_id": "case-1",
            "canonical_term": "검수용어",
            "category": "인물",
            "source_candidates": candidates,
            "gold_set_metadata": {
                "gold_case_order": 1,
                "gold_case_id": "gold-case-1",
            },
        }

    def make_case_row(self) -> dict[str, str]:
        return {
            "gold_case_order": "1",
            "gold_case_id": "gold-case-1",
            "term_review_task_id": "task-1",
            "resolution_case_id": "case-1",
            "canonical_term": "검수용어",
            "category": "인물",
            "candidate_count": "4",
            "gold_link_status": "ACCEPTED",
            "requires_problem_review": "YES",
            "gold_decision_reason": "두 인물을 구분했다.",
            "reviewer": "reviewer-1",
            "case_review_status": "COMPLETE",
        }

    def make_candidate_row(
        self,
        candidate_number: int,
        role: str,
        alternative_key: str = "",
        display_name: str = "",
        entity_type: str = "",
        related_entity_key: str = "",
        related_display_name: str = "",
        related_entity_type: str = "",
    ) -> dict[str, str]:
        return {
            "gold_case_order": "1",
            "gold_case_id": "gold-case-1",
            "term_review_task_id": "task-1",
            "resolution_case_id": "case-1",
            "canonical_term": "검수용어",
            "category": "인물",
            "source_candidate_id": f"candidate-{candidate_number}",
            "source_record_id": f"AKS:ARTICLE:E{candidate_number}:r1",
            "source": "AKS",
            "gold_candidate_role": role,
            "gold_alternative_key": alternative_key,
            "gold_display_name": display_name,
            "gold_entity_type": entity_type,
            "gold_related_entity_key": related_entity_key,
            "gold_related_display_name": related_display_name,
            "gold_related_entity_type": related_entity_type,
            "gold_reason": f"후보 {candidate_number} 판정 근거",
        }

    def make_valid_candidate_rows(self) -> list[dict[str, str]]:
        return [
            self.make_candidate_row(
                1,
                "IDENTITY_MEMBER",
                "ALT_001",
                "검수용어(첫 번째)",
                "Person",
            ),
            self.make_candidate_row(
                2,
                "IDENTITY_MEMBER",
                "ALT_001",
            ),
            self.make_candidate_row(
                3,
                "IDENTITY_MEMBER",
                "ALT_002",
                "검수용어(두 번째)",
                "Person",
            ),
            self.make_candidate_row(
                4,
                "EVIDENCE_ONLY",
                related_entity_key="REL_001",
                related_display_name="관련 인물",
                related_entity_type="Person",
            ),
        ]

    def test_valid_multiple_alternatives_are_preserved(self):
        outputs = self.import_gold_annotations(
            [self.make_case_row()],
            self.make_valid_candidate_rows(),
            [self.make_task()],
            self.policy,
        )

        self.assertTrue(outputs["validation_errors"].empty)
        self.assertEqual(len(outputs["gold_decisions"]), 1)
        decision = outputs["gold_decisions"][0]
        self.assertEqual(len(decision["proposed_alternatives"]), 2)
        self.assertEqual(
            decision["proposed_alternatives"][0][
                "identity_member_source_candidate_ids"
            ],
            ["candidate-1", "candidate-2"],
        )
        self.assertEqual(
            decision["evidence_only_sources"][0]["source_candidate_id"],
            "candidate-4",
        )
        self.assertEqual(
            decision["proposed_related_entities"],
            [
                {
                    "related_entity_key": "REL_001",
                    "display_name": "관련 인물",
                    "entity_type": "Person",
                    "evidence_source_candidate_ids": ["candidate-4"],
                    "reason": "후보 4 판정 근거",
                }
            ],
        )
        self.assertEqual(
            outputs["gold_case_outcomes"].iloc[0]["related_entity_count"],
            1,
        )
        related_tasks = outputs["related_entity_tasks"]
        self.assertEqual(len(related_tasks), 1)
        self.assertEqual(
            related_tasks[0]["canonical_term"],
            "관련 인물",
        )
        self.assertEqual(
            related_tasks[0]["seed_source_candidate_ids"],
            ["candidate-4"],
        )
        self.assertEqual(decision["review_model"], "human_gold_adjudication")

    def test_blank_candidate_role_is_implicitly_rejected(self):
        candidate_rows = self.make_valid_candidate_rows()
        candidate_rows[3]["gold_candidate_role"] = ""
        candidate_rows[3]["gold_reason"] = ""
        candidate_rows[3]["gold_related_entity_key"] = ""
        candidate_rows[3]["gold_related_display_name"] = ""
        candidate_rows[3]["gold_related_entity_type"] = ""

        outputs = self.import_gold_annotations(
            [self.make_case_row()],
            candidate_rows,
            [self.make_task()],
            self.policy,
        )

        self.assertTrue(outputs["validation_errors"].empty)
        self.assertEqual(
            outputs["gold_decisions"][0]["rejected_sources"][0][
                "source_candidate_id"
            ],
            "candidate-4",
        )

    def test_incomplete_case_does_not_require_candidate_annotations(self):
        case_row = self.make_case_row()
        case_row["case_review_status"] = "IN_PROGRESS"
        candidate_rows = self.make_valid_candidate_rows()
        for candidate_row in candidate_rows:
            candidate_row["gold_candidate_role"] = ""
            candidate_row["gold_alternative_key"] = ""
            candidate_row["gold_display_name"] = ""
            candidate_row["gold_entity_type"] = ""
            candidate_row["gold_related_entity_key"] = ""
            candidate_row["gold_related_display_name"] = ""
            candidate_row["gold_related_entity_type"] = ""
            candidate_row["gold_reason"] = ""

        outputs = self.import_gold_annotations(
            [case_row],
            candidate_rows,
            [self.make_task()],
            self.policy,
        )

        self.assertEqual(outputs["gold_decisions"], [])
        self.assertEqual(
            set(outputs["validation_errors"]["error_code"]),
            {"CASE_REVIEW_NOT_COMPLETE"},
        )

    def test_inconsistent_alternative_metadata_blocks_case_export(self):
        candidate_rows = self.make_valid_candidate_rows()
        candidate_rows[1]["gold_display_name"] = "다른 표시명"

        outputs = self.import_gold_annotations(
            [self.make_case_row()],
            candidate_rows,
            [self.make_task()],
            self.policy,
        )

        self.assertEqual(outputs["gold_decisions"], [])
        self.assertIn(
            "INCONSISTENT_ALTERNATIVE_DISPLAY_NAME",
            set(outputs["validation_errors"]["error_code"]),
        )

    def test_evidence_only_rejects_identity_alternative_fields(self):
        candidate_rows = self.make_valid_candidate_rows()
        candidate_rows[3]["gold_alternative_key"] = "ALT_003"

        outputs = self.import_gold_annotations(
            [self.make_case_row()],
            candidate_rows,
            [self.make_task()],
            self.policy,
        )

        self.assertEqual(outputs["gold_decisions"], [])
        self.assertIn(
            "NON_IDENTITY_ALTERNATIVE_FIELDS_PRESENT",
            set(outputs["validation_errors"]["error_code"]),
        )

    def test_immutable_source_record_change_is_rejected(self):
        candidate_rows = self.make_valid_candidate_rows()
        candidate_rows[0]["source_record_id"] = "CHANGED"

        outputs = self.import_gold_annotations(
            [self.make_case_row()],
            candidate_rows,
            [self.make_task()],
            self.policy,
        )

        self.assertEqual(outputs["gold_decisions"], [])
        self.assertIn(
            "IMMUTABLE_FIELD_MISMATCH",
            set(outputs["validation_errors"]["error_code"]),
        )

    def test_standard_library_xlsx_reader_preserves_blank_cells(self):
        content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
        root_relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
        workbook = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Case Review" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
        workbook_relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
        sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>id</t></is></c><c r="B1" t="inlineStr"><is><t>label</t></is></c><c r="C1" t="inlineStr"><is><t>note</t></is></c></row>
    <row r="2"><c r="A2" t="inlineStr"><is><t>case-1</t></is></c><c r="C2" t="inlineStr"><is><t>검수</t></is></c></row>
  </sheetData>
</worksheet>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / "fixture.xlsx"
            with ZipFile(workbook_path, "w") as archive:
                archive.writestr("[Content_Types].xml", content_types)
                archive.writestr("_rels/.rels", root_relationships)
                archive.writestr("xl/workbook.xml", workbook)
                archive.writestr(
                    "xl/_rels/workbook.xml.rels",
                    workbook_relationships,
                )
                archive.writestr("xl/worksheets/sheet1.xml", sheet)

            records = self.read_xlsx_sheet_records(
                str(workbook_path),
                "Case Review",
            )

        self.assertEqual(
            records,
            [{"id": "case-1", "label": "", "note": "검수"}],
        )

    def test_csv_directory_preserves_ids_blanks_and_multiline_context(self):
        case_row = self.make_case_row()
        candidate_row = self.make_valid_candidate_rows()[0]
        candidate_row["source_context_json"] = "첫 줄, 설명\n둘째 줄"
        importer_policy = self.policy["entity_resolution"][
            "semantic_review"
        ]["gold_set"]["importer"]

        with tempfile.TemporaryDirectory() as temp_dir:
            input_directory = Path(temp_dir)
            csv_files = importer_policy["csv_input_files"]
            case_path = input_directory / csv_files["case_annotations"]
            candidate_path = input_directory / csv_files[
                "candidate_annotations"
            ]
            with case_path.open(
                "w",
                encoding="utf-8-sig",
                newline="",
            ) as case_file:
                writer = DictWriter(case_file, fieldnames=list(case_row))
                writer.writeheader()
                writer.writerow(case_row)
            with candidate_path.open(
                "w",
                encoding="utf-8-sig",
                newline="",
            ) as candidate_file:
                writer = DictWriter(
                    candidate_file,
                    fieldnames=list(candidate_row),
                )
                writer.writeheader()
                writer.writerow(candidate_row)

            case_records, candidate_records = self.load_annotation_records(
                str(input_directory),
                self.policy,
            )
            descriptor = self.describe_annotation_input(
                str(input_directory),
                self.policy,
            )

        self.assertEqual(case_records, [case_row])
        self.assertEqual(candidate_records, [candidate_row])
        self.assertEqual(
            descriptor["annotation_input_type"],
            "CSV_DIRECTORY",
        )
        self.assertEqual(len(descriptor["annotation_files"]), 2)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai.pack_generation.batch_constraints import (
    SpecExhaustedError,
    main,
    merge_pack_banks,
    select_approved_specs,
)


def approved_specs() -> dict:
    path = Path(__file__).with_name("graph_pack_specs_10_20260727.json")
    return json.loads(path.read_text(encoding="utf-8-sig"))


class BatchConstraintsTest(unittest.TestCase):
    def test_two_weekly_runs_consume_each_spec_once(self) -> None:
        source = approved_specs()
        first_selection, first_manifest = select_approved_specs(source, {"packs": []}, 5, 5)
        self.assertEqual(len(first_selection["packs"]), 5)
        self.assertEqual(first_manifest["remaining_unused_spec_count"], 5)

        existing_bank = {
            "packs": [],
            "consumed_spec_ids": first_manifest["selected_spec_ids"],
        }
        second_selection, second_manifest = select_approved_specs(source, existing_bank, 5, 5)
        self.assertEqual(len(second_selection["packs"]), 5)
        self.assertEqual(second_manifest["remaining_unused_spec_count"], 0)
        self.assertTrue(
            set(first_manifest["selected_spec_ids"]).isdisjoint(
                second_manifest["selected_spec_ids"]
            )
        )

        exhausted_bank = {
            "packs": [],
            "consumed_spec_ids": [
                *first_manifest["selected_spec_ids"],
                *second_manifest["selected_spec_ids"],
            ],
        }
        with self.assertRaisesRegex(SpecExhaustedError, "not enough unused approved specs"):
            select_approved_specs(source, exhausted_bank, 5, 5)

    def test_more_than_five_packs_per_run_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 5"):
            select_approved_specs(approved_specs(), {"packs": []}, 6, 5)

    @patch("ai.pack_generation.batch_constraints.read_json")
    @patch("ai.pack_generation.batch_constraints.parse_args")
    def test_exhausted_cli_returns_notification_exit_code(
        self,
        parse_args_mock: MagicMock,
        read_json_mock: MagicMock,
    ) -> None:
        source = approved_specs()
        first_selection, first_manifest = select_approved_specs(source, {"packs": []}, 5, 5)
        _, second_manifest = select_approved_specs(
            source,
            {"packs": [], "consumed_spec_ids": first_manifest["selected_spec_ids"]},
            5,
            5,
        )
        parse_args_mock.return_value = SimpleNamespace(
            command="select",
            approved_specs=Path("approved.json"),
            existing_bank=Path("bank.json"),
            output=Path("selected.json"),
            manifest=Path("manifest.json"),
            packs_per_run=5,
            maximum_packs_per_run=5,
        )
        read_json_mock.side_effect = [
            source,
            {
                "packs": [],
                "consumed_spec_ids": [
                    *first_manifest["selected_spec_ids"],
                    *second_manifest["selected_spec_ids"],
                ],
            },
        ]
        with redirect_stderr(StringIO()):
            self.assertEqual(main(), 42)

    def test_duplicate_approved_spec_is_rejected(self) -> None:
        source = approved_specs()
        source["packs"][1] = dict(source["packs"][0])
        with self.assertRaisesRegex(ValueError, "contains a duplicate"):
            select_approved_specs(source, {"packs": []}, 5, 5)

    @patch("ai.pack_generation.batch_constraints.validate_pack_bank")
    def test_merge_commits_pack_and_spec_ids_together(
        self,
        _validate_mock: MagicMock,
    ) -> None:
        selected, manifest = select_approved_specs(approved_specs(), {"packs": []}, 1, 5)
        spec_id = selected["packs"][0]["spec_id"]
        new_pack = {"family_id": "new", "source_spec_id": spec_id}
        merged = merge_pack_banks(
            {"pack_count": 1, "packs": [{"family_id": "seed"}]},
            {"pack_count": 1, "packs": [new_pack]},
            manifest,
        )
        self.assertEqual(merged["pack_count"], 2)
        self.assertEqual(merged["consumed_spec_ids"], [spec_id])
        self.assertEqual(merged["packs"][-1], new_pack)


if __name__ == "__main__":
    unittest.main()

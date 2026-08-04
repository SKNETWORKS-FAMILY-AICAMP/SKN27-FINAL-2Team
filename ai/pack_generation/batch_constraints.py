"""Validate approved Graph specs and maintain the cumulative pack bank."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ai.pack_generation.builder import validate_pack_bank
from ai.pack_generation.graph_builder import validate_spec


class SpecExhaustedError(ValueError):
    """Raised when a run cannot reserve enough unused approved specs."""


def calculate_spec_id(spec: dict[str, Any]) -> str:
    """Return a stable identifier derived from the reviewed spec content."""
    canonical_spec = dict(spec)
    canonical_spec.pop("spec_id", None)
    encoded = json.dumps(
        canonical_spec,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"graph_spec:{hashlib.sha256(encoded).hexdigest()}"


def consumed_spec_ids(pack_bank: dict[str, Any]) -> list[str]:
    """Collect spec identifiers already committed to the cumulative bank."""
    identifiers = [
        str(identifier)
        for identifier in pack_bank.get("consumed_spec_ids", [])
        if str(identifier)
    ]
    packs = pack_bank.get("packs", pack_bank if isinstance(pack_bank, list) else [])
    if not isinstance(packs, list):
        raise ValueError("pack bank must contain a packs array")
    for pack in packs:
        if not isinstance(pack, dict):
            raise ValueError("pack bank entries must be objects")
        identifier = str(
            pack.get("source_spec_id")
            or (pack.get("graph_source") or {}).get("spec_id")
            or ""
        )
        if identifier:
            identifiers.append(identifier)
    return list(dict.fromkeys(identifiers))


def select_approved_specs(
    approved_specs: dict[str, Any],
    pack_bank: dict[str, Any],
    packs_per_run: int,
    maximum_packs_per_run: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select the next unused reviewed specs without allowing silent reuse."""
    if packs_per_run < 1 or packs_per_run > maximum_packs_per_run:
        raise ValueError(
            f"packs_per_run must be between 1 and {maximum_packs_per_run}"
        )

    specs = approved_specs.get("packs")
    if not isinstance(specs, list) or not specs:
        raise ValueError("approved spec file must contain a non-empty packs array")

    identified_specs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_spec in specs:
        if not isinstance(raw_spec, dict):
            raise ValueError("approved spec entries must be objects")
        validate_spec(raw_spec)
        identifier = calculate_spec_id(raw_spec)
        supplied_identifier = str(raw_spec.get("spec_id") or "")
        if supplied_identifier and supplied_identifier != identifier:
            raise ValueError("approved spec contains a mismatched spec_id")
        if identifier in seen_ids:
            raise ValueError(f"approved spec file contains a duplicate: {identifier}")
        seen_ids.add(identifier)
        identified_specs.append({**raw_spec, "spec_id": identifier})

    already_consumed = set(consumed_spec_ids(pack_bank))
    unused_specs = [
        spec for spec in identified_specs if spec["spec_id"] not in already_consumed
    ]
    if len(unused_specs) < packs_per_run:
        raise SpecExhaustedError(
            "not enough unused approved specs: "
            f"required={packs_per_run}, available={len(unused_specs)}"
        )

    selected_specs = unused_specs[:packs_per_run]
    selected_ids = [str(spec["spec_id"]) for spec in selected_specs]
    selected_payload = {
        **{key: value for key, value in approved_specs.items() if key != "packs"},
        "pack_count": len(selected_specs),
        "packs": selected_specs,
    }
    selection_manifest = {
        "status": "APPROVED_SPECS_SELECTED",
        "approved_spec_count": len(identified_specs),
        "previously_consumed_spec_count": len(already_consumed),
        "selected_spec_count": len(selected_specs),
        "selected_spec_ids": selected_ids,
        "remaining_unused_spec_count": len(unused_specs) - len(selected_specs),
    }
    return selected_payload, selection_manifest


def merge_pack_banks(
    existing_bank: dict[str, Any],
    new_bank: dict[str, Any],
    selection_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Commit generated packs and their consumed spec identifiers together."""
    existing_packs = existing_bank.get("packs")
    new_packs = new_bank.get("packs")
    selected_ids = selection_manifest.get("selected_spec_ids")
    if not isinstance(existing_packs, list) or not isinstance(new_packs, list):
        raise ValueError("both pack banks must contain packs arrays")
    if not isinstance(selected_ids, list) or not selected_ids:
        raise ValueError("selection manifest must contain selected_spec_ids")
    if len(new_packs) != len(selected_ids):
        raise ValueError("generated pack count does not match selected spec count")

    generated_ids = [
        str(pack.get("source_spec_id") or "")
        for pack in new_packs
        if isinstance(pack, dict)
    ]
    if generated_ids != [str(identifier) for identifier in selected_ids]:
        raise ValueError("generated packs do not match the selected approved specs")

    previous_ids = consumed_spec_ids(existing_bank)
    duplicated_ids = set(previous_ids).intersection(generated_ids)
    if duplicated_ids:
        raise ValueError(f"approved specs were already consumed: {sorted(duplicated_ids)}")

    merged_packs = [*existing_packs, *new_packs]
    validate_pack_bank(merged_packs)
    return {
        **{key: value for key, value in existing_bank.items() if key != "packs"},
        "pack_count": len(merged_packs),
        "consumed_spec_ids": [*previous_ids, *generated_ids],
        "packs": merged_packs,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    select_parser = subparsers.add_parser("select", help="select unused approved specs")
    select_parser.add_argument("--approved-specs", type=Path, required=True)
    select_parser.add_argument("--existing-bank", type=Path, required=True)
    select_parser.add_argument("--output", type=Path, required=True)
    select_parser.add_argument("--manifest", type=Path, required=True)
    select_parser.add_argument("--packs-per-run", type=int, default=5)
    select_parser.add_argument("--maximum-packs-per-run", type=int, default=5)

    merge_parser = subparsers.add_parser("merge", help="merge generated packs into the bank")
    merge_parser.add_argument("--existing-bank", type=Path, required=True)
    merge_parser.add_argument("--new-bank", type=Path, required=True)
    merge_parser.add_argument("--selection-manifest", type=Path, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    if args.command == "select":
        try:
            selected, manifest = select_approved_specs(
                read_json(args.approved_specs),
                read_json(args.existing_bank),
                args.packs_per_run,
                args.maximum_packs_per_run,
            )
        except SpecExhaustedError as exc:
            print(f"SPEC_EXHAUSTED: {exc}", file=sys.stderr)
            return 42
        write_json(args.output, selected)
        write_json(args.manifest, manifest)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0
    if args.command == "merge":
        merged = merge_pack_banks(
            read_json(args.existing_bank),
            read_json(args.new_bank),
            read_json(args.selection_manifest),
        )
        write_json(args.output, merged)
        print(json.dumps({"pack_count": merged["pack_count"]}, ensure_ascii=False))
        return 0
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

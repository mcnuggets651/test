#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

PARITY_MODES = ("auto", "required", "diagnostic")
EARLY_SEASON_OVERLAY_LAST_GW = 5


def parity_is_required(payload: dict, mode: str) -> bool:
    if mode not in PARITY_MODES:
        raise ValueError(f"unknown parity mode: {mode}")
    if mode == "required":
        return True
    if mode == "diagnostic":
        return False
    return int(payload.get("gameweek") or 0) > EARLY_SEASON_OVERLAY_LAST_GW


def validate(payload: dict, *, parity_mode: str = "auto") -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != "dastan-smartplay-free-acceptance-v3":
        errors.append(f"unexpected acceptance schema: {payload.get('schema')!r}")
    if not payload.get("fpl_is_next"):
        errors.append("projection was not generated for Official FPL is_next")
    if int(payload.get("fixture_rows") or 0) <= 0:
        errors.append("no fixture-level projection rows")
    if int(payload.get("player_rows") or 0) <= 0:
        errors.append("no player-level projection rows")
    if int(payload.get("current_mapping_count") or 0) <= 0:
        errors.append("no active-season player mappings were resolved")

    outputs = payload.get("outputs") or {}
    if not outputs.get("fixtures") or not outputs.get("solver"):
        errors.append("acceptance record does not name both output files")

    required = parity_is_required(payload, parity_mode)
    reference = payload.get("smartplay_reference") or {}
    gate = payload.get("parity_gate") or {}
    if required:
        if not reference.get("available"):
            errors.append("SmartPlay manual reference comparison is unavailable")
        matched = int(reference.get("matched") or 0)
        total = int(reference.get("total") or 0)
        if total != 5 or matched != total:
            errors.append(f"SmartPlay spot-check coverage is {matched}/{total}, expected 5/5")
        if not gate.get("passed") or gate.get("status") != "PASS":
            errors.append(f"SmartPlay parity gate failed: {json.dumps(gate, sort_keys=True)}")
    return errors


def parity_summary(payload: dict, mode: str) -> dict:
    required = parity_is_required(payload, mode)
    gate = payload.get("parity_gate") or {}
    if required:
        status = "REQUIRED_PASS" if gate.get("passed") else "REQUIRED_FAIL"
    elif int(payload.get("gameweek") or 0) <= EARLY_SEASON_OVERLAY_LAST_GW:
        # SmartPlay publicly documents a separate early-season overlay through GW5.
        # Raw open-Dastan parity is therefore diagnostic, not an OSS correctness gate.
        status = "EARLY_SEASON_DIAGNOSTIC"
    else:
        status = "DIAGNOSTIC"
    return {
        "status": status,
        "required": required,
        "raw_gate": gate.get("status", "NOT_CHECKED"),
        "mae": gate.get("mae"),
        "max_abs_delta": gate.get("max_abs_delta"),
        "rounded_1dp_matches": gate.get("rounded_1dp_matches"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the open Dastan + SmartPlay Solver acceptance evidence")
    parser.add_argument("acceptance_json", type=Path)
    parser.add_argument("--parity-mode", choices=PARITY_MODES, default="auto")
    args = parser.parse_args()

    payload = json.loads(args.acceptance_json.read_text(encoding="utf-8"))
    errors = validate(payload, parity_mode=args.parity_mode)
    summary = parity_summary(payload, args.parity_mode)
    if errors:
        print("OPEN STACK ACCEPTANCE FAILED")
        print(f"HOSTED PARITY: {json.dumps(summary, sort_keys=True)}")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        "OPEN STACK ACCEPTANCE PASSED | "
        f"GW{payload['gameweek']} | players={payload['player_rows']} | "
        f"hosted_parity={summary['status']} | raw_gate={summary['raw_gate']} | "
        f"mae={summary['mae']} | max_abs_delta={summary['max_abs_delta']} | "
        f"rounded_matches={summary['rounded_1dp_matches']}"
    )
    if summary["status"] == "EARLY_SEASON_DIAGNOSTIC":
        print(
            "NOTE: SmartPlay documents a separate team-news / position-price early-season "
            "overlay through GW5. Hosted values are recorded for diagnostics but are not "
            "treated as a correctness test for the open Dastan release until GW6."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(payload: dict, *, require_parity: bool) -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != "dastan-smartplay-free-acceptance-v3":
        errors.append(f"unexpected acceptance schema: {payload.get('schema')!r}")
    if not payload.get("fpl_is_next"):
        errors.append("projection was not generated for Official FPL is_next")
    if int(payload.get("fixture_rows") or 0) <= 0:
        errors.append("no fixture-level projection rows")
    if int(payload.get("player_rows") or 0) <= 0:
        errors.append("no player-level projection rows")

    reference = payload.get("smartplay_reference") or {}
    if require_parity:
        if not reference.get("available"):
            errors.append("SmartPlay manual reference comparison is unavailable")
        matched = int(reference.get("matched") or 0)
        total = int(reference.get("total") or 0)
        if total != 5 or matched != total:
            errors.append(f"SmartPlay spot-check coverage is {matched}/{total}, expected 5/5")
        gate = payload.get("parity_gate") or {}
        if not gate.get("passed") or gate.get("status") != "PASS":
            errors.append(f"SmartPlay parity gate failed: {json.dumps(gate, sort_keys=True)}")

    outputs = payload.get("outputs") or {}
    if not outputs.get("fixtures") or not outputs.get("solver"):
        errors.append("acceptance record does not name both output files")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Dastan/SmartPlay acceptance evidence")
    parser.add_argument("acceptance_json", type=Path)
    parser.add_argument("--require-parity", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.acceptance_json.read_text(encoding="utf-8"))
    errors = validate(payload, require_parity=args.require_parity)
    if errors:
        print("ACCEPTANCE FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    gate = payload.get("parity_gate") or {}
    print(
        "ACCEPTANCE PASSED | "
        f"GW{payload['gameweek']} | players={payload['player_rows']} | "
        f"parity={gate.get('status', 'NOT_CHECKED')} | mae={gate.get('mae')} | "
        f"max_abs_delta={gate.get('max_abs_delta')} | "
        f"rounded_matches={gate.get('rounded_1dp_matches')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

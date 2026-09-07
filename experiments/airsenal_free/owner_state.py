from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "airsenal-chat-owner-state-v1"
EXPECTED_ENTRY_ID = 63984
POSITION_COUNTS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}


class OwnerStateError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise OwnerStateError(f"{label} must be an integer")
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise OwnerStateError(f"{label} must be an integer") from exc
    if minimum is not None and out < minimum:
        raise OwnerStateError(f"{label} must be >= {minimum}")
    return out


def _normalize_position(value: Any) -> str:
    pos = str(value or "").upper()
    if pos == "GKP":
        pos = "GK"
    if pos not in POSITION_COUNTS:
        raise OwnerStateError(f"invalid position {value!r}")
    return pos


def _official_index(bootstrap: dict[str, Any]) -> tuple[int, dict[int, dict[str, Any]]]:
    events = [e for e in bootstrap.get("events", []) if isinstance(e, dict) and e.get("is_next") is True]
    if len(events) != 1:
        raise OwnerStateError(f"Official FPL must expose exactly one is_next event, found {len(events)}")
    next_gw = _int(events[0].get("id"), "official next gameweek", minimum=1)
    players: dict[int, dict[str, Any]] = {}
    type_pos = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    for raw in bootstrap.get("elements", []):
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        pid = _int(raw["id"], "official player id", minimum=1)
        players[pid] = {
            "element_id": pid,
            "web_name": raw.get("web_name"),
            "position": type_pos.get(_int(raw.get("element_type"), f"element_type[{pid}]", minimum=1)),
            "team_id": _int(raw.get("team"), f"team[{pid}]", minimum=1),
            "current_price_tenths": _int(raw.get("now_cost"), f"now_cost[{pid}]", minimum=1),
            "status": raw.get("status"),
        }
    return next_gw, players


def validate_and_whitelist(raw_bytes: bytes, official_bootstrap: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise OwnerStateError("private strategy snapshot is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise OwnerStateError("private strategy snapshot must be a JSON object")

    run = raw.get("run") or {}
    team = raw.get("team_state") or {}
    if not isinstance(run, dict) or not isinstance(team, dict):
        raise OwnerStateError("private snapshot run/team_state must be objects")
    if run.get("attestation_scope") != "PRIVATE_MANAGER":
        raise OwnerStateError("private snapshot is not PRIVATE_MANAGER scope")
    if run.get("immutable") is not True:
        raise OwnerStateError("private snapshot is not immutable")
    if team.get("state_complete_for_transfers") is not True:
        raise OwnerStateError("private snapshot is not complete for transfers")

    official_gw, official_players = _official_index(official_bootstrap)
    target_gw = _int(run.get("target_gameweek"), "run.target_gameweek", minimum=1)
    if target_gw != official_gw:
        raise OwnerStateError(f"private target GW{target_gw} does not match Official FPL is_next GW{official_gw}")
    published_gw = _int(team.get("published_gw"), "team_state.published_gw", minimum=1)
    if published_gw != target_gw:
        raise OwnerStateError(f"private published GW{published_gw} does not match target GW{target_gw}")
    entry_id = _int(team.get("entry_id"), "team_state.entry_id", minimum=1)
    if entry_id != EXPECTED_ENTRY_ID:
        raise OwnerStateError(f"unexpected entry_id {entry_id}; expected {EXPECTED_ENTRY_ID}")
    bank = _int(team.get("bank_tenths"), "team_state.bank_tenths", minimum=0)
    free_transfers = _int(team.get("free_transfers"), "team_state.free_transfers", minimum=1)
    if free_transfers > 5:
        raise OwnerStateError("free transfers exceeds FPL maximum 5")

    squad_raw = team.get("squad")
    if not isinstance(squad_raw, list) or len(squad_raw) != 15:
        raise OwnerStateError("owner state must contain exactly 15 squad rows")

    squad: list[dict[str, Any]] = []
    ids: set[int] = set()
    positions = {k: 0 for k in POSITION_COUNTS}
    teams: dict[int, int] = {}
    for idx, source in enumerate(squad_raw):
        if not isinstance(source, dict):
            raise OwnerStateError(f"squad[{idx}] must be an object")
        pid = _int(source.get("element_id"), f"squad[{idx}].element_id", minimum=1)
        if pid in ids:
            raise OwnerStateError(f"duplicate squad element_id {pid}")
        ids.add(pid)
        position = _normalize_position(source.get("position"))
        team_id = _int(source.get("team_id"), f"squad[{idx}].team_id", minimum=1)
        current_price = _int(source.get("current_price_tenths_at_snapshot"), f"current price {pid}", minimum=1)
        purchase = _int(source.get("purchase_price_tenths"), f"purchase price {pid}", minimum=1)
        selling = _int(source.get("selling_price_tenths"), f"selling price {pid}", minimum=1)
        if selling > current_price:
            raise OwnerStateError(f"selling price exceeds current price for element {pid}")
        official = official_players.get(pid)
        if official is None:
            raise OwnerStateError(f"owned element {pid} is missing from live Official FPL")
        checks = {
            "position": (position, official["position"]),
            "team_id": (team_id, official["team_id"]),
            "current_price_tenths": (current_price, official["current_price_tenths"]),
            "status": (source.get("status_at_snapshot"), official["status"]),
        }
        for label, (private_value, official_value) in checks.items():
            if private_value != official_value:
                raise OwnerStateError(
                    f"stale/mismatched owner element {pid}: {label} private={private_value!r} official={official_value!r}"
                )
        positions[position] += 1
        teams[team_id] = teams.get(team_id, 0) + 1
        squad.append({
            "element_id": pid,
            "web_name": source.get("web_name") if source.get("web_name") == official.get("web_name") else official.get("web_name"),
            "position": position,
            "team_id": team_id,
            "current_price_tenths": current_price,
            "status": official.get("status"),
            "can_transact": bool(source.get("can_transact_at_snapshot")),
            "purchase_price_tenths": purchase,
            "selling_price_tenths": selling,
        })
    if positions != POSITION_COUNTS:
        raise OwnerStateError(f"invalid squad position counts {positions}")
    if any(n > 3 for n in teams.values()):
        raise OwnerStateError(f"squad exceeds three-per-club rule: {teams}")

    return {
        "schema": SCHEMA,
        "entry_id": entry_id,
        "target_gameweek": target_gw,
        "published_gameweek": published_gw,
        "bank_tenths": bank,
        "free_transfers": free_transfers,
        "active_chip": team.get("active_chip"),
        "squad": sorted(squad, key=lambda row: row["element_id"]),
        "private_authority": {
            "run_id": run.get("run_id"),
            "release_tag": run.get("release_tag"),
            "published_at": run.get("published_at"),
            "season": run.get("season"),
            "attestation_scope": "PRIVATE_MANAGER",
            "immutable": True,
            "raw_snapshot_sha256": sha256_bytes(raw_bytes),
        },
        "privacy": {
            "classification": "PRIVATE_MANAGER_LOCAL_ONLY",
            "source_fields_whitelisted": True,
            "raw_snapshot_retained": False,
            "safe_for_public_artifact_upload": False,
        },
    }


def write_owner_state(raw_snapshot: Path, bootstrap_path: Path, output: Path) -> dict[str, Any]:
    raw_bytes = raw_snapshot.read_bytes()
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    result = validate_and_whitelist(raw_bytes, bootstrap)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    output.chmod(0o600)
    return result

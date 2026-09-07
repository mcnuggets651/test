from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"
SCHEMA = "airsenal-chat-official-fpl-v1"


class OfficialFPLError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch(url: str, timeout: float = 30.0) -> tuple[bytes, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "airsenal-chat/1 read-only"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            status = getattr(response, "status", 200)
            if status != 200:
                raise OfficialFPLError(f"Official FPL returned HTTP {status} for {url}")
    except Exception as exc:
        if isinstance(exc, OfficialFPLError):
            raise
        raise OfficialFPLError(f"failed to fetch Official FPL {url}: {type(exc).__name__}") from exc
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise OfficialFPLError(f"Official FPL returned invalid JSON for {url}") from exc
    return data, parsed


def acquire(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_raw, bootstrap = _fetch(BOOTSTRAP_URL)
    fixtures_raw, fixtures = _fetch(FIXTURES_URL)
    if not isinstance(bootstrap, dict) or not isinstance(fixtures, list):
        raise OfficialFPLError("unexpected Official FPL payload shapes")
    next_events = [event for event in bootstrap.get("events", []) if isinstance(event, dict) and event.get("is_next") is True]
    if len(next_events) != 1:
        raise OfficialFPLError(f"expected exactly one is_next event, found {len(next_events)}")
    event = next_events[0]
    target_gw = int(event["id"])
    deadline = event.get("deadline_time")
    if not deadline:
        raise OfficialFPLError("next event has no deadline_time")
    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
    bootstrap_path = output_dir / "bootstrap-static.json"
    fixtures_path = output_dir / "fixtures.json"
    bootstrap_path.write_bytes(bootstrap_raw)
    fixtures_path.write_bytes(fixtures_raw)
    manifest = {
        "schema": SCHEMA,
        "retrieved_at": retrieved_at,
        "target_gameweek": target_gw,
        "deadline_time": deadline,
        "endpoints": {
            "bootstrap_static": {"url": BOOTSTRAP_URL, "sha256": sha256_bytes(bootstrap_raw), "bytes": len(bootstrap_raw)},
            "fixtures": {"url": FIXTURES_URL, "sha256": sha256_bytes(fixtures_raw), "bytes": len(fixtures_raw)},
        },
        "counts": {"players": len(bootstrap.get("elements", [])), "fixtures": len(fixtures)},
    }
    manifest_path = output_dir / "official_fpl_provenance.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"manifest": manifest, "bootstrap_path": bootstrap_path, "fixtures_path": fixtures_path, "manifest_path": manifest_path}

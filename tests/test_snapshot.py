from __future__ import annotations

from pathlib import Path

from apex_price_risk.snapshot import read_snapshot_gzip, write_snapshot_gzip
from conftest import make_snapshot


def test_snapshot_roundtrip_and_deterministic_gzip(tmp_path: Path) -> None:
    snapshot = make_snapshot("2026-08-30T12:00:00Z")
    first = tmp_path / "a.json.gz"
    second = tmp_path / "b.json.gz"
    write_snapshot_gzip(first, snapshot)
    write_snapshot_gzip(second, snapshot)
    assert first.read_bytes() == second.read_bytes()
    assert read_snapshot_gzip(first) == snapshot

from __future__ import annotations

import gzip
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .schemas import PriceSnapshot


def snapshot_filename(captured_at_utc: str) -> str:
    stamp = datetime.fromisoformat(captured_at_utc.replace("Z", "+00:00"))
    return stamp.strftime("%Y%m%dT%H%M%SZ.json.gz")


def write_snapshot_gzip(path: Path, snapshot: PriceSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(
        snapshot.to_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with (
        temp.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0) as handle,
    ):
        handle.write(encoded)
    os.replace(temp, path)


def read_snapshot_gzip(path: Path) -> PriceSnapshot:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return PriceSnapshot.from_dict(payload)


def load_snapshots(root: Path) -> list[PriceSnapshot]:
    snapshots = [read_snapshot_gzip(path) for path in sorted(root.rglob("*.json.gz"))]
    return sorted(snapshots, key=lambda snapshot: snapshot.captured_at_utc)

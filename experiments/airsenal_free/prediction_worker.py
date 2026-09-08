#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from pathlib import Path
from typing import Any

SCHEMA = "airsenal-chat-prediction-stage-v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object in {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-state", type=Path, required=True)
    parser.add_argument("--horizon", type=int, choices=(3, 5), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    from airsenal.framework.schema import session
    from airsenal.framework.season import CURRENT_SEASON
    from airsenal.scripts.fill_predictedscore_table import make_predictedscore_table

    owner = load_object(args.owner_state)
    if owner.get("schema") != "airsenal-chat-owner-state-v1":
        raise ValueError("unexpected owner-state schema")
    target_gw = int(owner["target_gameweek"])
    gameweeks = list(range(target_gw, target_gw + args.horizon))
    if gameweeks[-1] > 38:
        raise ValueError(
            f"AIrsenal cannot produce requested H{args.horizon}: range extends beyond GW38"
        )

    random.seed(42)
    db_path = Path(os.environ["AIRSENAL_DB_FILE"]).expanduser().resolve()
    pre_prediction_db_sha256 = sha256_file(db_path)
    prediction_tag = make_predictedscore_table(
        gw_range=gameweeks,
        season=CURRENT_SEASON,
        include_bonus=True,
        include_cards=True,
        include_saves=True,
        include_def_con=True,
        tag_prefix=f"airsenal-chat-h{args.horizon}-",
        dbsession=session,
    )
    post_prediction_db_sha256 = sha256_file(db_path)
    payload = {
        "schema": SCHEMA,
        "horizon": args.horizon,
        "gameweeks": gameweeks,
        "entry_id": int(owner["entry_id"]),
        "target_gameweek": target_gw,
        "prediction_tag": prediction_tag,
        "pre_prediction_db_sha256": pre_prediction_db_sha256,
        "post_prediction_db_sha256": post_prediction_db_sha256,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

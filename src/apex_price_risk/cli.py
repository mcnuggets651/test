from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .advisory import build_route_advisory
from .architecture import architecture_check
from .evaluate import evaluate_forecast_history
from .model import ModelBundle, build_forecast, cold_start_bundle
from .official import capture_from_bootstrap, fetch_bootstrap
from .schemas import ForecastBundle
from .snapshot import load_snapshots, read_snapshot_gzip, write_snapshot_gzip
from .train import train_model_bundle


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "capture":
        snapshot = capture_from_bootstrap(fetch_bootstrap())
        write_snapshot_gzip(Path(args.output), snapshot)
        print(snapshot.captured_at_utc)
        return 0
    if args.command == "forecast":
        snapshot = read_snapshot_gzip(Path(args.snapshot))
        previous = read_snapshot_gzip(Path(args.previous)) if args.previous else None
        model = _read_model(Path(args.model)) if args.model else cold_start_bundle()
        forecast = build_forecast(snapshot, previous, model, args.code_sha)
        _write_json(Path(args.output), forecast.to_dict())
        print(forecast.model_id)
        return 0
    if args.command == "train":
        model = train_model_bundle(load_snapshots(Path(args.observations)))
        _write_json(Path(args.output), model.to_dict())
        print(model.model_id)
        return 0
    if args.command == "evaluate":
        snapshots = load_snapshots(Path(args.observations))
        prediction_paths = sorted(Path(args.predictions).rglob("*.json"))
        report = evaluate_forecast_history(snapshots, prediction_paths)
        _write_json(Path(args.output), report)
        print(report["status"])
        return 0
    if args.command == "route-advisory":
        forecast = _read_forecast(Path(args.forecast))
        with Path(args.routes).open("r", encoding="utf-8") as handle:
            routes: dict[str, Any] = json.load(handle)
        _write_json(Path(args.output), build_route_advisory(forecast, routes))
        print("ADVISORY_ONLY")
        return 0
    if args.command == "architecture-check":
        root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
        errors = architecture_check(root)
        if errors:
            for error in errors:
                print(error)
            return 1
        print("ARCHITECTURE_OK_NON_SERVING")
        return 0
    parser.error("unknown command")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apex-price-risk")
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--output", required=True)
    forecast = sub.add_parser("forecast")
    forecast.add_argument("--snapshot", required=True)
    forecast.add_argument("--previous")
    forecast.add_argument("--model")
    forecast.add_argument("--output", required=True)
    forecast.add_argument("--code-sha", default=os.environ.get("GITHUB_SHA", "UNKNOWN"))
    train = sub.add_parser("train")
    train.add_argument("--observations", required=True)
    train.add_argument("--output", required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--observations", required=True)
    evaluate.add_argument("--predictions", required=True)
    evaluate.add_argument("--output", required=True)
    advisory = sub.add_parser("route-advisory")
    advisory.add_argument("--forecast", required=True)
    advisory.add_argument("--routes", required=True)
    advisory.add_argument("--output", required=True)
    check = sub.add_parser("architecture-check")
    check.add_argument("--root")
    return parser


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _read_model(path: Path) -> ModelBundle:
    with path.open("r", encoding="utf-8") as handle:
        return ModelBundle.from_dict(json.load(handle))


def _read_forecast(path: Path) -> ForecastBundle:
    with path.open("r", encoding="utf-8") as handle:
        return ForecastBundle.from_dict(json.load(handle))


if __name__ == "__main__":
    raise SystemExit(main())

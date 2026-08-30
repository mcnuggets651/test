from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from .boundary import FORECAST_HORIZON_HOURS, PRODUCTION_INFLUENCE, SERVING_AUTHORIZED
from .features import FEATURE_NAMES, build_feature_rows
from .schemas import FEATURE_VERSION, ForecastBundle, ForecastRow, PriceSnapshot, SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class DirectionModel:
    kind: str
    mean: tuple[float, ...] = ()
    scale: tuple[float, ...] = ()
    coef: tuple[float, ...] = ()
    intercept: float = 0.0
    calibration_a: float = 1.0
    calibration_b: float = 0.0
    n_train: int = 0
    n_positive: int = 0
    metrics: dict[str, float] | None = None

    def predict(self, features: tuple[float, ...], direction: str) -> float:
        if self.kind == "cold_start":
            return _cold_start_probability(features, direction)
        if self.kind != "trained":
            raise ValueError(f"Unknown model kind: {self.kind}")
        if not (len(self.mean) == len(self.scale) == len(self.coef) == len(features)):
            raise ValueError("Model feature dimensions do not match forecast features")
        x = np.asarray(features, dtype=float)
        mean = np.asarray(self.mean, dtype=float)
        scale = np.asarray(self.scale, dtype=float)
        coef = np.asarray(self.coef, dtype=float)
        safe_scale = np.where(scale == 0.0, 1.0, scale)
        base_logit = self.intercept + float(np.dot((x - mean) / safe_scale, coef))
        calibrated_logit = self.calibration_a * base_logit + self.calibration_b
        return _sigmoid(calibrated_logit)


@dataclass(frozen=True, slots=True)
class ModelBundle:
    schema_version: int
    feature_version: int
    created_at_utc: str
    model_id: str
    rise: DirectionModel
    fall: DirectionModel
    prospective_qualified: bool
    serving_authorized: bool
    production_influence: str
    training_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelBundle:
        return cls(
            schema_version=int(payload["schema_version"]),
            feature_version=int(payload["feature_version"]),
            created_at_utc=str(payload["created_at_utc"]),
            model_id=str(payload["model_id"]),
            rise=DirectionModel(**payload["rise"]),
            fall=DirectionModel(**payload["fall"]),
            prospective_qualified=bool(payload.get("prospective_qualified", False)),
            serving_authorized=bool(payload.get("serving_authorized", False)),
            production_influence=str(payload.get("production_influence", "NONE")),
            training_summary=dict(payload.get("training_summary", {})),
        )


def cold_start_bundle(created_at: datetime | None = None) -> ModelBundle:
    created_at = (created_at or datetime.now(UTC)).astimezone(UTC)
    rise = DirectionModel(kind="cold_start")
    fall = DirectionModel(kind="cold_start")
    training_summary: dict[str, Any] = {"status": "COLD_START"}
    hash_payload = {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "created_at_utc": created_at.isoformat(),
        "rise": asdict(rise),
        "fall": asdict(fall),
        "prospective_qualified": False,
        "serving_authorized": SERVING_AUTHORIZED,
        "production_influence": PRODUCTION_INFLUENCE,
        "training_summary": training_summary,
    }
    return ModelBundle(
        schema_version=SCHEMA_VERSION,
        feature_version=FEATURE_VERSION,
        created_at_utc=created_at.isoformat(),
        model_id=_model_id(hash_payload),
        rise=rise,
        fall=fall,
        prospective_qualified=False,
        serving_authorized=SERVING_AUTHORIZED,
        production_influence=PRODUCTION_INFLUENCE,
        training_summary=training_summary,
    )


def make_model_bundle(
    *,
    rise: DirectionModel,
    fall: DirectionModel,
    training_summary: dict[str, Any],
    created_at: datetime | None = None,
) -> ModelBundle:
    created_at = (created_at or datetime.now(UTC)).astimezone(UTC)
    hash_payload = {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "created_at_utc": created_at.isoformat(),
        "rise": asdict(rise),
        "fall": asdict(fall),
        "prospective_qualified": False,
        "serving_authorized": SERVING_AUTHORIZED,
        "production_influence": PRODUCTION_INFLUENCE,
        "training_summary": training_summary,
    }
    return ModelBundle(
        schema_version=SCHEMA_VERSION,
        feature_version=FEATURE_VERSION,
        created_at_utc=created_at.isoformat(),
        model_id=_model_id(hash_payload),
        rise=rise,
        fall=fall,
        prospective_qualified=False,
        serving_authorized=SERVING_AUTHORIZED,
        production_influence=PRODUCTION_INFLUENCE,
        training_summary=training_summary,
    )


def build_forecast(
    snapshot: PriceSnapshot,
    previous: PriceSnapshot | None,
    model: ModelBundle,
    code_sha: str,
    generated_at: datetime | None = None,
) -> ForecastBundle:
    if model.serving_authorized or model.production_influence != "NONE":
        raise RuntimeError("Price-risk model violates the non-serving boundary")
    generated_at = (generated_at or datetime.now(UTC)).astimezone(UTC)
    players = snapshot.player_map()
    rows: list[ForecastRow] = []
    for feature_row in build_feature_rows(snapshot, previous):
        player = players[feature_row.element_id]
        p_rise = model.rise.predict(feature_row.features, "rise")
        p_fall = model.fall.predict(feature_row.features, "fall")
        trained = model.rise.kind == "trained" and model.fall.kind == "trained"
        rows.append(
            ForecastRow(
                element_id=player.element_id,
                web_name=player.web_name,
                now_cost=player.now_cost,
                p_rise_24h=round(p_rise, 8),
                p_fall_24h=round(p_fall, 8),
                confidence="MEDIUM" if trained else "LOW",
                model_status="SHADOW_TRAINED_UNQUALIFIED" if trained else "COLD_START",
            )
        )
    return ForecastBundle(
        schema_version=SCHEMA_VERSION,
        feature_version=FEATURE_VERSION,
        generated_at_utc=generated_at.isoformat(),
        source_snapshot_at_utc=snapshot.captured_at_utc,
        source_bootstrap_sha256=snapshot.bootstrap_sha256,
        code_sha=code_sha,
        model_id=model.model_id,
        horizon_hours=FORECAST_HORIZON_HOURS,
        serving_authorized=SERVING_AUTHORIZED,
        production_influence=PRODUCTION_INFLUENCE,
        rows=tuple(rows),
    )


def _cold_start_probability(features: tuple[float, ...], direction: str) -> float:
    values = dict(zip(FEATURE_NAMES, features, strict=True))
    pressure = float(values["net_event_per_owner"])
    velocity = float(values["net_velocity_per_owner_hour"])
    available = float(values["available"])
    if direction == "rise":
        score = -4.6 + 8.0 * max(pressure, 0.0) + 90.0 * max(velocity, 0.0) + 0.2 * available
    elif direction == "fall":
        score = -4.6 + 8.0 * max(-pressure, 0.0) + 90.0 * max(-velocity, 0.0) + 0.7 * (1.0 - available)
    else:
        raise ValueError("direction must be rise or fall")
    return min(_sigmoid(score), 0.45)


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _model_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

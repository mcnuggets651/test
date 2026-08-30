from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from apex_price_risk.features import FEATURE_NAMES, LabeledExample
from apex_price_risk.train import PURGE_HOURS, _chronological_split, _train_direction


def _examples() -> list[LabeledExample]:
    examples: list[LabeledExample] = []
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    for time_index in range(40):
        timestamp = (start + timedelta(days=time_index)).isoformat()
        for row_index in range(100):
            positive = int(row_index % 10 == 0)
            signal = 0.25 if positive else -0.01
            features = tuple(
                signal if name == "net_event_per_owner" else 0.0 for name in FEATURE_NAMES
            )
            examples.append(
                LabeledExample(
                    observed_at_utc=timestamp,
                    element_id=row_index,
                    features=features,
                    rise_24h=positive,
                    fall_24h=positive,
                )
            )
    return examples


def test_chronological_training_can_produce_serializable_shadow_model() -> None:
    model, summary = _train_direction(_examples(), "rise")
    assert model.kind == "trained"
    assert summary["status"] == "TRAINED"
    assert summary["purge_hours"] == PURGE_HOURS
    assert len(model.coef) == len(FEATURE_NAMES)
    assert model.metrics is not None


def test_time_split_purges_label_horizon_between_partitions() -> None:
    examples = _examples()
    times = np.asarray([example.observed_at_utc for example in examples], dtype=object)
    split = _chronological_split(times)
    parsed = np.asarray([datetime.fromisoformat(str(value)) for value in times], dtype=object)
    train_max = max(parsed[split.train])
    calibration_min = min(parsed[split.calibration])
    calibration_max = max(parsed[split.calibration])
    test_min = min(parsed[split.test])
    assert (calibration_min - train_max).total_seconds() / 3600.0 >= PURGE_HOURS
    assert (test_min - calibration_max).total_seconds() / 3600.0 >= PURGE_HOURS

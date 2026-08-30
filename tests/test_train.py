from __future__ import annotations

from apex_price_risk.features import FEATURE_NAMES, LabeledExample
from apex_price_risk.train import _train_direction


def test_chronological_training_can_produce_serializable_shadow_model() -> None:
    examples: list[LabeledExample] = []
    for time_index in range(20):
        timestamp = f"2026-08-{time_index + 1:02d}T12:00:00+00:00"
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
    model, summary = _train_direction(examples, "rise")
    assert model.kind == "trained"
    assert summary["status"] == "TRAINED"
    assert len(model.coef) == len(FEATURE_NAMES)
    assert model.metrics is not None

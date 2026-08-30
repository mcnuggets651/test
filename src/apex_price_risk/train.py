from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss
from sklearn.preprocessing import StandardScaler

from .features import FEATURE_NAMES, LabeledExample, build_labeled_examples
from .model import DirectionModel, ModelBundle, make_model_bundle
from .schemas import PriceSnapshot

MIN_EXAMPLES = 1200
MIN_POSITIVES = 20
MIN_NEGATIVES = 200
MIN_UNIQUE_TIMES = 10


@dataclass(frozen=True, slots=True)
class Split:
    train: np.ndarray[Any, np.dtype[np.bool_]]
    calibration: np.ndarray[Any, np.dtype[np.bool_]]
    test: np.ndarray[Any, np.dtype[np.bool_]]


def train_model_bundle(snapshots: list[PriceSnapshot]) -> ModelBundle:
    examples = build_labeled_examples(snapshots)
    rise, rise_summary = _train_direction(examples, "rise")
    fall, fall_summary = _train_direction(examples, "fall")
    return make_model_bundle(
        rise=rise,
        fall=fall,
        training_summary={
            "status": "SHADOW_TRAINED" if rise.kind == fall.kind == "trained" else "COLD_START_PARTIAL",
            "n_examples": len(examples),
            "rise": rise_summary,
            "fall": fall_summary,
            "prospective_qualification": "NOT_EVALUATED_FOR_INTEGRATION",
        },
    )


def _train_direction(
    examples: list[LabeledExample], direction: Literal["rise", "fall"]
) -> tuple[DirectionModel, dict[str, Any]]:
    labels = np.asarray(
        [example.rise_24h if direction == "rise" else example.fall_24h for example in examples],
        dtype=int,
    )
    summary: dict[str, Any] = {
        "n": int(len(labels)),
        "positives": int(labels.sum()) if len(labels) else 0,
    }
    if not _enough_data(examples, labels):
        summary["status"] = "INSUFFICIENT_DATA"
        return DirectionModel(kind="cold_start"), summary

    x = np.asarray([example.features for example in examples], dtype=float)
    times = np.asarray([example.observed_at_utc for example in examples], dtype=object)
    split = _chronological_split(times)
    if not _split_has_both_classes(labels, split):
        summary["status"] = "INSUFFICIENT_CLASS_COVERAGE_IN_TIME_SPLIT"
        return DirectionModel(kind="cold_start"), summary

    scaler = StandardScaler().fit(x[split.train])
    x_train = scaler.transform(x[split.train])
    base = LogisticRegression(class_weight="balanced", max_iter=2000, solver="lbfgs")
    base.fit(x_train, labels[split.train])

    calibration_logits = base.decision_function(scaler.transform(x[split.calibration])).reshape(-1, 1)
    calibrator = LogisticRegression(C=1_000_000.0, max_iter=2000, solver="lbfgs")
    calibrator.fit(calibration_logits, labels[split.calibration])

    test_logits = base.decision_function(scaler.transform(x[split.test])).reshape(-1, 1)
    test_prob = calibrator.predict_proba(test_logits)[:, 1]
    y_test = labels[split.test]
    train_prevalence = float(labels[split.train].mean())
    model_brier = float(brier_score_loss(y_test, test_prob))
    naive_brier = float(np.mean((y_test - train_prevalence) ** 2))
    metrics = {
        "brier": model_brier,
        "naive_brier": naive_brier,
        "brier_skill": 0.0 if naive_brier == 0.0 else 1.0 - model_brier / naive_brier,
        "log_loss": float(log_loss(y_test, test_prob, labels=[0, 1])),
        "average_precision": float(average_precision_score(y_test, test_prob)),
        "test_prevalence": float(y_test.mean()),
        "ece_10": _expected_calibration_error(y_test, test_prob, 10),
    }
    summary.update({"status": "TRAINED", "metrics": metrics})
    return (
        DirectionModel(
            kind="trained",
            mean=tuple(float(value) for value in scaler.mean_),
            scale=tuple(float(value) for value in scaler.scale_),
            coef=tuple(float(value) for value in base.coef_[0]),
            intercept=float(base.intercept_[0]),
            calibration_a=float(calibrator.coef_[0][0]),
            calibration_b=float(calibrator.intercept_[0]),
            n_train=int(split.train.sum()),
            n_positive=int(labels[split.train].sum()),
            metrics=metrics,
        ),
        summary,
    )


def _enough_data(examples: list[LabeledExample], labels: np.ndarray[Any, np.dtype[np.int_]]) -> bool:
    if len(examples) < MIN_EXAMPLES:
        return False
    positives = int(labels.sum())
    negatives = int(len(labels) - positives)
    unique_times = len({example.observed_at_utc for example in examples})
    return positives >= MIN_POSITIVES and negatives >= MIN_NEGATIVES and unique_times >= MIN_UNIQUE_TIMES


def _chronological_split(times: np.ndarray[Any, np.dtype[object]]) -> Split:
    unique_times = sorted(set(str(value) for value in times.tolist()))
    first_cut = unique_times[max(1, int(len(unique_times) * 0.70)) - 1]
    second_cut = unique_times[max(2, int(len(unique_times) * 0.85)) - 1]
    train = np.asarray([str(value) <= first_cut for value in times], dtype=bool)
    calibration = np.asarray([first_cut < str(value) <= second_cut for value in times], dtype=bool)
    test = np.asarray([str(value) > second_cut for value in times], dtype=bool)
    return Split(train=train, calibration=calibration, test=test)


def _split_has_both_classes(
    labels: np.ndarray[Any, np.dtype[np.int_]], split: Split
) -> bool:
    for mask in (split.train, split.calibration, split.test):
        values = labels[mask]
        if len(values) == 0 or len(set(int(value) for value in values.tolist())) < 2:
            return False
    return True


def _expected_calibration_error(
    labels: np.ndarray[Any, np.dtype[np.int_]], probabilities: np.ndarray[Any, np.dtype[np.float64]], bins: int
) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(labels)
    error = 0.0
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        mask = (probabilities >= low) & (probabilities < high if high < 1.0 else probabilities <= high)
        if not mask.any():
            continue
        error += float(mask.sum()) / total * abs(float(probabilities[mask].mean()) - float(labels[mask].mean()))
    return error

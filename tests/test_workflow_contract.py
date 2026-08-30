from __future__ import annotations

from pathlib import Path

import yaml


def _load(name: str) -> dict[object, object]:
    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / name
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    assert isinstance(payload, dict)
    return payload


def test_capture_schedule_and_write_boundary() -> None:
    workflow = _load("capture.yml")
    on = workflow.get("on") or workflow.get(True)
    assert isinstance(on, dict)
    schedule = on["schedule"]
    assert schedule == [{"cron": "17 */3 * * *"}]
    assert workflow["permissions"] == {"contents": "write"}
    text = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "capture.yml").read_text()
    assert "observations" in text
    assert "cancel-in-progress: false" in text


def test_evaluation_is_non_serving_and_serialized_with_capture() -> None:
    workflow = _load("evaluate.yml")
    assert workflow["concurrency"]["group"] == "apex-price-risk-data"
    text = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "evaluate.yml").read_text()
    assert "architecture-check" in text
    assert "observations" in text

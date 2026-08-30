from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(name: str) -> dict[object, object]:
    with (WORKFLOWS / name).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    assert isinstance(payload, dict)
    return payload


def test_capture_schedule_deployment_smoke_and_write_boundary() -> None:
    workflow = _load("capture.yml")
    on = workflow.get("on") or workflow.get(True)
    assert isinstance(on, dict)
    assert on["schedule"] == [{"cron": "17 */3 * * *"}]
    assert on["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "write"}
    assert workflow["concurrency"]["group"] == "apex-price-risk-data"
    assert workflow["concurrency"]["cancel-in-progress"] is False
    text = (WORKFLOWS / "capture.yml").read_text(encoding="utf-8")
    assert "observations" in text
    assert "actions/checkout@v7" in text
    assert "actions/setup-python@v7" in text


def test_evaluation_is_non_serving_and_serialized_with_capture() -> None:
    workflow = _load("evaluate.yml")
    assert workflow["concurrency"]["group"] == "apex-price-risk-data"
    text = (WORKFLOWS / "evaluate.yml").read_text(encoding="utf-8")
    assert "architecture-check" in text
    assert "observations" in text
    assert "actions/checkout@v7" in text
    assert "actions/setup-python@v7" in text


def test_ci_runs_lint_tests_compile_and_architecture_guard() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    for required in ("ruff check", "pytest", "compileall", "architecture-check"):
        assert required in text
    assert "actions/checkout@v7" in text
    assert "actions/setup-python@v7" in text

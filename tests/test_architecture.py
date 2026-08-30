from __future__ import annotations

from pathlib import Path

from apex_price_risk.architecture import architecture_check
from apex_price_risk.boundary import APEX_RUNTIME_DEPENDENCY_ALLOWED, PRODUCTION_INFLUENCE, SERVING_AUTHORIZED


def test_hard_boundary_constants() -> None:
    assert SERVING_AUTHORIZED is False
    assert PRODUCTION_INFLUENCE == "NONE"
    assert APEX_RUNTIME_DEPENDENCY_ALLOWED is False


def test_repository_has_no_runtime_coupling() -> None:
    root = Path(__file__).resolve().parents[1]
    assert architecture_check(root) == []

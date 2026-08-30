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


def test_architecture_guard_rejects_apex_runtime_import(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "bad.py").write_text("import apex\n", encoding="utf-8")
    errors = architecture_check(tmp_path)
    assert any("Forbidden runtime dependency" in error for error in errors)


def test_architecture_guard_rejects_apex_workflow_coupling(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (workflow_dir / "bad.yml").write_text("run: mcnuggets651/fpl-apex\n", encoding="utf-8")
    errors = architecture_check(tmp_path)
    assert any("Forbidden Apex coupling marker" in error for error in errors)

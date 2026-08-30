from __future__ import annotations

import ast
from pathlib import Path

from .boundary import assert_non_serving
from .official import FPL_BOOTSTRAP_URL

FORBIDDEN_IMPORT_PREFIXES = ("apex", "apex_fpl")
FORBIDDEN_WORKFLOW_MARKERS = (
    "mcnuggets651/fpl-apex",
    "APEX_V2_",
    "FPL_SESSION_COOKIE",
    "FPL_X_API_AUTHORIZATION",
)


def architecture_check(repo_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        assert_non_serving()
    except RuntimeError as exc:
        errors.append(str(exc))
    if FPL_BOOTSTRAP_URL != "https://fantasy.premierleague.com/api/bootstrap-static/":
        errors.append("Official acquisition endpoint drifted")

    for path in (repo_root / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if module == "apex_price_risk" or module.startswith("apex_price_risk."):
                    continue
                if any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES):
                    errors.append(f"Forbidden runtime dependency {module!r} in {path.relative_to(repo_root)}")

    workflow_root = repo_root / ".github" / "workflows"
    if workflow_root.exists():
        for path in workflow_root.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            for marker in FORBIDDEN_WORKFLOW_MARKERS:
                if marker in text:
                    errors.append(f"Forbidden Apex coupling marker {marker!r} in {path.name}")
    return errors

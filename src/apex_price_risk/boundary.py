"""Non-negotiable production boundary for Apex Price Risk."""

SERVING_AUTHORIZED: bool = False
PRODUCTION_INFLUENCE: str = "NONE"
APEX_RUNTIME_DEPENDENCY_ALLOWED: bool = False
FORECAST_HORIZON_HOURS: int = 24


def assert_non_serving() -> None:
    """Fail if a future edit weakens the advisory-only contract."""
    if SERVING_AUTHORIZED:
        raise RuntimeError("Apex Price Risk must remain non-serving")
    if PRODUCTION_INFLUENCE != "NONE":
        raise RuntimeError("Apex Price Risk must not influence canonical Apex decisions")
    if APEX_RUNTIME_DEPENDENCY_ALLOWED:
        raise RuntimeError("Runtime dependency on FPL Apex is forbidden")

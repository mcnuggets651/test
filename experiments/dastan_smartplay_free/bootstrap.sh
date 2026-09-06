#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="${DSS_VENDOR:-$ROOT/.vendor}"
VENV="${DSS_VENV:-$ROOT/.venv}"
DASTAN_SHA="19376523afdec4836d0e6b5632c6773d0fe40c53"
SOLVER_SHA="7ec56e944982020f8709db5d00b0b78821fb1f38"

mkdir -p "$VENDOR"

pin_repo() {
  local url="$1"
  local path="$2"
  local sha="$3"
  if [[ ! -d "$path/.git" ]]; then
    git clone "$url" "$path"
  fi
  git -C "$path" fetch --quiet origin "$sha"
  git -C "$path" checkout --quiet --detach "$sha"
  local actual
  actual="$(git -C "$path" rev-parse HEAD)"
  if [[ "$actual" != "$sha" ]]; then
    echo "pin verification failed for $path: $actual != $sha" >&2
    exit 2
  fi
}

pin_repo https://github.com/qazybekb/smartplayfpl-dastan.git \
  "$VENDOR/smartplayfpl-dastan" "$DASTAN_SHA"
pin_repo https://github.com/qazybekb/smartplayfpl-solver.git \
  "$VENDOR/smartplayfpl-solver" "$SOLVER_SHA"

if [[ ! -x "$VENV/bin/python" ]]; then
  python3.12 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$VENDOR/smartplayfpl-dastan/requirements-data.txt"
"$VENV/bin/python" -m pip install -e "$VENDOR/smartplayfpl-solver"

"$VENV/bin/python" -m compileall -q "$ROOT/live_gw.py"
"$VENV/bin/python" -m dastan.artifacts 2>/dev/null || \
  PYTHONPATH="$VENDOR/smartplayfpl-dastan" "$VENV/bin/python" -m dastan.artifacts
"$VENV/bin/smartplay-solver" demo >/dev/null

echo "Pinned free stack ready"
echo "Dastan: $DASTAN_SHA"
echo "SmartPlay Solver: $SOLVER_SHA"
echo "Python: $VENV/bin/python"

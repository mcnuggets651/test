#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAT_HOME="${AIRSENAL_CHAT_HOME:-$HOME/.local/share/airsenal-chat}"
PINS="$SCRIPT_DIR/pins.json"

read_pin() {
  local key="$1"
  python3 - "$PINS" "$key" <<'PY'
import json,sys
with open(sys.argv[1], encoding='utf-8') as f:
    value=json.load(f)[sys.argv[2]]
print(value)
PY
}

UPSTREAM_SHA="$(read_pin upstream_sha)"
UPSTREAM_VERSION="$(read_pin upstream_project_version)"
LICENSE_BLOB="$(read_pin upstream_license_blob_sha)"
LOCK_BLOB="$(read_pin upstream_uv_lock_blob_sha)"
UV_VERSION="$(read_pin uv_version)"
PYTHON_VERSION="$(read_pin python_version)"

mkdir -p "$CHAT_HOME" "$CHAT_HOME/upstream" "$CHAT_HOME/uv-cache"
chmod 700 "$CHAT_HOME"

python_is_exact() {
  local candidate="$1"
  [[ -x "$candidate" ]] || return 1
  [[ "$($candidate -c 'import sys; print(sys.version.split()[0])' 2>/dev/null)" == "$PYTHON_VERSION" ]]
}

resolve_exact_python() {
  local candidate
  for candidate in "$(command -v python3 2>/dev/null || true)" "/opt/homebrew/opt/python@3.12/bin/python3.12" "$(command -v python3.12 2>/dev/null || true)"; do
    if [[ -n "$candidate" ]] && python_is_exact "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    # This function is called in a command substitution. Homebrew can print
    # advisory text on stdout even for successful operations, so suppress all
    # Homebrew command output here. The only stdout from this function must be
    # the final exact interpreter path emitted by printf below.
    brew update --quiet >/dev/null 2>&1
    if brew list --versions python@3.12 >/dev/null 2>&1; then
      brew upgrade python@3.12 >/dev/null 2>&1 || true
    else
      brew install python@3.12 >/dev/null 2>&1
    fi
    candidate="$(brew --prefix python@3.12 2>/dev/null)/bin/python3.12"
    if python_is_exact "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  echo "ERROR: exact CPython $PYTHON_VERSION is unavailable; refusing to loosen runtime pin" >&2
  return 2
}

EXACT_PYTHON="$(resolve_exact_python)"

echo "Using exact Python: $EXACT_PYTHON ($PYTHON_VERSION)"

BOOTSTRAP_VENV="$CHAT_HOME/bootstrap-venv"
if [[ -x "$BOOTSTRAP_VENV/bin/python" ]] && [[ "$($BOOTSTRAP_VENV/bin/python -c 'import sys; print(sys.version.split()[0])')" != "$PYTHON_VERSION" ]]; then
  rm -rf "$BOOTSTRAP_VENV"
fi
if [[ ! -x "$BOOTSTRAP_VENV/bin/python" ]]; then
  "$EXACT_PYTHON" -m venv "$BOOTSTRAP_VENV"
fi
if [[ ! -x "$BOOTSTRAP_VENV/bin/uv" ]] || [[ "$($BOOTSTRAP_VENV/bin/uv --version 2>/dev/null || true)" != "uv $UV_VERSION" ]]; then
  "$BOOTSTRAP_VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "uv==$UV_VERSION"
fi
UV="$BOOTSTRAP_VENV/bin/uv"
export UV_CACHE_DIR="$CHAT_HOME/uv-cache"

UPSTREAM="$CHAT_HOME/upstream/AIrsenal"
if [[ ! -d "$UPSTREAM/.git" ]]; then
  git clone --filter=blob:none https://github.com/alan-turing-institute/AIrsenal.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch --quiet --force origin "$UPSTREAM_SHA"
git -C "$UPSTREAM" checkout --quiet --detach "$UPSTREAM_SHA"
git -C "$UPSTREAM" reset --quiet --hard "$UPSTREAM_SHA"
git -C "$UPSTREAM" clean --quiet -fdx

ACTUAL_SHA="$(git -C "$UPSTREAM" rev-parse HEAD)"
[[ "$ACTUAL_SHA" == "$UPSTREAM_SHA" ]] || { echo "ERROR: upstream SHA mismatch" >&2; exit 2; }
[[ -z "$(git -C "$UPSTREAM" status --porcelain=v1)" ]] || { echo "ERROR: upstream checkout dirty" >&2; exit 2; }
[[ "$(git -C "$UPSTREAM" hash-object LICENSE)" == "$LICENSE_BLOB" ]] || { echo "ERROR: upstream LICENSE pin mismatch" >&2; exit 2; }
[[ "$(git -C "$UPSTREAM" hash-object uv.lock)" == "$LOCK_BLOB" ]] || { echo "ERROR: upstream uv.lock pin mismatch" >&2; exit 2; }

VENV="$CHAT_HOME/venv"
if [[ -x "$VENV/bin/python" ]] && [[ "$($VENV/bin/python -c 'import sys; print(sys.version.split()[0])')" != "$PYTHON_VERSION" ]]; then
  rm -rf "$VENV"
fi
export UV_PROJECT_ENVIRONMENT="$VENV"
"$UV" sync --python "$EXACT_PYTHON" --project "$UPSTREAM" --frozen --no-dev

"$VENV/bin/python" - "$UPSTREAM_VERSION" "$PYTHON_VERSION" <<'PY'
import airsenal,sys
expected_version, expected_python=sys.argv[1:]
actual=getattr(airsenal,'__version__',None)
if actual != expected_version:
    raise SystemExit(f"AIrsenal version mismatch: {actual!r} != {expected_version!r}")
if sys.version.split()[0] != expected_python:
    raise SystemExit(f"Python version mismatch: {sys.version.split()[0]!r} != {expected_python!r}")
from airsenal.scripts.fill_predictedscore_table import make_predictedscore_table
from airsenal.scripts.fill_transfersuggestion_table import run_optimization
print(f"AIrsenal {actual} import OK on Python {sys.version.split()[0]}")
PY

"$VENV/bin/python" - "$CHAT_HOME/runtime.json" "$UPSTREAM_SHA" "$UPSTREAM_VERSION" "$LICENSE_BLOB" "$LOCK_BLOB" "$UV_VERSION" "$PYTHON_VERSION" "$UPSTREAM" "$VENV" <<'PY'
import datetime as dt,json,sys
(path,sha,version,license_blob,lock_blob,uv_version,python_version,upstream,venv)=sys.argv[1:]
payload={
  "schema":"airsenal-chat-runtime-v1",
  "verified_at":dt.datetime.now(dt.timezone.utc).isoformat(),
  "upstream_sha":sha,
  "upstream_project_version":version,
  "upstream_license_blob_sha":license_blob,
  "upstream_uv_lock_blob_sha":lock_blob,
  "uv_version":uv_version,
  "python_version_pin":python_version,
  "upstream_path":upstream,
  "venv_path":venv,
}
with open(path,'w',encoding='utf-8') as f:
    json.dump(payload,f,indent=2,sort_keys=True); f.write('\n')
PY
chmod 600 "$CHAT_HOME/runtime.json"

echo "AIrsenal runtime ready: $VENV"

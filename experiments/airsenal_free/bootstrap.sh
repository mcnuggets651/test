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
PYTHON_VERSION="$(python3 - "$PINS" <<'PY'
import json,sys
p=json.load(open(sys.argv[1], encoding='utf-8'))
print(p.get('python_version') or p.get('python_minor') or '3.12')
PY
)"

mkdir -p "$CHAT_HOME" "$CHAT_HOME/upstream" "$CHAT_HOME/uv-cache" "$CHAT_HOME/uv-python"
chmod 700 "$CHAT_HOME"

BOOTSTRAP_VENV="$CHAT_HOME/bootstrap-venv"
if [[ ! -x "$BOOTSTRAP_VENV/bin/python" ]]; then
  python3 -m venv "$BOOTSTRAP_VENV"
fi
if [[ ! -x "$BOOTSTRAP_VENV/bin/uv" ]] || [[ "$($BOOTSTRAP_VENV/bin/uv --version 2>/dev/null || true)" != "uv $UV_VERSION" ]]; then
  "$BOOTSTRAP_VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "uv==$UV_VERSION"
fi
UV="$BOOTSTRAP_VENV/bin/uv"
export UV_CACHE_DIR="$CHAT_HOME/uv-cache"
export UV_PYTHON_INSTALL_DIR="$CHAT_HOME/uv-python"

"$UV" python install "$PYTHON_VERSION"

UPSTREAM="$CHAT_HOME/upstream/AIrsenal"
if [[ ! -d "$UPSTREAM/.git" ]]; then
  git clone --filter=blob:none https://github.com/alan-turing-institute/AIrsenal.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch --quiet --force origin "$UPSTREAM_SHA"
git -C "$UPSTREAM" checkout --quiet --detach "$UPSTREAM_SHA"
git -C "$UPSTREAM" reset --quiet --hard "$UPSTREAM_SHA"
git -C "$UPSTREAM" clean -quiet -fdx

ACTUAL_SHA="$(git -C "$UPSTREAM" rev-parse HEAD)"
[[ "$ACTUAL_SHA" == "$UPSTREAM_SHA" ]] || { echo "ERROR: upstream SHA mismatch" >&2; exit 2; }
[[ -z "$(git -C "$UPSTREAM" status --porcelain=v1)" ]] || { echo "ERROR: upstream checkout dirty" >&2; exit 2; }
[[ "$(git -C "$UPSTREAM" hash-object LICENSE)" == "$LICENSE_BLOB" ]] || { echo "ERROR: upstream LICENSE pin mismatch" >&2; exit 2; }
[[ "$(git -C "$UPSTREAM" hash-object uv.lock)" == "$LOCK_BLOB" ]] || { echo "ERROR: upstream uv.lock pin mismatch" >&2; exit 2; }

VENV="$CHAT_HOME/venv"
export UV_PROJECT_ENVIRONMENT="$VENV"
"$UV" sync --project "$UPSTREAM" --frozen --no-dev

"$VENV/bin/python" - "$UPSTREAM_VERSION" "$PYTHON_VERSION" <<'PY'
import airsenal,sys
expected_version, expected_python=sys.argv[1:]
actual=getattr(airsenal,'__version__',None)
if actual != expected_version:
    raise SystemExit(f"AIrsenal version mismatch: {actual!r} != {expected_version!r}")
if not sys.version.split()[0].startswith(expected_python):
    if expected_python.count('.') != 1 or not sys.version.split()[0].startswith(expected_python + '.'):
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

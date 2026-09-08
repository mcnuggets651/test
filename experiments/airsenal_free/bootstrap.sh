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

sha256_path() {
  python3 - "$1" <<'PY'
import hashlib,sys
p=sys.argv[1]
h=hashlib.sha256()
with open(p,'rb') as f:
    for chunk in iter(lambda:f.read(1024*1024),b''):
        h.update(chunk)
print(h.hexdigest())
PY
}

distribution_digest() {
  local python_bin="$1"
  "$python_bin" - <<'PY'
import hashlib
from importlib import metadata
rows=[]
for d in metadata.distributions():
    name=(d.metadata.get('Name') or '').strip().lower()
    version=(d.version or '').strip()
    if name:
        rows.append(f"{name}=={version}")
blob='\n'.join(sorted(set(rows))).encode()
print(hashlib.sha256(blob).hexdigest())
PY
}

UPSTREAM_SHA="$(read_pin upstream_sha)"
UPSTREAM_VERSION="$(read_pin upstream_project_version)"
LICENSE_BLOB="$(read_pin upstream_license_blob_sha)"
LOCK_BLOB="$(read_pin upstream_uv_lock_blob_sha)"
UV_VERSION="$(read_pin uv_version)"
PYTHON_VERSION="$(read_pin python_version)"
PINS_SHA256="$(sha256_path "$PINS")"

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

UPSTREAM="$CHAT_HOME/upstream/AIrsenal"
VENV="$CHAT_HOME/venv"
RUNTIME_JSON="$CHAT_HOME/runtime.json"

cache_is_exact() {
  [[ -f "$RUNTIME_JSON" ]] || return 1
  [[ -d "$UPSTREAM/.git" ]] || return 1
  [[ -x "$VENV/bin/python" ]] || return 1
  python_is_exact "$VENV/bin/python" || return 1
  [[ "$(git -C "$UPSTREAM" rev-parse HEAD 2>/dev/null || true)" == "$UPSTREAM_SHA" ]] || return 1
  [[ -z "$(git -C "$UPSTREAM" status --porcelain=v1 2>/dev/null || true)" ]] || return 1
  [[ "$(git -C "$UPSTREAM" hash-object LICENSE 2>/dev/null || true)" == "$LICENSE_BLOB" ]] || return 1
  [[ "$(git -C "$UPSTREAM" hash-object uv.lock 2>/dev/null || true)" == "$LOCK_BLOB" ]] || return 1
  local current_dist_digest
  current_dist_digest="$(distribution_digest "$VENV/bin/python" 2>/dev/null || true)"
  [[ -n "$current_dist_digest" ]] || return 1
  "$VENV/bin/python" - "$RUNTIME_JSON" "$UPSTREAM_SHA" "$UPSTREAM_VERSION" "$LICENSE_BLOB" "$LOCK_BLOB" "$UV_VERSION" "$PYTHON_VERSION" "$PINS_SHA256" "$current_dist_digest" <<'PY'
import json,sys
(path,sha,version,license_blob,lock_blob,uv_version,python_version,pins_sha,dist_sha)=sys.argv[1:]
try:
    payload=json.load(open(path,encoding='utf-8'))
except Exception:
    raise SystemExit(1)
expected={
  'schema':'airsenal-chat-runtime-v2',
  'upstream_sha':sha,
  'upstream_project_version':version,
  'upstream_license_blob_sha':license_blob,
  'upstream_uv_lock_blob_sha':lock_blob,
  'uv_version':uv_version,
  'python_version_pin':python_version,
  'pins_sha256':pins_sha,
  'installed_distributions_sha256':dist_sha,
}
for k,v in expected.items():
    if payload.get(k) != v:
        raise SystemExit(1)
import airsenal
if getattr(airsenal,'__version__',None) != version:
    raise SystemExit(1)
PY
}

if cache_is_exact; then
  echo "AIrsenal cached runtime verified; no dependency rebuild required."
  exit 0
fi

echo "AIrsenal cache missing or drifted; rebuilding exact pinned runtime."
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

DIST_SHA256="$(distribution_digest "$VENV/bin/python")"
"$VENV/bin/python" - "$RUNTIME_JSON" "$UPSTREAM_SHA" "$UPSTREAM_VERSION" "$LICENSE_BLOB" "$LOCK_BLOB" "$UV_VERSION" "$PYTHON_VERSION" "$UPSTREAM" "$VENV" "$PINS_SHA256" "$DIST_SHA256" <<'PY'
import datetime as dt,json,sys
(path,sha,version,license_blob,lock_blob,uv_version,python_version,upstream,venv,pins_sha,dist_sha)=sys.argv[1:]
payload={
  "schema":"airsenal-chat-runtime-v2",
  "verified_at":dt.datetime.now(dt.timezone.utc).isoformat(),
  "upstream_sha":sha,
  "upstream_project_version":version,
  "upstream_license_blob_sha":license_blob,
  "upstream_uv_lock_blob_sha":lock_blob,
  "uv_version":uv_version,
  "python_version_pin":python_version,
  "upstream_path":upstream,
  "venv_path":venv,
  "pins_sha256":pins_sha,
  "installed_distributions_sha256":dist_sha,
}
with open(path,'w',encoding='utf-8') as f:
    json.dump(payload,f,indent=2,sort_keys=True); f.write('\n')
PY
chmod 600 "$RUNTIME_JSON"

cache_is_exact || { echo "ERROR: exact runtime failed post-build cache verification" >&2; exit 2; }
echo "AIrsenal runtime ready: $VENV"

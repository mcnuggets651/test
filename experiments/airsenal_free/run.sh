#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAT_HOME="${AIRSENAL_CHAT_HOME:-$HOME/.local/share/airsenal-chat}"
PRIVATE_REPO=""
DOCTOR_ONLY=0

args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
  case "${args[$i]}" in
    --private-repo)
      i=$((i + 1))
      [[ $i -lt ${#args[@]} ]] || { echo "ERROR: --private-repo requires a value" >&2; exit 2; }
      PRIVATE_REPO="${args[$i]}"
      ;;
    --private-repo=*) PRIVATE_REPO="${args[$i]#*=}" ;;
    --doctor-only) DOCTOR_ONLY=1 ;;
  esac
  i=$((i + 1))
done

[[ -n "$PRIVATE_REPO" ]] || { echo "ERROR: --private-repo is required" >&2; exit 2; }

python3 "$SCRIPT_DIR/doctor.py" \
  --phase pre \
  --home "$CHAT_HOME" \
  --pins "$SCRIPT_DIR/pins.json" \
  --private-repo "$PRIVATE_REPO" \
  --json-out "$CHAT_HOME/doctor-pre.json"

AIRSENAL_CHAT_HOME="$CHAT_HOME" "$SCRIPT_DIR/bootstrap.sh"

"$CHAT_HOME/venv/bin/python" "$SCRIPT_DIR/doctor.py" \
  --phase post \
  --home "$CHAT_HOME" \
  --pins "$SCRIPT_DIR/pins.json" \
  --private-repo "$PRIVATE_REPO" \
  --json-out "$CHAT_HOME/doctor-post.json"

if [[ $DOCTOR_ONLY -eq 1 ]]; then
  echo "AIrsenal doctor checks passed; model execution skipped by --doctor-only."
  exit 0
fi

filtered=()
for arg in "${args[@]}"; do
  [[ "$arg" == "--doctor-only" ]] && continue
  filtered+=("$arg")
done

exec env AIRSENAL_CHAT_HOME="$CHAT_HOME" "$CHAT_HOME/venv/bin/python" "$SCRIPT_DIR/operational.py" --home "$CHAT_HOME" "${filtered[@]}"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAT_HOME="${AIRSENAL_CHAT_HOME:-$HOME/.local/share/airsenal-chat}"
PRIVATE_REPO=""

safe_fail() {
  local failure_class="$1"
  local rc="$2"
  printf 'AIRSENAL_SAFE_FAILURE_CLASS=%s\n' "$failure_class" >&2
  exit "$rc"
}

run_guarded() {
  local failure_class="$1"
  shift
  set +e
  "$@"
  local rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    safe_fail "$failure_class" "$rc"
  fi
}

args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
  case "${args[$i]}" in
    --private-repo)
      i=$((i + 1))
      [[ $i -lt ${#args[@]} ]] || safe_fail "ARGUMENT_VALIDATION_FAILED" 2
      PRIVATE_REPO="${args[$i]}"
      ;;
    --private-repo=*) PRIVATE_REPO="${args[$i]#*=}" ;;
  esac
  i=$((i + 1))
done

[[ -n "$PRIVATE_REPO" ]] || safe_fail "ARGUMENT_VALIDATION_FAILED" 2

run_guarded "DOCTOR_PRE_FAILED" \
  python3 "$SCRIPT_DIR/doctor.py" \
    --phase pre \
    --home "$CHAT_HOME" \
    --pins "$SCRIPT_DIR/pins.json" \
    --private-repo "$PRIVATE_REPO" \
    --json-out "$CHAT_HOME/producer-doctor-pre.json"

run_guarded "BOOTSTRAP_FAILED" \
  env AIRSENAL_CHAT_HOME="$CHAT_HOME" "$SCRIPT_DIR/bootstrap.sh"

run_guarded "DOCTOR_POST_FAILED" \
  "$CHAT_HOME/venv/bin/python" "$SCRIPT_DIR/doctor.py" \
    --phase post \
    --home "$CHAT_HOME" \
    --pins "$SCRIPT_DIR/pins.json" \
    --private-repo "$PRIVATE_REPO" \
    --json-out "$CHAT_HOME/producer-doctor-post.json"

run_guarded "PRODUCER_FAILED" \
  env AIRSENAL_CHAT_HOME="$CHAT_HOME" "$CHAT_HOME/venv/bin/python" \
    "$SCRIPT_DIR/daily_producer.py" --home "$CHAT_HOME" "${args[@]}"

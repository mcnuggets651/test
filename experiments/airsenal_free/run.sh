#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAT_HOME="${AIRSENAL_CHAT_HOME:-$HOME/.local/share/airsenal-chat}"
AIRSENAL_CHAT_HOME="$CHAT_HOME" "$SCRIPT_DIR/bootstrap.sh"
exec env AIRSENAL_CHAT_HOME="$CHAT_HOME" "$CHAT_HOME/venv/bin/python" "$SCRIPT_DIR/operational.py" --home "$CHAT_HOME" "$@"

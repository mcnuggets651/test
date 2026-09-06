#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$ROOT/bootstrap.sh"

VENDOR="${DSS_VENDOR:-$ROOT/.vendor}"
SMARTPLAY_SHA="9b5bec6ae12541be24decd980e119af90617a868"
SMARTPLAY="$VENDOR/smartplayfpl-public"

if [[ ! -d "$SMARTPLAY/.git" ]]; then
  git clone https://github.com/qazybekb/smartplayfpl.git "$SMARTPLAY"
fi
git -C "$SMARTPLAY" fetch --quiet origin "$SMARTPLAY_SHA"
git -C "$SMARTPLAY" checkout --quiet --detach "$SMARTPLAY_SHA"
actual="$(git -C "$SMARTPLAY" rev-parse HEAD)"
if [[ "$actual" != "$SMARTPLAY_SHA" ]]; then
  echo "SmartPlay public mapping pin verification failed: $actual" >&2
  exit 2
fi

# Hard acceptance guard: these were the two clubs that blocked Dastan's Aug-10
# active-season mapping release. Do not silently proceed unless the refreshed public
# source now has concrete Understat IDs for both.
python - "$SMARTPLAY/data/mappings/clubs_golden_record.csv" <<'PY'
import csv, sys
path = sys.argv[1]
rows = {r['club_name']: r for r in csv.DictReader(open(path, encoding='utf-8'))}
for name in ('Coventry City', 'Hull City'):
    row = rows.get(name)
    if not row or not row.get('understat_team_id'):
        raise SystemExit(f'missing refreshed Understat mapping for {name}')
    print(f"{name}: {row['understat_name']} / {row['understat_team_id']}")
PY

echo "SmartPlay public mapping: $SMARTPLAY_SHA"

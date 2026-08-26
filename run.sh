#!/bin/bash
# Read one WaterFurnace unit over Modbus and push it to Jumboshack.
#
# Schedule it every minute with cron (crontab -e):
#   * * * * * /home/pi/jumboshack-sender/run.sh > /home/pi/jumboshack-sender/lastrun.log 2>&1
#
# Reading a second unit (its own bus/adapter)? Pass the unit number:  run.sh 2
#
# READ-ONLY: this only reads registers from your heat pump; it never writes to it.

set -euo pipefail

UNIT="${1:-1}"
SERIAL="${SERIAL:-/dev/ttyUSB0}"
DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK="/tmp/jumboshack_unit${UNIT}.lock"

# Don't let two runs fight over the serial bus — if one is still going, skip.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date '+%F %T') unit${UNIT} skipped — previous run still in progress"
  exit 0
fi

START=$(date +%s)
mkdir -p "$DIR/readings"
OUT="$DIR/readings/unit${UNIT}.json"

# 1) Read the unit over Modbus -> JSON
python3 "$DIR/fetch_modbus.py" --unit "$UNIT" --port "$SERIAL" --output "$OUT"

# 2) Push to Jumboshack (skipped if you haven't created ingest.env yet).
#    Non-fatal: a push failure spools and retries; it never aborts the run.
if [ -f "$DIR/ingest.env" ]; then
  set -a; . "$DIR/ingest.env"; set +a
  python3 "$DIR/push_reading.py" "$OUT" || true
fi

echo "$(date '+%F %T') unit${UNIT} done — $(( $(date +%s) - START ))s — $OUT"

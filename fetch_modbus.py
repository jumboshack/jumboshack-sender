#!/usr/bin/env python3
"""Read a WaterFurnace unit natively over Modbus (Python) → JSON.

Drop-in replacement for the aurora_fetch (Ruby gem) + convert.py path: it reads the
same registers directly over Modbus and emits the SAME JSON — both go through
converter.build_record(), and the output was validated field-for-field against
aurora_fetch (1570+ fields, 0 diffs beyond a live-toggling accessory-output bit).
This retires the Ruby dependency; the reader is READ-ONLY by design.

    python3 fetch_modbus.py --unit 1 --output readings/unit1.json
    python3 fetch_modbus.py --unit 2                 # prints JSON to stdout
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geothermal import modbus_reader as mr


def main():
    ap = argparse.ArgumentParser(description="Read a unit over Modbus → JSON (native Python).")
    ap.add_argument("--unit", type=int, default=1, help="label for this unit (1, 2, …)")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--output", default=None, help="write JSON here; omit to print to stdout")
    ap.add_argument("--retries", type=int, default=1,
                    help="extra whole-read attempts if the bus glitches (default 1)")
    args = ap.parse_args()

    record = None
    last_err = None
    for _ in range(args.retries + 1):
        try:
            record = mr.read_record(args.unit, port=args.port)
            break
        except Exception as e:            # transient serial/bus error — brief pause, retry
            last_err = e
            time.sleep(1.0)
    if record is None:
        sys.stderr.write(f"fetch_modbus: read failed for unit {args.unit}: {last_err}\n")
        sys.exit(1)

    json_text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json_text, encoding="utf-8")
        print(f"Written: {out}", flush=True)
    else:
        sys.stdout.write(json_text)


if __name__ == "__main__":
    main()

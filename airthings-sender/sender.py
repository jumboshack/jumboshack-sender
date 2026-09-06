"""Airthings → Jumboshack sender (self-serve).

Reads your Airthings account over the cloud API and POSTs each room's latest reading
straight to the Jumboshack product ingest gateway (POST /api/ingest/airquality) with a
per-device token. No InfluxDB, no NAS, no local hardware — runs on any always-on box
with internet (a spare Pi, a mini-PC, a NAS container).

This is the version handed to product users. It combines what the NAS setup does in two
containers (airthings-poller fetch + airquality-pusher POST) into one dependency-free
script. Writes are idempotent (keyed by each reading's own `recorded` time), so a missed
cycle simply backfills on the next one.

Required env:
  AIRTHINGS_CLIENT_ID / AIRTHINGS_CLIENT_SECRET / AIRTHINGS_ACCOUNT_ID
  INGEST_TOKEN            your Jumboshack device token (module = air quality)
Optional env:
  INGEST_URL             default https://ingest.jumboshack.com/api/ingest/airquality
  POLL_INTERVAL_SECONDS  default 300
  VALIDATE               "1" -> dry-run: POST ?validate=1 once and exit (nothing stored)
  FLOOR_MAP_JSON         {"<serial>":["Basement",3], ...} to label/sort floors
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("airthings-sender")

AT_CLIENT_ID     = os.environ.get("AIRTHINGS_CLIENT_ID", "").strip()
AT_CLIENT_SECRET = os.environ.get("AIRTHINGS_CLIENT_SECRET", "").strip()
AT_ACCOUNT_ID    = os.environ.get("AIRTHINGS_ACCOUNT_ID", "").strip()

INGEST_URL   = os.environ.get("INGEST_URL", "https://ingest.jumboshack.com/api/ingest/airquality").strip()
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "").strip()

POLL     = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
VALIDATE = os.environ.get("VALIDATE", "").lower() in ("1", "true", "yes", "on")
try:
    FLOOR_MAP = {k: tuple(v) for k, v in json.loads(os.environ.get("FLOOR_MAP_JSON", "{}")).items()}
except Exception:                                          # noqa: BLE001
    log.warning("FLOOR_MAP_JSON not valid JSON — ignoring"); FLOOR_MAP = {}

_token, _token_exp = "", 0.0


def get_token() -> str:
    global _token, _token_exp
    now = time.time()
    if _token and now < _token_exp - 60:
        return _token
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": AT_CLIENT_ID,
        "client_secret": AT_CLIENT_SECRET, "scope": "read:device:current_values",
    }).encode()
    req = urllib.request.Request("https://accounts-api.airthings.com/v1/token", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    _token, _token_exp = data["access_token"], now + data.get("expires_in", 3600)
    log.info("Airthings token refreshed (expires in %ds)", data.get("expires_in", 3600))
    return _token


def at_get(path: str) -> dict:
    req = urllib.request.Request(f"https://consumer-api.airthings.com{path}",
                                 headers={"Authorization": f"Bearer {get_token()}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _floor(device: dict, serial: str):
    """Floor label + sort order. Prefer an explicit FLOOR_MAP override, else the Airthings
    location/segment name, else 'Home'."""
    if serial in FLOOR_MAP:
        lbl, order = FLOOR_MAP[serial]
        return str(lbl), int(order)
    for key in ("location", "segment", "room"):
        obj = device.get(key)
        if isinstance(obj, dict) and obj.get("name"):
            return str(obj["name"]), 1
        if isinstance(obj, str) and obj:
            return obj, 1
    return "Home", 1


def collect(devices: dict) -> list:
    """Latest reading per room, as ingest-payload room dicts (same shape the NAS pusher sends)."""
    data, rooms = at_get(f"/v1/accounts/{AT_ACCOUNT_ID}/sensors"), []
    for sr in data.get("results", []):
        sn = sr.get("serialNumber")
        recorded = sr.get("recorded")
        if not sn or not recorded:
            continue
        device = devices.get(sn, {})
        floor, floor_order = _floor(device, sn)
        smap = {s["sensorType"]: s["value"] for s in sr.get("sensors", [])}
        room = {"serial": sn, "name": device.get("name", sn), "floor": floor,
                "floor_order": floor_order, "device_type": device.get("type", "UNKNOWN"),
                "recorded": recorded}
        if smap.get("radonShortTermAvg") is not None:
            bq = float(smap["radonShortTermAvg"])
            room["radon_bq"], room["radon_pci"] = bq, round(bq / 37.0, 3)
        if smap.get("temp") is not None:
            room["temp_f"] = round(float(smap["temp"]) * 9 / 5 + 32, 2)
        for src, dst in (("humidity", "humidity_pct"), ("co2", "co2_ppm"), ("voc", "voc_ppb"),
                         ("pressure", "pressure_mbar"), ("pm1", "pm1_ugm3"), ("pm25", "pm25_ugm3")):
            if smap.get(src) is not None:
                room[dst] = float(smap[src])
        if sr.get("batteryPercentage") is not None:
            room["battery_pct"] = float(sr["batteryPercentage"])
        # need at least one measurement field beyond the identity keys
        if len(room) > 6:
            rooms.append(room)
    return rooms


def push(rooms: list, validate: bool = False) -> bool:
    url = INGEST_URL + ("?validate=1" if validate else "")
    payload = json.dumps({
        "metadata": {"time": datetime.now(timezone.utc).isoformat(), "software_version": "airthings-sender/1.0"},
        "rooms": rooms,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": f"Bearer {INGEST_TOKEN}", "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read() or b"{}")
                log.info("%s %d rooms — %s", "validated" if validate else "pushed", len(rooms), body)
                return True
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            log.warning("push attempt %d failed: HTTP %s — %s", attempt + 1, exc.code, detail)
            if exc.code in (401, 403, 404):                # bad token / wrong module — retrying won't help
                return False
        except Exception as exc:                           # noqa: BLE001
            log.warning("push attempt %d failed: %s", attempt + 1, exc)
        time.sleep(2 * (attempt + 1))
    return False


def _cycle() -> None:
    dev_data = at_get(f"/v1/accounts/{AT_ACCOUNT_ID}/devices")
    devices = {d["serialNumber"]: d for d in dev_data.get("devices", [])}
    rooms = collect(devices)
    if not rooms:
        log.info("no readings from Airthings this cycle"); return
    push(rooms)


def main():
    missing = [n for n, v in (("AIRTHINGS_CLIENT_ID", AT_CLIENT_ID), ("AIRTHINGS_CLIENT_SECRET", AT_CLIENT_SECRET),
               ("AIRTHINGS_ACCOUNT_ID", AT_ACCOUNT_ID), ("INGEST_TOKEN", INGEST_TOKEN)) if not v]
    if missing:
        log.error("missing required env: %s — set them and restart.", ", ".join(missing))
        while True:
            time.sleep(3600)

    if VALIDATE:
        log.info("VALIDATE=1 — dry run against %s (nothing will be stored)", INGEST_URL)
        dev_data = at_get(f"/v1/accounts/{AT_ACCOUNT_ID}/devices")
        devices = {d["serialNumber"]: d for d in dev_data.get("devices", [])}
        rooms = collect(devices)
        log.info("collected %d rooms from Airthings", len(rooms))
        ok = push(rooms, validate=True) if rooms else False
        log.info("dry run %s", "OK ✓" if ok else "FAILED ✗ — check the log above")
        return

    log.info("airthings-sender starting  interval=%ds  -> %s", POLL, INGEST_URL)
    while True:
        try:
            _cycle()
        except Exception as exc:                           # noqa: BLE001
            log.error("cycle failed: %s", exc)
        time.sleep(POLL)


if __name__ == "__main__":
    main()

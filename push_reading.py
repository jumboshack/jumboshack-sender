#!/usr/bin/env python3
"""Push a Jumboshack JSON reading to one or more ingest targets, with per-target
store-and-forward buffering.

Non-fatal by design: a target that's down spools to its own `spool/<target>/` and
retries on the next run, so one slow/dead endpoint never blocks the others and a
network blip never costs you a reading.

Config via env (set in a gitignored ingest.env - see ingest.env.example):

  Single target (the usual case):
    INGEST_TARGETS=cloud
    INGEST_URL_cloud=https://ingest.jumboshack.com/api/ingest/geothermal
    INGEST_TOKEN_cloud=jsk_your_device_token

  Or the short form:
    INGEST_URL=https://ingest.jumboshack.com/api/ingest/geothermal
    INGEST_TOKEN=jsk_your_device_token

  Add more targets (e.g. a local server) by listing them in INGEST_TARGETS and
  giving each its own INGEST_URL_<name> / INGEST_TOKEN_<name>.

  Optional:
    INGEST_SPOOL      spool dir (default: ./spool next to this script)
    INGEST_SPOOL_MAX  cap per target, oldest dropped beyond it (default 2000)
    INGEST_TIMEOUT    per-request seconds (default 10)

Usage: push_reading.py <path-to-reading.json>
"""

import glob
import os
import shutil
import sys
import urllib.request

SPOOL_BASE = os.environ.get("INGEST_SPOOL",
                            os.path.join(os.path.dirname(os.path.abspath(__file__)), "spool"))
SPOOL_MAX = int(os.environ.get("INGEST_SPOOL_MAX", "2000"))
TIMEOUT = int(os.environ.get("INGEST_TIMEOUT", "10"))


def _targets():
    """Return [(name, url, token), ...] from env. Multi-target if INGEST_TARGETS is
    set, else the single INGEST_URL/INGEST_TOKEN as an implicit 'default'."""
    names = [n.strip() for n in os.environ.get("INGEST_TARGETS", "").split(",") if n.strip()]
    out = []
    if names:
        for n in names:
            url = os.environ.get(f"INGEST_URL_{n}")
            tok = os.environ.get(f"INGEST_TOKEN_{n}", "")
            if url and tok:
                out.append((n, url, tok))
    else:
        url = os.environ.get("INGEST_URL")
        tok = os.environ.get("INGEST_TOKEN", "")
        if url and tok:
            out.append(("default", url, tok))
    return out


def _post(url: str, token: str, path: str) -> bool:
    """POST one reading to one target. Returns True on 2xx, False otherwise (never raises)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _spool_add(spool_dir: str, path: str) -> None:
    """Copy a failed reading into a target's spool, enforcing the cap (drop oldest)."""
    try:
        os.makedirs(spool_dir, exist_ok=True)
        existing = sorted(glob.glob(os.path.join(spool_dir, "*.json")))
        while len(existing) >= SPOOL_MAX:
            try:
                os.remove(existing.pop(0))
            except OSError:
                break
        dest = os.path.join(spool_dir, os.path.basename(path))
        if not os.path.exists(dest):
            shutil.copyfile(path, dest)
    except OSError:
        pass  # spooling is best-effort; never break the cron


def _drain(url: str, token: str, spool_dir: str) -> bool:
    """Send a target's spooled readings oldest-first. Stop on the first failure
    (retry next run). Returns True if the spool is empty/clear afterward."""
    if not os.path.isdir(spool_dir):
        return True
    for p in sorted(glob.glob(os.path.join(spool_dir, "*.json"))):
        if _post(url, token, p):
            try:
                os.remove(p)
            except OSError:
                pass
        else:
            return False  # target still down — leave the rest for next run
    return True


def main() -> None:
    targets = _targets()
    if not targets or len(sys.argv) < 2:
        return  # not configured, or no reading given — do nothing
    reading = sys.argv[1]
    for name, url, token in targets:
        spool_dir = os.path.join(SPOOL_BASE, name)
        _drain(url, token, spool_dir)      # clear this target's backlog first (keeps order)
        if not _post(url, token, reading):  # then the fresh reading
            _spool_add(spool_dir, reading)


if __name__ == "__main__":
    main()

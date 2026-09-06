# airthings-sender

Streams your **Airthings** air-quality readings into your Jumboshack dashboard. It reads
the Airthings cloud API and POSTs each room's latest reading to the Jumboshack ingest
gateway with your device token — no InfluxDB, no NAS, no local hardware. Runs on any
always-on box with internet (a spare Raspberry Pi, a mini-PC, or a container on your NAS).

> Works with **WiFi Airthings** devices (View Plus, View Radon, View Pollution, or a
> Wave series paired with a **Hub**). Bluetooth-only units (e.g. Corentium Home) can't be
> read by the API and won't work.

## 1. Get Airthings API access
1. Sign in at **dashboard.airthings.com** → **Integrations** → **API**.
2. Create an **API client** using **client credentials**. Give it the scope
   `read:device:current_values`.
3. Copy the **client id**, **client secret**, and your **account id**.

## 2. Create a Jumboshack device
On **app.jumboshack.com/account**, add a device with module **air quality**. Copy the
device **token** (shown once).

## 3. Configure + run
```bash
cp .env.example .env      # then fill in the five values

# Optional first-run sanity check — validates against the server, stores nothing:
docker build -t airthings-sender .
docker run --rm --env-file .env -e VALIDATE=1 airthings-sender

# Start streaming (every 5 min):
docker run -d --restart unless-stopped --name airthings-sender --env-file .env airthings-sender
```

Or with the bundled compose file: `docker compose up -d`.

Then open **app.jumboshack.com** → **Air Quality ▸ Now** — your rooms appear within a
few minutes. Writes are idempotent (keyed by each reading's own timestamp), so a missed
cycle just backfills on the next one.

## Environment
| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `AIRTHINGS_CLIENT_ID` | ✓ | | from the Airthings API client |
| `AIRTHINGS_CLIENT_SECRET` | ✓ | | from the Airthings API client |
| `AIRTHINGS_ACCOUNT_ID` | ✓ | | your Airthings account id |
| `INGEST_TOKEN` | ✓ | | your Jumboshack device token (module = air quality) |
| `INGEST_URL` | | `https://ingest.jumboshack.com/api/ingest/airquality` | |
| `POLL_INTERVAL_SECONDS` | | `300` | Airthings updates every few minutes |
| `VALIDATE` | | | `1` = dry run (validate + exit) |
| `FLOOR_MAP_JSON` | | `{}` | `{"<serial>":["Basement",3]}` to label/sort floors |

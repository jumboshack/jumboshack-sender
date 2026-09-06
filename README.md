# Jumboshack Sender

Stream your **WaterFurnace geothermal heat pump** into your [Jumboshack](https://jumboshack.com)
dashboard from a Raspberry Pi. It reads the Aurora control over its Modbus bus once a minute and
pushes each reading to Jumboshack over HTTPS.

- **Pure Python**, one dependency (`minimalmodbus`) — no Ruby, no compilers.
- **Fast & light** — pulls a full ~1,500-register snapshot in ~5 seconds (about 4.5× faster than
  the reference tooling), so it's easy on your Pi and barely touches the control bus.
- **Read-only by design** — it only ever *reads* registers. There are intentionally no write
  functions anywhere in this code; it can never command your equipment.

📖 Prefer a full illustrated walkthrough (parts list, wiring photos)? See the
**[Build Guide](https://jumboshack.com/build)**. Streaming from something other than a WaterFurnace?
Skip this and POST your own JSON — see the **[API docs](https://app.jumboshack.com/ingest-docs)**.

> **Tracking air quality instead?** The [`airthings-sender/`](airthings-sender/) folder is a separate,
> dependency-free sender that streams a WiFi Airthings monitor into Jumboshack over the Airthings cloud
> API — no Pi or wiring needed. See its [README](airthings-sender/README.md) and the
> [air-quality setup guide](https://jumboshack.com/air-build.html).

## What you need

- A Raspberry Pi (a Pi Zero 2 W is plenty) on your network
- A USB-to-RS485 adapter (FTDI-based recommended) wired to your Aurora Modbus bus
  (`A→D+`, `B→D−`, `GND→ground`) — it shows up as `/dev/ttyUSB0`
- Python 3.9+

## Install

```bash
sudo apt update && sudo apt install -y python3 python3-pip
git clone https://github.com/jumboshack/jumboshack-sender.git ~/jumboshack-sender
cd ~/jumboshack-sender
pip3 install -r requirements.txt
```

## 1. Test the wiring

Read your unit once and print it:

```bash
python3 fetch_modbus.py --unit 1
```

If that prints your heat pump's temperatures instead of an error, your wiring is good. 🎉
(No `/dev/ttyUSB0`? Check the adapter. Timeout/garbage? Usually A/B are swapped — flip the two data wires.)

## 2. Get a device token

In your Jumboshack [Account](https://app.jumboshack.com/account), add a **Location**, then a
**Device**. You'll get a one-time token that looks like `jsk_…` — copy it now; it's shown only once.

## 3. Point the sender at your account

```bash
cp ingest.env.example ingest.env
# edit ingest.env and paste your jsk_… token
```

Dry-run it — this checks your payload but stores nothing (`"valid": true` means you're good):

```bash
curl -X POST "https://ingest.jumboshack.com/api/ingest/geothermal?validate=1" \
  -H "Authorization: Bearer jsk_your_device_token" \
  -H "Content-Type: application/json" \
  --data @readings/unit1.json
```

## 4. Run it every minute

```bash
crontab -e
```

Add:

```
* * * * * /home/pi/jumboshack-sender/run.sh > /home/pi/jumboshack-sender/lastrun.log 2>&1
```

Within a minute your dashboard lights up — live temperatures, power, pressures, flow, and a
schematic of your loop. History and cost fill in automatically from there.

Reading a second unit (on its own bus/adapter)? Run `run.sh 2`, and add a second cron line for it.

## How it works

```
WaterFurnace Aurora ──(RS-485 / Modbus)── USB adapter ── Raspberry Pi
                                                            │
                                  fetch_modbus.py (native Python) → push (HTTPS)
                                                            │
                                            ingest.jumboshack.com → your dashboard
```

- `fetch_modbus.py` — reads the registers over Modbus and writes a JSON reading.
- `push_reading.py` — posts it to Jumboshack, with store-and-forward buffering so a network blip
  never costs you a reading.
- `run.sh` — ties them together under a lock; schedule it from cron.

## Safety

Jumboshack is a **monitoring** product. This sender is strictly read-only — `geothermal/modbus_client.py`
has no write functions by design. It will never change a setting or command your equipment.

## License

MIT — see [LICENSE](LICENSE). The WaterFurnace Aurora register decoders are ported from the
MIT-licensed [`waterfurnace_aurora`](https://github.com/ccutrer/waterfurnace_aurora) gem by
Cody Cutrer; see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

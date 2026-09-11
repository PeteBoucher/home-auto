# home-auto

A local-first home automation dashboard running on a Raspberry Pi. Controls smart lights, sockets, and media devices over the local network — no cloud dependency for daily use.

## Supported devices

| Integration | Transport | Device classes |
| --- | --- | --- |
| Tuya | `tinytuya` — direct LAN (protocol v3.3 / v3.5) | Smart bulbs (RGB + tunable white), smart plugs |
| Zigbee2MQTT | MQTT via local Mosquitto broker | Bulbs, plugs (incl. power metering), in-wall switch relays, temperature/humidity sensors |
| hOn | `pyhOn` — Haier cloud API (needs internet) | Haier air conditioner: power, mode, target temp, fan speed, louvre, quiet |
| Fire TV | `androidtv` — ADB over LAN (feature-flagged, off by default) | Amazon Fire TV — playback-state monitoring |

Any Zigbee device supported by Zigbee2MQTT should work for its device class; import it via `/devices/z2m` and pick the matching type.

### Tested hardware

The specific models running in the live deployment:

| Device | Model | Integration | Notes |
| --- | --- | --- | --- |
| Air conditioner | Haier AS35RBAHRA-4 | hOn | Full control. "Eco" isn't exposed usefully by the cloud API — see `app/devices/hon.py`. |
| Smart bulb | Lidl / Silvercrest (Tuya) | Tuya LAN | RGB + white + brightness |
| Smart bulb | Innr RB 282 C — E27 RGBW | Zigbee2MQTT | |
| Smart plug | Lidl / Silvercrest HG06337 | Zigbee2MQTT | On/off |
| Smart plug | Sonoff S60ZBTPF | Zigbee2MQTT | On/off + voltage / power / current / energy metering |
| Smart switch (in-wall relay) | Sonoff ZBMINIR2 | Zigbee2MQTT | Wired behind an existing physical switch — controls the light without cutting its power, so it stays always reachable. On/off only, no metering. |
| Temp + humidity sensor | Sonoff SNZB-02 | Zigbee2MQTT | Basic indoor sensor |
| Temp + humidity sensor | Sonoff SNZB-02DR2 | Zigbee2MQTT | E-ink screen with a secondary "EXT1" field that can mirror another sensor |
| Outdoor temp + humidity sensor | Sonoff SNZB-02WD | Zigbee2MQTT | IP65-rated, wide range, for exterior mounting |

**Partially working / blocked:**

- **Haier smart TV (H50S80GUX)** — pairs via hOn, but `pyhOn`'s generic command builder produces payloads Haier's cloud accepts and the TV then ignores. Shelved pending a traffic capture of the official app; TV control code is not shipped.
- **Fire TV Stick 4K Select** — runs Amazon's Vega OS, which exposes no ADB. The ADB monitoring below only works on Android-based Fire OS devices.

## Features

### Dashboard

- Live device cards with on/off toggle, brightness, colour temperature, and RGB colour picker for bulbs
- Air conditioner card: power, mode, target temp, fan speed, a louvre-position diagram, quiet toggle, plus live indoor/outdoor temperature
- Temperature and humidity sensor cards showing live readings, battery level, and a link to the climate history chart
- Smart-plug cards showing power draw, and a link to the power/energy history charts
- [Climate overview widget](#climate-overview) at the top of the dashboard — every room's temperature and humidity on one chart
- [Device groups](#device-groups) — control several lights as one, kept in sync
- Inline device rename and room assignment
- Served from local DB cache — loads instantly; HTMX auto-refreshes device state every 30 seconds
- All JS/CSS assets bundled locally — dashboard works fully on LAN with no internet connection
- [RoachCam](#roachcam) live MJPEG feed embedded when configured
- `/history` — event log of automation firings and errors; `/network` — LAN device map with WAN reachability check

### Evening timer

Each device card has a **Timer** section. Set an on-time and off-time; the schedule is stored in SQLite and loaded into APScheduler on startup so it survives restarts. The enabled checkbox lets you suspend a schedule without deleting it.

### Automation rule engine

Create rules at `/automations` with time or device-state triggers and cross-device actions. Actions can target any integration, including the A/C.

**Trigger types:**

| Type | Example |
| --- | --- |
| Time of day | Fire at 22:30 every day |
| Sunrise / sunset | Fire 30 minutes after sunset (sun times from Open-Meteo, with an offline `astral` fallback) |
| Device state | When Zigbee plug turns on; when the A/C's outdoor temp drops below 20° |
| Fire TV media state | When Fire TV starts playing |
| Fire TV app ID | When Netflix launches |

**Device-state trigger fields:** `state`, `brightness`, `online`, `temperature` (A/C target), `indoor_temp`, `outdoor_temp`, `media_state`, `app_id`.

**Operators:** `=`, `≠`, `>`, `<`, and `within ± of` — compares two live fields on the same device (e.g. "outdoor temp within 1° of the target temp").

**Time window (device-state triggers only):** optionally restrict a rule to a start–end window ("only 18:00–23:00"). Leaving the window resets the edge, so re-entering it with the condition still true re-arms a fresh fire. Overnight spans (`22:00`–`06:00`) work.

**Actions:** turn on/off, set brightness, set colour temperature, set RGB colour.

State triggers are edge-detected — the rule fires once on the false→true transition, not on every poll.

### Device groups

Create a group at `/groups` from any mix of Zigbee and Tuya lights. A group card on the dashboard controls all members at once (on/off, brightness, colour). Zigbee members are mirrored into a real Zigbee2MQTT group, so a group command is a single native groupcast rather than one message per bulb; non-Zigbee members are commanded alongside it.

Members stay in sync: a confirmed state change on any member — from the dashboard, an automation, a poll, or a physical switch — pulls the others to match.

Commanding one member from its own card (for task lighting) marks it **Independent** and detaches it from group sync until the group itself is next commanded, which re-syncs everyone.

### Climate overview

A widget at the top of the dashboard charts temperature and humidity for every room on one graph — temperature on the left axis (solid lines), humidity on the right (dashed), each room in its own colour. The A/C's own indoor and outdoor sensors appear as extra lines. Readings from multiple sensors in the same room are averaged into one line. 1h / 6h / 24h / 7d windows, auto-refreshing on the dashboard's 30-second cadence.

### Air conditioner

The Haier A/C card exposes power, mode (auto / cool / heat / dry / fan), target temperature, fan speed, vertical louvre position (shown as a small diagram), and a quiet toggle. It also shows the unit's own indoor and outdoor temperature readings, and a **Chart** link plots target / indoor / outdoor temperature over time.

The integration talks to Haier's hOn cloud via `pyhOn`, so it needs internet. Set `HON_EMAIL` / `HON_PASSWORD` in `.env`. Several non-obvious quirks of the cloud API are documented in `app/devices/hon.py` and `.claude/memory/project.md`.

### Power & energy history

Smart plugs that report metering (e.g. Sonoff S60ZBTPF) log voltage, power, and current to `PowerSample` on every report (pruned after 7 days). The plug's **Chart** link shows those as time-series with 1h / 6h / 24h / 7d windows, plus energy-by-day and energy-by-calendar-month bar charts backed by an `EnergyDailySummary` table kept indefinitely.

### Temperature and humidity sensors

Zigbee sensors (tested with Sonoff SNZB-02, SNZB-02DR2, SNZB-02WD) are registered via the Z2M import page (`/devices/z2m`). Select **Sensor** as the device type when importing.

The dashboard card shows live temperature, humidity, and battery level. A **Chart** link opens a history page with 1h / 6h / 24h / 7d lookback windows.

Readings are stored in `ClimateSample` on every report from the sensor. On app restart, the last known values are seeded from Zigbee2MQTT's `state.json` so the card shows data immediately rather than waiting up to an hour for the next natural sensor report.

**Secondary display — SNZB-02DR2 only:** this specific model has an e-ink screen with a smaller "EXT1" field alongside its own reading (the SNZB-02WD and plain SNZB-02 only ever show their own reading). When a sensor reports the capability, its card gains a **Screen EXT1 field** dropdown that feeds that field from another sensor — e.g. the outdoor SNZB-02WD's temperature shown on the indoor DR2. The device's own reading always stays primary. (Humidity mirroring depends on firmware; the current unit rejects it.)

### Weather automation

Polls [Open-Meteo](https://open-meteo.com/) every 10 minutes for the configured location. When it's raining (WMO codes 51–99), all Tuya bulbs switch to pale blue (`#add8e6`). When rain clears, they restore to their previous state (mode, colour, brightness, and colour temperature). Configure location via `.env`:

```env
LAT=<your latitude>
LON=<your longitude>
```

### Red Alert

A RED ALERT button in the nav flashes all RGB bulbs bright red at ~1 Hz — Tuya bulbs via a persistent LAN socket per bulb (no reconnect overhead per flash), Zigbee bulbs via `/set` with `transition: 0`. Stand Down restores the pre-alert state. Auto-cancels after 60 seconds. The dashboard auto-poll and device-state automations are suppressed during the alert so cards don't flicker and rules don't fire on every flash.

### RoachCam

Embeds a live MJPEG stream from a [RoachCam](https://github.com/PeteBoucher/roachcam) Pi on the dashboard. Set `ROACHCAM_URL` in `.env` to enable the Camera section:

```env
ROACHCAM_URL=http://roachcam.local:8080
```

### Fire TV

Polls an Amazon Fire TV Stick every 5 seconds over ADB and exposes playback state as automation triggers. The device card shows current media state (playing / paused / idle / standby / off) and the active app.

**Setup:**

1. On the Fire TV: Settings → My Fire TV → Developer Options → enable **ADB Debugging** and **Network ADB**
2. Note the Fire TV IP (Settings → My Fire TV → About → Network)
3. Add to `.env`:

   ```env
   FIRETV_HOST=<fire-tv-ip>
   ```

4. Restart the service — it generates an ADB key (`firetv.adbkey`) and attempts to connect
5. **A prompt appears on the TV screen** — accept "Allow ADB Debugging"
6. The Fire TV card appears on the dashboard and is available as an automation trigger

**Automation example** — dim lights when playback starts:

| Field | Value |
| --- | --- |
| Trigger type | Device state changes |
| Trigger device | Fire TV |
| Field | Media state |
| Operator | = |
| Value | Playing |
| Action | Set brightness 20% |

**Available trigger fields:**

| Field | Values |
| --- | --- |
| `media_state` | `playing`, `paused`, `idle`, `standby`, `off` |
| `app_id` | e.g. `com.netflix.ninja`, `com.amazon.firetv.launcher` |

## Stack

- **FastAPI** + **Jinja2** + **HTMX** — server-rendered UI with partial HTML swaps
- **SQLModel** + **SQLite** (WAL mode) — device, group, schedule, automation, and time-series persistence
- **Chart.js** (bundled) — climate, A/C, and power history charts
- **tinytuya** — Tuya LAN protocol (v3.3 and v3.5)
- **aiomqtt** — Zigbee2MQTT bridge
- **pyhOn** — Haier hOn cloud API
- **androidtv** — Fire TV ADB polling
- **APScheduler 3.x** — timers, weather polling, Tuya/hOn state polling, and time-based automations
- **httpx** + **astral** — Open-Meteo requests, with offline sunrise/sunset fallback

## Raspberry Pi deployment

### Prerequisites

- Raspberry Pi running Raspberry Pi OS Lite
- Sonoff CC2652P Zigbee dongle on `/dev/ttyUSB0`
- Mosquitto MQTT broker
- Zigbee2MQTT

Run the setup script (idempotent):

```bash
curl -fsSL https://raw.githubusercontent.com/PeteBoucher/home-auto/main/deploy/setup.sh | bash
```

This installs Python, Node, Zigbee2MQTT, Mosquitto, nginx, and the two systemd services.

### Services

```
home-auto.service       # uvicorn on port 8000, proxied by nginx on port 80
zigbee2mqtt.service     # Z2M with 3s startup delay for USB init
```

```bash
sudo systemctl status home-auto
sudo journalctl -u home-auto -f
```

### Configuration

Create `/opt/home-auto/.env`:

```env
LAT=<your latitude>
LON=<your longitude>

# Haier A/C (hOn cloud) — needed for the air conditioner card
HON_EMAIL=<your hon account email>
HON_PASSWORD=<your hon account password>

# Optional integrations
ROACHCAM_URL=http://roachcam.local:8080
FIRETV_HOST=<fire-tv-ip>
FIRETV_ENABLED=false   # set to true to enable ADB polling and remote control buttons
```

The A/C integration is skipped silently if `HON_EMAIL` / `HON_PASSWORD` are unset.

### Deployment pipeline

Pushing to `main` automatically tests and deploys via a GitHub Actions self-hosted runner installed on the Pi:

1. Runner picks up the job immediately on push
2. Checks out the branch and runs `pytest` inside `/opt/home-auto/.venv`
3. If tests pass: `git fetch + reset --hard` to `/opt/home-auto`, then `systemctl restart home-auto`
4. If tests fail: the service is not touched

View run history at `github.com/PeteBoucher/home-auto/actions`.

**Runner setup** (already done — for reference if rebuilding the Pi):

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -sL https://github.com/actions/runner/releases/download/v2.335.1/actions-runner-linux-arm64-2.335.1.tar.gz | tar xz
./config.sh --url https://github.com/PeteBoucher/home-auto --token <runner-token> --name homeauto-pi --unattended
sudo ./svc.sh install pete && sudo ./svc.sh start
echo 'pete ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart home-auto' | sudo tee /etc/sudoers.d/home-auto-deploy
sudo chmod 440 /etc/sudoers.d/home-auto-deploy
```

Get a fresh `<runner-token>` from GitHub → repo Settings → Actions → Runners → New self-hosted runner.

**Emergency manual deploy** (bypasses pipeline — Pi needs internet access to `git fetch`):

```bash
ssh pete@homeauto.local "cd /opt/home-auto && git fetch origin main && git reset --hard origin/main && sudo systemctl restart home-auto"
```

**Manual deploy without internet** (rsync from your Mac when the Pi has no WAN):

```bash
rsync -avz app/ pete@homeauto.local:/opt/home-auto/app/
ssh pete@homeauto.local "sudo systemctl restart home-auto"
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload
```

## Notes on smart bulbs and physical switches

Smart bulbs need constant power to receive commands. If a physical switch cuts power to the bulb, it goes offline and can't be controlled until power is restored — at which point it comes back on according to its `power_on_behavior` setting (the Innr RB 282 C bulbs are set to `previous`, so they restore their last on-state). A brief power blip can therefore switch a bulb back on by itself; the 30-second dashboard auto-poll reflects the change within half a minute.

The proper fix is to wire a smart relay (e.g. Sonoff ZBMINIR2) behind the existing switch so it sends a Zigbee command without cutting power, keeping the bulb always controllable. Import it via `/devices/z2m` as a **Smart Switch** — a bare on/off card with no brightness/colour/metering, same idea as a plug but for a permanently-wired circuit rather than something you plug in.

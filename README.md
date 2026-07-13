# home-auto

A local-first home automation dashboard running on a Raspberry Pi. Controls smart lights, sockets, and media devices over the local network — no cloud dependency for daily use.

## Supported devices

| Integration | Protocol | Devices |
|---|---|---|
| Tuya LAN | `tinytuya` (direct LAN, v3.3/v3.5) | Smart bulbs (RGB + white), smart plugs |
| Zigbee2MQTT | MQTT over local broker | Zigbee sockets, bulbs |
| hOn | pyhOn cloud API | Haier A/C (experimental) |
| Fire TV | ADB over network | Amazon Fire TV Stick (monitoring) |

## Features

### Dashboard

- Live device cards with on/off toggle, brightness, colour temperature, and RGB colour picker for bulbs
- Inline device rename
- Served from local DB cache — loads instantly; HTMX auto-refreshes device state every 30 seconds
- [RoachCam](#roachcam) live MJPEG feed embedded when configured

### Evening timer

Each device card has a **Timer** section. Set an on-time and off-time; the schedule is stored in SQLite and loaded into APScheduler on startup so it survives restarts. The enabled checkbox lets you suspend a schedule without deleting it.

### Automation rule engine

Create rules at `/automations` with time or device-state triggers and cross-device actions.

**Trigger types:**

| Type | Example |
|---|---|
| Time of day | Fire at 22:30 every day |
| Device state | When Zigbee plug turns on |
| Fire TV media state | When Fire TV starts playing |
| Fire TV app ID | When Netflix launches |

**Actions:** turn on/off, set brightness, set colour temperature, set RGB colour.

State triggers are edge-detected — the rule fires once on the False→True transition, not on every poll.

### Weather automation

Polls [Open-Meteo](https://open-meteo.com/) every 10 minutes for the configured location. When it's raining (WMO codes 51–99), all Tuya bulbs switch to pale blue (`#add8e6`). When rain clears, they restore to their previous state (mode, colour, brightness, and colour temperature). Configure location via `.env`:

```
LAT=<your latitude>
LON=<your longitude>
```

### Red Alert

A RED ALERT button in the nav flashes all RGB bulbs bright red at ~1 Hz using a persistent LAN socket per bulb (no reconnect overhead per flash). Stand Down restores the pre-alert state. Auto-cancels after 60 seconds. The dashboard auto-poll is suppressed during the alert so cards don't flicker.

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

- **FastAPI** + **HTMX** — server-rendered UI with partial HTML swaps
- **SQLModel** + **SQLite** — device, schedule, and automation persistence
- **tinytuya** — Tuya LAN protocol (v3.3 and v3.5)
- **aiomqtt** — Zigbee2MQTT bridge
- **androidtv** — Fire TV ADB polling
- **APScheduler 3.x** — evening timers, weather polling, and time-based automations
- **httpx** — async Open-Meteo requests

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

# Optional integrations
ROACHCAM_URL=http://roachcam.local:8080
FIRETV_HOST=<fire-tv-ip>
```

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

**Emergency manual deploy** (bypasses pipeline):

```bash
ssh pete@homeauto.local "cd /opt/home-auto && git fetch origin main && git reset --hard origin/main && sudo systemctl restart home-auto"
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload
```

## Notes on smart bulbs and physical switches

Smart bulbs need constant power to receive commands. If a physical switch cuts power to the bulb, it goes offline and can't be controlled until power is restored — at which point it typically powers on at full white brightness regardless of the app's last command. The 30-second dashboard auto-poll will reflect the change within half a minute.

The proper fix is to wire a smart relay (e.g. Sonoff ZBMINI) behind the existing switch so it sends a Zigbee command without cutting power, keeping the bulb always controllable.

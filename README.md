# home-auto

A local-first home automation dashboard running on a Raspberry Pi. Controls smart lights and sockets over the local network — no cloud dependency for daily use.

## Supported devices

| Integration | Protocol | Devices |
|---|---|---|
| Tuya LAN | `tinytuya` (direct LAN, v3.3/v3.5) | Smart bulbs (RGB + white), smart plugs |
| Zigbee2MQTT | MQTT over local broker | Zigbee sockets, bulbs |
| hOn | pyhOn cloud API | Haier A/C (experimental) |

## Features

### Dashboard

- Live device cards with on/off toggle, brightness, colour temperature, and RGB colour picker for bulbs
- Inline device rename
- Auto-refreshes every 30 seconds so physical switch use (power-cycling a bulb) is reflected within half a minute

### Evening timer

Each device card has a **Timer** section. Set an on-time and off-time; the schedule is stored in SQLite and loaded into APScheduler on startup so it survives restarts. The enabled checkbox lets you suspend a schedule without deleting it.

### Weather automation

Polls [Open-Meteo](https://open-meteo.com/) every 10 minutes for the configured location. When it's raining (WMO codes 51–99), all Tuya bulbs switch to pale blue (`#add8e6`). When rain clears, they restore to their previous state (mode, colour, brightness, and colour temperature). Configure location via `.env`:

```
LAT=<your latitude>
LON=<your longitude>
```

### Red Alert

A RED ALERT button in the nav flashes all RGB bulbs bright red at ~1 Hz using a persistent LAN socket per bulb (no reconnect overhead per flash). Stand Down restores the pre-alert state. Auto-cancels after 60 seconds. The dashboard auto-poll is suppressed during the alert so cards don't flicker.

## Stack

- **FastAPI** + **HTMX** — server-rendered UI with partial HTML swaps
- **SQLModel** + **SQLite** — device and schedule persistence
- **tinytuya** — Tuya LAN protocol (v3.3 and v3.5)
- **aiomqtt** — Zigbee2MQTT bridge
- **APScheduler 3.x** — evening timers and weather polling
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
LAT=36.44          # location for rain detection
LON=-5.27
```

### Updating

```bash
cd /opt/home-auto
sudo git pull
sudo .venv/bin/pip install -e .
sudo systemctl restart home-auto
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

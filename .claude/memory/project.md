# home-auto: Project Objectives

A Python-based web app to unify control and automation of home devices currently managed across separate vendor apps.

## Devices

| Device | App | Integration |
| --- | --- | --- |
| Lidl smart plug | Lidl Home | `tinytuya` — local LAN (Tuya protocol) |
| Lidl smart bulb | Lidl Home | `tinytuya` — local LAN (Tuya protocol) |
| Haier A/C unit | hOn | `pyhOn` — Haier cloud API |
| Zigbee socket / bulb | — | Zigbee2MQTT + Mosquitto |
| Amazon Fire TV Stick | — | ADB over network (`androidtv`) |

## Goals

1. **Unified dashboard** — see all device states in one place, no switching between apps.
2. **Direct control** — toggle plug/bulb on/off, adjust bulb brightness, control A/C (power, mode, temperature, fan speed).
3. **Cross-device automations** — a rule engine supporting time-based triggers and device-state triggers with conditional actions (e.g. "turn off plug at 23:00", "if Fire TV starts playing, dim the bulb").
4. **Event history** — a log of automation firings and errors.

## Stack

- **FastAPI** — async web framework
- **tinytuya** — local LAN control for Tuya/Lidl devices
- **pyhOn** — Haier hOn cloud API
- **androidtv** — Fire TV ADB polling
- **APScheduler** — time-based automation jobs and weather polling
- **SQLite + SQLModel** — device registry, automation rules, schedules, event log
- **Jinja2 + HTMX** — server-rendered UI with live partial updates
- **Zigbee2MQTT + Mosquitto** — MQTT broker for Zigbee devices

## Phased Plan

| Phase | Description | Status |
| --- | --- | --- |
| 1 | Tuya device discovery, registration, toggle/brightness/RGB control via dashboard | ✅ Done |
| 2 | hOn / Haier A/C card (power, mode, temp, fan speed) | ⚠️ Code complete — blocked on Pi by `pyhOn` version `country` kwarg mismatch |
| 3 | Zigbee2MQTT integration (Mosquitto broker, Zigbee bulb + socket adapter) | ✅ Done |
| 4 | Automation engine — time cron + device state + sunrise/sunset triggers → cross-device actions, HTMX UI at /automations | ✅ Done |
| 5 | Event history log — `Event` table, /history page, auto-refreshes every 30s | ✅ Done |

## Integrations Added Beyond Original Plan

- **RoachCam** — MJPEG live feed from motion-capture Pi embedded on dashboard (`ROACHCAM_URL` env var)
- **Fire TV** — ADB polling every 5s; `media_state` and `app_id` as automation trigger fields; device card shows playback state. Only works on Android-based Fire OS devices — see Backlog blocker for Vega OS.
- **Sunrise/sunset automation triggers** — new `TriggerType.sun`; sun times fetched daily from Open-Meteo (`app/services/weather.py get_sun_times()`, no API key needed) using `LAT`/`LON` env vars, same location config the rain automation already used. Automations pick sunrise or sunset plus a +/- minute offset; `refresh_sun_jobs()` in `automation_engine.py` reschedules one-shot APScheduler jobs daily and whenever a sun rule is created/edited/toggled. Silently no-ops if `LAT`/`LON` aren't set, same as the rain check.
- **Zigbee RGB / colour-temperature bulb control** — `app/devices/zigbee_color.py` converts between the dashboard's 0-100 slider scale and Zigbee2MQTT's native units (mireds for colour temp, hue/saturation for RGB), mirroring the existing Tuya hsv-hex helpers. Wired into both directions: `api/devices.py send_command()` forwards `color_temp`/`color_rgb`/`color_mode` to Z2M's `/set` topic, and `devices/mqtt.py _apply_state()` parses inbound `color_temp`/`color`/`color_mode` so the card reflects the bulb's real reported state. Gated by the existing `Device.dimmable` flag — the Zigbee discovery import form already had a "Dimmable / colour controls" checkbox, but it wasn't set for the two Innr RB 282 C bulbs (Dining room uplighter, Porch light) at import time; fixed directly in the DB.
- **Red alert covers Zigbee bulbs too** — `red_alert.py` previously only flashed Tuya bulbs (`get_tuya_bulbs()` + persistent-socket `flash_sync`). Added `mqtt.get_zigbee_bulbs()` (filters by `dimmable`) and an async `_flash_zigbee()`/`_restore_zigbee()` pair so dimmable Zigbee bulbs flash red and restore alongside the Tuya ones. Flash/restore payloads set `"transition": 0` — without it the bulb's default fade blends flash cycles together and can be captured mid-transition. Also fixed a cross-automation bug this surfaced: flashing a bulb rapidly was tripping any `device_state` automation watching that bulb (e.g. "Dining/Lounge sync"), cascading into unrelated devices for the duration of the alert — `automation_engine.check_state_triggers()` now short-circuits while `red_alert.is_active()`.

## File Structure

```text
app/
├── main.py                        # FastAPI app, lifespan, router mounts
├── db.py                          # SQLite engine, init_db() with ALTER TABLE migrations
├── templating.py                  # Jinja2 templates instance + globals (firetv_enabled)
│
├── api/
│   ├── devices.py                 # All device HTTP routes (toggle, command, schedule, import, charts)
│   ├── automations.py             # /automations CRUD
│   ├── alerts.py                  # /alert red-alert endpoints
│   ├── history.py                 # /history event log
│   └── network.py                 # /network LAN scan + WAN check (_check_wan via TCP to 1.1.1.1:53)
│
├── devices/
│   ├── models.py                  # SQLModel tables: Device, Schedule, Automation, Event, PowerSample, ClimateSample
│   ├── mqtt.py                    # aiomqtt listener, _apply_state(), ClimateSample/PowerSample writes, Z2M state.json seed
│   ├── tuya.py                    # tinytuya LAN commands
│   ├── hon.py                     # pyhOn Haier cloud API
│   └── firetv.py                  # androidtv ADB polling (ENABLED flag, off by default)
│
├── services/
│   ├── automation_engine.py       # check_state_triggers(), fire_action(), refresh_sun_jobs()
│   ├── automations.py             # load_time_automations(), APScheduler job wiring
│   ├── scheduler.py               # apply_schedule(), remove_schedule() for device on/off timers
│   ├── red_alert.py               # flash-all-bulbs red alert with persistent Tuya sockets
│   ├── tuya_poller.py             # background Tuya state polling
│   └── weather.py                 # Open-Meteo rain check + get_sun_times()
│
├── static/                        # All assets bundled locally (no CDN — works offline)
│   ├── tailwind.js                # Tailwind Play CDN runtime
│   ├── htmx.min.js
│   ├── bootstrap-icons.min.css + fonts/
│   ├── chart.umd.min.js + chartjs-adapter-date-fns.bundle.min.js
│   └── manifest.json + icons (PWA)
│
└── templates/
    ├── base.html                  # Shared layout, nav, PWA meta tags
    ├── index.html                 # Dashboard (device grid + RoachCam)
    ├── automations.html           # Automation rule list + form
    ├── history.html               # Event log
    ├── network.html               # LAN map page
    ├── climate_chart.html         # Temperature/humidity history chart
    ├── power_chart.html           # Plug power/voltage/current history chart
    ├── z2m_discover.html          # Zigbee2MQTT device import
    ├── add_device.html            # Manual Tuya device add form
    └── partials/
        ├── device_card.html       # Per-device card (plug/bulb/ac/tv/sensor variants)
        ├── device_grid.html       # Full device grid (HTMX polling target)
        ├── device_schedule.html   # On/off timer section inside card
        ├── device_name.html       # Inline-editable device name
        ├── network_devices.html   # WAN spine + LAN device grid + Zigbee mesh (HTMX swap target)
        ├── automation_form.html   # New/edit automation form
        ├── automation_row.html    # Single automation row
        ├── history_rows.html      # Event log rows
        ├── red_alert_btn.html     # Red Alert / Stand Down toggle
        └── rename_form.html       # Inline rename input
```

## Backlog

- **Rolling shutter / persiana** — new patio door has an external tubular-motor shutter. Best fit: Zigbee tubular motor (e.g. Zemismart ZM25TQ) via existing Zigbee2MQTT. Needs `DeviceType.cover`, `position` field, Z2M cover payload handling, open/stop/close card UI, `set_position` automation action.
- **Tuya bulb scenes/moods** — DPS 25 hex-encoded scene strings for animated presets (breathing, colour cycling); new `set_scene` action type with named-preset dropdown.
- **hOn Pi fix** — resolve `pyhOn` `country` kwarg mismatch on Raspberry Pi OS.
- **Fire TV control** — send ADB key events (play/pause, volume, back) from the dashboard. Code complete (`app/devices/firetv.py` `send_key()`, `POST /devices/{id}/key`, card buttons) and unit-tested, but **unverified against real hardware and blocked** on the user's actual device: a Fire TV Stick 4K Select runs Amazon's new Vega OS (Linux-based, not Android), which has no ADB Debugging option at all — Developer Options only exposes "Deep Sleep". This also means the existing ADB-based `media_state` polling above won't work against this device either. On hold — see "Fire TV Alexa control" below for the path being considered instead.
- **Fire TV Alexa control** — since Vega OS blocks ADB entirely, explored routing control through Amazon's Alexa API instead (Fire TV Stick 4K Select is Alexa-enabled). No official public API exists for this; the hobbyist standard is the unofficial `alexapy`/`aioamazondevices` library (used by Home Assistant's `alexa_media_player`), which authenticates as the user (email/password + 2FA) by mimicking the Alexa app — not an OAuth app credential, and Amazon can break it without notice. Also unconfirmed whether it can control Fire TV (vs. just Echo speakers) and how fast play/pause state updates arrive. Paused before implementation — user put this on hold 2026-07-14.
- **History filtering** — filter /history by category or automation name.
- **Zigbee permit_join status** — Z2M page should show current join-open state on load and reflect it in the button; permit_join window expires silently on page refresh.

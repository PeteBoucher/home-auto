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

## Backlog

- **Rolling shutter / persiana** — new patio door has an external tubular-motor shutter. Best fit: Zigbee tubular motor (e.g. Zemismart ZM25TQ) via existing Zigbee2MQTT. Needs `DeviceType.cover`, `position` field, Z2M cover payload handling, open/stop/close card UI, `set_position` automation action.
- **Tuya bulb scenes/moods** — DPS 25 hex-encoded scene strings for animated presets (breathing, colour cycling); new `set_scene` action type with named-preset dropdown.
- **hOn Pi fix** — resolve `pyhOn` `country` kwarg mismatch on Raspberry Pi OS.
- **Fire TV control** — send ADB key events (play/pause, volume, back) from the dashboard. Code complete (`app/devices/firetv.py` `send_key()`, `POST /devices/{id}/key`, card buttons) and unit-tested, but **unverified against real hardware and blocked** on the user's actual device: a Fire TV Stick 4K Select runs Amazon's new Vega OS (Linux-based, not Android), which has no ADB Debugging option at all — Developer Options only exposes "Deep Sleep". This also means the existing ADB-based `media_state` polling above won't work against this device either. On hold — see "Fire TV Alexa control" below for the path being considered instead.
- **Fire TV Alexa control** — since Vega OS blocks ADB entirely, explored routing control through Amazon's Alexa API instead (Fire TV Stick 4K Select is Alexa-enabled). No official public API exists for this; the hobbyist standard is the unofficial `alexapy`/`aioamazondevices` library (used by Home Assistant's `alexa_media_player`), which authenticates as the user (email/password + 2FA) by mimicking the Alexa app — not an OAuth app credential, and Amazon can break it without notice. Also unconfirmed whether it can control Fire TV (vs. just Echo speakers) and how fast play/pause state updates arrive. Paused before implementation — user put this on hold 2026-07-14.
- **History filtering** — filter /history by category or automation name.
- **Zigbee permit_join status** — Z2M page should show current join-open state on load and reflect it in the button; permit_join window expires silently on page refresh.

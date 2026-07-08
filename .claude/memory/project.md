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
| 4 | Automation engine — time cron + device state triggers → cross-device actions, HTMX UI at /automations | ✅ Done |
| 5 | Event history log — `Event` table, /history page, auto-refreshes every 30s | ✅ Done |

## Integrations Added Beyond Original Plan

- **RoachCam** — MJPEG live feed from motion-capture Pi embedded on dashboard (`ROACHCAM_URL` env var)
- **Fire TV** — ADB polling every 5s; `media_state` and `app_id` as automation trigger fields; device card shows playback state

## Backlog

- **Rolling shutter / persiana** — new patio door has an external tubular-motor shutter. Best fit: Zigbee tubular motor (e.g. Zemismart ZM25TQ) via existing Zigbee2MQTT. Needs `DeviceType.cover`, `position` field, Z2M cover payload handling, open/stop/close card UI, `set_position` automation action.
- **Tuya bulb scenes/moods** — DPS 25 hex-encoded scene strings for animated presets (breathing, colour cycling); new `set_scene` action type with named-preset dropdown.
- **hOn Pi fix** — resolve `pyhOn` `country` kwarg mismatch on Raspberry Pi OS.
- **Fire TV control** — send ADB key events (play/pause, volume, back) from the dashboard.
- **History filtering** — filter /history by category or automation name.

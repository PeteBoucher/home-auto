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
| 2 | hOn / Haier A/C card (power, mode, temp, fan speed) | ✅ Done — `pyhOn` no longer takes `country`/`language` kwargs as of 0.17.5; `hon.py start()` updated, unblocked 2026-07-29 |
| 3 | Zigbee2MQTT integration (Mosquitto broker, Zigbee bulb + socket adapter) | ✅ Done |
| 4 | Automation engine — time cron + device state + sunrise/sunset triggers → cross-device actions, HTMX UI at /automations | ✅ Done |
| 5 | Event history log — `Event` table, /history page, auto-refreshes every 30s | ✅ Done |

## Integrations Added Beyond Original Plan

- **RoachCam** — MJPEG live feed from motion-capture Pi embedded on dashboard (`ROACHCAM_URL` env var)
- **Fire TV** — ADB polling every 5s; `media_state` and `app_id` as automation trigger fields; device card shows playback state. Only works on Android-based Fire OS devices — see Backlog blocker for Vega OS.
- **Sunrise/sunset automation triggers** — new `TriggerType.sun`; sun times fetched daily from Open-Meteo (`app/services/weather.py get_sun_times()`, no API key needed) using `LAT`/`LON` env vars, same location config the rain automation already used. Automations pick sunrise or sunset plus a +/- minute offset; `refresh_sun_jobs()` in `automation_engine.py` reschedules one-shot APScheduler jobs daily and whenever a sun rule is created/edited/toggled. Silently no-ops if `LAT`/`LON` aren't set, same as the rain check.
- **Zigbee RGB / colour-temperature bulb control** — `app/devices/zigbee_color.py` converts between the dashboard's 0-100 slider scale and Zigbee2MQTT's native units (mireds for colour temp, hue/saturation for RGB), mirroring the existing Tuya hsv-hex helpers. Wired into both directions: `api/devices.py send_command()` forwards `color_temp`/`color_rgb`/`color_mode` to Z2M's `/set` topic, and `devices/mqtt.py _apply_state()` parses inbound `color_temp`/`color`/`color_mode` so the card reflects the bulb's real reported state. Gated by the existing `Device.dimmable` flag — the Zigbee discovery import form already had a "Dimmable / colour controls" checkbox, but it wasn't set for the two Innr RB 282 C bulbs (Dining room uplighter, Porch light) at import time; fixed directly in the DB.
- **Red alert covers Zigbee bulbs too** — `red_alert.py` previously only flashed Tuya bulbs (`get_tuya_bulbs()` + persistent-socket `flash_sync`). Added `mqtt.get_zigbee_bulbs()` (filters by `dimmable`) and an async `_flash_zigbee()`/`_restore_zigbee()` pair so dimmable Zigbee bulbs flash red and restore alongside the Tuya ones. Flash/restore payloads set `"transition": 0` — without it the bulb's default fade blends flash cycles together and can be captured mid-transition. Also fixed a cross-automation bug this surfaced: flashing a bulb rapidly was tripping any `device_state` automation watching that bulb (e.g. the old "Dining/Lounge sync" automation, since replaced by Groups below), cascading into unrelated devices for the duration of the alert — `automation_engine.check_state_triggers()` now short-circuits while `red_alert.is_active()`.
- **Device Groups** — cross-integration groups that stay in sync (state, brightness, colour), new `DeviceGroup` model + `Device.group_id`, `/groups` page, group card on the dashboard. Zigbee members are mirrored into a real Zigbee2MQTT group (`zigbee_group_name`, created/maintained via `bridge/request/group/*` in `devices/mqtt.py`) so a group command is a single native Zigbee groupcast; non-Zigbee members (Tuya, etc.) are commanded individually alongside it — `services/groups.py send_group_command()`. Any confirmed member state change from *any* source (MQTT, Tuya polling, an automation, a direct per-device command) pulls every other member into matching state via `propagate_member_change()`, wired into both `devices/mqtt.py _listen()` and `services/tuya_poller.py`. Extracted the shared single-device command logic into `services/device_commands.py` so the per-device endpoint and group fan-out share one code path. Replaced the old "Dining/Lounge sync on/off" automation pair with a real group, **"Lounge & Dining lights"** (Standard lamp + Uplighter), created 2026-07-22.

## Known Gotchas

- **Renaming a Zigbee device in the Z2M frontend breaks it** — a device's Zigbee2MQTT `friendly_name` *is* the MQTT topic Z2M publishes state to and listens for commands on. Our dashboard's own rename (pencil icon on the card, `POST /devices/{id}/rename`) only changes the display `name`/`room` and never touches `Device.device_id` (which stores that friendly_name) — it's a display label, not a resync. So renaming a device in the Z2M frontend instead silently breaks both directions for that device: inbound state updates stop matching any row (`_apply_state` looks up `Device.device_id == friendly_name`), and outbound commands keep publishing to the old, now-dead topic. Nothing errors — the card just quietly stops updating and stops responding to toggles. Fix: check Z2M's current friendly_name for the device (`mosquitto_sub -h localhost -t zigbee2mqtt/bridge/devices -v -C 1 --retained-only`, match by `ieee_address`) and update `Device.device_id` in the DB to match — there's no UI for this yet. Happened 2026-07-22 with the Living room Lidl smart socket (renamed to `LIDL_smart_socket` in Z2M, DB still had `living_room_socket`). Prefer renaming through the dashboard's own edit form to avoid this entirely.
- **pyhOn command quirks (found 2026-07-29 via live introspection of the AC unit, model AS35RBAHRA-4)** — three separate traps, all silent (no exception surfaces to the user):
  1. `machMode`/`windSpeed`/`windDirectionVertical` are `HonParameterEnum` and validate against a list of *strings* — setting an int raises `ValueError` even when it's numerically an allowed option (`8 not in ['2','4','5','6','8']` because `8 != '8'`). `hon.py send_command()` must `str(...)` these. `tempSel`/`muteStatus` are `HonParameterRange` and want native int/float — don't cast those.
  2. Power off isn't a parameter — `onOffStatus` is locked (`HonParameterFixed`) to `1` on the `startProgram` command and to `0` on `stopProgram`. Turning the unit off means calling the `stopProgram` command, not setting `onOffStatus=0` on `startProgram` (that will always be a no-op).
  3. **The big one**: `HonCommand.send_parameters()` (inside pyhOn itself) calls `appliance.sync_command_to_params()`, which overwrites the appliance's local attribute cache with the command's *intended* values before the network call even happens — unconditionally, regardless of whether the API call succeeds or the physical device complies. `get_state()` must call `await appliance.update(force=True)` before reading `attributes["parameters"]`, or it just reports back its own last command's intent, not reality — every command looks confirmed the instant it's sent.
  Discovered via a throwaway local script authenticating directly against the hOn cloud (credentials in `.env`) and inspecting `appliance.commands["startProgram"].parameters[...]` types/values live — worth doing again for any new hOn appliance (e.g. the not-yet-paired Haier smart TV) rather than assuming the AC's parameter names/behavior carry over.

## File Structure

```text
app/
├── main.py                        # FastAPI app, lifespan, router mounts
├── db.py                          # SQLite engine, init_db() with ALTER TABLE migrations
├── templating.py                  # Jinja2 templates instance + globals (firetv_enabled)
│
├── api/
│   ├── devices.py                 # All device HTTP routes (toggle, command, schedule, import, charts)
│   ├── groups.py                  # /groups CRUD + group command endpoint
│   ├── automations.py             # /automations CRUD
│   ├── alerts.py                  # /alert red-alert endpoints
│   ├── history.py                 # /history event log
│   └── network.py                 # /network LAN scan + WAN check (_check_wan via TCP to 1.1.1.1:53)
│
├── devices/
│   ├── models.py                  # SQLModel tables: Device, DeviceGroup, Schedule, Automation, Event, PowerSample, ClimateSample, EnergyDailySummary
│   ├── mqtt.py                    # aiomqtt listener, _apply_state(), build_set_payload(), Zigbee group management, Z2M state.json seed
│   ├── tuya.py                    # tinytuya LAN commands
│   ├── hon.py                     # pyhOn Haier cloud API
│   └── firetv.py                  # androidtv ADB polling (ENABLED flag, off by default)
│
├── services/
│   ├── automation_engine.py       # check_state_triggers(), fire_action(), refresh_sun_jobs()
│   ├── automations.py             # load_time_automations(), APScheduler job wiring
│   ├── device_commands.py         # apply_device_command() — shared Tuya/Zigbee single-device command logic
│   ├── groups.py                  # create/delete/set_group_members(), send_group_command(), propagate_member_change()
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
- **hOn Haier smart TV** — new TV, controllable via the hOn app per the user, but as of 2026-07-29 it is **not yet paired** in the hOn app — querying the account directly returns only the A/C unit. Nothing to build until it's paired; once it is, re-run discovery to see its actual `appliance_type`/`commands`/`parameters` (Haier TV control likely uses a different command/parameter set than the A/C's `tempSel`/`machMode`/`windSpeed`, needs live introspection, don't guess at it). Also need a UI path — `DeviceType.tv` already exists but its card is hardcoded to the Fire TV's ADB media-state model; hOn TV control would need its own branch (by `integration`) rather than reusing that block as-is.
- **Fire TV control** — send ADB key events (play/pause, volume, back) from the dashboard. Code complete (`app/devices/firetv.py` `send_key()`, `POST /devices/{id}/key`, card buttons) and unit-tested, but **unverified against real hardware and blocked** on the user's actual device: a Fire TV Stick 4K Select runs Amazon's new Vega OS (Linux-based, not Android), which has no ADB Debugging option at all — Developer Options only exposes "Deep Sleep". This also means the existing ADB-based `media_state` polling above won't work against this device either. On hold — see "Fire TV Alexa control" below for the path being considered instead.
- **Fire TV Alexa control** — since Vega OS blocks ADB entirely, explored routing control through Amazon's Alexa API instead (Fire TV Stick 4K Select is Alexa-enabled). No official public API exists for this; the hobbyist standard is the unofficial `alexapy`/`aioamazondevices` library (used by Home Assistant's `alexa_media_player`), which authenticates as the user (email/password + 2FA) by mimicking the Alexa app — not an OAuth app credential, and Amazon can break it without notice. Also unconfirmed whether it can control Fire TV (vs. just Echo speakers) and how fast play/pause state updates arrive. Paused before implementation — user put this on hold 2026-07-14.
- **History filtering** — filter /history by category or automation name.
- **Zigbee permit_join status** — Z2M page should show current join-open state on load and reflect it in the button; permit_join window expires silently on page refresh.

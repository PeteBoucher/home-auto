import asyncio
import logging
import os
from datetime import datetime, time, timedelta

from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Automation, Device, DeviceGroup, Event, Integration, TriggerType
from app.devices import tuya as tuya_client
from app.devices import mqtt as mqtt_client
from app.devices import hon as hon_client
from app.services import groups as groups_service
from app.services import red_alert
from app.services.device_commands import apply_device_command
from app.services.weather import get_sun_times, is_raining

log = logging.getLogger(__name__)

_last_eval: dict[int, bool] = {}  # automation_id → last condition result (edge detection)
_snapshots: dict[str, dict] = {}  # "device:<id>" → state captured by a snapshot_before action,
# consumed by a later restore_snapshot action targeting the same device. In-memory
# only — lost on restart, same trade-off the old hardcoded rain automation made.


def _log(category: str, message: str) -> None:
    with Session(engine) as session:
        session.add(Event(category=category, message=message))
        session.commit()


def _describe_command(action_type: str, action_value: str | None, device_name: str) -> str:
    if action_type == "set_state_on":
        return f"turned on {device_name}"
    if action_type == "set_state_off":
        return f"turned off {device_name}"
    if action_type == "set_brightness":
        return f"set {device_name} brightness to {action_value}%"
    if action_type == "set_color_temp":
        return f"set {device_name} colour temp to {action_value}"
    if action_type == "set_color_rgb":
        return f"set {device_name} colour to {action_value}"
    if action_type == "restore_snapshot":
        return f"restored {device_name} to its pre-automation state"
    return f"{action_type} on {device_name}"


def _build_command(action_type: str, action_value: str | None) -> dict:
    if action_type == "set_state_on":
        return {"state": True}
    if action_type == "set_state_off":
        return {"state": False}
    if action_type == "set_brightness":
        return {"brightness": int(action_value or 50)}
    if action_type == "set_color_temp":
        return {"color_temp": int(action_value or 50)}
    if action_type == "set_color_rgb":
        return {"color_rgb": action_value or "#ffffff"}
    return {}


def _group_member_devices(session: Session, group_id: int) -> list[Device]:
    return list(session.exec(select(Device).where(Device.group_id == group_id)).all())


async def _snapshot_device(device: Device) -> dict:
    """Capture enough of a device's current state to restore it later via
    _restore_command(). Tuya devices get a live query (tuya.live_snapshot,
    the same thing a per-device command already does); Zigbee has no
    synchronous equivalent, so its last-known DB state — kept fresh by the
    MQTT listener pushing every real report — is used instead."""
    if device.integration == Integration.tuya:
        return await tuya_client.live_snapshot(device)
    return {
        "state": device.state,
        "color_mode": device.color_mode,
        "color_rgb": device.color_rgb,
        "brightness": device.brightness,
        "color_temp": device.color_temp,
    }


def _restore_command(snap: dict) -> dict:
    """Build a command dict that puts a device back into a previously
    captured snapshot — same precedence the old rain automation's
    restore_bulb_state used: off wins outright, then colour mode, then plain
    white brightness/colour-temp."""
    if not snap.get("state"):
        return {"state": False}
    command: dict = {"state": True}
    if snap.get("color_mode") == "colour" and snap.get("color_rgb"):
        command["color_rgb"] = snap["color_rgb"]
    else:
        if snap.get("brightness") is not None:
            command["brightness"] = snap["brightness"]
        if snap.get("color_temp") is not None:
            command["color_temp"] = snap["color_temp"]
    return command


async def _snapshot_targets(members: list[Device]) -> None:
    for device in members:
        _snapshots[f"device:{device.id}"] = await _snapshot_device(device)


async def _restore_targets(session: Session, members: list[Device]) -> None:
    for device in members:
        snap = _snapshots.pop(f"device:{device.id}", None)
        if snap is None:
            continue
        await apply_device_command(session, device, _restore_command(snap))


async def _fire(automation: Automation) -> None:
    with Session(engine) as session:
        group = session.get(DeviceGroup, automation.action_group_id) if automation.action_group_id else None
        device = None if group else session.get(Device, automation.action_device_id)
        target = group or device
        if not target:
            log.warning(
                "Automation %r: action target not found (group=%r, device=%r)",
                automation.name, automation.action_group_id, automation.action_device_id,
            )
            return
        members = _group_member_devices(session, group.id) if group else [device]

        if automation.action_type == "restore_snapshot":
            description = _describe_command("restore_snapshot", None, target.name)
            log.warning("Automation %r firing: %s", automation.name, description)
            _log("automation", f"'{automation.name}' fired — {description}")
            await _restore_targets(session, members)
            return

        command = _build_command(automation.action_type, automation.action_value)
        if not command:
            log.warning("Automation %r: empty command for action_type=%r", automation.name, automation.action_type)
            return

        if automation.action_snapshot_before:
            await _snapshot_targets(members)

        description = _describe_command(automation.action_type, automation.action_value, target.name)
        log.warning("Automation %r firing: %s", automation.name, description)
        _log("automation", f"'{automation.name}' fired — {description}")

        if group:
            # Native Zigbee groupcast for Zigbee members, individual commands
            # for everything else — same fan-out a group card's own command
            # button uses.
            await groups_service.send_group_command(session, group, command)
        elif device.integration == Integration.tuya:
            await tuya_client.send_command(device, command)
        elif device.integration == Integration.zigbee2mqtt:
            payload = mqtt_client.build_set_payload(command)
            if payload:
                await mqtt_client.publish(f"{mqtt_client.PREFIX}/{device.device_id}/set", payload)
        elif device.integration == Integration.hon:
            await hon_client.send_command(device.device_id, command)


def _within_time_window(start: str | None, end: str | None, now: datetime | None = None) -> bool:
    """True if start/end aren't both set (no restriction), or the current
    local time falls within [start, end). Handles overnight spans (e.g.
    22:00-06:00) where start > end by treating "in window" as being at or
    after start OR before end, rather than requiring start <= now <= end."""
    if not start or not end:
        return True
    now_t = (now or datetime.now()).time()
    start_h, start_m = map(int, start.split(":"))
    end_h, end_m = map(int, end.split(":"))
    start_t, end_t = time(start_h, start_m), time(end_h, end_m)
    if start_t <= end_t:
        return start_t <= now_t < end_t
    return now_t >= start_t or now_t < end_t


def _eval_condition(field: str, operator: str, trigger_value: str, state: dict, compare_field: str | None = None) -> bool:
    raw = state.get(field)
    if raw is None:
        return False
    try:
        if operator == "within":
            # trigger_value is a tolerance, not an absolute value — true when
            # `field` is within that many units of `compare_field` on the same
            # device's state, e.g. outdoor_temp within 2 of temperature (target).
            if not compare_field:
                return False
            other = state.get(compare_field)
            if other is None:
                return False
            return abs(float(raw) - float(other)) <= float(trigger_value)
        if field in ("state", "online", "raining"):
            expected = trigger_value.lower() in ("true", "on", "1")
            actual = bool(raw)
            if operator == "eq":
                return actual == expected
            if operator == "ne":
                return actual != expected
            return False
        else:
            exp = float(trigger_value)
            act = float(raw)
            return {"eq": act == exp, "ne": act != exp, "gt": act > exp, "lt": act < exp}.get(operator, False)
    except (ValueError, TypeError):
        return False


async def _fire_on_rising_edge(auto: Automation, met: bool) -> None:
    """Shared edge-detection: fires `auto` only on the false→true transition
    of `met`, tracked per-automation in _last_eval so repeated "still true"
    checks (a poll, a re-evaluated weather check) don't refire it."""
    was_met = _last_eval.get(auto.id, False)
    _last_eval[auto.id] = met
    if met and not was_met:
        try:
            await _fire(auto)
        except Exception as exc:
            log.error("Automation %r fire error: %s", auto.name, exc, exc_info=True)
            _log("error", f"'{auto.name}' failed: {exc}")


async def check_state_triggers(device_id: int, state: dict) -> None:
    if red_alert.is_active():
        # Red alert flashes a bulb's state rapidly; state-trigger automations
        # watching that bulb would otherwise fire on every flash cycle and
        # cascade into unrelated devices for the duration of the alert.
        return
    with Session(engine) as session:
        automations = list(session.exec(
            select(Automation).where(
                Automation.enabled == True,
                Automation.trigger_type == TriggerType.device_state,
                Automation.trigger_device_id == device_id,
            )
        ).all())
    for auto in automations:
        if not auto.trigger_field or not auto.trigger_operator or auto.trigger_value is None:
            continue
        met = (
            _eval_condition(auto.trigger_field, auto.trigger_operator, auto.trigger_value, state, auto.trigger_compare_field)
            and _within_time_window(auto.trigger_window_start, auto.trigger_window_end)
        )
        await _fire_on_rising_edge(auto, met)


async def check_weather_triggers() -> None:
    """Polled periodically (see main.py) rather than event-driven like
    check_state_triggers — there's no device pushing state changes for
    weather, so this is the "weather" trigger type's only entry point."""
    lat = float(os.getenv("LAT", "0"))
    lon = float(os.getenv("LON", "0"))
    if lat == 0 and lon == 0:
        log.warning("LAT/LON not configured — skipping weather-trigger check")
        return
    try:
        rain_now = await is_raining(lat, lon)
    except Exception as exc:
        log.warning("Weather check failed: %s", exc)
        return

    state = {"raining": rain_now}
    with Session(engine) as session:
        automations = list(session.exec(
            select(Automation).where(
                Automation.enabled == True,
                Automation.trigger_type == TriggerType.weather,
            )
        ).all())
    for auto in automations:
        if not auto.trigger_operator or auto.trigger_value is None:
            continue
        met = _eval_condition("raining", auto.trigger_operator, auto.trigger_value, state)
        await _fire_on_rising_edge(auto, met)


async def _fire_by_id(automation_id: int) -> None:
    with Session(engine) as session:
        auto = session.get(Automation, automation_id)
    if auto and auto.enabled:
        await _fire(auto)


def _load_time_job(automation: Automation) -> None:
    from app.services.scheduler import scheduler
    job_id = f"auto_{automation.id}"
    if not automation.enabled or not automation.trigger_time:
        job = scheduler.get_job(job_id)
        if job:
            job.remove()
        return
    h, m = map(int, automation.trigger_time.split(":"))
    scheduler.add_job(
        _fire_by_id, "cron", hour=h, minute=m,
        id=job_id,
        args=[automation.id],
        replace_existing=True,
    )


def load_time_automations() -> None:
    with Session(engine) as session:
        automations = list(session.exec(
            select(Automation).where(Automation.trigger_type == TriggerType.time)
        ).all())
    for auto in automations:
        _load_time_job(auto)


async def refresh_sun_jobs() -> None:
    """Recompute today's sunrise/sunset and (re)schedule sun-triggered automations.

    Run daily (times drift a bit each day) and whenever a sun automation is
    created, edited, toggled, so job times stay in sync with the actual rules.
    """
    from app.services.scheduler import scheduler

    with Session(engine) as session:
        automations = list(session.exec(
            select(Automation).where(Automation.trigger_type == TriggerType.sun)
        ).all())

    sunrise = sunset = None
    if any(a.enabled for a in automations):
        lat = float(os.getenv("LAT", "0"))
        lon = float(os.getenv("LON", "0"))
        if lat == 0 and lon == 0:
            log.warning("LAT/LON not configured — skipping sun-trigger scheduling")
        else:
            try:
                sunrise, sunset = await get_sun_times(lat, lon)
            except Exception as exc:
                log.warning("Sun times fetch failed: %s", exc)

    now = datetime.now()
    for auto in automations:
        job_id = f"auto_sun_{auto.id}"
        target = None
        if auto.enabled and sunrise and sunset:
            base = sunrise if auto.trigger_sun_event == "sunrise" else sunset
            target = base + timedelta(minutes=auto.trigger_sun_offset or 0)
            if target <= now:
                # Today's event has already passed — this job runs once a day at
                # whatever time the service last started, not at a fixed pre-dawn
                # time, so "today's" sunrise/sunset is routinely already behind us.
                # Roll forward a day rather than dropping the job; the ~1-2 min/day
                # drift is corrected on the next daily refresh anyway.
                target += timedelta(days=1)
        if target and target > now:
            scheduler.add_job(
                _fire_by_id, "date", run_date=target,
                id=job_id, args=[auto.id], replace_existing=True,
            )
        else:
            existing = scheduler.get_job(job_id)
            if existing:
                existing.remove()


async def apply_automation(automation: Automation) -> None:
    if automation.trigger_type == TriggerType.time:
        _load_time_job(automation)
    elif automation.trigger_type == TriggerType.sun:
        await refresh_sun_jobs()
    else:
        _last_eval.pop(automation.id, None)


def remove_automation(automation_id: int) -> None:
    from app.services.scheduler import scheduler
    for job_id in (f"auto_{automation_id}", f"auto_sun_{automation_id}"):
        job = scheduler.get_job(job_id)
        if job:
            job.remove()
    _last_eval.pop(automation_id, None)

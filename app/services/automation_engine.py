import asyncio
import logging
from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Automation, Device, Event, Integration, TriggerType
from app.devices import tuya as tuya_client
from app.devices import mqtt as mqtt_client

log = logging.getLogger(__name__)

_last_eval: dict[int, bool] = {}  # automation_id → last condition result (edge detection)


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


async def _fire(automation: Automation) -> None:
    with Session(engine) as session:
        device = session.get(Device, automation.action_device_id)
    if not device:
        log.warning("Automation %r: action device %d not found", automation.name, automation.action_device_id)
        return
    command = _build_command(automation.action_type, automation.action_value)
    if not command:
        log.warning("Automation %r: empty command for action_type=%r", automation.name, automation.action_type)
        return
    description = _describe_command(automation.action_type, automation.action_value, device.name)
    log.warning("Automation %r firing: %s", automation.name, description)
    _log("automation", f"'{automation.name}' fired — {description}")
    if device.integration == Integration.tuya:
        await tuya_client.send_command(device, command)
    elif device.integration == Integration.zigbee2mqtt:
        payload: dict = {}
        if "state" in command:
            payload["state"] = "ON" if command["state"] else "OFF"
        if "brightness" in command:
            payload["brightness"] = round(command["brightness"] * 2.54)
        await mqtt_client.publish(f"{mqtt_client.PREFIX}/{device.device_id}/set", payload)


def _eval_condition(field: str, operator: str, trigger_value: str, state: dict) -> bool:
    raw = state.get(field)
    if raw is None:
        return False
    try:
        if field in ("state", "online"):
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


async def check_state_triggers(device_id: int, state: dict) -> None:
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
        met = _eval_condition(auto.trigger_field, auto.trigger_operator, auto.trigger_value, state)
        was_met = _last_eval.get(auto.id, False)
        _last_eval[auto.id] = met
        if met and not was_met:
            try:
                await _fire(auto)
            except Exception as exc:
                log.error("Automation %r fire error: %s", auto.name, exc, exc_info=True)
                _log("error", f"'{auto.name}' failed: {exc}")


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


def apply_automation(automation: Automation) -> None:
    if automation.trigger_type == TriggerType.time:
        _load_time_job(automation)
    else:
        _last_eval.pop(automation.id, None)


def remove_automation(automation_id: int) -> None:
    from app.services.scheduler import scheduler
    job = scheduler.get_job(f"auto_{automation_id}")
    if job:
        job.remove()
    _last_eval.pop(automation_id, None)

import time
from collections.abc import Iterable

from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, DeviceGroup, Integration
from app.devices import mqtt as mqtt_client
from app.services.device_commands import apply_device_command

# How long after we command a group member its own reports are treated as
# echoes of that command rather than as a fresh change to fan out — see
# propagate_member_change(). Generous on purpose: a Tuya poll already in
# flight when the command went out can take 5s to come back with the
# pre-command state, and the Pi's event loop isn't always prompt.
_SETTLE_SECONDS = 10
_settling_until: dict[int, float] = {}  # device id → time.monotonic() deadline


def _mark_settling(device_ids: Iterable[int]) -> None:
    deadline = time.monotonic() + _SETTLE_SECONDS
    for device_id in device_ids:
        _settling_until[device_id] = deadline


def _is_settling(device_id: int) -> bool:
    deadline = _settling_until.get(device_id)
    if deadline is None:
        return False
    if time.monotonic() >= deadline:
        del _settling_until[device_id]
        return False
    return True


async def create_group(session: Session, name: str, device_ids: list[int]) -> DeviceGroup:
    group = DeviceGroup(name=name)
    session.add(group)
    session.commit()
    session.refresh(group)
    await set_group_members(session, group, device_ids)
    return group


async def set_group_members(session: Session, group: DeviceGroup, device_ids: list[int]) -> None:
    current = list(session.exec(select(Device).where(Device.group_id == group.id)).all())
    current_ids = {d.id for d in current}
    new_ids = set(device_ids)

    removed = [d for d in current if d.id not in new_ids]
    added_ids = new_ids - current_ids
    added = list(session.exec(select(Device).where(Device.id.in_(added_ids))).all()) if added_ids else []

    zigbee_added = [d for d in added if d.integration == Integration.zigbee2mqtt]
    zigbee_removed = [d for d in removed if d.integration == Integration.zigbee2mqtt]

    if zigbee_added and not group.zigbee_group_name:
        group.zigbee_group_name = f"group_{group.id}"
        await mqtt_client.create_zigbee_group(group.zigbee_group_name)

    if group.zigbee_group_name:
        for d in zigbee_added:
            await mqtt_client.add_group_member(group.zigbee_group_name, d.device_id)
        for d in zigbee_removed:
            await mqtt_client.remove_group_member(group.zigbee_group_name, d.device_id)

    for d in removed:
        d.group_id = None
        session.add(d)
    for d in added:
        d.group_id = group.id
        session.add(d)
    session.add(group)
    session.commit()


async def delete_group(session: Session, group: DeviceGroup) -> None:
    members = list(session.exec(select(Device).where(Device.group_id == group.id)).all())
    for d in members:
        d.group_id = None
        session.add(d)
    session.commit()
    if group.zigbee_group_name:
        await mqtt_client.remove_zigbee_group(group.zigbee_group_name)
    session.delete(group)
    session.commit()


async def send_group_command(session: Session, group: DeviceGroup, command: dict) -> None:
    """Command every member of the group. Zigbee members get a single native
    Zigbee groupcast; every other member is commanded individually.

    Commanding the group is treated as re-syncing it: any member previously
    detached via group_override (see propagate_member_change) rejoins.

    Every DB write is committed *before* any network I/O. A flushed-but-
    uncommitted write holds SQLite's write lock, and holding it across an
    await (the Tuya round-trip below can take seconds) deadlocks the app
    against itself: the next writer on the event loop — typically the MQTT
    listener applying a Zigbee member's confirmation — blocks the whole loop
    in busy_timeout, so this coroutine can never resume to commit and release
    it. That froze everything for the full 15s on every group command, then
    dropped the waiting write (see project.md "Known Gotchas").
    """
    members = list(session.exec(select(Device).where(Device.group_id == group.id)).all())
    zigbee_group_name = group.zigbee_group_name
    zigbee_members = [m for m in members if m.integration == Integration.zigbee2mqtt]
    other_members = [m for m in members if m.integration != Integration.zigbee2mqtt]
    groupcast = bool(zigbee_members and zigbee_group_name)

    for m in members:
        if groupcast and m.integration == Integration.zigbee2mqtt:
            _apply_command_locally(m, command)
        m.group_override = False
        session.add(m)
    _apply_command_locally(group, command)
    session.add(group)
    session.commit()
    _mark_settling(m.id for m in members)

    if groupcast:
        payload = mqtt_client.build_set_payload(command)
        if payload:
            await mqtt_client.publish(f"{mqtt_client.PREFIX}/{zigbee_group_name}/set", payload)

    for m in other_members:
        await apply_device_command(session, m, command)


def _apply_command_locally(target, command: dict) -> None:
    """Optimistically mirror a command onto a Device or DeviceGroup's own fields
    (real confirmation for Zigbee members arrives via the MQTT subscription).

    Deliberately does NOT touch `online` — a device that's actually unreachable
    (broken Zigbee join, dead battery, etc.) must not be marked online just
    because we sent it a command; only a real message from the device itself
    (state report or availability topic, handled in devices/mqtt.py
    _apply_state()) should ever confirm that. Optimistically flipping it here
    previously masked a genuinely broken Uplighter: toggling the group made it
    show "online" while it stayed completely unresponsive to every control.
    """
    if "state" in command:
        target.state = command["state"]
    if "brightness" in command:
        target.brightness = command["brightness"]
    if "color_temp" in command:
        target.color_temp = command["color_temp"]
        target.color_mode = "white"
    if "color_rgb" in command:
        target.color_rgb = command["color_rgb"]
        target.color_mode = "colour"
    if "color_mode" in command and "color_temp" not in command and "color_rgb" not in command:
        target.color_mode = command["color_mode"]


async def propagate_member_change(device_id: int) -> None:
    """Whenever one grouped device's confirmed state changes (via MQTT, Tuya
    polling, an automation, or a direct per-device command), pull every other
    member of its group into matching state/brightness/colour.

    A member with group_override set (see api/devices.py send_command) has been
    deliberately taken out of sync via its own card — e.g. for task lighting —
    and neither pushes its own changes to the group nor receives them, until
    the group itself is next commanded (send_group_command clears the flag).

    A report from a member we commanded ourselves in the last _SETTLE_SECONDS
    is an echo, not a change, and is not fanned out. Without that, two members
    whose reports cross (one still confirming an older command while the
    other confirms a newer one) each push their own state onto the other,
    whose confirmation then pushes it straight back — the group flips on/off
    a couple of times a second indefinitely.
    """
    if _is_settling(device_id):
        return
    with Session(engine) as session:
        device = session.get(Device, device_id)
        if not device or not device.group_id or device.group_override:
            return
        group = session.get(DeviceGroup, device.group_id)
        if not group:
            return

        group.state = device.state
        if device.dimmable:
            group.brightness = device.brightness
            group.color_temp = device.color_temp
            group.color_mode = device.color_mode
            group.color_rgb = device.color_rgb
        session.add(group)
        session.commit()

        siblings = list(session.exec(
            select(Device).where(
                Device.group_id == group.id, Device.id != device.id, Device.group_override == False,  # noqa: E712
            )
        ).all())
        for sib in siblings:
            command: dict = {}
            if sib.state != device.state:
                command["state"] = device.state
            if sib.dimmable and device.dimmable:
                mode_differs = sib.color_mode != device.color_mode
                if device.color_mode == "colour" and device.color_rgb:
                    if mode_differs or sib.color_rgb != device.color_rgb:
                        command["color_rgb"] = device.color_rgb
                else:
                    if device.color_temp is not None and (mode_differs or sib.color_temp != device.color_temp):
                        command["color_temp"] = device.color_temp
                    if device.brightness is not None and sib.brightness != device.brightness:
                        command["brightness"] = device.brightness
            if command:
                _mark_settling([sib.id])
                await apply_device_command(session, sib, command)

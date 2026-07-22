from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, DeviceGroup, Integration
from app.devices import mqtt as mqtt_client
from app.services.device_commands import apply_device_command


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
    Zigbee groupcast; every other member is commanded individually."""
    members = list(session.exec(select(Device).where(Device.group_id == group.id)).all())
    zigbee_members = [m for m in members if m.integration == Integration.zigbee2mqtt]
    other_members = [m for m in members if m.integration != Integration.zigbee2mqtt]

    if zigbee_members and group.zigbee_group_name:
        payload = mqtt_client.build_set_payload(command)
        if payload:
            await mqtt_client.publish(f"{mqtt_client.PREFIX}/{group.zigbee_group_name}/set", payload)
        for m in zigbee_members:
            _apply_command_locally(m, command)
            session.add(m)
        session.commit()

    for m in other_members:
        await apply_device_command(session, m, command)

    _apply_command_locally(group, command)
    session.add(group)
    session.commit()


def _apply_command_locally(target, command: dict) -> None:
    """Optimistically mirror a command onto a Device or DeviceGroup's own fields
    (real confirmation for Zigbee members arrives via the MQTT subscription)."""
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
    if hasattr(target, "online"):
        target.online = True


async def propagate_member_change(device_id: int) -> None:
    """Whenever one grouped device's confirmed state changes (via MQTT, Tuya
    polling, an automation, or a direct per-device command), pull every other
    member of its group into matching state/brightness/colour."""
    with Session(engine) as session:
        device = session.get(Device, device_id)
        if not device or not device.group_id:
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
            select(Device).where(Device.group_id == group.id, Device.id != device.id)
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
                await apply_device_command(session, sib, command)

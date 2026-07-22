from sqlmodel import Session

from app.devices.models import Device, Integration
from app.devices import mqtt as mqtt_client
from app.devices import tuya as tuya_client


async def apply_device_command(session: Session, device: Device, command: dict) -> None:
    """Send `command` (state/brightness/color_temp/color_rgb) to one device and
    persist the result. Shared by the per-device command endpoint and group fan-out —
    covers Tuya and Zigbee only, since those are the integrations groups apply to.
    """
    if device.integration == Integration.tuya:
        await tuya_client.send_command(device, command)
        state = await tuya_client.get_state(device)
        device.online = state["online"]
        device.state = state["state"]
        device.brightness = state["brightness"]
        device.color_temp = state.get("color_temp")
        device.color_mode = state.get("color_mode", "white")
        device.color_rgb = state.get("color_rgb")
        session.add(device)
        session.commit()
        session.refresh(device)

    elif device.integration == Integration.zigbee2mqtt:
        payload = mqtt_client.build_set_payload(command)
        if payload:
            await mqtt_client.publish(f"{mqtt_client.PREFIX}/{device.device_id}/set", payload)
        if "state" in command:
            device.state = command["state"]
        if "brightness" in command:
            device.brightness = command["brightness"]
        if "color_temp" in command:
            device.color_temp = command["color_temp"]
            device.color_mode = "white"
        if "color_rgb" in command:
            device.color_rgb = command["color_rgb"]
            device.color_mode = "colour"
        if "color_mode" in command and "color_temp" not in command and "color_rgb" not in command:
            device.color_mode = command["color_mode"]
        device.online = True
        session.add(device)
        session.commit()
        session.refresh(device)

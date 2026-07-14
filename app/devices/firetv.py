import asyncio
import logging
import os
from pathlib import Path

from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, DeviceType, Integration
from app.services.automation_engine import check_state_triggers

log = logging.getLogger(__name__)

_ADB_KEY = Path("firetv.adbkey")
_last_state: dict[int, dict] = {}
_ftv = None  # live androidtv connection, set by run() while connected

_KEY_ACTIONS = {
    "play_pause": "media_play_pause",
    "back": "back",
    "home": "home",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "enter": "enter",
    "volume_up": "volume_up",
    "volume_down": "volume_down",
    "mute": "mute_volume",
}


async def send_key(action: str) -> bool:
    method_name = _KEY_ACTIONS.get(action)
    if not method_name or _ftv is None or not _ftv.available:
        return False
    try:
        await getattr(_ftv, method_name)()
        return True
    except Exception as exc:
        log.error("Fire TV key '%s' failed: %s", action, exc)
        return False


def _ensure_adbkey() -> None:
    if not _ADB_KEY.exists():
        from adb_shell.auth.keygen import keygen
        keygen(str(_ADB_KEY))
        log.warning("Generated ADB key at %s — accept the prompt on your Fire TV screen", _ADB_KEY)


def _get_or_create_device(host: str) -> Device:
    with Session(engine) as session:
        device = session.exec(
            select(Device).where(Device.integration == Integration.firetv)
        ).first()
        if device is None:
            device = Device(
                name="Fire TV",
                integration=Integration.firetv,
                device_id=host,
                type=DeviceType.tv,
            )
            session.add(device)
            session.commit()
            session.refresh(device)
        return device


def _save_state(device_id: int, media_state: str, current_app: str, online: bool) -> None:
    with Session(engine) as session:
        device = session.get(Device, device_id)
        if device:
            device.media_state = media_state
            device.current_app = current_app
            device.online = online
            session.add(device)
            session.commit()


async def run() -> None:
    host = os.getenv("FIRETV_HOST", "").strip()
    if not host:
        return

    from androidtv import setup_async
    from androidtv.adb_manager.adb_manager_async import ADBPythonAsync

    _ensure_adbkey()
    device = _get_or_create_device(host)

    global _ftv
    while True:
        try:
            signer = await ADBPythonAsync.load_adbkey(str(_ADB_KEY))
            ftv = await setup_async.setup(host, 5555, device_class="firetv", signer=signer)

            if not ftv.available:
                raise ConnectionError(f"ADB connect failed to {host}:5555")

            log.warning("Fire TV connected: %s", host)
            _ftv = ftv

            while True:
                state, current_app, _, _ = await ftv.update()
                media_state = state or "off"
                current_app = current_app or ""

                new_state = {"media_state": media_state, "app_id": current_app}
                if new_state != _last_state.get(device.id):
                    _last_state[device.id] = new_state
                    _save_state(device.id, media_state, current_app, True)
                    await check_state_triggers(device.id, new_state)
                    log.info("Fire TV: %s / %s", media_state, current_app)

                await asyncio.sleep(5)

        except Exception as exc:
            log.error("Fire TV error, reconnecting in 30s: %s", exc)
            _ftv = None
            _save_state(device.id, "off", "", False)
            await asyncio.sleep(30)

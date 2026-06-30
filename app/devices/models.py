from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class DeviceType(str, Enum):
    plug = "plug"
    bulb = "bulb"
    ac = "ac"


class Integration(str, Enum):
    tuya = "tuya"
    hon = "hon"
    zigbee2mqtt = "zigbee2mqtt"


class Device(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    device_id: str = Field(unique=True)  # Z2M: friendly_name; Tuya: device ID
    local_key: str = Field(default="")
    ip_address: str = Field(default="")
    type: DeviceType
    integration: Integration = Integration.tuya
    online: bool = False
    state: bool = False
    brightness: Optional[int] = None     # bulbs: 10–100
    temperature: Optional[int] = None    # A/C: target °C
    ac_mode: Optional[str] = None        # A/C: auto/cool/heat/dry/fan
    fan_speed: Optional[int] = None      # A/C: 0=auto 1–4=speeds
    color_temp: Optional[int] = None             # bulbs: 0 (warm) – 100 (cool)
    color_mode: str = Field(default="white")     # bulbs: "white" or "colour"
    color_rgb: Optional[str] = None              # bulbs in colour mode: "#rrggbb"
    protocol_version: float = Field(default=3.3)  # Tuya LAN protocol version

from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel, Relationship


class TriggerType(str, Enum):
    time = "time"
    device_state = "device_state"


class DeviceType(str, Enum):
    plug = "plug"
    bulb = "bulb"
    ac = "ac"
    tv = "tv"


class Integration(str, Enum):
    tuya = "tuya"
    hon = "hon"
    zigbee2mqtt = "zigbee2mqtt"
    firetv = "firetv"


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
    media_state: Optional[str] = None   # Fire TV: playing/paused/idle/standby/off
    current_app: Optional[str] = None   # Fire TV: current app package name


class Schedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="device.id", unique=True)
    on_time: str   # "HH:MM" local time
    off_time: str  # "HH:MM" local time
    enabled: bool = Field(default=True)


class Automation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    enabled: bool = Field(default=True)

    # Trigger
    trigger_type: TriggerType
    trigger_time: Optional[str] = None                                       # "HH:MM" — time triggers
    trigger_device_id: Optional[int] = Field(default=None, foreign_key="device.id")  # state triggers
    trigger_field: Optional[str] = None                                      # "state", "brightness", "temperature"
    trigger_operator: Optional[str] = None                                   # "eq", "ne", "gt", "lt"
    trigger_value: Optional[str] = None

    # Action
    action_device_id: int = Field(foreign_key="device.id")
    action_type: str   # "set_state_on", "set_state_off", "set_brightness", "set_color_temp", "set_color_rgb"
    action_value: Optional[str] = None

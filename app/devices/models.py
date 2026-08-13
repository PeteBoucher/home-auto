from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel, Relationship


class TriggerType(str, Enum):
    time = "time"
    device_state = "device_state"
    sun = "sun"


class DeviceType(str, Enum):
    plug = "plug"
    bulb = "bulb"
    ac = "ac"
    tv = "tv"
    sensor = "sensor"


class Integration(str, Enum):
    tuya = "tuya"
    hon = "hon"
    zigbee2mqtt = "zigbee2mqtt"
    firetv = "firetv"


class Device(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    room: Optional[str] = None            # free-text install location, e.g. "Lounge"
    group_id: Optional[int] = Field(default=None, foreign_key="devicegroup.id")
    group_override: bool = Field(default=False)  # detached from group sync until the group is next commanded
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
    fan_speed: Optional[int] = None      # A/C: device-specific enum — 1=high 2=med 3=low 5=auto (no 0/4)
    quiet: Optional[bool] = None         # A/C: quiet/mute mode
    louvre_position: Optional[int] = None  # A/C: vertical louvre position (device-specific enum, no fixed labels)
    indoor_temp: Optional[float] = None    # A/C: measured room temperature
    outdoor_temp: Optional[float] = None   # A/C: measured outdoor temperature
    color_temp: Optional[int] = None             # bulbs: 0 (warm) – 100 (cool)
    color_mode: str = Field(default="white")     # bulbs: "white" or "colour"
    color_rgb: Optional[str] = None              # bulbs in colour mode: "#rrggbb"
    protocol_version: float = Field(default=3.3)  # Tuya LAN protocol version
    dimmable: bool = Field(default=True)          # bulbs: show brightness/color controls
    power_on_behavior: Optional[str] = None       # zigbee plugs: on/off/previous
    overload_protection: Optional[str] = None     # zigbee plugs: JSON blob from Z2M
    media_state: Optional[str] = None   # Fire TV: playing/paused/idle/standby/off
    current_app: Optional[str] = None   # Fire TV: current app package name
    power: Optional[float] = None       # plugs with monitoring: watts
    current: Optional[float] = None     # plugs with monitoring: amps
    voltage: Optional[float] = None     # plugs with monitoring: volts
    energy: Optional[float] = None      # plugs with monitoring: kWh cumulative
    energy_today: Optional[float] = None  # plugs with monitoring: kWh since midnight
    energy_month: Optional[float] = None  # plugs with monitoring: kWh since 1st of month
    sensor_temperature: Optional[float] = None  # sensors: ambient °C
    humidity: Optional[float] = None            # sensors: relative humidity %
    battery: Optional[int] = None               # battery-powered devices: %


class DeviceGroup(SQLModel, table=True):
    """A cross-integration group of devices that stay in sync.

    Zigbee members are additionally mirrored into a real Zigbee2MQTT group
    (`zigbee_group_name`), so a single command to the group fans out as one
    native Zigbee groupcast rather than one message per Zigbee member.
    Non-Zigbee members (Tuya, etc.) are always commanded individually — there's
    no equivalent native grouping for them.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    zigbee_group_name: Optional[str] = None       # Z2M friendly_name for the mirrored Zigbee group
    state: bool = False
    brightness: Optional[int] = None
    color_temp: Optional[int] = None
    color_mode: str = Field(default="white")
    color_rgb: Optional[str] = None
    dimmable: bool = Field(default=True)


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
    trigger_operator: Optional[str] = None                                   # "eq", "ne", "gt", "lt", "within"
    trigger_value: Optional[str] = None                                      # "within": tolerance, not an absolute value
    trigger_compare_field: Optional[str] = None                              # "within" only — other field on the same device to compare trigger_field against
    trigger_sun_event: Optional[str] = None                                  # "sunrise" or "sunset" — sun triggers
    trigger_sun_offset: Optional[int] = None                                 # minutes; negative = before, positive = after

    # Action
    action_device_id: int = Field(foreign_key="device.id")
    action_type: str   # "set_state_on", "set_state_off", "set_brightness", "set_color_temp", "set_color_rgb"
    action_value: Optional[str] = None


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    category: str  # "automation", "error"
    message: str


class PowerSample(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="device.id", index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    voltage: Optional[float] = None
    power: Optional[float] = None
    current: Optional[float] = None
    energy_today: Optional[float] = None
    energy_month: Optional[float] = None


class ClimateSample(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="device.id", index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    temperature: Optional[float] = None
    humidity: Optional[float] = None


class AcSample(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="device.id", index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    temperature: Optional[int] = None      # selected/target °C
    indoor_temp: Optional[float] = None    # measured room °C
    outdoor_temp: Optional[float] = None   # measured outdoor °C
    ac_state: Optional[bool] = None        # was the unit on at sample time


class EnergyDailySummary(SQLModel, table=True):
    """One row per device per calendar day, kept indefinitely (unlike PowerSample,
    which is pruned after 7 days) so daily/monthly energy charts have history to show.

    Each field holds the last value seen that day for its counter — since
    energy_today/energy_month are monotonically increasing within their period,
    the last value recorded on a given date is that period's running total as of
    end of day, which is exactly what a daily bar or a calendar-month rollup needs.
    """
    __table_args__ = (UniqueConstraint("device_id", "date", name="uq_energy_daily_device_date"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="device.id", index=True)
    date: str  # "YYYY-MM-DD", local calendar date
    energy_today: Optional[float] = None
    energy_month: Optional[float] = None

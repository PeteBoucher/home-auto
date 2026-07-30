from datetime import date, datetime

import httpx
from astral import LocationInfo
from astral.sun import sun as astral_sun

# WMO weather codes that indicate precipitation
_RAIN_CODES = frozenset([
    *range(51, 68),   # drizzle and rain
    *range(80, 83),   # rain showers
    *range(95, 100),  # thunderstorm
])

_URL = "https://api.open-meteo.com/v1/forecast"


async def is_raining(lat: float, lon: float) -> bool:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(_URL, params={
            "latitude": lat,
            "longitude": lon,
            "current": "weather_code",
        })
        resp.raise_for_status()
    code = resp.json()["current"]["weather_code"]
    return code in _RAIN_CODES


def sun_times_for_date(lat: float, lon: float, day: date) -> tuple[datetime, datetime]:
    """Pure astronomical calculation, no network required, for an arbitrary date."""
    observer = LocationInfo(latitude=lat, longitude=lon).observer
    s = astral_sun(observer, date=day)
    sunrise = s["sunrise"].astimezone().replace(tzinfo=None)
    sunset = s["sunset"].astimezone().replace(tzinfo=None)
    return sunrise, sunset


def _offline_sun_times(lat: float, lon: float) -> tuple[datetime, datetime]:
    """Used when Open-Meteo is unreachable so sun-triggered automations
    survive internet outages."""
    return sun_times_for_date(lat, lon, date.today())


async def get_sun_times(lat: float, lon: float) -> tuple[datetime, datetime]:
    """Returns (sunrise, sunset) as naive local datetimes for today.

    Prefers Open-Meteo; falls back to an offline astronomical calculation
    if the network request fails for any reason.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_URL, params={
                "latitude": lat,
                "longitude": lon,
                "daily": "sunrise,sunset",
                "timezone": "auto",
            })
            resp.raise_for_status()
        daily = resp.json()["daily"]
        sunrise = datetime.fromisoformat(daily["sunrise"][0])
        sunset = datetime.fromisoformat(daily["sunset"][0])
        return sunrise, sunset
    except Exception:
        return _offline_sun_times(lat, lon)

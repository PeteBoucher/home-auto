from datetime import datetime

import httpx

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


async def get_sun_times(lat: float, lon: float) -> tuple[datetime, datetime]:
    """Returns (sunrise, sunset) as naive local datetimes for today."""
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

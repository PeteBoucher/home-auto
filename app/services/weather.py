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

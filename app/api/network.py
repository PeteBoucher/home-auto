import asyncio
import os
import socket
from urllib.parse import urlparse
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.devices.models import Device, Integration

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None

SessionDep = Annotated[Session, Depends(get_session)]


def _icon(hostname: str, ha_type: str | None, is_gateway: bool, is_self: bool) -> str:
    if is_gateway:
        return "router-fill"
    if is_self:
        return "cpu-fill"
    if ha_type:
        return {"plug": "plug-fill", "bulb": "lightbulb-fill", "ac": "snow", "tv": "tv-fill"}.get(ha_type, "cpu")
    h = hostname.lower()
    if any(x in h for x in ("iphone", "android", "phone")):
        return "phone-fill"
    if any(x in h for x in ("ipad", "tablet")):
        return "tablet-fill"
    if any(x in h for x in ("macbook", "laptop")):
        return "laptop-fill"
    if any(x in h for x in ("mac", "imac", "desktop")):
        return "display-fill"
    if any(x in h for x in ("appletv", "firestick", "amazon", "chromecast")):
        return "tv-fill"
    if any(x in h for x in ("tl-", "wa8", "wa6", "extender", "repeater", "access")):
        return "router-fill"
    if any(x in h for x in ("cam", "roachcam")):
        return "camera-video-fill"
    if any(x in h for x in ("pi", "raspberry")):
        return "cpu-fill"
    return "pc-display"


async def _get_gateway() -> tuple[str, str]:
    """Returns (gateway_ip, self_ip)."""
    proc = await asyncio.create_subprocess_exec(
        "ip", "route", "show", "default",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    gateway, self_ip = "", ""
    for line in stdout.decode().splitlines():
        parts = line.split()
        if "via" in parts:
            gateway = parts[parts.index("via") + 1]
        if "src" in parts:
            self_ip = parts[parts.index("src") + 1]
    return gateway, self_ip


async def _resolve(ip: str) -> str:
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, socket.gethostbyaddr, ip),
            timeout=0.5,
        )
        return result[0].split(".")[0]
    except Exception:
        return ""


def _known_hostnames() -> dict[str, str]:
    """Build IP→hostname overrides for devices mDNS can't resolve on Linux."""
    known: dict[str, str] = {}

    roachcam_url = os.getenv("ROACHCAM_URL", "").strip()
    if roachcam_url:
        host = urlparse(roachcam_url).hostname or ""
        if host:
            try:
                ip = socket.gethostbyname(host)
                known[ip] = host.split(".")[0]  # "roachcam" from "roachcam.local"
            except Exception:
                pass
    return known


async def scan(session: Session) -> list[dict]:
    proc = await asyncio.create_subprocess_exec(
        "ip", "neigh", "show",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    gateway, self_ip = await _get_gateway()
    overrides = _known_hostnames()

    # Known home-auto devices keyed by IP
    all_devices = list(session.exec(select(Device)).all())
    ha_by_ip: dict[str, Device] = {}
    for d in all_devices:
        if d.ip_address:
            ha_by_ip[d.ip_address] = d
        if d.integration == Integration.firetv and d.device_id:
            ha_by_ip[d.device_id] = d

    entries: list[tuple[str, str]] = []  # (ip, mac)
    for line in stdout.decode().splitlines():
        parts = line.split()
        if not parts:
            continue
        ip, state = parts[0], parts[-1]
        if ":" in ip or state in ("FAILED", "INCOMPLETE"):  # skip IPv6 and failed
            continue
        mac = parts[parts.index("lladdr") + 1] if "lladdr" in parts else ""
        entries.append((ip, mac))

    # Add self if not already present
    if self_ip and not any(ip == self_ip for ip, _ in entries):
        entries.append((self_ip, ""))

    hostnames = await asyncio.gather(*[_resolve(ip) for ip, _ in entries])

    devices = []
    for (ip, mac), hostname in zip(entries, hostnames):
        ha = ha_by_ip.get(ip)
        is_gw = ip == gateway
        is_self = ip == self_ip
        # Prefer env-derived overrides (solves mDNS), then DNS, then IP
        label = overrides.get(ip) or (socket.gethostname() if is_self else "") or hostname or ip
        devices.append({
            "ip": ip,
            "mac": mac,
            "hostname": label,
            "is_gateway": is_gw,
            "is_self": is_self,
            "ha_name": ha.name if ha else None,
            "ha_type": ha.type.value if ha else None,
            "icon": _icon(label, ha.type.value if ha else None, is_gw, is_self),
        })

    devices.sort(key=lambda d: (
        not d["is_gateway"], not d["is_self"],
        [int(x) for x in d["ip"].split(".")],
    ))
    return devices


@router.get("/network", response_class=HTMLResponse)
async def network_page(request: Request, session: SessionDep):
    devices = await scan(session)
    return templates.TemplateResponse(request, "network.html", {"devices": devices})


@router.get("/network/scan", response_class=HTMLResponse)
async def network_scan(request: Request, session: SessionDep):
    devices = await scan(session)
    return templates.TemplateResponse(request, "partials/network_devices.html", {"devices": devices})

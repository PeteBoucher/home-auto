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
    if any(x in h for x in ("iphone", "android", "phone", "samsung", "galaxy", "pixel", "oneplus", "huawei", "xiaomi", "redmi", "oppo", "nokia")):
        return "phone-fill"
    if any(x in h for x in ("ipad", "tablet")):
        return "tablet-fill"
    if any(x in h for x in ("macbook", "laptop", "notebook")):
        return "laptop-fill"
    if any(x in h for x in ("imac", "desktop-", "workstation", "mac")):
        return "display-fill"
    if any(x in h for x in ("appletv", "firestick", "amazon", "chromecast")):
        return "tv-fill"
    if any(x in h for x in ("brw", "brn", "printer", "epson", "canon", "hp-", "xerox")):
        return "printer-fill"
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


async def _ping_sweep(subnet_prefix: str) -> None:
    """Ping all hosts in /24 subnet in parallel to populate the ARP cache."""
    procs = await asyncio.gather(*[
        asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "1", f"{subnet_prefix}.{i}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        for i in range(1, 255)
    ])
    await asyncio.gather(*[p.communicate() for p in procs])


async def _nmap_hosts(subnet: str) -> dict[str, str]:
    """Return {ip: hostname} via nmap -sn. Returns {} if nmap is not installed."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nmap", "-sn", subnet,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
    except FileNotFoundError:
        return {}
    hosts: dict[str, str] = {}
    for line in stdout.decode().splitlines():
        if not line.startswith("Nmap scan report for "):
            continue
        rest = line[len("Nmap scan report for "):]
        if "(" in rest:
            hostname, ip = rest.split("(", 1)
            hosts[ip.rstrip(")")] = hostname.strip().split(".")[0]
        else:
            hosts[rest.strip()] = ""
    return hosts


async def _avahi_hosts() -> dict[str, str]:
    """Return {ip: hostname} for mDNS devices via avahi-browse. Returns {} if not installed."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "avahi-browse", "--all", "--resolve", "--terminate", "--parsable",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
    except FileNotFoundError:
        return {}
    hosts: dict[str, str] = {}
    for line in stdout.decode().splitlines():
        # Resolved entry format: =;iface;IPv4;name;_service;local;hostname.local;ip;port;txt
        if not line.startswith("="):
            continue
        parts = line.split(";")
        if len(parts) < 8 or parts[2] != "IPv4":
            continue
        hostname = parts[6].split(".")[0]  # "Pete-iPhone-11" from "Pete-iPhone-11.local"
        ip = parts[7]
        if ip and hostname:
            hosts[ip] = hostname
    return hosts


async def scan(session: Session) -> list[dict]:
    gateway, self_ip = await _get_gateway()
    subnet_prefix = ".".join(self_ip.split(".")[:3]) if self_ip else ""

    nmap_coro = _nmap_hosts(f"{subnet_prefix}.0/24") if subnet_prefix else asyncio.sleep(0, result={})
    nmap_hosts, avahi = await asyncio.gather(nmap_coro, _avahi_hosts())
    if not nmap_hosts and subnet_prefix:
        # nmap not installed — fall back to manual ping sweep
        await _ping_sweep(subnet_prefix)

    proc = await asyncio.create_subprocess_exec(
        "ip", "neigh", "show",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
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
        # Priority: env overrides → avahi mDNS → nmap → self hostname → DNS → raw IP
        label = overrides.get(ip) or avahi.get(ip) or nmap_hosts.get(ip) or (socket.gethostname() if is_self else "") or hostname or ip
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

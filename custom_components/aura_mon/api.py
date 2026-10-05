"""Async client for the AuraMon device HTTP API.

See firmware/API.md in the aura-mon repo for the full API description. Live readings are
pushed to Home Assistant via a webhook (see WEBHOOK_INGESTION.md), so this client only
implements ``GET /status``, used for device identity/firmware metadata.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import aiohttp

from .const import DEFAULT_TIMEOUT


@dataclass
class DeviceStatus:
    """A single device entry from GET /status."""

    name: str
    volts: float
    amps: float
    pf: float
    hz: float


@dataclass
class DatalogInfo:
    """The `datalog` object from GET /status."""

    first_rev: int
    first_ts: int
    last_rev: int
    last_ts: int
    interval: int
    size: int


@dataclass
class NetworkInfo:
    """The `network` object from GET /status."""

    hostname: str
    ip: str
    gateway: str
    subnet: str
    dns: str
    mac: str


@dataclass
class StatusResponse:
    """The full response of GET /status."""

    version: str
    devices: list[DeviceStatus] = field(default_factory=list)
    datalog: DatalogInfo | None = None
    network: NetworkInfo | None = None


class AuraMonClient:
    """Thin async client for the AuraMon HTTP API."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client."""
        self._host = host
        self._session = session
        self._timeout = timeout

    def _url(self, path: str) -> str:
        return f"http://{self._host}{path}"

    async def get_status(self) -> StatusResponse:
        """Fetch and parse GET /status."""
        async with asyncio.timeout(self._timeout):
            async with self._session.get(self._url("/status")) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

        devices = [
            DeviceStatus(
                name=d["name"],
                volts=float(d["volts"]),
                amps=float(d["amps"]),
                pf=float(d["pf"]),
                hz=float(d["hz"]),
            )
            for d in data.get("devices", [])
        ]

        datalog_data = data.get("datalog") or {}
        datalog = DatalogInfo(
            first_rev=int(datalog_data.get("firstRev", 0)),
            first_ts=int(datalog_data.get("firstTS", 0)),
            last_rev=int(datalog_data.get("lastRev", 0)),
            last_ts=int(datalog_data.get("lastTS", 0)),
            interval=int(datalog_data.get("interval", 0)),
            size=int(datalog_data.get("size", 0)),
        )

        network_data = data.get("network") or {}
        network = NetworkInfo(
            hostname=network_data.get("hostname", ""),
            ip=network_data.get("ip", ""),
            gateway=network_data.get("gateway", ""),
            subnet=network_data.get("subnet", ""),
            dns=network_data.get("dns", ""),
            mac=network_data.get("mac", ""),
        )

        return StatusResponse(
            version=data.get("version", ""),
            devices=devices,
            datalog=datalog,
            network=network,
        )

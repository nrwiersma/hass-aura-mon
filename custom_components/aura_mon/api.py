"""Async client for the AuraMon device HTTP API.

See firmware/API.md in the aura-mon repo for the full API description. This client only
implements the read-only endpoints needed by the Home Assistant integration: ``GET /status``
and ``GET /energy``.
"""
from __future__ import annotations

import asyncio
import csv
import io
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


@dataclass
class DeviceEnergy:
    """A single device's columns in one GET /energy row."""

    name: str
    voltage: float
    current: float
    power: float
    energy_wh: float
    power_factor: float


@dataclass
class EnergyRow:
    """A single row (interval) from GET /energy."""

    timestamp: int
    hz: float
    devices: dict[str, DeviceEnergy] = field(default_factory=dict)


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

    async def get_energy(
        self, start: int, end: int | None = None, interval: int | None = None
    ) -> list[EnergyRow]:
        """Fetch and parse GET /energy, returning [] on 204 (no new data)."""
        params: dict[str, str] = {"start": str(start)}
        if end is not None:
            params["end"] = str(end)
        if interval is not None:
            params["interval"] = str(interval)

        async with asyncio.timeout(self._timeout):
            async with self._session.get(self._url("/energy"), params=params) as resp:
                if resp.status == 204:
                    return []
                resp.raise_for_status()
                text = await resp.text()

        return self._parse_energy_csv(text)

    @staticmethod
    def _parse_energy_csv(text: str) -> list[EnergyRow]:
        """Parse the `/energy` CSV body into EnergyRow objects."""
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration:
            return []

        # header: timestamp, Hz, <name>.V, <name>.A, <name>.W, <name>.Wh, <name>.PF, ...
        device_names: list[str] = []
        for col in header[2:]:
            if col.endswith(".V"):
                device_names.append(col[: -len(".V")])

        rows: list[EnergyRow] = []
        for raw_row in reader:
            if not raw_row:
                continue
            timestamp = int(float(raw_row[0]))
            hz = float(raw_row[1])
            devices: dict[str, DeviceEnergy] = {}
            for i, name in enumerate(device_names):
                base = 2 + i * 5
                if base + 5 > len(raw_row):
                    break
                devices[name] = DeviceEnergy(
                    name=name,
                    voltage=float(raw_row[base]),
                    current=float(raw_row[base + 1]),
                    power=float(raw_row[base + 2]),
                    energy_wh=float(raw_row[base + 3]),
                    power_factor=float(raw_row[base + 4]),
                )
            rows.append(EnergyRow(timestamp=timestamp, hz=hz, devices=devices))

        return rows
